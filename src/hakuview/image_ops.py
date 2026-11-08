"""Fast image loading, background preloading cache, and non-destructive rotate/flip/crop operations."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import importlib.util
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import List, Optional, Tuple

from PIL import Image, ImageFilter, ImageOps, JpegImagePlugin
from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QRunnable, QSize, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QImageWriter, QPainter, QPainterPath, QPen, QPolygonF, QTransform

from .formatting import human_size
from .proc import no_window


def _register_optional_plugins() -> None:
    """Enable HEIC/HEIF decoding through Pillow when the optional pillow-heif package is installed."""
    try:
        import pillow_heif  # type: ignore[import-not-found]

        pillow_heif.register_heif_opener()
    except Exception:
        pass


_register_optional_plugins()


def describe_load_failure(path: Path | str) -> str:
    """Human-readable reason an image couldn't be decoded, for the canvas message."""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        return f"The file can't be read: {exc.strerror or exc}."
    if size == 0:
        return "The file is empty (0 bytes)."
    ext = p.suffix.lower()
    if ext in (".heic", ".heif"):
        if importlib.util.find_spec("pillow_heif") is None:
            return (
                "HEIC images need the optional 'pillow-heif' package.\n"
                "Install it with:  uv tool install --force --with pillow-heif git+https://github.com/hakanu/hakuview.git"
            )
    try:
        with Image.open(p) as im:
            im.load()
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        if "cannot identify image file" in detail:
            return "This doesn't look like a valid image (unknown format or corrupted file)."
        return f"The image is damaged or unsupported: {detail}"
    return "The image could not be decoded."


def load_qimage_fast(path: Path | str) -> Optional[QImage]:
    """Load a QImage rapidly using Qt's native C++ QImageReader with Pillow fallback."""
    file_path = str(path)
    reader = QImageReader(file_path)
    reader.setAutoTransform(True)  # Respect EXIF orientation tags automatically
    image = reader.read()
    if not image.isNull():
        return image

    # Fallback to Pillow for exotic formats (e.g. certain TIFF/WebP/AVIF/HEIC/ICO files)
    try:
        with Image.open(file_path) as pil_img:
            pil_img = ImageOps.exif_transpose(pil_img)
            if pil_img.mode not in ("RGB", "RGBA"):
                pil_img = pil_img.convert("RGBA" if "A" in pil_img.getbands() else "RGB")
            data = pil_img.tobytes("raw", pil_img.mode)
            fmt = (
                QImage.Format.Format_RGBA8888
                if pil_img.mode == "RGBA"
                else QImage.Format.Format_RGB888
            )
            bytes_per_line = pil_img.width * (4 if pil_img.mode == "RGBA" else 3)
            qimg = QImage(data, pil_img.width, pil_img.height, bytes_per_line, fmt)
            return qimg.copy()  # Detach from temporary buffer
    except Exception:
        return None


def is_animated_image(path: Path | str) -> bool:
    """True if the file holds more than one frame (animated GIF / WebP / APNG)."""
    reader = QImageReader(str(path))
    return reader.supportsAnimation() and reader.imageCount() > 1


def rotate_qimage(image: QImage, degrees: int) -> QImage:
    """Rotate a QImage by 90, -90, or 180 degrees."""
    transform = QTransform()
    transform.rotate(degrees)
    return image.transformed(transform, Qt.TransformationMode.SmoothTransformation)


def flip_qimage(image: QImage, horizontal: bool = False, vertical: bool = False) -> QImage:
    """Flip a QImage horizontally and/or vertically."""
    if not (horizontal or vertical):
        return image
    if not hasattr(image, "flipped"):  # Qt < 6.9 only has the (now deprecated) mirrored()
        return image.mirrored(horizontal, vertical)
    if horizontal and vertical:
        return image.flipped(Qt.Orientation.Horizontal | Qt.Orientation.Vertical)
    return image.flipped(Qt.Orientation.Horizontal if horizontal else Qt.Orientation.Vertical)


def crop_qimage(image: QImage, rect: QRect) -> QImage:
    """Crop a QImage to a bounded rectangle inside the image dimensions."""
    bounded = rect.intersected(image.rect())
    if bounded.isEmpty() or bounded.width() < 2 or bounded.height() < 2:
        return image
    return image.copy(bounded)


