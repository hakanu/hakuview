"""Tests for HakuView folder navigation, image operations, and main window workflow."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage
import pytest

from hakuview.folder_scanner import FolderNavigator, natural_sort_key
from hakuview.image_ops import (
    ImageDocument,
    crop_qimage,
    load_qimage_fast,
    rotate_qimage,
    save_qimage_atomic,
)
from hakuview.window import HakuViewWindow

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _create_sample_image(path: Path, width: int = 120, height: int = 80, color: QColor = QColor(40, 120, 220)) -> None:
    img = QImage(width, height, QImage.Format.Format_RGB32)
    img.fill(color)
    save_qimage_atomic(img, path)


def test_natural_sort_and_open_from_middle(tmp_path: Path) -> None:
    names = ["photo1.jpg", "photo2.jpg", "photo10.jpg", "clip.mp4", "photo3.png"]
    for name in names:
        p = tmp_path / name
        if name.endswith(".mp4"):
            p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
        else:
            _create_sample_image(p)

    nav = FolderNavigator()
    # Open photo3.png from the middle of the folder
    current = nav.load_target(tmp_path / "photo3.png")
    assert current == tmp_path / "photo3.png"
    assert [f.name for f in nav.files] == [
        "clip.mp4",
        "photo1.jpg",
        "photo2.jpg",
        "photo3.png",
        "photo10.jpg",
    ]
    assert nav.index == 3

    # Cycle forward -> photo10.jpg -> wraps to clip.mp4
    assert nav.next().name == "photo10.jpg"
    assert nav.next().name == "clip.mp4"
    # Cycle backward -> photo10.jpg
    assert nav.prev().name == "photo10.jpg"


def test_random_selection_from_folder(tmp_path: Path) -> None:
    for i in range(5):
        _create_sample_image(tmp_path / f"img_{i}.png")

    nav = FolderNavigator()
    chosen = nav.load_target(tmp_path, pick_random=True)
    assert chosen in nav.files
    other = nav.random_file()
    assert other in nav.files
    assert other != chosen


def test_image_document_rotate_crop_undo_and_save(tmp_path: Path) -> None:
    img_path = tmp_path / "sample.png"
    _create_sample_image(img_path, width=200, height=100)

    loaded = load_qimage_fast(img_path)
    assert loaded is not None
    assert (loaded.width(), loaded.height()) == (200, 100)

    doc = ImageDocument(img_path, loaded)
    assert not doc.modified

    # Rotate right -> 100x200
    doc.rotate_right()
    assert doc.modified
    assert (doc.current.width(), doc.current.height()) == (100, 200)

    # Crop to 50x60
    assert doc.crop(QRect(10, 20, 50, 60))
    assert (doc.current.width(), doc.current.height()) == (50, 60)

    # Undo crop -> back to 100x200
    assert doc.undo()
    assert (doc.current.width(), doc.current.height()) == (100, 200)
    assert doc.modified

    # Save atomically and verify on disk
    save_qimage_atomic(doc.current, img_path)
    doc.mark_saved()
    assert not doc.modified

    reloaded = load_qimage_fast(img_path)
    assert reloaded is not None
    assert (reloaded.width(), reloaded.height()) == (100, 200)


def test_window_cycle_rotate_crop_and_slideshow(tmp_path: Path, qtbot) -> None:
    for i in range(1, 4):
        _create_sample_image(tmp_path / f"img_{i}.png", width=160, height=90)

    # Open the middle image (img_2.png)
    win = HakuViewWindow(initial_path=tmp_path / "img_2.png")
    qtbot.addWidget(win)
    win._prompt_unsaved = False

    assert win.navigator.current_file == tmp_path / "img_2.png"
    assert win.navigator.index == 1

    # Cycle Right -> img_3.png
    win.next_item()
    assert win.navigator.current_file == tmp_path / "img_3.png"

    # Cycle Left twice -> img_1.png
    win.prev_item()
    win.prev_item()
    assert win.navigator.current_file == tmp_path / "img_1.png"

    # Rotate Right -> 90x160
    win.rotate_right()
    assert win.document is not None
    assert (win.document.current.width(), win.document.current.height()) == (90, 160)
    assert win.document.modified

    # Press C (trigger_crop_action) -> activates Border Crop Mode at full image borders (0, 0, 90, 160)
    assert win.canvas.selection.isNull()
    assert win.trigger_crop_action()
    assert win.canvas.selection == QRect(0, 0, 90, 160)

    # Pull borders inward to (5, 10, 40, 50) and press C again to commit crop
    win.canvas._selection = QRect(5, 10, 40, 50)
    assert win.trigger_crop_action()
    assert (win.document.current.width(), win.document.current.height()) == (40, 50)

    # Save in place
    assert win.save_current()
    assert not win.document.modified

    # 1-Click Lossless Compression as Variant (saves img_1_lossless.png in same folder)
    variant1 = win.compress_current_variant()
    assert variant1 is not None
    assert variant1.exists()
    assert variant1.name == "img_1_lossless.png"
    assert variant1 in win.navigator.files

    # Switch preset to Lossless WebP and compress again -> img_1_lossless.webp
    win.combo_compress.setCurrentIndex(1)  # Lossless WebP
    variant2 = win.compress_current_variant()
    assert variant2 is not None
    assert variant2.exists()
    assert variant2.name == "img_1_lossless.webp"

    # Toggle slideshow on and off (next file after img_1.png is now img_1_lossless.png)
    win.set_slideshow(True)
    assert win.slideshow_active
    win._on_slideshow_tick()
    assert win.navigator.current_file == tmp_path / "img_1_lossless.png"
    win.set_slideshow(False)
    assert not win.slideshow_active

    # Verify Ctrl+W shortcut closes the window
    close_actions = [a for a in win.actions() if a.shortcut().toString() == "Ctrl+W"]
    assert len(close_actions) == 1
    win.show()
    assert win.isVisible()
    close_actions[0].trigger()
    assert not win.isVisible()


def test_clickable_video_widget(qtbot) -> None:
    from hakuview.video_view import VideoPlayerWidget

    widget = VideoPlayerWidget()
    qtbot.addWidget(widget)

    # Click video widget calls toggle_play_pause
    clicks = []
    widget.toggle_play_pause = lambda: clicks.append(True)
    widget.video_widget.clicked.emit()
    assert len(clicks) == 1

    # Double click emits doubleClicked
    double_clicks = []
    widget.doubleClicked.connect(lambda: double_clicks.append(True))
    widget.video_widget.doubleClicked.emit()
    assert len(double_clicks) == 1


def test_slideshow_cycles_through_videos(tmp_path: Path, qtbot) -> None:
    vid_path = tmp_path / "vid1.mp4"
    vid_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    _create_sample_image(tmp_path / "img2.png")

    win = HakuViewWindow(initial_path=vid_path)
    qtbot.addWidget(win)
    win._prompt_unsaved = False

    assert win.current_is_video
    assert win.navigator.current_file == vid_path

    # Start slideshow while viewing video (F5)
    win.set_slideshow(True)
    assert win.slideshow_active
    assert win.slideshow_timer.isActive()

    # Slideshow tick advances to next file
    win._on_slideshow_tick()
    assert win.navigator.current_file == tmp_path / "img2.png"
    assert not win.current_is_video

    # Advancing back to video keeps slideshow timer running
    win._on_slideshow_tick()
    assert win.navigator.current_file == vid_path
    assert win.current_is_video
    assert win.slideshow_timer.isActive()

    # Video completion signal also advances immediately
    win._on_video_finished_in_slideshow()
    assert win.navigator.current_file == tmp_path / "img2.png"


def test_more_options_menu_and_toolbar_extension(tmp_path: Path, qtbot) -> None:
    _create_sample_image(tmp_path / "img1.png")
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    win.show()

    assert hasattr(win, "btn_more")
    assert win.btn_more.text() == "⋮"

    # Verify menu action
    called = []
    win.show_more_options_menu = lambda anchor=None: called.append(True)
    win.btn_more.click()
    assert len(called) == 1

    # Verify extension hook
    win._hook_toolbar_extension()


def test_ensure_hakuview_ico() -> None:
    from hakuview.installer import ensure_hakuview_ico
    from PySide6.QtGui import QIcon

    ico_path = ensure_hakuview_ico()
    assert ico_path.exists()
    assert ico_path.stat().st_size > 0
    icon = QIcon(str(ico_path))
    assert not icon.isNull()


def test_combobox_popup_dark_theme(tmp_path: Path, qtbot) -> None:
    _create_sample_image(tmp_path / "img1.png")
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    win.show()

    # The compression combo box popup view must have a dark background palette, not white
    view = win.combo_compress.view()
    bg_color = view.palette().base().color()
    # Lightness is 0-255; dark background should be < 100, not 255 (white)
    assert bg_color.lightness() < 100


def test_video_trim_markers_and_ui(qtbot) -> None:
    from hakuview.video_view import VideoPlayerWidget

    player_widget = VideoPlayerWidget()
    qtbot.addWidget(player_widget)

    assert not player_widget.has_trim_points()
    assert not player_widget.btn_trim.isEnabled()

    # Simulate video duration of 10 seconds (10000ms)
    player_widget.seek_slider.setRange(0, 10000)

    # Set In-point at 2000ms
    player_widget.set_trim_in(2000)
    assert player_widget.has_trim_points()
    assert player_widget.trim_in_ms == 2000
    assert player_widget.seek_slider.trim_in_pos == 2000
    assert "[00:02" in player_widget.btn_set_in.text()

    # Set Out-point at 7500ms
    player_widget.set_trim_out(7500)
    assert player_widget.trim_out_ms == 7500
    assert player_widget.seek_slider.trim_out_pos == 7500
    assert "00:07]" in player_widget.btn_set_out.text()
    assert player_widget.btn_trim.isEnabled()
    assert "00:05" in player_widget.btn_trim.text()

    # Clear trim points
    player_widget.clear_trim()
    assert not player_widget.has_trim_points()
    assert player_widget.trim_in_ms is None
    assert player_widget.trim_out_ms is None
    assert player_widget.seek_slider.trim_in_pos is None
    assert player_widget.seek_slider.trim_out_pos is None
    assert not player_widget.btn_trim.isEnabled()


def test_video_trim_worker(tmp_path: Path) -> None:
    import shutil
    import subprocess
    from hakuview.video_view import _TrimWorker

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available for trim worker test")

    # Generate a lightweight 3s synthetic test video
    src_vid = tmp_path / "sample_input.mp4"
    dest_vid = tmp_path / "sample_input_trim.mp4"
    res = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=160x120:rate=15",
            str(src_vid),
        ],
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0
    assert src_vid.exists()

    completed_paths = []
    worker = _TrimWorker(src_vid, dest_vid, start_ms=1000, end_ms=2000)
    worker.finished.connect(completed_paths.append)
    worker.run()

    assert len(completed_paths) == 1
    assert completed_paths[0] == dest_vid
    assert dest_vid.exists()
    assert dest_vid.stat().st_size > 500


def test_timeline_arbitrary_click_moves_position(qtbot) -> None:
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QMouseEvent
    from hakuview.video_view import VideoPlayerWidget

    player_widget = VideoPlayerWidget()
    qtbot.addWidget(player_widget)
    player_widget.show()

    slider = player_widget.seek_slider
    slider.resize(400, 24)
    slider.setRange(0, 100000)  # 100 seconds
    slider.setValue(0)

    # Click at 75% along the track (~300px)
    click_pos = QPointF(300, 12)
    evt = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        click_pos,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    slider.mousePressEvent(evt)

    # Slider value must immediately jump to ~75,000, not stay at 0 or step by a small page
    assert slider.value() > 70000
    assert slider.value() < 80000


def test_media_info_dialog_and_extraction(tmp_path: Path, qtbot) -> None:
    from hakuview.info_dialog import MediaInfoDialog, extract_media_info
    from PySide6.QtWidgets import QApplication

    img_path = tmp_path / "test_photo.png"
    _create_sample_image(img_path, width=200, height=100)

    info = extract_media_info(img_path)
    assert info["file_name"] == "test_photo.png"
    assert info["width"] == 200
    assert info["height"] == 100
    assert info["is_video"] is False
    assert "compression_ratio" in info
    assert "bits_per_pixel" in info

    # Dialog instantiation and clipboard report test
    dlg = MediaInfoDialog(img_path)
    qtbot.addWidget(dlg)
    dlg.show()

    assert dlg.btn_copy is not None
    dlg._copy_to_clipboard()
    copied_text = QApplication.clipboard().text()
    assert "# HakuView Media Information" in copied_text
    assert "test_photo.png" in copied_text
    assert "200 × 100" in copied_text

    # Window integration test
    win = HakuViewWindow(initial_path=img_path)
    qtbot.addWidget(win)
    win.show()
    assert hasattr(win, "btn_info")
    assert win.btn_info.isEnabled()
