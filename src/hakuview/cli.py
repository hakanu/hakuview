"""Command-line interface and launcher for HakuView."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import List, Optional

from . import __app_name__, __version__, display_version
from .installer import (
    install_desktop_integration,
    run_self_update,
    uninstall_desktop_integration,
)


class _VersionAction(argparse.Action):
    """Like action="version", but only asks git for the version when the flag is used."""

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        print(f"{__app_name__} {display_version()}")
        parser.exit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hakuview",
        description=(
            "HakuView — Fast, lightweight IrfanView-style image & video viewer for Ubuntu and Windows.\n"
            "Open any file from the middle of a folder and cycle through all files with Left/Right arrow keys."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Path to an image, video, or directory to open.",
    )
    parser.add_argument(
        "-r",
        "--random",
        action="store_true",
        help="Open a random image/video from the target folder.",
    )
    parser.add_argument(
        "-s",
        "--slideshow",
        action="store_true",
        help="Start slideshow mode immediately upon launch.",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=3.0,
        help="Slideshow interval in seconds (default: 3.0).",
    )
    parser.add_argument(
        "-f",
        "--fullscreen",
        action="store_true",
        help="Launch directly in fullscreen mode.",
    )
    parser.add_argument(
        "--install-desktop",
        action="store_true",
        help="Install OS desktop shortcut and 'Open with HakuView' file association.",
    )
    parser.add_argument(
        "--uninstall-desktop",
        action="store_true",
        help="Remove OS desktop shortcut and file association.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Self-update HakuView to the latest version.",
    )
    parser.add_argument(
        "-v",
        "--version",
        action=_VersionAction,
        nargs=0,
        help="Show the version and exit.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.install_desktop:
        msg = install_desktop_integration()
        print(msg)
        return 0

    if args.uninstall_desktop:
        msg = uninstall_desktop_integration()
        print(msg)
        return 0

    if args.update:
        return run_self_update()

    # Import Qt only when launching the GUI so CLI utility flags stay instant
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HakuView.Desktop")
        except Exception:
            pass

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from .installer import ensure_hakuview_ico
    from .window import HakuViewWindow

    app = QApplication(sys.argv if argv is None else ["hakuview", *argv])
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)

    ico_path = ensure_hakuview_ico()
    if ico_path.exists():
        app_icon = QIcon(str(ico_path))
        app.setWindowIcon(app_icon)

    initial_path: Optional[Path] = Path(args.path) if args.path else None
    if initial_path is None and args.random:
        initial_path = Path.cwd()

    window = HakuViewWindow(
        initial_path=initial_path,
        pick_random=args.random,
        start_slideshow=args.slideshow,
        slideshow_interval=args.interval,
        start_fullscreen=args.fullscreen,
        check_updates=True,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
