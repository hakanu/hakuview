"""Small reusable widgets and helpers for the main window."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMenu, QPushButton, QWidget

from .theme import PALETTE


def three_dots_icon(size: int = 24, color: Optional[QColor] = None) -> QIcon:
    """Draw a reliable, anti-aliased vertical 3-dots icon (font glyphs often render as blank boxes)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(color or QColor(PALETTE["text"]))
    painter.setPen(Qt.PenStyle.NoPen)
    for cy in (size * 0.25, size * 0.5, size * 0.75):
        painter.drawEllipse(QPointF(size / 2.0, cy), 1.8, 1.8)
    painter.end()
    return QIcon(pixmap)


def chevron_icon(size: int = 16, color: Optional[QColor] = None) -> QIcon:
    """Light '»' icon for the toolbar overflow button (Qt's stock arrow is invisible on the dark theme)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(color or QColor(PALETTE["text"]), 1.6))
    for x0 in (size * 0.25, size * 0.5):
        painter.drawPolyline([QPointF(x0, size * 0.3), QPointF(x0 + size * 0.2, size * 0.5), QPointF(x0, size * 0.7)])
    painter.end()
    return QIcon(pixmap)


class FullscreenControls(QWidget):
    """Floating More / Restore / Close buttons shown in the top-right corner while fullscreen."""

    moreClicked = Signal()
    restoreClicked = Signal()
    closeClicked = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("fullscreenOverlay")  # styled by theme.APP_QSS
        # QWidget subclasses only paint a stylesheet background with this attribute set.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(4)

        self.btn_more = self._add_button(layout, "", "More Options (3 dots menu)", self.moreClicked)
        self.btn_more.setIcon(three_dots_icon(size=20))
        self.btn_more.setIconSize(QSize(16, 16))
        self.btn_restore = self._add_button(layout, "🗗", "Exit Fullscreen (F or Esc)", self.restoreClicked)
        self.btn_close = self._add_button(layout, "✕", "Close Window (Ctrl+W)", self.closeClicked)
        self.btn_close.setObjectName("fsClose")
        self.hide()

    def _add_button(self, layout: QHBoxLayout, text: str, tooltip: str, signal) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolTip(tooltip)
        btn.clicked.connect(signal.emit)
        layout.addWidget(btn)
        return btn

    def place(self, margin_right: int = 16, margin_top: int = 12) -> None:
        """Pin to the parent's top-right corner, above the image/video."""
        if not self.isVisible():
            return
        self.adjustSize()
        self.move(self.parentWidget().width() - self.width() - margin_right, margin_top)
        self.raise_()


def menu_position_under(menu: QMenu, anchor: QWidget, fallback: QWidget) -> QPoint:
    """Where to pop `menu` so its top-right corner sits under `anchor`, clamped to the screen.

    Pops upwards if there's no room below. If `anchor` is hidden, uses the top-right of `fallback`.
    """
    menu.adjustSize()
    menu_w = menu.sizeHint().width()
    menu_h = menu.sizeHint().height()

    if anchor.isVisible():
        pos = anchor.mapToGlobal(QPoint(anchor.width() - menu_w, anchor.height()))
    else:
        pos = fallback.mapToGlobal(QPoint(fallback.width() - menu_w - 16, 40))

    screen = anchor.screen() or fallback.screen() or QApplication.primaryScreen()
    if screen is None:
        return pos
    avail = screen.availableGeometry()
    x = max(avail.left(), min(pos.x(), avail.right() - menu_w))
    y = pos.y()
    if y + menu_h > avail.bottom():
        y = anchor.mapToGlobal(QPoint(0, 0)).y() - menu_h if anchor.isVisible() else avail.bottom() - menu_h
    y = max(avail.top(), y)
    return QPoint(x, y)
