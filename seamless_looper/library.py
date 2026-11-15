"""The library: saved, loop-ready pieces of music."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .audio_io import format_duration, format_timestamp, human_size
from .widgets import CoverArt, ElidedLabel, GlyphButton, LoopMark, label


def format_added_at(value) -> str:
    """Turn a stored ISO timestamp into a compact local label."""
    if not value:
        return ""
    text = str(value)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text.replace("T", " ")[:19]


class TrackCard(QFrame):
    play_requested = Signal(dict)
    delete_requested = Signal(dict)
    open_folder_requested = Signal(dict)

    def __init__(self, meta: dict, *, active: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.meta = meta
        self._active = bool(active)
        self.setObjectName("TrackCard")
        self.setProperty("active", "true" if active else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(104)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._build()

    def _build(self) -> None:
        meta = self.meta
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 13, 14, 13)
        layout.setSpacing(15)

        self.cover = CoverArt(78, 14, seed=str(meta.get("title", "")))
        self.cover.set_cover(meta.get("_cover_path"), str(meta.get("title", "")))
        layout.addWidget(self.cover)

        info = QVBoxLayout()
        info.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title = ElidedLabel(str(meta.get("title", "未命名")))
        self.title.setFont(theme.font(18, 600))
        self.title.setStyleSheet(f"color: {theme.TEXT};")
        title_row.addWidget(self.title, 1)
        self.playing_chip = label("播放中", object_name="Chip")
        self.playing_chip.setStyleSheet(
            f"color: {theme.ACCENT}; background: {theme.ACCENT_DARK}; "
            f"border: 1px solid #5A4426; border-radius: 9px; padding: 2px 8px; font-size: 10px;"
        )
        self.playing_chip.setVisible(self._active)
        title_row.addWidget(self.playing_chip, 0)
        info.addLayout(title_row)

        artist = str(meta.get("artist", "未知艺术家"))
        album = str(meta.get("album", "")).strip()
        subtitle = artist if not album else f"{artist}  ·  {album}"
        self.artist = ElidedLabel(subtitle)
        self.artist.setFont(theme.font(12, 400))
        self.artist.setStyleSheet(f"color: {theme.MUTED};")
        info.addWidget(self.artist)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        source = meta.get("source", {})
        cut = meta.get("cut", {})
        playback = meta.get("playback", {})
        sample_rate = int(playback.get("sample_rate", source.get("sample_rate", 44100)))
        loop_frames = int(cut.get("loop_frames", 0))
        loop_chip = label(f"循环 {format_timestamp(loop_frames, sample_rate)}", object_name="Chip")
        loop_chip.setStyleSheet(
            f"background: {theme.MINT_DARK}; border: 1px solid #2E5243; color: {theme.MINT}; "
            f"border-radius: 9px; padding: 2px 8px; font-size: 10px;"
        )
        chips.addWidget(loop_chip)
        original_frames = int(source.get("frames", 0))
        if original_frames <= 0:
            original_frames = int(round(float(source.get("duration", 0)) * sample_rate))
        chips.addWidget(label(f"原曲 {format_duration(original_frames / sample_rate)}", object_name="Chip"))
        bits = playback.get("bit_depth", source.get("bit_depth", ""))
        if bits:
            chips.addWidget(label(f"{bits}-bit", object_name="Chip"))
        chips.addWidget(label(f"{sample_rate / 1000:g} kHz", object_name="Chip"))
        added_text = format_added_at(meta.get("created_at"))
        if added_text:
            chips.addSpacing(5)
            added = label(f"添加于 {added_text}", object_name="Tiny")
            added.setToolTip(str(meta.get("created_at", "")))
            chips.addWidget(added)
        chips.addStretch(1)
        info.addLayout(chips)
        layout.addLayout(info, 1)

        right = QVBoxLayout()
        right.setSpacing(4)
        right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.play_button = GlyphButton("chevron")
        self.play_button.setProperty("role", "round")
        self.play_button.set_glyph_color(theme.TEXT)
        self.play_button.setToolTip("打开播放器")
        self.play_button.clicked.connect(lambda: self.play_requested.emit(self.meta))
        right.addWidget(self.play_button, 0, Qt.AlignmentFlag.AlignRight)
        self.size_label = label(human_size(int(meta.get("total_bytes", 0))), object_name="Tiny")
        self.size_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(self.size_label)
        layout.addLayout(right)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.playing_chip.setVisible(active)
        self.setProperty("active", "true" if active else "false")
        theme.repolish(self)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.play_requested.emit(self.meta)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        play = QAction("打开播放器", menu)
        play.triggered.connect(lambda: self.play_requested.emit(self.meta))
        menu.addAction(play)
        folder = QAction("在资源管理器中显示", menu)
        folder.triggered.connect(lambda: self.open_folder_requested.emit(self.meta))
        menu.addAction(folder)
        menu.addSeparator()
        delete = QAction("从曲库删除…", menu)
        delete.triggered.connect(lambda: self.delete_requested.emit(self.meta))
        menu.addAction(delete)
        menu.exec(event.globalPos())


class LibraryPage(QWidget):
    import_requested = Signal()
    play_requested = Signal(dict)
    delete_requested = Signal(dict)
    open_folder_requested = Signal(dict)
    library_folder_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cards: list[TrackCard] = []
        self._active_id: str | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 34, 24)
        layout.setSpacing(18)

        header = QHBoxLayout()
        header.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Your library", object_name="Eyebrow"))
        titles.addWidget(label("曲库", object_name="PageTitle"))
        lead = label(
            "这里保存着你的🧊！   🧊🧊🧊~（这你冰我？）",
            object_name="PageLead",
        )
        titles.addWidget(lead)
        header.addLayout(titles, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.folder_button = QPushButton("打开曲库")
        self.folder_button.setProperty("role", "ghost")
        self.folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_button.clicked.connect(self.library_folder_requested.emit)
        buttons.addWidget(self.folder_button)
        self.import_button = QPushButton("＋  导入音频")
        self.import_button.setProperty("role", "primary")
        self.import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.import_button.clicked.connect(self.import_requested.emit)
        buttons.addWidget(self.import_button)
        header.addLayout(buttons)
        layout.addLayout(header)

        stat_row = QHBoxLayout()
        self.count_label = label("0 首曲目", object_name="Tiny")
        stat_row.addWidget(self.count_label)
        self.stat_dot = label("·", object_name="Tiny")
        stat_row.addWidget(self.stat_dot)
        self.size_label = label("", object_name="Tiny")
        stat_row.addWidget(self.size_label)
        stat_row.addStretch(1)
        formats = label("WAV  ·  AIFF  ·  FLAC  ·  MP3  ·  M4A/AAC  ·  OGG", object_name="Tiny")
        stat_row.addWidget(formats)
        layout.addLayout(stat_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.content = QWidget()
        self.content.setStyleSheet("background: transparent;")
        self.cards_layout = QVBoxLayout(self.content)
        self.cards_layout.setContentsMargins(0, 2, 4, 8)
        self.cards_layout.setSpacing(10)
        self.cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)

        self.empty_panel = self._build_empty()
        layout.addWidget(self.empty_panel, 1)

    def _build_empty(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("EmptyPanel")
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(40, 32, 40, 32)
        layout.setSpacing(10)
        layout.addStretch(1)
        mark = LoopMark(156)
        layout.addWidget(mark, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(6)
        heading = label("添加你的第一块🧊", font=theme.serif(26, 600))
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        copy = label(
            "曲库空空，\n"
            "赶快导入喜欢的音乐吧 ~",
            object_name="PageLead",
            wrap=True,
        )
        copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(copy)
        layout.addSpacing(6)
        import_button = QPushButton("＋  导入第一首音频")
        import_button.setProperty("role", "primary")
        import_button.setCursor(Qt.CursorShape.PointingHandCursor)
        import_button.clicked.connect(self.import_requested.emit)
        layout.addWidget(import_button, 0, Qt.AlignmentFlag.AlignHCenter)
        support = label(
            "本机 ffmpeg 解码  ·  切点吸附编码帧边界  ·  不重采样 / 不二次有损编码",
            object_name="Tiny",
        )
        support.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(support)
        layout.addStretch(1)
        return panel

    def refresh(self, tracks: list[dict], active_id: str | None = None) -> None:
        self._active_id = active_id
        self._clear_cards()
        total_size = sum(int(track.get("total_bytes", 0)) for track in tracks)
        self.count_label.setText(f"{len(tracks)} 首曲目")
        self.size_label.setText(human_size(total_size) if tracks else "")
        self.stat_dot.setVisible(bool(tracks))
        self.empty_panel.setVisible(not tracks)
        self.scroll.setVisible(bool(tracks))

        if not tracks:
            return
        for meta in tracks:
            card = TrackCard(meta, active=str(meta.get("id")) == str(active_id))
            card.play_requested.connect(self.play_requested.emit)
            card.delete_requested.connect(self.delete_requested.emit)
            card.open_folder_requested.connect(self.open_folder_requested.emit)
            self.cards_layout.addWidget(card)
            self._cards.append(card)
        self.cards_layout.addStretch(1)

    def set_active(self, active_id: str | None) -> None:
        self._active_id = active_id
        for card in self._cards:
            card.set_active(str(card.meta.get("id")) == str(active_id))

    def _clear_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Take it out of the visual tree immediately.  deleteLater()
                # alone leaves the old card painted until the event loop gets
                # around to it, which produces ghost cards after a refresh.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._cards.clear()
