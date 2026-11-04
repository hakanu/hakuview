"""Main HakuView window implementing IrfanView-style navigation, editing, and slideshow."""

from __future__ import annotations

from datetime import datetime
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QProcess, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeyEvent,
    QKeySequence,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __app_name__, __version__, dialogs, display_version
from .canvas import ImageCanvas
from .color_bar import ColorAdjustBar
from .folder_scanner import SUPPORTED_EXTENSIONS, FolderNavigator, is_video_file
from .formatting import human_size
from .image_ops import (
    COMPRESSION_MODES,
    CompressionError,
    ImageDocument,
    ImagePreloadCache,
    adjust_qimage_color,
    compress_image_as_variant,
    is_animated_image,
    _next_variant_path,
    save_qimage_atomic,
)
from .info_dialog import MediaInfoDialog
from .installer import ensure_hakuview_ico
from .theme import APP_QSS
from .updater import RELEASES_URL, UpdateChecker, UpdateRunner, UpdateSettings, can_self_update, is_newer
from .video_view import VideoPlayerWidget, _format_ms
from .widgets import FullscreenControls, chevron_icon, menu_position_under, three_dots_icon


class HakuViewWindow(QMainWindow):
    """Lightweight, keyboard-driven desktop viewer for images and videos."""

    def __init__(
        self,
        initial_path: Optional[Path | str] = None,
        pick_random: bool = False,
        start_slideshow: bool = False,
        slideshow_interval: float = 3.0,
        start_fullscreen: bool = False,
        check_updates: bool = False,
    ) -> None:
        super().__init__()

        self.navigator = FolderNavigator()
        self.cache = ImagePreloadCache(max_items=10)
        self.document: Optional[ImageDocument] = None
        self.current_is_video: bool = False
        self._info_visible: bool = False
        self._prompt_unsaved: bool = True

        # Slideshow state
        self.slideshow_active: bool = False
        self.slideshow_random: bool = False
        self.slideshow_interval_sec: float = max(0.5, float(slideshow_interval))
        self.slideshow_timer = QTimer(self)
        self.slideshow_timer.timeout.connect(self._on_slideshow_tick)

        self.setStyleSheet(APP_QSS)
        self._build_ui()
        self._build_shortcuts()

        self.setWindowTitle(__app_name__)
        self.setWindowIcon(QIcon(str(ensure_hakuview_ico())))
        self.resize(1180, 780)
        self.setAcceptDrops(True)

        if initial_path is not None:
            self.open_path(Path(initial_path), pick_random=pick_random)

        if start_fullscreen:
            self.toggle_fullscreen()

        if start_slideshow and self.navigator.count > 0:
            self.set_slideshow(True)

        self.update_settings = UpdateSettings()
        self.update_checker = UpdateChecker(self)
        self.update_checker.finished.connect(self._on_update_check_finished)
        self._update_check_manual = False
        self._update_runner: Optional[UpdateRunner] = None
        if check_updates and self.update_settings.startup_check_due():
            # Let the first image paint before touching the network.
            QTimer.singleShot(2500, self, lambda: self.check_for_updates(manual=False))

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Central stacked widget switching between ImageCanvas and VideoPlayerWidget
        self.stack = QStackedWidget(self)
        self.canvas = ImageCanvas(self)
        self.video_view = VideoPlayerWidget(self)
        self.stack.addWidget(self.canvas)
        self.stack.addWidget(self.video_view)

        self.color_bar = ColorAdjustBar(self)
        self.color_bar.changed.connect(self._on_color_changed)
        self.color_bar.saveRequested.connect(self.save_adjusted_image)
        self.color_bar.saveVideoRequested.connect(self.export_adjusted_video)
        self.color_bar.closeRequested.connect(self.toggle_color_bar)

        central_container = QWidget(self)
        c_layout = QVBoxLayout(central_container)
        c_layout.setContentsMargins(0, 0, 0, 0)
        c_layout.setSpacing(0)
        c_layout.addWidget(self.color_bar)
        c_layout.addWidget(self.stack, stretch=1)
        self.setCentralWidget(central_container)

        self.canvas.zoomChanged.connect(self._on_zoom_changed)
        self.canvas.selectionChanged.connect(self._on_selection_changed)
        self.canvas.doubleClicked.connect(self.toggle_fullscreen)
        self.video_view.doubleClicked.connect(self.toggle_fullscreen)
        self.video_view.videoFinished.connect(self._on_video_finished_in_slideshow)
        self.video_view.transcodeCompleted.connect(self._on_video_transcoded)
        self.video_view.trimCompleted.connect(self._on_video_trimmed)
        self.video_view.colorExportCompleted.connect(self._on_color_export_done)
        self.video_view.colorExportFailed.connect(self._on_color_export_failed)
        self.video_view.infoRequested.connect(self.show_media_info_dialog)

        # Compact Top Toolbar
        # Top bar = main toolbar (stretches; extra buttons go to its » overflow popup when the
        # window is narrow) + the ⋯ button in a fixed slot on the right, so it is always reachable.
        self.top_bar = QWidget(self)
        self.top_bar.setObjectName("topBar")
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(0, 0, 6, 0)
        top_layout.setSpacing(0)
        self.toolbar = QToolBar("Main Toolbar", self.top_bar)
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        top_layout.addWidget(self.toolbar, stretch=1)
        self.setMenuWidget(self.top_bar)

        self.btn_open = self._add_tb_button("📂 Open", self.open_file_dialog, "Open an image or video file", "O")
        self.btn_prev = self._add_tb_button("◀ Prev", self.prev_item, "Previous file in folder", "←")
        self.btn_next = self._add_tb_button("Next ▶", self.next_item, "Next file in folder", "→")

        self.toolbar.addSeparator()

        self.btn_rot_left = self._add_tb_button("↺", self.rotate_left, "Rotate 90° counter-clockwise", "L")
        self.btn_rot_right = self._add_tb_button("↻", self.rotate_right, "Rotate 90° clockwise", "R")
        self.btn_flip_h = self._add_tb_button("⇆", self.flip_horizontal, "Flip horizontally", "H")
        self.btn_crop = self._add_tb_button(
            "✂ Crop",
            self.trigger_crop_action,
            "Show border crop handles (or drag a box), then press C or Enter to apply the crop",
            "C",
        )
        self.btn_crop.setEnabled(False)
        self.btn_color = self._add_tb_button(
            "🎨 Color",
            self.toggle_color_bar,
            "Adjust brightness & contrast for images and videos",
            "B",
            checkable=True,
        )
        self.btn_undo = self._add_tb_button("↶ Undo", self.undo_edit, "Undo last image edit", "Ctrl+Z")
        self.btn_undo.setEnabled(False)
        self.btn_save = self._add_tb_button("💾 Save", self.save_current, "Save changes (Ctrl+Shift+S for Save As)", "Ctrl+S")
        self.btn_save.setEnabled(False)

        self.btn_compress = self._add_tb_button(
            "🗜 Compress",
            self.compress_current_variant,
            "Compress & save as a variant file in the same folder",
            "K",
        )
        self.btn_compress.setEnabled(False)

        self.combo_compress = QComboBox(self)
        self.combo_compress.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.combo_compress.setToolTip("Select compression method for 1-click variant save")
        for label, mode_id in COMPRESSION_MODES:
            self.combo_compress.addItem(label, mode_id)
        self.toolbar.addWidget(self.combo_compress)

        self.toolbar.addSeparator()

        self.btn_slideshow = self._add_tb_button(
            "▶ Slideshow",
            self.toggle_slideshow,
            "Start/stop automatic slideshow",
            "W / F5",
            checkable=True,
        )

        self.toolbar.addWidget(QLabel(" Interval:"))

        self.spin_interval = QDoubleSpinBox(self)
        self.spin_interval.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.spin_interval.setRange(0.5, 120.0)
        self.spin_interval.setSingleStep(0.5)
        self.spin_interval.setDecimals(1)
        self.spin_interval.setSuffix(" s")
        self.spin_interval.setValue(self.slideshow_interval_sec)
        self.spin_interval.valueChanged.connect(self._on_interval_changed)
        self.toolbar.addWidget(self.spin_interval)

        self.chk_shuffle = QCheckBox("Shuffle", self)
        self.chk_shuffle.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.chk_shuffle.toggled.connect(self._on_shuffle_toggled)
        self.toolbar.addWidget(self.chk_shuffle)

        self.toolbar.addSeparator()

        self.btn_fit = self._add_tb_button("⊞ Fit", self.canvas.zoom_fit, "Fit image to window", "0")
        self.btn_actual = self._add_tb_button("1:1", self.canvas.zoom_actual, "Actual pixel size (100%)", "1")
        self.btn_fullscreen = self._add_tb_button("⛶", self.toggle_fullscreen, "Toggle fullscreen", "F / F11")
        self.btn_info = self._add_tb_button(
            "ℹ Info",
            self.show_media_info_dialog,
            "Media Inspector: compression ratio, bitrate, codec, EXIF & tech specs",
            "I / Ctrl+I",
        )
        self.btn_info.setEnabled(False)

        # Spacer & 3-dots More Options button
        self.btn_more = QPushButton(self.top_bar)
        self.btn_more.setObjectName("moreButton")
        self.btn_more.setIcon(three_dots_icon(size=22))
        self.btn_more.setIconSize(QSize(20, 20))
        self.btn_more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_more.setToolTip("More Options (3 dots menu)")
        self.btn_more.clicked.connect(lambda: self.show_more_options_menu(self.btn_more))
        top_layout.addWidget(self.btn_more)

        overflow = self.toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
        if overflow is not None:
            overflow.setIcon(chevron_icon())
            overflow.setToolTip("More toolbar buttons")

        # Floating top-right controls for fullscreen mode
        self.fs_overlay = FullscreenControls(self)
        self.fs_overlay.moreClicked.connect(lambda: self.show_more_options_menu(self.fs_overlay.btn_more))
        self.fs_overlay.restoreClicked.connect(self.toggle_fullscreen)
        self.fs_overlay.closeClicked.connect(self.close)

        # Status Bar
        self.status = QStatusBar(self)
        self.setStatusBar(self.status)

        self.lbl_position = QLabel("[0 / 0]")
        self.lbl_filename = QLabel("No file loaded")
        self.lbl_dimensions = QLabel("")
        self.lbl_zoom = QLabel("")
        self.lbl_modified = QLabel("")
        self.lbl_modified.setObjectName("modifiedLabel")

        self.status.addWidget(self.lbl_position)
        self.status.addWidget(self.lbl_filename, 1)
        self.status.addPermanentWidget(self.lbl_modified)
        self.status.addPermanentWidget(self.lbl_dimensions)
        self.status.addPermanentWidget(self.lbl_zoom)

    def toggle_color_bar(self) -> None:
        """Toggle visibility of the brightness/contrast bar."""
        visible = not self.color_bar.isVisible()
        self.color_bar.setVisible(visible)
        self.btn_color.setChecked(visible)
        if visible:
            self.color_bar.slider_brightness.setFocus()

    def _on_color_changed(self, brightness: int, contrast: int) -> None:
        if self.current_is_video:
            self.video_view.set_color_scaling(brightness, contrast)
        elif self.document is not None:
            self.canvas.set_image(adjust_qimage_color(self.document.current, brightness, contrast), reset_view=False)
        self._update_ui_state()

    def reset_color_adjustments(self, update_view: bool = True) -> None:
        """Reset brightness and contrast back to 0 (optionally without re-rendering)."""
        self.color_bar.reset()
        if update_view:
            self._on_color_changed(0, 0)

    def save_adjusted_image(self) -> Optional[Path]:
        """Save the color-adjusted image (or current video frame) as a new image in the current folder."""
        cur = self.navigator.current_file
        if cur is None:
            return None
        if self.current_is_video:
            frame = self.video_view.grab_current_frame()
            if frame is None or frame.isNull():
                QMessageBox.warning(self, __app_name__, "Could not capture the current video frame.")
                return None
            pos_sec = max(0, int(self.video_view.player.position() / 1000))
            source, target, what = frame, _next_variant_path(cur, f"frame_{pos_sec}s_color", ".png"), "video frame"
        elif self.document is not None:
            source, target, what = self.document.current, _next_variant_path(cur, "color", cur.suffix or ".png"), "image"
        else:
            return None

        adjusted = adjust_qimage_color(source, self.color_bar.brightness, self.color_bar.contrast)
        try:
            save_qimage_atomic(adjusted, target)
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Could not save adjusted {what}:\n{exc}")
            return None
        self.navigator.load_target(cur)
        self._update_ui_state()
        self.status.showMessage(f"💾 Saved {what} as new image: {target.name} (Press → to view)", 8000)
        return target

    def _add_tb_button(
        self,
        text: str,
        slot,
        tooltip: str,
        shortcut: Optional[str] = None,
        checkable: bool = False,
    ) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolTip(f"{tooltip}  ({shortcut})" if shortcut else tooltip)
        btn.setCheckable(checkable)
        btn.clicked.connect(lambda _checked=False: slot())
        self.toolbar.addWidget(btn)
        return btn

    def _build_shortcuts(self) -> None:
        # Every key is bound in exactly one place. Letter/symbol commands live here as
        # window-wide QActions; keys that a focused widget (slider, spin box) may need to
        # consume itself — arrows, Home/End, Page Up/Down, Space, Enter, Esc — are handled
        # in keyPressEvent, which Qt only reaches when the focused widget ignores the key.
        # Context-dependent keys (I, O, [, ], T, M) dispatch on image vs. video mode.
        self._shortcut_table = [
            (("O", "Ctrl+O"), self._on_key_o, "O / Ctrl+O", "Open file (O marks video Out-point)"),
            (("Ctrl+W", "Ctrl+Q"), self.close, "Ctrl+W / Ctrl+Q", "Close window"),
            (("Ctrl+S",), self.save_current, "Ctrl+S", "Save changes (atomic in-place)"),
            (("Ctrl+Shift+S",), self.save_as_dialog, "Ctrl+Shift+S", "Save As..."),
            (("Ctrl+Z",), self.undo_edit, "Ctrl+Z", "Undo last edit"),
            (("Ctrl+M",), self.random_item, "Ctrl+M", "Jump to random file in folder"),
            (("I", "Ctrl+I"), self._on_key_i, "I / Ctrl+I", "Media Inspector (I marks video In-point)"),
            (("Shift+I",), self.toggle_info_overlay, "Shift+I", "Toggle info HUD overlay"),
            (("R",), self.rotate_right, "R / L", "Rotate 90° Right / Left"),
            (("L",), self.rotate_left, None, None),
            (("H",), self.flip_horizontal, "H / V", "Flip Horizontal / Vertical"),
            (("V",), self.flip_vertical, None, None),
            (("C",), self.trigger_crop_action, "C", "Border Crop (drag borders, then C or Enter)"),
            (("Ctrl+Y",), self.apply_crop, "Ctrl+Y", "Apply crop selection"),
            (("B",), self.toggle_color_bar, "B", "Brightness / Contrast panel"),
            (("K", "Ctrl+K"), self.compress_current_variant, "K", "1-Click Compress & save as variant"),
            (("W", "F5"), self.toggle_slideshow, "W / F5", "Toggle Slideshow"),
            (("[",), self._on_key_bracket_left, "[ / ]", "Video: mark In / Out — Images: slideshow faster / slower"),
            (("]",), self._on_key_bracket_right, None, None),
            (("T",), self._on_key_t, "T", "Export trimmed video clip (FFmpeg)"),
            (("M",), self._on_key_m, "M", "Mute / unmute video"),
            (("F", "F11"), self.toggle_fullscreen, "F / F11 / Double-Click", "Toggle Fullscreen"),
            (("+", "="), self.canvas.zoom_in, "+ / - or Wheel", "Zoom (centered at mouse)"),
            (("-",), self.canvas.zoom_out, None, None),
            (("0",), self.canvas.zoom_fit, "0 / 1", "Fit to window / 100% 1:1 size"),
            (("1",), self.canvas.zoom_actual, None, None),
            (("Ctrl+A",), self.canvas.select_all, "Ctrl+A", "Select whole image for cropping"),
        ]
        for sequences, handler, _keys_label, _help in self._shortcut_table:
            act = QAction(self)
            act.setShortcuts([QKeySequence(seq) for seq in sequences])
            # Call without arguments: triggered(bool) would otherwise be passed into
            # handlers with default parameters (e.g. zoom_in(factor=False)).
            act.triggered.connect(lambda _checked=False, fn=handler: fn())
            self.addAction(act)

    _KEYPRESS_HELP = [
        ("← / →", "Previous / Next file in folder (Shift: seek video ±5 s)"),
        ("Home / End, PgUp / PgDn", "First / Last file, Previous / Next file"),
        ("Space or Click", "Play / Pause video (hold Space + drag to pan image)"),
        ("Enter", "Apply crop selection, otherwise toggle fullscreen"),
        ("Esc", "Close panel / clear trim or crop / stop slideshow / exit fullscreen"),
    ]

    def _on_key_o(self) -> None:
        if self.current_is_video:
            self._mark_trim_out()
        else:
            self.open_file_dialog()

    def _on_key_i(self) -> None:
        ctrl_held = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.current_is_video and not ctrl_held:
            self._mark_trim_in()
        else:
            self.show_media_info_dialog()

    def _on_key_bracket_left(self) -> None:
        if self.current_is_video:
            self._mark_trim_in()
        else:
            self.spin_interval.setValue(self.spin_interval.value() - 0.5)

    def _on_key_bracket_right(self) -> None:
        if self.current_is_video:
            self._mark_trim_out()
        else:
            self.spin_interval.setValue(self.spin_interval.value() + 0.5)

    def _on_key_t(self) -> None:
        if self.current_is_video:
            self.video_view.execute_trim()

    def _on_key_m(self) -> None:
        if self.current_is_video:
            self.video_view.toggle_mute()

    def _mark_trim_in(self) -> None:
        self.video_view.set_trim_in()
        self.status.showMessage(f"✂ Mark In: {_format_ms(self.video_view.trim_in_ms or 0)}", 3000)

    def _mark_trim_out(self) -> None:
        self.video_view.set_trim_out()
        self.status.showMessage(f"✂ Mark Out: {_format_ms(self.video_view.trim_out_ms or 0)}", 3000)

    # ------------------------------------------------------------------
    # Keyboard handling (keys a focused widget may consume first)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if key in (Qt.Key.Key_Right, Qt.Key.Key_Left):
            forward = key == Qt.Key.Key_Right
            if self.current_is_video and shift:
                self.video_view.seek_relative_ms(5000 if forward else -5000)
            elif forward:
                self.next_item()
            else:
                self.prev_item()
        elif key in (Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self.next_item() if key == Qt.Key.Key_PageDown else self.prev_item()
        elif key == Qt.Key.Key_Home:
            self.first_item()
        elif key == Qt.Key.Key_End:
            self.last_item()
        elif key == Qt.Key.Key_Space:
            if self.current_is_video:
                self.video_view.toggle_play_pause()
            elif not event.isAutoRepeat():
                self.canvas.set_space_pressed(True)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self.current_is_video and not self.canvas.selection.isNull():
                self.apply_crop()
            else:
                self.toggle_fullscreen()
        elif key == Qt.Key.Key_Escape:
            self._on_escape()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _on_escape(self) -> None:
        if self.color_bar.isVisible():
            self.toggle_color_bar()
        elif self.current_is_video and self.video_view.has_trim_points():
            self.video_view.clear_trim()
            self.status.showMessage("Trim markers cleared.", 2000)
        elif not self.current_is_video and not self.canvas.selection.isNull():
            self.canvas.clear_selection()
        elif self.slideshow_active:
            self.set_slideshow(False)
        elif self.isFullScreen():
            self.toggle_fullscreen()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.canvas.set_space_pressed(False)
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ------------------------------------------------------------------
    # Drag and Drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if not urls:
            return
        local_path = urls[0].toLocalFile()
        if local_path:
            self.open_path(Path(local_path))

    # ------------------------------------------------------------------
    # Opening & Cycling Media in Folder
    # ------------------------------------------------------------------

    def open_file_dialog(self) -> None:
        if not self._confirm_discard_or_save():
            return
        start_dir = str(self.navigator.folder) if self.navigator.folder else str(Path.home())
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Image or Video",
            start_dir,
            f"Media Files ({' '.join('*' + ext for ext in sorted(SUPPORTED_EXTENSIONS))});;All Files (*)",
        )
        if file_path:
            self.open_path(Path(file_path))

    def open_path(self, path: Path, pick_random: bool = False) -> bool:
        if not self._confirm_discard_or_save():
            return False
        selected = self.navigator.load_target(path, pick_random=pick_random)
        if selected is None:
            QMessageBox.warning(
                self,
                __app_name__,
                f"No supported images or videos found in:\n{path}",
            )
            return False
        return self._display_current_file()

    def _navigate(self, move) -> Optional[Path]:
        """Run a FolderNavigator move (after the unsaved-changes prompt) and display the result."""
        if self.navigator.count == 0:
            return None
        if not self._confirm_discard_or_save():
            return self.navigator.current_file
        target = move()
        if target is not None:
            self._display_current_file()
        return target

    def next_item(self) -> Optional[Path]:
        if self.slideshow_active and self.slideshow_random:
            return self._navigate(self.navigator.random_file)
        return self._navigate(self.navigator.next)

    def prev_item(self) -> Optional[Path]:
        return self._navigate(self.navigator.prev)

    def first_item(self) -> Optional[Path]:
        return self._navigate(self.navigator.first)

    def last_item(self) -> Optional[Path]:
        return self._navigate(self.navigator.last)

    def random_item(self) -> Optional[Path]:
        return self._navigate(self.navigator.random_file)

    def _display_current_file(self) -> bool:
        self.reset_color_adjustments(update_view=False)
        current = self.navigator.current_file
        if current is None:
            self.document = None
            self.video_view.stop()
            self.canvas.set_image(None)
            self.stack.setCurrentWidget(self.canvas)
            self._update_ui_state()
            return False

        if is_video_file(current):
            self.current_is_video = True
            self.document = None
            self.stack.setCurrentWidget(self.video_view)
            self.video_view.load_video(current, slideshow_active=self.slideshow_active)
            if self.slideshow_active:
                self._start_slideshow_timer()
            self._update_ui_state()
            self.cache.preload_neighbors(self.navigator.neighbors(radius=2))
            return True

        # Image file
        self.current_is_video = False
        self.video_view.stop()
        self.stack.setCurrentWidget(self.canvas)

        qimg = self.cache.get(current)
        if qimg is None or qimg.isNull():
            self.document = None
            self.canvas.set_image(None)
            self.lbl_filename.setText(f"Failed to load: {current.name}")
            self._update_ui_state()
            return False

        self.document = ImageDocument(current, qimg)
        self.canvas.set_image(qimg, reset_view=True, movie_path=current)
        if self._info_visible:
            self.canvas.set_info_overlay(True, self._build_info_text())

        if self.slideshow_active:
            self._start_slideshow_timer()

        self._update_ui_state()
        self.cache.preload_neighbors(self.navigator.neighbors(radius=2))
        return True

    # ------------------------------------------------------------------
    # Image Editing (Rotate, Flip, Crop, Undo, Save)
    # ------------------------------------------------------------------

    def _apply_edit(self, operation, reset_view: bool) -> None:
        if self.document is None or self.current_is_video:
            return
        operation(self.document)
        self.canvas.set_image(self.document.current, reset_view=reset_view)
        self._update_ui_state()

    def rotate_left(self) -> None:
        self._apply_edit(ImageDocument.rotate_left, reset_view=True)

    def rotate_right(self) -> None:
        self._apply_edit(ImageDocument.rotate_right, reset_view=True)

    def flip_horizontal(self) -> None:
        self._apply_edit(ImageDocument.flip_horizontal, reset_view=False)

    def flip_vertical(self) -> None:
        self._apply_edit(ImageDocument.flip_vertical, reset_view=False)

    def trigger_crop_action(self) -> bool:
        """Toggle border crop handles with C (for image), or export trim (for video)."""
        if self.current_is_video:
            if not self.video_view.has_trim_points():
                self.status.showMessage("Mark In ([ or I) and Out (] or O) points first, then press T to trim.", 4000)
                return False
            self.video_view.execute_trim()
            return True
        if self.document is None:
            return False
        sel = self.canvas.selection
        img_rect = self.document.current.rect()

        # 1. No crop box active yet -> place crop handles along the outer image borders
        if sel.isNull() or sel.width() < 2 or sel.height() < 2:
            self.canvas.select_all()
            self.status.showMessage(
                "Crop mode active: drag any border or corner inward, then press C or Enter to crop (Esc to cancel).",
                4500,
            )
            return True

        # 2. Crop box is still untouched at full image borders -> toggle it off
        if sel == img_rect:
            self.canvas.clear_selection()
            self.status.showMessage("Crop mode cancelled.", 2000)
            return False

        # 3. Selection is smaller than full image -> apply the crop!
        return self.apply_crop()

    def apply_crop(self) -> bool:
        if self.document is None or self.current_is_video:
            return False
        sel = self.canvas.selection
        if sel.isNull() or sel.width() < 2 or sel.height() < 2:
            self.canvas.select_all()
            self.status.showMessage(
                "Crop mode active: drag any border or corner inward, then press C or Enter to crop.",
                4000,
            )
            return False
        if self.document.crop(sel):
            self.canvas.set_image(self.document.current, reset_view=True)
            self._update_ui_state()
            self.status.showMessage(
                f"Cropped to {self.document.current.width()} × {self.document.current.height()} px (Ctrl+S to save, Ctrl+Z to undo)",
                4000,
            )
            return True
        return False

    def undo_edit(self) -> None:
        if self.document is None or self.current_is_video:
            return
        if self.document.undo():
            self.canvas.set_image(self.document.current, reset_view=True)
            self._update_ui_state()
            self.status.showMessage("Undid last change.", 2500)

    def save_current(self) -> bool:
        if not self.color_bar.is_neutral:
            return bool(self.save_adjusted_image())
        if self.document is None or self.current_is_video:
            return False
        if is_animated_image(self.document.path):
            # The document holds a single frame; writing it back would destroy the animation.
            QMessageBox.warning(
                self,
                "Animated Image",
                f"'{self.document.path.name}' is animated. Saving in place would keep only one frame.\n"
                "Use Save As (Ctrl+Shift+S) to write the edited frame to a new file.",
            )
            return False
        try:
            save_qimage_atomic(self.document.current, self.document.path)
            self.cache.invalidate(self.document.path)
            self.cache.put(self.document.path, self.document.current)
            self.document.mark_saved()
            self._update_ui_state()
            self.status.showMessage(f"Saved {self.document.path.name}", 3000)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Could not save {self.document.path}:\n{exc}")
            return False

    def save_as_dialog(self) -> bool:
        if self.document is None or self.current_is_video:
            return False
        default_path = str(self.document.path)
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Image As",
            default_path,
            "Images (*.jpg *.jpeg *.png *.webp *.bmp *.tiff)",
        )
        if not out_path:
            return False
        dest = Path(out_path)
        try:
            save_qimage_atomic(self.document.current, dest)
            self.cache.invalidate(dest)
            self.cache.put(dest, self.document.current)
            self.document.mark_saved(dest)
            # Refresh folder list if saved into current folder
            if self.navigator.folder and dest.parent.resolve() == self.navigator.folder.resolve():
                self.navigator.load_target(dest)
            self._update_ui_state()
            self.status.showMessage(f"Saved as {dest.name}", 3000)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Save Error", f"Could not save {dest}:\n{exc}")
            return False

    def compress_current_variant(self) -> Optional[Path]:
        """One-click compress the current image and save as a new variant in the same folder."""
        if self.document is None or self.current_is_video:
            return None
        mode = str(self.combo_compress.currentData() or "lossless")
        try:
            variant_path, orig_bytes, new_bytes = compress_image_as_variant(self.document, mode=mode)
            # Re-scan folder so the new variant is included in folder navigation while keeping current file active
            current_path = self.document.path
            self.navigator.load_target(current_path)
            self._update_ui_state()

            if orig_bytes > 0:
                diff_pct = ((new_bytes - orig_bytes) / orig_bytes) * 100.0
                sign = "+" if diff_pct > 0 else ""
                stats = f"{human_size(orig_bytes)} → {human_size(new_bytes)} ({sign}{diff_pct:.1f}%)"
            else:
                stats = human_size(new_bytes)

            self.status.showMessage(f"Saved variant: {variant_path.name}  [{stats}]", 5500)
            return variant_path
        except CompressionError as exc:
            QMessageBox.information(self, "Compress", str(exc))
            return None
        except Exception as exc:
            QMessageBox.critical(self, "Compression Error", f"Failed to compress image:\n{exc}")
            return None

    def _confirm_discard_or_save(self) -> bool:
        """If the current image has unsaved edits, ask whether to Save, Discard, or Cancel."""
        if not self._prompt_unsaved or self.document is None or not self.document.modified:
            return True

        was_slideshow = self.slideshow_active
        if was_slideshow:
            self.set_slideshow(False)

        box = QMessageBox(self)
        box.setWindowTitle("Unsaved Image Changes")
        box.setText(f"'{self.document.path.name}' has unsaved edits (rotate/crop).")
        box.setInformativeText("Do you want to save your changes before switching files?")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        choice = box.exec()

        if choice == QMessageBox.StandardButton.Save:
            return self.save_current()
        if choice == QMessageBox.StandardButton.Discard:
            self.document.reset()
            return True
        return False

    # ------------------------------------------------------------------
    # Slideshow & Fullscreen
    # ------------------------------------------------------------------

    def toggle_slideshow(self) -> None:
        self.set_slideshow(not self.slideshow_active)

    def set_slideshow(self, active: bool) -> None:
        if active and self.navigator.count == 0:
            self.btn_slideshow.setChecked(False)
            return

        self.slideshow_active = active
        self.btn_slideshow.setChecked(active)
        self.video_view.set_slideshow_mode(active)

        if active:
            self.btn_slideshow.setText("⏹ Stop")
            self._start_slideshow_timer()
            self.status.showMessage(
                f"Slideshow started ({self.slideshow_interval_sec:.1f}s interval, "
                f"{'shuffle' if self.slideshow_random else 'sequential'})",
                3000,
            )
        else:
            self.btn_slideshow.setText("▶ Slideshow")
            self.slideshow_timer.stop()
            self.status.showMessage("Slideshow stopped", 2000)

    def _start_slideshow_timer(self) -> None:
        if not self.slideshow_active:
            return
        interval_ms = int(self.slideshow_interval_sec * 1000)
        if self.current_is_video:
            if self.video_view.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self.video_view.player.play()
            dur = self.video_view.player.duration()
            pos = self.video_view.player.position()
            remaining_ms = (dur - pos) if (dur > 0 and dur > pos) else 0
            if 0 < remaining_ms <= 30000:
                wait_ms = max(interval_ms, remaining_ms + 250)
            else:
                wait_ms = interval_ms
            self.slideshow_timer.start(wait_ms)
        else:
            self.slideshow_timer.start(interval_ms)

    def _on_interval_changed(self, value: float) -> None:
        self.slideshow_interval_sec = max(0.5, value)
        if self.slideshow_active:
            self._start_slideshow_timer()

    def _on_shuffle_toggled(self, checked: bool) -> None:
        self.slideshow_random = checked

    def _on_slideshow_tick(self) -> None:
        if not self.slideshow_active:
            return
        self.next_item()

    def _on_video_finished_in_slideshow(self) -> None:
        if self.slideshow_active:
            self.slideshow_timer.stop()
            self.next_item()

    def _on_video_transcoded(self, new_mp4_path: Path) -> None:
        self.navigator.load_target(new_mp4_path)
        self._display_current_file()
        self.status.showMessage(f"Transcoded to {new_mp4_path.name} and playing full video!", 5000)

    def _add_new_file_to_folder(self, new_path: Path, message: str) -> None:
        """Re-scan so a file written next to the current one shows up in ←/→ navigation."""
        cur = self.navigator.current_file
        if cur is not None and new_path.parent == cur.parent:
            self.navigator.load_target(cur)
            self._update_ui_state()
        self.status.showMessage(f"{message}: {new_path.name} (Press → to view)", 8000)

    def _on_video_trimmed(self, new_clip_path: Path) -> None:
        self._add_new_file_to_folder(new_clip_path, "✂ Trimmed video saved")

    def export_adjusted_video(self) -> Optional[Path]:
        """Write a copy of the current video with the brightness/contrast baked in (background ffmpeg)."""
        if not self.current_is_video:
            return None
        if self.color_bar.is_neutral:
            self.status.showMessage("Move the Brightness or Contrast slider first.", 3000)
            return None
        target = self.video_view.export_color_adjusted(self.color_bar.brightness, self.color_bar.contrast)
        if target is not None:
            self.color_bar.set_exporting(True)
            self.status.showMessage(f"⏳ Exporting {target.name} in the background...", 5000)
        return target

    def _on_color_export_done(self, new_path: Path) -> None:
        self.color_bar.set_exporting(False)
        self._add_new_file_to_folder(new_path, "🎬 Color-adjusted video saved")

    def _on_color_export_failed(self, error: str) -> None:
        self.color_bar.set_exporting(False)
        QMessageBox.warning(self, "Video Export Error", error)

    def toggle_fullscreen(self) -> None:
        # Toolbars / status bar / overlay follow in changeEvent, which also covers the
        # window manager toggling fullscreen on its own (e.g. a desktop-wide F11).
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _apply_fullscreen_chrome(self) -> None:
        fullscreen = self.isFullScreen()
        for bar in (self.top_bar, self.status):
            bar.setVisible(not fullscreen)
        self.fs_overlay.setVisible(fullscreen)
        if fullscreen:
            self.fs_overlay.place()
            # The fullscreen geometry usually arrives after the state change; re-pin once it has.
            QTimer.singleShot(0, self.fs_overlay, self.fs_overlay.place)

    def toggle_info_overlay(self) -> None:
        self._info_visible = not self._info_visible
        self.canvas.set_info_overlay(self._info_visible, self._build_info_text() if self._info_visible else "")

    def show_media_info_dialog(self) -> None:
        """Open the geeky Media Inspector dialog showing bitrate, compression, and EXIF."""
        cur = self.navigator.current_file
        if cur is None or not cur.exists():
            self.status.showMessage("No file loaded to inspect.", 3000)
            return
        dlg = MediaInfoDialog(cur, self)
        dlg.exec()

    def _build_info_text(self) -> str:
        cur = self.navigator.current_file
        if cur is None:
            return ""
        try:
            st = cur.stat()
            size_str = human_size(st.st_size)
            mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            size_str = "?"
            mtime_str = "?"

        dims = ""
        if self.document is not None:
            dims = f"{self.document.current.width()} × {self.document.current.height()} px"

        return (
            f"File      : {cur.name}\n"
            f"Folder    : {cur.parent}\n"
            f"Index     : {self.navigator.index + 1} / {self.navigator.count}\n"
            f"Resolution: {dims}\n"
            f"Size      : {size_str}\n"
            f"Modified  : {mtime_str}"
        )

    # ------------------------------------------------------------------
    # UI Status Updates
    # ------------------------------------------------------------------

    def _on_zoom_changed(self, scale: float) -> None:
        if not self.current_is_video and self.document is not None:
            self.lbl_zoom.setText(f"{int(round(scale * 100))}%")
        else:
            self.lbl_zoom.setText("")

    def _on_selection_changed(self, rect: QRect) -> None:
        is_ready = (not self.current_is_video and self.document is not None) or self.current_is_video
        self.btn_crop.setEnabled(is_ready)

    def _update_ui_state(self) -> None:
        cur = self.navigator.current_file
        has_file = cur is not None
        is_img = has_file and not self.current_is_video and self.document is not None
        is_vid = has_file and self.current_is_video
        self.color_bar.set_video_mode(bool(is_vid))

        self.btn_prev.setEnabled(self.navigator.count > 1)
        self.btn_next.setEnabled(self.navigator.count > 1)
        self.btn_color.setEnabled(has_file)

        self.btn_rot_left.setEnabled(is_img)
        self.btn_rot_right.setEnabled(is_img)
        self.btn_flip_h.setEnabled(is_img)
        self.btn_crop.setEnabled(is_img or is_vid)
        if is_vid:
            self.btn_crop.setText("✂ Trim")
            self.btn_crop.setToolTip("Export the marked clip with FFmpeg: mark In / Out with [ / ] or I / O, then press T  (T)")
        else:
            self.btn_crop.setText("✂ Crop")
            self.btn_crop.setToolTip("Show border crop handles (or drag a box), then press C or Enter to apply the crop  (C)")
        self.btn_compress.setEnabled(is_img)
        self.combo_compress.setEnabled(is_img)
        self.btn_fit.setEnabled(is_img)
        self.btn_actual.setEnabled(is_img)
        self.btn_info.setEnabled(has_file)

        modified = bool(is_img and self.document and self.document.modified)
        has_color_adj = not self.color_bar.is_neutral
        self.btn_undo.setEnabled(modified)
        self.btn_save.setEnabled(modified or has_color_adj)

        if not has_file or cur is None:
            self.setWindowTitle(__app_name__)
            self.lbl_position.setText("[0 / 0]")
            self.lbl_filename.setText("No file loaded")
            self.lbl_dimensions.setText("")
            self.lbl_zoom.setText("")
            self.lbl_modified.setText("")
            return

        idx_str = f"[{self.navigator.index + 1} / {self.navigator.count}]"
        mod_prefix = "● " if modified else ""
        self.setWindowTitle(f"{mod_prefix}{cur.name}  {idx_str} — {__app_name__}")
        self.lbl_position.setText(idx_str)

        try:
            fsize = human_size(cur.stat().st_size)
        except OSError:
            fsize = ""
        self.lbl_filename.setText(f"{cur.name}   ({fsize})" if fsize else cur.name)

        if is_img and self.document is not None:
            w = self.document.current.width()
            h = self.document.current.height()
            self.lbl_dimensions.setText(f"{w} × {h} px")
            self.lbl_zoom.setText(f"{int(round(self.canvas.zoom_scale * 100))}%")
            if has_color_adj:
                self.lbl_modified.setText("● COLOR ADJ (Ctrl+S to save new)")
            elif modified:
                self.lbl_modified.setText("● MODIFIED (Ctrl+S to save)")
            else:
                self.lbl_modified.setText("")
            if self._info_visible:
                self.canvas.set_info_overlay(True, self._build_info_text())
        else:
            self.lbl_dimensions.setText("VIDEO")
            self.lbl_zoom.setText("")
            if has_color_adj:
                self.lbl_modified.setText("● COLOR ADJ (Ctrl+S to save frame)")
            else:
                self.lbl_modified.setText("")

    def closeEvent(self, event) -> None:
        if not self._confirm_discard_or_save():
            event.ignore()
            return
        self.video_view.stop()
        self.video_view.shutdown()
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.isFullScreen():
            self.fs_overlay.place()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            self._apply_fullscreen_chrome()
        super().changeEvent(event)

    def show_more_options_menu(self, anchor_widget: Optional[QWidget] = None, exec_menu: bool = True) -> QMenu:
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.WindowType.Popup)
        is_vid = self.current_is_video
        is_img = not is_vid and self.document is not None
        has_file = self.navigator.current_file is not None
        modified = bool(is_img and self.document.modified)

        def add(text: str, slot, enabled: bool = True) -> QAction:
            act = menu.addAction(text, slot)
            act.setEnabled(enabled)
            return act

        add("📂 Open File... (O)", self.open_file_dialog)
        add("💾 Save (Ctrl+S)", self.save_current, modified)
        add("💾 Save As... (Ctrl+Shift+S)", self.save_as_dialog, is_img)
        menu.addSeparator()

        add("🗜 Compress Variant (K)", self.compress_current_variant, is_img)
        presets = menu.addMenu("🎯 Compression Preset")
        group = QActionGroup(presets)
        cur_mode = self.combo_compress.currentData()
        for idx, (label, mode_id) in enumerate(COMPRESSION_MODES):
            act = presets.addAction(label, lambda index=idx: self.combo_compress.setCurrentIndex(index))
            act.setCheckable(True)
            act.setChecked(mode_id == cur_mode)
            group.addAction(act)
        menu.addSeparator()

        add("✂ Trim Video (T)" if is_vid else "✂ Border Crop (C)", self.trigger_crop_action, is_img or is_vid)
        add("🎨 Color Scaling / Brightness & Contrast (B)", self.toggle_color_bar, has_file)
        add("↺ Rotate Left 90° (L)", self.rotate_left, is_img)
        add("↻ Rotate Right 90° (R)", self.rotate_right, is_img)
        add("⇆ Flip Horizontal (H)", self.flip_horizontal, is_img)
        add("⇅ Flip Vertical (V)", self.flip_vertical, is_img)
        add("↶ Undo Edit (Ctrl+Z)", self.undo_edit, modified)
        menu.addSeparator()

        add("🎲 Random File (Ctrl+M)", self.random_item, self.navigator.count > 1)
        slide = add("▶ Slideshow (W or F5)", self.toggle_slideshow)
        slide.setCheckable(True)
        slide.setChecked(self.slideshow_active)
        add("⊞ Fit to Window (0)", self.canvas.zoom_fit, is_img)
        add("1:1 Actual Size (1)", self.canvas.zoom_actual, is_img)
        add("⛶ Fullscreen (F)", self.toggle_fullscreen)
        add("ℹ Media Inspector / Info Card (Ctrl+I)", self.show_media_info_dialog, has_file)
        menu.addSeparator()
        add("⌨ Keyboard Shortcuts...", self.show_shortcuts_dialog)
        add("🔄 Check for Updates...", self.check_for_updates, not self.update_checker.running)
        auto = add("Check for Updates on Startup", self._toggle_startup_update_check)
        auto.setCheckable(True)
        auto.setChecked(self.update_settings.check_on_startup)
        add("🦉 About HakuView...", self.show_about_dialog)

        if exec_menu:
            menu.exec(menu_position_under(menu, anchor_widget or self.btn_more, fallback=self))
        return menu

    def show_shortcuts_dialog(self) -> None:
        # Generated from the same tables that bind the keys, so the help can't drift.
        rows = self._KEYPRESS_HELP + [(label, text) for _seqs, _fn, label, text in self._shortcut_table if label]
        dialogs.show_shortcuts_dialog(self, rows)

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def _toggle_startup_update_check(self) -> None:
        self.update_settings.check_on_startup = not self.update_settings.check_on_startup
        state = "on" if self.update_settings.check_on_startup else "off"
        self.status.showMessage(f"Update check on startup turned {state}.", 3000)

    def check_for_updates(self, manual: bool = True) -> None:
        """Ask GitHub for the newest tagged version. Manual checks also report 'up to date' / errors."""
        self._update_check_manual = manual
        if manual:
            self.status.showMessage("🔄 Checking for updates...", 3000)
        self.update_checker.start()

    def _on_update_check_finished(self, latest: str, error: str) -> None:
        manual = self._update_check_manual
        self.update_settings.mark_checked()
        if error:
            if manual:
                QMessageBox.information(self, "Check for Updates", f"Couldn't reach GitHub to check for updates.\n\n{error}")
            return
        if not latest:
            if manual:
                QMessageBox.information(
                    self,
                    "Check for Updates",
                    f"No releases are published on GitHub yet, so there's nothing to compare against.\n\n"
                    f"You have {__app_name__} {display_version()}.",
                )
            return
        if not is_newer(latest):
            if manual:
                QMessageBox.information(
                    self, "Check for Updates", f"You're up to date: {__app_name__} {display_version()} (latest is {latest})."
                )
            return
        if not manual and latest == self.update_settings.skipped_version:
            return
        self._show_update_prompt(latest)

    def _show_update_prompt(self, latest: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("Update Available")
        box.setText(f"<b>{__app_name__} {latest}</b> is available.")
        box.setInformativeText(f"You have {__version__}.")
        update_label = "Update Now" if can_self_update() else "Open Download Page"
        btn_update = box.addButton(update_label, QMessageBox.ButtonRole.AcceptRole)
        btn_skip = box.addButton("Skip This Version", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(btn_update)
        auto = QCheckBox("Check for updates on startup")
        auto.setChecked(self.update_settings.check_on_startup)
        box.setCheckBox(auto)
        box.exec()

        self.update_settings.check_on_startup = auto.isChecked()
        clicked = box.clickedButton()
        if clicked is btn_skip:
            self.update_settings.skipped_version = latest
        elif clicked is btn_update:
            self.run_update()

    def run_update(self) -> None:
        if not can_self_update():
            QDesktopServices.openUrl(QUrl(RELEASES_URL))
            return
        if self._update_runner is not None:
            return
        progress = QProgressDialog("Updating HakuView...", None, 0, 0, self)
        progress.setWindowTitle("Updating")
        progress.setMinimumDuration(0)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.show()

        runner = UpdateRunner(self)
        self._update_runner = runner

        def done(ok: bool, output: str) -> None:
            progress.close()
            self._update_runner = None
            runner.deleteLater()
            self._on_update_finished(ok, output)

        runner.finished.connect(done)
        runner.start()

    def _on_update_finished(self, ok: bool, output: str) -> None:
        if not ok:
            box = QMessageBox(QMessageBox.Icon.Warning, "Update Failed", "The update didn't complete.", parent=self)
            box.setDetailedText(output[-4000:])
            box.exec()
            return
        reply = QMessageBox.question(
            self,
            "Update Installed",
            "HakuView was updated. Restart now to use the new version?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.restart()

    def restart(self) -> None:
        """Close (honoring the unsaved-changes prompt) and relaunch on the same file."""
        current = self.navigator.current_file
        args = ["-m", "hakuview.cli"] + ([str(current)] if current else [])
        if self.close():
            QProcess.startDetached(sys.executable, args)

    def show_about_dialog(self) -> None:
        dialogs.show_about_dialog(self)
