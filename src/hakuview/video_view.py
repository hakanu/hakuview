"""Lightweight Qt6 hardware-accelerated video player widget for HakuView."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional

from PySide6.QtCore import QObject, QRect, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPen, QResizeEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
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
        )
        return res.stdout.strip().lower() or None
    except Exception:
        return None


class _TranscodeWorker(QObject):
    finished = Signal(Path)
    failed = Signal(str)

    def __init__(self, src_path: Path, dest_path: Path) -> None:
        super().__init__()
        self.src_path = src_path
        self.dest_path = dest_path

    def run(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.failed.emit("ffmpeg is not installed on this system.")
            return
        try:
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(self.src_path),
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "22",
                "-c:a",
                "copy",
                str(self.dest_path),
            ]
            res = subprocess.run(cmd, capture_output=True, check=False)
            if res.returncode == 0 and self.dest_path.exists():
                self.finished.emit(self.dest_path)
            else:
                err_msg = res.stderr.decode("utf-8", errors="replace")[-300:]
                self.failed.emit(f"Transcoding failed:\n{err_msg}")
        except Exception as exc:
            self.failed.emit(str(exc))


class _TrimWorker(QObject):
    """Worker performing lossless stream-copy video trimming with FFmpeg in a background thread."""

    finished = Signal(Path)
    failed = Signal(str)

    def __init__(self, src_path: Path, dest_path: Path, start_ms: int, end_ms: int) -> None:
        super().__init__()
        self.src_path = src_path
        self.dest_path = dest_path
        self.start_ms = start_ms
        self.end_ms = end_ms

    def run(self) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.failed.emit("ffmpeg is not installed on this system.")
            return

        start_ts = _format_ffmpeg_timestamp(self.start_ms)
        end_ts = _format_ffmpeg_timestamp(self.end_ms)

        try:
            # 1. First attempt: Instant lossless stream copy (-c copy)
            cmd = [
                ffmpeg,
                "-y",
                "-ss",
                start_ts,
                "-to",
                end_ts,
                "-i",
                str(self.src_path),
                "-c",
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                str(self.dest_path),
            ]
            res = subprocess.run(cmd, capture_output=True, check=False)
            if res.returncode == 0 and self.dest_path.exists() and self.dest_path.stat().st_size > 1000:
                self.finished.emit(self.dest_path)
                return

            # 2. Fallback: Fast visually lossless re-encode if container rejects stream-copy cut
            cmd_fallback = [
                ffmpeg,
                "-y",
                "-ss",
                start_ts,
                "-to",
                end_ts,
                "-i",
                str(self.src_path),
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "ultrafast",
                "-c:a",
                "copy",
                str(self.dest_path),
            ]
            res2 = subprocess.run(cmd_fallback, capture_output=True, check=False)
            if res2.returncode == 0 and self.dest_path.exists() and self.dest_path.stat().st_size > 0:
                self.finished.emit(self.dest_path)
            else:
                err_msg = res2.stderr.decode("utf-8", errors="replace")[-300:]
                self.failed.emit(f"Trimming failed:\n{err_msg}")
        except Exception as exc:
            self.failed.emit(str(exc))


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


class VideoColorOverlay(QWidget):
    """Semi-transparent visual overlay for brightness & contrast scaling on video widgets."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.brightness: int = 0
        self.contrast: int = 0

    def set_color(self, brightness: int, contrast: int) -> None:
        if self.brightness == brightness and self.contrast == contrast:
            return
        self.brightness = brightness
        self.contrast = contrast
        self.update()

    def paintEvent(self, event) -> None:
        if self.brightness == 0 and self.contrast == 0:
            return
        painter = QPainter(self)
        rect = self.rect()

        # 1. Brightness scaling
        if self.brightness > 0:
            alpha = int(min(250, self.brightness * 2.2))
            painter.fillRect(rect, QColor(255, 255, 255, alpha))
        elif self.brightness < 0:
            alpha = int(min(250, -self.brightness * 2.2))
            painter.fillRect(rect, QColor(0, 0, 0, alpha))

        # 2. Contrast scaling
        if self.contrast > 0:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Overlay)
            alpha = int(min(240, self.contrast * 2.0))
            painter.fillRect(rect, QColor(128, 128, 128, alpha))
        elif self.contrast < 0:
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            alpha = int(min(220, -self.contrast * 1.8))
            painter.fillRect(rect, QColor(128, 128, 128, alpha))

        painter.end()


