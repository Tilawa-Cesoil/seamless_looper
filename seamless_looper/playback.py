"""
Sample-continuous playback.

Two layers live here:

* :class:`PlaybackPlan` is a pure sample-state machine.  It can be tested
  without an audio device and is the single source of truth for the
  intro -> loop × n -> outro flow.
* :class:`StreamPlayer` wraps the plan in a long-lived PortAudio output stream,
  a bounded producer queue, and a callback that only copies memory.

The callback never restarts the stream at a segment or loop boundary.  A
transition is just the next range of samples in the same producer stream.
"""

from __future__ import annotations

import queue
import threading
import time
from enum import Enum

import numpy as np
from PySide6.QtCore import QObject, Signal

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - surfaced to the user at runtime
    sd = None

from .audio_io import AudioError, LoadedTrack


class Phase(str, Enum):
    INTRO = "intro"
    LOOP = "loop"
    OUTRO = "outro"
    DONE = "done"


class PlaybackPlan:
    """A seam-tolerant, block-size-agnostic playback state machine."""

    def __init__(
        self,
        track: LoadedTrack,
        repeats: int | None,
        *,
        end_event: threading.Event | None = None,
    ):
        if repeats is not None and repeats < 1:
            raise ValueError("循环次数必须大于等于 1")
        self.track = track
        self.repeats = repeats
        self.end_event = end_event or threading.Event()
        self.phase = Phase.INTRO
        self.position = 0
        self.loops_completed = 0
        self._total_loops = repeats
        self._closed = False

    @property
    def is_infinite(self) -> bool:
        return self._total_loops is None

    def request_end_loop(self) -> None:
        self.end_event.set()

    def _advance_phase(self) -> None:
        if self.phase is Phase.INTRO:
            self.position = 0
            if self.end_event.is_set():
                self.phase = Phase.OUTRO
            else:
                self.phase = Phase.LOOP
            return
        if self.phase is Phase.LOOP:
            self.loops_completed += 1
            at_limit = self._total_loops is not None and self.loops_completed >= self._total_loops
            if self.end_event.is_set() or at_limit:
                self.phase = Phase.OUTRO
                self.position = 0
            else:
                self.position = 0
            return
        if self.phase is Phase.OUTRO:
            self.phase = Phase.DONE
            self.position = 0
            self._closed = True

    def _current_data(self) -> np.ndarray | None:
        if self.phase is Phase.INTRO:
            return self.track.intro
        if self.phase is Phase.LOOP:
            return self.track.loop
        if self.phase is Phase.OUTRO:
            return self.track.outro
        return None

    def render(self, frames: int) -> tuple[np.ndarray, int]:
        """Render exactly ``frames`` samples unless the true stream ends.

        Returned samples are contiguous across every state boundary.  No zero
        padding is inserted between intro, loops, or outro; only the natural
        end of the stream may return fewer samples than requested.
        """
        frames = int(frames)
        channels = self.track.channels
        output = np.zeros((frames, channels), dtype=np.float32)
        written = 0
        while written < frames and self.phase is not Phase.DONE:
            # If the user asks to end the loop exactly between passes (or
            # before the first loop sample), go straight to the outro instead
            # of starting one more pass.  This is the boundary counterpart of
            # "finish the pass currently playing".
            if self.phase is Phase.LOOP and self.position == 0 and self.end_event.is_set():
                self.phase = Phase.OUTRO
                self.position = 0
                continue
            data = self._current_data()
            if data is None:
                break
            if self.position >= len(data):
                self._advance_phase()
                continue
            take = min(frames - written, len(data) - self.position)
            output[written : written + take] = data[self.position : self.position + take]
            self.position += take
            written += take
            if self.position >= len(data):
                self._advance_phase()
        if self.phase is Phase.DONE:
            self._closed = True
        return output, written

    @property
    def closed(self) -> bool:
        return self._closed


