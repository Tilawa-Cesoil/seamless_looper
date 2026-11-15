"""Background workers for operations that must not freeze the waveform UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from . import audio_io
from .audio_io import DecodedAudio, SavedTrack


class ImportWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, path: str | Path, parent=None):
        super().__init__(parent)
        self.path = str(path)

    def run(self) -> None:  # noqa: D401
        try:
            decoded = audio_io.decode_audio(
                self.path,
                progress=lambda value, message: self.progress.emit(int(value), str(message)),
            )
            self.completed.emit(decoded)
        except Exception as exc:
            self.failed.emit(str(exc))


class SaveWorker(QThread):
    progress = Signal(int, str)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        decoded: DecodedAudio,
        cut1: int,
        cut2: int,
        *,
        library_dir: str | Path,
        title: str,
        artist: str,
        album: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.decoded = decoded
        self.cut1 = int(cut1)
        self.cut2 = int(cut2)
        self.library_dir = str(library_dir)
        self.title = title
        self.artist = artist
        self.album = album

    def run(self) -> None:  # noqa: D401
        try:
            saved: SavedTrack = audio_io.save_track(
                self.decoded,
                self.cut1,
                self.cut2,
                library_dir=self.library_dir,
                title=self.title,
                artist=self.artist,
                album=self.album,
                progress=lambda value, message: self.progress.emit(int(value), str(message)),
            )
            self.completed.emit(saved)
        except Exception as exc:
            self.failed.emit(str(exc))
