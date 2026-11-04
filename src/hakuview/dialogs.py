"""Keyboard-shortcuts and About dialogs."""

from __future__ import annotations

import html
from typing import Iterable, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox, QWidget

from . import __app_name__, __version__
from .installer import ensure_hakuview_ico
from .theme import PALETTE

_LICENSE_TEXT = """\
HakuView — MIT License

Copyright (c) 2026 Hakan Uysal (@hakanu_) / HakuView

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

--------------------------------------------------
Third-Party Open-Source Components & Licenses:
• Qt6 / PySide6: GNU Lesser General Public License v3 (LGPLv3)
• Pillow (PIL): Historical Permission Notice and Disclaimer (HPND / MIT-equivalent)
• FFmpeg (via Qt Multimedia): LGPL v2.1+ / GPL
"""


def show_shortcuts_dialog(parent: QWidget, rows: Iterable[Tuple[str, str]]) -> None:
    """Show a table of (keys, description) rows."""
    msg = (
        f"<h3>{__app_name__} Keyboard & Mouse Shortcuts</h3>"
        "<table cellpadding='4' style='font-size: 13px;'>"
        + "".join(f"<tr><td><b>{html.escape(k)}</b></td><td>{html.escape(t)}</td></tr>" for k, t in rows)
        + "</table>"
    )
    box = QMessageBox(parent)
    box.setWindowTitle(f"Keyboard Shortcuts — {__app_name__}")
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(msg)
    box.exec()


def show_about_dialog(parent: QWidget) -> None:
    accent = PALETTE["accent"]
    box = QMessageBox(parent)
    box.setWindowTitle(f"About {__app_name__}")
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setIconPixmap(QIcon(str(ensure_hakuview_ico())).pixmap(64, 64))
    box.setText(
        f"<h3>🦉 {__app_name__} <span style='font-size: 13px; color: {accent};'>v{__version__}</span></h3>"
        "<p>Fast, lightweight IrfanView-inspired image & video viewer for Ubuntu & Windows.</p>"
        "<p><b>Features:</b> Instant middle-of-folder cycling, border crop, rotate, "
        "1-click compression, hardware-accelerated video playback, and slideshows.</p>"
        f"<p><b>GitHub:</b> <a href='https://github.com/hakanu/hakuview' style='color: {accent};'>github.com/hakanu/hakuview</a><br>"
        f"<b>Author / Twitter:</b> <a href='https://x.com/hakanu_' style='color: {accent};'>x.com/hakanu_</a><br>"
        "<b>License:</b> Released under the <b>MIT License</b>.</p>"
    )
    box.setDetailedText(_LICENSE_TEXT)
    box.exec()
