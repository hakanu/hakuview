"""Settings dialog: theme, browsing, image, slideshow, video, update and session preferences."""

from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .folder_scanner import SORT_MODES
from .image_ops import COMPRESSION_MODES
from .settings import AppSettings
from .updater import UpdateSettings


class SettingsDialog(QDialog):
    """Edits a copy of the preferences; nothing is saved unless the user presses OK."""

    def __init__(self, app_settings: AppSettings, update_settings: UpdateSettings, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")  # styled by theme.app_qss()
        self.setWindowTitle("Settings — HakuView")
        self.setMinimumWidth(420)
        self._app = app_settings
        self._updates = update_settings
        root = QVBoxLayout(self)

        # Appearance
        self.theme_buttons = QButtonGroup(self)
        theme_row = QHBoxLayout()
        for mode, label in (("system", "Follow system"), ("dark", "Dark"), ("light", "Light")):
            btn = QRadioButton(label, self)
            btn.setProperty("mode", mode)
            btn.setChecked(app_settings.theme == mode)
            self.theme_buttons.addButton(btn)
            theme_row.addWidget(btn)
        theme_row.addStretch(1)
        root.addWidget(self._group("Appearance", [("Theme", theme_row)]))

        # Browsing
        self.combo_sort = self._combo(SORT_MODES.items(), app_settings.sort_mode)
        self.combo_wheel = self._combo(
            (("zoom", "Zoom (Ctrl+wheel also zooms)"), ("navigate", "Previous / next file (Ctrl+wheel zooms)")),
            app_settings.wheel_action,
        )
        root.addWidget(self._group("Browsing", [("Sort files by", self.combo_sort), ("Mouse wheel", self.combo_wheel)]))

        # Images & slideshow
        self.combo_compress = self._combo(((m, label) for label, m in COMPRESSION_MODES), app_settings.compression_mode)
        self.spin_interval = QDoubleSpinBox(self)
        self.spin_interval.setRange(0.5, 120.0)
        self.spin_interval.setSingleStep(0.5)
        self.spin_interval.setDecimals(1)
        self.spin_interval.setSuffix(" s")
        self.spin_interval.setValue(app_settings.slideshow_interval)
        self.chk_shuffle = QCheckBox("Shuffle slideshow order", self)
        self.chk_shuffle.setChecked(app_settings.slideshow_shuffle)
        root.addWidget(
            self._group(
                "Images && slideshow",
                [("Compress preset", self.combo_compress), ("Slideshow interval", self.spin_interval), ("", self.chk_shuffle)],
            )
        )

        # Video
        self.chk_loop = QCheckBox("Loop videos (outside slideshows)", self)
        self.chk_loop.setChecked(app_settings.video_loop)
        root.addWidget(self._group("Video", [("", self.chk_loop)]))

        # Startup
        self.chk_updates = QCheckBox("Check for updates on startup", self)
        self.chk_updates.setChecked(update_settings.check_on_startup)
        self.chk_session = QCheckBox("Remember window size, position and recent folders", self)
        self.chk_session.setChecked(app_settings.remember_session)
        root.addWidget(self._group("Startup", [("", self.chk_updates), ("", self.chk_session)]))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _combo(self, items, current: str) -> QComboBox:
        combo = QComboBox(self)
        for value, label in items:
            combo.addItem(label, value)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
        return combo

    def _group(self, title: str, rows) -> QGroupBox:
        box = QGroupBox(title, self)
        form = QFormLayout(box)
        for label, field in rows:
            if isinstance(field, QHBoxLayout):
                form.addRow(label, field)
            elif label:
                form.addRow(label, field)
            else:
                form.addRow(field)
        return box

    @property
    def selected_theme(self) -> str:
        checked = self.theme_buttons.checkedButton()
        return checked.property("mode") if checked else "system"

    def accept(self) -> None:
        self._app.theme = self.selected_theme
        self._app.sort_mode = self.combo_sort.currentData()
        self._app.wheel_action = self.combo_wheel.currentData()
        self._app.compression_mode = self.combo_compress.currentData()
        self._app.slideshow_interval = self.spin_interval.value()
        self._app.slideshow_shuffle = self.chk_shuffle.isChecked()
        self._app.video_loop = self.chk_loop.isChecked()
        self._app.remember_session = self.chk_session.isChecked()
        self._updates.check_on_startup = self.chk_updates.isChecked()
        super().accept()
