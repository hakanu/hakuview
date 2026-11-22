"""Lazy-loaded PDF Export & Passport/Document Sheet Builder (Ctrl+P) — zero startup cost."""

from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
from typing import Optional

from PIL import Image, ImageDraw
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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
from .image_ops import _pil_to_qimage, _qimage_to_pil, apply_quick_filter


PDF_PAGE_MODES: list[tuple[str, str]] = [
    ("Fit to Image (Zero Margins)", "fit_image"),
    ("A4 Document Page (210 × 297 mm)", "a4"),
    ("US Letter Page (8.5 × 11 in)", "letter"),
    ("Passport / ID 4-Up Sheet (35×45 mm on 4×6 in)", "passport_4up"),
]


def compose_pdf_page_pil(
    image: QImage,
    *,
    page_mode: str = "fit_image",
    scale_pct: int = 100,
    dpi: int = 150,
    clean_scan: bool = False,
) -> tuple[Image.Image, float]:
    """Compose `image` onto a Pillow RGB page for PDF encoding; returns `(pil_rgb, effective_dpi)`."""
    src_qimg = apply_quick_filter(image, "doc_scan") if clean_scan else image
    pil_src = _qimage_to_pil(src_qimg).convert("RGB")
    pct = max(10, min(100, int(scale_pct))) / 100.0
    eff_dpi = max(72.0, float(dpi) * pct)

    if page_mode == "fit_image":
        if pct < 0.999:
            nw = max(16, int(round(pil_src.width * pct)))
            nh = max(16, int(round(pil_src.height * pct)))
            pil_src = pil_src.resize((nw, nh), Image.Resampling.LANCZOS)
        return pil_src, float(dpi)

    if page_mode == "passport_4up":
        # 4x6 inch photo sheet at 300 DPI (scaled by pct), containing four 35x45 mm ID photos + cut marks
        sheet_dpi = max(120.0, 300.0 * pct)
        sw = int(round(6.0 * sheet_dpi))
        sh = int(round(4.0 * sheet_dpi))
        page = Image.new("RGB", (sw, sh), (255, 255, 255))
        draw = ImageDraw.Draw(page)

        pw = int(round((35.0 / 25.4) * sheet_dpi))
        ph = int(round((45.0 / 25.4) * sheet_dpi))
        # Center-crop source to 35:45 aspect ratio if needed, then Lanczos resize
        target_ar = 35.0 / 45.0
        src_ar = pil_src.width / max(1.0, float(pil_src.height))
        if src_ar > target_ar:
            crop_w = max(1, int(round(pil_src.height * target_ar)))
            left = (pil_src.width - crop_w) // 2
            cropped = pil_src.crop((left, 0, left + crop_w, pil_src.height))
        else:
            crop_h = max(1, int(round(pil_src.width / target_ar)))
            top = (pil_src.height - crop_h) // 2
            cropped = pil_src.crop((0, top, pil_src.width, top + crop_h))
        tile = cropped.resize((pw, ph), Image.Resampling.LANCZOS)

        gap = max(12, int(round(0.18 * sheet_dpi)))
        grid_w = pw * 4 + gap * 3
        if grid_w > sw - 24:
            # 2x2 layout on 4x6 sheet so 35x45mm stays exact physical size!
            cols, rows = 2, 2
            # Portrait 4x6 if 2x2 fits better vertically
            sw, sh = int(round(4.0 * sheet_dpi)), int(round(6.0 * sheet_dpi))
            page = Image.new("RGB", (sw, sh), (255, 255, 255))
            draw = ImageDraw.Draw(page)
        else:
            cols, rows = 4, 1

        total_w = cols * pw + (cols - 1) * gap
        total_h = rows * ph + (rows - 1) * gap
        ox = max(8, (sw - total_w) // 2)
        oy = max(8, (sh - total_h) // 2)

        for r in range(rows):
            for c in range(cols):
                tx = ox + c * (pw + gap)
                ty = oy + r * (ph + gap)
                page.paste(tile, (tx, ty))
                draw.rectangle([tx - 1, ty - 1, tx + pw, ty + ph], outline=(203, 213, 225), width=1)
        return page, sheet_dpi

    # A4 (210x297 mm) or US Letter (8.5x11 in), auto-matching portrait vs landscape
    if page_mode == "letter":
        w_in, h_in = 8.5, 11.0
    else:
        w_in, h_in = 210.0 / 25.4, 297.0 / 25.4
    if pil_src.width > pil_src.height:
        w_in, h_in = h_in, w_in

    pw = max(64, int(round(w_in * eff_dpi)))
    ph = max(64, int(round(h_in * eff_dpi)))
    margin = int(round(min(pw, ph) * 0.045))
    avail_w = max(16, pw - 2 * margin)
    avail_h = max(16, ph - 2 * margin)

    fit = min(avail_w / max(1, pil_src.width), avail_h / max(1, pil_src.height))
    fw = max(1, int(round(pil_src.width * fit)))
    fh = max(1, int(round(pil_src.height * fit)))
    fg = pil_src.resize((fw, fh), Image.Resampling.LANCZOS)

    page = Image.new("RGB", (pw, ph), (255, 255, 255))
    page.paste(fg, ((pw - fw) // 2, (ph - fh) // 2))
    return page, eff_dpi


def encode_pdf_bytes(
    image: QImage,
    *,
    page_mode: str = "fit_image",
    quality: int = 85,
    scale_pct: int = 100,
    dpi: int = 150,
    clean_scan: bool = False,
) -> bytes:
    """Encode `image` into an in-memory single-page PDF with DCT/JPEG stream compression."""
    if image is None or image.isNull():
        return b""
    page_pil, eff_dpi = compose_pdf_page_pil(
        image,
        page_mode=page_mode,
        scale_pct=scale_pct,
        dpi=dpi,
        clean_scan=clean_scan,
    )
    buf = io.BytesIO()
    q = max(15, min(98, int(quality)))
    page_pil.save(buf, format="PDF", resolution=eff_dpi, quality=q)
    return buf.getvalue()


def save_pdf_atomic(pdf_bytes: bytes, target_path: Path | str) -> int:
    """Atomically write `pdf_bytes` to `target_path` and return written byte count."""
    dest = Path(target_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.stem}_tmp_", suffix=".pdf", dir=str(dest.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_bytes(pdf_bytes)
        os.replace(str(tmp_path), str(dest))
        return len(pdf_bytes)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


class PdfExportDialog(QDialog):
    """Export current image to PDF (Fit, A4, Letter, or 4-Up Passport ID Sheet) with live KB auto-fit."""

    def __init__(
        self,
        frame: QImage,
        source_path: Optional[Path] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("📄 Export Image to PDF (Document / Form / ID Sheet)")
        self.setMinimumWidth(540)

        self._frame = frame
        self._source_path = source_path
        self._pdf_bytes: bytes = b""
        self._saved_path: Optional[Path] = None
        self._updating = False

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Preview group
        pv_group = QGroupBox("Live Page Preview & PDF Size", self)
        pv_layout = QVBoxLayout(pv_group)
        self.lbl_preview = QLabel(self)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(420, 200)
        self.lbl_preview.setStyleSheet(
            "background-color: #0b0f19; border: 1px solid rgba(148, 163, 184, 0.25); border-radius: 6px;"
        )
        pv_layout.addWidget(self.lbl_preview)

        self.lbl_summary = QLabel(self)
        self.lbl_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_summary.setStyleSheet("color: #34d399; font-size: 12.5px; font-weight: 700;")
        pv_layout.addWidget(self.lbl_summary)
        root.addWidget(pv_group)

        # Layout & Document Scan options
        opt_group = QGroupBox("PDF Page Layout & Compression", self)
        form = QFormLayout(opt_group)

        self.combo_page = QComboBox(self)
        for label, mode_id in PDF_PAGE_MODES:
            self.combo_page.addItem(label, mode_id)
        form.addRow("Page Layout:", self.combo_page)

        self.chk_clean_scan = QCheckBox("📄 Clean Document / Receipt Scan (whiten paper & sharpen text)", self)
        form.addRow("", self.chk_clean_scan)

        q_row = QHBoxLayout()
        self.slider_quality = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_quality.setRange(20, 96)
        self.slider_quality.setValue(85)
        self.lbl_quality = QLabel("85%", self)
        self.lbl_quality.setMinimumWidth(44)
        q_row.addWidget(self.slider_quality, stretch=1)
        q_row.addWidget(self.lbl_quality)
        form.addRow("JPEG Quality:", q_row)

        s_row = QHBoxLayout()
        self.slider_scale = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_scale.setRange(20, 100)
        self.slider_scale.setValue(100)
        self.lbl_scale = QLabel("100%", self)
        self.lbl_scale.setMinimumWidth(44)
        s_row.addWidget(self.slider_scale, stretch=1)
        s_row.addWidget(self.lbl_scale)
        form.addRow("Resolution %:", s_row)

        fit_row = QHBoxLayout()
        self.spin_target_kb = QSpinBox(self)
        self.spin_target_kb.setRange(25, 50000)
        self.spin_target_kb.setValue(200)
        self.spin_target_kb.setSuffix(" KB")
        self.btn_fit_kb = QPushButton("🎯 Fit ≤ Target KB", self)
        self.btn_fit_kb.setStyleSheet("font-weight: 600; color: #34d399;")
        self.btn_fit_kb.clicked.connect(lambda: self.fit_to_target_kb(self.spin_target_kb.value()))
        fit_row.addWidget(self.spin_target_kb)
        fit_row.addWidget(self.btn_fit_kb)
        for kb, lbl in ((200, "≤ 200 KB"), (500, "≤ 500 KB"), (1024, "≤ 1 MB")):
            b = QPushButton(lbl, self)
            b.clicked.connect(lambda _c=False, k=kb: self.fit_to_target_kb(k))
            fit_row.addWidget(b)
        form.addRow("Max PDF Size:", fit_row)
        root.addWidget(opt_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("💾 Save PDF...")
        buttons.accepted.connect(self._on_save_clicked)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.combo_page.currentIndexChanged.connect(self._refresh)
        self.chk_clean_scan.toggled.connect(self._refresh)
        self.slider_quality.valueChanged.connect(self._refresh)
        self.slider_scale.valueChanged.connect(self._refresh)
        self.spin_target_kb.valueChanged.connect(self._update_summary_label)

        self._refresh()

    @property
    def page_mode(self) -> str:
        return str(self.combo_page.currentData() or "fit_image")

    @property
    def pdf_bytes(self) -> bytes:
        return self._pdf_bytes

    @property
    def saved_path(self) -> Optional[Path]:
        return self._saved_path

    def fit_to_target_kb(self, target_kb: int) -> int:
        target_kb = max(25, int(target_kb))
        self._updating = True
        try:
            self.spin_target_kb.setValue(target_kb)
        finally:
            self._updating = False

        limit = target_kb * 1024
        # 1. Try tuning quality (35..92) at current resolution %
        cur_scale = self.slider_scale.value()
        lo_q, hi_q, best_q = 35, 92, -1
        while lo_q <= hi_q:
            mid_q = (lo_q + hi_q) // 2
            b = encode_pdf_bytes(
                self._frame,
                page_mode=self.page_mode,
                quality=mid_q,
                scale_pct=cur_scale,
                clean_scan=self.chk_clean_scan.isChecked(),
            )
            if len(b) <= limit:
                best_q = mid_q
                lo_q = mid_q + 1
            else:
                hi_q = mid_q - 1

        if best_q > 0:
            self._updating = True
            try:
                self.slider_quality.setValue(best_q)
            finally:
                self._updating = False
            self._refresh()
            return len(self._pdf_bytes)

        # 2. Binary-search resolution scale (20..100%) at quality=75
        lo_s, hi_s, best_s = 20, cur_scale, 20
        while lo_s <= hi_s:
            mid_s = (lo_s + hi_s) // 2
            b = encode_pdf_bytes(
                self._frame,
                page_mode=self.page_mode,
                quality=75,
                scale_pct=mid_s,
                clean_scan=self.chk_clean_scan.isChecked(),
            )
            if len(b) <= limit:
                best_s = mid_s
                lo_s = mid_s + 1
            else:
                hi_s = mid_s - 1

        self._updating = True
        try:
            self.slider_quality.setValue(75)
            self.slider_scale.setValue(best_s)
        finally:
            self._updating = False
        self._refresh()

        # 3. Final quality step-down if still slightly over at 20% scale
        if len(self._pdf_bytes) > limit:
            for q in (60, 45, 30, 20):
                b = encode_pdf_bytes(
                    self._frame,
                    page_mode=self.page_mode,
                    quality=q,
                    scale_pct=best_s,
                    clean_scan=self.chk_clean_scan.isChecked(),
                )
                if len(b) <= limit or q == 20:
                    self._updating = True
                    try:
                        self.slider_quality.setValue(q)
                    finally:
                        self._updating = False
                    self._refresh()
                    break
        return len(self._pdf_bytes)

    def _refresh(self) -> None:
        if self._updating:
            return
        q = self.slider_quality.value()
        s = self.slider_scale.value()
        self.lbl_quality.setText(f"{q}%")
        self.lbl_scale.setText(f"{s}%")

        page_pil, _dpi = compose_pdf_page_pil(
            self._frame,
            page_mode=self.page_mode,
            scale_pct=s,
            clean_scan=self.chk_clean_scan.isChecked(),
        )
        pv_qimg = _pil_to_qimage(page_pil).scaled(
            390, 190, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        self.lbl_preview.setPixmap(QPixmap.fromImage(pv_qimg))

        self._pdf_bytes = encode_pdf_bytes(
            self._frame,
            page_mode=self.page_mode,
            quality=q,
            scale_pct=s,
            clean_scan=self.chk_clean_scan.isChecked(),
        )
        self._update_summary_label()

    def _update_summary_label(self) -> None:
        nbytes = len(self._pdf_bytes)
        limit = self.spin_target_kb.value() * 1024
        ok = nbytes <= limit
        icon = "✓" if ok else "⚠"
        color = "#34d399" if ok else "#facc15"
        self.lbl_summary.setStyleSheet(f"color: {color}; font-size: 12.5px; font-weight: 700;")
        self.lbl_summary.setText(
            f"{icon} Exact PDF Size: {human_size(nbytes)}   (Quality {self.slider_quality.value()}%, Scale {self.slider_scale.value()}%)"
        )

    def _on_save_clicked(self) -> None:
        if self._source_path is not None:
            default_pdf = self._source_path.with_suffix(".pdf")
            if default_pdf.exists():
                counter = 2
                while (self._source_path.parent / f"{self._source_path.stem}_{counter}.pdf").exists():
                    counter += 1
                default_pdf = self._source_path.parent / f"{self._source_path.stem}_{counter}.pdf"
        else:
            default_pdf = Path.home() / "document.pdf"

        out_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save PDF Document",
            str(default_pdf),
            "PDF Documents (*.pdf)",
        )
        if not out_str:
            return
        dest = Path(out_str)
        if dest.suffix.lower() != ".pdf":
            dest = dest.with_suffix(".pdf")
        save_pdf_atomic(self._pdf_bytes, dest)
        self._saved_path = dest
        self.accept()
