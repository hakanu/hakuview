"""Region Erase / Redact / Blur / Pixelate / Heal tool for images and videos."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
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


ERASE_STYLES: list[tuple[str, str]] = [
    ("blur", "🌫️ Heavy Blur (Faces, Text & Plates)"),
    ("pixelate", "🟦 Pixelate / Mosaic Censor"),
    ("heal", "🪄 Smart Heal / Delogo (Blend Surrounding Pixels)"),
    ("surround", "🎨 Fill with Surrounding Color"),
    ("blackout", "⬛ Solid Blackout Box"),
]


@dataclass
class EraseRegionSpec:
    """Specification for erasing/redacting a rectangular region in image/video pixel coordinates."""

    rect: QRect
    style: str = "blur"  # "blur" | "pixelate" | "heal" | "surround" | "blackout"
    strength: int = 60  # 10..100
    only_trim_range: bool = False


def sample_surrounding_color(image: QImage, rect: QRect) -> QColor:
    """Sample the average RGB color of pixels just outside `rect` (or inside edges if full-bleed)."""
    if image is None or image.isNull():
        return QColor(0, 0, 0)
    iw, ih = image.width(), image.height()
    bounded = rect.intersected(image.rect())
    if bounded.isEmpty():
        return QColor(0, 0, 0)

    x0 = max(0, bounded.left() - 2)
    y0 = max(0, bounded.top() - 2)
    x1 = min(iw - 1, bounded.right() + 2)
    y1 = min(ih - 1, bounded.bottom() + 2)

    r_sum = g_sum = b_sum = count = 0
    step_x = max(1, (x1 - x0) // 32)
    step_y = max(1, (y1 - y0) // 32)

    for x in range(x0, x1 + 1, step_x):
        for y in (y0, y1):
            c = image.pixelColor(x, y)
            r_sum += c.red()
            g_sum += c.green()
            b_sum += c.blue()
            count += 1
    for y in range(y0, y1 + 1, step_y):
        for x in (x0, x1):
            c = image.pixelColor(x, y)
            r_sum += c.red()
            g_sum += c.green()
            b_sum += c.blue()
            count += 1

    if count == 0:
        return QColor(0, 0, 0)
    return QColor(r_sum // count, g_sum // count, b_sum // count)


def _heal_patch(image: QImage, bounded: QRect) -> QImage:
    """Inpaint `bounded` by bilinearly diffusing the top/bottom/left/right boundary strips."""
    iw, ih = image.width(), image.height()
    bw, bh = bounded.width(), bounded.height()
    x_top_y = max(0, bounded.top() - 1)
    x_bot_y = min(ih - 1, bounded.bottom() + 1)
    y_left_x = max(0, bounded.left() - 1)
    y_right_x = min(iw - 1, bounded.right() + 1)

    # Render onto a low-frequency 24x24 grid then smoothly upscale for natural, artifact-free blending
    gw = min(28, max(8, bw))
    gh = min(28, max(8, bh))
    grid = QImage(gw, gh, QImage.Format.Format_RGB32)

    top_samples = [
        image.pixelColor(min(iw - 1, bounded.left() + int((gx + 0.5) * bw / gw)), x_top_y)
        for gx in range(gw)
    ]
    bot_samples = [
        image.pixelColor(min(iw - 1, bounded.left() + int((gx + 0.5) * bw / gw)), x_bot_y)
        for gx in range(gw)
    ]
    left_samples = [
        image.pixelColor(y_left_x, min(ih - 1, bounded.top() + int((gy + 0.5) * bh / gh)))
        for gy in range(gh)
    ]
    right_samples = [
        image.pixelColor(y_right_x, min(ih - 1, bounded.top() + int((gy + 0.5) * bh / gh)))
        for gy in range(gh)
    ]

    for gy in range(gh):
        v = (gy + 0.5) / gh
        w_top = 1.0 / max(0.04, v)
        w_bot = 1.0 / max(0.04, 1.0 - v)
        c_left = left_samples[gy]
        c_right = right_samples[gy]
        for gx in range(gw):
            u = (gx + 0.5) / gw
            w_left = 1.0 / max(0.04, u)
            w_right = 1.0 / max(0.04, 1.0 - u)
            w_total = w_top + w_bot + w_left + w_right
            c_top = top_samples[gx]
            c_bot = bot_samples[gx]
            r = round((c_top.red() * w_top + c_bot.red() * w_bot + c_left.red() * w_left + c_right.red() * w_right) / w_total)
            g = round((c_top.green() * w_top + c_bot.green() * w_bot + c_left.green() * w_left + c_right.green() * w_right) / w_total)
            b = round((c_top.blue() * w_top + c_bot.blue() * w_bot + c_left.blue() * w_left + c_right.blue() * w_right) / w_total)
            grid.setPixelColor(gx, gy, QColor(max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))))

    return grid.scaled(QSize(bw, bh), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def erase_image_region(
    image: QImage,
    rect: QRect,
    style: str = "blur",
    strength: int = 60,
) -> QImage:
    """Return a copy of `image` with `rect` erased/redacted using `style`."""
    if image is None or image.isNull():
        return image
    bounded = rect.intersected(image.rect())
    if bounded.isEmpty() or bounded.width() < 2 or bounded.height() < 2:
        return image.copy()

    out = image.copy()
    bw, bh = bounded.width(), bounded.height()
    s = max(10, min(100, int(strength)))

    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    if style == "blackout":
        painter.fillRect(bounded, QColor(0, 0, 0))
    elif style == "surround":
        painter.fillRect(bounded, sample_surrounding_color(image, bounded))
    elif style == "heal":
        patch = _heal_patch(image, bounded)
        painter.drawImage(bounded.topLeft(), patch)
    elif style == "pixelate":
        # Mosaic blocks: higher strength -> larger blocks (fewer cells)
        cells = max(3, round(20 - (s / 100.0) * 16))
        aspect = bw / max(1, bh)
        if aspect >= 1.0:
            tw = max(2, min(bw, cells))
            th = max(2, min(bh, round(cells / aspect)))
        else:
            th = max(2, min(bh, cells))
            tw = max(2, min(bw, round(cells * aspect)))
        tiny = image.copy(bounded).scaled(
            QSize(tw, th),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        mosaic = tiny.scaled(
            QSize(bw, bh),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        painter.drawImage(bounded.topLeft(), mosaic)
    else:  # "blur" (default)
        # Sample with a surrounding margin so the blur blends in surrounding context as well
        pad = max(6, round(min(bw, bh) * 0.28))
        ctx = bounded.adjusted(-pad, -pad, pad, pad).intersected(image.rect())
        cw, ch = max(2, ctx.width()), max(2, ctx.height())
        div = max(3.0, 2.5 + (s / 100.0) * 18.0)
        tw = max(2, round(cw / div))
        th = max(2, round(ch / div))
        patch = image.copy(ctx)
        for _ in range(2):
            patch = patch.scaled(QSize(tw, th), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            patch = patch.scaled(QSize(cw, ch), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        sub = patch.copy(bounded.left() - ctx.left(), bounded.top() - ctx.top(), bw, bh)
        painter.drawImage(bounded.topLeft(), sub)

    painter.end()
    return out


def build_erase_video_vf(
    rect: QRect,
    video_w: int,
    video_h: int,
    *,
    style: str = "blur",
    strength: int = 60,
    surround_hex: str = "#000000",
    trim_range_ms: Optional[tuple[int, int]] = None,
) -> str:
    """Build an FFmpeg `-vf` filtergraph that erases/redacts `rect` on a `(video_w, video_h)` video."""
    vw = max(16, int(video_w))
    vh = max(16, int(video_h))
    bounded = rect.intersected(QRect(0, 0, vw, vh))
    if bounded.isEmpty():
        bounded = QRect(vw // 4, vh // 4, vw // 2, vh // 2)

    x = max(0, min(vw - 4, bounded.left()))
    y = max(0, min(vh - 4, bounded.top()))
    w = max(4, min(vw - x, bounded.width()))
    h = max(4, min(vh - y, bounded.height()))
    s = max(10, min(100, int(strength)))

    enable_opt = ""
    if trim_range_ms is not None:
        in_ms, out_ms = trim_range_ms
        if out_ms > in_ms:
            enable_opt = f":enable='between(t,{in_ms / 1000.0:.3f},{out_ms / 1000.0:.3f})'"

    even_tail = "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"

    if style == "heal":
        # FFmpeg delogo requires at least a 1px border inside the video frame
        dx = max(1, min(vw - 6, x))
        dy = max(1, min(vh - 6, y))
        dw = max(4, min(vw - dx - 2, w))
        dh = max(4, min(vh - dy - 2, h))
        return f"delogo=x={dx}:y={dy}:w={dw}:h={dh}{enable_opt},{even_tail}"

    if style in ("blackout", "surround"):
        col = surround_hex if (style == "surround" and QColor(surround_hex).isValid()) else "black"
        return f"drawbox=x={x}:y={y}:w={w}:h={h}:color={col}:t=fill{enable_opt},{even_tail}"

    if style == "pixelate":
        div = max(4, round(5 + (s / 100.0) * 18))
        pw = max(2, w // div)
        ph = max(2, h // div)
        return (
            f"split[base][fg];"
            f"[fg]crop={w}:{h}:{x}:{y},scale={pw}:{ph}:flags=bilinear,scale={w}:{h}:flags=neighbor[px];"
            f"[base][px]overlay={x}:{y}{enable_opt},{even_tail}"
        )

    # Default: "blur"
    sigma = max(6, round(8 + (s / 100.0) * 38))
    return (
        f"split[base][fg];"
        f"[fg]crop={w}:{h}:{x}:{y},gblur=sigma={sigma}[bl];"
        f"[base][bl]overlay={x}:{y}{enable_opt},{even_tail}"
    )


class _InteractiveRegionPreview(QWidget):
    """Interactive preview widget where the user can drag & resize the erase region directly."""

    regionChanged = Signal(QRect)

    HANDLE_RADIUS = 6

    def __init__(self, frame: QImage, initial_rect: QRect, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(460, 250)
        self.setMouseTracking(True)
        self._frame = frame
        self._display_frame = frame
        self._region = QRect(initial_rect)
        self._drag_mode: Optional[str] = None
        self._drag_start_pt = QPoint()
        self._drag_initial_rect = QRect()

    @property
    def region(self) -> QRect:
        return self._region

    def set_region(self, rect: QRect, emit_signal: bool = False) -> None:
        bounded = rect.intersected(self._frame.rect())
        if bounded.width() >= 4 and bounded.height() >= 4:
            self._region = bounded
            if emit_signal:
                self.regionChanged.emit(self._region)
            self.update()

    def set_display_frame(self, image: QImage) -> None:
        self._display_frame = image
        self.update()

    def _target_rect(self) -> QRectF:
        if self._frame.isNull():
            return QRectF()
        fw, fh = self._frame.width(), self._frame.height()
        vw, vh = max(1, self.width() - 12), max(1, self.height() - 12)
        scale = min(vw / fw, vh / fh)
        tw, th = fw * scale, fh * scale
        return QRectF((self.width() - tw) / 2.0, (self.height() - th) / 2.0, tw, th)

    def _widget_to_image(self, pos: QPointF) -> QPoint:
        tr = self._target_rect()
        if tr.isEmpty():
            return QPoint(0, 0)
        scale = tr.width() / max(1, self._frame.width())
        ix = round((pos.x() - tr.left()) / scale)
        iy = round((pos.y() - tr.top()) / scale)
        return QPoint(
            max(0, min(self._frame.width(), ix)),
            max(0, min(self._frame.height(), iy)),
        )

    def _image_rect_to_widget(self, rect: QRect) -> QRectF:
        tr = self._target_rect()
        if tr.isEmpty():
            return QRectF()
        scale = tr.width() / max(1, self._frame.width())
        return QRectF(
            tr.left() + rect.left() * scale,
            tr.top() + rect.top() * scale,
            rect.width() * scale,
            rect.height() * scale,
        )

    @staticmethod
    def _handles(r: QRectF) -> dict[str, QPointF]:
        cx, cy = r.center().x(), r.center().y()
        return {
            "nw": r.topLeft(),
            "ne": r.topRight(),
            "sw": r.bottomLeft(),
            "se": r.bottomRight(),
            "n": QPointF(cx, r.top()),
            "s": QPointF(cx, r.bottom()),
            "w": QPointF(r.left(), cy),
            "e": QPointF(r.right(), cy),
        }

    def _hit_test(self, pos: QPointF) -> Optional[str]:
        rw = self._image_rect_to_widget(self._region)
        for name, pt in self._handles(rw).items():
            if (pos - pt).manhattanLength() <= self.HANDLE_RADIUS * 2.3:
                return name
        if rw.contains(pos):
            return "move"
        return "new"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        hit = self._hit_test(pos)
        self._drag_mode = hit
        self._drag_start_pt = self._widget_to_image(pos)
        self._drag_initial_rect = QRect(self._region)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._drag_mode is None:
            hit = self._hit_test(pos)
            cursors = {
                "nw": Qt.CursorShape.SizeFDiagCursor,
                "se": Qt.CursorShape.SizeFDiagCursor,
                "ne": Qt.CursorShape.SizeBDiagCursor,
                "sw": Qt.CursorShape.SizeBDiagCursor,
                "n": Qt.CursorShape.SizeVerCursor,
                "s": Qt.CursorShape.SizeVerCursor,
                "e": Qt.CursorShape.SizeHorCursor,
                "w": Qt.CursorShape.SizeHorCursor,
                "move": Qt.CursorShape.SizeAllCursor,
            }
            self.setCursor(cursors.get(hit, Qt.CursorShape.CrossCursor))
            return

        cur = self._widget_to_image(pos)
        fw, fh = self._frame.width(), self._frame.height()
        r = QRect(self._drag_initial_rect)

        if self._drag_mode == "new":
            x1, x2 = sorted((self._drag_start_pt.x(), cur.x()))
            y1, y2 = sorted((self._drag_start_pt.y(), cur.y()))
            if x2 - x1 >= 6 and y2 - y1 >= 6:
                self._region = QRect(x1, y1, x2 - x1, y2 - y1)
        elif self._drag_mode == "move":
            dx = cur.x() - self._drag_start_pt.x()
            dy = cur.y() - self._drag_start_pt.y()
            nx = max(0, min(fw - r.width(), r.left() + dx))
            ny = max(0, min(fh - r.height(), r.top() + dy))
            self._region = QRect(nx, ny, r.width(), r.height())
        else:
            left, top, right, bottom = r.left(), r.top(), r.left() + r.width(), r.top() + r.height()
            if "w" in self._drag_mode:
                left = min(cur.x(), right - 6)
            if "e" in self._drag_mode:
                right = max(cur.x(), left + 6)
            if "n" in self._drag_mode:
                top = min(cur.y(), bottom - 6)
            if "s" in self._drag_mode:
                bottom = max(cur.y(), top + 6)
            self._region = QRect(
                max(0, left),
                max(0, top),
                max(6, min(fw, right) - max(0, left)),
                max(6, min(fh, bottom) - max(0, top)),
            )

        self.regionChanged.emit(self._region)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_mode = None

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.fillRect(self.rect(), QColor(15, 23, 42))

        tr = self._target_rect()
        if tr.isEmpty():
            painter.end()
            return
        painter.drawImage(tr, self._display_frame, QRectF(self._display_frame.rect()))

        rw = self._image_rect_to_widget(self._region)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 200), 2))
        painter.drawRect(rw)
        painter.setPen(QPen(QColor(248, 113, 113), 1.8, Qt.PenStyle.DashLine))
        painter.drawRect(rw)

        painter.setBrush(QColor(248, 113, 113))
        painter.setPen(QPen(QColor(15, 23, 42), 1.5))
        for pt in self._handles(rw).values():
            painter.drawEllipse(pt, 5.0, 5.0)

        badge = f"🧹 {self._region.width()} × {self._region.height()} px  (Drag box or handles)"
        font = QFont(self.font())
        font.setPointSize(8)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(badge) + 14
        th = fm.height() + 6
        bx = max(tr.left(), min(tr.right() - tw, rw.left()))
        by = rw.top() - th - 4
        if by < tr.top():
            by = min(tr.bottom() - th, rw.bottom() + 4)
        brect = QRectF(bx, by, tw, th)
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.setPen(QPen(QColor(248, 113, 113, 190), 1))
        painter.drawRoundedRect(brect, 4, 4)
        painter.setPen(QColor(241, 245, 249))
        painter.drawText(brect, Qt.AlignmentFlag.AlignCenter, badge)
        painter.end()


class EraseRegionDialog(QDialog):
    """Interactive dialog to select & erase/blur/pixelate/heal a region on an image or video."""

    def __init__(
        self,
        frame: Optional[QImage] = None,
        *,
        initial_rect: Optional[QRect] = None,
        initial_style: str = "blur",
        initial_strength: int = 60,
        is_video: bool = False,
        trim_range_label: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Erase / Redact Region — HakuView" if not is_video else "Erase / Redact Video Region — HakuView")
        self.setMinimumWidth(520)

        self._is_video = is_video
        self.save_as_copy: bool = False
        self._syncing_spins = False

        if frame is not None and not frame.isNull():
            self._frame = frame
        else:
            fallback = QImage(640, 360, QImage.Format.Format_RGB32)
            fallback.fill(QColor("#1e293b"))
            self._frame = fallback

        fw, fh = self._frame.width(), self._frame.height()
        if initial_rect is not None and not initial_rect.isEmpty() and initial_rect != self._frame.rect():
            start_rect = initial_rect.intersected(self._frame.rect())
        else:
            rw, rh = max(16, fw // 3), max(16, fh // 3)
            start_rect = QRect((fw - rw) // 2, (fh - rh) // 2, rw, rh)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # 1. Interactive Region Preview
        prev_group = QGroupBox("Drag Over the Face / Object to Delete", self)
        prev_layout = QVBoxLayout(prev_group)
        self.preview = _InteractiveRegionPreview(self._frame, start_rect, self)
        self.preview.regionChanged.connect(self._on_preview_region_changed)
        prev_layout.addWidget(self.preview)
        root.addWidget(prev_group)

        # 2. Erase Style & Strength
        ctrl_group = QGroupBox("Erase / Redaction Mode", self)
        form = QFormLayout(ctrl_group)

        self.combo_style = QComboBox(self)
        for style_id, label in ERASE_STYLES:
            self.combo_style.addItem(label, style_id)
        idx = self.combo_style.findData(initial_style)
        if idx >= 0:
            self.combo_style.setCurrentIndex(idx)
        self.combo_style.currentIndexChanged.connect(self._on_style_changed)
        form.addRow("Method:", self.combo_style)

        strength_row = QHBoxLayout()
        self.slider_strength = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_strength.setRange(10, 100)
        self.slider_strength.setValue(max(10, min(100, int(initial_strength))))
        self.lbl_strength = QLabel(f"{self.slider_strength.value()}%", self)
        self.lbl_strength.setFixedWidth(42)
        self.slider_strength.valueChanged.connect(self._on_strength_changed)
        strength_row.addWidget(self.slider_strength, stretch=1)
        strength_row.addWidget(self.lbl_strength)
        form.addRow("Intensity:", strength_row)

        box_row = QHBoxLayout()
        self.spin_x = self._make_spin(0, max(0, fw - 4), start_rect.x(), "X:")
        self.spin_y = self._make_spin(0, max(0, fh - 4), start_rect.y(), "Y:")
        self.spin_w = self._make_spin(4, fw, start_rect.width(), "W:")
        self.spin_h = self._make_spin(4, fh, start_rect.height(), "H:")
        for lbl_txt, sp in (("X:", self.spin_x), ("Y:", self.spin_y), ("W:", self.spin_w), ("H:", self.spin_h)):
            box_row.addWidget(QLabel(lbl_txt, self))
            box_row.addWidget(sp)
        form.addRow("Region (px):", box_row)

        self.chk_trim_range = QCheckBox(
            f"Only erase between marked In/Out points ({trim_range_label})" if trim_range_label else "Only erase between In/Out points",
            self,
        )
        self.chk_trim_range.setVisible(bool(is_video and trim_range_label))
        self.chk_trim_range.setChecked(bool(is_video and trim_range_label))
        if is_video and trim_range_label:
            form.addRow("Timing:", self.chk_trim_range)

        root.addWidget(ctrl_group)

        # 3. Dialog Action Buttons
        btn_row = QHBoxLayout()
        if not is_video:
            self.btn_save_copy = QPushButton("💾 Save as New Image", self)
            self.btn_save_copy.setToolTip("Write <name>_redacted.<ext> next to the original without modifying the open image")
            self.btn_save_copy.clicked.connect(self._accept_as_copy)
            btn_row.addWidget(self.btn_save_copy)
        btn_row.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("🎬 Export Redacted Video" if is_video else "✓ Delete / Erase Region")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        root.addLayout(btn_row)

        self._on_style_changed()

    def _make_spin(self, lo: int, hi: int, val: int, _label: str) -> QSpinBox:
        sp = QSpinBox(self)
        sp.setRange(lo, hi)
        sp.setValue(val)
        sp.valueChanged.connect(self._on_spins_changed)
        return sp

    def _accept_as_copy(self) -> None:
        self.save_as_copy = True
        self.accept()

    def spec(self) -> EraseRegionSpec:
        return EraseRegionSpec(
            rect=QRect(self.preview.region),
            style=str(self.combo_style.currentData() or "blur"),
            strength=int(self.slider_strength.value()),
            only_trim_range=not self.chk_trim_range.isHidden() and self.chk_trim_range.isChecked(),
        )

    def _on_preview_region_changed(self, rect: QRect) -> None:
        self._syncing_spins = True
        try:
            self.spin_x.setValue(rect.x())
            self.spin_y.setValue(rect.y())
            self.spin_w.setValue(rect.width())
            self.spin_h.setValue(rect.height())
        finally:
            self._syncing_spins = False
        self._refresh_preview()

    def _on_spins_changed(self) -> None:
        if self._syncing_spins:
            return
        r = QRect(self.spin_x.value(), self.spin_y.value(), self.spin_w.value(), self.spin_h.value())
        self.preview.set_region(r, emit_signal=False)
        self._refresh_preview()

    def _on_style_changed(self, _index: int = 0) -> None:
        style = str(self.combo_style.currentData() or "blur")
        self.slider_strength.setEnabled(style in ("blur", "pixelate"))
        self._refresh_preview()

    def _on_strength_changed(self, val: int) -> None:
        self.lbl_strength.setText(f"{val}%")
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        sp = self.spec()
        rendered = erase_image_region(self._frame, sp.rect, style=sp.style, strength=sp.strength)
        self.preview.set_display_frame(rendered)
