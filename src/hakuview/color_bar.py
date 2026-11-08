"""Brightness / contrast adjustment bar shown above the image or video."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QButtonGroup, QCheckBox, QColorDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QWidget


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


DRAW_TOOLS: list[tuple[str, str, str]] = [
    ("✒ Pen", "pen", "Freehand pen stroke"),
    ("🖍 Marker", "highlighter", "Semi-transparent neon highlighter"),
    ("↗ Arrow", "arrow", "Directional pointer arrow"),
    ("▭ Box", "rect", "Rounded rectangle callout"),
    ("◯ Circle", "ellipse", "Circle / ellipse callout"),
    ("╱ Line", "line", "Straight line segment"),
]

DRAW_SWATCHES: list[tuple[str, str]] = [
    ("Red", "#ef4444"),
    ("Yellow", "#facc15"),
    ("Green", "#22c55e"),
    ("Sky Blue", "#38bdf8"),
    ("White", "#ffffff"),
    ("Dark", "#0f172a"),
]


class DrawAnnotateBar(QWidget):
    """Quick Draw & Annotate toolbar shown above the canvas when drawing mode (D) is active."""

    styleChanged = Signal(str, str, int)  # tool, color_hex, size
    undoStrokeRequested = Signal()
    applyRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("colorBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._tool: str = "arrow"
        self._color: str = "#ef4444"

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)

        title = QLabel("✏️ Draw:", self)
        title.setObjectName("colorTitle")
        layout.addWidget(title)

        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_buttons: dict[str, QPushButton] = {}
        for label, tool_id, tip in DRAW_TOOLS:
            btn = QPushButton(label, self)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            if tool_id == self._tool:
                btn.setChecked(True)
            btn.clicked.connect(lambda _checked=False, tid=tool_id: self.set_tool(tid))
            self.tool_group.addButton(btn)
            self.tool_buttons[tool_id] = btn
            layout.addWidget(btn)

        # Color swatches
        self.swatch_buttons: dict[str, QPushButton] = {}
        for name, hex_code in DRAW_SWATCHES:
            sbtn = QPushButton("", self)
            sbtn.setFixedSize(22, 22)
            sbtn.setToolTip(f"Color: {name} ({hex_code})")
            sbtn.clicked.connect(lambda _checked=False, hc=hex_code: self.set_color(hc))
            self.swatch_buttons[hex_code.lower()] = sbtn
            layout.addWidget(sbtn)

        self.btn_custom_color = QPushButton("🎨", self)
        self.btn_custom_color.setFixedWidth(30)
        self.btn_custom_color.setToolTip("Pick custom annotation color...")
        self.btn_custom_color.clicked.connect(self._pick_custom_color)
        layout.addWidget(self.btn_custom_color)

        layout.addWidget(QLabel("Size:", self))
        self.slider_size = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_size.setRange(2, 36)
        self.slider_size.setValue(6)
        self.slider_size.setFixedWidth(72)
        self.slider_size.setToolTip("Stroke thickness (2px to 36px)")
        self.slider_size.valueChanged.connect(self._on_size_changed)
        layout.addWidget(self.slider_size)
        self.lbl_size = QLabel("6px", self)
        self.lbl_size.setObjectName("colorValue")
        self.lbl_size.setFixedWidth(32)
        layout.addWidget(self.lbl_size)

        self.btn_undo_stroke = QPushButton("↶ Undo", self)
        self.btn_undo_stroke.setToolTip("Undo last drawn stroke")
        self.btn_undo_stroke.clicked.connect(self.undoStrokeRequested.emit)
        layout.addWidget(self.btn_undo_stroke)

        self.btn_apply = QPushButton("✓ Apply (Enter)", self)
        self.btn_apply.setObjectName("btnSaveAdj")
        self.btn_apply.setToolTip("Bake annotations onto image (Enter) — undoable with Ctrl+Z")
        self.btn_apply.clicked.connect(self.applyRequested.emit)
        layout.addWidget(self.btn_apply)

        layout.addStretch(1)

        self.btn_cancel = QPushButton("✕", self)
        self.btn_cancel.setToolTip("Discard unapplied strokes and exit Draw mode (Esc)")
        self.btn_cancel.clicked.connect(self.cancelRequested.emit)
        layout.addWidget(self.btn_cancel)

        self._refresh_swatch_styles()
        self.hide()

    @property
    def tool(self) -> str:
        return self._tool

    @property
    def color_hex(self) -> str:
        return self._color

    @property
    def size_px(self) -> int:
        return int(self.slider_size.value())

    def set_tool(self, tool_id: str) -> None:
        if tool_id not in self.tool_buttons:
            return
        self._tool = tool_id
        self.tool_buttons[tool_id].setChecked(True)
        self.styleChanged.emit(self._tool, self._color, self.size_px)

    def set_color(self, hex_code: str) -> None:
        c = QColor(hex_code)
        if not c.isValid():
            return
        self._color = c.name().lower()
        self._refresh_swatch_styles()
        self.styleChanged.emit(self._tool, self._color, self.size_px)

    def _pick_custom_color(self) -> None:
        c = QColorDialog.getColor(QColor(self._color), self, "Pick Annotation Color")
        if c.isValid():
            self.set_color(c.name())

    def _on_size_changed(self, val: int) -> None:
        self.lbl_size.setText(f"{val}px")
        self.styleChanged.emit(self._tool, self._color, self.size_px)

    def _refresh_swatch_styles(self) -> None:
        for hc, btn in self.swatch_buttons.items():
            selected = hc == self._color.lower()
            ring = "2px solid #38bdf8" if selected else "1px solid rgba(148,163,184,0.45)"
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {hc}; border: {ring}; border-radius: 11px; min-width: 18px; max-width: 22px; min-height: 18px; max-height: 22px; padding: 0px; }}"
            )

