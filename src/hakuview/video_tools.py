"""Lazy-loaded Video Superpowers: Spatial Crop, Storyboard Contact Sheet, and Speed-Ramp / Timelapse Export."""

from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .formatting import human_size
from .image_ops import save_qimage_atomic
from .proc import no_window
from .video_view import _FfmpegJob, _format_ffmpeg_timestamp, _format_ms


# ------------------------------------------------------------------
# 1. Spatial Video Crop (C in video mode)
# ------------------------------------------------------------------


def normalize_even_crop(rect: QRect, frame_w: int, frame_h: int) -> QRect:
    """Clamp `rect` to `(frame_w, frame_h)` and make width/height even (>= 16) for H.264 yuv420p."""
    fw = max(16, int(frame_w))
    fh = max(16, int(frame_h))
    x = max(0, min(fw - 16, int(rect.x())))
    y = max(0, min(fh - 16, int(rect.y())))
    w = max(16, min(fw - x, int(rect.width())))
    h = max(16, min(fh - y, int(rect.height())))
    w -= w % 2
    h -= h % 2
    w = max(16, w)
    h = max(16, h)
    if x + w > fw:
        x = max(0, fw - w)
    if y + h > fh:
        y = max(0, fh - h)
    return QRect(x, y, w, h)


def build_video_crop_vf(rect: QRect, frame_w: int, frame_h: int) -> str:
    """Build FFmpeg `-vf` crop expression with guaranteed even dimensions + yuv420p."""
    r = normalize_even_crop(rect, frame_w, frame_h)
    return f"crop={r.width()}:{r.height()}:{r.x()}:{r.y()},format=yuv420p"


class _CropVideoWorker(_FfmpegJob):
    """Exports a spatially cropped video clip (`crop=w:h:x:y`), optionally trimmed to `[start_ms, end_ms]`."""

    label = "Video crop"

    def __init__(
        self,
        src_path: Path,
        dest_path: Path,
        crop_rect: QRect,
        frame_size: tuple[int, int],
        trim_range_ms: Optional[tuple[int, int]] = None,
    ) -> None:
        exp_sec = None
        if trim_range_ms is not None and trim_range_ms[1] > trim_range_ms[0]:
            exp_sec = (trim_range_ms[1] - trim_range_ms[0]) / 1000.0
        super().__init__(src_path, dest_path, expected_duration_sec=exp_sec)
        self.crop_rect = crop_rect
        self.frame_size = frame_size
        self.trim_range_ms = trim_range_ms

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        vf = build_video_crop_vf(self.crop_rect, self.frame_size[0], self.frame_size[1])
        head = [ffmpeg, "-y"]
        if self.trim_range_ms is not None and self.trim_range_ms[1] > self.trim_range_ms[0]:
            s_ms, e_ms = self.trim_range_ms
            head += [
                "-ss",
                _format_ffmpeg_timestamp(s_ms),
                "-t",
                f"{(e_ms - s_ms) / 1000.0:.3f}",
            ]
        head += ["-i", str(self.src_path), "-vf", vf]
        base_v = ["-c:v", "libx264", "-preset", "fast", "-crf", "20", "-movflags", "+faststart"]
        return [
            (head + base_v + ["-c:a", "copy", str(self.dest_path)], 0),
            (head + base_v + ["-c:a", "aac", "-b:a", "160k", str(self.dest_path)], 0),
        ]


