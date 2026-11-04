"""Lightweight Qt6 hardware-accelerated video player widget for HakuView."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from typing import List, Optional

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSizeF, QThread, QThreadPool, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoFrame, QVideoSink
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)


from .image_ops import _next_variant_path, apply_color_lut, color_lut, color_lut_params


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
        )
        return res.stdout.strip().lower() or None
    except Exception:
        return None


class _CodecProbeSignals(QObject):
    """Carries an async ffprobe result (path, codec name) back to the UI thread."""

    detected = Signal(str, str)


class _FfmpegJob(QObject):
    """Runs one or more ffmpeg attempts in a worker thread; cancel() kills the running process.

    Each attempt is (command, min_output_bytes); the first that exits 0 and produces a large
    enough file wins. `done` is always emitted last so the owning QThread can quit, even when
    the job was cancelled (cancellation emits neither `finished` nor `failed`).
    """

    finished = Signal(Path)
    failed = Signal(str)
    done = Signal()

    label = "FFmpeg job"

    def __init__(self, src_path: Path, dest_path: Path) -> None:
        super().__init__()
        self.src_path = src_path
        self.dest_path = dest_path
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
        try:
            for cmd, min_bytes in self._attempts(ffmpeg):
                if self._cancelled:
                    break
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                _, stderr = self._proc.communicate()
                if (
                    self._proc.returncode == 0
                    and self.dest_path.exists()
                    and self.dest_path.stat().st_size > min_bytes
                ):
                    self.finished.emit(self.dest_path)
                    return
                err_msg = stderr.decode("utf-8", errors="replace")[-300:]
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
        super().__init__(src_path, dest_path)
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
    colorExportCompleted = Signal(Path)
    colorExportFailed = Signal(str)
    infoRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._in_slideshow: bool = False
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
        bar_layout.setContentsMargins(10, 6, 10, 6)
        bar_layout.setSpacing(8)

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

        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_btn.setFixedWidth(38)
        self.mute_btn.clicked.connect(self.toggle_mute)
        bar_layout.addWidget(self.mute_btn)

        self.volume_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(85)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        bar_layout.addWidget(self.volume_slider)

        self.speed_combo = QComboBox()
        self.speed_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for label, rate in [("0.5x", 0.5), ("1.0x", 1.0), ("1.25x", 1.25), ("1.5x", 1.5), ("2.0x", 2.0)]:
            self.speed_combo.addItem(label, rate)
        self.speed_combo.setCurrentIndex(1)
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
        # At most one ffmpeg job (trim or transcode) runs at a time.
        self._job: Optional[_FfmpegJob] = None
        self._job_thread: Optional[QThread] = None
        self._finished_emitted: bool = False
        self._codec_probe = _CodecProbeSignals(self)
        self._codec_probe.detected.connect(self._on_codec_detected)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.banner)
        root_layout.addWidget(self.video_stack, stretch=1)
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
        thread = QThread(self)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.finished.connect(on_success)
        job.failed.connect(on_failure)
        job.done.connect(thread.quit)
        thread.finished.connect(self._on_job_thread_finished)
        thread.finished.connect(job.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._job, self._job_thread = job, thread
        thread.start()
        return True

    def _on_job_thread_finished(self) -> None:
        self._job = None
        self._job_thread = None

    def shutdown(self) -> None:
        """Cancel any running ffmpeg job and wait for its thread (call before the app exits)."""
        job, thread = self._job, self._job_thread
        if job is not None and thread is not None:
            job.cancel()
            thread.quit()
            thread.wait(5000)
        self._job = None
        self._job_thread = None

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
        muted = not self.audio.isMuted()
        self.audio.setMuted(muted)
        self.mute_btn.setText("🔇" if muted else "🔊")

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
            res = subprocess.run(cmd, capture_output=True, timeout=2.5)
            if res.returncode == 0 and res.stdout:
                img = QImage()
                if img.loadFromData(res.stdout):
                    return img
        except Exception:
            pass
        return None

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
            else:
                self.player.setPosition(0)
                self.player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            # Never loop on a file that can't be played: replaying it fails again forever.
            if self._in_slideshow:
                self._emit_finished_once()

    def _on_player_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        if self._in_slideshow:
            self._emit_finished_once()

    def _emit_finished_once(self) -> None:
        # A broken file reports both InvalidMedia and errorOccurred; advance the slideshow only once.
        if not self._finished_emitted:
            self._finished_emitted = True
            self.videoFinished.emit()
