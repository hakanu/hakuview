"""Fast image loading, background preloading cache, and non-destructive rotate/flip/crop operations."""

from __future__ import annotations

from collections import OrderedDict
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import List, Optional, Tuple

from PIL import Image, ImageOps, JpegImagePlugin
from PySide6.QtCore import QObject, QRect, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QImageWriter, QTransform

from .formatting import human_size


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
    data = pil_img.point(lut * 3 + _IDENTITY_LUT).tobytes()
    return QImage(data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


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


class ImageDocument:
    """Tracks the currently displayed image, its undo stack, and modified flag."""

    MAX_UNDO = 10

    def __init__(self, path: Path, image: QImage) -> None:
        self.path: Path = path
        self.original: QImage = image
        self.current: QImage = image
        self.undo_stack: List[QImage] = []
        self.modified: bool = False

    def _push_undo(self) -> None:
        self.undo_stack.append(self.current)
        if len(self.undo_stack) > self.MAX_UNDO:
            self.undo_stack.pop(0)
        self.modified = True

    def rotate_left(self) -> None:
        self._push_undo()
        self.current = rotate_qimage(self.current, -90)

    def rotate_right(self) -> None:
        self._push_undo()
        self.current = rotate_qimage(self.current, 90)

    def flip_horizontal(self) -> None:
        self._push_undo()
        self.current = flip_qimage(self.current, horizontal=True, vertical=False)

    def flip_vertical(self) -> None:
        self._push_undo()
        self.current = flip_qimage(self.current, horizontal=False, vertical=True)

    def crop(self, rect: QRect) -> bool:
        bounded = rect.intersected(self.current.rect())
        if bounded.isEmpty() or bounded.width() < 2 or bounded.height() < 2:
            return False
        if bounded == self.current.rect():
            return False
        self._push_undo()
        self.current = crop_qimage(self.current, bounded)
        return True

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.current = self.undo_stack.pop()
        self.modified = bool(self.undo_stack)
        return True

    def reset(self) -> bool:
        if not self.modified:
            return False
        self.current = self.original
        self.undo_stack.clear()
        self.modified = False
        return True

    def mark_saved(self, new_path: Optional[Path] = None) -> None:
        if new_path is not None:
            self.path = new_path
        self.original = self.current
        self.undo_stack.clear()
        self.modified = False


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
