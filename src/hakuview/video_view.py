"""Lightweight Qt6 hardware-accelerated video player widget for HakuView."""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
from typing import List, Optional

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSize, QSizeF, QThread, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)


from .image_ops import _next_variant_path, apply_color_lut, color_lut, color_lut_params
from .proc import no_window


PLAYBACK_SPEEDS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0)

# WhatsApp Web media limit is 16 MB for standard video clips; targeting <= 15 MiB with a capped
# H.264 bitrate ensures fast uploads and universal playback across desktop and mobile clients.
WHATSAPP_MAX_MB = 15.0
WHATSAPP_DEFAULT_MAXRATE_KBPS = 1800
WHATSAPP_MIN_VIDEO_KBPS = 250
WHATSAPP_AUDIO_KBPS = 128

VIDEO_SIZE_PRESETS: tuple[tuple[str, str, int, int], ...] = (
    ("16:9_1080p", "16:9 Landscape — 1920 × 1080 (Full HD)", 1920, 1080),
    ("16:9_720p", "16:9 Landscape — 1280 × 720 (HD)", 1280, 720),
    ("9:16_1080p", "9:16 Vertical — 1080 × 1920 (Reels / Shorts / TikTok)", 1080, 1920),
    ("1:1_1080", "1:1 Square — 1080 × 1080 (Instagram / Feed)", 1080, 1080),
    ("4:5_1080", "4:5 Portrait — 1080 × 1350 (Social Post)", 1080, 1350),
    ("4:3_1080", "4:3 Classic — 1440 × 1080", 1440, 1080),
    ("21:9_1080", "21:9 Ultrawide — 2560 × 1080", 2560, 1080),
    ("custom", "Custom Resolution (W × H)", 0, 0),
)

VIDEO_BG_STYLES: tuple[tuple[str, str], ...] = (
    ("dominant_blur", "✨ Dominant Color + Blurred Video (CapCut Style)"),
    ("ambient_glow", "🌈 Living Dominant Glow (Dynamic 4-Corner Gradient)"),
    ("video_blur", "🌫 Pure Blurred Video Backdrop"),
    ("dominant_solid", "🎨 Dominant Color Solid Fill"),
    ("crop", "🔍 Zoom & Center-Crop to Fill (No Bars)"),
)


