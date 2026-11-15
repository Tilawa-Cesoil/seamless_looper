"""Small, reusable pieces of the Loop Studio visual language."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from . import theme


# ---------------------------------------------------------------------------
# Application icon
# ---------------------------------------------------------------------------

_APP_ICON: QIcon | None = None


def _app_icon_candidates() -> list[Path]:
    """Locations to try for the shared app.ico asset.

    Works from source, a PyInstaller onedir/onefile bundle, and a portable
    folder where app.ico sits next to the executable.
    """
    bases: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(Path(meipass))
    if getattr(sys, "frozen", False):
        bases.append(Path(sys.executable).resolve().parent)
    package_root = Path(__file__).resolve().parent.parent
    bases.extend(
        [
            package_root,
            Path(__file__).resolve().parent,
            Path.cwd(),
        ]
    )

    candidates: list[Path] = []
    seen: set[Path] = set()
    for base in bases:
        candidate = (base / "app.ico").resolve()
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


def app_icon() -> QIcon:
    """Return the one shared QIcon loaded from app.ico."""
    global _APP_ICON
    if _APP_ICON is not None:
        return _APP_ICON
    for path in _app_icon_candidates():
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                _APP_ICON = icon
                return icon
    _APP_ICON = QIcon()
    return _APP_ICON


# ---------------------------------------------------------------------------
# Painting helpers
# ---------------------------------------------------------------------------


def rounded_path(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def accent_color_for(seed: str) -> QColor:
    if not seed:
        return QColor(theme.ACCENT)
    value = sum((index + 1) * ord(char) for index, char in enumerate(seed))
    hue = (value * 37) % 360
    return QColor.fromHsv(hue, 105, 210)


def make_cover_pixmap(
    cover_path: str | Path | None,
    size: int,
    radius: int,
    seed: str,
    glyph: str = "∞",
) -> QPixmap:
    """Crop a cover, or paint a deterministic, track-specific fallback."""
    scale = 2
    target = max(16, int(size))
    pixmap = QPixmap(target * scale, target * scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    rect = QRectF(0, 0, target * scale, target * scale)
    path = rounded_path(rect, radius * scale)
    painter.setClipPath(path)

    source = QPixmap(str(cover_path)) if cover_path else QPixmap()
    if not source.isNull():
        scaled = source.scaled(
            QSize(target * scale, target * scale),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (scaled.width() - target * scale) // 2
        y = (scaled.height() - target * scale) // 2
        painter.drawPixmap(0, 0, scaled, x, y, target * scale, target * scale)
        # A subtle vignette keeps light album art from fighting the UI.
        shade = QLinearGradient(0, 0, 0, target * scale)
        shade.setColorAt(0.0, QColor(0, 0, 0, 0))
        shade.setColorAt(1.0, QColor(0, 0, 0, 60))
        painter.fillRect(rect, shade)
    else:
        base = accent_color_for(seed)
        top = QColor(base)
        top.setHsv(top.hue(), max(35, top.saturation() - 25), max(35, top.value() - 95))
        bottom = QColor(base)
        bottom.setHsv((bottom.hue() + 26) % 360, bottom.saturation() - 15, max(22, bottom.value() - 145))
        gradient = QLinearGradient(0, 0, target * scale, target * scale)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(1.0, bottom)
        painter.fillRect(rect, gradient)

        # Faint orbit lines.
        orbit_pen = QPen(QColor(255, 255, 255, 20), 1.2 * scale)
        painter.setPen(orbit_pen)
        for fraction in (0.62, 0.86):
            diameter = target * scale * fraction
            painter.drawEllipse(
                QRectF(
                    (target * scale - diameter) / 2,
                    (target * scale - diameter) / 2,
                    diameter,
                    diameter,
                )
            )
        painter.setPen(QColor(255, 255, 255, 205))
        font = QFont(theme.SERIF)
        font.setPixelSize(int(target * scale * 0.38))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, glyph)
        shimmer = QRadialGradient(rect.center(), target * scale * 0.55)
        shimmer.setColorAt(0.0, QColor(255, 255, 255, 16))
        shimmer.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillRect(rect, shimmer)

    painter.setClipping(False)
    painter.setPen(QPen(QColor(255, 255, 255, 28), 1.0 * scale))
    painter.drawPath(path)
    painter.end()
    pixmap.setDevicePixelRatio(scale)
    return pixmap


# ---------------------------------------------------------------------------
# Brand / cover
# ---------------------------------------------------------------------------


class LogoMark(QWidget):
    """The sidebar brand mark, rendered from the shared app.ico."""

    def __init__(self, size: int = 38, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self._icon = app_icon()
        self._pixmap: QPixmap | None = None
        self.setFixedSize(size, size)

    def _refresh_pixmap(self) -> None:
        ratio = max(1.0, float(self.devicePixelRatioF()))
        self._pixmap = self._icon.pixmap(QSize(self._size, self._size), ratio)
        if not self._pixmap.isNull():
            self._pixmap.setDevicePixelRatio(ratio)

    def paintEvent(self, event) -> None:  # noqa: N802
        if self._pixmap is None:
            self._refresh_pixmap()
        if self._pixmap is None or self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(0, 0, self._pixmap)


class CoverArt(QWidget):
    def __init__(
        self,
        size: int = 72,
        radius: int = 14,
        *,
        seed: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._size = size
        self._radius = radius
        self._seed = seed
        self._pixmap = make_cover_pixmap(None, size, radius, seed)
        self.setFixedSize(size, size)

    def set_cover(self, path: str | Path | None, seed: str | None = None) -> None:
        if seed is not None:
            self._seed = seed
        self._pixmap = make_cover_pixmap(path, self._size, self._radius, self._seed)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(0, 0, self._pixmap)


class AlbumStage(QWidget):
    """Cover art with slowly turning constellation rings while playing."""

    def __init__(self, diameter: int = 406, parent: QWidget | None = None):
        super().__init__(parent)
        self._diameter = diameter
        self._cover_size = int(diameter * 0.76)
        self._cover = make_cover_pixmap(None, self._cover_size, 26, "")
        self._playing = False
        self._angle = 28.0
        self._timer = QTimer(self)
        self._timer.setInterval(32)
        self._timer.timeout.connect(self._tick)
        self.setFixedSize(diameter, diameter)

    def set_cover(self, path: str | Path | None, seed: str) -> None:
        self._cover = make_cover_pixmap(path, self._cover_size, 26, seed)
        self.update()

    def set_playing(self, playing: bool) -> None:
        self._playing = playing
        if playing and not self._timer.isActive():
            self._timer.start()
        elif not playing:
            self._timer.stop()
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 0.55) % 360
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        center = QPointF(self.width() / 2, self.height() / 2)

        # Soft halo.
        halo = QRadialGradient(center, self.width() * 0.49)
        halo.setColorAt(0.0, QColor(231, 179, 106, 22))
        halo.setColorAt(0.62, QColor(231, 179, 106, 8))
        halo.setColorAt(1.0, QColor(231, 179, 106, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, self.width() * 0.49, self.width() * 0.49)

        # Orbit rings.
        for index, fraction in enumerate((0.88, 0.955, 1.0)):
            radius = self.width() * 0.5 * fraction
            pen = QPen(QColor(255, 255, 255, 12 + index * 5), 1.0)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, radius, radius)

        if self._playing:
            arc_rect = QRectF(
                center.x() - self.width() * 0.477,
                center.y() - self.width() * 0.477,
                self.width() * 0.954,
                self.width() * 0.954,
            )
            pen = QPen(QColor(theme.ACCENT), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(arc_rect, int(self._angle * 16), int(50 * 16))
            pen.setColor(QColor(theme.MINT))
            pen.setWidthF(1.4)
            painter.setPen(pen)
            painter.drawArc(arc_rect, int((self._angle + 155) * 16), int(22 * 16))

        # Cover shadow and artwork.
        cover_rect = QRectF(
            center.x() - self._cover_size / 2,
            center.y() - self._cover_size / 2,
            self._cover_size,
            self._cover_size,
        )
        for offset, alpha in ((18, 24), (10, 28), (4, 32)):
            shadow_rect = cover_rect.translated(0, offset)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(shadow_rect, 27, 27)
        painter.drawPixmap(int(cover_rect.x()), int(cover_rect.y()), self._cover)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
        painter.drawRoundedRect(cover_rect, 26, 26)

        # Tiny status dot.
        dot_center = QPointF(
            cover_rect.right() - 16,
            cover_rect.top() + 16,
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT if self._playing else theme.MUTED_DARK))
        painter.drawEllipse(dot_center, 5, 5)
        if self._playing:
            painter.setBrush(QColor(231, 179, 106, 55))
            painter.drawEllipse(dot_center, 10, 10)


# ---------------------------------------------------------------------------
# Typography helpers
# ---------------------------------------------------------------------------


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None, *, mode=Qt.TextElideMode.ElideRight):
        super().__init__(text, parent)
        self._full_text = text
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    def setText(self, text: str) -> None:  # noqa: N802
        self._full_text = text
        self.setToolTip(text)
        self._refresh()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        metrics = QFontMetrics(self.font())
        available = max(0, self.width() - 2)
        QLabel.setText(self, metrics.elidedText(self._full_text, self._mode, available))


def label(text: str, *, object_name: str | None = None, font=None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if object_name:
        widget.setObjectName(object_name)
    if font:
        widget.setFont(font)
    widget.setWordWrap(wrap)
    return widget


class Separator(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Hairline")
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


# ---------------------------------------------------------------------------
# Buttons
# ---------------------------------------------------------------------------


class GlyphButton(QPushButton):
    """A transport button whose glyph is painted, not font-dependent."""

    def __init__(self, glyph: str = "play", parent: QWidget | None = None):
        super().__init__("", parent)
        self._glyph = glyph
        self._glyph_color: QColor | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def set_glyph(self, glyph: str) -> None:
        self._glyph = glyph
        self.update()

    def set_glyph_color(self, color: QColor | str | None) -> None:
        self._glyph_color = QColor(color) if color is not None else None
        self.update()

    def glyph(self) -> str:
        return self._glyph

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._glyph_color or self.palette().color(self.foregroundRole())
        if not self.isEnabled():
            color = QColor(theme.MUTED_DARK)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        rect = self.rect()
        cx, cy = rect.center().x() + 1, rect.center().y() + 1
        size = min(rect.width(), rect.height())
        if self._glyph == "play":
            path = QPainterPath()
            path.moveTo(cx - size * 0.10, cy - size * 0.15)
            path.lineTo(cx + size * 0.17, cy)
            path.lineTo(cx - size * 0.10, cy + size * 0.15)
            path.closeSubpath()
            painter.drawPath(path)
        elif self._glyph == "pause":
            width = size * 0.07
            height = size * 0.30
            gap = size * 0.075
            painter.drawRoundedRect(
                QRectF(cx - gap - width, cy - height / 2, width, height), width / 2, width / 2
            )
            painter.drawRoundedRect(
                QRectF(cx + gap, cy - height / 2, width, height), width / 2, width / 2
            )
        elif self._glyph == "stop":
            side = size * 0.22
            painter.drawRoundedRect(QRectF(cx - side / 2, cy - side / 2, side, side), 2, 2)
        elif self._glyph == "close":
            pen = QPen(color, max(1.6, size * 0.045))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            d = size * 0.12
            painter.drawLine(QPointF(cx - d, cy - d), QPointF(cx + d, cy + d))
            painter.drawLine(QPointF(cx + d, cy - d), QPointF(cx - d, cy + d))
        elif self._glyph == "back":
            pen = QPen(color, max(1.6, size * 0.045))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            d = size * 0.12
            painter.drawLine(QPointF(cx + d * 0.5, cy - d), QPointF(cx - d * 0.7, cy))
            painter.drawLine(QPointF(cx - d * 0.7, cy), QPointF(cx + d * 0.5, cy + d))
        elif self._glyph == "chevron":
            pen = QPen(color, max(1.6, size * 0.05))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            d = size * 0.13
            painter.drawLine(QPointF(cx - d * 0.55, cy - d), QPointF(cx + d * 0.45, cy))
            painter.drawLine(QPointF(cx + d * 0.45, cy), QPointF(cx - d * 0.55, cy + d))


class SegmentedControl(QWidget):
    changed = Signal(int)

    def __init__(self, options: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        self.setStyleSheet(
            f"SegmentedControl {{ background: {theme.BG_SOFT}; border: 1px solid {theme.LINE_SOFT}; border-radius: 12px; }}"
        )
        self._buttons: list[QPushButton] = []
        self._index = 0
        for index, option in enumerate(options):
            button = QPushButton(option)
            button.setProperty("role", "seg")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda checked=False, i=index: self.set_index(i, emit=True))
            layout.addWidget(button)
            self._buttons.append(button)
        self.set_index(0, emit=False)

    def index(self) -> int:
        return self._index

    def set_index(self, index: int, *, emit: bool = False) -> None:
        if not 0 <= index < len(self._buttons):
            return
        changed = index != self._index
        self._index = index
        for i, button in enumerate(self._buttons):
            button.setProperty("active", "true" if i == index else "false")
            theme.repolish(button)
        if emit and changed:
            self.changed.emit(index)


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------


class BusyOverlay(QWidget):
    """A blocking, animated overlay used while decoding or saving."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._message = "正在处理…"
        self._detail = ""
        self._progress: int | None = None
        self._angle = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self.parentWidget().rect())
        return False

    def start(self, message: str, detail: str = "", progress: int | None = None) -> None:
        self._message = message
        self._detail = detail
        self._progress = progress
        self._angle = 0
        parent = self.parentWidget()
        if parent:
            self.setGeometry(parent.rect())
        self.raise_()
        self.show()
        self._timer.start()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def finish(self) -> None:
        self._timer.stop()
        self.hide()

    def set_progress(self, value: int | None, message: str | None = None, detail: str | None = None) -> None:
        self._progress = value
        if message is not None:
            self._message = message
        if detail is not None:
            self._detail = detail
        self.update()

    def _tick(self) -> None:
        self._angle = (self._angle + 7.0) % 360
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def wheelEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(7, 9, 8, 188))
        card = QRectF(self.width() / 2 - 180, self.height() / 2 - 100, 360, 200)
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
        painter.setBrush(QColor(theme.SURFACE_RAISED))
        painter.drawRoundedRect(card, 22, 22)

        center = QPointF(card.center().x(), card.top() + 62)
        radius = 21
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT_DARK))
        painter.drawEllipse(center, radius + 5, radius + 5)
        pen = QPen(QColor(theme.ACCENT), 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        arc = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        painter.drawArc(arc, int(self._angle * 16), int(100 * 16))

        painter.setPen(QColor(theme.TEXT))
        painter.setFont(theme.font(16, 600))
        painter.drawText(
            QRectF(card.left() + 20, card.top() + 105, card.width() - 40, 26),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._message,
        )
        if self._detail:
            painter.setPen(QColor(theme.MUTED))
            painter.setFont(theme.font(12))
            painter.drawText(
                QRectF(card.left() + 20, card.top() + 132, card.width() - 40, 20),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                self._detail,
            )

        track = QRectF(card.left() + 28, card.bottom() - 28, card.width() - 56, 3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.LINE))
        painter.drawRoundedRect(track, 1.5, 1.5)
        if self._progress is not None:
            ratio = max(0.0, min(1.0, self._progress / 100.0))
        else:
            ratio = 0.22
        fill = QRectF(track.left(), track.top(), track.width() * ratio, track.height())
        painter.setBrush(QColor(theme.ACCENT))
        painter.drawRoundedRect(fill, 1.5, 1.5)


