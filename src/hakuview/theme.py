"""HakuView's dark theme: one color palette and the Qt stylesheet built from it.

The main window applies APP_QSS to itself, so every child widget (toolbar, color bar,
fullscreen controls, video transport bar, AV1 banner, menus and popups) is styled from
here. Widget-specific rules are scoped by objectName instead of being set inline.
"""

from __future__ import annotations

import re

PALETTE = {
    "window": "#14161a",
    "panel": "#1a1d25",  # toolbars, menus, popups, color bar
    "statusbar": "#181b21",
    "video_bar": "#161920",
    "surface": "#252a34",  # buttons, inputs
    "surface_hover": "#323946",
    "surface_disabled": "#1e222a",
    "border": "#353c4b",
    "border_hover": "#4b5563",
    "border_subtle": "#2b303c",
    "border_disabled": "#262b35",
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
}


def _qss(template: str) -> str:
    """Substitute @name palette tokens (QSS uses braces, so str.format doesn't fit)."""
    return re.sub(r"@([a-z_]+)", lambda m: PALETTE[m.group(1)], template)


APP_QSS = _qss(
    """
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
    QCheckBox { color: @text_soft; font-size: 12px; spacing: 4px; }
    QComboBox {
        background-color: @surface;
        color: @text;
        border: 1px solid @border;
        border-radius: 4px;
        padding: 2px 22px 2px 8px;
        font-size: 12px;
        min-height: 22px;
    }
    QComboBox:hover { background-color: #2e3442; border-color: @border_hover; }
    QComboBox:on { border-color: @accent_strong; }
    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid @border;
        border-top-right-radius: 4px;
        border-bottom-right-radius: 4px;
    }
    QComboBox::down-arrow {
        width: 0;
        height: 0;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid @text_muted;
        margin-right: 2px;
    }
    QComboBox::down-arrow:hover { border-top: 5px solid @accent; }
    QComboBox QAbstractItemView {
        background-color: @panel;
        color: @text;
        border: 1px solid #334155;
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

    /* ---- Menus ---- */
    QMenu {
        background-color: @panel;
        color: @text;
        border: 1px solid #334155;
        border-radius: 6px;
        padding: 4px 0;
        font-size: 12px;
    }
    QMenu::item { padding: 6px 24px 6px 14px; background-color: transparent; }
    QMenu::item:selected { background-color: @accent_strong; color: #ffffff; }
    QMenu::item:disabled { color: @text_disabled; }
    QMenu::separator { height: 1px; background-color: #2b3240; margin: 4px 8px; }

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
        background-color: rgba(20, 23, 30, 0.90);
        border: 1px solid #3b4252;
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
    #colorBar QSlider::groove:horizontal { height: 4px; background: #2b3240; border-radius: 2px; }
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
        background-color: #262b36;
        border: 1px solid #374151;
        border-radius: 4px;
        padding: 4px 10px;
        color: @text_bright;
        font-size: 12px;
    }
    #videoControls QPushButton:hover { background-color: #334155; }
    #videoControls QPushButton:disabled {
        background-color: @surface_disabled;
        color: @text_disabled;
        border-color: #282f3a;
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
    #av1Banner QPushButton:disabled { background-color: @border_hover; color: #9ca3af; }
    """
)
