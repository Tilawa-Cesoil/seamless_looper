"""
Manual audio-engine self-test (uses the default output device at volume 0).

Run:  python tools/selftest_audio.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from PySide6.QtCore import QCoreApplication, QTimer

from seamless_looper.audio_io import LoadedTrack
from seamless_looper.playback import StreamPlayer

app = QCoreApplication(sys.argv)
SR = 44100
CH = 2


def make_track(intro_s: float, loop_s: float, outro_s: float) -> LoadedTrack:
    def block(seconds: float, value: float) -> np.ndarray:
        frames = int(SR * seconds)
        return np.full((frames, CH), value, dtype=np.float32)

    return LoadedTrack(
        meta={},
        intro=block(intro_s, 0.02),
        loop=block(loop_s, 0.04),
        outro=block(outro_s, 0.01),
        sample_rate=SR,
        channels=CH,
    )


def run_case(name: str, track: LoadedTrack, repeats: int | None, timeout_s: float, end_after: float | None = None) -> None:
    player = StreamPlayer()
    errors: list[str] = []
    player.error_occurred.connect(errors.append)
    player.start(track, repeats, volume=0.0)
    started = time.monotonic()
    if end_after is not None:
        QTimer.singleShot(int(end_after * 1000), player.request_end_loop)
    deadline = started + timeout_s
    while time.monotonic() < deadline and player.active:
        app.processEvents()
        time.sleep(0.01)
    elapsed = time.monotonic() - started
    state = "finished" if not player.active else "still active"
    print(
        f"{name}: {state} in {elapsed:.2f}s; underruns={player.underruns}; "
        f"loops={player.loops_completed}; errors={errors}"
    )
    if player.active:
        player.stop(immediate=True)
    else:
        player.cleanup_finished_stream()
    if errors:
        raise RuntimeError(errors)
    if state != "finished":
        raise RuntimeError(f"{name} did not finish")
    if player.underruns:
        raise RuntimeError(f"{name} had underruns: {player.underruns}")


run_case("finite x2", make_track(0.12, 0.10, 0.09), 2, 3.0)
run_case("infinite + end loop", make_track(0.10, 0.12, 0.09), None, 3.0, end_after=0.12)
run_case("single pass", make_track(0.08, 0.08, 0.08), 1, 3.0)
print("audio engine self-test passed")