def paint_overlay_on_painter(
    painter: QPainter,
    overlay: QImage,
    target_rect: QRectF,
    *,
    opacity: int = 255,
    rounded_pct: int = 0,
    border: bool = False,
) -> None:
    """Paint `overlay` inside `target_rect` with optional opacity, rounded corners, and photo frame + shadow."""
    if overlay is None or overlay.isNull() or target_rect.isEmpty():
        return
    alpha = max(0, min(255, int(opacity))) / 255.0
    if alpha <= 0.0:
        return

    min_side = min(target_rect.width(), target_rect.height())
    radius = min_side * (max(0, min(50, int(rounded_pct))) / 100.0)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    if border:
        shadow_off = max(2.0, min_side * 0.022)
        shadow_rect = target_rect.translated(shadow_off, shadow_off)
        painter.setOpacity(alpha * 0.55)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 180))
        if radius > 0.5:
            painter.drawRoundedRect(shadow_rect, radius, radius)
        else:
            painter.drawRect(shadow_rect)

    painter.setOpacity(alpha)
    if radius > 0.5:
        clip = QPainterPath()
        clip.addRoundedRect(target_rect, radius, radius)
        painter.save()
        painter.setClipPath(clip)
        painter.drawImage(target_rect, overlay, QRectF(overlay.rect()))
        painter.restore()
    else:
        painter.drawImage(target_rect, overlay, QRectF(overlay.rect()))

    if border:
        pen_w = max(1.5, min_side * 0.012)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 235), pen_w))
        if radius > 0.5:
            painter.drawRoundedRect(target_rect, radius, radius)
        else:
            painter.drawRect(target_rect)

    painter.restore()


def composite_overlay_image(
    base: QImage,
    overlay: QImage,
    rect: QRect,
    *,
    opacity: int = 255,
    rounded_pct: int = 0,
    border: bool = False,
) -> QImage:
    """Return a copy of `base` with `overlay` stamped onto `rect`."""
    if base is None or base.isNull() or overlay is None or overlay.isNull() or rect.isEmpty():
        return base
    out = base.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(out)
    paint_overlay_on_painter(
        painter,
        overlay,
        QRectF(rect),
        opacity=opacity,
        rounded_pct=rounded_pct,
        border=border,
    )
    painter.end()
    if base.format() in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32):
        return out.convertToFormat(base.format())
    return out


