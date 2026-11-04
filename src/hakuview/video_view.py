"""Lightweight Qt6 hardware-accelerated video player widget for HakuView."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional

from PySide6.QtCore import QObject, QThread, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
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


class VideoPlayerWidget(QWidget):
    """Embedded video player with transport controls, AV1 compatibility banner, and transcode tool."""

    videoFinished = Signal()
    doubleClicked = Signal()
    transcodeCompleted = Signal(Path)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._loop_when_not_slideshow: bool = True
        self._in_slideshow: bool = False
        self._slider_dragging: bool = False

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.8)
        self.player.setAudioOutput(self.audio)

        self.video_widget = QVideoWidget(self)
        self.video_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
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
            QComboBox {
                background-color: #262b36;
                border: 1px solid #374151;
                border-radius: 4px;
                padding: 2px 6px;
                color: #f1f5f9;
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

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(self._on_slider_pressed)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        self.seek_slider.sliderMoved.connect(self._on_slider_moved)
        bar_layout.addWidget(self.seek_slider, stretch=1)

        self.mute_btn = QPushButton("🔊")
        self.mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_btn.setFixedWidth(38)
        self.mute_btn.clicked.connect(self.toggle_mute)
        bar_layout.addWidget(self.mute_btn)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
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

    def load_video(self, path: Path, slideshow_active: bool = False) -> None:
        self._current_path = path
        self._in_slideshow = slideshow_active

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

    def stop(self) -> None:
        self.banner.setVisible(False)
        self.player.stop()
        self.player.setSource(QUrl())

    def set_slideshow_mode(self, active: bool) -> None:
        self._in_slideshow = active

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

    def _on_speed_changed(self, index: int) -> None:
        rate = self.speed_combo.itemData(index)
        if rate:
            self.player.setPlaybackRate(float(rate))

    def _on_slider_pressed(self) -> None:
        self._slider_dragging = True

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
                self.videoFinished.emit()
            elif self._loop_when_not_slideshow:
                self.player.setPosition(0)
                self.player.play()
