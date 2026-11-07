"""Caption / Text overlay renderer and interactive dialog for images and videos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
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
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


CAPTION_FONT_CHOICES: list[tuple[str, str]] = [
    ("Sans Serif", "Sans Serif (Clean Modern)"),
    ("Serif", "Serif (Editorial / Classic)"),
    ("Monospace", "Monospace (Typewriter / Code)"),
    ("Impact", "Impact (Bold Meme / Headline)"),
    ("Arial", "Arial"),
    ("Verdana", "Verdana"),
    ("Georgia", "Georgia"),
    ("Courier New", "Courier New"),
    ("Comic Sans MS", "Comic Sans MS"),
    ("DejaVu Sans", "DejaVu Sans"),
    ("Ubuntu", "Ubuntu"),
]

CAPTION_BG_STYLES: list[tuple[str, str]] = [
    ("pill", "💬 Rounded Pill Box (CapCut / Reels)"),
    ("banner", "🎬 Full-Width Subtitle Banner (Snapchat)"),
    ("outline", "🔥 Thick Outline + Shadow (No Box)"),
    ("none", "✨ Plain Text + Subtle Drop Shadow"),
]

CAPTION_POSITIONS: list[tuple[str, str]] = [
    ("bottom", "Bottom Center"),
    ("center", "Middle Center"),
    ("top", "Top Center"),
    ("bottom_left", "Bottom Left"),
    ("bottom_right", "Bottom Right"),
    ("top_left", "Top Left"),
    ("top_right", "Top Right"),
]

CAPTION_STYLE_PRESETS: list[tuple[str, str, dict]] = [
    (
        "pill_dark",
        "💬 Reels Dark Pill (White on Dark)",
        {
            "font_family": "Sans Serif",
            "bold": True,
            "italic": False,
            "text_color": "#ffffff",
            "bg_style": "pill",
            "bg_color": "#000000",
            "bg_opacity": 185,
        },
    ),
    (
        "pill_light",
        "🏷️ Clean Light Pill (Dark on White)",
        {
            "font_family": "Sans Serif",
            "bold": True,
            "italic": False,
            "text_color": "#0f172a",
            "bg_style": "pill",
            "bg_color": "#ffffff",
            "bg_opacity": 230,
        },
    ),
    (
        "pill_yellow",
        "⚡ Viral Highlight (Black on Yellow Pill)",
        {
            "font_family": "Sans Serif",
            "bold": True,
            "italic": False,
            "text_color": "#111827",
            "bg_style": "pill",
            "bg_color": "#facc15",
            "bg_opacity": 240,
        },
    ),
    (
        "banner_dark",
        "🎬 Snapchat Banner (Full-Width Bar)",
        {
            "font_family": "Sans Serif",
            "bold": False,
            "italic": False,
            "text_color": "#ffffff",
            "bg_style": "banner",
            "bg_color": "#000000",
            "bg_opacity": 175,
        },
    ),
    (
        "meme_impact",
        "🔥 Classic Meme (Impact + Black Stroke)",
        {
            "font_family": "Impact",
            "bold": True,
            "italic": False,
            "text_color": "#ffffff",
            "bg_style": "outline",
            "bg_color": "#000000",
            "bg_opacity": 220,
        },
    ),
    (
        "cinema_sub",
        "🎞️ Cinema Yellow Subtitle",
        {
            "font_family": "Serif",
            "bold": True,
            "italic": True,
            "text_color": "#fde047",
            "bg_style": "outline",
            "bg_color": "#000000",
            "bg_opacity": 210,
        },
    ),
]


@dataclass
class CaptionSpec:
    """Complete styling specification for an image or video caption."""

    text: str
    font_family: str = "Sans Serif"
    font_size: int = 42  # reference pixel size on a 720p-tall canvas (2% - 16% of height)
    bold: bool = True
    italic: bool = False
    text_color: str = "#ffffff"
    bg_style: str = "pill"  # "pill" | "banner" | "outline" | "none"
    bg_color: str = "#000000"
    bg_opacity: int = 185  # 0..255
    position: str = "bottom"  # "bottom" | "center" | "top" | "bottom_left" | "bottom_right" | "top_left" | "top_right"
    margin_pct: int = 8  # vertical/horizontal edge margin percentage (2..45)
    only_trim_range: bool = False  # for videos: restrict overlay to [trim_in, trim_out]


def _wrap_paragraph(line: str, fm: QFontMetricsF, max_width: float) -> list[str]:
    """Word-wrap a single paragraph so no line exceeds `max_width`."""
    if not line:
        return [""]
    words = line.split(" ")
    wrapped: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}" if current else word
        if fm.horizontalAdvance(candidate) <= max_width:
            current = candidate
        else:
            if current:
                wrapped.append(current)
            # If a single word is wider than max_width, break by character
            if fm.horizontalAdvance(word) > max_width and len(word) > 1:
                chunk = ""
                for ch in word:
                    if fm.horizontalAdvance(chunk + ch) <= max_width or not chunk:
                        chunk += ch
                    else:
                        wrapped.append(chunk)
                        chunk = ch
                current = chunk
            else:
                current = word
    wrapped.append(current)
    return wrapped


def render_caption_overlay(width: int, height: int, spec: CaptionSpec) -> QImage:
    """Render the caption onto a transparent RGBA QImage of `(width, height)`.

    Used identically for:
    - Live dialog preview
    - Compositing onto an `ImageDocument`
    - Saving a transparent PNG overlay that FFmpeg burns onto videos (`overlay=0:0`)
    """
    W = max(32, int(width))
    H = max(32, int(height))
    overlay = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
    overlay.fill(Qt.GlobalColor.transparent)

    raw_text = (spec.text or "").strip("\r\n")
    if not raw_text.strip():
        return overlay

    # Scale font size proportionally to the canvas reference dimension so preview == full-res output
    ref_dim = max(240.0, min(float(W), float(H), float(H) * 1.25))
    px_size = max(10, round(max(12, min(140, int(spec.font_size))) * (ref_dim / 720.0)))

    font = QFont(spec.font_family)
    if spec.font_family == "Sans Serif":
        font.setStyleHint(QFont.StyleHint.SansSerif)
    elif spec.font_family == "Serif":
        font.setStyleHint(QFont.StyleHint.Serif)
    elif spec.font_family == "Monospace":
        font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(px_size)
    font.setBold(bool(spec.bold))
    font.setItalic(bool(spec.italic))

    fm = QFontMetricsF(font)
    pad_x = max(8.0, px_size * 0.55)
    pad_y = max(5.0, px_size * 0.30)
    margin_x = max(12.0, W * 0.045)
    margin_y = max(10.0, H * (max(2, min(45, int(spec.margin_pct))) / 100.0))
    max_text_w = max(40.0, W - 2.0 * (margin_x + pad_x))

    lines: list[str] = []
    for para in raw_text.splitlines():
        lines.extend(_wrap_paragraph(para.rstrip(), fm, max_text_w))
    if not lines:
        return overlay

    line_h = fm.height()
    line_gap = max(2.0, px_size * 0.14)
    line_widths = [fm.horizontalAdvance(ln) for ln in lines]
    max_line_w = max(line_widths) if line_widths else 0.0
    total_text_h = len(lines) * line_h + max(0, len(lines) - 1) * line_gap
    box_w = min(float(W) - 2.0 * margin_x, max_line_w + 2.0 * pad_x)
    box_h = total_text_h + 2.0 * pad_y

    pos = spec.position or "bottom"
    # Horizontal anchor
    if pos.endswith("_left"):
        box_x = margin_x
        h_align = "left"
    elif pos.endswith("_right"):
        box_x = max(margin_x, W - margin_x - box_w)
        h_align = "right"
    else:
        box_x = (W - box_w) / 2.0
        h_align = "center"

    # Vertical anchor
    if pos.startswith("top"):
        box_y = min(max(4.0, margin_y), max(4.0, H - box_h - 4.0))
    elif pos == "center":
        box_y = (H - box_h) / 2.0
    else:
        box_y = max(4.0, H - margin_y - box_h)

    painter = QPainter(overlay)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(font)

    bg_col = QColor(spec.bg_color if QColor(spec.bg_color).isValid() else "#000000")
    bg_col.setAlpha(max(0, min(255, int(spec.bg_opacity))))
    txt_col = QColor(spec.text_color if QColor(spec.text_color).isValid() else "#ffffff")

    # 1. Background box / banner / pill
    if spec.bg_style == "banner" and bg_col.alpha() > 0:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_col)
        painter.drawRect(QRectF(0.0, box_y, float(W), box_h))
    elif spec.bg_style == "pill" and bg_col.alpha() > 0:
        radius = min(box_h * 0.32, px_size * 0.65, 28.0)
        # Drop shadow under pill
        shadow_col = QColor(0, 0, 0, min(110, bg_col.alpha() // 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow_col)
        painter.drawRoundedRect(QRectF(box_x, box_y + max(2.0, px_size * 0.06), box_w, box_h), radius, radius)
        painter.setBrush(bg_col)
        painter.drawRoundedRect(QRectF(box_x, box_y, box_w, box_h), radius, radius)

    # 2. Draw each text line with optional outline / drop shadow
    stroke_col = QColor(spec.bg_color if QColor(spec.bg_color).isValid() else "#000000")
    stroke_col.setAlpha(max(160, min(255, int(spec.bg_opacity) if spec.bg_opacity > 0 else 220)))

    cur_y = box_y + pad_y + fm.ascent()
    for ln, lw in zip(lines, line_widths):
        if h_align == "left":
            tx = box_x + pad_x
        elif h_align == "right":
            tx = box_x + box_w - pad_x - lw
        else:
            tx = box_x + (box_w - lw) / 2.0

        path = QPainterPath()
        path.addText(QPointF(tx, cur_y), font, ln)

        if spec.bg_style == "outline":
            # Thick outer stroke + subtle drop shadow for high legibility on any background
            shadow_offset = max(1.5, px_size * 0.05)
            shadow_path = QPainterPath()
            shadow_path.addText(QPointF(tx + shadow_offset, cur_y + shadow_offset), font, ln)
            painter.fillPath(shadow_path, QColor(0, 0, 0, 150))

            pen = QPen(stroke_col, max(2.5, px_size * 0.14), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.strokePath(path, pen)
            painter.fillPath(path, txt_col)
        elif spec.bg_style == "none":
            shadow_offset = max(1.5, px_size * 0.05)
            shadow_path = QPainterPath()
            shadow_path.addText(QPointF(tx + shadow_offset, cur_y + shadow_offset), font, ln)
            painter.fillPath(shadow_path, QColor(0, 0, 0, 140))
            painter.fillPath(path, txt_col)
        else:
            painter.fillPath(path, txt_col)

        cur_y += line_h + line_gap

    painter.end()
    return overlay


def apply_caption_to_image(image: QImage, spec: CaptionSpec) -> QImage:
    """Return a copy of `image` with `spec` rendered on top."""
    if image is None or image.isNull():
        return image
    if not (spec.text or "").strip():
        return image.copy()

    base = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    overlay = render_caption_overlay(base.width(), base.height(), spec)
    painter = QPainter(base)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(0, 0, overlay)
    painter.end()
    if image.format() in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32):
        return base.convertToFormat(image.format())
    return base


class CaptionDialog(QDialog):
    """Interactive dialog for adding styled caption text with background, font, and size to images & videos."""

    def __init__(
        self,
        frame: Optional[QImage] = None,
        *,
        is_video: bool = False,
        trim_range_label: Optional[str] = None,
        initial_spec: Optional[CaptionSpec] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Add Caption / Text — HakuView" if not is_video else "Add Video Caption / Text — HakuView")
        self.setMinimumWidth(520)

        self._is_video = is_video
        self.save_as_copy: bool = False
        self._updating_preset = False

        if frame is not None and not frame.isNull():
            self._frame = frame
        else:
            fallback = QImage(640, 360, QImage.Format.Format_RGB32)
            fallback.fill(QColor("#1e293b"))
            self._frame = fallback

        spec = initial_spec or CaptionSpec(text="")
        self._text_color = QColor(spec.text_color if QColor(spec.text_color).isValid() else "#ffffff")
        self._bg_color = QColor(spec.bg_color if QColor(spec.bg_color).isValid() else "#000000")

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # 1. Live Preview
        preview_group = QGroupBox("Live Preview", self)
        preview_layout = QVBoxLayout(preview_group)
        self.lbl_preview = QLabel(self)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(460, 230)
        preview_layout.addWidget(self.lbl_preview)
        root.addWidget(preview_group)

        # 2. Caption Text Input + Style Presets
        text_group = QGroupBox("Caption Text && Style Preset", self)
        text_form = QFormLayout(text_group)

        self.combo_preset = QComboBox(self)
        for preset_id, label, data in CAPTION_STYLE_PRESETS:
            self.combo_preset.addItem(label, (preset_id, data))
        self.combo_preset.currentIndexChanged.connect(self._on_preset_changed)
        text_form.addRow("Quick Preset:", self.combo_preset)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlaceholderText("Type your caption here… (supports multiple lines & emojis)")
        self.text_edit.setFixedHeight(64)
        self.text_edit.setPlainText(spec.text)
        self.text_edit.textChanged.connect(self._update_preview)
        text_form.addRow("Caption:", self.text_edit)
        root.addWidget(text_group)

        # 3. Font, Size, Colors & Background
        style_group = QGroupBox("Font, Size && Background", self)
        style_form = QFormLayout(style_group)

        font_row = QHBoxLayout()
        self.combo_font = QComboBox(self)
        seen_families: set[str] = set()
        for fam, label in CAPTION_FONT_CHOICES:
            self.combo_font.addItem(label, fam)
            seen_families.add(fam.lower())
        try:
            for sys_fam in QFontDatabase.families()[:60]:
                if sys_fam.lower() not in seen_families and not sys_fam.startswith("@"):
                    self.combo_font.addItem(sys_fam, sys_fam)
                    seen_families.add(sys_fam.lower())
        except Exception:
            pass
        idx_font = self.combo_font.findData(spec.font_family)
        if idx_font >= 0:
            self.combo_font.setCurrentIndex(idx_font)
        self.combo_font.currentIndexChanged.connect(self._update_preview)
        font_row.addWidget(self.combo_font, stretch=1)

        self.chk_bold = QCheckBox("Bold", self)
        self.chk_bold.setChecked(spec.bold)
        self.chk_bold.toggled.connect(self._update_preview)
        font_row.addWidget(self.chk_bold)

        self.chk_italic = QCheckBox("Italic", self)
        self.chk_italic.setChecked(spec.italic)
        self.chk_italic.toggled.connect(self._update_preview)
        font_row.addWidget(self.chk_italic)
        style_form.addRow("Font:", font_row)

        size_row = QHBoxLayout()
        self.slider_size = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_size.setRange(14, 120)
        self.slider_size.setValue(max(14, min(120, int(spec.font_size))))
        self.spin_size = QSpinBox(self)
        self.spin_size.setRange(14, 120)
        self.spin_size.setSuffix(" pt")
        self.spin_size.setValue(self.slider_size.value())
        self.slider_size.valueChanged.connect(self.spin_size.setValue)
        self.spin_size.valueChanged.connect(self.slider_size.setValue)
        self.slider_size.valueChanged.connect(self._update_preview)
        size_row.addWidget(self.slider_size, stretch=1)
        size_row.addWidget(self.spin_size)
        style_form.addRow("Font Size:", size_row)

        self.combo_bg_style = QComboBox(self)
        for style_id, label in CAPTION_BG_STYLES:
            self.combo_bg_style.addItem(label, style_id)
        idx_bg = self.combo_bg_style.findData(spec.bg_style)
        if idx_bg >= 0:
            self.combo_bg_style.setCurrentIndex(idx_bg)
        self.combo_bg_style.currentIndexChanged.connect(self._on_bg_style_changed)
        style_form.addRow("Background:", self.combo_bg_style)

        color_row = QHBoxLayout()
        self.lbl_text_swatch = QLabel(self)
        self.lbl_text_swatch.setFixedSize(22, 22)
        self.btn_text_color = QPushButton("🎨 Text Color...", self)
        self.btn_text_color.clicked.connect(self.choose_text_color)
        color_row.addWidget(self.lbl_text_swatch)
        color_row.addWidget(self.btn_text_color)

        color_row.addSpacing(12)
        self.lbl_bg_swatch = QLabel(self)
        self.lbl_bg_swatch.setFixedSize(22, 22)
        self.btn_bg_color = QPushButton("🎨 Box / Stroke Color...", self)
        self.btn_bg_color.clicked.connect(self.choose_bg_color)
        color_row.addWidget(self.lbl_bg_swatch)
        color_row.addWidget(self.btn_bg_color)
        color_row.addStretch(1)
        style_form.addRow("Colors:", color_row)

        opacity_row = QHBoxLayout()
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_opacity.setRange(0, 255)
        self.slider_opacity.setValue(max(0, min(255, int(spec.bg_opacity))))
        self.lbl_opacity_val = QLabel(f"{round(self.slider_opacity.value() * 100 / 255)}%", self)
        self.lbl_opacity_val.setFixedWidth(44)
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        opacity_row.addWidget(self.slider_opacity, stretch=1)
        opacity_row.addWidget(self.lbl_opacity_val)
        style_form.addRow("Box Opacity:", opacity_row)

        pos_row = QHBoxLayout()
        self.combo_position = QComboBox(self)
        for pos_id, label in CAPTION_POSITIONS:
            self.combo_position.addItem(label, pos_id)
        idx_pos = self.combo_position.findData(spec.position)
        if idx_pos >= 0:
            self.combo_position.setCurrentIndex(idx_pos)
        self.combo_position.currentIndexChanged.connect(self._update_preview)
        pos_row.addWidget(self.combo_position, stretch=1)

        pos_row.addWidget(QLabel("Edge Offset:", self))
        self.slider_margin = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_margin.setRange(2, 45)
        self.slider_margin.setValue(max(2, min(45, int(spec.margin_pct))))
        self.lbl_margin_val = QLabel(f"{self.slider_margin.value()}%", self)
        self.lbl_margin_val.setFixedWidth(36)
        self.slider_margin.valueChanged.connect(self._on_margin_changed)
        pos_row.addWidget(self.slider_margin, stretch=1)
        pos_row.addWidget(self.lbl_margin_val)
        style_form.addRow("Placement:", pos_row)

        self.chk_trim_range = QCheckBox(
            f"Only show caption between marked In/Out points ({trim_range_label})" if trim_range_label else "Only show between In/Out points",
            self,
        )
        self.chk_trim_range.setVisible(bool(is_video and trim_range_label))
        self.chk_trim_range.setChecked(bool(is_video and trim_range_label))
        if is_video and trim_range_label:
            style_form.addRow("Timing:", self.chk_trim_range)

        root.addWidget(style_group)

        # 4. Dialog Buttons
        btn_row = QHBoxLayout()
        if not is_video:
            self.btn_save_copy = QPushButton("💾 Save as New Image", self)
            self.btn_save_copy.setToolTip("Write <name>_caption.<ext> next to the original without modifying the open image")
            self.btn_save_copy.clicked.connect(self._accept_as_copy)
            btn_row.addWidget(self.btn_save_copy)
        btn_row.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("🎬 Export Captioned Video" if is_video else "✓ Apply to Image")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        root.addLayout(btn_row)

        self._update_swatches()
        self._update_preview()
        self.text_edit.setFocus()

    def _accept_as_copy(self) -> None:
        self.save_as_copy = True
        self.accept()

    @property
    def caption_text(self) -> str:
        return self.text_edit.toPlainText().strip("\r\n")

    def spec(self, *, preview_placeholder: bool = False) -> CaptionSpec:
        txt = self.caption_text
        if not txt.strip() and preview_placeholder:
            txt = "Your caption here ✨"
        return CaptionSpec(
            text=txt,
            font_family=str(self.combo_font.currentData() or "Sans Serif"),
            font_size=int(self.slider_size.value()),
            bold=self.chk_bold.isChecked(),
            italic=self.chk_italic.isChecked(),
            text_color=self._text_color.name(),
            bg_style=str(self.combo_bg_style.currentData() or "pill"),
            bg_color=self._bg_color.name(),
            bg_opacity=int(self.slider_opacity.value()),
            position=str(self.combo_position.currentData() or "bottom"),
            margin_pct=int(self.slider_margin.value()),
            only_trim_range=not self.chk_trim_range.isHidden() and self.chk_trim_range.isChecked(),
        )

    def _on_preset_changed(self, index: int) -> None:
        if self._updating_preset:
            return
        data = self.combo_preset.itemData(index)
        if not data:
            return
        _preset_id, cfg = data
        self._updating_preset = True
        try:
            idx_f = self.combo_font.findData(cfg["font_family"])
            if idx_f >= 0:
                self.combo_font.setCurrentIndex(idx_f)
            self.chk_bold.setChecked(bool(cfg["bold"]))
            self.chk_italic.setChecked(bool(cfg["italic"]))
            self._text_color = QColor(cfg["text_color"])
            self._bg_color = QColor(cfg["bg_color"])
            idx_bg = self.combo_bg_style.findData(cfg["bg_style"])
            if idx_bg >= 0:
                self.combo_bg_style.setCurrentIndex(idx_bg)
            self.slider_opacity.setValue(int(cfg["bg_opacity"]))
            self._update_swatches()
        finally:
            self._updating_preset = False
        self._update_preview()

    def _on_bg_style_changed(self, _index: int = 0) -> None:
        style = str(self.combo_bg_style.currentData() or "pill")
        has_bg_or_stroke = style in ("pill", "banner", "outline")
        self.btn_bg_color.setEnabled(has_bg_or_stroke)
        self.slider_opacity.setEnabled(style in ("pill", "banner"))
        self._update_preview()

    def _on_opacity_changed(self, val: int) -> None:
        self.lbl_opacity_val.setText(f"{round(val * 100 / 255)}%")
        self._update_preview()

    def _on_margin_changed(self, val: int) -> None:
        self.lbl_margin_val.setText(f"{val}%")
        self._update_preview()

    def choose_text_color(self) -> None:
        chosen = QColorDialog.getColor(self._text_color, self, "Choose Caption Text Color")
        if chosen.isValid():
            self._text_color = chosen
            self._update_swatches()
            self._update_preview()

    def choose_bg_color(self) -> None:
        chosen = QColorDialog.getColor(self._bg_color, self, "Choose Caption Box / Stroke Color")
        if chosen.isValid():
            self._bg_color = chosen
            self._update_swatches()
            self._update_preview()

    def _update_swatches(self) -> None:
        self.lbl_text_swatch.setStyleSheet(
            f"background-color: {self._text_color.name()}; border: 1px solid #94a3b8; border-radius: 4px;"
        )
        self.lbl_bg_swatch.setStyleSheet(
            f"background-color: {self._bg_color.name()}; border: 1px solid #94a3b8; border-radius: 4px;"
        )

    def _update_preview(self) -> None:
        scaled_frame = self._frame.scaled(
            QSize(460, 230),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        rendered = apply_caption_to_image(scaled_frame, self.spec(preview_placeholder=True))
        self.lbl_preview.setPixmap(QPixmap.fromImage(rendered))
