"""Fast image loading, background preloading cache, and non-destructive rotate/flip/crop operations."""

from __future__ import annotations

from collections import OrderedDict
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from typing import List, Optional, Tuple

from PIL import Image, ImageOps
from PySide6.QtCore import QObject, QRect, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage, QImageReader, QImageWriter, QTransform


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


def rotate_qimage(image: QImage, degrees: int) -> QImage:
    """Rotate a QImage by 90, -90, or 180 degrees."""
    transform = QTransform()
    transform.rotate(degrees)
    return image.transformed(transform, Qt.TransformationMode.SmoothTransformation)


def flip_qimage(image: QImage, horizontal: bool = False, vertical: bool = False) -> QImage:
    """Flip a QImage horizontally and/or vertically."""
    return image.flipped(
        Qt.Orientation.Horizontal if horizontal else Qt.Orientation(0)
        | (Qt.Orientation.Vertical if vertical else Qt.Orientation(0))
    ) if hasattr(image, "flipped") else image.mirrored(horizontal, vertical)


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
            jpegtran = shutil.which("jpegtran")
            if not doc.modified and jpegtran:
                res = subprocess.run(
                    [jpegtran, "-copy", "none", "-optimize", "-progressive", "-outfile", str(out_path), str(src_path)],
                    check=False,
                    capture_output=True,
                )
                if res.returncode != 0 or not out_path.exists():
                    pil_img.convert("RGB").save(
                        str(out_path), format="JPEG", quality=95, optimize=True, progressive=True, subsampling=0
                    )
            else:
                pil_img.convert("RGB").save(
                    str(out_path), format="JPEG", quality=95, optimize=True, progressive=True, subsampling=0
                )
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