def stitch_images(base: QImage, other: QImage, direction: str = "horizontal") -> QImage:
    """Stitch `other` next to (`horizontal`) or below (`vertical`) `base`, matching edge length."""
    if base is None or base.isNull() or other is None or other.isNull():
        return base
    bw, bh = base.width(), base.height()
    ow, oh = max(1, other.width()), max(1, other.height())
    if direction == "vertical":
        new_ow = bw
        new_oh = max(1, round(oh * (bw / ow)))
        scaled = other.scaled(QSize(new_ow, new_oh), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        canvas = QImage(bw, bh + new_oh, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawImage(0, 0, base)
        painter.drawImage(0, bh, scaled)
        painter.end()
    else:
        new_oh = bh
        new_ow = max(1, round(ow * (bh / oh)))
        scaled = other.scaled(QSize(new_ow, new_oh), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        canvas = QImage(bw + new_ow, bh, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawImage(0, 0, base)
        painter.drawImage(bw, 0, scaled)
        painter.end()
    if base.format() in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32):
        return canvas.convertToFormat(base.format())
    return canvas


@dataclass
class DrawStroke:
    """A single freehand or geometric annotation stroke in image pixel coordinates."""

    tool: str = "pen"  # "pen", "highlighter", "arrow", "rect", "ellipse", "line"
    color: str = "#ef4444"
    size: int = 6
    points: List[QPointF] = field(default_factory=list)


def render_draw_stroke(
    painter: QPainter,
    stroke: DrawStroke,
    *,
    scale: float = 1.0,
    offset: QPointF = QPointF(0.0, 0.0),
) -> None:
    """Render `stroke` onto `painter`, optionally mapped from image pixels via `offset + pt * scale`."""
    if not stroke.points:
        return

    def _map(pt: QPointF) -> QPointF:
        return QPointF(offset.x() + pt.x() * scale, offset.y() + pt.y() * scale)

    pts = [_map(p) for p in stroke.points]
    c = QColor(stroke.color) if QColor(stroke.color).isValid() else QColor("#ef4444")
    pen_w = max(1.5, float(stroke.size) * scale)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    if stroke.tool == "highlighter":
        c.setAlpha(95)
        pen = QPen(c, pen_w * 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(pts) == 1:
            painter.drawPoint(pts[0])
        else:
            path = QPainterPath(pts[0])
            for p in pts[1:]:
                path.lineTo(p)
            painter.drawPath(path)

    elif stroke.tool == "pen":
        pen = QPen(c, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(pts) == 1:
            painter.setBrush(c)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(pts[0], pen_w / 2.0, pen_w / 2.0)
        else:
            path = QPainterPath(pts[0])
            for p in pts[1:]:
                path.lineTo(p)
            painter.drawPath(path)

    elif stroke.tool == "line":
        p0, p1 = pts[0], pts[-1]
        pen = QPen(c, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawLine(p0, p1)

    elif stroke.tool == "arrow":
        p0, p1 = pts[0], pts[-1]
        dx = p1.x() - p0.x()
        dy = p1.y() - p0.y()
        length = math.hypot(dx, dy)
        pen = QPen(c, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if length < 2.0:
            painter.drawPoint(p0)
        else:
            head_len = max(12.0 * scale, min(length * 0.65, pen_w * 3.6))
            head_angle = math.radians(28.0)
            angle = math.atan2(dy, dx)
            # Draw shaft slightly inset so it doesn't poke through the arrowhead tip
            shaft_end = QPointF(
                p1.x() - math.cos(angle) * (head_len * 0.55),
                p1.y() - math.sin(angle) * (head_len * 0.55),
            )
            painter.drawLine(p0, shaft_end)
            left_wing = QPointF(
                p1.x() - head_len * math.cos(angle - head_angle),
                p1.y() - head_len * math.sin(angle - head_angle),
            )
            right_wing = QPointF(
                p1.x() - head_len * math.cos(angle + head_angle),
                p1.y() - head_len * math.sin(angle + head_angle),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c)
            painter.drawPolygon(QPolygonF([p1, left_wing, right_wing]))

    elif stroke.tool in ("rect", "ellipse"):
        p0, p1 = pts[0], pts[-1]
        rect = QRectF(
            min(p0.x(), p1.x()),
            min(p0.y(), p1.y()),
            abs(p1.x() - p0.x()),
            abs(p1.y() - p0.y()),
        )
        pen = QPen(c, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if stroke.tool == "ellipse":
            painter.drawEllipse(rect)
        else:
            rad = min(rect.width(), rect.height()) * 0.08
            painter.drawRoundedRect(rect, rad, rad)

    painter.restore()


def apply_draw_strokes(base: QImage, strokes: List[DrawStroke]) -> QImage:
    """Return a copy of `base` with all `strokes` rendered onto it."""
    if base is None or base.isNull() or not strokes:
        return base
    out = base.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(out)
    for s in strokes:
        render_draw_stroke(painter, s, scale=1.0, offset=QPointF(0.0, 0.0))
    painter.end()
    if base.format() in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32):
        return out.convertToFormat(base.format())
    return out


def resize_qimage(
    image: QImage,
    width: int,
    height: int,
    *,
    mode: str = "scale",
    resample: str = "lanczos",
    bg_color: str = "#0f172a",
    blur_sigma: int = 28,
) -> QImage:
    """Resize or reframe `image` to `(width, height)`.

    Modes:
    - "scale": Direct resample to `(width, height)` using `resample` ("lanczos", "bilinear", "nearest").
    - "pad_blur": Aspect-fit inside `(width, height)` over a Gaussian-blurred + dominant-tinted backdrop.
    - "pad_solid": Aspect-fit inside `(width, height)` over a solid `bg_color` canvas.
    - "crop_fill": Aspect-expand to cover `(width, height)` and center-crop.
    """
    if image is None or image.isNull():
        return image
    tw = max(1, int(width))
    th = max(1, int(height))
    pil_src = _qimage_to_pil(image)

    resample_map = {
        "lanczos": Image.Resampling.LANCZOS,
        "bilinear": Image.Resampling.BILINEAR,
        "nearest": Image.Resampling.NEAREST,
    }
    pil_filter = resample_map.get(resample, Image.Resampling.LANCZOS)

    if mode == "scale":
        resized = pil_src.resize((tw, th), resample=pil_filter)
    elif mode == "crop_fill":
        src_w, src_h = max(1, pil_src.width), max(1, pil_src.height)
        scale = max(tw / src_w, th / src_h)
        sw = max(tw, int(round(src_w * scale)))
        sh = max(th, int(round(src_h * scale)))
        scaled = pil_src.resize((sw, sh), resample=pil_filter)
        left = max(0, (sw - tw) // 2)
        top = max(0, (sh - th) // 2)
        resized = scaled.crop((left, top, left + tw, top + th))
    elif mode in ("pad_blur", "pad_solid"):
        src_w, src_h = max(1, pil_src.width), max(1, pil_src.height)
        fit_scale = min(tw / src_w, th / src_h)
        fw = max(1, min(tw, int(round(src_w * fit_scale))))
        fh = max(1, min(th, int(round(src_h * fit_scale))))
        fg = pil_src.resize((fw, fh), resample=pil_filter)

        qc = QColor(bg_color) if QColor(bg_color).isValid() else QColor("#0f172a")
        if mode == "pad_solid":
            bg = Image.new("RGBA", (tw, th), (qc.red(), qc.green(), qc.blue(), 255))
        else:
            # CapCut-style blurred cover backdrop tinted with bg_color
            cov_scale = max(tw / src_w, th / src_h)
            cw = max(tw, int(round(src_w * cov_scale)))
            ch = max(th, int(round(src_h * cov_scale)))
            bg_cov = pil_src.convert("RGB").resize((cw, ch), resample=Image.Resampling.BILINEAR)
            bx = max(0, (cw - tw) // 2)
            by = max(0, (ch - th) // 2)
            bg_crop = bg_cov.crop((bx, by, bx + tw, by + th))
            sigma = max(4, min(80, int(blur_sigma)))
            blurred = bg_crop.filter(ImageFilter.GaussianBlur(radius=sigma * 0.45))
            tint = Image.new("RGB", (tw, th), (qc.red(), qc.green(), qc.blue()))
            bg = Image.blend(blurred, tint, alpha=0.28).convert("RGBA")

        ox = (tw - fw) // 2
        oy = (th - fh) // 2
        bg.paste(fg, (ox, oy), fg)
        resized = bg
    else:
        resized = pil_src.resize((tw, th), resample=pil_filter)

    rgba_bytes = resized.convert("RGBA").tobytes("raw", "RGBA")
    out = QImage(rgba_bytes, tw, th, tw * 4, QImage.Format.Format_RGBA8888).copy()
    if image.format() in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32):
        return out.convertToFormat(image.format())
    return out


def save_qimage_atomic(image: QImage, target_path: Path | str, quality: int = 95) -> None:
    """Atomically write QImage to disk so a crash or interruption never corrupts the file."""
    dest = Path(target_path).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    suffix = dest.suffix if dest.suffix else ".png"

    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.stem}_tmp_", suffix=suffix, dir=str(dest.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)

    try:
        writer = QImageWriter(str(tmp_path))
        if quality >= 0:
            writer.setQuality(quality)
        ok = writer.write(image)
        if not ok:
            # Fallback to Pillow writer
            buffer_img = image.convertToFormat(QImage.Format.Format_RGBA8888)
            ptr = buffer_img.constBits()
            pil_img = Image.frombuffer(
                "RGBA",
                (buffer_img.width(), buffer_img.height()),
                bytes(ptr),
                "raw",
                "RGBA",
                0,
                1,
            )
            if suffix.lower() in (".jpg", ".jpeg", ".bmp"):
                pil_img = pil_img.convert("RGB")
            pil_img.save(str(tmp_path), quality=quality)

        # Preserve original file permissions if overwriting
        if dest.exists():
            try:
                st = dest.stat()
                os.chmod(tmp_path, st.st_mode)
            except OSError:
                pass

        os.replace(str(tmp_path), str(dest))
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _qimage_to_pil(image: QImage) -> Image.Image:
    """Convert a QImage into a detached Pillow RGBA Image."""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width, height = rgba.width(), rgba.height()
    bpl = rgba.bytesPerLine()
    raw = bytes(rgba.constBits())
    pil_img = Image.frombuffer("RGBA", (width, height), raw, "raw", "RGBA", bpl, 1).copy()
    return pil_img


def color_lut_params(brightness: int, contrast: int) -> Tuple[float, float]:
    """(contrast factor, brightness offset) for: out = clip(round(factor * (x - 128 + offset) + 128))."""
    c = max(-99, min(99, int(contrast))) * 2.55
    factor = (259.0 * (c + 255.0)) / (255.0 * (259.0 - c))
    return factor, float(brightness) * 1.28


def color_lut(brightness: int, contrast: int) -> List[int]:
    """256-entry lookup table for brightness (-100..100) and contrast (-100..100), 0 = neutral."""
    factor, offset = color_lut_params(brightness, contrast)
    return [max(0, min(255, int(round(factor * (x - 128.0 + offset) + 128.0)))) for x in range(256)]


_IDENTITY_LUT = list(range(256))


def apply_color_lut(image: QImage, lut: List[int]) -> QImage:
    """Map R, G and B through `lut` (alpha untouched). Fast enough for live video frames."""
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = rgba.width(), rgba.height()
    pil_img = Image.frombuffer("RGBA", (w, h), rgba.constBits(), "raw", "RGBA", rgba.bytesPerLine(), 1)
    mapped = pil_img.point(lut * 3 + _IDENTITY_LUT)
    # Paste straight into the result QImage's memory: half the cost of tobytes() + QImage.copy().
    out = QImage(w, h, QImage.Format.Format_RGBA8888)
    target = Image.frombuffer("RGBA", (w, h), out.bits(), "raw", "RGBA", out.bytesPerLine(), 1)
    target.readonly = 0  # write into the buffer instead of copy-on-write
    target.paste(mapped)
    return out


def adjust_qimage_color(image: QImage, brightness: int, contrast: int) -> QImage:
    """Return `image` with brightness/contrast applied (the same image if both are 0)."""
    if brightness == 0 and contrast == 0:
        return image
    return apply_color_lut(image, color_lut(brightness, contrast))


def _next_variant_path(source_path: Path, tag: str, ext: str) -> Path:
    """Generate a unique variant filename in the same folder without overwriting existing files."""
    if not ext.startswith("."):
        ext = f".{ext}"
    parent = source_path.parent
    stem = source_path.stem
    candidate = parent / f"{stem}_{tag}{ext}"
    counter = 2
    while candidate.exists():
        candidate = parent / f"{stem}_{tag}_{counter}{ext}"
        counter += 1
    return candidate


class CompressionError(Exception):
    """A compression preset can't produce a useful variant; the message is user-facing."""


def _jpegtran_optimize(src_path: Path, out_path: Path) -> None:
    """Truly lossless JPEG re-pack (optimized Huffman tables + progressive), keeping metadata."""
    jpegtran = shutil.which("jpegtran")
    if not jpegtran:
        raise CompressionError(
            "Lossless JPEG optimization needs the 'jpegtran' tool, which isn't installed.\n\n"
            "Ubuntu/Debian:  sudo apt install libjpeg-turbo-progs\n"
            "Windows:  install libjpeg-turbo and add jpegtran to PATH.\n\n"
            "Re-encoding instead would lose quality, so no variant was saved."
        )
    # -copy all keeps EXIF (notably Orientation, so the variant isn't shown rotated).
    res = subprocess.run(
        [jpegtran, "-copy", "all", "-optimize", "-progressive", "-outfile", str(out_path), str(src_path)],
        check=False,
        capture_output=True,
        **no_window(),
    )
    if res.returncode != 0 or not out_path.exists():
        out_path.unlink(missing_ok=True)
        err = res.stderr.decode("utf-8", errors="replace").strip()[-300:]
        raise CompressionError(f"jpegtran failed:\n{err}")


def _save_jpeg_like_source(image: Image.Image, src_path: Path, out_path: Path) -> None:
    """Encode `image` as JPEG with the same quantization tables/subsampling as `src_path`."""
    kwargs: dict = {"quality": 95}
    try:
        with Image.open(src_path) as src:
            if src.format == "JPEG":
                kwargs = {"qtables": src.quantization}
                sampling = JpegImagePlugin.get_sampling(src)
                if sampling >= 0:
                    kwargs["subsampling"] = sampling
    except OSError:
        pass
    image.convert("RGB").save(str(out_path), format="JPEG", optimize=True, progressive=True, **kwargs)


COMPRESSION_MODES = [
    ("Lossless (Auto)", "lossless"),
    ("Lossless WebP", "lossless_webp"),
    ("Smart Balanced (Q85)", "smart"),
    ("Compact WebP (Q80)", "webp_q80"),
]


def compress_image_as_variant(
    doc: "ImageDocument",
    mode: str = "lossless",
) -> Tuple[Path, int, int]:
    """Compress the current image and save it as a new variant file in the same directory.

    Returns:
        Tuple of (variant_path, original_size_bytes, new_size_bytes).
    """
    src_path = doc.path
    try:
        orig_bytes = src_path.stat().st_size
    except OSError:
        orig_bytes = 0

    orig_ext = src_path.suffix.lower() or ".png"
    pil_img = _qimage_to_pil(doc.current)
    has_alpha = doc.current.hasAlphaChannel()

    if mode == "lossless_webp":
        out_path = _next_variant_path(src_path, "lossless", ".webp")
        out_img = pil_img if has_alpha else pil_img.convert("RGB")
        out_img.save(str(out_path), format="WEBP", lossless=True, quality=100, method=6)

    elif mode == "webp_q80":
        out_path = _next_variant_path(src_path, "q80", ".webp")
        out_img = pil_img if has_alpha else pil_img.convert("RGB")
        out_img.save(str(out_path), format="WEBP", lossless=False, quality=80, method=6)

    elif mode == "smart":
        target_ext = orig_ext if orig_ext in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
        out_path = _next_variant_path(src_path, "q85", target_ext)
        if target_ext in (".jpg", ".jpeg"):
            pil_img.convert("RGB").save(
                str(out_path), format="JPEG", quality=85, optimize=True, progressive=True
            )
        elif target_ext == ".webp":
            out_img = pil_img if has_alpha else pil_img.convert("RGB")
            out_img.save(str(out_path), format="WEBP", quality=85, method=6)
        else:
            # PNG smart quantization (256-color adaptive palette + max deflate)
            quantized = pil_img.quantize(colors=256, method=Image.Quantize.FASTOCTREE)
            quantized.save(str(out_path), format="PNG", optimize=True, compress_level=9)

    else:
        # Default: "lossless" (same format or PNG for raw bitmaps)
        if orig_ext in (".jpg", ".jpeg"):
            out_path = _next_variant_path(src_path, "lossless", orig_ext)
            if doc.modified:
                # Edited pixels have to be re-encoded; reuse the source's quantization tables and
                # chroma subsampling so the variant keeps the original quality instead of inflating it.
                _save_jpeg_like_source(pil_img, src_path, out_path)
            else:
                _jpegtran_optimize(src_path, out_path)
        elif orig_ext == ".webp":
            out_path = _next_variant_path(src_path, "lossless", ".webp")
            out_img = pil_img if has_alpha else pil_img.convert("RGB")
            out_img.save(str(out_path), format="WEBP", lossless=True, quality=100, method=6)
        elif orig_ext in (".tif", ".tiff"):
            out_path = _next_variant_path(src_path, "lossless", orig_ext)
            out_img = pil_img if has_alpha else pil_img.convert("RGB")
            out_img.save(str(out_path), format="TIFF", compression="tiff_adobe_deflate")
        else:
            # PNG or uncompressed formats (.bmp, .ppm, etc.) -> lossless max-deflate PNG
            out_path = _next_variant_path(src_path, "lossless", ".png")
            out_img = pil_img if has_alpha else pil_img.convert("RGB")
            out_img.save(str(out_path), format="PNG", optimize=True, compress_level=9)

    new_bytes = out_path.stat().st_size if out_path.exists() else 0
    if orig_bytes and new_bytes >= orig_bytes:
        # A "compressed" copy that is larger than the original is useless; don't leave it behind.
        out_path.unlink(missing_ok=True)
        label = dict((mode_id, name) for name, mode_id in COMPRESSION_MODES).get(mode, mode)
        raise CompressionError(
            f"{label} would not make this file smaller "
            f"({human_size(orig_bytes)} → {human_size(new_bytes)}), so no variant was saved.\n\n"
            "The image is probably already well compressed. Try a different preset."
        )
    return out_path, orig_bytes, new_bytes


LOSSLESS_JPEG_TRANSFORMS = ("rot90", "rot180", "rot270", "flip_h", "flip_v", "transpose", "transverse")
_JPEGTRAN_ARGS = {
    "rot90": ["-rotate", "90"],
    "rot180": ["-rotate", "180"],
    "rot270": ["-rotate", "270"],
    "flip_h": ["-flip", "horizontal"],
    "flip_v": ["-flip", "vertical"],
    "transpose": ["-transpose"],
    "transverse": ["-transverse"],
}


class ImageDocument:
    """Tracks the currently displayed image, its undo stack, and modified flag.

    `ops` records the edits since the last save ("rotate_left", "flip_h", "crop", ...) so a
    rotate/flip-only edit of a JPEG can be saved losslessly with jpegtran.
    """

    MAX_UNDO = 10

    def __init__(self, path: Path, image: QImage) -> None:
        self.path: Path = path
        self.original: QImage = image
        self.current: QImage = image
        self.undo_stack: List[QImage] = []
        self.ops: List[str] = []
        self.modified: bool = False

    def _push_undo(self, op: str) -> None:
        self.undo_stack.append(self.current)
        self.ops.append(op)
        if len(self.undo_stack) > self.MAX_UNDO:
            self.undo_stack.pop(0)
        self.modified = True

    def rotate_left(self) -> None:
        self._push_undo("rotate_left")
        self.current = rotate_qimage(self.current, -90)

    def rotate_right(self) -> None:
        self._push_undo("rotate_right")
        self.current = rotate_qimage(self.current, 90)

    def flip_horizontal(self) -> None:
        self._push_undo("flip_h")
        self.current = flip_qimage(self.current, horizontal=True, vertical=False)

    def flip_vertical(self) -> None:
        self._push_undo("flip_v")
        self.current = flip_qimage(self.current, horizontal=False, vertical=True)

    def crop(self, rect: QRect) -> bool:
        bounded = rect.intersected(self.current.rect())
        if bounded.isEmpty() or bounded.width() < 2 or bounded.height() < 2:
            return False
        if bounded == self.current.rect():
            return False
        self._push_undo("crop")
        self.current = crop_qimage(self.current, bounded)
        return True

    def apply_caption(self, spec) -> bool:
        from .caption_dialog import apply_caption_to_image

        if not getattr(spec, "text", "").strip():
            return False
        self._push_undo("caption")
        self.current = apply_caption_to_image(self.current, spec)
        return True

    def stamp_overlay(
        self,
        overlay: QImage,
        rect: QRect,
        *,
        opacity: int = 255,
        rounded_pct: int = 0,
        border: bool = False,
    ) -> bool:
        if overlay is None or overlay.isNull() or rect.isEmpty() or rect.width() < 2 or rect.height() < 2:
            return False
        self._push_undo("stamp")
        self.current = composite_overlay_image(
            self.current,
            overlay,
            rect,
            opacity=opacity,
            rounded_pct=rounded_pct,
            border=border,
        )
        return True

    def stitch_with(self, other: QImage, direction: str = "horizontal") -> bool:
        if other is None or other.isNull():
            return False
        self._push_undo("stitch")
        self.current = stitch_images(self.current, other, direction=direction)
        return True

    def erase_region(self, rect: QRect, style: str = "blur", strength: int = 60) -> bool:
        from .erase_dialog import erase_image_region

        bounded = rect.intersected(self.current.rect())
        if bounded.isEmpty() or bounded.width() < 2 or bounded.height() < 2:
            return False
        self._push_undo("erase")
        self.current = erase_image_region(self.current, bounded, style=style, strength=strength)
        return True

    def resize_image(
        self,
        width: int,
        height: int,
        *,
        mode: str = "scale",
        resample: str = "lanczos",
        bg_color: str = "#0f172a",
        blur_sigma: int = 28,
    ) -> bool:
        tw, th = max(1, int(width)), max(1, int(height))
        if mode == "scale" and tw == self.current.width() and th == self.current.height():
            return False
        self._push_undo("resize")
        self.current = resize_qimage(
            self.current,
            tw,
            th,
            mode=mode,
            resample=resample,
            bg_color=bg_color,
            blur_sigma=blur_sigma,
        )
        return True

    def apply_annotations(self, strokes: List[DrawStroke]) -> bool:
        if not strokes:
            return False
        self._push_undo("annotate")
        self.current = apply_draw_strokes(self.current, strokes)
        return True

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.current = self.undo_stack.pop()
        if self.ops:
            self.ops.pop()
        self.modified = bool(self.undo_stack)
        return True

    def reset(self) -> bool:
        if not self.modified:
            return False
        self.current = self.original
        self.undo_stack.clear()
        self.ops.clear()
        self.modified = False
        return True

    def mark_saved(self, new_path: Optional[Path] = None) -> None:
        if new_path is not None:
            self.path = new_path
        self.original = self.current
        self.undo_stack.clear()
        self.ops.clear()
        self.modified = False

    def net_transform(self) -> Optional[str]:
        """The single jpegtran transform equal to all edits so far, "identity", or None if lossy.

        Rotations and flips compose into one of 8 orientations; rather than doing the group
        algebra by hand, apply the same ops to a tiny asymmetric image and see which
        candidate transform produces the same pixels.
        """
        if any(op not in ("rotate_left", "rotate_right", "flip_h", "flip_v") for op in self.ops):
            return None
        probe = QImage(3, 2, QImage.Format.Format_RGB32)
        for i, (x, y) in enumerate((x, y) for y in range(2) for x in range(3)):
            probe.setPixel(x, y, 0xFF000000 | (i * 40 << 16) | (255 - i * 40))
        edited = probe
        for op in self.ops:
            edited = {
                "rotate_left": lambda im: rotate_qimage(im, -90),
                "rotate_right": lambda im: rotate_qimage(im, 90),
                "flip_h": lambda im: flip_qimage(im, horizontal=True),
                "flip_v": lambda im: flip_qimage(im, vertical=True),
            }[op](edited)
        candidates = {
            "identity": probe,
            "rot90": rotate_qimage(probe, 90),
            "rot180": rotate_qimage(probe, 180),
            "rot270": rotate_qimage(probe, -90),
            "flip_h": flip_qimage(probe, horizontal=True),
            "flip_v": flip_qimage(probe, vertical=True),
            "transpose": flip_qimage(rotate_qimage(probe, 90), horizontal=True),
            "transverse": flip_qimage(rotate_qimage(probe, -90), horizontal=True),
        }
        return next((name for name, im in candidates.items() if im == edited), None)


def _exif_orientation(path: Path) -> int:
    try:
        with Image.open(path) as im:
            return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def jpegtran_lossless_save(src: Path, dest: Path, transform: str) -> bool:
    """Rotate/flip a JPEG without re-encoding (atomic). False if not possible -> caller re-encodes.

    Requires jpegtran, a file whose stored pixels match what's displayed (EXIF Orientation 1),
    and -perfect: dimensions must be whole MCU blocks, otherwise jpegtran would have to drop
    or garble the edge pixels.
    """
    jpegtran = shutil.which("jpegtran")
    if not jpegtran or transform not in _JPEGTRAN_ARGS or _exif_orientation(src) != 1:
        return False
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.stem}_tmp_", suffix=dest.suffix, dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        res = subprocess.run(
            [jpegtran, "-perfect", "-copy", "all", *_JPEGTRAN_ARGS[transform], "-outfile", str(tmp), str(src)],
            capture_output=True,
            check=False,
            **no_window(),
        )
        if res.returncode != 0 or tmp.stat().st_size == 0:
            return False
        if dest.exists():
            os.chmod(tmp, dest.stat().st_mode)
        os.replace(tmp, dest)
        return True
    finally:
        tmp.unlink(missing_ok=True)


def save_jpeg_matching_source(image: QImage, src: Path, dest: Path) -> None:
    """Re-encode an edited JPEG with the source's quantization tables (atomic).

    Saving at a fixed quality 95 roughly doubles already-compressed photos (e.g. from
    messaging apps); matching the source keeps the size and quality where they were.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.stem}_tmp_", suffix=dest.suffix, dir=str(dest.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        _save_jpeg_like_source(_qimage_to_pil(image), src, tmp)
        if dest.exists():
            os.chmod(tmp, dest.stat().st_mode)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


class _PreloadSignals(QObject):
    loaded = Signal(str, QImage)


class _PreloadTask(QRunnable):
    def __init__(self, path: Path, signals: _PreloadSignals) -> None:
        super().__init__()
        self.path = path
        self.signals = signals

    def run(self) -> None:
        qimg = load_qimage_fast(self.path)
        if qimg is not None and not qimg.isNull():
            self.signals.loaded.emit(str(self.path), qimg)


class ImagePreloadCache(QObject):
    """Thread-safe LRU cache that pre-decodes neighboring images in a background thread."""

    def __init__(self, max_items: int = 8) -> None:
        super().__init__()
        self.max_items = max_items
        self._cache: OrderedDict[Tuple[str, int], QImage] = OrderedDict()
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)
        self._signals = _PreloadSignals()
        self._signals.loaded.connect(self._on_preload_finished)

    @staticmethod
    def _mtime_ns(path: Path) -> int:
        try:
            return path.stat().st_mtime_ns
        except OSError:
            return 0

    def get(self, path: Path) -> Optional[QImage]:
        key = (str(path), self._mtime_ns(path))
        with self._lock:
            img = self._cache.get(key)
            if img is not None:
                self._cache.move_to_end(key)
                return img
        # Load synchronously on cache miss and store
        img = load_qimage_fast(path)
        if img is not None:
            self.put(path, img)
        return img

    def put(self, path: Path, image: QImage) -> None:
        key = (str(path), self._mtime_ns(path))
        with self._lock:
            self._cache[key] = image
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_items:
                self._cache.popitem(last=False)

    def invalidate(self, path: Path) -> None:
        target = str(path)
        with self._lock:
            keys_to_remove = [k for k in self._cache if k[0] == target]
            for k in keys_to_remove:
                del self._cache[k]

    def preload_neighbors(self, paths: List[Path]) -> None:
        for path in paths:
            p_str = str(path)
            key = (p_str, self._mtime_ns(path))
            with self._lock:
                if key in self._cache or p_str in self._in_flight:
                    continue
                self._in_flight.add(p_str)
            task = _PreloadTask(path, self._signals)
            self._pool.start(task)

    def _on_preload_finished(self, path_str: str, image: QImage) -> None:
        path = Path(path_str)
        with self._lock:
            self._in_flight.discard(path_str)
        self.put(path, image)
