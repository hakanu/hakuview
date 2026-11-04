"""Brightness / contrast adjustment bar shown above the image or video."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QWidget


class ColorAdjustBar(QWidget):
    """Two -100..100 sliders plus Reset / Save / Close. Styled by theme.APP_QSS (#colorBar)."""

    changed = Signal(int, int)  # brightness, contrast (user edits and Reset)
    saveRequested = Signal()
    closeRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("colorBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(8)

        title = QLabel("🎨 Color Scaling:", self)
        title.setObjectName("colorTitle")
        layout.addWidget(title)

        self.slider_brightness, self.lbl_b_val = self._add_slider(layout, "☀️ Brightness:", "Adjust Brightness (-100 to +100)")
        self.slider_contrast, self.lbl_c_val = self._add_slider(layout, "◐ Contrast:", "Adjust Contrast (-100 to +100)")

        self.btn_reset = QPushButton("↺ Reset", self)
        self.btn_reset.setToolTip("Reset brightness & contrast back to 0")
        self.btn_reset.clicked.connect(self._on_reset_clicked)
        layout.addWidget(self.btn_reset)

        self.btn_save = QPushButton("💾 Save as New Image", self)
        self.btn_save.setObjectName("btnSaveAdj")
        self.btn_save.setToolTip("Save adjusted image or video frame as a new image file in the current folder (Ctrl+S)")
        self.btn_save.clicked.connect(self.saveRequested.emit)
        layout.addWidget(self.btn_save)

        layout.addStretch(1)

        self.btn_close = QPushButton("✕", self)
        self.btn_close.setToolTip("Close Color panel (B)")
        self.btn_close.clicked.connect(self.closeRequested.emit)
        layout.addWidget(self.btn_close)

        self.hide()

    def _add_slider(self, layout: QHBoxLayout, label: str, tooltip: str) -> tuple[QSlider, QLabel]:
        layout.addWidget(QLabel(label, self))
        slider = QSlider(Qt.Orientation.Horizontal, self)
        slider.setRange(-100, 100)
        slider.setFixedWidth(100)
        slider.setToolTip(tooltip)
        slider.valueChanged.connect(self._on_slider_changed)
        layout.addWidget(slider)
        value_label = QLabel("0", self)
        value_label.setObjectName("colorValue")
        value_label.setFixedWidth(28)
        layout.addWidget(value_label)
        return slider, value_label

    @property
    def brightness(self) -> int:
        return self.slider_brightness.value()

    @property
    def contrast(self) -> int:
        return self.slider_contrast.value()

    @property
    def is_neutral(self) -> bool:
        return self.brightness == 0 and self.contrast == 0

    def reset(self) -> None:
        """Return both sliders to 0 without emitting `changed`."""
        for slider in (self.slider_brightness, self.slider_contrast):
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
        self._update_labels()

    def _on_reset_clicked(self) -> None:
        self.reset()
        self.changed.emit(0, 0)

    def _on_slider_changed(self) -> None:
        self._update_labels()
        self.changed.emit(self.brightness, self.contrast)

    def _update_labels(self) -> None:
        for slider, label in ((self.slider_brightness, self.lbl_b_val), (self.slider_contrast, self.lbl_c_val)):
            value = slider.value()
            label.setText(f"{value:+d}" if value else "0")
