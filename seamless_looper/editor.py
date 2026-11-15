"""The precision cutting desk: waveform, snapped cut points, and monitoring."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetrics,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .audio_io import (
    DecodedAudio,
    FrameGrid,
    PeakData,
    SavedTrack,
    format_sample_count,
    format_timestamp,
)
from .playback import PreviewPlayer
from .widgets import ElidedLabel, GlyphButton, Separator, label
from .workers import SaveWorker


# ---------------------------------------------------------------------------
# Waveform
# ---------------------------------------------------------------------------


class WaveformView(QWidget):
    point_selected = Signal(str)
    cut1_changed = Signal(int)
    cut2_changed = Signal(int)
    playhead_seek = Signal(int)
    drag_finished = Signal()

    HEADER_HEIGHT = 46
    FOOTER_HEIGHT = 28
    SIDE_MARGIN = 22

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._peaks: PeakData | None = None
        self._total = 0
        self._sample_rate = 44100
        self._cut1 = 0
        self._cut2 = 0
        self._playhead = 0
        self._selected = "playhead"
        self._drag: str | None = None
        self._hover: str | None = None
        self._bg_cache: QPixmap | None = None
        self._wave_cache: QPixmap | None = None
        self._section_font = theme.font(9.5, 600, spacing=1.4)
        self._pill_font = theme.font(9, 500, theme.NUMERIC)
        self._drag_font = theme.font(10, 600, theme.NUMERIC)
        self.setMinimumHeight(286)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip("点击定位播放头；拖动 A / B 切点。方向键可逐帧微调。")

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(980, 326)

    def set_data(
        self,
        peaks: PeakData,
        total_frames: int,
        sample_rate: int,
        cut1: int,
        cut2: int,
        playhead: int = 0,
    ) -> None:
        self._peaks = peaks
        self._total = max(0, int(total_frames))
        self._sample_rate = int(sample_rate)
        self._cut1 = int(cut1)
        self._cut2 = int(cut2)
        self._playhead = int(playhead)
        self._drag = None
        self._hover = None
        self._invalidate_caches()
        self.update()

    def set_points(self, *, cut1: int | None = None, cut2: int | None = None, playhead: int | None = None) -> None:
        if cut1 is not None:
            self._cut1 = int(cut1)
        if cut2 is not None:
            self._cut2 = int(cut2)
        if playhead is not None:
            self._playhead = int(playhead)
        self.update()

    def set_selected(self, key: str) -> None:
        self._selected = key
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(
            self.SIDE_MARGIN,
            self.HEADER_HEIGHT,
            max(1, self.width() - self.SIDE_MARGIN * 2),
            max(1, self.height() - self.HEADER_HEIGHT - self.FOOTER_HEIGHT),
        )

    def _x_for_sample(self, sample: int) -> float:
        plot = self._plot_rect()
        if self._total <= 0:
            return plot.left()
        ratio = min(1.0, max(0.0, sample / self._total))
        return plot.left() + ratio * plot.width()

    def _sample_for_x(self, x: float) -> int:
        plot = self._plot_rect()
        if plot.width() <= 0 or self._total <= 0:
            return 0
        ratio = min(1.0, max(0.0, (x - plot.left()) / plot.width()))
        return int(round(ratio * self._total))

    def _points(self) -> list[tuple[str, float]]:
        return [
            ("cut1", self._x_for_sample(self._cut1)),
            ("cut2", self._x_for_sample(self._cut2)),
            ("playhead", self._x_for_sample(self._playhead)),
        ]

    def _nearest_marker(self, x: float, threshold: float = 10.0) -> str | None:
        best: tuple[float, str] | None = None
        for key, marker_x in self._points():
            distance = abs(x - marker_x)
            if distance <= threshold and (best is None or distance < best[0]):
                best = (distance, key)
        return best[1] if best else None

    def _invalidate_caches(self) -> None:
        self._bg_cache = None
        self._wave_cache = None

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._invalidate_caches()
        super().resizeEvent(event)

    # -- painting -----------------------------------------------------------
    def _draw_panel(self, painter: QPainter) -> None:
        panel = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        background = QLinearGradient(0, 0, 0, self.height())
        background.setColorAt(0.0, QColor("#181C18"))
        background.setColorAt(1.0, QColor("#121512"))
        path = QPainterPath()
        path.addRoundedRect(panel, 18, 18)
        painter.fillPath(path, background)
        painter.setPen(QPen(QColor(theme.LINE_SOFT), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self._peaks is None or self._total <= 0:
            self._draw_panel(painter)
            painter.setPen(QColor(theme.MUTED_DARK))
            painter.setFont(theme.font(13))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "导入音频后，这里会出现波形")
            return

        if self._bg_cache is None or self._wave_cache is None:
            self._build_caches()
        if self._bg_cache is None or self._wave_cache is None:
            return

        target = QRectF(0, 0, self.width(), self.height())
        painter.drawPixmap(target, self._bg_cache, QRectF(self._bg_cache.rect()))

        plot = self._plot_rect()
        wave_top = plot.top() + 4
        wave_bottom = plot.bottom() - 2
        x1 = self._x_for_sample(self._cut1)
        x2 = self._x_for_sample(self._cut2)

        # Cheap per-frame section wash; the expensive waveform stays cached.
        painter.save()
        painter.setClipRect(QRectF(plot.left(), wave_top, plot.width(), wave_bottom - wave_top))
        for left, right, color in (
            (plot.left(), x1, QColor(231, 179, 106, 11)),
            (x1, x2, QColor(120, 198, 168, 13)),
            (x2, plot.right(), QColor(217, 140, 130, 8)),
        ):
            if right > left:
                painter.fillRect(QRectF(left, wave_top, right - left, wave_bottom - wave_top), color)
        painter.restore()

        painter.drawPixmap(target, self._wave_cache, QRectF(self._wave_cache.rect()))
        self._draw_section_labels(painter, plot, x1, x2)
        self._draw_markers(painter, plot, x1, x2)
        if self._drag in {"cut1", "cut2", "playhead"}:
            self._draw_drag_pill(painter, plot)

    def _build_caches(self) -> None:
        """Render the static waveform once per size/load, not per frame.

        The previous implementation drew thousands of vertical QPainter lines
        on every playhead tick.  That competed with the audio producer for the
        GIL and produced the stutter the user heard.  Two cached pixmaps (one
        for background/ticks, one transparent for the waveform) reduce a
        playback frame to two blits plus a handful of marker lines.
        """
        width = max(1, self.width())
        height = max(1, self.height())
        ratio = max(1.0, float(self.devicePixelRatioF()))
        device_w = max(1, int(round(width * ratio)))
        device_h = max(1, int(round(height * ratio)))

        bg = QPixmap(device_w, device_h)
        bg.fill(Qt.GlobalColor.transparent)
        p = QPainter(bg)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(ratio, ratio)
        self._draw_panel(p)
        plot = self._plot_rect()
        self._draw_grid(p, plot)
        self._draw_footer(p, plot)
        p.end()

        wave = QPixmap(device_w, device_h)
        wave.fill(Qt.GlobalColor.transparent)
        p = QPainter(wave)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(ratio, ratio)
        self._paint_waveform(p, plot)
        p.end()

        self._bg_cache = bg
        self._wave_cache = wave

    def _tick_seconds(self) -> int:
        duration = self._total / max(1, self._sample_rate)
        if duration <= 30:
            return 5
        if duration <= 90:
            return 10
        if duration <= 240:
            return 30
        return 60

    def _draw_grid(self, painter: QPainter, plot: QRectF) -> None:
        painter.save()
        painter.setClipRect(plot)
        interval = int(self._tick_seconds() * self._sample_rate)
        tick = 0
        while tick <= self._total:
            x = self._x_for_sample(tick)
            painter.setPen(QPen(QColor(255, 255, 255, 12), 1))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            tick += interval
        painter.restore()

    def _draw_section_labels(self, painter: QPainter, plot: QRectF, x1: float, x2: float) -> None:
        sections = [
            (plot.left(), x1, "A  ·  引子", QColor(theme.ACCENT)),
            (x1, x2, "B  ·  循环段", QColor(theme.MINT)),
            (x2, plot.right(), "C  ·  尾声", QColor(theme.ROSE)),
        ]
        painter.setFont(self._section_font)
        for left, right, text, color in sections:
            width = right - left
            if width < 58:
                continue
            color.setAlpha(185)
            painter.setPen(color)
            metrics = QFontMetrics(painter.font())
            display = text
            if width < metrics.horizontalAdvance(text) + 14:
                display = text.split("·")[0].strip()
            painter.drawText(
                QRectF(left, 8, width, 20),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                display,
            )

        # Loop bracket.
        if x2 - x1 > 40:
            pen = QPen(QColor(120, 198, 168, 100), 1.0)
            painter.setPen(pen)
            y = 32
            painter.drawLine(QPointF(x1 + 2, y), QPointF(x2 - 2, y))
            painter.drawLine(QPointF(x1 + 2, y - 4), QPointF(x1 + 2, y + 2))
            painter.drawLine(QPointF(x2 - 2, y - 4), QPointF(x2 - 2, y + 2))
            loop_frames = self._cut2 - self._cut1
            text = format_timestamp(loop_frames, self._sample_rate)
            paint_font = self._pill_font
            painter.setFont(paint_font)
            metrics = QFontMetrics(paint_font)
            if x2 - x1 > metrics.horizontalAdvance(text) + 36:
                pill = QRectF((x1 + x2) / 2 - (metrics.horizontalAdvance(text) + 18) / 2, 22, metrics.horizontalAdvance(text) + 18, 18)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#1A211C"))
                painter.drawRoundedRect(pill, 8, 8)
                painter.setPen(QColor(theme.MINT))
                painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    def _wave_gain(self) -> float:
        if self._peaks is None:
            return 1.0
        maximum = float(
            max(
                np.max(np.abs(self._peaks.left_min)),
                np.max(np.abs(self._peaks.left_max)),
                np.max(np.abs(self._peaks.right_min)),
                np.max(np.abs(self._peaks.right_max)),
            )
        )
        if maximum <= 0.02:
            return 1.0
        return min(3.0, 0.92 / maximum)

    def _paint_waveform(self, painter: QPainter, plot: QRectF) -> None:
        """Paint one asymmetric waveform: L on top, R below the centre.

        Rendering this into the cache rather than per-frame is the crucial
        performance fix.  A 3-minute track still uses one path per channel,
        with one point per horizontal pixel.
        """
        peaks = self._peaks
        if peaks is None:
            return
        wave_top = plot.top() + 8
        wave_bottom = plot.bottom() - 2
        mid = (wave_top + wave_bottom) / 2
        amplitude = (wave_bottom - wave_top) / 2 - 2
        gain = self._wave_gain()
        buckets = max(1, peaks.buckets)
        left_edge = max(1.0, plot.left())
        right_edge = max(left_edge + 1, plot.right())

        left_peak = np.maximum(np.abs(peaks.left_min), np.abs(peaks.left_max))
        if peaks.channels >= 2:
            right_peak = np.maximum(np.abs(peaks.right_min), np.abs(peaks.right_max))
        else:
            right_peak = left_peak

        xs = np.arange(int(round(left_edge)), int(round(right_edge)) + 1)
        if len(xs) == 0:
            return
        bucket_idx = np.clip(
            ((xs - plot.left()) / plot.width() * buckets).astype(np.int32),
            0,
            buckets - 1,
        )
        top_values = np.clip(left_peak[bucket_idx] * gain, 0.0, 1.0)
        bottom_values = np.clip(right_peak[bucket_idx] * gain, 0.0, 1.0)

        top_path = QPainterPath(QPointF(float(xs[0]), mid))
        for x, value in zip(xs, top_values):
            top_path.lineTo(QPointF(float(x), float(mid - value * amplitude)))
        top_path.lineTo(QPointF(float(xs[-1]), mid))
        top_path.closeSubpath()

        bottom_path = QPainterPath(QPointF(float(xs[0]), mid))
        for x, value in zip(xs, bottom_values):
            bottom_path.lineTo(QPointF(float(x), float(mid + value * amplitude)))
        bottom_path.lineTo(QPointF(float(xs[-1]), mid))
        bottom_path.closeSubpath()

        painter.save()
        painter.setClipRect(QRectF(plot.left(), wave_top, plot.width(), wave_bottom - wave_top))

        painter.setPen(QPen(QColor(255, 255, 255, 24), 1))
        painter.drawLine(QPointF(plot.left(), mid), QPointF(plot.right(), mid))

        top_fill = QLinearGradient(0, wave_top, 0, mid)
        top_fill.setColorAt(0.0, QColor(245, 205, 148, 220))
        top_fill.setColorAt(1.0, QColor(231, 179, 106, 122))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(top_path, top_fill)

        bottom_fill = QLinearGradient(0, mid, 0, wave_bottom)
        bottom_fill.setColorAt(0.0, QColor(120, 198, 168, 120))
        bottom_fill.setColorAt(1.0, QColor(120, 198, 168, 212))
        painter.fillPath(bottom_path, bottom_fill)

        painter.setFont(theme.font(7.5, 600, spacing=1.4))
        painter.setPen(QColor(231, 179, 106, 120))
        painter.drawText(QRectF(2, wave_top + 2, 16, 12), Qt.AlignmentFlag.AlignLeft, "L")
        painter.setPen(QColor(120, 198, 168, 120))
        painter.drawText(QRectF(2, wave_bottom - 14, 16, 12), Qt.AlignmentFlag.AlignLeft, "R")
        painter.restore()

    def _draw_markers(self, painter: QPainter, plot: QRectF, x1: float, x2: float) -> None:
        painter.save()
        painter.setClipRect(QRectF(plot.left() - 6, 0, plot.width() + 12, self.height()))
        for key, x, color in (
            ("cut1", x1, QColor(theme.MINT)),
            ("cut2", x2, QColor(theme.ROSE)),
        ):
            selected = self._selected == key
            if selected:
                painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 42), 7))
                painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 230), 1.7))
            painter.drawLine(QPointF(x, plot.top() - 4), QPointF(x, plot.bottom()))
            triangle = QPolygonF(
                [
                    QPointF(x - 5, plot.top() - 12),
                    QPointF(x + 5, plot.top() - 12),
                    QPointF(x, plot.top() - 4),
                ]
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(triangle)

        playhead_color = QColor(theme.TEXT)
        x = self._x_for_sample(self._playhead)
        if self._selected == "playhead":
            painter.setPen(QPen(QColor(255, 255, 255, 28), 6))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        pen = QPen(QColor(playhead_color.red(), playhead_color.green(), playhead_color.blue(), 215), 1.2)
        painter.setPen(pen)
        painter.drawLine(QPointF(x, plot.top() - 4), QPointF(x, plot.bottom()))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(playhead_color)
        painter.drawEllipse(QPointF(x, plot.top() - 8), 4.0, 4.0)
        painter.restore()

    def _draw_footer(self, painter: QPainter, plot: QRectF) -> None:
        interval = int(self._tick_seconds() * self._sample_rate)
        painter.setFont(theme.font(9, 500, theme.NUMERIC))
        painter.setPen(QColor(theme.MUTED_DARK))
        metrics = QFontMetrics(painter.font())
        tick = 0
        while tick <= self._total:
            x = self._x_for_sample(tick)
            text = format_timestamp(tick, self._sample_rate, millis=False)
            width = metrics.horizontalAdvance(text)
            left = max(plot.left(), min(plot.right() - width, x - width / 2))
            painter.drawText(QRectF(left, plot.bottom() + 7, width + 2, 14), Qt.AlignmentFlag.AlignLeft, text)
            tick += interval

    def _draw_drag_pill(self, painter: QPainter, plot: QRectF) -> None:
        if self._drag is None:
            return
        if self._drag == "cut1":
            sample, color = self._cut1, QColor(theme.MINT)
        elif self._drag == "cut2":
            sample, color = self._cut2, QColor(theme.ROSE)
        else:
            sample, color = self._playhead, QColor(theme.TEXT)
        x = self._x_for_sample(sample)
        text = format_timestamp(sample, self._sample_rate)
        font = self._drag_font
        painter.setFont(font)
        metrics = QFontMetrics(font)
        width = metrics.horizontalAdvance(text) + 18
        pill = QRectF(
            max(plot.left(), min(plot.right() - width, x - width / 2)),
            2,
            width,
            21,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(21, 25, 22, 238))
        painter.drawRoundedRect(pill, 7, 7)
        painter.setPen(color)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)

    # -- interaction --------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or self._peaks is None:
            return super().mousePressEvent(event)
        x = event.position().x()
        marker = self._nearest_marker(x, threshold=11)
        if marker is None:
            marker = "playhead"
            sample = self._sample_for_x(x)
            self._playhead = sample
            self.playhead_seek.emit(sample)
            self.point_selected.emit("playhead")
        else:
            self.point_selected.emit(marker)
            if marker == "playhead":
                sample = self._sample_for_x(x)
                self._playhead = sample
                self.playhead_seek.emit(sample)
        self._drag = marker
        self._selected = marker
        self.update()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._peaks is None:
            return
        x = event.position().x()
        if self._drag is None:
            hover = self._nearest_marker(x, threshold=10)
            if hover != self._hover:
                self._hover = hover
            self.setCursor(
                Qt.CursorShape.SizeHorCursor if hover else Qt.CursorShape.CrossCursor
            )
            return
        sample = self._sample_for_x(x)
        if self._drag == "cut1":
            sample = max(1, min(sample, self._cut2 - 1))
            self._cut1 = sample
            self.cut1_changed.emit(sample)
            self.point_selected.emit("cut1")
        elif self._drag == "cut2":
            sample = max(self._cut1 + 1, min(sample, self._total - 1))
            self._cut2 = sample
            self.cut2_changed.emit(sample)
            self.point_selected.emit("cut2")
        else:
            sample = max(0, min(sample, self._total))
            self._playhead = sample
            self.playhead_seek.emit(sample)
            self.point_selected.emit("playhead")
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None:
            self._drag = None
            self.drag_finished.emit()
            self.update()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        super().leaveEvent(event)


# ---------------------------------------------------------------------------
# Time point cards
# ---------------------------------------------------------------------------


class PointCard(QFrame):
    selected = Signal(str)
    align_requested = Signal(str)
    preview_requested = Signal(str)

    def __init__(
        self,
        key: str,
        title: str,
        accent: str,
        *,
        show_align: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.key = key
        self._accent = accent
        self.setObjectName("PointCard")
        self.setProperty("active", "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(112)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 11, 13, 10)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(7)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {accent}; font-size: 8px;")
        top.addWidget(dot)
        name = QLabel(title)
        name.setFont(theme.font(10.5, 600, spacing=1.0))
        name.setStyleSheet(f"color: {theme.MUTED};")
        top.addWidget(name)
        top.addStretch(1)
        self.align_button = None
        self.preview_button = None
        if show_align:
            self.preview_button = GlyphButton("play")
            self.preview_button.setProperty("role", "icon")
            self.preview_button.setFixedSize(24, 24)
            self.preview_button.set_glyph_color(theme.ACCENT)
            self.preview_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.preview_button.setToolTip("从该切点开始试听")
            self.preview_button.clicked.connect(lambda: self.preview_requested.emit(self.key))
            top.addWidget(self.preview_button)
            self.align_button = QPushButton("对齐播放头")
            self.align_button.setProperty("role", "icon")
            self.align_button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.align_button.setStyleSheet("font-size: 10px; color: #889188; padding: 2px 5px;")
            self.align_button.setToolTip("将本切点吸附到播放头最近的帧边界")
            self.align_button.clicked.connect(lambda: self.align_requested.emit(self.key))
            top.addWidget(self.align_button)
        layout.addLayout(top)

        self.time_label = QLabel("00:00.000")
        self.time_label.setFont(theme.numeric(25, 500, spacing=0.3))
        self.time_label.setStyleSheet(f"color: {theme.TEXT};")
        layout.addWidget(self.time_label)

        self.meta_label = QLabel("样本 0")
        self.meta_label.setFont(theme.font(9.5, 400, theme.MONO))
        self.meta_label.setStyleSheet(f"color: {theme.MUTED_DARK};")
        layout.addWidget(self.meta_label)

    def set_value(self, samples: int, sample_rate: int, suffix: str = "") -> None:
        self.time_label.setText(format_timestamp(samples, sample_rate))
        text = f"样本 {format_sample_count(samples)}"
        if suffix:
            text += f"  ·  {suffix}"
        self.meta_label.setText(text)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        theme.repolish(self)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.key)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.preview_button is not None:
            self.preview_requested.emit(self.key)
        super().mouseDoubleClickEvent(event)


# ---------------------------------------------------------------------------
# Save dialog
# ---------------------------------------------------------------------------


class SaveTrackDialog(QDialog):
    def __init__(self, decoded: DecodedAudio, cut1: int, cut2: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.info = decoded.info
        self.cut1 = cut1
        self.cut2 = cut2
        self.setWindowTitle("保存到曲库")
        self.setModal(True)
        self.setMinimumWidth(530)
        self.setStyleSheet(f"QDialog {{ background: {theme.BG}; }}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(14)

        eyebrow = label("Save to library", object_name="Eyebrow")
        layout.addWidget(eyebrow)
        layout.addWidget(label("保存这段循环", font=theme.serif(28, 600)))
        lead = label(
            "剪辑后的音频将保存至曲库；原始文件不会被修改。",
            object_name="PageLead",
            wrap=True,
        )
        layout.addWidget(lead)

        form = QVBoxLayout()
        form.setSpacing(6)
        form.addWidget(label("曲目标题", object_name="Tiny"))
        self.title_edit = QLineEdit(self.info.title)
        self.title_edit.setMaxLength(120)
        form.addWidget(self.title_edit)
        form.addSpacing(4)
        form.addWidget(label("艺术家", object_name="Tiny"))
        self.artist_edit = QLineEdit(self.info.artist)
        self.artist_edit.setMaxLength(120)
        form.addWidget(self.artist_edit)
        layout.addLayout(form)

        summary = QFrame()
        summary.setObjectName("Card")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(16, 13, 16, 13)
        summary_layout.setSpacing(8)
        for name, start, end in (
            ("A · 引子", 0, cut1),
            ("B · 循环段", cut1, cut2),
            ("C · 尾声", cut2, self.info.frames),
        ):
            row = QHBoxLayout()
            row.addWidget(label(name, object_name="Muted"))
            row.addStretch(1)
            value = label(
                f"{format_timestamp(end - start, self.info.sample_rate)}  ·  "
                f"{format_sample_count(end - start)} 样本",
                font=theme.font(11, 500, theme.MONO),
            )
            value.setStyleSheet(f"color: {theme.TEXT_DIM};")
            row.addWidget(value)
            summary_layout.addLayout(row)
        summary_layout.addWidget(Separator())
        note = label(
            f"保存格式：{self.info.output.container} · {self.info.output.bit_depth}-bit · "
            f"{self.info.sample_rate / 1000:g} kHz · {self.info.channels} 声道",
            object_name="Tiny",
            wrap=True,
        )
        summary_layout.addWidget(note)
        layout.addWidget(summary)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setProperty("role", "ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存到曲库")
        save.setProperty("role", "primary")
        save.clicked.connect(self._accept)
        actions.addWidget(cancel)
        actions.addWidget(save)
        layout.addLayout(actions)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _accept(self) -> None:
        if not self.title_edit.text().strip():
            self.title_edit.setFocus()
            return
        self.accept()

    def values(self) -> tuple[str, str]:
        return self.title_edit.text().strip(), self.artist_edit.text().strip() or "未知艺术家"


# ---------------------------------------------------------------------------
# Editor page
# ---------------------------------------------------------------------------


class EditorPage(QWidget):
    back_requested = Signal()
    track_saved = Signal(object)
    notice = Signal(str, str)
    busy_started = Signal(str, str)
    busy_progress = Signal(int, str)
    busy_finished = Signal()

    def __init__(self, library_dir: str | Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.library_dir = Path(library_dir)
        self._decoded: DecodedAudio | None = None
        self._grid: FrameGrid | None = None
        self._cut1 = 0
        self._cut2 = 0
        self._playhead = 0
        self._selected = "cut1"
        self._preview = PreviewPlayer(self)
        self._preview.error_occurred.connect(lambda message: self.notice.emit(message, "error"))
        self._save_worker: SaveWorker | None = None
        self._preview_ok = False
        self._last_card_update = 0.0
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._install_shortcuts()

    # -- construction -------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 20)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(13)
        self.back_button = GlyphButton("back")
        self.back_button.setProperty("role", "ghost")
        self.back_button.setFixedSize(38, 38)
        self.back_button.set_glyph_color(theme.MUTED)
        self.back_button.setToolTip("返回曲库")
        self.back_button.clicked.connect(self.back_requested.emit)
        header.addWidget(self.back_button)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title_box.addWidget(label("Cutting desk", object_name="Eyebrow"))
        self.title_label = ElidedLabel("等待导入")
        self.title_label.setFont(theme.serif(27, 600))
        self.title_label.setStyleSheet(f"color: {theme.TEXT};")
        title_box.addWidget(self.title_label)
        header.addLayout(title_box, 1)

        self.source_label = label("", object_name="Chip")
        header.addWidget(self.source_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.save_button = QPushButton("保存到曲库")
        self.save_button.setProperty("role", "primary")
        self.save_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_button.clicked.connect(self._save)
        self.save_button.setEnabled(False)
        header.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        self.waveform = WaveformView()
        self.waveform.point_selected.connect(self._select_point)
        self.waveform.cut1_changed.connect(self._wave_cut1)
        self.waveform.cut2_changed.connect(self._wave_cut2)
        self.waveform.playhead_seek.connect(self._seek_playhead)
        layout.addWidget(self.waveform, 1)

        cards = QHBoxLayout()
        cards.setSpacing(11)
        self.card_playhead = PointCard("playhead", "播放头", theme.TEXT)
        self.card_cut1 = PointCard("cut1", "切点 A · 循环开始", theme.MINT, show_align=True)
        self.card_cut2 = PointCard("cut2", "切点 B · 循环结束", theme.ROSE, show_align=True)
        for card in (self.card_playhead, self.card_cut1, self.card_cut2):
            card.selected.connect(self._select_point)
            card.align_requested.connect(self._align_cut)
            card.preview_requested.connect(self._preview_from_cut)
            cards.addWidget(card)
        layout.addLayout(cards)

        control = QFrame()
        control.setObjectName("ControlBar")
        control_layout = QHBoxLayout(control)
        control_layout.setContentsMargins(12, 8, 15, 8)
        control_layout.setSpacing(10)

        self.play_button = GlyphButton("play")
        self.play_button.setProperty("role", "transport")
        self.play_button.setToolTip("试听 / 暂停（空格）")
        self.play_button.clicked.connect(self._toggle_preview)
        control_layout.addWidget(self.play_button)
        monitor_box = QVBoxLayout()
        monitor_box.setSpacing(0)
        monitor_box.addWidget(label("试听", font=theme.font(11, 600)))
        self.monitor_label = label("空格播放 / 暂停", object_name="Tiny")
        monitor_box.addWidget(self.monitor_label)
        control_layout.addLayout(monitor_box)

        control_layout.addWidget(self._vline())

        control_layout.addWidget(label("微调", object_name="Tiny"))
        self.nudge_left = QPushButton("←  一帧")
        self.nudge_left.setProperty("role", "secondary")
        self.nudge_left.setToolTip("将选中点向左移动一个编码帧（←）")
        self.nudge_left.clicked.connect(lambda: self._nudge(-1))
        self.nudge_right = QPushButton("一帧  →")
        self.nudge_right.setProperty("role", "secondary")
        self.nudge_right.setToolTip("将选中点向右移动一个编码帧（→）")
        self.nudge_right.clicked.connect(lambda: self._nudge(1))
        control_layout.addWidget(self.nudge_left)
        control_layout.addWidget(self.nudge_right)

        self.selection_label = label("已选：切点 A", object_name="Muted", font=theme.font(11))
        control_layout.addWidget(self.selection_label)
        control_layout.addStretch(1)

        self.sync_button = QPushButton("对齐播放头")
        self.sync_button.setProperty("role", "secondary")
        self.sync_button.setToolTip("把选中切点吸附到播放头最近的帧边界")
        self.sync_button.clicked.connect(self._sync_selected)
        control_layout.addWidget(self.sync_button)

        control_layout.addWidget(self._vline())
        control_layout.addWidget(label("监听音量", object_name="Tiny"))
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(104)
        self.volume_slider.valueChanged.connect(self._volume_changed)
        control_layout.addWidget(self.volume_slider)
        layout.addWidget(control)

        footer = QHBoxLayout()
        footer.setContentsMargins(2, 0, 2, 0)
        self.grid_label = label("", object_name="Tiny")
        footer.addWidget(self.grid_label)
        footer.addStretch(1)
        hint = label(
            "SPACE 试听   ·   切点卡片 ▶ 从此处试听   ·   ←/→ 微调   ·   Ctrl+S 保存",
            object_name="Tiny",
        )
        footer.addWidget(hint)
        layout.addLayout(footer)

        self._select_point("cut1")

    def _vline(self) -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 24)
        line.setStyleSheet(f"background: {theme.LINE};")
        return line

    def _install_shortcuts(self) -> None:
        self._space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._space.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._space.activated.connect(self._toggle_preview)
        self._left = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self._left.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._left.activated.connect(lambda: self._nudge(-1))
        self._right = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self._right.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._right.activated.connect(lambda: self._nudge(1))
        self._save_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self._save_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._save_shortcut.activated.connect(self._save)

    # -- lifecycle ----------------------------------------------------------
    @property
    def has_audio(self) -> bool:
        return self._decoded is not None

    def set_audio(self, decoded: DecodedAudio) -> None:
        self._stop_preview()
        if not self._timer.isActive():
            self._timer.start()
        self._decoded = decoded
        info = decoded.info
        self._grid = info.frame_grid
        total = info.frames
        self._cut1 = self._grid.snap(total / 3.0, low=0, high=total)
        self._cut2 = self._grid.snap(total * 2.0 / 3.0, low=self._cut1, high=total)
        if self._cut1 <= 0:
            self._cut1 = self._grid.minimum_cut
        if self._cut2 >= total:
            self._cut2 = self._grid.maximum_cut
        if not (0 < self._cut1 < self._cut2 < total):
            midpoint = total // 2
            self._cut1, self._cut2 = max(1, midpoint // 2), min(total - 1, midpoint + midpoint // 2)
        self._playhead = 0
        self._selected = "cut1"

        self.title_label.setText(info.title)
        self.source_label.setText(info.summary())
        self.grid_label.setText(
            f"切点吸附：{info.frame_label}  ·  左闭右开 [A, B)  ·  "
            f"{info.output.container} {info.output.bit_depth}-bit 保存"
        )
        self.waveform.set_data(decoded.peaks, total, info.sample_rate, self._cut1, self._cut2, 0)
        self.waveform.set_selected("cut1")
        self._update_cards()
        self._select_point("cut1")
        self.save_button.setEnabled(True)
        self._preview_ok = False
        self._load_preview()

    def activate(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        if self._decoded is not None and not self._preview.available:
            self._load_preview()
        self.waveform.setFocus(Qt.FocusReason.OtherFocusReason)

    def deactivate(self) -> None:
        self._stop_preview()
        self._preview.unload()
        self._preview_ok = False
        self._timer.stop()

    def shutdown(self) -> None:
        self._stop_preview()
        self._preview.unload()
        if self._save_worker is not None and self._save_worker.isRunning():
            # Never let Qt destroy a live QThread.  A long 24-bit export can
            # legitimately take a little while on a busy machine.
            self._save_worker.wait(60_000)

    def _load_preview(self) -> None:
        if self._decoded is None:
            return
        try:
            self._preview.load(self._decoded.pcm, self._decoded.info.sample_rate)
            self._preview_ok = True
            self.play_button.setEnabled(True)
            self.monitor_label.setText("空格播放 / 暂停")
        except Exception as exc:
            self._preview_ok = False
            self.play_button.setEnabled(False)
            self.monitor_label.setText("试听设备不可用")
            self.notice.emit(str(exc), "error")

    # -- view state ---------------------------------------------------------
    def _update_cards(self) -> None:
        if self._decoded is None:
            return
        sr = self._decoded.info.sample_rate
        self.card_playhead.set_value(self._playhead, sr)
        suffix = self._decoded.info.frame_label
        self.card_cut1.set_value(self._cut1, sr, suffix)
        self.card_cut2.set_value(self._cut2, sr, suffix)

    def _select_point(self, key: str) -> None:
        self._selected = key
        self.waveform.set_selected(key)
        self.card_playhead.set_active(key == "playhead")
        self.card_cut1.set_active(key == "cut1")
        self.card_cut2.set_active(key == "cut2")
        names = {"playhead": "播放头", "cut1": "切点 A", "cut2": "切点 B"}
        self.selection_label.setText(f"已选：{names.get(key, '切点 A')}")
        is_cut = key in {"cut1", "cut2"}
        self.sync_button.setEnabled(is_cut)
        self.sync_button.setText(f"将{names.get(key, '切点')}对齐播放头" if is_cut else "对齐播放头")

    def _set_preview_button(self, playing: bool) -> None:
        self.play_button.set_glyph("pause" if playing else "play")
        self.play_button.setProperty("playing", "true" if playing else "false")
        self.play_button.set_glyph_color("#21180D" if playing else theme.TEXT)
        theme.repolish(self.play_button)
        self.monitor_label.setText("正在试听" if playing else "空格播放 / 暂停")

    def _tick(self) -> None:
        if not self._preview.available:
            return
        if self._preview.playing:
            position = self._preview.position
            if position != self._playhead:
                self._playhead = position
                self.waveform.set_points(playhead=position)
                now = time.monotonic()
                if now - self._last_card_update >= 0.1:
                    self.card_playhead.set_value(position, self._decoded.info.sample_rate)
                    self._last_card_update = now
        if self._preview.consume_ended():
            self._playhead = self._preview.position
            self.waveform.set_points(playhead=self._playhead)
            self.card_playhead.set_value(self._playhead, self._decoded.info.sample_rate)
            self._last_card_update = time.monotonic()
            self._set_preview_button(False)

    # -- commands -----------------------------------------------------------
    def _toggle_preview(self) -> None:
        if self._decoded is None:
            return
        if not self._preview_ok:
            self._load_preview()
            if not self._preview_ok:
                return
        if self._preview.paused and self._preview.position >= self._decoded.info.frames:
            self._preview.seek(0)
            self._playhead = 0
            self.waveform.set_points(playhead=0)
            self.card_playhead.set_value(0, self._decoded.info.sample_rate)
        playing = self._preview.toggle()
        self._set_preview_button(playing)

    def _preview_from_cut(self, key: str) -> None:
        """Move the playhead to a cut and start monitoring from there."""
        if self._decoded is None:
            return
        if key == "cut1":
            sample = self._cut1
        elif key == "cut2":
            sample = self._cut2
        else:
            sample = self._playhead
        self._select_point(key)
        self._seek_playhead(sample)
        if not self._preview_ok:
            self._load_preview()
            if not self._preview_ok:
                return
        self._preview.seek(sample)
        self._preview.play()
        self._last_card_update = 0.0
        self._set_preview_button(True)
        self.waveform.setFocus(Qt.FocusReason.OtherFocusReason)

    def _stop_preview(self) -> None:
        if self._preview.available:
            self._preview.pause()
        self._set_preview_button(False)

    def _seek_playhead(self, sample: int) -> None:
        if self._decoded is None:
            return
        sample = max(0, min(int(sample), self._decoded.info.frames))
        self._playhead = sample
        self.waveform.set_points(playhead=sample)
        self.card_playhead.set_value(sample, self._decoded.info.sample_rate)
        if self._preview.available:
            self._preview.seek(sample)

    def _wave_cut1(self, sample: int) -> None:
        if self._decoded is None or self._grid is None:
            return
        value = self._grid.snap_cut1(sample, self._cut2)
        if value <= 0:
            value = self._grid.minimum_cut
        self._cut1 = value
        self.waveform.set_points(cut1=value)
        self._update_cards()
        self._select_point("cut1")

    def _wave_cut2(self, sample: int) -> None:
        if self._decoded is None or self._grid is None:
            return
        value = self._grid.snap_cut2(sample, self._cut1)
        if value >= self._decoded.info.frames:
            value = self._grid.maximum_cut
        self._cut2 = value
        self.waveform.set_points(cut2=value)
        self._update_cards()
        self._select_point("cut2")

    def _nudge(self, direction: int) -> None:
        if self._decoded is None or self._grid is None:
            return
        total = self._decoded.info.frames
        if self._selected == "cut1":
            value = self._grid.nudge(self._cut1, direction, low=0, high=self._cut2)
            self._cut1 = value
            self._update_cards()
            self.waveform.set_points(cut1=value)
        elif self._selected == "cut2":
            value = self._grid.nudge(self._cut2, direction, low=self._cut1, high=total)
            self._cut2 = value
            self._update_cards()
            self.waveform.set_points(cut2=value)
        else:
            step = max(1, self._grid.step)
            value = max(0, min(total - 1, self._playhead + direction * step))
            self._seek_playhead(value)
        self.waveform.update()

    def _sync_selected(self) -> None:
        self._align_cut(self._selected)

    def _align_cut(self, key: str) -> None:
        if self._decoded is None or self._grid is None or key == "playhead":
            return
        total = self._decoded.info.frames
        if key == "cut1":
            value = self._grid.snap_cut1(self._playhead, self._cut2)
            self._cut1 = value
            self.waveform.set_points(cut1=value)
            name = "切点 A"
        else:
            value = self._grid.snap_cut2(self._playhead, self._cut1)
            self._cut2 = value
            self.waveform.set_points(cut2=value)
            name = "切点 B"
        self._update_cards()
        self._select_point(key)
        self.notice.emit(
            f"{name} 已吸附到 {format_timestamp(value, self._decoded.info.sample_rate)}",
            "success",
        )

    def _volume_changed(self, value: int) -> None:
        volume = value / 100.0
        self._preview.set_volume(volume)

    # -- save ---------------------------------------------------------------
    def _save(self) -> None:
        if self._decoded is None or self._grid is None:
            return
        if self._save_worker is not None and self._save_worker.isRunning():
            return
        info = self._decoded.info
        if not (0 < self._cut1 < self._cut2 < info.frames):
            self.notice.emit("切点顺序无效，请重新调整。", "error")
            return
        self._stop_preview()
        dialog = SaveTrackDialog(self._decoded, self._cut1, self._cut2, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        title, artist = dialog.values()
        self.save_button.setEnabled(False)
        self.busy_started.emit("正在保存无损片段", "准备切片")
        self._save_worker = SaveWorker(
            self._decoded,
            self._cut1,
            self._cut2,
            library_dir=self.library_dir,
            title=title,
            artist=artist,
            album=info.album,
            parent=self,
        )
        worker = self._save_worker
        worker.progress.connect(self._save_progress)
        worker.completed.connect(self._save_completed)
        worker.failed.connect(self._save_failed)
        worker.finished.connect(self._save_finished)
        worker.start()

    def _save_progress(self, value: int, message: str) -> None:
        self.busy_progress.emit(value, message)

    def _save_completed(self, saved: SavedTrack) -> None:
        self.busy_finished.emit()
        self.save_button.setEnabled(True)
        self.track_saved.emit(saved)

    def _save_failed(self, message: str) -> None:
        self.busy_finished.emit()
        self.save_button.setEnabled(True)
        self.notice.emit(message or "保存失败。", "error")

    def _save_finished(self) -> None:
        worker = self._save_worker
        self._save_worker = None
        if worker is not None:
            worker.deleteLater()
