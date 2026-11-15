"""
Visual language for Loop Studio (working product name: 衔音).

The palette is deliberately warm and quiet: near-black olive surfaces, cream
type, and a single amber accent.  Keeping colour decisions here makes it easy
to tune the whole application without touching layout code.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# Core palette -------------------------------------------------------------
BG = "#0B0D0C"
BG_SOFT = "#101310"
SURFACE = "#151916"
SURFACE_RAISED = "#1A1F1B"
SURFACE_HOVER = "#20261F"
LINE = "#2A302A"
LINE_SOFT = "#20251F"
TEXT = "#F3EFE5"
TEXT_DIM = "#B9BEB4"
MUTED = "#889188"
MUTED_DARK = "#5D665D"
ACCENT = "#E7B36A"
ACCENT_BRIGHT = "#F6CE90"
ACCENT_DARK = "#3A2C1A"
MINT = "#78C6A8"
MINT_DARK = "#1D332B"
ROSE = "#D98C82"
ROSE_DARK = "#35221F"
DANGER = "#E17D72"
DANGER_DARK = "#3A211E"

SANS = "Microsoft YaHei UI"
SANS_FALLBACK = "Noto Sans SC"
SERIF = "Noto Serif SC"
MONO = "Cascadia Mono"
NUMERIC = "Georgia"


def _first_family(candidates: list[str], fallback: str) -> str:
    available = set(QFontDatabase.families())
    for family in candidates:
        if family in available:
            return family
    return fallback


def resolve_fonts() -> None:
    """Resolve fonts that are actually installed on this machine."""
    global SANS, SERIF, MONO, NUMERIC
    SANS = _first_family(["Microsoft YaHei UI", "Noto Sans SC", "Microsoft YaHei"], "Sans Serif")
    SERIF = _first_family(["Noto Serif SC", "STSong", "SimSun"], SANS)
    MONO = _first_family(["Cascadia Mono", "Consolas", "JetBrains Mono"], "Monospace")
    NUMERIC = _first_family(["Georgia", "Cambria", "Times New Roman"], SERIF)


def font(size: int | float, weight: int = 400, family: str | None = None, spacing: float = 0) -> QFont:
    f = QFont(family or SANS)
    f.setPixelSize(round(size))
    f.setWeight(QFont.Weight(weight))
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def serif(size: int | float, weight: int = 500, spacing: float = 0) -> QFont:
    return font(size, weight, SERIF, spacing)


def mono(size: int | float, weight: int = 400, spacing: float = 0) -> QFont:
    return font(size, weight, MONO, spacing)


def numeric(size: int | float, weight: int = 400, spacing: float = 0) -> QFont:
    return font(size, weight, NUMERIC, spacing)


def apply_theme(app: QApplication) -> None:
    resolve_fonts()
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(BG))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(MUTED))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE_RAISED))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    app.setPalette(palette)
    app.setFont(font(14))
    app.setStyleSheet(STYLESHEET)


STYLESHEET = f"""
* {{
    outline: 0;
}}
QWidget {{
    color: {TEXT};
    font-family: "{SANS}";
    font-size: 14px;
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QToolTip {{
    background: {SURFACE_RAISED};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 6px 9px;
}}
QFrame#Sidebar {{
    background: {BG_SOFT};
    border-right: 1px solid {LINE_SOFT};
}}
QFrame#Card, QFrame#Panel {{
    background: {SURFACE};
    border: 1px solid {LINE_SOFT};
    border-radius: 18px;
}}
QFrame#PanelRaised {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    border-radius: 18px;
}}
QFrame#Hairline {{
    background: {LINE_SOFT};
    max-height: 1px;
    border: none;
}}
QFrame#PointCard {{
    background: {SURFACE};
    border: 1px solid {LINE_SOFT};
    border-radius: 15px;
}}
QFrame#PointCard:hover {{
    background: {SURFACE_RAISED};
    border-color: {LINE};
}}
QFrame#PointCard[active="true"] {{
    background: {SURFACE_RAISED};
    border-color: {ACCENT};
}}
QFrame#ControlBar {{
    background: {SURFACE};
    border: 1px solid {LINE_SOFT};
    border-radius: 16px;
}}
QFrame#TrackCard {{
    background: {SURFACE};
    border: 1px solid {LINE_SOFT};
    border-radius: 17px;
}}
QFrame#TrackCard:hover {{
    background: {SURFACE_RAISED};
    border-color: #353D34;
}}
QFrame#TrackCard[active="true"] {{
    background: {SURFACE_RAISED};
    border-color: {ACCENT_DARK};
}}
QFrame#EmptyPanel {{
    background: {SURFACE};
    border: 1px solid {LINE_SOFT};
    border-radius: 22px;
}}
QFrame#MiniPlayer {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    border-radius: 14px;
}}
QFrame#MiniPlayer:hover {{
    background: {SURFACE_HOVER};
    border-color: #3A4439;
}}
QLabel {{
    background: transparent;
}}
QLabel#Eyebrow {{
    color: {ACCENT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}}
