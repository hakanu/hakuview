"""Interactive Image Resize, Resample & CapCut-Style Reframe Dialog (Ctrl+R)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QRectF, QTimer, Qt
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
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .formatting import human_size
from .image_ops import estimate_encoded_size, resize_qimage
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

IMAGE_OUTPUT_FORMATS: list[tuple[str, str]] = [
    ("Match Original Format", ""),
    ("JPEG (.jpg) — Smallest Photo / Gov Forms", ".jpg"),
    ("WebP (.webp) — Modern High Compression", ".webp"),
    ("PNG (.png) — Lossless", ".png"),
]


class ImageResizeDialog(QDialog):
    """IrfanView Ctrl+R + CapCut Canvas Reframe dialog for images with live preview & target KB size."""

    def __init__(
        self,
        frame: QImage,
        parent: Optional[QWidget] = None,
        *,
        source_path: Optional[Path | str] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📐 Resize / Resample Image & Target File Size")
        self.setMinimumWidth(560)

        self._frame = frame
        self._source_path: Optional[Path] = Path(source_path) if source_path is not None else None
        self._orig_w = max(1, frame.width() if not frame.isNull() else 1920)
        self._orig_h = max(1, frame.height() if not frame.isNull() else 1080)
        self._aspect = self._orig_w / float(self._orig_h)
        self._dominant_color: QColor = extract_dominant_color(frame)
        self._save_as_copy: bool = False
        self._updating: bool = False
        self._quality_override: int = -1
        self._estimated_bytes: int = 0

        if self._source_path is not None and self._source_path.exists():
            try:
                self._orig_bytes = self._source_path.stat().st_size
            except OSError:
                self._orig_bytes = estimate_encoded_size(frame, self._source_path)
        else:
            self._orig_bytes = estimate_encoded_size(frame, self._source_path)

        # Debounce timer for full-resolution byte estimation on large (>1.5MP) images while dragging sliders
        self._size_timer = QTimer(self)
        self._size_timer.setSingleShot(True)
        self._size_timer.setInterval(35)
        self._size_timer.timeout.connect(self._compute_exact_size_now)

        # Small source copy for fast live preview rendering
        self._preview_src = (
            frame.scaled(420, 420, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            if not frame.isNull()
            else QImage(320, 240, QImage.Format.Format_RGB32)
        )

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # 1. Live Preview Box
        preview_group = QGroupBox("Live Preview & Estimated File Size", self)
        pv_layout = QVBoxLayout(preview_group)
        self.lbl_preview = QLabel(self)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(440, 195)
        self.lbl_preview.setStyleSheet(
            "background-color: #0b0f19; border: 1px solid rgba(148, 163, 184, 0.25); border-radius: 6px;"
        )
        pv_layout.addWidget(self.lbl_preview)

        self.lbl_summary = QLabel(self)
        self.lbl_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_summary.setStyleSheet("color: #e2e8f0; font-size: 12.5px; font-weight: 600;")
        pv_layout.addWidget(self.lbl_summary)
        root.addWidget(preview_group)

        # 2. Dimensions & Percentage + Scale Slider with Live Size
        dim_group = QGroupBox("Target Dimensions & Scale Slider", self)
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

        # Interactive Scale Slider showing live resulting file size right next to the slider
        slider_row = QHBoxLayout()
        self.slider_scale = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_scale.setRange(5, 200)
        self.slider_scale.setValue(100)
        self.slider_scale.setToolTip("Drag to scale image dimensions and see the resulting file size in real time")
        self.lbl_slider_size = QLabel(self)
        self.lbl_slider_size.setMinimumWidth(145)
        self.lbl_slider_size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_slider_size.setStyleSheet(
            "background-color: #0f172a; color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.45); "
            "border-radius: 5px; padding: 3px 8px; font-weight: 700; font-size: 12px;"
        )
        slider_row.addWidget(self.slider_scale, stretch=1)
        slider_row.addWidget(self.lbl_slider_size)
        dim_form.addRow("Size Slider:", slider_row)

        # Quick percentage buttons row
        quick_row = QHBoxLayout()
        for pct in (25, 50, 75, 100, 150, 200):
            b = QPushButton(f"{pct}%", self)
            b.clicked.connect(lambda _checked=False, p=pct: self.set_percentage(p))
            quick_row.addWidget(b)
        dim_form.addRow("Quick Scale:", quick_row)
        root.addWidget(dim_group)

        # 3. Target File Size Limit & Quality (Government Forms 200 KB helper)
        size_group = QGroupBox("Target File Size & Quality (e.g. ≤ 200 KB Form Limit)", self)
        size_form = QFormLayout(size_group)

        qual_row = QHBoxLayout()
        self.slider_quality = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_quality.setRange(10, 100)
        self.slider_quality.setValue(90)
        self.slider_quality.setToolTip("Adjust JPEG / WebP compression quality (lower = smaller file size in KB)")
        self.lbl_quality_val = QLabel("Auto", self)
        self.lbl_quality_val.setMinimumWidth(52)
        self.lbl_quality_val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_quality_val.setStyleSheet("color: #38bdf8; font-weight: 600;")
        self.combo_format = QComboBox(self)
        for label, ext_id in IMAGE_OUTPUT_FORMATS:
            self.combo_format.addItem(label, ext_id)
        qual_row.addWidget(self.slider_quality, stretch=1)
        qual_row.addWidget(self.lbl_quality_val)
        qual_row.addWidget(self.combo_format)
        size_form.addRow("Quality / Fmt:", qual_row)

        fit_row = QHBoxLayout()
        self.spin_target_kb = QSpinBox(self)
        self.spin_target_kb.setRange(10, 50000)
        self.spin_target_kb.setValue(200)
        self.spin_target_kb.setSuffix(" KB")
        self.spin_target_kb.setToolTip("Maximum allowed file size in KB (e.g. 200 KB for government/visa forms)")

        self.btn_fit_kb = QPushButton("🎯 Fit ≤ Target KB", self)
        self.btn_fit_kb.setToolTip("Automatically tune quality & scale so the final file is guaranteed under Target KB")
        self.btn_fit_kb.setStyleSheet("font-weight: 600; color: #34d399;")
        self.btn_fit_kb.clicked.connect(lambda: self.fit_to_target_kb(self.spin_target_kb.value()))

        fit_row.addWidget(self.spin_target_kb)
        fit_row.addWidget(self.btn_fit_kb)
        for kb, label in ((100, "≤ 100 KB"), (200, "≤ 200 KB"), (500, "≤ 500 KB"), (1024, "≤ 1 MB")):
            btn_q = QPushButton(label, self)
            btn_q.setToolTip(f"One-click auto-fit under {label}")
            btn_q.clicked.connect(lambda _checked=False, k=kb: self.fit_to_target_kb(k))
            fit_row.addWidget(btn_q)
        size_form.addRow("Max File Size:", fit_row)
        root.addWidget(size_group)

        # 4. Fit Mode & Resampling Filter
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

        # 5. Action Buttons
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
        self.slider_scale.valueChanged.connect(self._on_scale_slider_changed)
        self.chk_aspect.toggled.connect(self._on_aspect_toggled)
        self.slider_quality.valueChanged.connect(self._on_quality_slider_changed)
        self.combo_format.currentIndexChanged.connect(self._on_format_changed)
        self.spin_target_kb.valueChanged.connect(self._update_size_labels)
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
    def quality(self) -> int:
        return self._quality_override

    @property
    def output_ext(self) -> str:
        chosen = str(self.combo_format.currentData() or "")
        if chosen:
            return chosen
        if self._source_path is not None and self._source_path.suffix:
            return self._source_path.suffix.lower()
        return ".jpg"

    @property
    def estimated_bytes(self) -> int:
        if self._size_timer.isActive():
            self._size_timer.stop()
            self._compute_exact_size_now()
        return self._estimated_bytes

    @property
    def save_as_copy(self) -> bool:
        return self._save_as_copy

    def _on_save_copy(self) -> None:
        self._save_as_copy = True
        self.accept()

    def set_quality(self, quality: int) -> None:
        self._updating = True
        try:
            q = max(10, min(100, int(quality)))
            self._quality_override = q
            self.slider_quality.setValue(q)
            self.lbl_quality_val.setText(f"{q}%")
        finally:
            self._updating = False
        self._refresh_preview()

    def set_percentage(self, pct: int) -> None:
        self._updating = True
        try:
            nw = max(1, int(round(self._orig_w * (pct / 100.0))))
            nh = max(1, int(round(self._orig_h * (pct / 100.0))))
            self.spin_width.setValue(nw)
            self.spin_height.setValue(nh)
            self.spin_pct_w.setValue(pct)
            self.spin_pct_h.setValue(pct)
            self.slider_scale.setValue(max(self.slider_scale.minimum(), min(self.slider_scale.maximum(), pct)))
        finally:
            self._updating = False
        self._refresh_preview()

    def fit_to_target_kb(self, target_kb: int) -> int:
        """Automatically adjust quality and/or scale % so the encoded image fits under `target_kb` KB."""
        target_kb = max(5, int(target_kb))
        self._updating = True
        try:
            self.spin_target_kb.setValue(target_kb)
        finally:
            self._updating = False

        target_bytes = target_kb * 1024
        ext = self.output_ext

        # If format is PNG (lossless) and current image exceeds target_bytes significantly, switch to JPEG
        if ext in (".png", ".bmp", ".tiff", ".tif"):
            cur_est = self._measure_encoded_bytes(self.target_width, self.target_height, ext=ext, quality=-1)
            if cur_est > target_bytes:
                idx_jpg = self.combo_format.findData(".jpg")
                if idx_jpg >= 0:
                    self._updating = True
                    try:
                        self.combo_format.setCurrentIndex(idx_jpg)
                    finally:
                        self._updating = False
                    ext = ".jpg"

        tw, th = self.target_width, self.target_height
        # 1. Try keeping the current dimensions (tw, th) and tuning JPEG/WebP quality (45..95)
        if ext in (".jpg", ".jpeg", ".webp"):
            min_q_bytes = self._measure_encoded_bytes(tw, th, ext=ext, quality=45)
            if min_q_bytes <= target_bytes:
                lo, hi, best_q = 45, 95, 45
                while lo <= hi:
                    mid = (lo + hi) // 2
                    b = self._measure_encoded_bytes(tw, th, ext=ext, quality=mid)
                    if b <= target_bytes:
                        best_q = mid
                        lo = mid + 1
                    else:
                        hi = mid - 1
                self.set_quality(best_q)
                self._compute_exact_size_now()
                return self._estimated_bytes

        # 2. Current dimensions even at Q45 are still > target_bytes -> binary-search scale percentage at Q80
        work_q = 80 if ext in (".jpg", ".jpeg", ".webp") else -1
        cur_pct = max(5, min(200, int(round(tw * 100.0 / self._orig_w))))
        lo_pct, hi_pct, best_pct = 5, cur_pct, 5
        while lo_pct <= hi_pct:
            mid_pct = (lo_pct + hi_pct) // 2
            mw = max(1, int(round(self._orig_w * (mid_pct / 100.0))))
            mh = max(1, int(round(self._orig_h * (mid_pct / 100.0))))
            b = self._measure_encoded_bytes(mw, mh, ext=ext, quality=work_q)
            if b <= target_bytes:
                best_pct = mid_pct
                lo_pct = mid_pct + 1
            else:
                hi_pct = mid_pct - 1

        if work_q > 0:
            self._updating = True
            try:
                self._quality_override = work_q
                self.slider_quality.setValue(work_q)
                self.lbl_quality_val.setText(f"{work_q}%")
            finally:
                self._updating = False

        self.set_percentage(best_pct)

        # 3. If even at 5% scale it's still above target_bytes, lower quality further (10..80)
        if self._estimated_bytes > target_bytes and ext in (".jpg", ".jpeg", ".webp"):
            fw, fh = self.target_width, self.target_height
            lo_q, hi_q, final_q = 10, 79, 10
            while lo_q <= hi_q:
                mid_q = (lo_q + hi_q) // 2
                if self._measure_encoded_bytes(fw, fh, ext=ext, quality=mid_q) <= target_bytes:
                    final_q = mid_q
                    lo_q = mid_q + 1
                else:
                    hi_q = mid_q - 1
            self.set_quality(final_q)

        self._compute_exact_size_now()
        return self._estimated_bytes

    def _measure_encoded_bytes(self, w: int, h: int, *, ext: str, quality: int) -> int:
        if self._frame is None or self._frame.isNull():
            return 0
        if self.mode == "scale" and w == self._orig_w and h == self._orig_h:
            candidate = self._frame
        else:
            candidate = resize_qimage(
                self._frame,
                w,
                h,
                mode=self.mode,
                resample=self.resample_filter,
                bg_color=self.bg_color_hex,
                blur_sigma=24,
            )
        return estimate_encoded_size(candidate, self._source_path, ext=ext, quality=quality)

    def swap_dimensions(self) -> None:
        self._updating = True
        try:
            w, h = self.spin_width.value(), self.spin_height.value()
            self.chk_aspect.setChecked(False)
            self.spin_width.setValue(h)
            self.spin_height.setValue(w)
            pct_w = max(1, min(800, int(round(h * 100.0 / self._orig_w))))
            pct_h = max(1, min(800, int(round(w * 100.0 / self._orig_h))))
            self.spin_pct_w.setValue(pct_w)
            self.spin_pct_h.setValue(pct_h)
            self.slider_scale.setValue(max(self.slider_scale.minimum(), min(self.slider_scale.maximum(), pct_w)))
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
                pct_w = max(1, min(800, int(round(pw * 100.0 / self._orig_w))))
                pct_h = max(1, min(800, int(round(ph * 100.0 / self._orig_h))))
                self.spin_pct_w.setValue(pct_w)
                self.spin_pct_h.setValue(pct_h)
                self.slider_scale.setValue(max(self.slider_scale.minimum(), min(self.slider_scale.maximum(), pct_w)))
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
            pct_w = max(1, min(800, int(round(w * 100.0 / self._orig_w))))
            self.spin_pct_w.setValue(pct_w)
            self.slider_scale.setValue(max(self.slider_scale.minimum(), min(self.slider_scale.maximum(), pct_w)))
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
            pct_h = max(1, min(800, int(round(h * 100.0 / self._orig_h))))
            self.spin_pct_h.setValue(pct_h)
            if self.chk_aspect.isChecked():
                nw = max(1, int(round(h * self._aspect)))
                self.spin_width.setValue(nw)
                pct_w = max(1, min(800, int(round(nw * 100.0 / self._orig_w))))
                self.spin_pct_w.setValue(pct_w)
                self.slider_scale.setValue(max(self.slider_scale.minimum(), min(self.slider_scale.maximum(), pct_w)))
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
            self.slider_scale.setValue(max(self.slider_scale.minimum(), min(self.slider_scale.maximum(), pct_w)))
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
                self.slider_scale.setValue(max(self.slider_scale.minimum(), min(self.slider_scale.maximum(), pct_h)))
        finally:
            self._updating = False
        self._refresh_preview()

    def _on_scale_slider_changed(self, pct: int) -> None:
        if self._updating:
            return
        self.set_percentage(pct)

    def _on_quality_slider_changed(self, q: int) -> None:
        if self._updating:
            return
        self._quality_override = int(q)
        self.lbl_quality_val.setText(f"{q}%")
        self._refresh_preview()

    def _on_format_changed(self, _index: int) -> None:
        if self._updating:
            return
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
        scale = min(max_pv / max(1, tw), 185.0 / max(1, th))
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

        # Compute exact encoded byte size immediately for normal images, or debounce for huge (>1.5MP) frames
        if tw * th <= 1_500_000:
            self._size_timer.stop()
            self._compute_exact_size_now()
        else:
            # Fast proportional approximation while dragging on 4K+ images, followed by exact calculation in 35ms
            area_ratio = (tw * th) / float(max(1, self._orig_w * self._orig_h))
            q_factor = (self._quality_override / 90.0) if self._quality_override > 0 else 1.0
            self._estimated_bytes = max(512, int(self._orig_bytes * (area_ratio ** 0.8) * q_factor))
            self._update_size_labels()
            self._size_timer.start()

    def _compute_exact_size_now(self) -> None:
        self._estimated_bytes = self._measure_encoded_bytes(
            self.target_width,
            self.target_height,
            ext=self.output_ext,
            quality=self._quality_override,
        )
        self._update_size_labels()

    def _update_size_labels(self) -> None:
        tw, th = self.target_width, self.target_height
        pct_w = round(tw * 100.0 / self._orig_w)
        pct_h = round(th * 100.0 / self._orig_h)
        est_str = human_size(self._estimated_bytes)
        orig_str = human_size(self._orig_bytes) if self._orig_bytes > 0 else "?"
        limit_bytes = self.spin_target_kb.value() * 1024
        under_limit = self._estimated_bytes <= limit_bytes

        # 1. Badge right next to the scale slider
        self.lbl_slider_size.setText(f"{pct_w}%  →  {est_str}")
        if under_limit:
            self.lbl_slider_size.setStyleSheet(
                "background-color: #06281e; color: #34d399; border: 1px solid rgba(52, 211, 153, 0.55); "
                "border-radius: 5px; padding: 3px 8px; font-weight: 700; font-size: 12px;"
            )
        else:
            self.lbl_slider_size.setStyleSheet(
                "background-color: #2a1810; color: #facc15; border: 1px solid rgba(250, 204, 21, 0.55); "
                "border-radius: 5px; padding: 3px 8px; font-weight: 700; font-size: 12px;"
            )

        # 2. Summary line under the preview box
        delta_note = ""
        if self._orig_bytes > 0:
            diff_pct = ((self._estimated_bytes - self._orig_bytes) / float(self._orig_bytes)) * 100.0
            sign = "+" if diff_pct > 0 else ""
            delta_note = f" ({orig_str} → {est_str}, {sign}{diff_pct:.0f}%)"
        else:
            delta_note = f" ({est_str})"

        status_icon = "✓" if under_limit else "⚠"
        self.lbl_summary.setText(
            f"{self._orig_w} × {self._orig_h} px  →  {tw} × {th} px ({pct_w}% × {pct_h}%)   •   "
            f"{status_icon} Final Size: {est_str}{delta_note}"
        )
