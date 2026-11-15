"""
Audio probing, decoding, frame-aware cutting, and the persistent library.

Everything that touches samples lives here, away from Qt.  The module decodes
*all* inputs to float PCM first, then cuts on exact sample indices.  No
compressed bitstream is ever spliced directly, so bit reservoirs, encoder
delay, and container priming cannot leak into a loop join.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import numpy as np
import soundfile as sf

ProgressCallback = Callable[[int, str], None]

LIBRARY_DIRNAME = "library"

SUPPORTED_EXTENSIONS = {
    ".wav",
    ".wave",
    ".aif",
    ".aiff",
    ".flac",
    ".mp3",
    ".m4a",
    ".mp4",
    ".ogg",
    ".oga",
}

EXTENSION_LABELS = {
    ".wav": "WAV",
    ".wave": "WAV",
    ".aif": "AIFF",
    ".aiff": "AIFF",
    ".flac": "FLAC",
    ".mp3": "MP3",
    ".m4a": "M4A",
    ".mp4": "M4A",
    ".ogg": "OGG",
    ".oga": "OGG",
}

_LOSSLESS_CODECS = {"flac", "alac"}
_SAMPLE_CODEC_PREFIXES = ("pcm_",)
_FIXED_PACKET_FRAMES = {"mp3": 1152, "aac": 1024}
_VARIABLE_PACKET_CODECS = {"opus", "vorbis"}


def _fixed_packet_step(codec: str, sample_rate: int) -> int:
    if codec == "mp3":
        # MPEG-1 Layer III is 1152 samples/frame; MPEG-2 and 2.5 (below
        # 32 kHz) use 576.
        return 1152 if sample_rate >= 32000 else 576
    return _FIXED_PACKET_FRAMES.get(codec, 1024)


class AudioError(RuntimeError):
    """A user-facing audio failure."""


class UnsupportedAudioError(AudioError):
    """Raised when the source is outside the MVP format contract."""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def format_timestamp(samples: int, sample_rate: int, *, millis: bool = True) -> str:
    """Format an exact sample position as mm:ss.mmm (or mm:ss)."""
    if sample_rate <= 0:
        return "00:00.000"
    total_ms = int(round(samples * 1000.0 / sample_rate))
    minutes, rem_ms = divmod(total_ms, 60_000)
    seconds, ms = divmod(rem_ms, 1000)
    if millis:
        return f"{minutes:02d}:{seconds:02d}.{ms:03d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def format_sample_count(samples: int) -> str:
    return f"{samples:,}"


def format_short_count(samples: int) -> str:
    if samples >= 1_000_000:
        return f"{samples / 1_000_000:.2f}M"
    if samples >= 1000:
        return f"{samples / 1000:.1f}k"
    return str(samples)


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _bundled_tool(name: str) -> str | None:
    """Look for a portable ffmpeg placed next to the app.

    Supported layouts (the first match wins):
        bin/ffmpeg.exe
        ffmpeg/bin/ffmpeg.exe
        vendor/ffmpeg/bin/ffmpeg.exe
    """
    base = Path(__file__).resolve().parent.parent
    filename = f"{name}.exe" if os.name == "nt" else name
    for relative in ("bin", "ffmpeg/bin", "vendor/ffmpeg/bin"):
        candidate = base / relative / filename
        if candidate.is_file():
            return str(candidate)
    return None


def ensure_ffmpeg() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe).

    Resolution order: environment override, app-local portable bundle, PATH.
    """
    # XIANYIN_* is kept as a compatibility alias for existing local setups.
    ffmpeg = (
        os.environ.get("LOOPSTUDIO_FFMPEG")
        or os.environ.get("XIANYIN_FFMPEG")
        or _bundled_tool("ffmpeg")
        or shutil.which("ffmpeg")
    )
    ffprobe = (
        os.environ.get("LOOPSTUDIO_FFPROBE")
        or os.environ.get("XIANYIN_FFPROBE")
        or _bundled_tool("ffprobe")
        or shutil.which("ffprobe")
    )
    if not ffmpeg or not ffprobe:
        raise AudioError(
            "没有找到 ffmpeg / ffprobe。请安装 ffmpeg 并加入 PATH，"
            "把 ffmpeg.exe / ffprobe.exe 放入项目的 bin/ 目录，"
            "或设置环境变量 LOOPSTUDIO_FFMPEG 与 LOOPSTUDIO_FFPROBE。"
        )
    return ffmpeg, ffprobe


