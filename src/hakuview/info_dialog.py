"""Comprehensive Geeky Media Inspector / Info Card dialog for HakuView."""

from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from PIL import ExifTags, Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from . import __app_name__
from .folder_scanner import is_video_file
from .formatting import human_size
from . import theme
from .installer import ensure_hakuview_ico


def _format_bitrate(bps: Optional[float | int | str]) -> str:
    if not bps:
        return "N/A"
    try:
        val = float(bps)
    except (ValueError, TypeError):
        return str(bps)
    if val >= 1_000_000_000:
        return f"{val / 1_000_000_000:.2f} Gbps"
    elif val >= 1_000_000:
        return f"{val / 1_000_000:.2f} Mbps"
    elif val >= 1_000:
        return f"{val / 1_000:.1f} kbps"
    return f"{int(val)} bps"


def _aspect_ratio_str(w: int, h: int) -> str:
    if not w or not h:
        return "N/A"
    g = math.gcd(int(w), int(h))
    sw, sh = int(w) // g, int(h) // g
    decimal = w / h
    # If the reduced aspect ratio numbers are very large (odd crop), show approximate
    if sw > 32 or sh > 32:
        return f"{decimal:.2f}:1"
    return f"{sw}:{sh} ({decimal:.2f}:1)"


def probe_video_ffprobe(path: Path) -> Optional[Dict[str, Any]]:
    """Probe video streams and container metadata via ffprobe in JSON format."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        cmd = [
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=3.0)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout)
    except Exception:
        pass
    return None


def extract_media_info(path: Path) -> Dict[str, Any]:
    """Extract comprehensive technical properties for an image or video."""
    is_vid = is_video_file(path)
    file_stat = path.stat()
    file_size = file_stat.st_size

    info: Dict[str, Any] = {
        "file_name": path.name,
        "file_path": str(path),
        "file_size": file_size,
        "file_size_human": human_size(file_size),
        "is_video": is_vid,
        "modified_time": datetime.fromtimestamp(file_stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "raw_json": None,
    }

    if is_vid:
        probe = probe_video_ffprobe(path)
        info["raw_json"] = probe
        if probe:
            fmt = probe.get("format", {})
            info["format_name"] = fmt.get("format_long_name", fmt.get("format_name", path.suffix.upper()[1:]))
            duration_s = float(fmt.get("duration", 0))
            info["duration_sec"] = duration_s
            mins, secs = divmod(int(duration_s), 60)
            hours, mins = divmod(mins, 60)
            if hours > 0:
                info["duration_str"] = f"{hours:d}:{mins:02d}:{secs:02d}"
            else:
                info["duration_str"] = f"{mins:02d}:{secs:02d}"

            overall_bitrate = fmt.get("bit_rate")
            info["overall_bitrate"] = overall_bitrate
            info["overall_bitrate_str"] = _format_bitrate(overall_bitrate)

            # Find video and audio streams
            streams = probe.get("streams", [])
            vid_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
            aud_streams = [s for s in streams if s.get("codec_type") == "audio"]

            if vid_stream:
                w = int(vid_stream.get("width", 0))
                h = int(vid_stream.get("height", 0))
                info["width"] = w
                info["height"] = h
                info["megapixels"] = round((w * h) / 1_000_000, 2)
                info["aspect_ratio"] = _aspect_ratio_str(w, h)
                info["video_codec"] = vid_stream.get("codec_name", "Unknown").upper()
                info["video_codec_long"] = vid_stream.get("codec_long_name", info["video_codec"])
                info["profile"] = vid_stream.get("profile", "N/A")
                info["pix_fmt"] = vid_stream.get("pix_fmt", "N/A")

                # Frame rate calculation
                fps_str = vid_stream.get("r_frame_rate", "0/0")
                try:
                    num, den = map(int, fps_str.split("/"))
                    fps = num / den if den != 0 else 0.0
                except Exception:
                    fps = 0.0
                info["fps"] = round(fps, 2)
                info["frames"] = vid_stream.get("nb_frames")

                vid_br = vid_stream.get("bit_rate") or overall_bitrate
                info["video_bitrate"] = vid_br
                info["video_bitrate_str"] = _format_bitrate(vid_br)

                # Compression metrics
                if w > 0 and h > 0 and fps > 0 and vid_br:
                    try:
                        br_num = float(vid_br)
                        bpp = br_num / (w * h * fps)
                        info["bits_per_pixel"] = f"{bpp:.3f} bpp"
                        # Uncompressed video stream size (w * h * 3 bytes * fps * duration)
                        raw_bytes = w * h * 3 * fps * duration_s
                        if raw_bytes > 0:
                            ratio = raw_bytes / file_size
                            savings = max(0.0, (1.0 - (file_size / raw_bytes)) * 100)
                            info["compression_ratio"] = f"{ratio:.1f}:1"
                            info["space_saved"] = f"{savings:.1f}%"
                    except Exception:
                        pass

            info["audio_streams"] = []
            for aud in aud_streams:
                aud_info = {
                    "codec": aud.get("codec_name", "Unknown").upper(),
                    "codec_long": aud.get("codec_long_name", ""),
                    "channels": aud.get("channels", "N/A"),
                    "channel_layout": aud.get("channel_layout", "N/A"),
                    "sample_rate": f"{aud.get('sample_rate', 'N/A')} Hz",
                    "bitrate_str": _format_bitrate(aud.get("bit_rate")),
                }
                info["audio_streams"].append(aud_info)
        else:
            info["format_name"] = path.suffix.upper()[1:]
            info["video_codec"] = "Video Stream"

    else:
        # Image file metadata via Pillow
        try:
            with Image.open(path) as im:
                w, h = im.size
                info["width"] = w
                info["height"] = h
                info["megapixels"] = round((w * h) / 1_000_000, 2)
                info["aspect_ratio"] = _aspect_ratio_str(w, h)
                info["format_name"] = im.format or path.suffix.upper()[1:]
                info["color_mode"] = im.mode

                # Bit depth & channel info
                bands = len(im.getbands())
                info["channels"] = bands
                bits_per_channel = 8
                if im.mode in ("I", "F"):
                    bits_per_channel = 32
                elif "16" in getattr(im, "mode", ""):
                    bits_per_channel = 16
                info["bit_depth"] = f"{bits_per_channel}-bit ({bands * bits_per_channel}-bit total)"

                # Compression metrics
                raw_bytes = w * h * max(1, bands)
                info["raw_memory_bytes"] = raw_bytes
                info["raw_memory_str"] = human_size(raw_bytes)
                if file_size > 0:
                    ratio = raw_bytes / file_size
                    savings = max(0.0, (1.0 - (file_size / raw_bytes)) * 100)
                    info["compression_ratio"] = f"{ratio:.1f}:1"
                    info["space_saved"] = f"{savings:.1f}%"
                    bpp = (file_size * 8.0) / (w * h)
                    info["bits_per_pixel"] = f"{bpp:.2f} bpp"

                # EXIF Metadata extraction
                exif_data = {}
                raw_exif = im.getexif()
                if raw_exif:
                    # Camera settings (exposure, ISO, lens, DateTimeOriginal...) live in the
                    # Exif sub-IFD, not the top-level IFD0 that getexif() iterates.
                    exif_items = {**raw_exif, **raw_exif.get_ifd(ExifTags.IFD.Exif)}
                    for tag_id, value in exif_items.items():
                        tag_name = ExifTags.TAGS.get(tag_id, str(tag_id))
                        if isinstance(value, bytes):
                            try:
                                value = value.decode("utf-8", errors="ignore").strip()
                            except Exception:
                                value = f"<{len(value)} bytes binary>"
                        # Clean common EXIF values
                        if tag_name in (
                            "Make",
                            "Model",
                            "LensModel",
                            "Software",
                            "DateTime",
                            "DateTimeOriginal",
                            "ExposureTime",
                            "FNumber",
                            "ISOSpeedRatings",
                            "FocalLength",
                            "FocalLengthIn35mmFilm",
                            "Flash",
                            "WhiteBalance",
                            "Orientation",
                            "ColorSpace",
                        ):
                            exif_data[tag_name] = str(value)
                info["exif"] = exif_data
        except Exception as exc:
            info["error"] = str(exc)
            info["format_name"] = path.suffix.upper()[1:]

    return info


class MediaInfoDialog(QDialog):
    """Sleek dark geeky info card displaying compression, bitrates, codecs, and EXIF."""

    def __init__(self, path: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(f"Media Inspector — {path.name}")
        self.setWindowIcon(QIcon(str(ensure_hakuview_ico())))
        self.resize(560, 430)
        self.setMinimumSize(460, 340)

        self._info = extract_media_info(path)
        self._build_ui()
        # The card's styles are written in dark colors; in the light theme convert them all.
        for widget in [self, *self.findChildren(QWidget)]:
            if widget.styleSheet():
                widget.setStyleSheet(theme.adapt(widget.styleSheet()))

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QDialog {
                background-color: #12151b;
                color: #e2e8f0;
            }
            QTabWidget::pane {
                border: 1px solid #28303e;
                background-color: #171b23;
                border-radius: 5px;
                top: -1px;
            }
            QTabBar::tab {
                background-color: #1b202a;
                color: #94a3b8;
                padding: 4px 12px;
                margin-right: 3px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                border: 1px solid #28303e;
                border-bottom: none;
                font-size: 11px;
                font-weight: 500;
            }
            QTabBar::tab:selected {
                background-color: #171b23;
                color: #38bdf8;
                border-color: #28303e;
                border-bottom: 1px solid #171b23;
            }
            QTabBar::tab:hover:!selected {
                background-color: #242b38;
                color: #f1f5f9;
            }
            QPushButton {
                background-color: #232936;
                color: #f1f5f9;
                border: 1px solid #374151;
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #334155;
                border-color: #4b5563;
            }
            QPushButton#btn_copy {
                background-color: #0284c7;
                color: #ffffff;
                border-color: #38bdf8;
            }
            QPushButton#btn_copy:hover {
                background-color: #0369a1;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QTextEdit {
                background-color: #0f1217;
                color: #cbd5e1;
                border: 1px solid #262c38;
                border-radius: 4px;
                font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace;
                font-size: 10px;
                padding: 4px;
            }
            """
        )

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 8, 10, 8)
        root_layout.setSpacing(6)

        # -------------------------------------------------------------
        # 1. Header Card (File Name, Type Badge, Resolution, Size)
        # -------------------------------------------------------------
        header_frame = QFrame(self)
        header_frame.setStyleSheet(
            """
            QFrame {
                background-color: #181d26;
                border: 1px solid #2a3342;
                border-radius: 6px;
                padding: 4px 8px;
            }
            """
        )
        h_layout = QHBoxLayout(header_frame)
        h_layout.setContentsMargins(4, 2, 4, 2)
        h_layout.setSpacing(10)

        icon_label = QLabel("🎬" if self._info["is_video"] else "🖼️", header_frame)
        icon_label.setStyleSheet("font-size: 22px;")
        h_layout.addWidget(icon_label)

        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        lbl_fname = QLabel(self._info["file_name"], header_frame)
        lbl_fname.setStyleSheet("color: #f8fafc; font-size: 13px; font-weight: bold;")
        title_box.addWidget(lbl_fname)

        dims_text = (
            f"{self._info.get('width', '?')} × {self._info.get('height', '?')} "
            f"({self._info.get('megapixels', '?')} MP) • {self._info.get('file_size_human', '')}"
        )
        lbl_sub = QLabel(dims_text, header_frame)
        lbl_sub.setStyleSheet("color: #94a3b8; font-size: 11px;")
        title_box.addWidget(lbl_sub)
        h_layout.addLayout(title_box, stretch=1)

        root_layout.addWidget(header_frame)

        # -------------------------------------------------------------
        # 2. Key Geek Metrics Row (Compression, Bitrate/BPP, Codec, Aspect)
        # -------------------------------------------------------------
        metrics_row = QHBoxLayout()
        metrics_row.setSpacing(6)

        comp_val = self._info.get("compression_ratio", "N/A")
        if self._info.get("space_saved"):
            comp_val += f" ({self._info['space_saved']})"
        metrics_row.addWidget(self._make_stat_badge("🗜 Compression", comp_val, "#38bdf8"))

        if self._info["is_video"]:
            br_val = self._info.get("video_bitrate_str", self._info.get("overall_bitrate_str", "N/A"))
            metrics_row.addWidget(self._make_stat_badge("⚡ Bitrate", br_val, "#4ade80"))
            codec_val = self._info.get("video_codec", "Unknown")
            metrics_row.addWidget(self._make_stat_badge("🎞 Codec", codec_val, "#f472b6"))
        else:
            bpp_val = self._info.get("bits_per_pixel", "N/A")
            metrics_row.addWidget(self._make_stat_badge("📊 Efficiency", bpp_val, "#4ade80"))
            fmt_val = self._info.get("format_name", "Image")
            metrics_row.addWidget(self._make_stat_badge("🎨 Format", fmt_val, "#f472b6"))

        aspect_val = self._info.get("aspect_ratio", "N/A")
        metrics_row.addWidget(self._make_stat_badge("📐 Aspect Ratio", aspect_val, "#fbbf24"))

        root_layout.addLayout(metrics_row)

        # -------------------------------------------------------------
        # 3. Tabbed Detailed Property Tables
        # -------------------------------------------------------------
        tabs = QTabWidget(self)

        # Tab 1: General & Compression
        tab_general = QWidget()
        tg_layout = QVBoxLayout(tab_general)
        tg_layout.setContentsMargins(6, 6, 6, 6)
        gen_data = [
            ("File Name", self._info["file_name"]),
            ("File Path", self._info["file_path"]),
            ("File Size", f"{self._info['file_size_human']} ({self._info['file_size']:,} bytes)"),
            ("Format / Container", self._info.get("format_name", "N/A")),
            ("Date Modified", self._info["modified_time"]),
            ("Dimensions", f"{self._info.get('width', '?')} × {self._info.get('height', '?')} px"),
            ("Megapixels", f"{self._info.get('megapixels', 'N/A')} MP"),
            ("Aspect Ratio", self._info.get("aspect_ratio", "N/A")),
            ("Compression Ratio", self._info.get("compression_ratio", "N/A")),
            ("Bits Per Pixel", self._info.get("bits_per_pixel", "N/A")),
        ]
        if not self._info["is_video"] and "raw_memory_str" in self._info:
            gen_data.append(("Raw Bitmap in RAM", self._info["raw_memory_str"]))
        if self._info["is_video"] and "duration_str" in self._info:
            gen_data.append(("Duration", f"{self._info['duration_str']} ({self._info.get('duration_sec', 0):.2f}s)"))
            gen_data.append(("Overall Bitrate", self._info.get("overall_bitrate_str", "N/A")))

        tg_layout.addWidget(self._make_property_table(gen_data))
        tabs.addTab(tab_general, "General && Metrics")

        # Tab 2: Streams / Visual Details
        tab_visual = QWidget()
        tv_layout = QVBoxLayout(tab_visual)
        tv_layout.setContentsMargins(6, 6, 6, 6)

        if self._info["is_video"]:
            vid_data = [
                ("Video Codec", self._info.get("video_codec_long", self._info.get("video_codec", "N/A"))),
                ("Profile & Level", self._info.get("profile", "N/A")),
                ("Resolution", f"{self._info.get('width', '?')} × {self._info.get('height', '?')}"),
                ("Frame Rate (FPS)", f"{self._info.get('fps', 'N/A')} fps"),
                ("Total Frames", str(self._info.get("frames", "N/A"))),
                ("Video Bitrate", self._info.get("video_bitrate_str", "N/A")),
                ("Pixel Format", self._info.get("pix_fmt", "N/A")),
            ]
            tv_layout.addWidget(self._make_property_table(vid_data))
            tabs.addTab(tab_visual, "Video Stream")

            # Tab 3: Audio Streams (if present)
            if self._info.get("audio_streams"):
                tab_audio = QWidget()
                ta_layout = QVBoxLayout(tab_audio)
                ta_layout.setContentsMargins(6, 6, 6, 6)
                for i, aud in enumerate(self._info["audio_streams"]):
                    aud_data = [
                        ("Track", f"Audio #{i + 1}"),
                        ("Codec", aud.get("codec_long") or aud.get("codec", "N/A")),
                        ("Channels", f"{aud.get('channels', 'N/A')} ({aud.get('channel_layout', '')})"),
                        ("Sample Rate", aud.get("sample_rate", "N/A")),
                        ("Bitrate", aud.get("bitrate_str", "N/A")),
                    ]
                    ta_layout.addWidget(self._make_property_table(aud_data))
                tabs.addTab(tab_audio, f"Audio ({len(self._info['audio_streams'])})")
        else:
            img_data = [
                ("Color Mode", self._info.get("color_mode", "N/A")),
                ("Channels", str(self._info.get("channels", "N/A"))),
                ("Bit Depth", self._info.get("bit_depth", "N/A")),
                ("Raw In-Memory Size", self._info.get("raw_memory_str", "N/A")),
                ("Compression Ratio", self._info.get("compression_ratio", "N/A")),
                ("Efficiency", self._info.get("bits_per_pixel", "N/A")),
            ]
            tv_layout.addWidget(self._make_property_table(img_data))
            tabs.addTab(tab_visual, "Image Encoding")

            # EXIF Tab (if present)
            if self._info.get("exif"):
                tab_exif = QWidget()
                te_layout = QVBoxLayout(tab_exif)
                te_layout.setContentsMargins(6, 6, 6, 6)
                exif_list = sorted(self._info["exif"].items())
                te_layout.addWidget(self._make_property_table(exif_list))
                tabs.addTab(tab_exif, f"EXIF Data ({len(exif_list)})")

        # Tab: Raw JSON / Diagnostic Dump
        if self._info.get("raw_json"):
            tab_raw = QWidget()
            tr_layout = QVBoxLayout(tab_raw)
            tr_layout.setContentsMargins(6, 6, 6, 6)
            txt_raw = QTextEdit(tab_raw)
            txt_raw.setReadOnly(True)
            txt_raw.setPlainText(json.dumps(self._info["raw_json"], indent=2))
            tr_layout.addWidget(txt_raw)
            tabs.addTab(tab_raw, "Raw FFprobe Dump")

        root_layout.addWidget(tabs, stretch=1)

        # -------------------------------------------------------------
        # 4. Action Buttons (Copy Markdown, Close)
        # -------------------------------------------------------------
        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)

        self.btn_copy = QPushButton("📋 Copy Info Card", self)
        self.btn_copy.setObjectName("btn_copy")
        self.btn_copy.clicked.connect(self._copy_to_clipboard)
        btn_box.addWidget(self.btn_copy)

        btn_box.addStretch(1)

        self.btn_close = QPushButton("Close (Esc)", self)
        self.btn_close.clicked.connect(self.accept)
        btn_box.addWidget(self.btn_close)

        root_layout.addLayout(btn_box)

    def _make_stat_badge(self, title: str, value: str, accent_hex: str) -> QFrame:
        frame = QFrame(self)
        frame.setStyleSheet(
            f"""
            QFrame {{
                background-color: #171b23;
                border: 1px solid #262c38;
                border-radius: 5px;
                padding: 3px 6px;
            }}
            """
        )
        vbox = QVBoxLayout(frame)
        vbox.setContentsMargins(2, 2, 2, 2)
        vbox.setSpacing(1)

        lbl_t = QLabel(title, frame)
        lbl_t.setStyleSheet("color: #94a3b8; font-size: 9px; font-weight: bold; text-transform: uppercase;")
        vbox.addWidget(lbl_t)

        lbl_v = QLabel(value, frame)
        lbl_v.setStyleSheet(f"color: {accent_hex}; font-size: 12px; font-weight: bold;")
        vbox.addWidget(lbl_v)
        return frame

    def _make_property_table(self, rows: List[tuple[str, str]]) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        for i, (k, v) in enumerate(rows):
            row_frame = QFrame(container)
            bg = "#1b202a" if i % 2 == 0 else "#161a22"
            row_frame.setStyleSheet(
                f"QFrame {{ background-color: {bg}; border-radius: 3px; padding: 1px 4px; }}"
            )
            r_layout = QHBoxLayout(row_frame)
            r_layout.setContentsMargins(6, 2, 6, 2)

            lbl_k = QLabel(k, row_frame)
            lbl_k.setFixedWidth(135)
            lbl_k.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 500;")
            r_layout.addWidget(lbl_k)

            lbl_v = QLabel(str(v), row_frame)
            lbl_v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lbl_v.setStyleSheet("color: #f1f5f9; font-size: 11px;")
            r_layout.addWidget(lbl_v, stretch=1)

            layout.addWidget(row_frame)

        layout.addStretch(1)
        scroll.setWidget(container)
        return scroll

    def _copy_to_clipboard(self) -> None:
        """Format media properties into a clean Markdown report and copy to system clipboard."""
        lines = [
            f"# {__app_name__} Media Information",
            f"- **File**: `{self._info['file_name']}`",
            f"- **Path**: `{self._info['file_path']}`",
            f"- **Size**: {self._info['file_size_human']} ({self._info['file_size']:,} bytes)",
            f"- **Type**: {'Video' if self._info['is_video'] else 'Image'} ({self._info.get('format_name', 'N/A')})",
            f"- **Resolution**: {self._info.get('width', '?')} × {self._info.get('height', '?')} ({self._info.get('megapixels', '?')} MP)",
            f"- **Aspect Ratio**: {self._info.get('aspect_ratio', 'N/A')}",
            f"- **Compression Ratio**: {self._info.get('compression_ratio', 'N/A')}",
            f"- **Efficiency**: {self._info.get('bits_per_pixel', 'N/A')}",
        ]
        if self._info["is_video"]:
            lines.extend(
                [
                    f"- **Video Codec**: {self._info.get('video_codec_long', self._info.get('video_codec', 'N/A'))}",
                    f"- **Profile**: {self._info.get('profile', 'N/A')}",
                    f"- **Pixel Format**: {self._info.get('pix_fmt', 'N/A')}",
                    f"- **Frame Rate**: {self._info.get('fps', 'N/A')} fps",
                    f"- **Total Frames**: {self._info.get('frames', 'N/A')}",
                    f"- **Video Bitrate**: {self._info.get('video_bitrate_str', 'N/A')}",
                    f"- **Overall Bitrate**: {self._info.get('overall_bitrate_str', 'N/A')}",
                    f"- **Duration**: {self._info.get('duration_str', 'N/A')} ({self._info.get('duration_sec', 0):.2f}s)",
                ]
            )
            for i, aud in enumerate(self._info.get("audio_streams", [])):
                lines.append(
                    f"- **Audio #{i + 1}**: {aud.get('codec', 'N/A')} • {aud.get('channels', 'N/A')} ch ({aud.get('channel_layout', '')}) • {aud.get('sample_rate', 'N/A')} • {aud.get('bitrate_str', 'N/A')}"
                )
        else:
            lines.extend(
                [
                    f"- **Color Mode**: {self._info.get('color_mode', 'N/A')} ({self._info.get('channels', 'N/A')} channels)",
                    f"- **Bit Depth**: {self._info.get('bit_depth', 'N/A')}",
                    f"- **Raw In-Memory Size**: {self._info.get('raw_memory_str', 'N/A')}",
                ]
            )
            if self._info.get("exif"):
                lines.append("\n## EXIF Metadata")
                for k, v in sorted(self._info["exif"].items()):
                    lines.append(f"- **{k}**: {v}")

        report = "\n".join(lines)
        QApplication.clipboard().setText(report)
        self.btn_copy.setText("✓ Copied to Clipboard!")
        # Context-object form: the timer is dropped if the dialog closes within 2 s.
        QTimer.singleShot(2000, self.btn_copy, lambda: self.btn_copy.setText("📋 Copy Info Card"))