def _format_ms(ms: int) -> str:
    total_sec = max(0, ms // 1000)
    m, s = divmod(total_sec, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _format_ffmpeg_timestamp(ms: int) -> str:
    total_sec = max(0, ms) // 1000
    millis = max(0, ms) % 1000
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    return f"{h:02d}:{m:02d}:{s:02d}.{millis:03d}"


def detect_video_codec(path: Path) -> Optional[str]:
    """Rapidly probe video stream codec name (e.g. 'av1', 'vp9', 'h264') using ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        res = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
            **no_window(),
        )
        return res.stdout.strip().lower() or None
    except Exception:
        return None


def probe_video_duration_sec(path: Path) -> Optional[float]:
    """Probe container duration in seconds via ffprobe."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        res = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            **no_window(),
        )
        val = float(res.stdout.strip())
        return val if val > 0 else None
    except Exception:
        return None


def whatsapp_target_bitrate_kbps(src_path: Path, duration_sec: Optional[float] = None) -> tuple[int, int]:
    """Compute (video_maxrate_kbps, audio_kbps) to lower bitrate and fit under WhatsApp Web's limit."""
    if duration_sec is None or duration_sec <= 0:
        duration_sec = probe_video_duration_sec(src_path)

    audio_kbps = 96 if (duration_sec and duration_sec > 180) else WHATSAPP_AUDIO_KBPS
    target_v_kbps = WHATSAPP_DEFAULT_MAXRATE_KBPS

    if duration_sec and duration_sec > 0:
        # 1. Cap total bitrate so the entire output stays safely under WHATSAPP_MAX_MB (15 MiB)
        total_budget_kbps = int((WHATSAPP_MAX_MB * 1024 * 8) / duration_sec)
        size_cap_v_kbps = max(WHATSAPP_MIN_VIDEO_KBPS, total_budget_kbps - audio_kbps)
        target_v_kbps = min(target_v_kbps, size_cap_v_kbps)

        # 2. Also lower the bitrate relative to the source file (~65% of source total bitrate)
        try:
            src_bytes = src_path.stat().st_size
            if src_bytes > 0:
                src_total_kbps = (src_bytes * 8.0) / (1000.0 * duration_sec)
                reduced_v_kbps = int(src_total_kbps * 0.65) - audio_kbps
                target_v_kbps = min(target_v_kbps, max(WHATSAPP_MIN_VIDEO_KBPS, reduced_v_kbps))
        except OSError:
            pass

    return max(WHATSAPP_MIN_VIDEO_KBPS, target_v_kbps), audio_kbps


class _CodecProbeSignals(QObject):
    """Carries an async ffprobe result (path, codec name) back to the UI thread."""

    detected = Signal(str, str)


_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_FFMPEG_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


class _FfmpegJob(QObject):
    """Runs one or more ffmpeg attempts in a worker thread; cancel() kills the running process.

    Each attempt is (command, min_output_bytes); the first that exits 0 and produces a large
    enough file wins. `done` is always emitted last so the owning QThread can quit, even when
    the job was cancelled (cancellation emits neither `finished` nor `failed`).
    """

    progress = Signal(int)
    finished = Signal(Path)
    failed = Signal(str)
    done = Signal()

    label = "FFmpeg job"

    def __init__(self, src_path: Path, dest_path: Path, expected_duration_sec: Optional[float] = None) -> None:
        super().__init__()
        self.src_path = src_path
        self.dest_path = dest_path
        self.expected_duration_sec = expected_duration_sec
        self._proc: Optional[subprocess.Popen] = None
        self._cancelled = False

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        raise NotImplementedError

    def cancel(self) -> None:
        self._cancelled = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def run(self) -> None:
        try:
            self._run()
        finally:
            self.done.emit()

    def _run(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.failed.emit("ffmpeg is not installed on this system.")
            return
        err_msg = ""
        self.progress.emit(0)
        try:
            for cmd, min_bytes in self._attempts(ffmpeg):
                if self._cancelled:
                    break
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **no_window())
                tail = ""
                total_sec = float(self.expected_duration_sec) if (self.expected_duration_sec and self.expected_duration_sec > 0) else 0.0
                last_pct = 0
                if self._proc.stderr is not None:
                    while True:
                        chunk = (
                            self._proc.stderr.read1(512)
                            if hasattr(self._proc.stderr, "read1")
                            else self._proc.stderr.read(512)
                        )
                        if not chunk:
                            break
                        text = chunk.decode("utf-8", errors="replace")
                        tail = (tail + text)[-1200:]
                        if total_sec <= 0:
                            dm = _FFMPEG_DURATION_RE.search(tail)
                            if dm:
                                total_sec = int(dm.group(1)) * 3600 + int(dm.group(2)) * 60 + float(dm.group(3))
                        if total_sec > 0:
                            for tm in _FFMPEG_TIME_RE.finditer(text):
                                cur_sec = int(tm.group(1)) * 3600 + int(tm.group(2)) * 60 + float(tm.group(3))
                                pct = max(1, min(99, int(round((cur_sec / total_sec) * 100.0))))
                                if pct != last_pct:
                                    last_pct = pct
                                    self.progress.emit(pct)
                self._proc.wait()
                if (
                    self._proc.returncode == 0
                    and self.dest_path.exists()
                    and self.dest_path.stat().st_size > min_bytes
                ):
                    self.progress.emit(100)
                    self.finished.emit(self.dest_path)
                    return
                err_msg = tail[-300:]
        except Exception as exc:
            err_msg = str(exc)
        if self._cancelled:
            self.dest_path.unlink(missing_ok=True)
            return
        self.failed.emit(f"{self.label} failed:\n{err_msg}")


class _TranscodeWorker(_FfmpegJob):
    """Re-encodes a video to H.264 MP4 (for streams the GPU/Qt backend can't decode)."""

    label = "Transcoding"

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        cmd = [
            ffmpeg, "-y", "-i", str(self.src_path),
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-c:a", "copy",
            str(self.dest_path),
        ]
        return [(cmd, 0)]


class _TrimWorker(_FfmpegJob):
    """Cuts [start_ms, end_ms] out of a video: stream copy first, re-encode as fallback.

    Stream copy is instant and lossless but can only cut on keyframes, so the clip may start
    slightly before the In-point.
    """

    label = "Trimming"

    def __init__(self, src_path: Path, dest_path: Path, start_ms: int, end_ms: int) -> None:
        clip_sec = max(0.0, (end_ms - start_ms) / 1000.0) if end_ms > start_ms else None
        super().__init__(src_path, dest_path, expected_duration_sec=clip_sec)
        self.start_ms = start_ms
        self.end_ms = end_ms

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        cut = [
            ffmpeg, "-y",
            "-ss", _format_ffmpeg_timestamp(self.start_ms),
            "-to", _format_ffmpeg_timestamp(self.end_ms),
            "-i", str(self.src_path),
        ]
        stream_copy = cut + ["-c", "copy", "-avoid_negative_ts", "make_zero", str(self.dest_path)]
        reencode = cut + [
            "-c:v", "libx264", "-crf", "18", "-preset", "ultrafast",
            "-c:a", "copy",
            str(self.dest_path),
        ]
        # A near-empty stream-copy output means the container rejected the cut.
        return [(stream_copy, 1000), (reencode, 0)]


class _ColorExportWorker(_FfmpegJob):
    """Re-encodes a video with the same brightness/contrast LUT the live preview uses."""

    label = "Color export"

    def __init__(self, src_path: Path, dest_path: Path, brightness: int, contrast: int) -> None:
        super().__init__(src_path, dest_path)
        self.brightness = brightness
        self.contrast = contrast

    def lut_expression(self) -> str:
        """ffmpeg lutrgb expression equal to image_ops.color_lut() for these settings."""
        factor, offset = color_lut_params(self.brightness, self.contrast)
        # Quoted so the commas inside clip() aren't read as filter-graph separators.
        return f"'clip(round({factor:.6f}*(val-128+{offset:.4f})+128),0,255)'"

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        expr = self.lut_expression()
        encode = [
            ffmpeg, "-y", "-i", str(self.src_path),
            "-vf", f"lutrgb=r={expr}:g={expr}:b={expr},format=yuv420p",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium",
            "-movflags", "+faststart",
        ]
        # Copying the audio is lossless, but MP4 rejects some codecs (e.g. Vorbis); then use AAC.
        return [
            (encode + ["-c:a", "copy", str(self.dest_path)], 0),
            (encode + ["-c:a", "aac", "-b:a", "192k", str(self.dest_path)], 0),
        ]


class _WhatsAppWorker(_FfmpegJob):
    """Re-encodes a video into a WhatsApp Web friendly H.264 + AAC MP4 with lowered bitrate."""

    label = "WhatsApp export"

    def __init__(self, src_path: Path, dest_path: Path, duration_sec: Optional[float] = None) -> None:
        super().__init__(src_path, dest_path, expected_duration_sec=duration_sec)
        self.duration_sec = duration_sec

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        v_kbps, a_kbps = whatsapp_target_bitrate_kbps(self.src_path, self.duration_sec)
        buf_kbps = v_kbps * 2
        vf = (
            "scale='if(gt(iw,ih),min(1280,iw),-2)':'if(gt(iw,ih),-2,min(1280,ih))',"
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
            "format=yuv420p"
        )
        cmd = [
            ffmpeg, "-y", "-i", str(self.src_path),
            "-vf", vf,
            "-c:v", "libx264", "-profile:v", "main", "-preset", "fast",
            "-crf", "26",
            "-maxrate", f"{v_kbps}k",
            "-bufsize", f"{buf_kbps}k",
            "-c:a", "aac", "-b:a", f"{a_kbps}k", "-ac", "2",
            "-movflags", "+faststart",
            str(self.dest_path),
        ]
        return [(cmd, 0)]


def extract_dominant_color(image: Optional[QImage]) -> QColor:
    """Extract a rich dominant color from a video frame, weighting saturated non-border pixels."""
    default = QColor("#1e293b")
    if image is None or image.isNull():
        return default
    sample = image.scaled(40, 40, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    sample = sample.convertToFormat(QImage.Format.Format_RGB888)
    buckets: dict[tuple[int, int, int], list[float]] = {}
    for y in range(sample.height()):
        for x in range(sample.width()):
            c = sample.pixelColor(x, y)
            r, g, b = c.red(), c.green(), c.blue()
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            if luma < 16 or luma > 242:
                continue  # skip letterbox black bars and blown highlights
            sat = c.hsvSaturationF()
            weight = 0.35 + 1.65 * max(0.0, sat)
            key = (r // 32, g // 32, b // 32)
            acc = buckets.get(key)
            if acc is None:
                buckets[key] = [weight, r * weight, g * weight, b * weight]
            else:
                acc[0] += weight
                acc[1] += r * weight
                acc[2] += g * weight
                acc[3] += b * weight
    if not buckets:
        return default
    best = max(buckets.values(), key=lambda item: item[0])
    w_sum = max(1e-6, best[0])
    out = QColor(round(best[1] / w_sum), round(best[2] / w_sum), round(best[3] / w_sum))
    # Keep lightness in a pleasing deep studio range so the foreground video always pops
    h, s, l, a = out.getHslF()
    clamped_l = max(0.16, min(0.45, l))
    boosted_s = min(1.0, s * 1.15) if s > 0.08 else s
    return QColor.fromHslF(max(0.0, h), boosted_s, clamped_l, a)


def build_resize_vf(
    width: int,
    height: int,
    bg_style: str = "dominant_blur",
    dominant_hex: str = "#1e293b",
    blur_sigma: int = 32,
    fg_scale_pct: int = 100,
) -> str:
    """Build an FFmpeg -vf filtergraph that reframes/resizes a video with a CapCut-style backdrop."""
    W = max(64, int(width) - (int(width) % 2))
    H = max(64, int(height) - (int(height) % 2))
    hex_rgb = dominant_hex.lstrip("#")
    if len(hex_rgb) != 6:
        hex_rgb = "1e293b"
    sigma = max(2, min(80, int(blur_sigma)))
    pct = max(60, min(100, int(fg_scale_pct)))
    fg_w = max(32, int(round(W * pct / 100.0)) // 2 * 2)
    fg_h = max(32, int(round(H * pct / 100.0)) // 2 * 2)

    if bg_style == "crop":
        return f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},format=yuv420p"

    if bg_style == "dominant_solid":
        return (
            f"scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x{hex_rgb},format=yuv420p"
        )

    fg_chain = (
        f"[fg]scale={fg_w}:{fg_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2[fg_out]"
    )

    if bg_style == "ambient_glow":
        bg_chain = (
            f"[bg]scale=4:4:flags=area,"
            f"scale={W}:{H}:flags=bicubic,"
            f"gblur=sigma={sigma}:steps=3,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=0x{hex_rgb}@0.28:t=fill,"
            f"eq=brightness=-0.06:saturation=1.3[bg_out]"
        )
    elif bg_style == "video_blur":
        bg_chain = (
            f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"gblur=sigma={sigma}:steps=3,"
            f"eq=brightness=-0.10:saturation=1.15[bg_out]"
        )
    else:  # "dominant_blur" (default CapCut style)
        bg_chain = (
            f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"gblur=sigma={sigma}:steps=3,"
            f"drawbox=x=0:y=0:w=iw:h=ih:color=0x{hex_rgb}@0.38:t=fill,"
            f"eq=brightness=-0.08:saturation=1.2[bg_out]"
        )

    return f"split=2[bg][fg];{bg_chain};{fg_chain};[bg_out][fg_out]overlay=(W-w)/2:(H-h)/2,format=yuv420p"


def render_resize_preview(
    frame: Optional[QImage],
    width: int,
    height: int,
    bg_style: str = "dominant_blur",
    dominant_color: Optional[QColor] = None,
    blur_sigma: int = 32,
    fg_scale_pct: int = 100,
    max_preview_side: int = 440,
) -> QImage:
    """Render a fast live preview QImage matching build_resize_vf() for VideoResizeDialog."""
    W = max(64, int(width))
    H = max(64, int(height))
    scale_factor = min(max_preview_side / max(W, H), 1.0)
    pw = max(48, round(W * scale_factor))
    ph = max(48, round(H * scale_factor))

    dom = dominant_color if (dominant_color is not None and dominant_color.isValid()) else extract_dominant_color(frame)
    canvas = QImage(pw, ph, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(dom)

    if frame is None or frame.isNull():
        return canvas

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    if bg_style == "crop":
        covered = frame.scaled(pw, ph, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        cx = max(0, (covered.width() - pw) // 2)
        cy = max(0, (covered.height() - ph) // 2)
        painter.drawImage(0, 0, covered, cx, cy, pw, ph)
        painter.end()
        return canvas

    if bg_style in ("dominant_blur", "video_blur", "ambient_glow"):
        # Downscale-then-upscale approximates Gaussian blur at zero CPU cost for live slider dragging
        if bg_style == "ambient_glow":
            tiny_w, tiny_h = 4, 4
        else:
            div = max(4, int(blur_sigma * 0.7))
            tiny_w = max(4, pw // div)
            tiny_h = max(4, ph // div)
        covered = frame.scaled(pw, ph, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        cx = max(0, (covered.width() - pw) // 2)
        cy = max(0, (covered.height() - ph) // 2)
        cropped_bg = covered.copy(cx, cy, pw, ph)
        blurred = cropped_bg.scaled(tiny_w, tiny_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation).scaled(
            pw, ph, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        painter.drawImage(0, 0, blurred)
        if bg_style in ("dominant_blur", "ambient_glow"):
            tint = QColor(dom)
            tint.setAlpha(105 if bg_style == "dominant_blur" else 80)
            painter.fillRect(0, 0, pw, ph, tint)
        painter.fillRect(0, 0, pw, ph, QColor(0, 0, 0, 32))

    # Draw foreground video frame centered at fg_scale_pct
    pct = max(60, min(100, int(fg_scale_pct))) / 100.0
    fg_box = QSize(max(16, round(pw * pct)), max(16, round(ph * pct)))
    fg_img = frame.scaled(fg_box, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    fx = (pw - fg_img.width()) // 2
    fy = (ph - fg_img.height()) // 2

    # Subtle drop shadow when background is visible around the foreground frame
    if fg_img.width() < pw or fg_img.height() < ph:
        painter.fillRect(fx - 2, fy - 2, fg_img.width() + 4, fg_img.height() + 4, QColor(0, 0, 0, 85))
    painter.drawImage(fx, fy, fg_img)
    painter.end()
    return canvas


class _ResizeWorker(_FfmpegJob):
    """Reframes/resizes a video to (width, height) with a blurred/dominant-color backdrop."""

    label = "Video resize"

    def __init__(
        self,
        src_path: Path,
        dest_path: Path,
        width: int,
        height: int,
        bg_style: str = "dominant_blur",
        dominant_hex: str = "#1e293b",
        blur_sigma: int = 32,
        fg_scale_pct: int = 100,
    ) -> None:
        super().__init__(src_path, dest_path)
        self.width = max(64, int(width) - (int(width) % 2))
        self.height = max(64, int(height) - (int(height) % 2))
        self.bg_style = bg_style
        self.dominant_hex = dominant_hex
        self.blur_sigma = blur_sigma
        self.fg_scale_pct = fg_scale_pct

    def vf_expression(self) -> str:
        return build_resize_vf(
            self.width,
            self.height,
            self.bg_style,
            self.dominant_hex,
            self.blur_sigma,
            self.fg_scale_pct,
        )

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        vf = self.vf_expression()
        encode = [
            ffmpeg, "-y", "-i", str(self.src_path),
            "-vf", vf,
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-movflags", "+faststart",
        ]
        return [
            (encode + ["-c:a", "copy", str(self.dest_path)], 0),
            (encode + ["-c:a", "aac", "-b:a", "192k", str(self.dest_path)], 0),
        ]


VIDEO_TRANSFORMS: dict[str, tuple[str, str, str]] = {
    "rot_right": ("transpose=1,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "rot90", "Rotate 90° right"),
    "rot_left": ("transpose=2,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "rot270", "Rotate 90° left"),
    "flip_h": ("hflip,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "fliph", "Flip horizontal"),
    "flip_v": ("vflip,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p", "flipv", "Flip vertical"),
}


class _TransformVideoWorker(_FfmpegJob):
    """Rotates (90° left/right) or flips (horizontal/vertical) a video via FFmpeg."""

    def __init__(self, src_path: Path, dest_path: Path, transform: str) -> None:
        super().__init__(src_path, dest_path)
        vf, _tag, label = VIDEO_TRANSFORMS[transform]
        self.transform = transform
        self.vf = vf
        self.label = label

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        encode = [
            ffmpeg, "-y", "-i", str(self.src_path),
            "-vf", self.vf,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-movflags", "+faststart",
        ]
        return [
            (encode + ["-c:a", "copy", str(self.dest_path)], 0),
            (encode + ["-c:a", "aac", "-b:a", "192k", str(self.dest_path)], 0),
        ]


class _StripAudioWorker(_FfmpegJob):
    """Losslessly strips the audio track from a video (-c:v copy -an), with re-encode fallback."""

    label = "Strip audio"

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        copy_cmd = [
            ffmpeg, "-y", "-i", str(self.src_path),
            "-c:v", "copy", "-an",
            "-movflags", "+faststart",
            str(self.dest_path),
        ]
        reencode_cmd = [
            ffmpeg, "-y", "-i", str(self.src_path),
            "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-an",
            "-movflags", "+faststart",
            str(self.dest_path),
        ]
        return [(copy_cmd, 500), (reencode_cmd, 0)]


def _probe_video_dimensions(path: Path) -> Optional[tuple[int, int]]:
    """Return `(width, height)` of the primary video stream using ffprobe, or None."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        res = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=s=x:p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=2.5,
            **no_window(),
        )
        if res.returncode == 0 and res.stdout.strip():
            line = res.stdout.strip().splitlines()[0]
            w_str, h_str = line.split("x", 1)
            w, h = int(w_str), int(h_str)
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return None


class _CaptionVideoWorker(_FfmpegJob):
    """Burns a QPainter-rendered transparent PNG caption overlay onto a video via FFmpeg."""

    label = "Video caption"

    def __init__(
        self,
        src_path: Path,
        dest_path: Path,
        spec,
        *,
        fallback_size: tuple[int, int] = (1280, 720),
        trim_range_ms: Optional[tuple[int, int]] = None,
    ) -> None:
        super().__init__(src_path, dest_path)
        self.spec = spec
        self.fallback_size = fallback_size
        self.trim_range_ms = trim_range_ms
        self._overlay_path = dest_path.with_name(f".{dest_path.stem}_caption_ovl.png")

    def run(self) -> None:
        try:
            super().run()
        finally:
            self._overlay_path.unlink(missing_ok=True)

    def _attempts(self, ffmpeg: str) -> list[tuple[list[str], int]]:
        from .caption_dialog import render_caption_overlay

        w, h = _probe_video_dimensions(self.src_path) or self.fallback_size
        w = max(64, int(w) - (int(w) % 2))
        h = max(64, int(h) - (int(h) % 2))
        ovl = render_caption_overlay(w, h, self.spec)
        ovl.save(str(self._overlay_path), "PNG")

        enable_expr = ""
        if self.trim_range_ms is not None:
            in_ms, out_ms = self.trim_range_ms
            if out_ms > in_ms:
                enable_expr = f":enable='between(t,{in_ms / 1000.0:.3f},{out_ms / 1000.0:.3f})'"

        fc = f"[0:v]scale={w}:{h}[base];[base][1:v]overlay=0:0{enable_expr},format=yuv420p[v]"
        encode = [
            ffmpeg,
            "-y",
            "-i",
            str(self.src_path),
            "-i",
            str(self._overlay_path),
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "fast",
            "-movflags",
            "+faststart",
        ]
        return [
            (encode + ["-c:a", "copy", str(self.dest_path)], 0),
            (encode + ["-c:a", "aac", "-b:a", "192k", str(self.dest_path)], 0),
        ]


class VideoResizeDialog(QDialog):
    """Interactive dialog for reframing/resizing a video with CapCut-style blurred dominant color fill."""

    def __init__(self, frame: Optional[QImage] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")  # inherits HakuView's dark/light dialog styling
        self.setWindowTitle("Reframe & Resize Video — HakuView")
        self.setMinimumWidth(500)

        self._frame = frame
        self._auto_color = extract_dominant_color(frame)
        self._dominant_color = QColor(self._auto_color)
        self._updating_preset = False

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Live Preview Box
        preview_group = QGroupBox("Live Canvas Preview", self)
        preview_layout = QVBoxLayout(preview_group)
        self.lbl_preview = QLabel(self)
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(440, 250)
        preview_layout.addWidget(self.lbl_preview)
        self.lbl_summary = QLabel("", self)
        self.lbl_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.lbl_summary)
        root.addWidget(preview_group)

        # Target Size / Aspect Ratio Group
        size_group = QGroupBox("Target Resolution && Aspect Ratio", self)
        size_form = QFormLayout(size_group)

        self.combo_preset = QComboBox(self)
        for preset_id, label, w, h in VIDEO_SIZE_PRESETS:
            self.combo_preset.addItem(label, (preset_id, w, h))
        size_form.addRow("Size preset", self.combo_preset)

        dims_row = QHBoxLayout()
        self.spin_width = QSpinBox(self)
        self.spin_width.setRange(64, 3840)
        self.spin_width.setSingleStep(2)
        self.spin_width.setSuffix(" px")
        self.spin_width.setValue(1920)
        self.spin_height = QSpinBox(self)
        self.spin_height.setRange(64, 3840)
        self.spin_height.setSingleStep(2)
        self.spin_height.setSuffix(" px")
        self.spin_height.setValue(1080)
        self.btn_swap = QPushButton("⇄ Swap W/H", self)
        self.btn_swap.setToolTip("Swap Width and Height (e.g. 1920×1080 ↔ 1080×1920)")
        self.btn_swap.clicked.connect(self.swap_dimensions)
        dims_row.addWidget(QLabel("W:", self))
        dims_row.addWidget(self.spin_width, stretch=1)
        dims_row.addWidget(QLabel("×  H:", self))
        dims_row.addWidget(self.spin_height, stretch=1)
        dims_row.addWidget(self.btn_swap)
        size_form.addRow("Dimensions", dims_row)
        root.addWidget(size_group)

        # Background Fill Group
        bg_group = QGroupBox("Background Fill (CapCut Canvas Style)", self)
        bg_form = QFormLayout(bg_group)

        self.combo_bg = QComboBox(self)
        for style_id, label in VIDEO_BG_STYLES:
            self.combo_bg.addItem(label, style_id)
        bg_form.addRow("Fill style", self.combo_bg)

        color_row = QHBoxLayout()
        self.btn_pick_color = QPushButton(self)
        self.btn_pick_color.setToolTip("Click to customize the dominant background color")
        self.btn_pick_color.clicked.connect(self._choose_color)
        self.btn_auto_color = QPushButton("Auto from Video", self)
        self.btn_auto_color.setToolTip("Reset to the dominant color automatically extracted from the video")
        self.btn_auto_color.clicked.connect(self._reset_auto_color)
        color_row.addWidget(self.btn_pick_color, stretch=1)
        color_row.addWidget(self.btn_auto_color)
        bg_form.addRow("Dominant color", color_row)

        blur_row = QHBoxLayout()
        self.slider_blur = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_blur.setRange(6, 64)
        self.slider_blur.setValue(32)
        self.lbl_blur = QLabel("32", self)
        self.lbl_blur.setFixedWidth(32)
        blur_row.addWidget(self.slider_blur, stretch=1)
        blur_row.addWidget(self.lbl_blur)
        bg_form.addRow("Blur strength", blur_row)

        scale_row = QHBoxLayout()
        self.slider_fg_scale = QSlider(Qt.Orientation.Horizontal, self)
        self.slider_fg_scale.setRange(70, 100)
        self.slider_fg_scale.setValue(100)
        self.lbl_fg_scale = QLabel("100%", self)
        self.lbl_fg_scale.setFixedWidth(40)
        scale_row.addWidget(self.slider_fg_scale, stretch=1)
        scale_row.addWidget(self.lbl_fg_scale)
        bg_form.addRow("Foreground fit", scale_row)
        root.addWidget(bg_group)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("🎬 Export Resized Video")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        # Connections
        self.combo_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.spin_width.valueChanged.connect(self._on_dims_edited)
        self.spin_height.valueChanged.connect(self._on_dims_edited)
        self.combo_bg.currentIndexChanged.connect(self._refresh_preview)
        self.slider_blur.valueChanged.connect(self._on_blur_changed)
        self.slider_fg_scale.valueChanged.connect(self._on_fg_scale_changed)

        self._update_color_button()
        self._refresh_preview()

    @property
    def target_width(self) -> int:
        val = self.spin_width.value()
        return max(64, val - (val % 2))

    @property
    def target_height(self) -> int:
        val = self.spin_height.value()
        return max(64, val - (val % 2))

    @property
    def bg_style(self) -> str:
        return str(self.combo_bg.currentData() or "dominant_blur")

    @property
    def dominant_color(self) -> QColor:
        return QColor(self._dominant_color)

    @property
    def dominant_hex(self) -> str:
        return self._dominant_color.name()

    @property
    def blur_sigma(self) -> int:
        return int(self.slider_blur.value())

    @property
    def fg_scale_pct(self) -> int:
        return int(self.slider_fg_scale.value())

    def swap_dimensions(self) -> None:
        w, h = self.spin_width.value(), self.spin_height.value()
        self.spin_width.blockSignals(True)
        self.spin_height.blockSignals(True)
        self.spin_width.setValue(h)
        self.spin_height.setValue(w)
        self.spin_width.blockSignals(False)
        self.spin_height.blockSignals(False)
        self._sync_preset_to_dims()
        self._refresh_preview()

    def _on_preset_changed(self, index: int) -> None:
        if self._updating_preset:
            return
        data = self.combo_preset.itemData(index)
        if not data:
            return
        _preset_id, w, h = data
        if w > 0 and h > 0:
            self._updating_preset = True
            self.spin_width.setValue(w)
            self.spin_height.setValue(h)
            self._updating_preset = False
            self._refresh_preview()

    def _on_dims_edited(self) -> None:
        if self._updating_preset:
            return
        self._sync_preset_to_dims()
        self._refresh_preview()

    def _sync_preset_to_dims(self) -> None:
        w, h = self.target_width, self.target_height
        self._updating_preset = True
        matched_idx = self.combo_preset.count() - 1  # default to "custom"
        for i in range(self.combo_preset.count()):
            data = self.combo_preset.itemData(i)
            if data and data[1] == w and data[2] == h:
                matched_idx = i
                break
        self.combo_preset.setCurrentIndex(matched_idx)
        self._updating_preset = False

    def _on_blur_changed(self, value: int) -> None:
        self.lbl_blur.setText(str(value))
        self._refresh_preview()

    def _on_fg_scale_changed(self, value: int) -> None:
        self.lbl_fg_scale.setText(f"{value}%")
        self._refresh_preview()

    def _choose_color(self) -> None:
        chosen = QColorDialog.getColor(self._dominant_color, self, "Select Dominant Background Color")
        if chosen.isValid():
            self._dominant_color = chosen
            self._update_color_button()
            self._refresh_preview()

    def _reset_auto_color(self) -> None:
        self._dominant_color = QColor(self._auto_color)
        self._update_color_button()
        self._refresh_preview()

    def _update_color_button(self) -> None:
        hex_code = self._dominant_color.name().upper()
        luma = (
            0.299 * self._dominant_color.red()
            + 0.587 * self._dominant_color.green()
            + 0.114 * self._dominant_color.blue()
        )
        fg = "#0f172a" if luma > 140 else "#f8fafc"
        self.btn_pick_color.setText(f"🎨 {hex_code}")
        self.btn_pick_color.setStyleSheet(
            f"background-color: {hex_code}; color: {fg}; font-weight: bold; border-radius: 4px; padding: 4px 10px;"
        )

    def _refresh_preview(self) -> None:
        style = self.bg_style
        uses_blur = style in ("dominant_blur", "ambient_glow", "video_blur")
        uses_color = style in ("dominant_blur", "ambient_glow", "dominant_solid")
        self.slider_blur.setEnabled(uses_blur)
        self.btn_pick_color.setEnabled(uses_color)
        self.btn_auto_color.setEnabled(uses_color)
        self.slider_fg_scale.setEnabled(style != "crop")

        preview_img = render_resize_preview(
            self._frame,
            self.target_width,
            self.target_height,
            bg_style=style,
            dominant_color=self._dominant_color,
            blur_sigma=self.blur_sigma,
            fg_scale_pct=self.fg_scale_pct,
        )
        self.lbl_preview.setPixmap(QPixmap.fromImage(preview_img))
        src_info = (
            f"{self._frame.width()} × {self._frame.height()} px  →  "
            if (self._frame is not None and not self._frame.isNull())
            else ""
        )
        self.lbl_summary.setText(f"{src_info}{self.target_width} × {self.target_height} px  ({self.dominant_hex.upper()})")


class ClickableSlider(QSlider):
    """QSlider that jumps directly to the clicked mouse position on arbitrary points."""

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            opt = QStyleOptionSlider()
            self.initStyleOption(opt)
            style = self.style()
            hr = style.subControlRect(
                QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderHandle, self
            )
            half_hw = hr.width() // 2
            span = max(1, self.width() - hr.width())
            x = int(event.position().x())
            val = style.sliderValueFromPosition(
                self.minimum(), self.maximum(), x - half_hw, span, self.invertedAppearance()
            )
            clamped = max(self.minimum(), min(self.maximum(), val))
            self.setValue(clamped)
            self.sliderMoved.emit(clamped)
        super().mousePressEvent(event)


class TrimSeekSlider(ClickableSlider):
    """QSlider that renders In/Out trim markers and highlights the selected cut range."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.trim_in_pos: Optional[int] = None
        self.trim_out_pos: Optional[int] = None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.maximum() <= self.minimum():
            return
        if self.trim_in_pos is None and self.trim_out_pos is None:
            return

        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider, opt, QStyle.SubControl.SC_SliderGroove, self
        )
        if groove.width() <= 0:
            return

        min_val = self.minimum()
        max_val = self.maximum()
        val_range = max_val - min_val
        if val_range <= 0:
            return

        in_val = self.trim_in_pos if self.trim_in_pos is not None else min_val
        out_val = self.trim_out_pos if self.trim_out_pos is not None else max_val

        in_x = groove.x() + int((in_val - min_val) / val_range * groove.width())
        out_x = groove.x() + int((out_val - min_val) / val_range * groove.width())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Highlight span between In and Out
        if in_x < out_x:
            highlight_rect = QRect(in_x, groove.y(), out_x - in_x, groove.height())
            painter.fillRect(highlight_rect, QColor(2, 132, 199, 140))

        # Draw In marker tick (cyan left bracket)
        if self.trim_in_pos is not None:
            pen = QPen(QColor(56, 189, 248), 2)
            painter.setPen(pen)
            painter.drawLine(in_x, groove.y() - 3, in_x, groove.y() + groove.height() + 3)
            painter.drawLine(in_x, groove.y() - 3, in_x + 3, groove.y() - 3)
            painter.drawLine(in_x, groove.y() + groove.height() + 3, in_x + 3, groove.y() + groove.height() + 3)

        # Draw Out marker tick (cyan right bracket)
        if self.trim_out_pos is not None:
            pen = QPen(QColor(56, 189, 248), 2)
            painter.setPen(pen)
            painter.drawLine(out_x, groove.y() - 3, out_x, groove.y() + groove.height() + 3)
            painter.drawLine(out_x, groove.y() - 3, out_x - 3, groove.y() - 3)
            painter.drawLine(out_x, groove.y() + groove.height() + 3, out_x - 3, groove.y() + groove.height() + 3)

        painter.end()


class _ClickToPlayMixin:
    """Left click emits `clicked` (delayed so a double click can cancel it); double click emits `doubleClicked`.

    The host class must declare `clicked` and `doubleClicked` signals and call `_init_click_timer()`.
    """

    def _init_click_timer(self) -> None:
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(self.clicked.emit)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_timer.stop()
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class ClickableVideoWidget(_ClickToPlayMixin, QVideoWidget):
    """Hardware-accelerated video output used when no color adjustment is active."""

    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_click_timer()


class AdjustedVideoView(_ClickToPlayMixin, QWidget):
    """Video output that applies a brightness/contrast LUT to every frame before painting it.

    QVideoWidget renders on its own native/GPU surface, so nothing painted on top of it can
    change the video's pixels. This view instead receives decoded frames from its own
    QVideoSink, scales them down to the view size, runs them through the color LUT and paints
    them. It costs CPU, so the player only routes video here while an adjustment is active.
    """

    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._init_click_timer()
        self.sink = QVideoSink(self)
        self.sink.videoFrameChanged.connect(self._on_frame)
        self._lut: Optional[List[int]] = None
        self._source: Optional[QImage] = None  # latest frame, scaled to the view, before the LUT
        self._image: Optional[QImage] = None  # what gets painted
        self._pending: Optional[QVideoFrame] = None
        # Frames arrive faster than we may process them; coalesce so only the newest is converted.
        self._process_timer = QTimer(self)
        self._process_timer.setSingleShot(True)
        self._process_timer.setInterval(0)
        self._process_timer.timeout.connect(self._process_pending)

    def set_lut(self, lut: Optional[List[int]]) -> None:
        self._lut = lut
        self._render()

    def show_frame(self, frame: QVideoFrame) -> None:
        """Seed the view with a frame (e.g. the paused frame when switching into this view)."""
        self._pending = frame
        self._process_pending()

    def clear(self) -> None:
        self._pending = None
        self._source = None
        self._image = None
        self.update()

    def _on_frame(self, frame: QVideoFrame) -> None:
        self._pending = frame
        if not self._process_timer.isActive():
            self._process_timer.start()

    def _process_pending(self) -> None:
        frame, self._pending = self._pending, None
        if frame is None or not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        target = self.size() * self.devicePixelRatioF()
        if image.width() > target.width() or image.height() > target.height():
            image = image.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self._source = image
        self._render()

    def _render(self) -> None:
        if self._source is not None:
            self._image = apply_color_lut(self._source, self._lut) if self._lut else self._source
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.GlobalColor.black)
        if self._image is not None and not self._image.isNull():
            size = self._image.deviceIndependentSize().scaled(QSizeF(self.size()), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF(QPointF(0, 0), size)
            target.moveCenter(QRectF(self.rect()).center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self._image)
        painter.end()


class VideoPlayerWidget(QWidget):
    """Embedded video player with transport controls, AV1 compatibility banner, and transcode tool."""

    videoFinished = Signal()
    doubleClicked = Signal()
    transcodeCompleted = Signal(Path)
    trimCompleted = Signal(Path)
    whatsappCompleted = Signal(Path)
    resizeCompleted = Signal(Path)
    transformCompleted = Signal(Path, str)
    stripAudioCompleted = Signal(Path)
    captionCompleted = Signal(Path)
    colorExportCompleted = Signal(Path)
    colorExportFailed = Signal(str)
    jobStarted = Signal(str)
    jobProgress = Signal(str, int)
    jobEnded = Signal()
    jobCancelled = Signal(str)
    playbackFailed = Signal(str)  # user-facing message
    infoRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._in_slideshow: bool = False
        self.loop: bool = True  # replay at the end when not in a slideshow
        self._slider_dragging: bool = False
        self.trim_in_ms: Optional[int] = None
        self.trim_out_ms: Optional[int] = None

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)

        self.video_widget = ClickableVideoWidget(self)
        self.video_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.video_widget.clicked.connect(self.toggle_play_pause)
        self.video_widget.doubleClicked.connect(self.doubleClicked.emit)
        self.player.setVideoOutput(self.video_widget)

        self.adjusted_view = AdjustedVideoView(self)
        self.adjusted_view.clicked.connect(self.toggle_play_pause)
        self.adjusted_view.doubleClicked.connect(self.doubleClicked.emit)

        # Normal playback uses the GPU-backed QVideoWidget; color adjustments switch to AdjustedVideoView.
        self.video_stack = QStackedWidget(self)
        self.video_stack.addWidget(self.video_widget)
        self.video_stack.addWidget(self.adjusted_view)

        # Transport bar
        self.controls_bar = QWidget(self)
        self.controls_bar.setObjectName("videoControls")  # styled by theme.APP_QSS
        bar_layout = QHBoxLayout(self.controls_bar)
        bar_layout.setContentsMargins(8, 5, 8, 5)
        bar_layout.setSpacing(5)

        self.play_btn = QPushButton("⏸ Pause")
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_btn.clicked.connect(self.toggle_play_pause)
        bar_layout.addWidget(self.play_btn)

        self.time_label = QLabel("00:00 / 00:00")
        bar_layout.addWidget(self.time_label)

        self.seek_slider = TrimSeekSlider(self)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(self._on_slider_pressed)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        self.seek_slider.sliderMoved.connect(self._on_slider_moved)
        bar_layout.addWidget(self.seek_slider, stretch=1)

        # Trimming controls
        self.btn_set_in = QPushButton("[ In (I)")
        self.btn_set_in.setToolTip("Mark In-point (start) at current position (Shortcut: [ or I)")
        self.btn_set_in.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_set_in.clicked.connect(lambda: self.set_trim_in())
        bar_layout.addWidget(self.btn_set_in)

        self.btn_set_out = QPushButton("Out ] (O)")
        self.btn_set_out.setToolTip("Mark Out-point (end) at current position (Shortcut: ] or O)")
        self.btn_set_out.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_set_out.clicked.connect(lambda: self.set_trim_out())
        bar_layout.addWidget(self.btn_set_out)

        self.btn_trim = QPushButton("✂ Trim")
        self.btn_trim.setToolTip("Export trimmed video clip via instant lossless stream copy (Shortcut: T)")
        self.btn_trim.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_trim.setEnabled(False)
        self.btn_trim.clicked.connect(self.execute_trim)
        bar_layout.addWidget(self.btn_trim)

        self.btn_clear_trim = QPushButton("✕")
        self.btn_clear_trim.setToolTip("Clear trim markers (Shortcut: Esc)")
        self.btn_clear_trim.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_clear_trim.setFixedWidth(26)
        self.btn_clear_trim.setVisible(False)
        self.btn_clear_trim.clicked.connect(self.clear_trim)
        bar_layout.addWidget(self.btn_clear_trim)

        self.btn_whatsapp = QPushButton("📱 WhatsApp")
        self.btn_whatsapp.setToolTip(
            "Export a WhatsApp Web friendly MP4 (lowered bitrate, H.264 + AAC, under 16 MB limit) (Shortcut: K)"
        )
        self.btn_whatsapp.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_whatsapp.clicked.connect(self.export_whatsapp)
        bar_layout.addWidget(self.btn_whatsapp)

        self.btn_resize = QPushButton("📐 Resize")
        self.btn_resize.setToolTip(
            "Reframe & Resize Video: convert vertical ↔ landscape with CapCut-style blurred dominant color backdrop"
        )
        self.btn_resize.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_resize.clicked.connect(self.open_resize_dialog)
        bar_layout.addWidget(self.btn_resize)

        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_btn.setFixedWidth(32)
        self.mute_btn.clicked.connect(self.toggle_mute)
        bar_layout.addWidget(self.mute_btn)

        self.volume_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(68)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        bar_layout.addWidget(self.volume_slider)

        self.speed_combo = QComboBox()
        self.speed_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for rate in PLAYBACK_SPEEDS:
            self.speed_combo.addItem(f"{rate:g}x", rate)
        self.speed_combo.setCurrentIndex(PLAYBACK_SPEEDS.index(1.0))
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        bar_layout.addWidget(self.speed_combo)

        self.btn_info = QPushButton("ℹ")
        self.btn_info.setToolTip("Media Inspector: Bitrate, Codec, Compression & Tech Specs (Ctrl+I)")
        self.btn_info.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_info.setFixedWidth(28)
        self.btn_info.clicked.connect(self.infoRequested.emit)
        bar_layout.addWidget(self.btn_info)

        # AV1 compatibility banner
        self.banner = QWidget(self)
        self.banner.setVisible(False)
        self.banner.setObjectName("av1Banner")  # styled by theme.APP_QSS
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 6, 12, 6)
        self.lbl_banner = QLabel("⚠️ AV1 video stream: playing audio only (GPU lacks hardware AV1 decoder).", self.banner)
        self.btn_transcode = QPushButton("⚡ Convert to MP4 (H.264)", self.banner)
        self.btn_transcode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_transcode.clicked.connect(self._on_transcode_clicked)
        banner_layout.addWidget(self.lbl_banner, stretch=1)
        banner_layout.addWidget(self.btn_transcode)

        self._current_path: Optional[Path] = None
        # At most one ffmpeg job (trim, transcode, WhatsApp, resize, color) runs at a time.
        self._job: Optional[_FfmpegJob] = None
        self._job_thread: Optional[QThread] = None
        self._job_desc: str = ""
        self._finished_emitted: bool = False
        self._codec_probe = _CodecProbeSignals(self)
        self._codec_probe.detected.connect(self._on_codec_detected)

        # Background job progress strip (shown above transport controls while FFmpeg is working)
        self.job_bar = QWidget(self)
        self.job_bar.setObjectName("videoJobBar")
        self.job_bar.setVisible(False)
        job_layout = QHBoxLayout(self.job_bar)
        job_layout.setContentsMargins(10, 4, 10, 4)
        job_layout.setSpacing(8)
        self.lbl_job = QLabel("", self.job_bar)
        self.job_progress = QProgressBar(self.job_bar)
        self.job_progress.setTextVisible(False)
        self.job_progress.setFixedHeight(8)
        self.lbl_job_pct = QLabel("", self.job_bar)
        self.lbl_job_pct.setObjectName("jobPctLabel")
        self.lbl_job_pct.setFixedWidth(44)
        self.lbl_job_pct.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.btn_cancel_job = QPushButton("✕ Cancel", self.job_bar)
        self.btn_cancel_job.setToolTip("Cancel the running video export")
        self.btn_cancel_job.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_cancel_job.clicked.connect(self.cancel_active_job)
        job_layout.addWidget(self.lbl_job)
        job_layout.addWidget(self.job_progress, stretch=1)
        job_layout.addWidget(self.lbl_job_pct)
        job_layout.addWidget(self.btn_cancel_job)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.banner)
        root_layout.addWidget(self.video_stack, stretch=1)
        root_layout.addWidget(self.job_bar)
        root_layout.addWidget(self.controls_bar)

        # Signals
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._on_player_error)

    def load_video(self, path: Path, slideshow_active: bool = False) -> None:
        self._current_path = path
        self._in_slideshow = slideshow_active
        self._finished_emitted = False
        self.clear_trim()
        self.set_color_scaling(0, 0)
        self.banner.setVisible(False)

        # Probe for AV1 streams off the UI thread (ffprobe can take a second or more);
        # the banner is shown when the result arrives, if this file is still current.
        if path.suffix.lower() in (".webm", ".mkv"):
            probe = self._codec_probe
            QThreadPool.globalInstance().start(
                lambda: probe.detected.emit(str(path), detect_video_codec(path) or "")
            )

        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _on_codec_detected(self, path_str: str, codec: str) -> None:
        if codec != "av1" or self._current_path is None or str(self._current_path) != path_str:
            return
        self.lbl_banner.setText("⚠️ AV1 video stream: playing audio only (GPU lacks hardware AV1 decoder).")
        self.btn_transcode.setEnabled(self._job is None)
        self.btn_transcode.setText("⚡ Convert to MP4 (H.264)")
        self.banner.setVisible(True)

    # ------------------------------------------------------------------
    # Background ffmpeg jobs
    # ------------------------------------------------------------------

    def is_job_running(self) -> bool:
        return self._job is not None

    def _start_job(self, job: _FfmpegJob, on_success, on_failure) -> bool:
        if self._job is not None:
            QMessageBox.information(self, "Busy", "Another video export is still running. Please wait for it to finish.")
            return False
        if job.expected_duration_sec is None and self.player.duration() > 0:
            job.expected_duration_sec = self.player.duration() / 1000.0
        self._job_desc = f"{job.label}: {job.dest_path.name}"
        self.lbl_job.setText(f"⏳ {self._job_desc}")
        self.lbl_job_pct.setText("...")
        self.job_progress.setRange(0, 0)  # pulsing indeterminate bar until the first percentage arrives
        self.job_bar.setVisible(True)

        thread = QThread(self)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.progress.connect(self._on_job_progress)
        job.finished.connect(on_success)
        job.failed.connect(on_failure)
        job.done.connect(thread.quit)
        thread.finished.connect(self._on_job_thread_finished)
        thread.finished.connect(job.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._job, self._job_thread = job, thread
        self.jobStarted.emit(self._job_desc)
        thread.start()
        return True

    def _on_job_progress(self, pct: int) -> None:
        if pct <= 0:
            self.job_progress.setRange(0, 0)
            self.lbl_job_pct.setText("...")
        else:
            self.job_progress.setRange(0, 100)
            self.job_progress.setValue(pct)
            self.lbl_job_pct.setText(f"{pct}%")
        self.jobProgress.emit(self._job_desc, pct)

    def _on_job_thread_finished(self) -> None:
        self._job = None
        self._job_thread = None
        self.job_bar.setVisible(False)
        self.jobEnded.emit()

    def _restore_job_buttons(self) -> None:
        self.btn_whatsapp.setEnabled(True)
        self.btn_whatsapp.setText("📱 WhatsApp")
        self.btn_resize.setEnabled(True)
        self.btn_resize.setText("📐 Resize")
        self.btn_transcode.setEnabled(True)
        self.btn_transcode.setText("⚡ Convert to MP4 (H.264)")
        self._update_trim_ui()

    def cancel_active_job(self) -> bool:
        """Cancel the currently running background FFmpeg job and restore button states."""
        job, thread = self._job, self._job_thread
        if job is None or thread is None:
            return False
        label = job.label
        job.cancel()
        thread.quit()
        thread.wait(3000)
        self._job = None
        self._job_thread = None
        self.job_bar.setVisible(False)
        self._restore_job_buttons()
        self.jobEnded.emit()
        self.jobCancelled.emit(label)
        return True

    def shutdown(self) -> None:
        """Cancel any running ffmpeg job and wait for its thread (call before the app exits)."""
        job, thread = self._job, self._job_thread
        if job is not None and thread is not None:
            job.cancel()
            thread.quit()
            thread.wait(5000)
        self._job = None
        self._job_thread = None
        self.job_bar.setVisible(False)

    def _on_transcode_clicked(self) -> None:
        if self._current_path is None:
            return
        dest_path = self._current_path.parent / f"{self._current_path.stem}.mp4"
        counter = 2
        while dest_path.exists():
            dest_path = self._current_path.parent / f"{self._current_path.stem}_{counter}.mp4"
            counter += 1

        job = _TranscodeWorker(self._current_path, dest_path)
        if self._start_job(job, self._on_transcode_success, self._on_transcode_failed):
            self.btn_transcode.setEnabled(False)
            self.btn_transcode.setText("⏳ Converting...")

    def _on_transcode_success(self, new_path: Path) -> None:
        self.banner.setVisible(False)
        self.transcodeCompleted.emit(new_path)

    def _on_transcode_failed(self, error: str) -> None:
        self.lbl_banner.setText(f"❌ {error}")
        self.btn_transcode.setEnabled(True)
        self.btn_transcode.setText("Retry")

    # ------------------------------------------------------------------
    # Video Trimming (In / Out points & FFmpeg Stream Copy)
    # ------------------------------------------------------------------

    def has_trim_points(self) -> bool:
        return self.trim_in_ms is not None or self.trim_out_ms is not None

    def set_trim_in(self, ms: Optional[int] = None) -> None:
        dur = self.player.duration()
        pos = self.player.position() if ms is None else ms
        if dur > 0:
            pos = max(0, min(dur, pos))
        self.trim_in_ms = pos
        if self.trim_out_ms is not None and self.trim_out_ms <= self.trim_in_ms:
            self.trim_out_ms = None
        self._update_trim_ui()

    def set_trim_out(self, ms: Optional[int] = None) -> None:
        dur = self.player.duration()
        pos = self.player.position() if ms is None else ms
        if dur > 0:
            pos = max(0, min(dur, pos))
        self.trim_out_ms = pos
        if self.trim_in_ms is not None and self.trim_in_ms >= self.trim_out_ms:
            self.trim_in_ms = None
        self._update_trim_ui()

    def clear_trim(self) -> None:
        self.trim_in_ms = None
        self.trim_out_ms = None
        self._update_trim_ui()

    def _update_trim_ui(self) -> None:
        self.seek_slider.trim_in_pos = self.trim_in_ms
        self.seek_slider.trim_out_pos = self.trim_out_ms
        self.seek_slider.update()

        dur = self.player.duration()
        in_str = f"[{_format_ms(self.trim_in_ms)}" if self.trim_in_ms is not None else "[ In (I)"
        out_str = f"{_format_ms(self.trim_out_ms)}]" if self.trim_out_ms is not None else "Out ] (O)"
        self.btn_set_in.setText(in_str)
        self.btn_set_out.setText(out_str)

        has_any = self.has_trim_points()
        self.btn_clear_trim.setVisible(has_any)

        if has_any:
            start_ms = self.trim_in_ms if self.trim_in_ms is not None else 0
            end_ms = self.trim_out_ms if self.trim_out_ms is not None else dur
            clip_len = max(0, end_ms - start_ms)
            self.btn_trim.setEnabled(clip_len > 0)
            self.btn_trim.setText(f"✂ Trim ({_format_ms(clip_len)})")
            self.btn_trim.setObjectName("btn_trim_ready")
        else:
            self.btn_trim.setEnabled(False)
            self.btn_trim.setText("✂ Trim")
            self.btn_trim.setObjectName("")

        self.btn_trim.style().polish(self.btn_trim)

    def execute_trim(self) -> None:
        if self._current_path is None:
            return
        dur = self.player.duration()
        start_ms = self.trim_in_ms if self.trim_in_ms is not None else 0
        end_ms = self.trim_out_ms if self.trim_out_ms is not None else dur
        if end_ms <= start_ms:
            return

        dest_path = _next_variant_path(self._current_path, "trim", self._current_path.suffix)

        job = _TrimWorker(self._current_path, dest_path, start_ms, end_ms)
        if self._start_job(job, self._on_trim_success, self._on_trim_failed):
            self.btn_trim.setEnabled(False)
            self.btn_trim.setText("⏳ Trimming...")

    def _on_trim_success(self, new_path: Path) -> None:
        self.clear_trim()
        self.trimCompleted.emit(new_path)

    def _on_trim_failed(self, error: str) -> None:
        self._update_trim_ui()
        QMessageBox.warning(self, "Trim Error", error)

    def export_whatsapp(self) -> Optional[Path]:
        """Export a WhatsApp Web friendly MP4 (<name>_whatsapp.mp4) with lowered bitrate."""
        if self._current_path is None:
            return None
        dest_path = _next_variant_path(self._current_path, "whatsapp", ".mp4")
        dur_ms = self.player.duration()
        duration_sec = (dur_ms / 1000.0) if dur_ms > 0 else None
        job = _WhatsAppWorker(self._current_path, dest_path, duration_sec=duration_sec)
        if not self._start_job(job, self._on_whatsapp_success, self._on_whatsapp_failed):
            return None
        self.btn_whatsapp.setEnabled(False)
        self.btn_whatsapp.setText("⏳ WhatsApp...")
        return dest_path

    def _on_whatsapp_success(self, new_path: Path) -> None:
        self.btn_whatsapp.setEnabled(True)
        self.btn_whatsapp.setText("📱 WhatsApp")
        self.whatsappCompleted.emit(new_path)

    def _on_whatsapp_failed(self, error: str) -> None:
        self.btn_whatsapp.setEnabled(True)
        self.btn_whatsapp.setText("📱 WhatsApp")
        QMessageBox.warning(self, "WhatsApp Export Error", error)

    def open_resize_dialog(self) -> Optional[Path]:
        """Open the interactive CapCut-style video resize/reframe dialog and start export if accepted."""
        if self._current_path is None:
            return None
        frame = self.grab_current_frame()
        dlg = VideoResizeDialog(frame=frame, parent=self.window() or self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.export_resized(
            width=dlg.target_width,
            height=dlg.target_height,
            bg_style=dlg.bg_style,
            dominant_hex=dlg.dominant_hex,
            blur_sigma=dlg.blur_sigma,
            fg_scale_pct=dlg.fg_scale_pct,
        )

    def export_resized(
        self,
        width: int,
        height: int,
        bg_style: str = "dominant_blur",
        dominant_hex: str = "#1e293b",
        blur_sigma: int = 32,
        fg_scale_pct: int = 100,
    ) -> Optional[Path]:
        """Start writing <name>_<W>x<H>.mp4 with the chosen backdrop in a background FFmpeg job."""
        if self._current_path is None:
            return None
        w = max(64, int(width) - (int(width) % 2))
        h = max(64, int(height) - (int(height) % 2))
        dest_path = _next_variant_path(self._current_path, f"{w}x{h}", ".mp4")
        job = _ResizeWorker(
            self._current_path,
            dest_path,
            width=w,
            height=h,
            bg_style=bg_style,
            dominant_hex=dominant_hex,
            blur_sigma=blur_sigma,
            fg_scale_pct=fg_scale_pct,
        )
        if not self._start_job(job, self._on_resize_success, self._on_resize_failed):
            return None
        self.btn_resize.setEnabled(False)
        self.btn_resize.setText("⏳ Resizing...")
        return dest_path

    def _on_resize_success(self, new_path: Path) -> None:
        self.btn_resize.setEnabled(True)
        self.btn_resize.setText("📐 Resize")
        self.resizeCompleted.emit(new_path)

    def _on_resize_failed(self, error: str) -> None:
        self.btn_resize.setEnabled(True)
        self.btn_resize.setText("📐 Resize")
        QMessageBox.warning(self, "Video Resize Error", error)

    def transform_video(self, transform: str) -> Optional[Path]:
        """Rotate or flip the current video (rot_right, rot_left, flip_h, flip_v) and save as <name>_<tag>.mp4."""
        if self._current_path is None or transform not in VIDEO_TRANSFORMS:
            return None
        _vf, tag, label = VIDEO_TRANSFORMS[transform]
        dest_path = _next_variant_path(self._current_path, tag, ".mp4")
        job = _TransformVideoWorker(self._current_path, dest_path, transform=transform)
        if not self._start_job(
            job,
            lambda p, lbl=label: self.transformCompleted.emit(p, lbl),
            lambda err: QMessageBox.warning(self, "Video Transform Error", err),
        ):
            return None
        return dest_path

    def strip_audio(self) -> Optional[Path]:
        """Losslessly strip the audio track from the current video and save as <name>_muted.<ext>."""
        if self._current_path is None:
            return None
        ext = self._current_path.suffix if self._current_path.suffix else ".mp4"
        dest_path = _next_variant_path(self._current_path, "muted", ext)
        job = _StripAudioWorker(self._current_path, dest_path)
        if not self._start_job(
            job,
            self.stripAudioCompleted.emit,
            lambda err: QMessageBox.warning(self, "Strip Audio Error", err),
        ):
            return None
        return dest_path

    def trim_range_bounds_ms(self) -> Optional[tuple[int, int]]:
        """Return `(start_ms, end_ms)` when at least one trim point is marked on a known-duration video."""
        if not self.has_trim_points():
            return None
        dur = self.player.duration()
        start_ms = self.trim_in_ms if self.trim_in_ms is not None else 0
        end_ms = self.trim_out_ms if self.trim_out_ms is not None else (dur if dur > 0 else 0)
        if end_ms > start_ms:
            return start_ms, end_ms
        return None

    def export_captioned_video(self, spec, *, fallback_size: tuple[int, int] = (1280, 720)) -> Optional[Path]:
        """Burn `spec` (CaptionSpec) onto the current video and save as <name>_caption.mp4."""
        if self._current_path is None or not getattr(spec, "text", "").strip():
            return None
        dest_path = _next_variant_path(self._current_path, "caption", ".mp4")
        trim_bounds = self.trim_range_bounds_ms() if getattr(spec, "only_trim_range", False) else None
        job = _CaptionVideoWorker(
            self._current_path,
            dest_path,
            spec,
            fallback_size=fallback_size,
            trim_range_ms=trim_bounds,
        )
        if not self._start_job(
            job,
            self.captionCompleted.emit,
            lambda err: QMessageBox.warning(self, "Video Caption Error", err),
        ):
            return None
        return dest_path

    def export_color_adjusted(self, brightness: int, contrast: int) -> Optional[Path]:
        """Start writing <name>_color.mp4 with the adjustment baked in. Returns the target path if started."""
        if self._current_path is None or (brightness == 0 and contrast == 0):
            return None
        dest_path = _next_variant_path(self._current_path, "color", ".mp4")
        job = _ColorExportWorker(self._current_path, dest_path, brightness, contrast)
        if not self._start_job(job, self.colorExportCompleted.emit, self.colorExportFailed.emit):
            return None
        return dest_path

    def stop(self) -> None:
        self.banner.setVisible(False)
        self.clear_trim()
        self.set_color_scaling(0, 0)
        self.player.stop()
        self.player.setSource(QUrl())

    def set_slideshow_mode(self, active: bool) -> None:
        self._in_slideshow = active
        if active and self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self.player.play()

    def toggle_play_pause(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_relative_ms(self, delta_ms: int) -> None:
        dur = self.player.duration()
        if dur <= 0:
            return
        new_pos = max(0, min(dur, self.player.position() + delta_ms))
        self.player.setPosition(new_pos)

    def toggle_mute(self) -> None:
        self.set_muted(not self.audio.isMuted())

    def set_muted(self, muted: bool) -> None:
        self.audio.setMuted(muted)
        self.mute_btn.setText("🔇" if muted else "🔊")

    @property
    def volume(self) -> int:
        return self.volume_slider.value()

    def set_volume(self, value: int) -> None:
        self.volume_slider.setValue(max(0, min(100, int(value))))

    def _on_volume_changed(self, value: int) -> None:
        self.audio.setVolume(value / 100.0)
        if value > 0 and self.audio.isMuted():
            self.audio.setMuted(False)
            self.mute_btn.setText("🔊")

    @property
    def color_adjusting(self) -> bool:
        return self.video_stack.currentWidget() is self.adjusted_view

    def set_color_scaling(self, brightness: int, contrast: int) -> None:
        """Apply brightness/contrast to live playback (0, 0 returns to the hardware video path)."""
        active = brightness != 0 or contrast != 0
        self.adjusted_view.set_lut(color_lut(brightness, contrast) if active else None)
        if active and not self.color_adjusting:
            paused_frame = self.video_widget.videoSink().videoFrame()
            self.player.setVideoSink(self.adjusted_view.sink)
            self.video_stack.setCurrentWidget(self.adjusted_view)
            self.adjusted_view.show_frame(paused_frame)
        elif not active and self.color_adjusting:
            self.player.setVideoOutput(self.video_widget)
            self.video_stack.setCurrentWidget(self.video_widget)
            self.adjusted_view.clear()

    def grab_current_frame(self) -> Optional[QImage]:
        """Extract the current video frame as a high-resolution QImage."""
        try:
            sink = self.adjusted_view.sink if self.color_adjusting else self.video_widget.videoSink()
            if sink:
                frame = sink.videoFrame()
                if frame.isValid():
                    img = frame.toImage()
                    if not img.isNull():
                        return img
        except Exception:
            pass

        cur = self._current_path
        if not cur or not cur.exists():
            return None
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        try:
            pos_sec = max(0.0, self.player.position() / 1000.0)
            cmd = [
                ffmpeg,
                "-ss",
                str(pos_sec),
                "-i",
                str(cur),
                "-vframes",
                "1",
                "-f",
                "image2pipe",
                "-vcodec",
                "png",
                "-",
            ]
            res = subprocess.run(cmd, capture_output=True, timeout=2.5, **no_window())
            if res.returncode == 0 and res.stdout:
                img = QImage()
                if img.loadFromData(res.stdout):
                    return img
        except Exception:
            pass
        return None

    def step_speed(self, direction: int) -> float:
        """Next faster (+1) / slower (-1) playback speed; returns the new rate."""
        index = max(0, min(self.speed_combo.count() - 1, self.speed_combo.currentIndex() + direction))
        self.speed_combo.setCurrentIndex(index)
        return float(self.speed_combo.itemData(index))

    def step_frame(self, direction: int) -> None:
        """Pause and move one frame forward (+1) or back (-1)."""
        self.player.pause()
        fps = self.player.metaData().value(QMediaMetaData.Key.VideoFrameRate)
        try:
            frame_ms = 1000.0 / float(fps) if fps and float(fps) > 0 else 1000.0 / 30
        except (TypeError, ValueError):
            frame_ms = 1000.0 / 30
        new_pos = self.player.position() + direction * max(1, round(frame_ms))
        dur = self.player.duration()
        self.player.setPosition(max(0, min(dur, new_pos) if dur > 0 else new_pos))

    def _on_speed_changed(self, index: int) -> None:
        rate = self.speed_combo.itemData(index)
        if rate:
            self.player.setPlaybackRate(float(rate))

    def _on_slider_pressed(self) -> None:
        self._slider_dragging = True
        self.player.setPosition(self.seek_slider.value())
        dur = self.player.duration()
        self.time_label.setText(f"{_format_ms(self.seek_slider.value())} / {_format_ms(dur)}")

    def _on_slider_released(self) -> None:
        self._slider_dragging = False
        self.player.setPosition(self.seek_slider.value())

    def _on_slider_moved(self, pos: int) -> None:
        dur = self.player.duration()
        self.time_label.setText(f"{_format_ms(pos)} / {_format_ms(dur)}")

    def _on_position_changed(self, pos: int) -> None:
        if not self._slider_dragging:
            self.seek_slider.setValue(pos)
            dur = self.player.duration()
            self.time_label.setText(f"{_format_ms(pos)} / {_format_ms(dur)}")

    def _on_duration_changed(self, dur: int) -> None:
        self.seek_slider.setRange(0, dur)
        pos = self.player.position()
        self.time_label.setText(f"{_format_ms(pos)} / {_format_ms(dur)}")

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.play_btn.setText("⏸ Pause")
        else:
            self.play_btn.setText("▶ Play")

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._in_slideshow:
                self._emit_finished_once()
            elif self.loop:
                self.player.setPosition(0)
                self.player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            # Never loop on a file that can't be played: replaying it fails again forever.
            if self._in_slideshow:
                self._emit_finished_once()

    def _on_player_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        name = self._current_path.name if self._current_path else "video"
        self.playbackFailed.emit(f"Can't play {name}: {error_string or 'unsupported or damaged file'}")
        if self._in_slideshow:
            self._emit_finished_once()

    def _emit_finished_once(self) -> None:
        # A broken file reports both InvalidMedia and errorOccurred; advance the slideshow only once.
        if not self._finished_emitted:
            self._finished_emitted = True
            self.videoFinished.emit()
