"""HakuView's themes: dark and light palettes and the Qt stylesheet built from them.

The main window applies app_qss() to itself, so every child widget (toolbar, color bar,
fullscreen controls, video transport bar, AV1 banner, menus, message boxes) is styled from
here. Widget-specific rules are scoped by objectName instead of being set inline.

PALETTE always holds the *active* palette and is updated in place by set_theme(), so code
that paints by hand (canvas backdrop, icons) can read colors from it at paint time.
"""

from __future__ import annotations

import colorsys
from pathlib import Path
import re
import tempfile
from typing import Dict

THEME_MODES = ("system", "dark", "light")

DARK: Dict[str, str] = {
    "window": "#14161a",
    "panel": "#1a1d25",  # toolbars, menus, popups, color bar
    "statusbar": "#181b21",
    "video_bar": "#161920",
    "surface": "#252a34",  # buttons, inputs
    "surface_hover": "#323946",
    "surface_disabled": "#1e222a",
    "combo_hover": "#2e3442",
    "border": "#353c4b",
    "border_hover": "#4b5563",
    "border_subtle": "#2b303c",
    "border_disabled": "#262b35",
    "popup_border": "#334155",
    "groove": "#2b3240",
    "text": "#e2e8f0",
    "text_bright": "#f1f5f9",
    "text_soft": "#cbd5e1",
    "text_muted": "#94a3b8",
    "text_disabled": "#64748b",
    "accent": "#38bdf8",
    "accent_strong": "#0284c7",
    "accent_dark": "#0369a1",
    "danger": "#dc2626",
    "danger_soft": "#ef4444",
    "warning_bg": "#2e1515",
    "warning_border": "#7f1d1d",
    "warning_text": "#fecaca",
    "overlay_bg": "rgba(20, 23, 30, 0.90)",
    "overlay_border": "#3b4252",
    "video_button": "#262b36",
    "video_button_border": "#374151",
    # Painted by hand (canvas)
    "canvas": "#14161a",
    "canvas_hint": "#919aa5",
    "checker_a": "#2a2c30",
    "checker_b": "#3a3c42",
}

LIGHT: Dict[str, str] = {
    "window": "#eceff3",
    "panel": "#f8f9fb",
    "statusbar": "#f1f3f6",
    "video_bar": "#f4f5f8",
    "surface": "#ffffff",
    "surface_hover": "#e8edf3",
    "surface_disabled": "#f1f3f6",
    "combo_hover": "#f1f5f9",
    "border": "#cfd5de",
    "border_hover": "#9aa5b4",
    "border_subtle": "#dde2e8",
    "border_disabled": "#e3e7ec",
    "popup_border": "#cbd5e1",
    "groove": "#d5dbe3",
    "text": "#1e293b",
    "text_bright": "#0f172a",
    "text_soft": "#334155",
    "text_muted": "#64748b",
    "text_disabled": "#a3adba",
    "accent": "#0284c7",
    "accent_strong": "#0284c7",
    "accent_dark": "#0369a1",
    "danger": "#dc2626",
    "danger_soft": "#ef4444",
    "warning_bg": "#fef2f2",
    "warning_border": "#fecaca",
    "warning_text": "#991b1b",
    "overlay_bg": "rgba(248, 249, 251, 0.92)",
    "overlay_border": "#cbd5e1",
    "video_button": "#ffffff",
    "video_button_border": "#cfd5de",
    "canvas": "#e4e7ec",
    "canvas_hint": "#64748b",
    "checker_a": "#ffffff",
    "checker_b": "#dfe3e8",
}

PALETTE: Dict[str, str] = dict(DARK)
_active = "dark"


def system_prefers_dark() -> bool:
    """Desktop light/dark preference (Qt >= 6.5); dark when unknown, matching the old look."""
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtCore import Qt

        app = QGuiApplication.instance()
        if app is None:
            return True
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return False
        if scheme == Qt.ColorScheme.Dark:
            return True
        # Unknown (common on X11 desktops like XFCE): guess from the window palette.
        return QGuiApplication.palette().window().color().lightness() < 128
    except Exception:
        return True


def resolve(mode: str) -> str:
    """'system' -> 'dark' / 'light' from the desktop; other values pass through."""
    if mode == "light":
        return "light"
    if mode == "dark":
        return "dark"
    return "dark" if system_prefers_dark() else "light"


def set_theme(mode: str) -> str:
    """Activate a theme mode ('system', 'dark', 'light'); returns the resolved 'dark' / 'light'."""
    global _active
    _active = resolve(mode)
    PALETTE.clear()
    PALETTE.update(DARK if _active == "dark" else LIGHT)
    return _active


def is_dark() -> bool:
    return _active == "dark"