class _VideoCropPreview(QWidget):
    """Draggable border/corner crop box over a video frame."""

    regionChanged = Signal(QRect)

    def __init__(self, frame: QImage, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(460, 260)
        self._frame = frame if (frame is not None and not frame.isNull()) else QImage(640, 360, QImage.Format.Format_RGB32)
        if frame is None or frame.isNull():
            self._frame.fill(QColor("#1e293b"))
        self._pixmap = QPixmap.fromImage(self._frame)
        fw, fh = self._frame.width(), self._frame.height()
        self._region = normalize_even_crop(
            QRect(int(fw * 0.1), int(fh * 0.1), int(fw * 0.8), int(fh * 0.8)),
            fw,
            fh,
        )
        self._aspect: Optional[float] = None
        self._drag_mode: Optional[str] = None
        self._drag_start = QPoint()
        self._drag_init = QRect()

    @property
    def region(self) -> QRect:
        return QRect(self._region)

    def set_region(self, r: QRect) -> None:
        nr = normalize_even_crop(r, self._frame.width(), self._frame.height())
        if nr != self._region:
            self._region = nr
            self.regionChanged.emit(self._region)
            self.update()

    def set_aspect_ratio(self, ratio: Optional[float]) -> None:
        self._aspect = ratio
        if ratio is not None and ratio > 0:
            fw, fh = self._frame.width(), self._frame.height()
            cx, cy = self._region.center().x(), self._region.center().y()
            max_w, max_h = int(fw * 0.86), int(fh * 0.86)
            if max_w / float(max_h) > ratio:
                nh = max_h
                nw = int(round(nh * ratio))
            else:
                nw = max_w
                nh = int(round(nw / ratio))
            self.set_region(QRect(cx - nw // 2, cy - nh // 2, nw, nh))
        self.update()

    def _target_rect(self) -> QRectF:
        fw, fh = max(1, self._frame.width()), max(1, self._frame.height())
        scale = min((self.width() - 20) / float(fw), (self.height() - 20) / float(fh))
        tw, th = fw * scale, fh * scale
        return QRectF((self.width() - tw) / 2.0, (self.height() - th) / 2.0, tw, th)

    def _w2i(self, pt: QPointF) -> QPoint:
        tr = self._target_rect()
        fw, fh = self._frame.width(), self._frame.height()
        ix = int(round((pt.x() - tr.left()) * fw / max(1.0, tr.width())))
        iy = int(round((pt.y() - tr.top()) * fh / max(1.0, tr.height())))
        return QPoint(max(0, min(fw, ix)), max(0, min(fh, iy)))

    def _i2w(self, r: QRect) -> QRectF:
        tr = self._target_rect()
        sx = tr.width() / max(1.0, float(self._frame.width()))
        sy = tr.height() / max(1.0, float(self._frame.height()))
        return QRectF(tr.left() + r.x() * sx, tr.top() + r.y() * sy, r.width() * sx, r.height() * sy)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#0b0f19"))
        tr = self._target_rect()
        p.drawPixmap(tr, self._pixmap, QRectF(self._pixmap.rect()))

        sr = self._i2w(self._region)
        outer = QPainterPath()
        outer.addRect(tr)
        inner = QPainterPath()
        inner.addRect(sr)
        p.fillPath(outer.subtracted(inner), QColor(0, 0, 0, 155))

        p.setPen(QPen(QColor(56, 189, 248), 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(sr)

        p.setBrush(QColor(56, 189, 248))
        p.setPen(QPen(QColor("#0f172a"), 1.2))
        cx, cy = sr.center().x(), sr.center().y()
        for pt in (
            sr.topLeft(),
            sr.topRight(),
            sr.bottomLeft(),
            sr.bottomRight(),
            QPointF(cx, sr.top()),
            QPointF(cx, sr.bottom()),
            QPointF(sr.left(), cy),
            QPointF(sr.right(), cy),
        ):
            p.drawEllipse(pt, 5.0, 5.0)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        sr = self._i2w(self._region)
        tol = 12.0
        nl = abs(pos.x() - sr.left()) <= tol and (sr.top() - tol <= pos.y() <= sr.bottom() + tol)
        nr = abs(pos.x() - sr.right()) <= tol and (sr.top() - tol <= pos.y() <= sr.bottom() + tol)
        nt = abs(pos.y() - sr.top()) <= tol and (sr.left() - tol <= pos.x() <= sr.right() + tol)
        nb = abs(pos.y() - sr.bottom()) <= tol and (sr.left() - tol <= pos.x() <= sr.right() + tol)
        mode = ""
        if nt:
            mode += "n"
        elif nb:
            mode += "s"
        if nl:
            mode += "w"
        elif nr:
            mode += "e"
        if not mode:
            mode = "move" if sr.contains(pos) else "new"
        self._drag_mode = mode
        self._drag_start = self._w2i(pos)
        self._drag_init = QRect(self._region)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._drag_mode:
            return
        cur = self._w2i(event.position())
        fw, fh = self._frame.width(), self._frame.height()
        r = QRect(self._drag_init)
        if self._drag_mode == "move":
            dx = cur.x() - self._drag_start.x()
            dy = cur.y() - self._drag_start.y()
            nx = max(0, min(fw - r.width(), r.left() + dx))
            ny = max(0, min(fh - r.height(), r.top() + dy))
            self.set_region(QRect(nx, ny, r.width(), r.height()))
        elif self._drag_mode == "new":
            x1, y1 = min(self._drag_start.x(), cur.x()), min(self._drag_start.y(), cur.y())
            w, h = max(16, abs(cur.x() - self._drag_start.x())), max(16, abs(cur.y() - self._drag_start.y()))
            if self._aspect is not None:
                h = max(16, int(round(w / self._aspect)))
            self.set_region(QRect(x1, y1, w, h))
        else:
            left, top, right, bottom = r.left(), r.top(), r.left() + r.width(), r.top() + r.height()
            if "w" in self._drag_mode:
                left = min(cur.x(), right - 16)
            if "e" in self._drag_mode:
                right = max(cur.x(), left + 16)
            if "n" in self._drag_mode:
                top = min(cur.y(), bottom - 16)
            if "s" in self._drag_mode:
                bottom = max(cur.y(), top + 16)
            w, h = max(16, right - left), max(16, bottom - top)
            if self._aspect is not None:
                h = max(16, int(round(w / self._aspect)))
            self.set_region(QRect(left, top, w, h))

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._drag_mode = None


class VideoCropDialog(QDialog):
    """Interactive Spatial Video Crop dialog (`C` in video mode)."""

    def __init__(
        self,
        frame: Optional[QImage],
        trim_range_label: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("✂️ Spatial Video Crop (Cut Area / Borders)")
        self.setMinimumWidth(560)
        self._updating = False

        safe_frame = frame if (frame is not None and not frame.isNull()) else QImage(1280, 720, QImage.Format.Format_RGB32)
        if frame is None or frame.isNull():
            safe_frame.fill(QColor("#1e293b"))
        self._fw, self._fh = safe_frame.width(), safe_frame.height()

        root = QVBoxLayout(self)
        self.preview = _VideoCropPreview(safe_frame, self)
        root.addWidget(self.preview, stretch=1)

        # Aspect ratio preset pills
        ar_row = QHBoxLayout()
        ar_row.addWidget(QLabel("Aspect Preset:", self))
        for label, ratio in (("Free", None), ("16:9", 16.0 / 9.0), ("9:16", 9.0 / 16.0), ("1:1", 1.0), ("4:3", 4.0 / 3.0)):
            b = QPushButton(label, self)
            b.clicked.connect(lambda _c=False, rt=ratio: self.preview.set_aspect_ratio(rt))
            ar_row.addWidget(b)
        ar_row.addStretch(1)
        root.addLayout(ar_row)

        box_group = QGroupBox("Crop Coordinates (even px for H.264)", self)
        form = QFormLayout(box_group)
        coord_row = QHBoxLayout()
        self.spin_x = QSpinBox(self)
        self.spin_x.setRange(0, max(0, self._fw - 16))
        self.spin_y = QSpinBox(self)
        self.spin_y.setRange(0, max(0, self._fh - 16))
        self.spin_w = QSpinBox(self)
        self.spin_w.setRange(16, self._fw)
        self.spin_w.setSingleStep(2)
        self.spin_h = QSpinBox(self)
        self.spin_h.setRange(16, self._fh)
        self.spin_h.setSingleStep(2)
        for lbl, sp in (("X:", self.spin_x), ("Y:", self.spin_y), ("W:", self.spin_w), ("H:", self.spin_h)):
            coord_row.addWidget(QLabel(lbl, self))
            coord_row.addWidget(sp, stretch=1)
        form.addRow("Region:", coord_row)

        self.chk_trim = QCheckBox(self)
        if trim_range_label:
            self.chk_trim.setText(f"✂ Also trim to marked [In, Out] range ({trim_range_label})")
            self.chk_trim.setChecked(True)
            form.addRow("", self.chk_trim)
        else:
            self.chk_trim.setVisible(False)
        root.addWidget(box_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("🎬 Export Cropped Video")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.preview.regionChanged.connect(self._sync_spins_from_preview)
        for sp in (self.spin_x, self.spin_y, self.spin_w, self.spin_h):
            sp.valueChanged.connect(self._sync_preview_from_spins)
        self._sync_spins_from_preview(self.preview.region)

    @property
    def crop_rect(self) -> QRect:
        return self.preview.region

    @property
    def frame_size(self) -> tuple[int, int]:
        return (self._fw, self._fh)

    @property
    def use_trim_range(self) -> bool:
        return not self.chk_trim.isHidden() and self.chk_trim.isChecked()

    def _sync_spins_from_preview(self, r: QRect) -> None:
        self._updating = True
        try:
            self.spin_x.setValue(r.x())
            self.spin_y.setValue(r.y())
            self.spin_w.setValue(r.width())
            self.spin_h.setValue(r.height())
        finally:
            self._updating = False

    def _sync_preview_from_spins(self) -> None:
        if self._updating:
            return
        self.preview.set_region(
            QRect(self.spin_x.value(), self.spin_y.value(), self.spin_w.value(), self.spin_h.value())
        )


# ------------------------------------------------------------------
# 2. MPC-HC / BS.Player Video Storyboard Sheet (4x4 Timestamp Grid)
# ------------------------------------------------------------------


def render_storyboard_sheet(
    tiles: list[tuple[int, QImage]],
    *,
    video_name: str,
    file_bytes: int = 0,
    duration_ms: int = 0,
    resolution_label: str = "",
    codec_label: str = "",
    cols: int = 4,
) -> QImage:
    """Compose an MPC-HC / BS.Player-style contact sheet from `(timestamp_ms, QImage)` tiles."""
    cols = max(1, int(cols))
    n = max(1, len(tiles))
    rows = max(1, math.ceil(n / float(cols)))

    tile_w = 340
    src_ar = 16.0 / 9.0
    if tiles and not tiles[0][1].isNull() and tiles[0][1].height() > 0:
        src_ar = max(0.45, min(2.6, tiles[0][1].width() / float(tiles[0][1].height())))
    tile_h = max(120, int(round(tile_w / src_ar)))

    pad = 16
    gap = 12
    header_h = 82
    sheet_w = pad * 2 + cols * tile_w + (cols - 1) * gap
    sheet_h = header_h + pad + rows * tile_h + (rows - 1) * gap + pad

    sheet = QImage(sheet_w, sheet_h, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor("#0b1120"))

    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # Header card
    header_rect = QRectF(pad, 12, sheet_w - pad * 2, header_h - 18)
    p.setBrush(QColor("#111b2e"))
    p.setPen(QPen(QColor(56, 189, 248, 110), 1.2))
    p.drawRoundedRect(header_rect, 8, 8)

    title_font = QFont("Sans Serif")
    title_font.setPixelSize(17)
    title_font.setBold(True)
    p.setFont(title_font)
    p.setPen(QColor("#f8fafc"))
    p.drawText(
        QRectF(header_rect.left() + 14, header_rect.top() + 8, header_rect.width() - 28, 24),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        f"🦉 {video_name}",
    )

    meta_parts = []
    if resolution_label:
        meta_parts.append(f"Resolution: {resolution_label}")
    if codec_label:
        meta_parts.append(f"Codec: {codec_label}")
    if duration_ms > 0:
        meta_parts.append(f"Duration: {_format_ms(duration_ms)}")
    if file_bytes > 0:
        meta_parts.append(f"Size: {human_size(file_bytes)}")
    meta_str = "   •   ".join(meta_parts) or "HakuView Video Storyboard"

    sub_font = QFont("Monospace")
    sub_font.setPixelSize(12)
    p.setFont(sub_font)
    p.setPen(QColor("#94a3b8"))
    p.drawText(
        QRectF(header_rect.left() + 14, header_rect.top() + 34, header_rect.width() - 28, 22),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        meta_str,
    )

    ts_font = QFont("Monospace")
    ts_font.setPixelSize(12)
    ts_font.setBold(True)

    for idx, (ts_ms, img) in enumerate(tiles):
        r_idx = idx // cols
        c_idx = idx % cols
        tx = pad + c_idx * (tile_w + gap)
        ty = header_h + r_idx * (tile_h + gap)
        trect = QRectF(tx, ty, tile_w, tile_h)

        # Shadow
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 110))
        p.drawRoundedRect(trect.translated(2, 3), 6, 6)

        clip = QPainterPath()
        clip.addRoundedRect(trect, 6, 6)
        p.save()
        p.setClipPath(clip)
        p.fillRect(trect, QColor("#070b14"))
        if img is not None and not img.isNull():
            scaled = img.scaled(
                tile_w, tile_h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            sx = max(0, (scaled.width() - tile_w) // 2)
            sy = max(0, (scaled.height() - tile_h) // 2)
            p.drawImage(trect, scaled, QRectF(sx, sy, tile_w, tile_h))
        p.restore()

        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(148, 163, 184, 90), 1.0))
        p.drawRoundedRect(trect, 6, 6)

        # Timestamp pill in bottom-right of tile
        ts_text = _format_ms(ts_ms)
        p.setFont(ts_font)
        fm = p.fontMetrics()
        pw = fm.horizontalAdvance(ts_text) + 14
        ph = fm.height() + 6
        pill = QRectF(trect.right() - pw - 6, trect.bottom() - ph - 6, pw, ph)
        p.setBrush(QColor(15, 23, 42, 220))
        p.setPen(QPen(QColor(56, 189, 248, 175), 1.0))
        p.drawRoundedRect(pill, 4, 4)
        p.setPen(QColor("#f8fafc"))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, ts_text)

    p.end()
    return sheet.convertToFormat(QImage.Format.Format_RGB32)


class _StoryboardWorker(_FfmpegJob):
    """Background worker that extracts 16 evenly-spaced frames via fast seek and writes a storyboard PNG."""

    label = "Storyboard sheet"

    def __init__(self, src_path: Path, dest_path: Path, cols: int = 4, rows: int = 4) -> None:
        super().__init__(src_path, dest_path)
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))

    def _run(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.failed.emit("ffmpeg is not installed on this system.")
            return
        ffprobe = shutil.which("ffprobe")
        dur_sec = 0.0
        res_label = ""
        codec_label = ""
        if ffprobe:
            try:
                pr = subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_entries",
                        "format=duration:stream=codec_type,codec_name,width,height",
                        "-of",
                        "json",
                        str(self.src_path),
                    ],
                    capture_output=True,
                    timeout=8,
                    check=False,
                    **no_window(),
                )
                info = json.loads(pr.stdout.decode("utf-8", errors="replace") or "{}")
                dur_sec = float(info.get("format", {}).get("duration") or 0.0)
                for st in info.get("streams", []):
                    if st.get("codec_type") == "video":
                        w, h = st.get("width"), st.get("height")
                        if w and h:
                            res_label = f"{w}×{h}"
                        codec_label = str(st.get("codec_name") or "").upper()
                        break
            except Exception:
                pass

        if dur_sec <= 0.0:
            dur_sec = float(self.expected_duration_sec or 4.0)

        total_tiles = self.cols * self.rows
        tiles: list[tuple[int, QImage]] = []
        self.progress.emit(2)

        for i in range(total_tiles):
            if self._cancelled:
                self.dest_path.unlink(missing_ok=True)
                return
            t_sec = dur_sec * ((i + 0.5) / float(total_tiles))
            cmd = [
                ffmpeg,
                "-loglevel",
                "error",
                "-ss",
                f"{max(0.0, t_sec):.3f}",
                "-i",
                str(self.src_path),
                "-frames:v",
                "1",
                "-vf",
                "scale='min(480,iw)':-2",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-",
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=15, check=False, **no_window())
            qimg = QImage()
            if res.returncode == 0 and res.stdout:
                qimg.loadFromData(res.stdout, "PNG")
            if not qimg.isNull():
                tiles.append((int(round(t_sec * 1000.0)), qimg))
            self.progress.emit(max(5, min(92, int(round((i + 1) * 90.0 / total_tiles)))))

        if not tiles:
            self.failed.emit("Could not extract video frames for storyboard.")
            return

        try:
            fbytes = self.src_path.stat().st_size
        except OSError:
            fbytes = 0

        sheet = render_storyboard_sheet(
            tiles,
            video_name=self.src_path.name,
            file_bytes=fbytes,
            duration_ms=int(round(dur_sec * 1000.0)),
            resolution_label=res_label,
            codec_label=codec_label,
            cols=self.cols,
        )
        try:
            save_qimage_atomic(sheet, self.dest_path)
        except Exception as exc:
            self.failed.emit(f"Failed to write storyboard:\n{exc}")
            return

        self.progress.emit(100)
        self.finished.emit(self.dest_path)


# ------------------------------------------------------------------
# 3. Timelapse / Speed-Ramp Video Export (0.5x .. 8x)
# ------------------------------------------------------------------


SPEED_EXPORT_PRESETS: list[tuple[str, float]] = [
    ("2× Fast — Snappy Demo / Tutorial", 2.0),
    ("1.5× Brisk — Walkthrough Pace", 1.5),
    ("4× Timelapse — Condense Long Recording", 4.0),
    ("8× Hyper-Timelapse — Ultra Fast", 8.0),
    ("1.25× Slightly Faster", 1.25),
    ("0.5× Half Speed — Slow Motion", 0.5),
]


def build_atempo_chain(speed: float) -> str:
    """Build chained FFmpeg `atempo=...` filters (each stage strictly within [0.5, 2.0])."""
    rem = max(0.125, min(16.0, float(speed)))
    stages: list[str] = []
    while rem > 2.0 + 1e-6:
        stages.append("atempo=2.0")
        rem /= 2.0
    while rem < 0.5 - 1e-6:
        stages.append("atempo=0.5")
        rem /= 0.5
    val = f"{rem:.4f}".rstrip("0")
    if val.endswith("."):
        val += "0"
    stages.append(f"atempo={val}")
    return ",".join(stages)


class _SpeedRampWorker(_FfmpegJob):
    """Exports a speed-ramped / timelapse video (`setpts` + chained pitch-preserving `atempo`)."""

    label = "Speed export"

    def __init__(
        self,
        src_path: Path,
        dest_path: Path,
        speed: float,
        *,
        mute_audio: bool = False,
        trim_range_ms: Optional[tuple[int, int]] = None,
        source_duration_sec: Optional[float] = None,
    ) -> None:
        spd = max(0.25, min(16.0, float(speed)))
        base_dur = source_duration_sec
        if trim_range_ms is not None and trim_range_ms[1] > trim_range_ms[0]:
            base_dur = (trim_range_ms[1] - trim_range_ms[0]) / 1000.0
        exp_out = (base_dur / spd) if (base_dur and base_dur > 0) else None
        super().__init__(src_path, dest_path, expected_duration_sec=exp_out)
        self.speed = spd
        self.mute_audio = mute_audio
        self.trim_range_ms = trim_range_ms

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        head = [ffmpeg, "-y"]
        if self.trim_range_ms is not None and self.trim_range_ms[1] > self.trim_range_ms[0]:
            s_ms, e_ms = self.trim_range_ms
            head += [
                "-ss",
                _format_ffmpeg_timestamp(s_ms),
                "-t",
                f"{(e_ms - s_ms) / 1000.0:.3f}",
            ]
        head += ["-i", str(self.src_path)]

        vf = f"setpts=PTS/{self.speed:.6f},format=yuv420p"
        v_enc = ["-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-movflags", "+faststart"]

        silent_cmd = head + v_enc + ["-an", str(self.dest_path)]
        if self.mute_audio or self.speed > 4.0:
            return [(silent_cmd, 0)]

        af = build_atempo_chain(self.speed)
        with_audio_cmd = head + v_enc + ["-af", af, "-c:a", "aac", "-b:a", "160k", str(self.dest_path)]
        # Try pitch-preserved audio first; if source video has no audio stream, fallback to -an cleanly!
        return [(with_audio_cmd, 0), (silent_cmd, 0)]


class SpeedRampDialog(QDialog):
    """Compact dialog for exporting a speed-ramped / timelapse MP4."""

    def __init__(
        self,
        initial_speed: float = 2.0,
        trim_range_label: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("⏩ Export Speed-Ramped / Timelapse Video")
        self.setMinimumWidth(430)

        root = QVBoxLayout(self)
        form = QFormLayout()

        self.combo_speed = QComboBox(self)
        best_idx = 0
        for idx, (label, spd) in enumerate(SPEED_EXPORT_PRESETS):
            self.combo_speed.addItem(label, spd)
            if abs(spd - initial_speed) < 0.05:
                best_idx = idx
        self.combo_speed.setCurrentIndex(best_idx)
        form.addRow("Target Speed:", self.combo_speed)

        self.chk_mute = QCheckBox("🔇 Mute audio track (timelapse silent video)", self)
        form.addRow("", self.chk_mute)

        self.chk_trim = QCheckBox(self)
        if trim_range_label:
            self.chk_trim.setText(f"✂ Export marked [In, Out] clip only ({trim_range_label})")
            self.chk_trim.setChecked(True)
            form.addRow("", self.chk_trim)
        else:
            self.chk_trim.setVisible(False)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("⏩ Export MP4")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @property
    def speed(self) -> float:
        return float(self.combo_speed.currentData() or 2.0)

    @property
    def mute_audio(self) -> bool:
        return self.chk_mute.isChecked()

    @property
    def use_trim_range(self) -> bool:
        return not self.chk_trim.isHidden() and self.chk_trim.isChecked()
