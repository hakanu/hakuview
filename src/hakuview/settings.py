"""Persisted user preferences (QSettings: HakuView/HakuView)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QByteArray, QSettings

from .folder_scanner import SORT_MODES
from .image_ops import COMPRESSION_MODES
from .theme import THEME_MODES

WHEEL_ACTIONS = ("zoom", "navigate")


def _setting(key: str, default, kind, choices=None):
    """A typed property backed by QSettings; invalid stored values fall back to the default."""

    def getter(self):
        value = self._s.value(key, default, type=kind)
        return value if choices is None or value in choices else default

    def setter(self, value) -> None:
        self._s.setValue(key, value)

    return property(getter, setter)


class AppSettings:
    def __init__(self, settings: Optional[QSettings] = None) -> None:
        self._s = settings or QSettings("HakuView", "HakuView")

    theme = _setting("appearance/theme", "dark", str, THEME_MODES)  # dark = HakuView's classic look
    wheel_action = _setting("view/wheel_action", "zoom", str, WHEEL_ACTIONS)
    sort_mode = _setting("view/sort_mode", "name", str, tuple(SORT_MODES))
    compression_mode = _setting("edit/compression_mode", COMPRESSION_MODES[0][1], str, tuple(m for _, m in COMPRESSION_MODES))
    slideshow_interval = _setting("slideshow/interval", 3.0, float)
    slideshow_shuffle = _setting("slideshow/shuffle", False, bool)
    video_loop = _setting("video/loop", True, bool)
    video_volume = _setting("video/volume", 80, int)
    video_muted = _setting("video/muted", False, bool)
    remember_session = _setting("session/remember", True, bool)
    last_folder = _setting("session/last_folder", "", str)

    @property
    def window_geometry(self) -> Optional[QByteArray]:
        value = self._s.value("session/geometry")
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        self._s.setValue("session/geometry", value)

    def sync(self) -> None:
        self._s.sync()
