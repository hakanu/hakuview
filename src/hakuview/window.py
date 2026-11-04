"""Main HakuView window implementing IrfanView-style navigation, editing, and slideshow."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QPoint, QRect, QSettings, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
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
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QToolButton,
    QWidget,
)

from . import __app_name__, __version__
from .canvas import ImageCanvas
from .folder_scanner import FolderNavigator, is_image_file, is_video_file
from .image_ops import (
    COMPRESSION_MODES,
    ImageDocument,
    ImagePreloadCache,
    compress_image_as_variant,
    save_qimage_atomic,
)
from .installer import ensure_hakuview_ico
from .video_view import VideoPlayerWidget


def _human_size(num_bytes: int) -> str:
    val = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024.0 or unit == "GB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{val:.1f} GB"


class HakuViewWindow(QMainWindow):
    """Lightweight, keyboard-driven desktop viewer for images and videos."""

    def __init__(
        self,
        initial_path: Optional[Path | str] = None,
        pick_random: bool = False,
        start_slideshow: bool = False,
        slideshow_interval: float = 3.0,
        start_fullscreen: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle(__app_name__)
        self.setWindowIcon(QIcon(str(ensure_hakuview_ico())))
        self.resize(1180, 780)
        self.setAcceptDrops(True)

        self.settings = QSettings("HakuView", "HakuView")
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

        self._apply_dark_theme()
        self._build_ui()
        self._build_shortcuts()

        if initial_path is not None:
            self.open_path(Path(initial_path), pick_random=pick_random)

        if start_fullscreen:
            self.toggle_fullscreen()

        if start_slideshow and self.navigator.count > 0:
            self.set_slideshow(True)

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background-color: #14161a;
                color: #e2e8f0;
            }
            QToolBar {
                background-color: #1a1d24;
                border-bottom: 1px solid #2b303c;
                spacing: 4px;
                padding: 3px 6px;
            }
            QToolBar QPushButton {
                background-color: #252a34;
                color: #e2e8f0;
                border: 1px solid #353c4b;
                border-radius: 4px;
                padding: 4px 9px;
                font-size: 12px;
            }
            QToolBar QPushButton:hover {
                background-color: #323946;
                border-color: #4b5563;
            }
            QToolBar QPushButton:checked {
                background-color: #0284c7;
                border-color: #38bdf8;
                color: #ffffff;
            }
            QToolBar QPushButton:disabled {
                color: #64748b;
                background-color: #1e222a;
                border-color: #262b35;
            }
            QDoubleSpinBox, QComboBox {
                background-color: #252a34;
                color: #e2e8f0;
                border: 1px solid #353c4b;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 12px;
            }
            QCheckBox {
                color: #cbd5e1;
                font-size: 12px;
                spacing: 4px;
            }
            QToolButton {
                background-color: #252a34;
                color: #e2e8f0;
                border: 1px solid #353c4b;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
            }
            QToolButton:hover {
                background-color: #323946;
                border-color: #4b5563;
            }
            QMenu {
                background-color: #1a1d25;
                color: #e2e8f0;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 4px 0;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 24px 6px 14px;
                background-color: transparent;
            }
            QMenu::item:selected {
                background-color: #0284c7;
                color: #ffffff;
            }
            QMenu::item:disabled {
                color: #64748b;
            }
            QMenu::separator {
                height: 1px;
                background-color: #2b3240;
                margin: 4px 8px;
            }
            QStatusBar {
                background-color: #181b21;
                color: #94a3b8;
                border-top: 1px solid #262b35;
                font-size: 12px;
            }
            QStatusBar QLabel {
                color: #cbd5e1;
                padding: 0 6px;
            }
            """
        )

    def _build_ui(self) -> None:
        # Central stacked widget switching between ImageCanvas and VideoPlayerWidget
        self.stack = QStackedWidget(self)
        self.canvas = ImageCanvas(self)
        self.video_view = VideoPlayerWidget(self)
        self.stack.addWidget(self.canvas)
        self.stack.addWidget(self.video_view)
        self.setCentralWidget(self.stack)

        self.canvas.zoomChanged.connect(self._on_zoom_changed)
        self.canvas.selectionChanged.connect(self._on_selection_changed)
        self.canvas.doubleClicked.connect(self.toggle_fullscreen)
        self.video_view.doubleClicked.connect(self.toggle_fullscreen)
        self.video_view.videoFinished.connect(self._on_video_finished_in_slideshow)
        self.video_view.transcodeCompleted.connect(self._on_video_transcoded)

        # Compact Top Toolbar
        self.toolbar = QToolBar("Main Toolbar", self)
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.toolbar)

        self.btn_open = self._add_tb_button("📂 Open (O)", self.open_file_dialog, "Open an image or video file")
        self.btn_prev = self._add_tb_button("◀ Prev (←)", self.prev_item, "Previous file in folder")
        self.btn_next = self._add_tb_button("Next (→) ▶", self.next_item, "Next file in folder")
        self.btn_random = self._add_tb_button("🎲 Random (Ctrl+M)", self.random_item, "Jump to a random file in folder")

        self.toolbar.addSeparator()

        self.btn_rot_left = self._add_tb_button("↺ Rotate L (L)", self.rotate_left, "Rotate 90° Counter-Clockwise")
        self.btn_rot_right = self._add_tb_button("↻ Rotate R (R)", self.rotate_right, "Rotate 90° Clockwise")
        self.btn_flip_h = self._add_tb_button("⇆ Flip H (H)", self.flip_horizontal, "Flip Horizontally")
        self.btn_crop = self._add_tb_button(
            "✂ Crop (C)",
            self.trigger_crop_action,
            "Press C to show border crop handles (or drag a box), then press C or Enter to apply crop",
        )
        self.btn_crop.setEnabled(False)
        self.btn_undo = self._add_tb_button("↶ Undo (Ctrl+Z)", self.undo_edit, "Undo last image edit")
        self.btn_undo.setEnabled(False)
        self.btn_save = self._add_tb_button("💾 Save (Ctrl+S)", self.save_current, "Save changes (Ctrl+Shift+S for Save As)")
        self.btn_save.setEnabled(False)

        self.btn_compress = self._add_tb_button(
            "🗜 Compress (K)",
            self.compress_current_variant,
            "One-click compress & save as a variant file in the same folder (Shortcut: K)",
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
            "▶ Slideshow (W)",
            self.toggle_slideshow,
            "Start/Stop automatic slideshow (W or F5)",
            checkable=True,
        )

        lbl_sec = QLabel(" Interval:")
        lbl_sec.setStyleSheet("color: #94a3b8; font-size: 12px;")
        self.toolbar.addWidget(lbl_sec)

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

        self.btn_fit = self._add_tb_button("⊞ Fit (0)", self.canvas.zoom_fit, "Fit image to window")
        self.btn_actual = self._add_tb_button("1:1 (1)", self.canvas.zoom_actual, "Actual pixel size (100%)")
        self.btn_fullscreen = self._add_tb_button("⛶ Fullscreen (F)", self.toggle_fullscreen, "Toggle Fullscreen (F or F11)")

        # Spacer & 3-dots More Options button
        spacer = QWidget(self)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.toolbar.addWidget(spacer)

        self.btn_more = QPushButton("⋮", self)
        self.btn_more.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_more.setToolTip("More Options (3 dots menu)")
        self.btn_more.setStyleSheet("font-weight: bold; font-size: 16px; padding: 2px 9px;")
        self.btn_more.clicked.connect(lambda: self.show_more_options_menu(self.btn_more))
        self.toolbar.addWidget(self.btn_more)

        # Status Bar
        self.status = QStatusBar(self)
        self.setStatusBar(self.status)

        self.lbl_position = QLabel("[0 / 0]")
        self.lbl_filename = QLabel("No file loaded")
        self.lbl_dimensions = QLabel("")
        self.lbl_zoom = QLabel("")
        self.lbl_modified = QLabel("")
        self.lbl_modified.setStyleSheet("color: #38bdf8; font-weight: bold;")

        self.status.addWidget(self.lbl_position)
        self.status.addWidget(self.lbl_filename, 1)
        self.status.addPermanentWidget(self.lbl_modified)
        self.status.addPermanentWidget(self.lbl_dimensions)
        self.status.addPermanentWidget(self.lbl_zoom)

    def _add_tb_button(
        self,
        text: str,
        slot,
        tooltip: str,
        checkable: bool = False,
    ) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setToolTip(tooltip)
        btn.setCheckable(checkable)
        btn.clicked.connect(slot)
        self.toolbar.addWidget(btn)
        return btn

    def _build_shortcuts(self) -> None:
        # File & Folder actions
        self._add_action("Open", "O", self.open_file_dialog)
        self._add_action("OpenCtrl", "Ctrl+O", self.open_file_dialog)
        self._add_action("CloseWindow", "Ctrl+W", self.close)
        self._add_action("QuitApp", "Ctrl+Q", self.close)
        self._add_action("Save", "Ctrl+S", self.save_current)
        self._add_action("SaveAs", "Ctrl+Shift+S", self.save_as_dialog)
        self._add_action("Undo", "Ctrl+Z", self.undo_edit)
        self._add_action("RandomFile", "Ctrl+M", self.random_item)
        self._add_action("DeleteFile", "Delete", self.delete_current_file)
        self._add_action("InfoOverlay", "I", self.toggle_info_overlay)

        # Image transformations (IrfanView keybindings)
        self._add_action("RotateRight", "R", self.rotate_right)
        self._add_action("RotateLeft", "L", self.rotate_left)
        self._add_action("FlipH", "H", self.flip_horizontal)
        self._add_action("FlipV", "V", self.flip_vertical)
        self._add_action("CropC", "C", self.trigger_crop_action)
        self._add_action("CropIrfan", "Ctrl+Y", self.apply_crop)
        self._add_action("CompressK", "K", self.compress_current_variant)
        self._add_action("CompressCtrlK", "Ctrl+K", self.compress_current_variant)

        # Slideshow
        self._add_action("SlideshowW", "W", self.toggle_slideshow)
        self._add_action("SlideshowF5", "F5", self.toggle_slideshow)
        self._add_action("FasterSlideshow", "[", lambda: self.spin_interval.setValue(max(0.5, self.spin_interval.value() - 0.5)))
        self._add_action("SlowerSlideshow", "]", lambda: self.spin_interval.setValue(min(120.0, self.spin_interval.value() + 0.5)))

        # Zoom & Fullscreen
        self._add_action("FullscreenF", "F", self.toggle_fullscreen)
        self._add_action("FullscreenF11", "F11", self.toggle_fullscreen)
        self._add_action("ZoomInPlus", "+", self.canvas.zoom_in)
        self._add_action("ZoomInEqual", "=", self.canvas.zoom_in)
        self._add_action("ZoomOutMinus", "-", self.canvas.zoom_out)
        self._add_action("ZoomFit0", "0", self.canvas.zoom_fit)
        self._add_action("ZoomActual1", "1", self.canvas.zoom_actual)
        self._add_action("SelectAll", "Ctrl+A", self.canvas.select_all)

    def _add_action(self, name: str, shortcut: str, slot) -> QAction:
        act = QAction(name, self)
        act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        self.addAction(act)
        return act

    # ------------------------------------------------------------------
    # Keyboard handling (Arrow keys, Space, Enter, Escape)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        mods = event.modifiers()

        # Left / Right Arrow keys ALWAYS cycle through the folder unless Shift is held during video playback
        if key == Qt.Key.Key_Right:
            if self.current_is_video and (mods & Qt.KeyboardModifier.ShiftModifier):
                self.video_view.seek_relative_ms(5000)
            else:
                self.next_item()
            event.accept()
            return

        if key == Qt.Key.Key_Left:
            if self.current_is_video and (mods & Qt.KeyboardModifier.ShiftModifier):
                self.video_view.seek_relative_ms(-5000)
            else:
                self.prev_item()
            event.accept()
            return

        if key == Qt.Key.Key_Home:
            self.first_item()
            event.accept()
            return

        if key == Qt.Key.Key_End:
            self.last_item()
            event.accept()
            return

        if key == Qt.Key.Key_PageDown:
            self.next_item()
            event.accept()
            return

        if key == Qt.Key.Key_PageUp:
            self.prev_item()
            event.accept()
            return

        if key == Qt.Key.Key_Space:
            if self.current_is_video:
                self.video_view.toggle_play_pause()
            else:
                if not event.isAutoRepeat():
                    self.canvas.set_space_pressed(True)
            event.accept()
            return

        if key == Qt.Key.Key_M and self.current_is_video:
            self.video_view.toggle_mute()
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self.current_is_video and not self.canvas.selection.isNull():
                self.apply_crop()
            else:
                self.toggle_fullscreen()
            event.accept()
            return

        if key == Qt.Key.Key_Escape:
            if not self.current_is_video and not self.canvas.selection.isNull():
                self.canvas.clear_selection()
            elif self.slideshow_active:
                self.set_slideshow(False)
            elif self.isFullScreen():
                self.toggle_fullscreen()
            event.accept()
            return

        super().keyPressEvent(event)

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
            "Media Files (*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tiff *.tif *.ico *.svg *.avif *.heic *.mp4 *.mkv *.webm *.avi *.mov *.m4v *.wmv *.flv *.mpg *.mpeg);;All Files (*)",
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

    def next_item(self) -> Optional[Path]:
        if self.navigator.count == 0:
            return None
        if not self._confirm_discard_or_save():
            return self.navigator.current_file
        target = (
            self.navigator.random_file()
            if (self.slideshow_active and self.slideshow_random)
            else self.navigator.next(wrap=True)
        )
        if target is not None:
            self._display_current_file()
        return target

    def prev_item(self) -> Optional[Path]:
        if self.navigator.count == 0:
            return None
        if not self._confirm_discard_or_save():
            return self.navigator.current_file
        target = self.navigator.prev(wrap=True)
        if target is not None:
            self._display_current_file()
        return target

    def first_item(self) -> Optional[Path]:
        if self.navigator.count == 0:
            return None
        if not self._confirm_discard_or_save():
            return self.navigator.current_file
        target = self.navigator.first()
        if target is not None:
            self._display_current_file()
        return target

    def last_item(self) -> Optional[Path]:
        if self.navigator.count == 0:
            return None
        if not self._confirm_discard_or_save():
            return self.navigator.current_file
        target = self.navigator.last()
        if target is not None:
            self._display_current_file()
        return target

    def random_item(self) -> Optional[Path]:
        if self.navigator.count == 0:
            return None
        if not self._confirm_discard_or_save():
            return self.navigator.current_file
        target = self.navigator.random_file()
        if target is not None:
            self._display_current_file()
        return target

    def _display_current_file(self) -> bool:
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

    def rotate_left(self) -> None:
        if self.document is None or self.current_is_video:
            return
        self.document.rotate_left()
        self.canvas.set_image(self.document.current, reset_view=True)
        self._update_ui_state()

    def rotate_right(self) -> None:
        if self.document is None or self.current_is_video:
            return
        self.document.rotate_right()
        self.canvas.set_image(self.document.current, reset_view=True)
        self._update_ui_state()

    def flip_horizontal(self) -> None:
        if self.document is None or self.current_is_video:
            return
        self.document.flip_horizontal()
        self.canvas.set_image(self.document.current, reset_view=False)
        self._update_ui_state()

    def flip_vertical(self) -> None:
        if self.document is None or self.current_is_video:
            return
        self.document.flip_vertical()
        self.canvas.set_image(self.document.current, reset_view=False)
        self._update_ui_state()

    def trigger_crop_action(self) -> bool:
        """Toggle border crop handles with C, or apply crop if borders/selection were adjusted."""
        if self.document is None or self.current_is_video:
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
        if self.document is None or self.current_is_video:
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
                stats = f"{_human_size(orig_bytes)} → {_human_size(new_bytes)} ({sign}{diff_pct:.1f}%)"
            else:
                stats = _human_size(new_bytes)

            self.status.showMessage(f"Saved variant: {variant_path.name}  [{stats}]", 5500)
            return variant_path
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

    def delete_current_file(self) -> None:
        current = self.navigator.current_file
        if current is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete File",
            f"Permanently delete '{current.name}' from disk?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            if self.current_is_video:
                self.video_view.stop()
            current.unlink()
            self.cache.invalidate(current)
            if self.document is not None:
                self.document.modified = False
            self.navigator.remove_current_from_list()
            self._display_current_file()
            self.status.showMessage(f"Deleted {current.name}", 3000)
        except OSError as exc:
            QMessageBox.critical(self, "Delete Error", f"Could not delete {current}:\n{exc}")

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
            self.btn_slideshow.setText("⏹ Stop (W)")
            self._start_slideshow_timer()
            self.status.showMessage(
                f"Slideshow started ({self.slideshow_interval_sec:.1f}s interval, "
                f"{'shuffle' if self.slideshow_random else 'sequential'})",
                3000,
            )
        else:
            self.btn_slideshow.setText("▶ Slideshow (W)")
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

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.showNormal()
            self.toolbar.setVisible(True)
            self.status.setVisible(True)
        else:
            self.toolbar.setVisible(False)
            self.status.setVisible(False)
            self.showFullScreen()

    def toggle_info_overlay(self) -> None:
        self._info_visible = not self._info_visible
        self.canvas.set_info_overlay(self._info_visible, self._build_info_text() if self._info_visible else "")

    def _build_info_text(self) -> str:
        cur = self.navigator.current_file
        if cur is None:
            return ""
        try:
            st = cur.stat()
            size_str = _human_size(st.st_size)
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
        is_img = not self.current_is_video and self.document is not None
        self.btn_crop.setEnabled(is_img)

    def _update_ui_state(self) -> None:
        cur = self.navigator.current_file
        has_file = cur is not None
        is_img = has_file and not self.current_is_video and self.document is not None

        self.btn_prev.setEnabled(self.navigator.count > 1)
        self.btn_next.setEnabled(self.navigator.count > 1)
        self.btn_random.setEnabled(self.navigator.count > 1)

        self.btn_rot_left.setEnabled(is_img)
        self.btn_rot_right.setEnabled(is_img)
        self.btn_flip_h.setEnabled(is_img)
        self.btn_crop.setEnabled(is_img)
        self.btn_compress.setEnabled(is_img)
        self.combo_compress.setEnabled(is_img)
        self.btn_fit.setEnabled(is_img)
        self.btn_actual.setEnabled(is_img)

        modified = bool(is_img and self.document and self.document.modified)
        self.btn_undo.setEnabled(modified)
        self.btn_save.setEnabled(modified)

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
            fsize = _human_size(cur.stat().st_size)
        except OSError:
            fsize = ""
        self.lbl_filename.setText(f"{cur.name}   ({fsize})" if fsize else cur.name)

        if is_img and self.document is not None:
            w = self.document.current.width()
            h = self.document.current.height()
            self.lbl_dimensions.setText(f"{w} × {h} px")
            self.lbl_zoom.setText(f"{int(round(self.canvas.zoom_scale * 100))}%")
            self.lbl_modified.setText("● MODIFIED (Ctrl+S to save)" if modified else "")
            if self._info_visible:
                self.canvas.set_info_overlay(True, self._build_info_text())
        else:
            self.lbl_dimensions.setText("VIDEO")
            self.lbl_zoom.setText("")
            self.lbl_modified.setText("")

    def closeEvent(self, event) -> None:
        if not self._confirm_discard_or_save():
            event.ignore()
            return
        self.video_view.stop()
        super().closeEvent(event)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._hook_toolbar_extension()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._hook_toolbar_extension()

    def _hook_toolbar_extension(self) -> None:
        if not hasattr(self, "toolbar"):
            return
        ext = self.toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
        if ext and not getattr(ext, "_hooked", False):
            ext._hooked = True
            ext.setToolTip("More Options (3 dots menu)")
            ext.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if hasattr(self, "toolbar") and event.type() in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonDblClick,
        ):
            ext = self.toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
            if watched is ext:
                self.show_more_options_menu(anchor_widget=ext)
                return True
        return super().eventFilter(watched, event)

    def show_more_options_menu(self, anchor_widget: Optional[QWidget] = None) -> None:
        menu = QMenu(self)
        is_img = not self.current_is_video and self.document is not None
        has_file = self.navigator.current_file is not None
        modified = bool(is_img and self.document and self.document.modified)

        # File actions
        menu.addAction("📂 Open File... (O)", self.open_file_dialog)
        act_save = menu.addAction("💾 Save (Ctrl+S)", self.save_current)
        act_save.setEnabled(modified)
        act_save_as = menu.addAction("💾 Save As... (Ctrl+Shift+S)", self.save_as_dialog)
        act_save_as.setEnabled(is_img)
        act_del = menu.addAction("🗑 Delete File (Delete)", self.delete_current_file)
        act_del.setEnabled(has_file)

        menu.addSeparator()

        # Image Compression
        act_compress = menu.addAction("🗜 Compress Variant (K)", self.compress_current_variant)
        act_compress.setEnabled(is_img)

        compress_submenu = menu.addMenu("🎯 Compression Preset")
        group = QActionGroup(compress_submenu)
        group.setExclusive(True)
        cur_mode = str(self.combo_compress.currentData() or "lossless")
        for idx, (label, mode_id) in enumerate(COMPRESSION_MODES):
            mode_act = compress_submenu.addAction(label)
            mode_act.setCheckable(True)
            mode_act.setChecked(mode_id == cur_mode)
            group.addAction(mode_act)

            def _make_setter(index: int):
                return lambda: self.combo_compress.setCurrentIndex(index)

            mode_act.triggered.connect(_make_setter(idx))

        menu.addSeparator()

        # Image operations
        act_crop = menu.addAction("✂ Border Crop (C)", self.trigger_crop_action)
        act_crop.setEnabled(is_img)
        act_rotl = menu.addAction("↺ Rotate Left 90° (L)", self.rotate_left)
        act_rotl.setEnabled(is_img)
        act_rotr = menu.addAction("↻ Rotate Right 90° (R)", self.rotate_right)
        act_rotr.setEnabled(is_img)
        act_fliph = menu.addAction("⇆ Flip Horizontal (H)", self.flip_horizontal)
        act_fliph.setEnabled(is_img)
        act_flipv = menu.addAction("⇅ Flip Vertical (V)", self.flip_vertical)
        act_flipv.setEnabled(is_img)
        act_undo = menu.addAction("↶ Undo Edit (Ctrl+Z)", self.undo_edit)
        act_undo.setEnabled(modified)

        menu.addSeparator()

        # Navigation & View
        act_rand = menu.addAction("🎲 Random File (Ctrl+M)", self.random_item)
        act_rand.setEnabled(self.navigator.count > 1)
        act_slide = menu.addAction("▶ Slideshow (W or F5)", self.toggle_slideshow)
        act_slide.setCheckable(True)
        act_slide.setChecked(self.slideshow_active)
        act_fit = menu.addAction("⊞ Fit to Window (0)", self.canvas.zoom_fit)
        act_fit.setEnabled(is_img)
        act_act = menu.addAction("1:1 Actual Size (1)", self.canvas.zoom_actual)
        act_act.setEnabled(is_img)
        act_fs = menu.addAction("⛶ Fullscreen (F)", self.toggle_fullscreen)
        act_info = menu.addAction("ℹ File Info HUD (I)", self.toggle_info_overlay)
        act_info.setEnabled(has_file)

        menu.addSeparator()
        menu.addAction("⌨ Keyboard Shortcuts...", self.show_shortcuts_dialog)
        menu.addAction("🦉 About HakuView...", self.show_about_dialog)

        # Show menu positioned under the anchor
        anchor = anchor_widget or getattr(self, "btn_more", None)
        if anchor:
            pos = anchor.mapToGlobal(QPoint(0, anchor.height()))
        else:
            pos = self.mapToGlobal(QPoint(self.width() - 200, 30))
        menu.exec(pos)

    def show_shortcuts_dialog(self) -> None:
        msg = (
            "<h3>HakuView Keyboard & Mouse Shortcuts</h3>"
            "<table cellpadding='4' style='font-size: 13px;'>"
            "<tr><td><b>← / →</b></td><td>Previous / Next file in folder</td></tr>"
            "<tr><td><b>Ctrl + M</b></td><td>Jump to random file in folder</td></tr>"
            "<tr><td><b>C</b></td><td>Border Crop (drag borders & press C or Enter)</td></tr>"
            "<tr><td><b>R / L</b></td><td>Rotate 90° Right / Left</td></tr>"
            "<tr><td><b>H / V</b></td><td>Flip Horizontal / Vertical</td></tr>"
            "<tr><td><b>K</b></td><td>1-Click Compress & save as variant</td></tr>"
            "<tr><td><b>Ctrl + Z</b></td><td>Undo last edit</td></tr>"
            "<tr><td><b>Ctrl + S</b></td><td>Save changes (atomic in-place)</td></tr>"
            "<tr><td><b>Ctrl + Shift + S</b></td><td>Save As...</td></tr>"
            "<tr><td><b>W or F5</b></td><td>Toggle Slideshow ([ and ] for speed)</td></tr>"
            "<tr><td><b>F or Double-Click</b></td><td>Toggle Fullscreen</td></tr>"
            "<tr><td><b>+ / - or Wheel</b></td><td>Zoom (centered at mouse)</td></tr>"
            "<tr><td><b>0 / 1</b></td><td>Fit to window / 100% 1:1 size</td></tr>"
            "<tr><td><b>Space or Click</b></td><td>Play / Pause video (or pan image)</td></tr>"
            "<tr><td><b>Ctrl + W / Esc</b></td><td>Close window / Exit fullscreen</td></tr>"
            "</table>"
        )
        box = QMessageBox(self)
        box.setWindowTitle("Keyboard Shortcuts — HakuView")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(msg)
        box.exec()

    def show_about_dialog(self) -> None:
        full_license_text = (
            "HakuView — MIT License\n\n"
            "Copyright (c) 2026 Hakan Ünal (@hakanu_) / HakuView\n\n"
            "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
            "of this software and associated documentation files (the \"Software\"), to deal\n"
            "in the Software without restriction, including without limitation the rights\n"
            "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell\n"
            "copies of the Software, and to permit persons to whom the Software is\n"
            "furnished to do so, subject to the following conditions:\n\n"
            "The above copyright notice and this permission notice shall be included in all\n"
            "copies or substantial portions of the Software.\n\n"
            "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n"
            "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
            "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\n"
            "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\n"
            "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\n"
            "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\n"
            "SOFTWARE.\n\n"
            "--------------------------------------------------\n"
            "Third-Party Open-Source Components & Licenses:\n"
            "• Qt6 / PySide6: GNU Lesser General Public License v3 (LGPLv3)\n"
            "• Pillow (PIL): Historical Permission Notice and Disclaimer (HPND / MIT-equivalent)\n"
            "• FFmpeg (via Qt Multimedia): LGPL v2.1+ / GPL\n"
        )

        box = QMessageBox(self)
        box.setWindowTitle(f"About {__app_name__}")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setIconPixmap(QIcon(str(ensure_hakuview_ico())).pixmap(64, 64))
        box.setText(
            f"<h3>🦉 {__app_name__} <span style='font-size: 13px; color: #38bdf8;'>v{__version__}</span></h3>"
            "<p>Fast, lightweight IrfanView-inspired image & video viewer for Ubuntu & Windows.</p>"
            "<p><b>Features:</b> Instant middle-of-folder cycling, border crop, rotate, "
            "1-click compression, hardware-accelerated video playback, and slideshows.</p>"
            "<p><b>GitHub:</b> <a href='https://github.com/hakanu/hakuview' style='color: #38bdf8;'>github.com/hakanu/hakuview</a><br>"
            "<b>Author / Twitter:</b> <a href='https://x.com/hakanu_' style='color: #38bdf8;'>x.com/hakanu_</a><br>"
            "<b>License:</b> Released under the <b>MIT License</b>.</p>"
        )
        box.setDetailedText(full_license_text)
        box.exec()