class StreamPlayer(QObject):
    """A persistent PortAudio stream fed by a bounded producer queue."""

    finished = Signal()
    error_occurred = Signal(str)
    phase_changed = Signal(str)

    BLOCKSIZE = 512
    LATENCY = 0.08
    # Six 512-frame blocks are ~70 ms at 44.1 kHz.  Enough of a cushion for a
    # Python producer, small enough that an "end loop" request lands in the
    # pass the listener is actually hearing.
    QUEUE_BLOCKS = 6
    PREBUFFER_BLOCKS = 3

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._stream = None
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=self.QUEUE_BLOCKS)
        self._plan: PlaybackPlan | None = None
        self._track: LoadedTrack | None = None
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._eos = threading.Event()
        self._stopping = threading.Event()
        self._finished_emitted = False
        self._current: np.ndarray | None = None
        self._current_pos = 0
        self._volume = 0.85
        self._underruns = 0
        self._active = False
        self._last_phase = Phase.INTRO

    # -- public API ---------------------------------------------------------
    @property
    def active(self) -> bool:
        return self._active

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def phase(self) -> Phase:
        return self._plan.phase if self._plan else Phase.DONE

    @property
    def loops_completed(self) -> int:
        return self._plan.loops_completed if self._plan else 0

    @property
    def underruns(self) -> int:
        return self._underruns

    def start(self, track: LoadedTrack, repeats: int | None, volume: float = 0.85) -> None:
        if sd is None:
            raise AudioError("sounddevice / PortAudio 未安装，无法播放。")
        self.stop(immediate=True, emit=False)
        if repeats is not None and repeats < 1:
            raise ValueError("循环次数必须大于等于 1")
        if track.loop.shape[0] <= 0:
            raise AudioError("循环段为空，无法播放。")

        self._track = track
        self._volume = max(0.0, min(1.0, float(volume)))
        self._queue = queue.Queue(maxsize=self.QUEUE_BLOCKS)
        self._paused.clear()
        self._stop.clear()
        self._eos.clear()
        self._stopping.clear()
        self._finished_emitted = False
        self._current = None
        self._current_pos = 0
        self._underruns = 0
        self._last_phase = Phase.INTRO
        self._plan = PlaybackPlan(track, repeats)
        self._active = True

        try:
            self._stream = sd.OutputStream(
                samplerate=track.sample_rate,
                channels=track.channels,
                dtype="float32",
                blocksize=self.BLOCKSIZE,
                latency=self.LATENCY,
                callback=self._audio_callback,
                finished_callback=self._stream_finished,
            )
        except Exception as exc:
            self._active = False
            self._plan = None
            raise AudioError(
                f"无法打开音频输出：{exc}\n请检查系统默认播放设备，"
                f"并确认它支持 {track.sample_rate} Hz / {track.channels} 声道。"
            ) from exc

        self._thread = threading.Thread(target=self._producer_loop, name="loopstudio-producer", daemon=True)
        self._thread.start()
        self._wait_for_prebuffer()
        try:
            self._stream.start()
        except Exception as exc:
            self.stop(immediate=True, emit=False)
            raise AudioError(f"无法启动音频输出：{exc}") from exc
        self._paused.clear()

    def pause(self, paused: bool = True) -> None:
        if not self._active:
            return
        if paused:
            self._paused.set()
        else:
            self._paused.clear()

    def toggle_pause(self) -> bool:
        if self._paused.is_set():
            self._paused.clear()
        else:
            self._paused.set()
        return self._paused.is_set()

    def request_end_loop(self) -> None:
        if self._plan and self._active:
            self._plan.request_end_loop()

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))

    def stop(self, *, immediate: bool = True, emit: bool = False) -> None:
        was_active = self._active
        self._stopping.set()
        self._stop.set()
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                if immediate:
                    stream.abort()
                else:
                    stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)
        self._thread = None
        self._active = False
        self._plan = None
        self._track = None
        self._queue = queue.Queue(maxsize=self.QUEUE_BLOCKS)
        self._current = None
        if emit and was_active:
            self.finished.emit()

    def cleanup_finished_stream(self) -> None:
        """Close a stream that stopped itself at the natural end."""
        self._stopping.set()
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._active = False
        self._plan = None
        self._track = None

    # -- internals ----------------------------------------------------------
    def _wait_for_prebuffer(self) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not self._eos.is_set():
            if self._queue.qsize() >= min(self.PREBUFFER_BLOCKS, self.QUEUE_BLOCKS):
                break
            time.sleep(0.004)

    def _producer_loop(self) -> None:
        plan = self._plan
        if plan is None:
            return
        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    time.sleep(0.005)
                    continue
                block, produced = plan.render(self.BLOCKSIZE)
                if plan.phase is not Phase.DONE and plan.phase is not self._last_phase:
                    self._last_phase = plan.phase
                    self.phase_changed.emit(plan.phase.value)
                if produced:
                    gain = self._volume
                    if gain != 1.0:
                        block = block * gain
                    try:
                        self._queue.put(block, timeout=0.2)
                    except queue.Full:
                        if self._stop.is_set():
                            break
                        # The queue is bounded on purpose; dropping here would
                        # create a seam, so wait again rather than discard.
                        while not self._stop.is_set():
                            try:
                                self._queue.put(block, timeout=0.05)
                                break
                            except queue.Full:
                                continue
                        if self._stop.is_set():
                            break
                if produced < self.BLOCKSIZE:
                    self._eos.set()
                    break
                if plan.closed:
                    self._eos.set()
                    break
        except Exception as exc:  # pragma: no cover - defensive
            self._eos.set()
            self.error_occurred.emit(f"播放线程出错：{exc}")

    def _audio_callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        outdata.fill(0)
        if self._paused.is_set():
            return
        written = 0
        try:
            while written < frames:
                if self._current is None:
                    try:
                        self._current = self._queue.get_nowait()
                        self._current_pos = 0
                    except queue.Empty:
                        if self._eos.is_set():
                            self._finished_emitted = True
                            raise sd.CallbackStop
                        self._underruns += 1
                        # No Qt signal from the audio callback: it should only
                        # touch memory.  The count is polled by the UI.
                        break
                block = self._current
                available = len(block) - self._current_pos
                take = min(frames - written, available)
                outdata[written : written + take] = block[self._current_pos : self._current_pos + take]
                self._current_pos += take
                written += take
                if self._current_pos >= len(block):
                    self._current = None
                    self._current_pos = 0
            if self._eos.is_set() and self._current is None and self._queue.empty() and written >= frames:
                # Everything requested for this callback was real audio.  Stop
                # on the next callback so the final block is not truncated.
                self._finished_emitted = True
                raise sd.CallbackStop
        except sd.CallbackStop:
            raise
        except Exception:
            # Never let an exception escape into PortAudio.
            outdata.fill(0)
            self._finished_emitted = True
            raise sd.CallbackStop

    def _stream_finished(self) -> None:
        if self._stopping.is_set():
            return
        self._active = False
        self.finished.emit()


