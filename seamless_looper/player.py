"""The listening room: a quiet player with no timeline, only flow."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .audio_io import AudioError, LoadedTrack, format_timestamp, load_track
from .playback import StreamPlayer
from .widgets import AlbumStage, ElidedLabel, SegmentedControl, label


class PlayerPage(QWidget):
    back_requested = Signal()
    notice = Signal(str, str)
    state_changed = Signal(str)
    settings_changed = Signal(int, int, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._meta: dict | None = None
        self._loaded: LoadedTrack | None = None
        self._state = "stopped"
        self._end_requested = False
        self._in_outro = False
        self._engine = StreamPlayer(self)
        self._engine.finished.connect(self._on_engine_finished)
        self._engine.phase_changed.connect(self._on_phase_changed)
        self._engine.error_occurred.connect(lambda message: self.notice.emit(message, "error"))
        self._build_ui()

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 26)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(12)
        back = QPushButton("←   返回曲库")
        back.setProperty("role", "ghost")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        header.addStretch(1)
        self.output_chip = label("PortAudio  ·  无缝输出", object_name="Chip")
        self.output_chip.setStyleSheet(
            f"color: {theme.MINT}; background: {theme.MINT_DARK}; "
            f"border: 1px solid #2E5243; border-radius: 10px; padding: 4px 10px; font-size: 10px;"
        )
        header.addWidget(self.output_chip)
        layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(42)
        body.addStretch(1)
        self.stage = AlbumStage(392)
        self.stage.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        body.addWidget(self.stage, 0, Qt.AlignmentFlag.AlignVCenter)
        body.addSpacing(6)

        info_host = QWidget()
        info_host.setFixedWidth(510)
        info = QVBoxLayout(info_host)
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(0)
        info.addStretch(1)
        self.eyebrow = label("Ready to loop", object_name="Eyebrow")
        info.addWidget(self.eyebrow)
        info.addSpacing(6)

        self.title_label = ElidedLabel("未选择曲目")
        self.title_label.setFont(theme.serif(42, 600, spacing=-0.4))
        self.title_label.setStyleSheet(f"color: {theme.TEXT};")
        self.title_label.setMinimumHeight(58)
        info.addWidget(self.title_label)

        self.artist_label = ElidedLabel("")
        self.artist_label.setFont(theme.font(14, 400))
        self.artist_label.setStyleSheet(f"color: {theme.MUTED};")
        info.addWidget(self.artist_label)
        info.addSpacing(13)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.format_chip = label("", object_name="Chip")
        self.loop_chip = label("", object_name="Chip")
        self.loop_chip.setStyleSheet(
            f"color: {theme.MINT}; background: {theme.MINT_DARK}; border: 1px solid #2E5243; "
            f"border-radius: 9px; padding: 3px 9px; font-size: 10px;"
        )
        chips.addWidget(self.format_chip)
        chips.addWidget(self.loop_chip)
        chips.addStretch(1)
        info.addLayout(chips)
        info.addSpacing(20)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme.LINE_SOFT};")
        info.addWidget(divider)
        info.addSpacing(16)

        mode_top = QHBoxLayout()
        mode_top.addWidget(label("循环方式", object_name="Tiny"))
        mode_top.addStretch(1)
        self.mode_hint = label("", object_name="Tiny")
        mode_top.addWidget(self.mode_hint)
        info.addLayout(mode_top)
        info.addSpacing(7)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        self.mode_control = SegmentedControl(["循环 n 次", "无限循环"])
        self.mode_control.setFixedWidth(286)
        self.mode_control.changed.connect(self._mode_changed)
        mode_row.addWidget(self.mode_control)
        self.n_spin = QSpinBox()
        self.n_spin.setRange(1, 9999)
        self.n_spin.setValue(3)
        self.n_spin.setSuffix(" 次")
        self.n_spin.setFixedWidth(94)
        self.n_spin.setToolTip("循环段播放次数，必须为大于等于 1 的整数")
        self.n_spin.valueChanged.connect(self._n_changed)
        mode_row.addWidget(self.n_spin)
        self.infinite_mark = label("∞", font=theme.numeric(27, 400))
        self.infinite_mark.setStyleSheet(f"color: {theme.ACCENT};")
        self.infinite_mark.hide()
        mode_row.addWidget(self.infinite_mark)
        mode_row.addStretch(1)
        info.addLayout(mode_row)
        info.addSpacing(18)

        volume_row = QHBoxLayout()
        volume_row.setSpacing(10)
        speaker = QLabel("音量")
        speaker.setFont(theme.font(10.5, 500))
        speaker.setStyleSheet(f"color: {theme.MUTED};")
        volume_row.addWidget(speaker)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(82)
        self.volume_slider.setFixedWidth(210)
        self.volume_slider.valueChanged.connect(self._volume_changed)
        volume_row.addWidget(self.volume_slider)
        self.volume_value = label("82%", font=theme.font(10.5, 500, theme.MONO))
        self.volume_value.setStyleSheet(f"color: {theme.TEXT_DIM};")
        self.volume_value.setFixedWidth(38)
        volume_row.addWidget(self.volume_value)
        volume_row.addStretch(1)
        info.addLayout(volume_row)
        info.addSpacing(22)

        self.main_button = QPushButton("开始循环")
        self.main_button.setProperty("role", "primary")
        self.main_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.main_button.setMinimumWidth(136)
        self.main_button.setMinimumHeight(44)
        self.main_button.clicked.connect(self._main_action)
        self.end_button = QPushButton("结束循环")
        self.end_button.setProperty("role", "secondary")
        self.end_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.end_button.setMinimumWidth(106)
        self.end_button.clicked.connect(self._end_loop)
        self.exit_button = QPushButton("立即退出")
        self.exit_button.setProperty("role", "danger")
        self.exit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.exit_button.clicked.connect(self._exit)
        button_row = QHBoxLayout()
        button_row.setSpacing(9)
        button_row.addWidget(self.main_button)
        button_row.addWidget(self.end_button)
        button_row.addWidget(self.exit_button)
        button_row.addStretch(1)
        info.addLayout(button_row)
        info.addSpacing(13)

        status_row = QHBoxLayout()
        status_row.setSpacing(7)
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet(f"color: {theme.MUTED_DARK}; font-size: 8px;")
        status_row.addWidget(self.status_dot)
        self.status_label = label("等待开始", object_name="Muted", font=theme.font(11))
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        info.addLayout(status_row)
        info.addSpacing(4)
        self.structure_label = label("", object_name="Tiny")
        info.addWidget(self.structure_label)
        info.addStretch(1)

        body.addWidget(info_host, 0)
        body.addStretch(1)
        layout.addLayout(body, 1)

    # -- public API ---------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def meta(self) -> dict | None:
        return self._meta

    @property
    def is_active(self) -> bool:
        return self._state in {"playing", "paused"}

    def load_track(self, meta: dict) -> None:
        self.stop_immediately()
        loaded = load_track(meta)
        self._meta = meta
        self._loaded = loaded
        self._end_requested = False
        self._in_outro = False
        self._set_state("stopped")
        self._apply_meta()
        self._refresh_controls()

    def stop_immediately(self) -> None:
        if self._engine.active:
            self._engine.stop(immediate=True, emit=False)
        self._engine.cleanup_finished_stream()
        self.stage.set_playing(False)
        self._end_requested = False
        self._in_outro = False
        if self._state != "stopped":
            self._set_state("stopped")
        self._refresh_controls()

    def start_playback(self) -> None:
        if self._state == "stopped":
            self._start_playback()

    def toggle_pause(self) -> None:
        if self._state == "playing":
            self._main_action()
        elif self._state == "paused":
            self._main_action()

    def clear(self) -> None:
        self.stop_immediately()
        self._meta = None
        self._loaded = None
        self._in_outro = False
        self.title_label.setText("未选择曲目")
        self.artist_label.setText("")
        self.format_chip.setText("")
        self.loop_chip.setText("")
        self.structure_label.setText("")
        self.status_label.setText("等待开始")
        self.stage.set_cover(None, "")
        self.main_button.setEnabled(False)

    def shutdown(self) -> None:
        self.stop_immediately()

    def set_playback_defaults(self, mode: int, repeats: int, volume: int) -> None:
        self.mode_control.set_index(max(0, min(1, mode)), emit=False)
        self.n_spin.setValue(max(1, repeats))
        self.volume_slider.setValue(max(0, min(100, volume)))
        self._update_mode_visibility()
        self._update_volume_label(volume)
        if self._loaded:
            self._refresh_controls()

    # -- display ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.BG))
        glow = QRadialGradient(self.width() * 0.31, self.height() * 0.52, self.width() * 0.48)
        glow.setColorAt(0.0, QColor(231, 179, 106, 17))
        glow.setColorAt(0.52, QColor(120, 198, 168, 7))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(self.rect(), glow)
        painter.end()

    def _apply_meta(self) -> None:
        meta = self._meta or {}
        source = meta.get("source", {})
        playback = meta.get("playback", {})
        cut = meta.get("cut", {})
        sr = int(playback.get("sample_rate", source.get("sample_rate", 44100)))
        bits = int(playback.get("bit_depth", source.get("bit_depth", 16)))
        title = str(meta.get("title", "未命名"))
        artist = str(meta.get("artist", "未知艺术家"))
        self.title_label.setText(title)
        self.artist_label.setText(artist)
        self.stage.set_cover(meta.get("_cover_path"), title)
        codec = str(source.get("codec", "")).upper()
        format_name = str(meta.get("playback", {}).get("format", ""))
        if format_name:
            self.format_chip.setText(f"{format_name}  ·  {sr / 1000:g} kHz  ·  {bits}-bit")
        else:
            self.format_chip.setText(f"{codec}  ·  {sr / 1000:g} kHz  ·  {bits}-bit")
        loop_frames = int(cut.get("loop_frames", 0))
        self.loop_chip.setText(f"循环段 {format_timestamp(loop_frames, sr)}")
        self.structure_label.setText(
            f"结构：引子  {format_timestamp(int(cut.get('start_sample', 0)), sr)}"
            f"  →  循环段  →  尾声"
        )
        self._state = "stopped"

    # -- state --------------------------------------------------------------
    def _set_state(self, state: str) -> None:
        changed = state != self._state
        self._state = state
        self.stage.set_playing(state == "playing")
        self._refresh_controls()
        if changed:
            self.state_changed.emit(state)

    def _refresh_controls(self) -> None:
        active = self._state in {"playing", "paused"}
        if self._state == "playing":
            self.main_button.setText("暂停")
            self.status_label.setText("正在无缝循环")
            self.eyebrow.setText("Now looping")
            self.status_dot.setStyleSheet(f"color: {theme.ACCENT}; font-size: 8px;")
        elif self._state == "paused":
            self.main_button.setText("继续播放")
            self.status_label.setText("已暂停 · 输出流保持连接")
            self.eyebrow.setText("Paused")
            self.status_dot.setStyleSheet(f"color: {theme.MINT}; font-size: 8px;")
        else:
            self.main_button.setText("开始循环")
            self.status_label.setText("等待开始")
            self.eyebrow.setText("Ready to loop")
            self.status_dot.setStyleSheet(f"color: {theme.MUTED_DARK}; font-size: 8px;")

        self.main_button.setEnabled(self._loaded is not None)
        self.end_button.setEnabled(active and not self._end_requested and not self._in_outro)
        self.exit_button.setEnabled(active)
        if self._in_outro:
            self.end_button.setText("已进入尾声")
        else:
            self.end_button.setText("本轮后收尾" if self._end_requested else "结束循环")
        self.mode_control.setEnabled(not active)
        self.n_spin.setEnabled(not active)
        self._update_mode_visibility()
        self._update_mode_hint()

    def _update_mode_visibility(self) -> None:
        infinite = self.mode_control.index() == 1
        self.n_spin.setVisible(not infinite)
        self.infinite_mark.setVisible(infinite)

    def _update_mode_hint(self) -> None:
        if self._state in {"playing", "paused"}:
            self.mode_hint.setText("播放中不可更改")
        elif self.mode_control.index() == 1:
            self.mode_hint.setText("直到你按下结束循环")
        else:
            self.mode_hint.setText("播完即停")

    def _update_volume_label(self, value: int) -> None:
        self.volume_value.setText(f"{value}%")

    # -- interactions -------------------------------------------------------
    def _mode_changed(self, index: int) -> None:
        self._update_mode_visibility()
        self._update_mode_hint()
        self._emit_settings()

    def _n_changed(self, value: int) -> None:
        self._update_mode_hint()
        self._emit_settings()

    def _volume_changed(self, value: int) -> None:
        self._update_volume_label(value)
        self._engine.set_volume(value / 100.0)
        self._emit_settings()

    def _emit_settings(self) -> None:
        self.settings_changed.emit(
            self.mode_control.index(),
            self.n_spin.value(),
            self.volume_slider.value(),
        )

    def _main_action(self) -> None:
        if self._state == "stopped":
            self._start_playback()
        elif self._state == "playing":
            self._engine.pause(True)
            self._set_state("paused")
        elif self._state == "paused":
            self._engine.pause(False)
            self._set_state("playing")

    def _start_playback(self) -> None:
        if self._loaded is None or self._meta is None:
            return
        repeats = None if self.mode_control.index() == 1 else int(self.n_spin.value())
        self._end_requested = False
        self._in_outro = False
        try:
            self._engine.start(self._loaded, repeats, self.volume_slider.value() / 100.0)
        except AudioError as exc:
            self.notice.emit(str(exc), "error")
            self._set_state("stopped")
            return
        except Exception as exc:
            self.notice.emit(f"无法开始播放：{exc}", "error")
            self._set_state("stopped")
            return
        self._set_state("playing")

    def _end_loop(self) -> None:
        if self._state not in {"playing", "paused"} or self._end_requested:
            return
        if self._in_outro or self._engine.phase.value == "outro":
            self._in_outro = True
            self._refresh_controls()
            return
        self._engine.request_end_loop()
        self._end_requested = True
        self.end_button.setEnabled(False)
        self.end_button.setText("本轮后收尾")
        self.status_label.setText("将在当前段落结束后进入尾声")

    def _exit(self) -> None:
        if self._state not in {"playing", "paused"}:
            return
        self._engine.stop(immediate=True, emit=False)
        self._engine.cleanup_finished_stream()
        self._end_requested = False
        self._in_outro = False
        self._set_state("stopped")
        self.status_label.setText("已立即退出 · 输出流已关闭")

    def _on_phase_changed(self, phase: str) -> None:
        if phase == "outro":
            self._in_outro = True
            self._refresh_controls()

    def _on_engine_finished(self) -> None:
        if self._engine.active:
            # A queued "finished" from the previous take must not reset a new
            # take that has already started.
            return
        self._engine.cleanup_finished_stream()
        self.stage.set_playing(False)
        self._end_requested = False
        self._in_outro = False
        self._state = "stopped"
        self._refresh_controls()
        self.status_label.setText("播放结束 · 可以再来一次")
        self.state_changed.emit("stopped")
