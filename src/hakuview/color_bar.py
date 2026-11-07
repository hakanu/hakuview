"""Brightness / contrast adjustment bar shown above the image or video."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget


class ColorAdjustBar(QWidget):
    """Two -100..100 sliders plus Reset / Save / Close. Styled by theme.APP_QSS (#colorBar)."""

    changed = Signal(int, int)  # brightness, contrast (user edits and Reset)
    saveRequested = Signal()  # image, or the current video frame, as a new image
    saveVideoRequested = Signal()  # whole video re-encoded with the adjustment
    closeRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("colorBar")
        # QWidget subclasses only paint a stylesheet background with this attribute set.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
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

        self.btn_save_video = QPushButton("🎬 Save as New Video", self)
        self.btn_save_video.setObjectName("btnSaveAdj")
        self.btn_save_video.setToolTip("Re-encode the whole video with this brightness/contrast into <name>_color.mp4")
        self.btn_save_video.clicked.connect(self.saveVideoRequested.emit)
        self.btn_save_video.hide()
        layout.addWidget(self.btn_save_video)

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

    def set_video_mode(self, is_video: bool) -> None:
        self.btn_save.setText("🖼 Save Frame" if is_video else "💾 Save as New Image")
        self.btn_save.setToolTip(
            "Save the current video frame, adjusted, as a new image"
            if is_video
            else "Save the adjusted image as a new file in the current folder (Ctrl+S)"
        )
        self.btn_save_video.setVisible(is_video)

    def set_exporting(self, exporting: bool) -> None:
        self.btn_save_video.setEnabled(not exporting)
        self.btn_save_video.setText("⏳ Exporting video..." if exporting else "🎬 Save as New Video")

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


class PasteOverlayBar(QWidget):
    """Floating paste control bar: Opacity, Corner Radius, Frame/Shadow, Stitch, Stamp & Cancel."""

    styleChanged = Signal(int, int, bool)  # opacity (0..255), rounded_pct (0..50), border (bool)
    stitchRequested = Signal(str)  # "horizontal" | "vertical"
    stampRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("colorBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(8)

        title = QLabel("📌 Floating Paste:", self)
        title.setObjectName("colorTitle")
        layout.addWidget(title)

        layout.addWidget(QLabel("Opacity:", self))
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_opacity.setRange(25, 255)
        self.slider_opacity.setValue(255)
        self.slider_opacity.setFixedWidth(85)
        self.slider_opacity.setToolTip("Adjust overlay opacity (10% to 100%)")
        self.slider_opacity.valueChanged.connect(self._on_changed)
        layout.addWidget(self.slider_opacity)
        self.lbl_opacity = QLabel("100%", self)
        self.lbl_opacity.setObjectName("colorValue")
        self.lbl_opacity.setFixedWidth(36)
        layout.addWidget(self.lbl_opacity)

        layout.addWidget(QLabel("Corners:", self))
        self.slider_rounded = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_rounded.setRange(0, 50)
        self.slider_rounded.setValue(0)
        self.slider_rounded.setFixedWidth(72)
        self.slider_rounded.setToolTip("Round the corners of the floating image (0% to 50%)")
        self.slider_rounded.valueChanged.connect(self._on_changed)
        layout.addWidget(self.slider_rounded)
        self.lbl_rounded = QLabel("0%", self)
        self.lbl_rounded.setObjectName("colorValue")
        self.lbl_rounded.setFixedWidth(30)
        layout.addWidget(self.lbl_rounded)

        self.chk_border = QCheckBox("Frame && Shadow", self)
        self.chk_border.setToolTip("Draw a white photo border and drop shadow around the pasted image")
        self.chk_border.toggled.connect(self._on_changed)
        layout.addWidget(self.chk_border)

        self.btn_stitch_h = QPushButton("◫ Side-by-Side", self)
        self.btn_stitch_h.setToolTip("Stitch pasted image to the right of the current image")
        self.btn_stitch_h.clicked.connect(lambda: self.stitchRequested.emit("horizontal"))
        layout.addWidget(self.btn_stitch_h)

        self.btn_stitch_v = QPushButton("⊟ Stack Below", self)
        self.btn_stitch_v.setToolTip("Stack pasted image directly below the current image")
        self.btn_stitch_v.clicked.connect(lambda: self.stitchRequested.emit("vertical"))
        layout.addWidget(self.btn_stitch_v)

        self.btn_stamp = QPushButton("✓ Stamp (Enter)", self)
        self.btn_stamp.setObjectName("btnSaveAdj")
        self.btn_stamp.setToolTip("Bake floating overlay onto the image (Enter) — undoable with Ctrl+Z")
        self.btn_stamp.clicked.connect(self.stampRequested.emit)
        layout.addWidget(self.btn_stamp)

        layout.addStretch(1)

        self.btn_cancel = QPushButton("✕", self)
        self.btn_cancel.setToolTip("Cancel floating paste (Esc)")
        self.btn_cancel.clicked.connect(self.cancelRequested.emit)
        layout.addWidget(self.btn_cancel)

        self.hide()

    @property
    def opacity(self) -> int:
        return self.slider_opacity.value()

    @property
    def rounded_pct(self) -> int:
        return self.slider_rounded.value()

    @property
    def border(self) -> bool:
        return self.chk_border.isChecked()

    def reset(self) -> None:
        for w, val in ((self.slider_opacity, 255), (self.slider_rounded, 0)):
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)
        self.chk_border.blockSignals(True)
        self.chk_border.setChecked(False)
        self.chk_border.blockSignals(False)
        self._update_labels()

    def _on_changed(self) -> None:
        self._update_labels()
        self.styleChanged.emit(self.opacity, self.rounded_pct, self.border)

    def _update_labels(self) -> None:
        self.lbl_opacity.setText(f"{round(self.opacity * 100 / 255)}%")
        self.lbl_rounded.setText(f"{self.rounded_pct}%")