class Toast(QWidget):
    """Quiet, non-blocking status message at the lower-right corner."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._message = ""
        self._kind = "info"
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(1.0)
        self.setGraphicsEffect(self._effect)
        self._animation = QPropertyAnimation(self._effect, b"opacity", self)
        self._animation.setDuration(260)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(54)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if not parent:
            return
        width = min(360, max(240, parent.width() - 64))
        self.setFixedWidth(width)
        self.move(parent.width() - width - 26, parent.height() - self.height() - 24)

    def show_message(self, message: str, kind: str = "info") -> None:
        self._message = message
        self._kind = kind
        self._animation.stop()
        self._effect.setOpacity(1.0)
        self._reposition()
        self.raise_()
        self.show()
        self._timer.start(3200)
        self.update()

    def _fade_out(self) -> None:
        self._animation.stop()
        try:
            self._animation.finished.disconnect(self._hide_after_fade)
        except (RuntimeError, TypeError):
            pass
        self._animation.setStartValue(self._effect.opacity())
        self._animation.setEndValue(0.0)
        self._animation.finished.connect(self._hide_after_fade)
        self._animation.start()

    def _hide_after_fade(self) -> None:
        try:
            self._animation.finished.disconnect(self._hide_after_fade)
        except (RuntimeError, TypeError):
            pass
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(255, 255, 255, 25), 1))
        painter.setBrush(QColor(28, 34, 29, 246))
        painter.drawRoundedRect(rect, 15, 15)
        color = QColor(theme.ACCENT if self._kind == "info" else theme.MINT)
        if self._kind == "error":
            color = QColor(theme.DANGER)
        elif self._kind == "success":
            color = QColor(theme.MINT)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(22, self.height() / 2), 3.5, 3.5)
        painter.setPen(QColor(theme.TEXT))
        painter.setFont(theme.font(12.5, 500))
        metrics = QFontMetrics(painter.font())
        text = metrics.elidedText(self._message, Qt.TextElideMode.ElideRight, self.width() - 58)
        painter.drawText(
            QRectF(38, 0, self.width() - 52, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )


class LoopMark(QWidget):
    """Decorative empty-state mark: two linked arcs with an open center."""

    def __init__(self, size: int = 148, parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        for index, fraction in enumerate((0.92, 0.74, 0.54)):
            color = QColor(255, 255, 255, 11 + index * 5)
            painter.setPen(QPen(color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            radius = self._size * 0.5 * fraction
            painter.drawEllipse(center, radius, radius)
        pen = QPen(QColor(theme.ACCENT), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        ring = QRectF(center.x() - self._size * 0.24, center.y() - self._size * 0.24, self._size * 0.48, self._size * 0.48)
        painter.drawArc(ring, 35 * 16, 290 * 16)
        painter.setPen(QPen(QColor(theme.MINT), 1.4))
        smaller = QRectF(
            center.x() - self._size * 0.13,
            center.y() - self._size * 0.13,
            self._size * 0.26,
            self._size * 0.26,
        )
        painter.drawArc(smaller, 210 * 16, 250 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.ACCENT_BRIGHT))
        painter.drawEllipse(QPointF(center.x() + self._size * 0.24, center.y()), 4, 4)