class PreviewPlayer(QObject):
    """Low-latency editor monitoring.

    The stream is opened when a file enters the editor and kept running with
    silence while paused.  Pressing space therefore only flips an atomic flag;
    it never pays the cost of opening a device.
    """

    error_occurred = Signal(str)

    BLOCKSIZE = 512
    LATENCY = 0.06

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._stream = None
        self._pcm: np.ndarray | None = None
        self._sample_rate = 0
        self._cursor = 0
        self._seek_target = -1
        self._paused = True
        self._volume = 0.8
        self._ended = threading.Event()
        self._total = 0

    @property
    def available(self) -> bool:
        return self._stream is not None and self._pcm is not None

    @property
    def playing(self) -> bool:
        return self.available and not self._paused and not self._ending

    @property
    def _ending(self) -> bool:
        return self._cursor >= self._total and self._total > 0

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def position(self) -> int:
        if self._seek_target >= 0:
            return int(self._seek_target)
        return int(self._cursor)

    @property
    def total_frames(self) -> int:
        return self._total

    def load(self, pcm: np.ndarray, sample_rate: int) -> None:
        if sd is None:
            raise AudioError("sounddevice / PortAudio 未安装，无法试听。")
        self.unload()
        self._pcm = np.ascontiguousarray(pcm, dtype=np.float32)
        self._sample_rate = int(sample_rate)
        self._total = int(self._pcm.shape[0])
        self._cursor = 0
        self._seek_target = -1
        self._paused = True
        self._ended.clear()
        try:
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=self._pcm.shape[1],
                dtype="float32",
                blocksize=self.BLOCKSIZE,
                latency=self.LATENCY,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            self._pcm = None
            raise AudioError(f"无法打开试听输出：{exc}") from exc

    def unload(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        self._pcm = None
        self._sample_rate = 0
        self._total = 0
        self._cursor = 0
        self._seek_target = -1
        self._paused = True
        self._ended.clear()

    def play(self) -> None:
        if not self.available:
            return
        if self._cursor >= self._total:
            self._seek_target = 0
        self._paused = False
        self._ended.clear()

    def pause(self) -> None:
        self._paused = True

    def toggle(self) -> bool:
        if not self.available:
            return False
        if self._paused:
            self.play()
            return True
        self.pause()
        return False

    def seek(self, frame: int) -> int:
        if not self.available:
            return 0
        frame = max(0, min(int(frame), self._total))
        self._seek_target = frame
        if self._paused:
            self._cursor = frame
            self._seek_target = -1
        self._ended.clear()
        return frame

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(1.0, float(volume)))

    def consume_ended(self) -> bool:
        if self._ended.is_set():
            self._ended.clear()
            return True
        return False

    def _audio_callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:
        outdata.fill(0)
        if self._pcm is None:
            return
        if self._seek_target >= 0:
            self._cursor = max(0, min(int(self._seek_target), self._total))
            self._seek_target = -1
        if self._paused or self._cursor >= self._total:
            if self._cursor >= self._total and self._total > 0:
                self._paused = True
                self._ended.set()
            return
        take = min(int(frames), self._total - self._cursor)
        if take > 0:
            block = self._pcm[self._cursor : self._cursor + take]
            gain = self._volume
            if gain == 1.0:
                outdata[:take] = block
            else:
                np.multiply(block, gain, out=outdata[:take])
            self._cursor += take
        if self._cursor >= self._total:
            self._paused = True
            self._ended.set()
