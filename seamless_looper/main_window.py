"""Application shell: sidebar, page routing, library lifecycle, and dialogs."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_TAGLINE, APP_TITLE, __version__, audio_io, theme
from .audio_io import AudioError, SavedTrack, scan_library
from .editor import EditorPage
from .library import LibraryPage
from .player import PlayerPage
from .widgets import (
    BusyOverlay,
    CoverArt,
    ElidedLabel,
    GlyphButton,
    LogoMark,
    Toast,
    app_icon,
    label,
)
from .workers import ImportWorker

APP_VERSION = __version__

SUPPORTED_DIALOG_FILTER = (
    "音频文件 (*.wav *.wave *.aif *.aiff *.flac *.mp3 *.m4a *.mp4 *.ogg *.oga);;"
    "所有文件 (*.*)"
)

def make_app_icon() -> QIcon:
    # The single shared icon asset lives at the project root (app.ico).
    return app_icon()


class MiniPlayer(QFrame):
    open_requested = Signal()
    toggle_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("MiniPlayer")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(64)
        self._build()
        self.hide()

    def _build(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.cover = CoverArt(46, 10)
        layout.addWidget(self.cover)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = ElidedLabel("")
        self.title.setFont(theme.font(11.5, 600))
        self.title.setStyleSheet(f"color: {theme.TEXT};")
        text.addWidget(self.title)
        self.state_label = label("正在播放", object_name="Tiny")
        text.addWidget(self.state_label)
        layout.addLayout(text, 1)
        self.toggle = GlyphButton("pause")
        self.toggle.setProperty("role", "icon")
        self.toggle.setFixedSize(26, 30)
        self.toggle.set_glyph_color(theme.TEXT_DIM)
        self.toggle.setToolTip("暂停 / 继续")
        self.toggle.clicked.connect(self.toggle_requested.emit)
        layout.addWidget(self.toggle)
        self.stop = GlyphButton("stop")
        self.stop.setProperty("role", "icon")
        self.stop.setFixedSize(24, 30)
        self.stop.set_glyph_color(theme.MUTED)
        self.stop.setToolTip("立即退出")
        self.stop.clicked.connect(self.stop_requested.emit)
        layout.addWidget(self.stop)

    def set_track(self, meta: dict, state: str) -> None:
        self.title.setText(str(meta.get("title", "未命名")))
        self.cover.set_cover(meta.get("_cover_path"), str(meta.get("title", "")))
        if state == "paused":
            self.state_label.setText("已暂停")
            self.toggle.set_glyph("play")
        else:
            self.state_label.setText("正在无缝循环")
            self.toggle.set_glyph("pause")
        self.show()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()
        super().mousePressEvent(event)


class Sidebar(QFrame):
    library_clicked = Signal()
    editor_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(224)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 28, 20, 20)
        layout.setSpacing(0)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        mark = LogoMark(39)
        brand.addWidget(mark)
        wordmark = QVBoxLayout()
        wordmark.setSpacing(0)
        title = label(APP_NAME, font=theme.serif(23, 700, spacing=0.5))
        title.setStyleSheet(f"color: {theme.TEXT};")
        wordmark.addWidget(title)
        english = label(APP_TAGLINE, object_name="Tiny", font=theme.font(7.5, 600, spacing=2.2))
        english.setStyleSheet(f"color: {theme.ACCENT};")
        wordmark.addWidget(english)
        brand.addLayout(wordmark, 1)
        layout.addLayout(brand)
        layout.addSpacing(34)

        layout.addWidget(label("工作区", object_name="Tiny"))
        layout.addSpacing(8)
        self.library_button = QPushButton("曲库")
        self.library_button.setProperty("role", "nav")
        self.library_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.library_button.clicked.connect(self.library_clicked.emit)
        layout.addWidget(self.library_button)
        self.editor_button = QPushButton("剪辑台")
        self.editor_button.setProperty("role", "nav")
        self.editor_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.editor_button.clicked.connect(self.editor_clicked.emit)
        layout.addWidget(self.editor_button)

        layout.addStretch(1)
        self.mini = MiniPlayer()
        layout.addWidget(self.mini)
        layout.addSpacing(12)

        status_card = QFrame()
        status_card.setObjectName("StatusCard")
        status_card.setStyleSheet(
            f"QFrame#StatusCard {{ background: {theme.SURFACE}; "
            f"border: 1px solid {theme.LINE_SOFT}; border-radius: 13px; }}"
        )
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(12, 11, 12, 11)
        status_layout.setSpacing(5)
        status_top = QHBoxLayout()
        status_top.setSpacing(6)
        self.audio_dot = QLabel("●")
        self.audio_dot.setStyleSheet(f"color: {theme.MINT}; font-size: 7px;")
        status_top.addWidget(self.audio_dot)
        self.audio_label = label("音频输出就绪", font=theme.font(10, 500))
        self.audio_label.setStyleSheet(f"color: {theme.TEXT_DIM};")
        status_top.addWidget(self.audio_label, 1)
        status_layout.addLayout(status_top)
        self.audio_detail = label("全程在本地处理", object_name="Tiny")
        status_layout.addWidget(self.audio_detail)
        layout.addWidget(status_card)
        layout.addSpacing(11)
        version = label(f"{APP_NAME}  v{APP_VERSION}  ·  MVP", object_name="Tiny")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

    def set_active(self, page: str) -> None:
        for button, key in ((self.library_button, "library"), (self.editor_button, "editor")):
            button.setProperty("active", "true" if key == page else "false")
            theme.repolish(button)

    def set_library_count(self, count: int) -> None:
        self.library_button.setText("曲库" if count == 0 else f"曲库（{count}首）")

    def set_audio_status(self, ok: bool, detail: str = "") -> None:
        color = theme.MINT if ok else theme.DANGER
        self.audio_dot.setStyleSheet(f"color: {color}; font-size: 7px;")
        self.audio_label.setText("音频输出就绪" if ok else "未找到音频输出")
        self.audio_detail.setText(detail or ("全程在本地处理" if ok else "请连接播放设备后重启"))


class MainWindow(QMainWindow):
    def __init__(self, *, library_dir: str | Path | None = None):
        super().__init__()
        self.library_dir = Path(library_dir) if library_dir else audio_io.default_library_dir()
        self.library_dir.mkdir(parents=True, exist_ok=True)
        # An INI next to the library is portable and also works in sandboxed
        # environments where the Windows registry is not writable.
        self._settings_path = self.library_dir.parent / "settings.ini"
        self._settings = QSettings(str(self._settings_path), QSettings.Format.IniFormat)
        self._tracks: list[dict] = []
        self._import_worker: ImportWorker | None = None
        self._last_dir = str(self._settings.value("last_import_dir", str(Path.home())))
        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(make_app_icon())
        self.setMinimumSize(1260, 780)
        self.resize(1300, 850)
        self.setAcceptDrops(True)
        self._dark_title_applied = False
        self._build()
        self._restore_settings()
        self._refresh_library()
        self._set_page(0)
        self._check_environment()

    # -- construction -------------------------------------------------------
    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("Root")
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.library_clicked.connect(lambda: self._set_page(0))
        self.sidebar.editor_clicked.connect(self._open_editor_from_sidebar)
        root.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.library = LibraryPage()
        self.editor = EditorPage(self.library_dir)
        self.player = PlayerPage()
        self.stack.addWidget(self.library)
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.player)
        root.addWidget(self.stack, 1)

        self.overlay = BusyOverlay(self)
        self.toast = Toast(self)

        self.library.import_requested.connect(self._choose_import)
        self.library.play_requested.connect(self._play_track)
        self.library.delete_requested.connect(self._delete_track)
        self.library.open_folder_requested.connect(self._open_track_folder)
        self.library.library_folder_requested.connect(self._open_library_folder)

        self.editor.back_requested.connect(lambda: self._set_page(0))
        self.editor.track_saved.connect(self._on_track_saved)
        self.editor.notice.connect(self._on_notice)
        self.editor.busy_started.connect(self._busy_start)
        self.editor.busy_progress.connect(self._busy_progress)
        self.editor.busy_finished.connect(self._busy_finish)

        self.player.back_requested.connect(lambda: self._set_page(0))
        self.player.notice.connect(self._on_notice)
        self.player.state_changed.connect(self._on_player_state)
        self.player.settings_changed.connect(self._save_playback_settings)

        self.sidebar.mini.open_requested.connect(lambda: self._set_page(2))
        self.sidebar.mini.toggle_requested.connect(self.player.toggle_pause)
        self.sidebar.mini.stop_requested.connect(self._mini_stop)

    # -- settings -----------------------------------------------------------
    def _restore_settings(self) -> None:
        mode = int(self._settings.value("playback/mode", 0))
        repeats = int(self._settings.value("playback/repeats", 3))
        volume = int(self._settings.value("playback/volume", 82))
        self.player.set_playback_defaults(mode, repeats, volume)
        geometry = self._settings.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1300, 850)

    def _save_playback_settings(self, mode: int, repeats: int, volume: int) -> None:
        self._settings.setValue("playback/mode", mode)
        self._settings.setValue("playback/repeats", repeats)
        self._settings.setValue("playback/volume", volume)

    # -- pages --------------------------------------------------------------
    def _set_page(self, index: int) -> None:
        # Page changes can leave a native tooltip floating for a frame while
        # the widget under the cursor disappears.  Hide it explicitly.
        QToolTip.hideText()
        previous = self.stack.currentIndex()
        if previous == 1 and index != 1:
            self.editor.deactivate()
        if index == 1 and not self.editor.has_audio:
            self._choose_import()
            return
        if index == 1:
            self.editor.activate()
        self.stack.setCurrentIndex(index)
        if index == 1:
            self.editor.waveform.setFocus(Qt.FocusReason.OtherFocusReason)
        self.sidebar.set_active({0: "library", 1: "editor", 2: "library"}.get(index, "library"))
        if index == 2:
            self.sidebar.mini.hide()
        else:
            self._sync_mini()
        if index == 0:
            self._refresh_library()
        self.toast.raise_()
        self.overlay.raise_()

    def _open_editor_from_sidebar(self) -> None:
        if self.player.is_active:
            answer = QMessageBox.question(
                self,
                "结束当前播放？",
                "进入剪辑台会立即结束当前播放。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.player.stop_immediately()
        if self.editor.has_audio:
            self._set_page(1)
        else:
            self._choose_import()

    # -- library ------------------------------------------------------------
    def _refresh_library(self) -> None:
        self._tracks = scan_library(self.library_dir)
        active_id = self.player.meta.get("id") if (self.player.is_active and self.player.meta) else None
        self.library.refresh(self._tracks, active_id)
        self.sidebar.set_library_count(len(self._tracks))

    def _meta_by_id(self, track_id: str) -> dict | None:
        for meta in self._tracks:
            if str(meta.get("id")) == str(track_id):
                return meta
        return None

    def _play_track(self, meta: dict) -> None:
        same = (
            self.player.meta is not None
            and str(self.player.meta.get("id")) == str(meta.get("id"))
        )
        if same:
            # Selecting a library item only opens the player.  Playback starts
            # exclusively from the player's own "开始循环" button.
            self._set_page(2)
            return

        # Switching tracks always performs an immediate exit first.
        if self.player.is_active:
            self.player.stop_immediately()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.player.load_track(meta)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self._show_error("无法打开曲目", str(exc))
            return
        QApplication.restoreOverrideCursor()
        self._set_page(2)
        self.player.set_playback_defaults(
            int(self._settings.value("playback/mode", 0)),
            int(self._settings.value("playback/repeats", 3)),
            int(self._settings.value("playback/volume", 82)),
        )

    def _delete_track(self, meta: dict) -> None:
        title = str(meta.get("title", "这首曲目"))
        answer = QMessageBox.question(
            self,
            "从曲库删除",
            f"确定删除「{title}」的曲库音频文件吗？\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        current_id = self.player.meta.get("id") if self.player.meta else None
        if str(current_id) == str(meta.get("id")):
            self.player.clear()
        try:
            audio_io.delete_track(meta)
        except OSError as exc:
            self._show_error("删除失败", str(exc))
            return
        self._refresh_library()
        self.toast.show_message("曲目已从曲库删除。", "info")

    def _open_library_folder(self) -> None:
        self.library_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library_dir)))

    def _open_track_folder(self, meta: dict) -> None:
        directory = meta.get("_directory")
        if directory:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    # -- import -------------------------------------------------------------
    def _choose_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入音频",
            self._last_dir,
            SUPPORTED_DIALOG_FILTER,
        )
        if not path:
            return
        self._last_dir = str(Path(path).parent)
        self._settings.setValue("last_import_dir", self._last_dir)
        self.import_path(path)

    def import_path(self, path: str | Path) -> None:
        if self._import_worker is not None and self._import_worker.isRunning():
            return
        source = Path(path).expanduser().resolve()
        if not source.exists():
            self._show_error("文件不存在", f"找不到：{source}")
            return
        if source.suffix.lower() not in audio_io.SUPPORTED_EXTENSIONS:
            self._show_error(
                "暂不支持这种格式",
                "MVP 支持 WAV、AIFF、FLAC、MP3、M4A/AAC、Ogg Opus/Vorbis。\n"
                "请先转换为 WAV 或 FLAC 后再导入。",
            )
            return
        if self.player.is_active:
            self.player.stop_immediately()
        self.editor.deactivate()
        self._busy_start("正在读取音频", f"{source.name}")
        self._import_worker = ImportWorker(source, self)
        worker = self._import_worker
        worker.progress.connect(self._busy_progress)
        worker.completed.connect(self._import_completed)
        worker.failed.connect(self._import_failed)
        worker.finished.connect(self._import_finished)
        worker.start()

    def _import_completed(self, decoded) -> None:
        self._busy_finish()
        try:
            self.editor.set_audio(decoded)
        except Exception as exc:
            self._show_error("无法载入音频", str(exc))
            return
        self._set_page(1)
        self.toast.show_message(
            f"已载入「{decoded.info.title}」 · 拖动切点开始剪辑",
            "success",
        )

    def _import_failed(self, message: str) -> None:
        self._busy_finish()
        self._show_error("无法导入音频", message)

    def _import_finished(self) -> None:
        worker = self._import_worker
        self._import_worker = None
        if worker is not None:
            worker.deleteLater()

    # -- editor save --------------------------------------------------------
    def _on_track_saved(self, saved: SavedTrack) -> None:
        self._refresh_library()
        meta = self._meta_by_id(saved.track_id)
        if meta is None:
            meta = saved.meta
        try:
            self.player.load_track(meta)
        except Exception as exc:
            self._show_error("保存成功，但无法打开", str(exc))
            self._set_page(0)
            return
        self.player.set_playback_defaults(
            int(self._settings.value("playback/mode", 0)),
            int(self._settings.value("playback/repeats", 3)),
            int(self._settings.value("playback/volume", 82)),
        )
        self._set_page(2)
        self.toast.show_message("已保存到曲库 · 随时可以开始溜🧊。", "success")

    # -- player state -------------------------------------------------------
    def _on_player_state(self, state: str) -> None:
        active = self.player.is_active
        active_id = self.player.meta.get("id") if (active and self.player.meta) else None
        self.library.set_active(active_id)
        self._sync_mini()

    def _sync_mini(self) -> None:
        if not self.player.is_active or self.stack.currentIndex() == 2 or self.player.meta is None:
            self.sidebar.mini.hide()
            return
        self.sidebar.mini.set_track(self.player.meta, self.player.state)

    def _mini_stop(self) -> None:
        self.player.stop_immediately()
        self.sidebar.mini.hide()

    # -- overlays / notices -------------------------------------------------
    def _busy_start(self, title: str, detail: str = "") -> None:
        self.overlay.start(title, detail, 0)

    def _busy_progress(self, value: int, message: str) -> None:
        self.overlay.set_progress(value, message)

    def _busy_finish(self) -> None:
        self.overlay.finish()

    def _on_notice(self, message: str, kind: str = "info") -> None:
        text = str(message).strip()
        if not text:
            return
        if kind == "error" and ("无法" in text or "失败" in text or len(text) > 110):
            self._show_error("播放提示", text)
        else:
            self.toast.show_message(text, kind)

    def _show_error(self, title: str, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(title)
        box.setText(title)
        box.setInformativeText(str(message))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    # -- environment / drag-drop -------------------------------------------
    def _check_environment(self) -> None:
        try:
            audio_io.ensure_ffmpeg()
        except AudioError as exc:
            self._show_error("缺少 ffmpeg", str(exc))
        try:
            import sounddevice as sd

            device = sd.query_devices(kind="output")
            if not device:
                self.sidebar.set_audio_status(False)
            else:
                self.sidebar.set_audio_status(True)
        except Exception:
            self.sidebar.set_audio_status(False)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if Path(url.toLocalFile()).suffix.lower() in audio_io.SUPPORTED_EXTENSIONS:
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in audio_io.SUPPORTED_EXTENSIONS:
                self.import_path(path)
                event.acceptProposedAction()
                return
        event.ignore()

    # -- lifecycle ----------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if os.name == "nt" and not self._dark_title_applied:
            self._dark_title_applied = True
            self._apply_dark_titlebar()

    def _apply_dark_titlebar(self) -> None:
        try:
            import ctypes

            hwnd = int(self.winId())
            enabled = ctypes.c_int(1)
            for attribute in (20, 19):
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)
                )
                if result == 0:
                    break
        except Exception:
            pass

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.overlay.setGeometry(self.rect())
        self.toast._reposition()
        self.overlay.raise_()
        self.toast.raise_()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._settings.setValue("window/geometry", self.saveGeometry())
        self._settings.sync()
        self.player.shutdown()
        self.editor.shutdown()
        if self._import_worker is not None and self._import_worker.isRunning():
            self._import_worker.wait(60_000)
        super().closeEvent(event)
