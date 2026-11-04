"""Command-line interface and launcher for HakuView."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import List, Optional

from . import __app_name__, __version__
from .installer import (
    install_desktop_integration,
    run_self_update,
    uninstall_desktop_integration,
)


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
        action="version",
        version=f"{__app_name__} {__version__}",
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
    from PySide6.QtWidgets import QApplication
    from .window import HakuViewWindow

    app = QApplication(sys.argv if argv is None else ["hakuview", *argv])
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)

    initial_path: Optional[Path] = Path(args.path) if args.path else None
    if initial_path is None and args.random:
        initial_path = Path.cwd()

    window = HakuViewWindow(
        initial_path=initial_path,
        pick_random=args.random,
        start_slideshow=args.slideshow,
        slideshow_interval=args.interval,
        start_fullscreen=args.fullscreen,
    )
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