QLabel#PageTitle {{
    color: {TEXT};
    font-family: "{SERIF}";
    font-size: 40px;
    font-weight: 600;
}}
QLabel#PageLead {{
    color: {MUTED};
    font-size: 14px;
}}
QLabel#SectionTitle {{
    color: {TEXT};
    font-family: "{SERIF}";
    font-size: 25px;
    font-weight: 600;
}}
QLabel#CardTitle {{
    color: {TEXT};
    font-size: 18px;
    font-weight: 600;
}}
QLabel#Muted {{
    color: {MUTED};
}}
QLabel#Tiny {{
    color: {MUTED_DARK};
    font-size: 11px;
}}
QLabel#Chip {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    border-radius: 10px;
    color: {TEXT_DIM};
    font-size: 11px;
    padding: 3px 9px;
}}
QPushButton {{
    background: transparent;
    color: {TEXT};
    border: none;
    border-radius: 12px;
    padding: 10px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: {SURFACE_HOVER};
}}
QPushButton:pressed {{
    background: {SURFACE_RAISED};
}}
QPushButton:disabled {{
    color: {MUTED_DARK};
    background: transparent;
}}
QPushButton[role="primary"] {{
    background: {ACCENT};
    color: #21180D;
    border: 1px solid {ACCENT};
    padding: 11px 20px;
}}
QPushButton[role="primary"]:hover {{
    background: {ACCENT_BRIGHT};
    border-color: {ACCENT_BRIGHT};
}}
QPushButton[role="primary"]:pressed {{
    background: #D8A45E;
}}
QPushButton[role="primary"]:disabled {{
    background: {ACCENT_DARK};
    border-color: {ACCENT_DARK};
    color: #8A7356;
}}
QPushButton[role="secondary"] {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    color: {TEXT};
}}
QPushButton[role="secondary"]:hover {{
    background: {SURFACE_HOVER};
    border-color: #3E463D;
}}
QPushButton[role="secondary"]:disabled {{
    background: transparent;
    border-color: {LINE_SOFT};
    color: {MUTED_DARK};
}}
QPushButton[role="ghost"] {{
    background: transparent;
    border: 1px solid {LINE};
    color: {TEXT_DIM};
}}
QPushButton[role="ghost"]:hover {{
    background: {SURFACE};
    border-color: #465046;
    color: {TEXT};
}}
QPushButton[role="danger"] {{
    background: transparent;
    border: 1px solid {DANGER_DARK};
    color: {DANGER};
}}
QPushButton[role="danger"]:hover {{
    background: {DANGER_DARK};
    border-color: {DANGER};
}}
QPushButton[role="danger"]:disabled {{
    background: transparent;
    color: {MUTED_DARK};
    border-color: {LINE_SOFT};
}}
QPushButton[role="ghost"]:disabled {{
    color: {MUTED_DARK};
    border-color: {LINE_SOFT};
}}
QPushButton[role="nav"] {{
    text-align: left;
    padding: 11px 14px;
    border-radius: 11px;
    color: {MUTED};
    font-weight: 500;
}}
QPushButton[role="nav"]:hover {{
    color: {TEXT};
    background: {SURFACE};
}}
QPushButton[role="nav"][active="true"] {{
    color: {TEXT};
    background: {SURFACE_RAISED};
}}
QPushButton[role="seg"] {{
    background: transparent;
    color: {MUTED};
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 9px 16px;
    font-weight: 600;
}}
QPushButton[role="seg"]:hover {{
    color: {TEXT};
}}
QPushButton[role="seg"][active="true"] {{
    background: {SURFACE_HOVER};
    border-color: {LINE};
    color: {TEXT};
}}
QPushButton[role="transport"] {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    border-radius: 23px;
    min-width: 46px;
    max-width: 46px;
    min-height: 46px;
    max-height: 46px;
    padding: 0;
    font-size: 15px;
}}
QPushButton[role="transport"]:hover {{
    background: {SURFACE_HOVER};
    border-color: #4A544A;
}}
QPushButton[role="transport"][playing="true"] {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: #21180D;
}}
QPushButton[role="round"] {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    border-radius: 24px;
    min-width: 48px;
    max-width: 48px;
    min-height: 48px;
    max-height: 48px;
    padding: 0;
    font-size: 17px;
}}
QPushButton[role="round"]:hover {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: #21180D;
}}
QPushButton[role="icon"] {{
    background: transparent;
    border: none;
    padding: 4px;
    border-radius: 8px;
    color: {MUTED};
}}
QPushButton[role="icon"]:hover {{
    color: {TEXT};
    background: {SURFACE_HOVER};
}}
QLineEdit {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    border-radius: 11px;
    padding: 10px 13px;
    color: {TEXT};
    selection-background-color: {ACCENT_DARK};
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}
QSpinBox {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    border-radius: 11px;
    padding: 8px 10px;
    color: {TEXT};
    min-width: 76px;
}}
QSpinBox:focus {{
    border-color: {ACCENT};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 18px;
    border: none;
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px 0;
}}
QScrollBar::handle:vertical {{
    background: #353C35;
    min-height: 32px;
    border-radius: 4px;
}}
QScrollBar::handle:vertical:hover {{
    background: #485248;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QSlider::groove:horizontal {{
    background: {LINE};
    height: 4px;
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    width: 15px;
    height: 15px;
    margin: -6px 0;
    border-radius: 7px;
    border: 2px solid {ACCENT};
}}
QSlider::handle:horizontal:hover {{
    background: {ACCENT_BRIGHT};
}}
QMenu {{
    background: {SURFACE_RAISED};
    border: 1px solid {LINE};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 22px 8px 14px;
    border-radius: 6px;
    color: {TEXT_DIM};
}}
QMenu::item:selected {{
    background: {SURFACE_HOVER};
    color: {TEXT};
}}
QMessageBox {{
    background: {SURFACE};
}}
QFileDialog {{
    background: {BG_SOFT};
}}
"""


def repolish(widget) -> None:
    """Re-evaluate property selectors after changing a dynamic property."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
