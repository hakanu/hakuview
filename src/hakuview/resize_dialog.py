"""Interactive Image Resize, Resample & CapCut-Style Reframe Dialog (Ctrl+R)."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .image_ops import resize_qimage
from .video_view import extract_dominant_color


IMAGE_RESIZE_PRESETS: list[tuple[str, str, int, int, int]] = [
    # (label, id, pct, w, h)
    ("Custom Dimensions / Percentage", "custom", 0, 0, 0),
    ("50% — Half Size (Quick Downscale)", "pct_50", 50, 0, 0),
    ("25% — Quarter Size (Thumbnail)", "pct_25", 25, 0, 0),
    ("75% — Three-Quarter Size", "pct_75", 75, 0, 0),
    ("150% — 1.5× Upscale", "pct_150", 150, 0, 0),
    ("200% — 2× Upscale (Pixel / Crisp)", "pct_200", 200, 0, 0),
    ("16:9 Full HD — 1920 × 1080 px", "hd_1080p", 0, 1920, 1080),
    ("16:9 HD — 1280 × 720 px", "hd_720p", 0, 1280, 720),
    ("16:9 4K UHD — 3840 × 2160 px", "uhd_4k", 0, 3840, 2160),
    ("1:1 Square — 1080 × 1080 px", "sq_1080", 0, 1080, 1080),
    ("4:5 Portrait — 1080 × 1350 px (Instagram)", "ig_portrait", 0, 1080, 1350),
    ("9:16 Vertical — 1080 × 1920 px (Story / Reels)", "story_1080", 0, 1080, 1920),
]

IMAGE_RESIZE_MODES: list[tuple[str, str]] = [
    ("Scale / Resample to Exact Dimensions", "scale"),
    ("Fit + Blurred Dominant Backdrop (CapCut Style)", "pad_blur"),
    ("Fit + Solid Color Pad (Letterbox / Pillarbox)", "pad_solid"),
    ("Fill + Center Crop (Cover Target Box)", "crop_fill"),
]

IMAGE_RESAMPLE_FILTERS: list[tuple[str, str]] = [
    ("Smooth Lanczos (Best for Photos)", "lanczos"),
    ("Fast Bilinear (Smooth & Quick)", "bilinear"),
    ("Crisp Nearest Neighbor (Pixel Art / Screenshots)", "nearest"),
]


class ImageResizeDialog(QDialog):
    """IrfanView Ctrl+R + CapCut Canvas Reframe dialog for images with live preview."""

    def __init__(self, frame: QImage, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("📐 Resize / Resample Image")
        self.setMinimumWidth(520)

        self._frame = frame
        self._orig_w = max(1, frame.width() if not frame.isNull() else 1920)
        self._orig_h = max(1, frame.height() if not frame.isNull() else 1080)
        self._aspect = self._orig_w / float(self._orig_h)
        self._dominant_color: QColor = extract_dominant_color(frame)
        self._save_as_copy: bool = False
        self._updating: bool = False

        # Small source copy for fast live preview rendering
        self._preview_src = (
            frame.scaled(420, 420, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            if not frame.isNull()
            else QImage(320, 240, QImage.Format.Format_RGB32)
        )

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # 1. Live Preview Box
        preview_group = QGroupBox("Live Preview", self)
        pv_layout = QVBoxLayout(preview_group)
        self.lbl_preview = QLabel(self)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(440, 210)
        self.lbl_preview.setStyleSheet(
            "background-color: #0b0f19; border: 1px solid rgba(148, 163, 184, 0.25); border-radius: 6px;"
        )
        pv_layout.addWidget(self.lbl_preview)

        self.lbl_summary = QLabel(self)
        self.lbl_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_summary.setStyleSheet("color: #94a3b8; font-size: 12px;")
        pv_layout.addWidget(self.lbl_summary)
        root.addWidget(preview_group)

        # 2. Dimensions & Percentage
        dim_group = QGroupBox("Target Dimensions & Percentage", self)
        dim_form = QFormLayout(dim_group)

        self.combo_preset = QComboBox(self)
        for label, preset_id, *_ in IMAGE_RESIZE_PRESETS:
            self.combo_preset.addItem(label, preset_id)
        dim_form.addRow("Preset:", self.combo_preset)

        px_row = QHBoxLayout()
        self.spin_width = QSpinBox(self)
        self.spin_width.setRange(1, 16384)
        self.spin_width.setValue(self._orig_w)
        self.spin_width.setSuffix(" px")

        self.spin_height = QSpinBox(self)
        self.spin_height.setRange(1, 16384)
        self.spin_height.setValue(self._orig_h)
        self.spin_height.setSuffix(" px")

        self.btn_swap = QPushButton("⇄ Swap W/H", self)
        self.btn_swap.setToolTip("Swap width and height")
        self.btn_swap.clicked.connect(self.swap_dimensions)

        px_row.addWidget(QLabel("W:", self))
        px_row.addWidget(self.spin_width, stretch=1)
        px_row.addWidget(QLabel("×  H:", self))
        px_row.addWidget(self.spin_height, stretch=1)
        px_row.addWidget(self.btn_swap)
        dim_form.addRow("Pixels:", px_row)

        pct_row = QHBoxLayout()
        self.spin_pct_w = QSpinBox(self)
        self.spin_pct_w.setRange(1, 800)
        self.spin_pct_w.setValue(100)
        self.spin_pct_w.setSuffix(" %")

        self.spin_pct_h = QSpinBox(self)
        self.spin_pct_h.setRange(1, 800)
        self.spin_pct_h.setValue(100)
        self.spin_pct_h.setSuffix(" %")

        self.chk_aspect = QCheckBox("🔒 Lock aspect ratio", self)
        self.chk_aspect.setChecked(True)

        pct_row.addWidget(QLabel("W%:", self))
        pct_row.addWidget(self.spin_pct_w, stretch=1)
        pct_row.addWidget(QLabel("H%:", self))
        pct_row.addWidget(self.spin_pct_h, stretch=1)
        pct_row.addWidget(self.chk_aspect)
        dim_form.addRow("Scale %:", pct_row)

        # Quick percentage buttons row
        quick_row = QHBoxLayout()
        for pct in (25, 50, 75, 100, 150, 200):
            b = QPushButton(f"{pct}%", self)
            b.clicked.connect(lambda _checked=False, p=pct: self.set_percentage(p))
            quick_row.addWidget(b)
        dim_form.addRow("Quick Scale:", quick_row)
        root.addWidget(dim_group)

        # 3. Fit Mode & Resampling Filter
        opt_group = QGroupBox("Resampling & Canvas Reframe", self)
        opt_form = QFormLayout(opt_group)

        self.combo_mode = QComboBox(self)
        for label, mode_id in IMAGE_RESIZE_MODES:
            self.combo_mode.addItem(label, mode_id)
        opt_form.addRow("Fit Mode:", self.combo_mode)

        self.combo_resample = QComboBox(self)
        for label, res_id in IMAGE_RESAMPLE_FILTERS:
            self.combo_resample.addItem(label, res_id)
        opt_form.addRow("Filter:", self.combo_resample)

        color_row = QHBoxLayout()
        self.btn_bg_color = QPushButton(self)
        self.btn_bg_color.setFixedWidth(110)
        self.btn_bg_color.clicked.connect(self._pick_bg_color)
        self.btn_auto_color = QPushButton("✨ Auto Dominant", self)
        self.btn_auto_color.clicked.connect(self._reset_dominant_color)
        color_row.addWidget(self.btn_bg_color)
        color_row.addWidget(self.btn_auto_color)
        color_row.addStretch(1)
        opt_form.addRow("Backdrop Color:", color_row)
        root.addWidget(opt_group)

        # 4. Action Buttons
        btn_row = QHBoxLayout()
        self.btn_save_copy = QPushButton("💾 Save as New Image", self)
        self.btn_save_copy.setToolTip("Save resized image as a new file (<name>_WxH.<ext>) without modifying original")
        self.btn_save_copy.clicked.connect(self._on_save_copy)
        btn_row.addWidget(self.btn_save_copy)
        btn_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("✓ Apply to Image")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        root.addLayout(btn_row)

        # Connections
        self.combo_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.spin_width.valueChanged.connect(self._on_width_edited)
        self.spin_height.valueChanged.connect(self._on_height_edited)
        self.spin_pct_w.valueChanged.connect(self._on_pct_w_edited)
        self.spin_pct_h.valueChanged.connect(self._on_pct_h_edited)
        self.chk_aspect.toggled.connect(self._on_aspect_toggled)
        self.combo_mode.currentIndexChanged.connect(self._refresh_preview)
        self.combo_resample.currentIndexChanged.connect(self._refresh_preview)

        self._update_color_button()
        self._refresh_preview()

    @property
    def target_width(self) -> int:
        return max(1, int(self.spin_width.value()))

    @property
    def target_height(self) -> int:
        return max(1, int(self.spin_height.value()))

    @property
    def mode(self) -> str:
        return str(self.combo_mode.currentData() or "scale")

    @property
    def resample_filter(self) -> str:
        return str(self.combo_resample.currentData() or "lanczos")

    @property
    def bg_color_hex(self) -> str:
        return self._dominant_color.name()

    @property
    def save_as_copy(self) -> bool:
        return self._save_as_copy

    def _on_save_copy(self) -> None:
        self._save_as_copy = True
        self.accept()

    def set_percentage(self, pct: int) -> None:
        self._updating = True
        try:
            nw = max(1, int(round(self._orig_w * (pct / 100.0))))
            nh = max(1, int(round(self._orig_h * (pct / 100.0))))
            self.spin_width.setValue(nw)
            self.spin_height.setValue(nh)
            self.spin_pct_w.setValue(pct)
            self.spin_pct_h.setValue(pct)
        finally:
            self._updating = False
        self._refresh_preview()

    def swap_dimensions(self) -> None:
        self._updating = True
        try:
            w, h = self.spin_width.value(), self.spin_height.value()
            self.chk_aspect.setChecked(False)
            self.spin_width.setValue(h)
            self.spin_height.setValue(w)
            self.spin_pct_w.setValue(max(1, min(800, int(round(h * 100.0 / self._orig_w)))))
            self.spin_pct_h.setValue(max(1, min(800, int(round(w * 100.0 / self._orig_h)))))
            # If aspect ratio changed significantly from original, default to Blurred Backdrop reframe
            if self.mode == "scale" and abs((h / max(1.0, float(w))) - self._aspect) > 0.25:
                idx = self.combo_mode.findData("pad_blur")
                if idx >= 0:
                    self.combo_mode.setCurrentIndex(idx)
        finally:
            self._updating = False
        self._refresh_preview()

    def _on_preset_changed(self, index: int) -> None:
        if index < 0 or index >= len(IMAGE_RESIZE_PRESETS):
            return
        _label, preset_id, pct, pw, ph = IMAGE_RESIZE_PRESETS[index]
        if preset_id == "custom":
            return
        if pct > 0:
            self.chk_aspect.setChecked(True)
            idx_scale = self.combo_mode.findData("scale")
            if idx_scale >= 0:
                self.combo_mode.setCurrentIndex(idx_scale)
            self.set_percentage(pct)
        elif pw > 0 and ph > 0:
            self._updating = True
            try:
                self.chk_aspect.setChecked(False)
                self.spin_width.setValue(pw)
                self.spin_height.setValue(ph)
                self.spin_pct_w.setValue(max(1, min(800, int(round(pw * 100.0 / self._orig_w)))))
                self.spin_pct_h.setValue(max(1, min(800, int(round(ph * 100.0 / self._orig_h)))))
                # If preset aspect ratio differs from original, switch to CapCut Blurred Backdrop automatically
                if abs((pw / float(ph)) - self._aspect) > 0.12:
                    idx_blur = self.combo_mode.findData("pad_blur")
                    if idx_blur >= 0:
                        self.combo_mode.setCurrentIndex(idx_blur)
            finally:
                self._updating = False
            self._refresh_preview()

    def _on_width_edited(self, w: int) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            self.spin_pct_w.setValue(max(1, min(800, int(round(w * 100.0 / self._orig_w)))))
            if self.chk_aspect.isChecked():
                nh = max(1, int(round(w / self._aspect)))
                self.spin_height.setValue(nh)
                self.spin_pct_h.setValue(max(1, min(800, int(round(nh * 100.0 / self._orig_h)))))
        finally:
            self._updating = False
        self._refresh_preview()

    def _on_height_edited(self, h: int) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            self.spin_pct_h.setValue(max(1, min(800, int(round(h * 100.0 / self._orig_h)))))
            if self.chk_aspect.isChecked():
                nw = max(1, int(round(h * self._aspect)))
                self.spin_width.setValue(nw)
                self.spin_pct_w.setValue(max(1, min(800, int(round(nw * 100.0 / self._orig_w)))))
        finally:
            self._updating = False
        self._refresh_preview()

    def _on_pct_w_edited(self, pct_w: int) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            nw = max(1, int(round(self._orig_w * (pct_w / 100.0))))
            self.spin_width.setValue(nw)
            if self.chk_aspect.isChecked():
                self.spin_pct_h.setValue(pct_w)
                nh = max(1, int(round(self._orig_h * (pct_w / 100.0))))
                self.spin_height.setValue(nh)
        finally:
            self._updating = False
        self._refresh_preview()

    def _on_pct_h_edited(self, pct_h: int) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            nh = max(1, int(round(self._orig_h * (pct_h / 100.0))))
            self.spin_height.setValue(nh)
            if self.chk_aspect.isChecked():
                self.spin_pct_w.setValue(pct_h)
                nw = max(1, int(round(self._orig_w * (pct_h / 100.0))))
                self.spin_width.setValue(nw)
        finally:
            self._updating = False
        self._refresh_preview()

    def _on_aspect_toggled(self, checked: bool) -> None:
        if checked and not self._updating:
            self._on_width_edited(self.spin_width.value())

    def _pick_bg_color(self) -> None:
        color = QColorDialog.getColor(self._dominant_color, self, "Pick Backdrop Color")
        if color.isValid():
            self._dominant_color = color
            self._update_color_button()
            self._refresh_preview()

    def _reset_dominant_color(self) -> None:
        self._dominant_color = extract_dominant_color(self._frame)
        self._update_color_button()
        self._refresh_preview()

    def _update_color_button(self) -> None:
        c = self._dominant_color
        luma = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
        fg = "#0f172a" if luma > 145 else "#f8fafc"
        self.btn_bg_color.setText(c.name().upper())
        self.btn_bg_color.setStyleSheet(
            f"background-color: {c.name()}; color: {fg}; font-weight: 600; border-radius: 4px; padding: 4px 8px;"
        )

    def _refresh_preview(self) -> None:
        tw, th = self.target_width, self.target_height
        # Render preview at bounded resolution so the UI stays 60fps
        max_pv = 380.0
        scale = min(max_pv / max(1, tw), 195.0 / max(1, th))
        pw = max(16, int(round(tw * scale)))
        ph = max(16, int(round(th * scale)))

        rendered = resize_qimage(
            self._preview_src,
            pw,
            ph,
            mode=self.mode,
            resample=self.resample_filter,
            bg_color=self.bg_color_hex,
            blur_sigma=20,
        )

        # Draw subtle border around preview
        canvas = QImage(pw + 4, ph + 4, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawImage(2, 2, rendered)
        painter.setPen(QPen(QColor(56, 189, 248, 170), 1.2))
        painter.drawRect(QRectF(1.5, 1.5, pw + 1, ph + 1))
        painter.end()

        self.lbl_preview.setPixmap(QPixmap.fromImage(canvas))
        pct_w = round(tw * 100.0 / self._orig_w)
        pct_h = round(th * 100.0 / self._orig_h)
        self.lbl_summary.setText(
            f"{self._orig_w} × {self._orig_h} px  →  {tw} × {th} px  ({pct_w}% × {pct_h}%)"
        )