_SVG_SHAPES = {
    "arrow-down": '<polygon points="1,3 9,3 5,8" fill="{c}"/>',
    "arrow-up": '<polygon points="1,7 9,7 5,2" fill="{c}"/>',
    "check": '<polyline points="2,5.2 4.2,7.4 8.2,2.8" fill="none" stroke="{c}" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/>',
    "dot": '<circle cx="5" cy="5" r="2.6" fill="{c}"/>',
}


def _svg_file(shape: str, color: str) -> str:
    """Path of a tiny 10x10 SVG in `color`: QSS needs image files for arrows and indicator marks."""
    folder = Path(tempfile.gettempdir()) / "hakuview-theme"
    folder.mkdir(exist_ok=True)
    path = folder / f"{shape}-{color.lstrip('#')}.svg"
    if not path.exists():
        body = _SVG_SHAPES[shape].format(c=color)
        path.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">{body}</svg>', encoding="utf-8")
    return path.as_posix()


def _qss(template: str) -> str:
    """Substitute @name palette tokens (QSS uses braces, so str.format doesn't fit)."""
    tokens = dict(PALETTE)
    tokens["arrow_down"] = _svg_file("arrow-down", PALETTE["text_muted"])
    tokens["arrow_up"] = _svg_file("arrow-up", PALETTE["text_muted"])
    tokens["check_mark"] = _svg_file("check", "#ffffff")
    tokens["radio_dot"] = _svg_file("dot", PALETTE["accent_strong"])
    return re.sub(r"@([a-z_]+)", lambda m: tokens[m.group(1)], template)