def _run(cmd: list[str], *, timeout: float | None = 120) -> subprocess.CompletedProcess:
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            creationflags=flags,
        )
    except FileNotFoundError as exc:
        raise AudioError(f"无法启动音频工具：{cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError("音频处理超时。") from exc


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _probe_json(path: Path) -> dict:
    _, ffprobe = ensure_ffmpeg()
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    if result.returncode != 0:
        message = _decode_text(result.stderr).strip()
        raise AudioError(f"无法读取音频信息：{message or '未知错误'}")
    try:
        return json.loads(_decode_text(result.stdout))
    except json.JSONDecodeError as exc:
        raise AudioError("ffprobe 返回了无法解析的信息。") from exc


def _first_tag(tags: dict, *names: str) -> str:
    lower = {str(k).lower(): v for k, v in (tags or {}).items()}
    for name in names:
        value = lower.get(name.lower())
        if value:
            return str(value).strip()
    return ""


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Packet-boundary grid
# ---------------------------------------------------------------------------


@dataclass
class FrameGrid:
    """A quantisation grid for cut points.

    PCM and FLAC use every sample frame.  Compressed inputs use their decoded
    packet boundaries.  MP3/AAC packet lengths are fixed; Opus and Vorbis are
    read from the actual packet durations so variable-size packets are
    represented faithfully.
    """

    total_frames: int
    step: int
    label: str
    boundaries: np.ndarray | None = None

    def _grid(self) -> np.ndarray:
        if self.boundaries is not None and len(self.boundaries):
            return self.boundaries
        return np.arange(0, self.total_frames + 1, max(1, self.step), dtype=np.int64)

    @property
    def minimum_cut(self) -> int:
        if self.boundaries is None and self.step <= 1:
            return 1 if self.total_frames > 1 else 0
        for value in self._grid():
            if value > 0:
                return int(value)
        return min(1, self.total_frames - 1)

    @property
    def maximum_cut(self) -> int:
        if self.boundaries is None and self.step <= 1:
            return self.total_frames - 1
        for value in self._grid()[::-1]:
            if value < self.total_frames:
                return int(value)
        return max(0, self.total_frames - 1)

    def _arithmetic_bounds(self, low: int, high: int) -> tuple[int, int] | None:
        """First/last multiples of step in (low, high), without building a grid."""
        step = max(1, self.step)
        first = ((max(0, low) // step) + 1) * step
        last = ((max(0, high) - 1) // step) * step
        if first >= high or last <= low or first > last:
            return None
        return first, last

    def snap(self, position: float, *, low: int = 0, high: int | None = None) -> int:
        """Nearest grid point in the open interval (low, high)."""
        total = self.total_frames
        hi = total if high is None else min(high, total)
        if hi <= low:
            return max(0, min(total, int(round(position))))

        # The common PCM/FLAC case is a one-sample grid.  Never materialise a
        # multi-million-entry arange for it: dragging a cut point calls this on
        # every mouse-move.
        if self.boundaries is None and self.step <= 1:
            return max(low + 1, min(hi - 1, int(round(position))))

        if self.boundaries is None:
            bounds = self._arithmetic_bounds(low, hi)
            if bounds is None:
                return max(low + 1, min(hi - 1, int(round(position))))
            first, last = bounds
            step = max(1, self.step)
            candidate = int(round(position / step)) * step
            return max(first, min(last, candidate))

        arr = self.boundaries[(self.boundaries > low) & (self.boundaries < hi)]
        if not len(arr):
            return max(low + 1, min(hi - 1, int(round(position))))
        clipped = min(max(position, float(arr[0])), float(arr[-1]))
        index = int(np.searchsorted(arr, clipped))
        candidates = []
        if index < len(arr):
            candidates.append(int(arr[index]))
        if index > 0:
            candidates.append(int(arr[index - 1]))
        return min(candidates, key=lambda value: abs(value - clipped))

    def snap_cut1(self, position: float, cut2: int) -> int:
        return self.snap(position, low=0, high=cut2)

    def snap_cut2(self, position: float, cut1: int) -> int:
        return self.snap(position, low=cut1, high=self.total_frames)

    def nudge(self, position: int, direction: int, *, low: int = 0, high: int | None = None) -> int:
        total = self.total_frames
        hi = total if high is None else min(high, total)
        if direction == 0:
            return self.snap(position, low=low, high=hi)
        if self.boundaries is None and self.step <= 1:
            return max(low + 1, min(hi - 1, position + direction))
        if self.boundaries is None:
            bounds = self._arithmetic_bounds(low, hi)
            if bounds is None:
                return max(low + 1, min(hi - 1, position + direction))
            first, last = bounds
            step = max(1, self.step)
            if direction > 0:
                next_value = (position // step + 1) * step
                return min(last, max(first, next_value))
            previous = ((position - 1) // step) * step
            return max(first, min(last, previous))

        arr = self.boundaries[(self.boundaries > low) & (self.boundaries < hi)]
        if not len(arr):
            return max(low + 1, min(hi - 1, position + direction))
        if direction > 0:
            index = int(np.searchsorted(arr, position, side="right"))
            if index >= len(arr):
                return int(arr[-1])
            return int(arr[index])
        index = int(np.searchsorted(arr, position, side="left")) - 1
        if index < 0:
            return int(arr[0])
        return int(arr[index])


def _packet_boundaries(path: Path, codec: str, sample_rate: int, total_frames: int) -> np.ndarray | None:
    """Read packet end times and map them onto the decoded sample timeline.

    Using pts + duration instead of cumulatively adding packet durations is
    important for Opus: its first packets start at a negative pts (pre-skip),
    so the first real decoded packet boundary is 648 samples into a 48 kHz
    stream, not at 960.  The boundary table remains useful even when all
    packet sizes are equal.
    """
    _, ffprobe = ensure_ffmpeg()
    result = _run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "packet=pts_time,duration_time",
            "-of",
            "json",
            str(path),
        ],
        timeout=90,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(_decode_text(result.stdout))
    except json.JSONDecodeError:
        return None
    ends: list[int] = []
    for packet in payload.get("packets", []):
        try:
            pts = float(packet.get("pts_time"))
            duration = float(packet.get("duration_time"))
        except (TypeError, ValueError):
            continue
        end_sample = int(round((pts + duration) * sample_rate))
        if 0 < end_sample < total_frames:
            ends.append(end_sample)
    if not ends:
        return None
    boundaries = np.unique(np.asarray([0, *ends], dtype=np.int64))
    if len(boundaries) < 2:
        return None
    return boundaries


# ---------------------------------------------------------------------------
# Probe model
# ---------------------------------------------------------------------------


@dataclass
class OutputSpec:
    container: str
    extension: str
    subtype: str
    bit_depth: int
    is_lossy: bool = False


@dataclass
class AudioInfo:
    path: Path
    title: str
    artist: str
    album: str
    codec: str
    container: str
    sample_rate: int
    channels: int
    bit_depth: int
    sample_fmt: str
    duration: float
    frames: int
    start_time: float = 0.0
    frame_step: int = 1
    frame_label: str = "1 采样帧"
    packet_boundaries: np.ndarray | None = field(default=None, repr=False)
    output: OutputSpec | None = None

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def extension_label(self) -> str:
        return EXTENSION_LABELS.get(self.extension, self.codec.upper())

    @property
    def is_lossless_source(self) -> bool:
        return self.codec == "flac" or self.codec == "alac" or self.codec.startswith("pcm_")

    @property
    def precision_label(self) -> str:
        if self.codec.startswith("pcm_") and "f32" in self.codec:
            return "32-bit float"
        if self.codec.startswith("pcm_") and "f64" in self.codec:
            return "64-bit float"
        return f"{self.bit_depth}-bit"

    @property
    def channel_label(self) -> str:
        return {1: "单声道", 2: "立体声"}.get(self.channels, f"{self.channels} 声道")

    @property
    def frame_grid(self) -> FrameGrid:
        return FrameGrid(
            total_frames=self.frames,
            step=max(1, self.frame_step),
            label=self.frame_label,
            boundaries=self.packet_boundaries,
        )

    def summary(self) -> str:
        return (
            f"{self.extension_label} · {self.sample_rate / 1000:g} kHz · "
            f"{self.precision_label} · {self.channel_label}"
        )


def _pick_output_spec(info: AudioInfo) -> OutputSpec:
    ext = info.path.suffix.lower()
    is_double = "dbl" in info.sample_fmt
    is_float = "flt" in info.sample_fmt or is_double
    bit_depth = info.bit_depth or 16

    if ext in {".wav", ".wave"}:
        if is_double:
            subtype, out_bits = "DOUBLE", 64
        elif is_float:
            subtype, out_bits = "FLOAT", 32
        elif bit_depth <= 8:
            subtype, out_bits = "PCM_U8", 8
        elif bit_depth <= 16:
            subtype, out_bits = "PCM_16", 16
        elif bit_depth <= 24:
            subtype, out_bits = "PCM_24", 24
        else:
            subtype, out_bits = "PCM_32", 32
        return OutputSpec("WAV", ".wav", subtype, out_bits)

    if ext in {".aif", ".aiff"}:
        if is_double:
            subtype, out_bits = "DOUBLE", 64
        elif is_float:
            subtype, out_bits = "FLOAT", 32
        elif bit_depth <= 8:
            subtype, out_bits = "PCM_S8", 8
        elif bit_depth <= 16:
            subtype, out_bits = "PCM_16", 16
        elif bit_depth <= 24:
            subtype, out_bits = "PCM_24", 24
        else:
            subtype, out_bits = "PCM_32", 32
        return OutputSpec("AIFF", ".aiff", subtype, out_bits)

    # FLAC sources stay FLAC.  Everything else becomes FLAC so the player gets
    # container-free, seek-free PCM segments with robust metadata.  libsndfile
    # cannot write 32-bit FLAC; rather than silently drop a bit, use 32-bit WAV
    # (still lossless) for those rare sources.
    if bit_depth > 24 and info.is_lossless_source:
        return OutputSpec("WAV", ".wav", "PCM_32", 32, is_lossy=False)
    if bit_depth <= 16 and info.is_lossless_source:
        subtype, out_bits = "PCM_16", 16
    else:
        subtype, out_bits = "PCM_24", 24
    return OutputSpec("FLAC", ".flac", subtype, out_bits, is_lossy=not info.is_lossless_source)


def probe_audio(path: str | Path) -> AudioInfo:
    """Inspect a source and enforce the MVP format contract."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise AudioError("文件不存在。")
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedAudioError(
            f"暂不支持 {ext or '无扩展名'} 格式。MVP 支持 WAV、AIFF、FLAC、MP3、"
            "M4A/AAC、Ogg Opus/Vorbis；请先用工具转换为 WAV 或 FLAC。"
        )

    data = _probe_json(path)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise AudioError("这个文件里没有可用的音频轨道。")
    stream = streams[0]
    codec = str(stream.get("codec_name", "")).lower()
    fmt = data.get("format", {})
    tags = {**fmt.get("tags", {}), **stream.get("tags", {})}

    if ext in {".wav", ".wave", ".aif", ".aiff"} and not codec.startswith(_SAMPLE_CODEC_PREFIXES):
        raise UnsupportedAudioError("该文件的外壳是 WAV/AIFF，但音频不是 PCM，无法按无损采样方式处理。")
    if ext == ".flac" and codec != "flac":
        raise UnsupportedAudioError("该 FLAC 文件未包含 FLAC 音频轨道。")
    if ext == ".mp3" and codec != "mp3":
        raise UnsupportedAudioError("该 MP3 文件未包含 MP3 音频轨道。")
    if ext in {".m4a", ".mp4"} and codec not in {"aac", "alac"}:
        raise UnsupportedAudioError(f"M4A/MP4 音频编码为 {codec or '未知'}，MVP 只支持 AAC 与 ALAC。")
    if ext in {".ogg", ".oga"} and codec not in {"opus", "vorbis"}:
        raise UnsupportedAudioError(f"Ogg 音频编码为 {codec or '未知'}，MVP 只支持 Opus 与 Vorbis。")
    if codec in {"ac3", "eac3"} and ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedAudioError("AC-3/E-AC-3 暂不在 MVP 支持范围内。")

    sample_rate = _safe_int(stream.get("sample_rate"), 0)
    channels = _safe_int(stream.get("channels"), 0)
    if sample_rate <= 0 or channels <= 0:
        raise AudioError("无法确定采样率或声道数。")

    bit_depth = _safe_int(stream.get("bits_per_raw_sample"), 0)
    if bit_depth <= 0:
        bit_depth = _safe_int(stream.get("bits_per_sample"), 0)
    if bit_depth <= 0:
        if codec in {"flac", "alac"}:
            bit_depth = 16
        elif codec.startswith("pcm_"):
            match = re.search(r"(\d+)", codec)
            bit_depth = int(match.group(1)) if match else 16
        else:
            bit_depth = 24

    sample_fmt = str(stream.get("sample_fmt", ""))
    duration = 0.0
    for candidate in (stream.get("duration"), fmt.get("duration")):
        try:
            duration = float(candidate)
            if duration > 0:
                break
        except (TypeError, ValueError):
            continue
    frames = int(round(duration * sample_rate)) if duration > 0 else 0
    start_time = 0.0
    try:
        start_time = float(stream.get("start_time") or 0.0)
    except (TypeError, ValueError):
        pass

    stem = re.sub(r"\s*[-–—]\s*", " - ", path.stem).strip()
    title = _first_tag(tags, "title") or stem
    artist = _first_tag(tags, "artist", "album_artist", "performer") or "未知艺术家"
    album = _first_tag(tags, "album") or ""

    if codec.startswith("pcm_") or codec in {"flac", "alac"}:
        # ALAC is decoded to PCM before cutting and re-saved as FLAC, so cutting
        # on the decoded sample grid is both finer and safer than chasing
        # variable-size ALAC frames.
        step = 1
        label = "1 采样帧"
        boundaries = None
    elif codec in _FIXED_PACKET_FRAMES:
        step = _fixed_packet_step(codec, sample_rate)
        label = f"{step} 采样 / {'MP3' if codec == 'mp3' else 'AAC'} 编码帧"
        boundaries = None
    elif codec in _VARIABLE_PACKET_CODECS:
        fallback = 960 if codec == "opus" else 1024
        label = f"{'Opus' if codec == 'opus' else 'Vorbis'} 包边界"
        boundaries = _packet_boundaries(path, codec, sample_rate, frames) if frames > 0 else None
        if boundaries is not None and len(boundaries) > 2:
            diffs = np.diff(boundaries)
            step = int(np.median(diffs)) if len(diffs) else fallback
        else:
            step = fallback
    else:
        raise UnsupportedAudioError(f"暂不支持 {codec} 编码。请先转换为 WAV 或 FLAC。")

    info = AudioInfo(
        path=path,
        title=title,
        artist=artist,
        album=album,
        codec=codec,
        container=str(fmt.get("format_name", ext.lstrip("."))),
        sample_rate=sample_rate,
        channels=channels,
        bit_depth=bit_depth,
        sample_fmt=sample_fmt,
        duration=duration,
        frames=frames,
        start_time=start_time,
        frame_step=step,
        frame_label=label,
        packet_boundaries=boundaries,
    )
    info.output = _pick_output_spec(info)
    return info


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


@dataclass
class DecodedAudio:
    info: AudioInfo
    pcm: np.ndarray
    peaks: "PeakData"


@dataclass
class PeakData:
    """Precomputed min/max peaks for two display channels."""

    left_min: np.ndarray
    left_max: np.ndarray
    right_min: np.ndarray
    right_max: np.ndarray
    buckets: int
    channels: int


def compute_peaks(pcm: np.ndarray, buckets: int = 8192) -> PeakData:
    frames = pcm.shape[0]
    channels = pcm.shape[1]
    buckets = max(1, min(buckets, max(1, frames)))

    def channel_peaks(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if frames == 0:
            zeros = np.zeros(buckets, dtype=np.float32)
            return zeros, zeros.copy()
        block = int(math.ceil(frames / buckets))
        pad = block * buckets - frames
        if pad:
            values = np.pad(values, (0, pad), mode="edge")
        reshaped = values.reshape(buckets, block)
        return reshaped.min(axis=1).astype(np.float32), reshaped.max(axis=1).astype(np.float32)

    left_min, left_max = channel_peaks(pcm[:, 0])
    if channels >= 2:
        right_min, right_max = channel_peaks(pcm[:, 1])
    else:
        right_min = np.zeros_like(left_min)
        right_max = np.zeros_like(left_max)
    return PeakData(left_min, left_max, right_min, right_max, buckets, channels)


def decode_audio(path: str | Path, progress: ProgressCallback | None = None) -> DecodedAudio:
    """Decode a supported file to interleaved float32 PCM via ffmpeg."""
    if progress:
        progress(12, "读取音频信息")
    info = probe_audio(path)
    ffmpeg, _ = ensure_ffmpeg()
    if progress:
        progress(28, "解码为 PCM")

    # float32 has a 24-bit mantissa and is exact for 8/16/24-bit PCM.  32-bit
    # integer PCM and 64-bit float need the wider carrier or the "lossless"
    # promise would quietly lose low bits before the cut is ever made.
    is_float_source = "flt" in info.sample_fmt or "dbl" in info.sample_fmt
    use_double = "dbl" in info.sample_fmt or (info.bit_depth > 24 and not is_float_source)
    raw_format = "f64le" if use_double else "f32le"
    raw_codec = "pcm_f64le" if use_double else "pcm_f32le"
    raw_dtype = "<f8" if use_double else "<f4"

    result = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            str(info.path),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-threads",
            "0",
            "-f",
            raw_format,
            "-acodec",
            raw_codec,
            "-",
        ],
        timeout=600,
    )
    if result.returncode != 0:
        message = _decode_text(result.stderr).strip()
        raise AudioError(f"解码失败：{message or '未知错误'}")
    raw = result.stdout
    bytes_per_sample = 8 if use_double else 4
    denominator = bytes_per_sample * info.channels
    if not raw or len(raw) % denominator:
        raise AudioError("解码器输出的 PCM 数据不完整。")
    pcm = np.frombuffer(raw, dtype=raw_dtype).reshape(-1, info.channels).copy()
    if pcm.shape[0] < 3:
        raise AudioError("音频太短，无法切出开头、循环和结尾三段。")
    info.frames = int(pcm.shape[0])
    info.duration = info.frames / info.sample_rate
    info.packet_boundaries = None
    if info.codec in _FIXED_PACKET_FRAMES:
        # Fixed-length compressed frames remain aligned to the decoded stream.
        info.frame_step = _fixed_packet_step(info.codec, info.sample_rate)
    elif info.codec in _VARIABLE_PACKET_CODECS:
        boundaries = _packet_boundaries(info.path, info.codec, info.sample_rate, info.frames)
        info.packet_boundaries = boundaries
        if boundaries is not None and len(boundaries) > 2:
            info.frame_step = int(np.median(np.diff(boundaries)))
    if progress:
        progress(76, "分析波形")
    peaks = compute_peaks(pcm)
    if progress:
        progress(100, "准备就绪")
    return DecodedAudio(info=info, pcm=pcm, peaks=peaks)


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


@dataclass
class SavedTrack:
    track_id: str
    directory: Path
    meta: dict


def _validate_cuts(info: AudioInfo, cut1: int, cut2: int) -> None:
    if not (0 < cut1 < cut2 < info.frames):
        raise AudioError("切点必须满足 0 < 切点 A < 切点 B < 曲目总长。")


def _timestamp_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def extract_cover(source: Path, destination: Path) -> bool:
    try:
        ffmpeg, _ = ensure_ffmpeg()
    except AudioError:
        return False
    result = _run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-vf",
            "scale='min(900,iw)':-2",
            str(destination),
        ],
        timeout=30,
    )
    return result.returncode == 0 and destination.exists() and destination.stat().st_size > 0


def save_track(
    decoded: DecodedAudio,
    cut1: int,
    cut2: int,
    *,
    library_dir: str | Path,
    title: str,
    artist: str,
    album: str | None = None,
    progress: ProgressCallback | None = None,
) -> SavedTrack:
    """Write three lossless segments plus metadata, atomically."""
    info = decoded.info
    pcm = decoded.pcm
    cut1, cut2 = int(cut1), int(cut2)
    _validate_cuts(info, cut1, cut2)
    output = info.output or _pick_output_spec(info)
    library = Path(library_dir)
    library.mkdir(parents=True, exist_ok=True)

    fragments = [
        ("intro", pcm[:cut1], 0, cut1),
        ("loop", pcm[cut1:cut2], cut1, cut2),
        ("outro", pcm[cut2:], cut2, info.frames),
    ]
    track_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    final_dir = library / track_id
    temp_dir = library / f".tmp-{track_id}"
    temp_dir.mkdir(parents=True, exist_ok=False)

    try:
        if progress:
            progress(4, "提取封面")
        # PNG avoids carrying the source JPEG's sometimes-invalid ICC profile
        # into Qt, which otherwise prints noisy libpng warnings on every load.
        has_cover = extract_cover(info.path, temp_dir / "cover.png")
        if progress:
            progress(12, "写入无损片段")

        segment_meta: dict[str, dict] = {}
        for index, (name, data, start, end) in enumerate(fragments):
            segment_path = temp_dir / f"{name}{output.extension}"
            try:
                sf.write(
                    str(segment_path),
                    np.ascontiguousarray(data),
                    info.sample_rate,
                    format=output.container,
                    subtype=output.subtype,
                )
            except Exception as exc:
                raise AudioError(f"写入 {name} 片段失败：{exc}") from exc
            segment_meta[name] = {
                "file": segment_path.name,
                "start_sample": start,
                "end_sample": end,
                "frames": end - start,
                "bytes": segment_path.stat().st_size,
            }
            if progress:
                progress(20 + int((index + 1) / len(fragments) * 62), f"写入{ {'intro': '开头', 'loop': '循环', 'outro': '结尾'}[name] }")

        meta = {
            "schema": 1,
            "id": track_id,
            "title": title.strip() or info.title,
            "artist": artist.strip() or info.artist,
            "album": (album if album is not None else info.album) or "",
            "created_at": _timestamp_iso(),
            "source": {
                "path": str(info.path),
                "filename": info.path.name,
                "codec": info.codec,
                "container": info.container,
                "sample_rate": info.sample_rate,
                "channels": info.channels,
                "bit_depth": info.bit_depth,
                "sample_fmt": info.sample_fmt,
                "duration": info.duration,
                "frames": info.frames,
                "frame_label": info.frame_label,
            },
            "cut": {
                "start_sample": cut1,
                "end_sample": cut2,
                "start_time": cut1 / info.sample_rate,
                "end_time": cut2 / info.sample_rate,
                "loop_frames": cut2 - cut1,
                "frame_step": max(1, info.frame_step),
            },
            "segments": segment_meta,
            "playback": {
                "sample_rate": info.sample_rate,
                "channels": info.channels,
                "format": output.container,
                "subtype": output.subtype,
                "bit_depth": output.bit_depth,
                "lossy_source": output.is_lossy,
            },
            "cover": "cover.png" if has_cover else None,
            "total_bytes": 0,
        }
        meta["total_bytes"] = sum(seg["bytes"] for seg in segment_meta.values()) + (
            (temp_dir / "cover.png").stat().st_size if has_cover else 0
        )
        metadata_path = temp_dir / "meta.json"
        metadata_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if progress:
            progress(90, "整理曲库")
        os.replace(temp_dir, final_dir)
        meta["_directory"] = str(final_dir)
        meta["_cover_path"] = str(final_dir / "cover.png") if has_cover else None
        if progress:
            progress(100, "保存完成")
        return SavedTrack(track_id=track_id, directory=final_dir, meta=meta)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


# ---------------------------------------------------------------------------
# Library and loading
# ---------------------------------------------------------------------------


def default_library_dir() -> Path:
    return Path(__file__).resolve().parent.parent / LIBRARY_DIRNAME


def read_track_meta(directory: str | Path) -> dict | None:
    directory = Path(directory)
    metadata_path = directory / "meta.json"
    if not metadata_path.exists():
        return None
    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict) or "id" not in meta:
        return None
    meta["_directory"] = str(directory)
    return meta


def scan_library(library_dir: str | Path | None = None) -> list[dict]:
    root = Path(library_dir) if library_dir else default_library_dir()
    if not root.exists():
        return []
    tracks: list[dict] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        meta = read_track_meta(entry)
        if meta:
            cover = entry / meta["cover"] if meta.get("cover") else None
            if cover and cover.exists():
                meta["_cover_path"] = str(cover)
            else:
                meta["_cover_path"] = None
            tracks.append(meta)
    tracks.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return tracks


def delete_track(meta: dict) -> None:
    directory = Path(meta.get("_directory") or "")
    if directory.exists():
        shutil.rmtree(directory)


@dataclass
class LoadedTrack:
    meta: dict
    intro: np.ndarray
    loop: np.ndarray
    outro: np.ndarray
    sample_rate: int
    channels: int

    @property
    def total_loop_frames(self) -> int:
        return int(self.loop.shape[0])


def track_segment_paths(meta: dict) -> dict[str, Path]:
    directory = Path(meta["_directory"])
    return {
        name: directory / details["file"]
        for name, details in meta.get("segments", {}).items()
    }


def load_track(meta: dict) -> LoadedTrack:
    paths = track_segment_paths(meta)
    missing = [name for name in ("intro", "loop", "outro") if name not in paths or not paths[name].exists()]
    if missing:
        raise AudioError("曲目文件不完整，请重新剪辑保存。")
    arrays: dict[str, np.ndarray] = {}
    sample_rate = int(meta.get("playback", {}).get("sample_rate", 0))
    channels = int(meta.get("playback", {}).get("channels", 0))
    for name in ("intro", "loop", "outro"):
        try:
            data, sr = sf.read(str(paths[name]), dtype="float32", always_2d=True)
        except Exception as exc:
            raise AudioError(f"读取{ {'intro': '开头', 'loop': '循环', 'outro': '结尾'}[name] }片段失败：{exc}") from exc
        if sample_rate and sr != sample_rate:
            raise AudioError("片段采样率不一致，曲目可能已损坏。")
        if channels and data.shape[1] != channels:
            raise AudioError("片段声道数不一致，曲目可能已损坏。")
        sample_rate = sr
        channels = data.shape[1]
        arrays[name] = np.ascontiguousarray(data, dtype=np.float32)
    return LoadedTrack(meta=meta, intro=arrays["intro"], loop=arrays["loop"], outro=arrays["outro"], sample_rate=sample_rate, channels=channels)


def library_total_size(tracks: Iterable[dict]) -> int:
    return sum(int(track.get("total_bytes", 0)) for track in tracks)


# ---------------------------------------------------------------------------
# Test / helper convenience
# ---------------------------------------------------------------------------


def save_cut_track_from_file(
    source: str | Path,
    cut1: int,
    cut2: int,
    *,
    library_dir: str | Path | None = None,
    title: str | None = None,
    artist: str | None = None,
    progress: ProgressCallback | None = None,
) -> SavedTrack:
    decoded = decode_audio(source, progress)
    return save_track(
        decoded,
        cut1,
        cut2,
        library_dir=library_dir or default_library_dir(),
        title=title if title is not None else decoded.info.title,
        artist=artist if artist is not None else decoded.info.artist,
        progress=progress,
    )
