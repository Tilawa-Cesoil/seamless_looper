"""
Launch this project.

Usage:
    python app.py [audio-file]

The optional audio-file argument opens the cutting desk immediately.  It is
handy for testing and for opening a song directly from Explorer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from seamless_looper import APP_NAME, APP_TITLE, __version__, theme
from seamless_looper.main_window import MainWindow, make_app_icon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} · 溜🧊溜大了做出来的")
    parser.add_argument("audio", nargs="?", help="启动后直接导入的音频文件")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    app.setOrganizationName("LoopStudio")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(make_app_icon())
    theme.apply_theme(app)

    window = MainWindow()
    window.show()
    if args.audio:
        path = Path(args.audio)
        if path.exists():
            window.import_path(path)
        else:
            window._show_error("文件不存在", f"找不到：{path}")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