def adapt_color(hex_color: str) -> str:
    """Light-theme counterpart of a color from a dark-only stylesheet.

    Neutral greys get their lightness mirrored (dark background -> light background,
    light text -> dark text); saturated accents keep their hue but are darkened enough
    to stay readable on white.
    """
    r, g, b = (int(hex_color[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    h, lightness, sat = colorsys.rgb_to_hls(r, g, b)
    if sat < 0.35 or lightness < 0.08 or lightness > 0.95:
        # Dark backgrounds/borders -> near-white with the same relative steps; light text -> dark.
        lightness = 1.0 - lightness * 0.45 if lightness < 0.5 else (1.0 - lightness) * 1.3
    else:
        lightness = min(lightness, 0.38)
    r, g, b = colorsys.hls_to_rgb(h, lightness, sat)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def adapt(text: str) -> str:
    """Return a dark-only stylesheet/color unchanged in dark mode, auto-converted in light mode."""
    if is_dark():
        return text
    return re.sub(r"#[0-9a-fA-F]{6}\b", lambda m: adapt_color(m.group(0)), text)


def app_qss() -> str:
    return _qss(_APP_QSS_TEMPLATE)


_APP_QSS_TEMPLATE = """
    QMainWindow { background-color: @window; color: @text; }

    /* ---- Toolbar (inside #topBar, next to the ⋯ button) ---- */
    #topBar { background-color: @panel; border-bottom: 1px solid @border_subtle; }
    QToolBar { background-color: @panel; border: none; spacing: 4px; padding: 3px 6px; }
    QToolBar QPushButton {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 4px 9px;
        font-size: 12px;
    }
    QToolBar QPushButton:hover { background-color: @surface_hover; border-color: @border_hover; }
    QToolBar QPushButton:checked { background-color: @accent_strong; border-color: @accent; color: #ffffff; }
    QToolBar QPushButton:disabled {
        color: @text_disabled;
        background-color: @surface_disabled;
        border-color: @border_disabled;
    }
    #topBar QPushButton#moreButton {
        background-color: @surface;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 4px 7px;
    }
    #topBar QPushButton#moreButton:hover { background-color: @surface_hover; border-color: @border_hover; }
    QToolBar QLabel { color: @text_muted; font-size: 12px; }
    QToolButton {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 13px;
    }
    QToolButton:hover { background-color: @surface_hover; border-color: @border_hover; }
    QToolBar QToolButton#qt_toolbar_ext_button { padding: 2px; min-width: 16px; }

    /* ---- Inputs ---- */
    QDoubleSpinBox {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 12px;
    }
    QCheckBox { color: @text_soft; font-size: 12px; spacing: 6px; }

    /* Checkbox / radio indicators: Fusion draws its marks in a grey that disappears on these
       backgrounds, so a click looked like it did nothing. Draw them from the palette instead. */
    QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; }
    QCheckBox::indicator {
        border: 1px solid @border_hover;
        border-radius: 3px;
        background-color: @surface;
    }
    QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: @accent; }
    QCheckBox::indicator:checked {
        background-color: @accent_strong;
        border-color: @accent_strong;
        image: url(@check_mark);
    }
    QRadioButton { spacing: 6px; }
    QRadioButton::indicator {
        border: 1px solid @border_hover;
        border-radius: 8px;
        background-color: @surface;
    }
    QRadioButton::indicator:checked { border-color: @accent_strong; image: url(@radio_dot); }
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
        background-color: @surface_disabled;
        border-color: @border_disabled;
    }
    QComboBox {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 2px 22px 2px 8px;
        font-size: 12px;
        min-height: 22px;
    }
    QComboBox:hover { background-color: @combo_hover; border-color: @border_hover; }
    QComboBox:on { border-color: @accent_strong; }
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid @border;
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
    }
    QComboBox::down-arrow { image: url(@arrow_down); width: 10px; height: 10px; }

    /* Dropdown buttons, spin boxes and separators (Qt's default arrows are invisible on dark) */
    QToolBar::separator { background-color: @border_subtle; width: 1px; margin: 5px 4px; }
    QToolButton::menu-button { border: none; border-left: 1px solid @border; width: 16px; background: transparent; }
    QToolButton::menu-arrow { image: url(@arrow_down); width: 10px; height: 10px; }
    QToolButton::menu-indicator {
        image: url(@arrow_down);
        subcontrol-origin: padding;
        subcontrol-position: center right;
        width: 10px;
        height: 10px;
        right: 4px;
    }
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {
        subcontrol-origin: border;
        width: 16px;
        border: none;
        border-left: 1px solid @border;
        background: transparent;
    }
    QAbstractSpinBox::up-button { subcontrol-position: top right; }
    QAbstractSpinBox::down-button { subcontrol-position: bottom right; }
    QAbstractSpinBox::up-arrow { image: url(@arrow_up); width: 8px; height: 8px; }
    QAbstractSpinBox::down-arrow { image: url(@arrow_down); width: 8px; height: 8px; }
    QComboBox QAbstractItemView {
        background-color: @panel;
        color: @text;
        border: 1px solid @popup_border;
        border-radius: 6px;
        padding: 4px;
        selection-background-color: @accent_strong;
        selection-color: #ffffff;
        outline: none;
    }
    QComboBox QAbstractItemView::item {
        min-height: 24px;
        padding: 4px 10px;
        color: @text;
        background-color: @panel;
    }
    QComboBox QAbstractItemView::item:hover,
    QComboBox QAbstractItemView::item:selected { background-color: @accent_strong; color: #ffffff; }

    /* ---- Message boxes & progress dialogs (update prompt, errors, About...) ---- */
    QMessageBox, QProgressDialog { background-color: @panel; }
    QMessageBox QLabel, QProgressDialog QLabel { color: @text; }
    QMessageBox QCheckBox { color: @text; }
    QMessageBox QPushButton, QProgressDialog QPushButton {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 4px 10px;
    }
    QMessageBox QPushButton:hover, QProgressDialog QPushButton:hover {
        background-color: @surface_hover;
        border-color: @border_hover;
    }
    QMessageBox QPushButton:default { background-color: @accent_strong; border-color: @accent; color: #ffffff; }
    QMessageBox QTextEdit { background-color: @window; color: @text; border: 1px solid @border; }
    QProgressBar { background-color: @surface; border: 1px solid @border; border-radius: 4px; height: 8px; }
    QProgressBar::chunk { background-color: @accent_strong; border-radius: 3px; }

    /* ---- Menus ---- */
    QMenu {
        background-color: @panel;
        color: @text;
        border: 1px solid @popup_border;
        border-radius: 6px;
        padding: 4px 0;
        font-size: 12px;
    }
    QMenu::item { padding: 6px 24px 6px 14px; background-color: transparent; }
    QMenu::item:selected { background-color: @accent_strong; color: #ffffff; }
    QMenu::item:disabled { color: @text_disabled; }
    QMenu::separator { height: 1px; background-color: @groove; margin: 4px 8px; }

    /* ---- Status bar ---- */
    QStatusBar {
        background-color: @statusbar;
        color: @text_muted;
        border-top: 1px solid @border_disabled;
        font-size: 12px;
    }
    QStatusBar QLabel { color: @text_soft; padding: 0 6px; }
    QStatusBar QLabel#modifiedLabel { color: @accent; font-weight: bold; }

    /* ---- Fullscreen floating controls ---- */
    #fullscreenOverlay {
        background-color: @overlay_bg;
        border: 1px solid @overlay_border;
        border-radius: 6px;
    }
    #fullscreenOverlay QPushButton {
        background-color: transparent;
        color: @text;
        border: 1px solid transparent;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 13px;
        min-width: 22px;
        min-height: 20px;
    }
    #fullscreenOverlay QPushButton:hover { background-color: @border; border-color: @border_hover; }
    #fullscreenOverlay QPushButton#fsClose:hover {
        background-color: @danger;
        border-color: @danger_soft;
        color: #ffffff;
    }

    /* ---- Brightness / contrast bar ---- */
    #colorBar { background-color: @panel; border-bottom: 1px solid @border_subtle; padding: 4px 8px; }
    #colorBar QLabel { color: @text_muted; font-size: 11px; font-weight: 500; }
    #colorBar QLabel#colorTitle { color: @accent; font-weight: bold; }
    #colorBar QLabel#colorValue { color: @text; font-family: monospace; }
    #colorBar QSlider::groove:horizontal { height: 4px; background: @groove; border-radius: 2px; }
    #colorBar QSlider::sub-page:horizontal { background: @accent_strong; border-radius: 2px; }
    #colorBar QSlider::handle:horizontal { background: @accent; width: 12px; margin: -4px 0; border-radius: 6px; }
    #colorBar QPushButton {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 3px 8px;
        font-size: 11px;
    }
    #colorBar QPushButton:hover { background-color: @surface_hover; border-color: @border_hover; }
    #colorBar QPushButton#btnSaveAdj {
        background-color: @accent_strong;
        color: #ffffff;
        border-color: @accent;
        font-weight: 500;
    }
    #colorBar QPushButton#btnSaveAdj:hover { background-color: @accent_dark; }

    /* ---- Video transport bar ---- */
    #videoControls { background-color: @video_bar; }
    #videoControls QLabel { color: @text; font-size: 12px; }
    #videoControls QPushButton {
        background-color: @video_button;
        border: 1px solid @video_button_border;
        border-radius: 4px;
        padding: 4px 10px;
        color: @text_bright;
        font-size: 12px;
    }
    #videoControls QPushButton:hover { background-color: @surface_hover; }
    #videoControls QPushButton:disabled {
        background-color: @surface_disabled;
        color: @text_disabled;
        border-color: @border_disabled;
    }
    #videoControls QPushButton#btn_trim_ready {
        background-color: @accent_strong;
        color: #ffffff;
        font-weight: bold;
        border: 1px solid @accent;
    }
    #videoControls QPushButton#btn_trim_ready:hover { background-color: @accent_dark; }

    /* ---- AV1 compatibility banner ---- */
    #av1Banner { background-color: @warning_bg; border-bottom: 1px solid @warning_border; }
    #av1Banner QLabel { color: @warning_text; font-size: 12px; }
    #av1Banner QPushButton {
        background-color: @danger_soft;
        color: #ffffff;
        font-weight: bold;
        border: none;
        border-radius: 4px;
        padding: 3px 10px;
        font-size: 12px;
    }
    #av1Banner QPushButton:hover { background-color: @danger; }
    #av1Banner QPushButton:disabled { background-color: @border_hover; color: @text_disabled; }
    /* ---- Settings / input dialogs ---- */
    QDialog#settingsDialog, QInputDialog { background-color: @panel; color: @text; }
    QDialog#settingsDialog QLabel, QInputDialog QLabel { color: @text; }
    QDialog#settingsDialog QGroupBox {
        color: @text_muted;
        border: 1px solid @border_subtle;
        border-radius: 6px;
        margin-top: 10px;
        padding: 10px 8px 6px 8px;
        font-weight: bold;
    }
    QDialog#settingsDialog QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
    QDialog#settingsDialog QRadioButton, QDialog#settingsDialog QCheckBox { color: @text; font-size: 12px; }
    QDialog#settingsDialog QPushButton, QInputDialog QPushButton {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 4px 12px;
    }
    QDialog#settingsDialog QPushButton:hover, QInputDialog QPushButton:hover { background-color: @surface_hover; }
    QDialog#settingsDialog QPushButton:default, QInputDialog QPushButton:default {
        background-color: @accent_strong;
        border-color: @accent;
        color: #ffffff;
    }
    /* ---- Go to File (Ctrl+G) ---- */
    QDialog#gotoDialog { background-color: @panel; color: @text; }
    QDialog#gotoDialog QLabel { color: @text_muted; font-size: 11px; }
    QDialog#gotoDialog QListWidget {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        outline: none;
    }
    QDialog#gotoDialog QListWidget::item { padding: 3px 6px; }
    QDialog#gotoDialog QListWidget::item:hover { background-color: @surface_hover; }
    QDialog#gotoDialog QListWidget::item:selected { background-color: @accent_strong; color: #ffffff; }
    QLineEdit, QSpinBox {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 3px 6px;
        selection-background-color: @accent_strong;
        selection-color: #ffffff;
    }
    QToolBar QToolButton#editMenuButton { padding: 4px 22px 4px 9px; font-size: 12px; }
    QToolBar QToolButton#compressButton, QToolBar QToolButton#slideshowButton {
        padding: 4px 22px 4px 9px;
        font-size: 12px;
    }
    QToolBar QToolButton#slideshowButton:checked { background-color: @accent_strong; border-color: @accent; color: #ffffff; }
    QToolBar QToolButton:disabled { color: @text_disabled; background-color: @surface_disabled; border-color: @border_disabled; }
"""