class ClickableVideoWidget(QVideoWidget):
    """QVideoWidget that emits clicked and doubleClicked signals on mouse events."""

    clicked = Signal()
    doubleClicked = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.setInterval(220)
        self._click_timer.timeout.connect(self.clicked.emit)
        self.color_overlay = VideoColorOverlay(self)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "color_overlay"):
            self.color_overlay.setGeometry(self.rect())
            self.color_overlay.raise_()

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


class VideoPlayerWidget(QWidget):
    """Embedded video player with transport controls, AV1 compatibility banner, and transcode tool."""

    videoFinished = Signal()
    doubleClicked = Signal()
    transcodeCompleted = Signal(Path)
    trimCompleted = Signal(Path)
    infoRequested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._loop_when_not_slideshow: bool = True
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

        # Transport bar
        self.controls_bar = QWidget(self)
        self.controls_bar.setStyleSheet(
            """
            QWidget {
                background-color: #161920;
                color: #e2e8f0;
                font-size: 12px;
            }
            QPushButton {
                background-color: #262b36;
                border: 1px solid #374151;
                border-radius: 4px;
                padding: 4px 10px;
                color: #f1f5f9;
            }
            QPushButton:hover {
                background-color: #334155;
            }
            QPushButton:disabled {
                background-color: #1e222a;
                color: #64748b;
                border-color: #282f3a;
            }
            QPushButton#btn_trim_ready {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: bold;
                border: 1px solid #38bdf8;
            }
            QPushButton#btn_trim_ready:hover {
                background-color: #0369a1;
            }
            QComboBox {
                background-color: #262b36;
                border: 1px solid #374151;
                border-radius: 4px;
                padding: 2px 20px 2px 6px;
                color: #f1f5f9;
            }
            QComboBox:hover {
                background-color: #2e3442;
                border-color: #4b5563;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 18px;
                border-left: 1px solid #374151;
            }
            QComboBox::down-arrow {
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #94a3b8;
                margin-right: 2px;
            }
            QComboBox::down-arrow:hover {
                border-top: 5px solid #38bdf8;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1d25;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 4px;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
                outline: none;
            }
            QComboBox QAbstractItemView::item {
                min-height: 22px;
                padding: 3px 8px;
                color: #e2e8f0;
                background-color: #1a1d25;
            }
            QComboBox QAbstractItemView::item:hover,
            QComboBox QAbstractItemView::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
            """
        )
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
        self.banner.setStyleSheet(
            """
            QWidget {
                background-color: #2e1515;
                border-bottom: 1px solid #7f1d1d;
                color: #fecaca;
                font-size: 12px;
            }
            QPushButton {
                background-color: #ef4444;
                color: #ffffff;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 3px 10px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #dc2626;
            }
            QPushButton:disabled {
                background-color: #4b5563;
                color: #9ca3af;
            }
            """
        )
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 6, 12, 6)
        self.lbl_banner = QLabel("⚠️ AV1 video stream: playing audio only (GPU lacks hardware AV1 decoder).", self.banner)
        self.btn_transcode = QPushButton("⚡ Convert to MP4 (H.264)", self.banner)
        self.btn_transcode.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_transcode.clicked.connect(self._on_transcode_clicked)
        banner_layout.addWidget(self.lbl_banner, stretch=1)
        banner_layout.addWidget(self.btn_transcode)

        self._current_path: Optional[Path] = None
        self._thread: Optional[QThread] = None
        self._worker: Optional[_TranscodeWorker] = None
        self._trim_thread: Optional[QThread] = None
        self._trim_worker: Optional[_TrimWorker] = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.banner)
        root_layout.addWidget(self.video_widget, stretch=1)
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
        self.clear_trim()

        # Check for AV1 streams in WebM files
        if path.suffix.lower() in (".webm", ".mkv"):
            codec = detect_video_codec(path)
            if codec == "av1":
                self.lbl_banner.setText("⚠️ AV1 video stream: playing audio only (GPU lacks hardware AV1 decoder).")
                self.btn_transcode.setEnabled(True)
                self.btn_transcode.setText("⚡ Convert to MP4 (H.264)")
                self.banner.setVisible(True)
            else:
                self.banner.setVisible(False)
        else:
            self.banner.setVisible(False)

        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _on_transcode_clicked(self) -> None:
        if self._current_path is None:
            return
        dest_path = self._current_path.parent / f"{self._current_path.stem}.mp4"
        counter = 2
        while dest_path.exists():
            dest_path = self._current_path.parent / f"{self._current_path.stem}_{counter}.mp4"
            counter += 1

        self.btn_transcode.setEnabled(False)
        self.btn_transcode.setText("⏳ Converting...")

        self._thread = QThread()
        self._worker = _TranscodeWorker(self._current_path, dest_path)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_transcode_success)
        self._worker.failed.connect(self._on_transcode_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

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

        dest_path = self._current_path.parent / f"{self._current_path.stem}_trim{self._current_path.suffix}"
        counter = 2
        while dest_path.exists():
            dest_path = self._current_path.parent / f"{self._current_path.stem}_trim_{counter}{self._current_path.suffix}"
            counter += 1

        self.btn_trim.setEnabled(False)
        self.btn_trim.setText("⏳ Trimming...")

        self._trim_thread = QThread()
        self._trim_worker = _TrimWorker(self._current_path, dest_path, start_ms, end_ms)
        self._trim_worker.moveToThread(self._trim_thread)
        self._trim_thread.started.connect(self._trim_worker.run)
        self._trim_worker.finished.connect(self._on_trim_success)
        self._trim_worker.failed.connect(self._on_trim_failed)
        self._trim_worker.finished.connect(self._trim_thread.quit)
        self._trim_worker.failed.connect(self._trim_thread.quit)
        self._trim_thread.start()

    def _on_trim_success(self, new_path: Path) -> None:
        self.clear_trim()
        self.trimCompleted.emit(new_path)

    def _on_trim_failed(self, error: str) -> None:
        self._update_trim_ui()
        QMessageBox.warning(self, "Trim Error", error)

    def stop(self) -> None:
        self.banner.setVisible(False)
        self.clear_trim()
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

    def set_color_scaling(self, brightness: int, contrast: int) -> None:
        """Apply color scaling overlay to live video playback."""
        if hasattr(self.video_widget, "color_overlay"):
            self.video_widget.color_overlay.set_color(brightness, contrast)

    def grab_current_frame(self) -> Optional[QImage]:
        """Extract the current video frame as a high-resolution QImage."""
        try:
            sink = self.video_widget.videoSink()
            if sink:
                frame = sink.videoFrame()
                if frame.isValid():
                    img = frame.toImage()
                    if not img.isNull():
                        return img
        except Exception:
            pass

        cur = getattr(self, "_current_path", None)
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
        if status in (QMediaPlayer.MediaStatus.EndOfMedia, QMediaPlayer.MediaStatus.InvalidMedia):
            if self._in_slideshow:
                self.videoFinished.emit()
            elif self._loop_when_not_slideshow:
                self.player.setPosition(0)
                self.player.play()

    def _on_player_error(self, error: QMediaPlayer.Error, error_string: str) -> None:
        if self._in_slideshow:
            self.videoFinished.emit()
