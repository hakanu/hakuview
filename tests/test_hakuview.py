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
    assert not win.btn_more.icon().isNull()

    # Verify menu action
    called = []
    win.show_more_options_menu = lambda anchor=None: called.append(True)
    win.btn_more.click()
    assert len(called) == 1


def test_fullscreen_controls_overlay(tmp_path: Path, qtbot) -> None:
    _create_sample_image(tmp_path / "img1.png")
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    win.show()

    assert hasattr(win, "fs_overlay")
    assert hasattr(win.fs_overlay, "btn_restore")
    assert hasattr(win.fs_overlay, "btn_close")
    assert hasattr(win.fs_overlay, "btn_more")
    assert not win.fs_overlay.isVisible()

    # Enter fullscreen -> overlay becomes visible
    win.toggle_fullscreen()
    assert win.isFullScreen()
    assert win.fs_overlay.isVisible()

    # Click restore button -> restores normal window and hides overlay
    win.fs_overlay.btn_restore.click()
    assert not win.isFullScreen()
    assert not win.fs_overlay.isVisible()


def test_show_more_options_menu_execution(tmp_path: Path, qtbot) -> None:
    _create_sample_image(tmp_path / "img1.png")
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    win.show()

    # Image mode (exec_menu=False avoids modal block)
    menu_img = win.show_more_options_menu(exec_menu=False)
    assert menu_img is not None
    action_texts = [a.text() for a in menu_img.actions()]
    assert any("Border Crop" in t for t in action_texts)
    assert any("Media Inspector" in t for t in action_texts)

    # Video mode
    win.current_is_video = True
    menu_vid = win.show_more_options_menu(exec_menu=False)
    assert menu_vid is not None
    vid_actions = [a.text() for a in menu_vid.actions()]
    assert any("Trim Video" in t for t in vid_actions)


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


def test_color_scaling_adjustments_and_save_new_image(tmp_path: Path, qtbot) -> None:
    from PySide6.QtGui import QColor

    img_path = tmp_path / "original_photo.png"
    _create_sample_image(img_path, width=100, height=80, color=QColor(100, 100, 100))
    orig_bytes = img_path.read_bytes()

    win = HakuViewWindow(initial_path=img_path)
    qtbot.addWidget(win)
    win.show()

    # Toolbar has Color button, no random button
    assert hasattr(win, "btn_color")
    assert not hasattr(win, "btn_random")
    assert not win.color_bar.isVisible()

    # Toggle color bar
    win.toggle_color_bar()
    assert win.color_bar.isVisible()
    assert win.btn_color.isChecked()

    # Adjust brightness and contrast
    win.color_bar.slider_brightness.setValue(25)
    win.color_bar.slider_contrast.setValue(15)
    assert win.color_bar.brightness == 25
    assert win.color_bar.contrast == 15
    assert win.color_bar.lbl_b_val.text() == "+25"
    assert win.color_bar.lbl_c_val.text() == "+15"

    # Saving must save as a NEW image and not overwrite original
    new_path = win.save_adjusted_image()
    assert new_path is not None
    assert new_path.exists()
    assert new_path != img_path
    assert "color" in new_path.name
    # Original is untouched
    assert img_path.read_bytes() == orig_bytes

    # Reset returns to neutral 0
    win.reset_color_adjustments()
    assert win.color_bar.brightness == 0
    assert win.color_bar.contrast == 0
    assert win.color_bar.lbl_b_val.text() == "0"
    assert win.color_bar.lbl_c_val.text() == "0"


def test_random_moved_to_more_options_menu(tmp_path: Path, qtbot) -> None:
    _create_sample_image(tmp_path / "img1.png")
    _create_sample_image(tmp_path / "img2.png")
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    win.show()

    # Random is under 3 dots menu
    menu = win.show_more_options_menu(exec_menu=False)
    actions = [a.text() for a in menu.actions()]
    assert any("Random File" in t for t in actions)
    assert any("Color Scaling" in t for t in actions)


def test_video_color_scaling_and_frame_save(tmp_path: Path, qtbot) -> None:
    vid_path = tmp_path / "clip.mp4"
    vid_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")

    win = HakuViewWindow(initial_path=vid_path)
    qtbot.addWidget(win)
    win.show()

    assert win.current_is_video
    assert not win.video_view.color_adjusting

    # Adjusting color routes video through the LUT-processing view; resetting restores the GPU path.
    win.color_bar.slider_brightness.setValue(30)
    win.color_bar.slider_contrast.setValue(20)
    assert win.video_view.color_adjusting
    assert win.video_view.adjusted_view._lut is not None
    win.reset_color_adjustments()
    assert not win.video_view.color_adjusting
    win.color_bar.slider_brightness.setValue(30)

    # Mock frame grabber to test save_adjusted_image on video
    sample_frame = QImage(64, 64, QImage.Format.Format_RGB32)
    sample_frame.fill(QColor(100, 100, 100))
    win.video_view.grab_current_frame = lambda: sample_frame

    saved_frame_path = win.save_adjusted_image()
    assert saved_frame_path is not None
    assert saved_frame_path.exists()
    assert "frame_" in saved_frame_path.name
    assert "color" in saved_frame_path.name


def _show_active(win: HakuViewWindow, qtbot) -> None:
    # Window-wide QAction shortcuts only fire for the active window.
    win.show()
    win.activateWindow()
    qtbot.waitUntil(win.isActiveWindow)


def _video_window(tmp_path: Path, qtbot) -> HakuViewWindow:
    vid_path = tmp_path / "clip.mp4"
    vid_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    win = HakuViewWindow(initial_path=vid_path)
    qtbot.addWidget(win)
    win._prompt_unsaved = False
    _show_active(win, qtbot)
    return win


def test_zoom_shortcuts_step_instead_of_collapsing(tmp_path: Path, qtbot) -> None:
    from PySide6.QtTest import QTest

    _create_sample_image(tmp_path / "img1.png", width=400, height=300)
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    _show_active(win, qtbot)
    start = win.canvas.zoom_scale

    # Regression: triggered(bool) used to be passed as zoom_in(factor=False) -> 2% zoom, and zoom_out crashed.
    QTest.keyClick(win, Qt.Key.Key_Plus)
    assert win.canvas.zoom_scale == pytest.approx(start * 1.25)
    QTest.keyClick(win, Qt.Key.Key_Minus)
    assert win.canvas.zoom_scale == pytest.approx(start)


def test_video_trim_keys_are_not_stolen_by_global_shortcuts(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtTest import QTest

    win = _video_window(tmp_path, qtbot)
    calls = []
    monkeypatch.setattr(win, "open_file_dialog", lambda: calls.append("open"))
    monkeypatch.setattr(win.video_view, "set_trim_in", lambda *a: calls.append("in"))
    monkeypatch.setattr(win.video_view, "set_trim_out", lambda *a: calls.append("out"))
    interval = win.spin_interval.value()

    for key in (Qt.Key.Key_I, Qt.Key.Key_O, Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight):
        QTest.keyClick(win, key)

    assert calls == ["in", "out", "in", "out"]
    assert win.spin_interval.value() == interval


def test_bracket_keys_change_slideshow_speed_for_images(tmp_path: Path, qtbot) -> None:
    from PySide6.QtTest import QTest

    _create_sample_image(tmp_path / "img1.png")
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    _show_active(win, qtbot)

    QTest.keyClick(win, Qt.Key.Key_BracketLeft)
    assert win.spin_interval.value() == pytest.approx(2.5)
    QTest.keyClick(win, Qt.Key.Key_BracketRight)
    assert win.spin_interval.value() == pytest.approx(3.0)


def test_shortcuts_dialog_lists_every_documented_key(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    win = HakuViewWindow()
    qtbot.addWidget(win)
    shown = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: shown.append(self.text()))
    win.show_shortcuts_dialog()

    assert len(shown) == 1
    for label in ("Ctrl+Shift+S", "[ / ]", "← / →", "Esc"):
        assert label in shown[0]


def test_media_info_reads_exif_sub_ifd(tmp_path: Path) -> None:
    from PIL import ExifTags, Image
    from hakuview.info_dialog import extract_media_info

    exif = Image.Exif()
    exif[0x010F] = "Canon"  # Make (IFD0)
    exif.get_ifd(ExifTags.IFD.Exif)[0x8827] = 400  # ISOSpeedRatings (Exif sub-IFD)
    img_path = tmp_path / "photo.jpg"
    Image.new("RGB", (16, 16)).save(img_path, exif=exif)

    info = extract_media_info(img_path)
    assert info["exif"]["Make"] == "Canon"
    assert info["exif"]["ISOSpeedRatings"] == "400"


def test_save_refuses_to_flatten_animated_gif(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PIL import Image
    from PySide6.QtWidgets import QMessageBox

    gif_path = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (20, 20), color) for color in ("red", "blue", "green")]
    frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    original = gif_path.read_bytes()

    win = HakuViewWindow(initial_path=gif_path)
    qtbot.addWidget(win)
    win._prompt_unsaved = False
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))

    win.rotate_right()
    assert not win.save_current()
    assert len(warnings) == 1
    assert gif_path.read_bytes() == original


def test_invalid_media_advances_slideshow_once_and_never_loops(qtbot) -> None:
    from PySide6.QtMultimedia import QMediaPlayer
    from hakuview.video_view import VideoPlayerWidget

    widget = VideoPlayerWidget()
    qtbot.addWidget(widget)
    finished = []
    widget.videoFinished.connect(lambda: finished.append(True))

    # Outside slideshow: an unplayable file must not be replayed.
    plays = []
    widget.player.play = lambda: plays.append(True)
    widget._on_media_status_changed(QMediaPlayer.MediaStatus.InvalidMedia)
    assert plays == [] and finished == []

    # In slideshow: InvalidMedia + errorOccurred for the same file advance only once.
    widget._in_slideshow = True
    widget._on_media_status_changed(QMediaPlayer.MediaStatus.InvalidMedia)
    widget._on_player_error(QMediaPlayer.Error.FormatError, "bad file")
    assert finished == [True]


def test_only_one_ffmpeg_job_runs_and_shutdown_cancels_it(tmp_path: Path, qtbot, monkeypatch) -> None:
    import shutil
    from PySide6.QtWidgets import QMessageBox
    from hakuview.video_view import VideoPlayerWidget, _TrimWorker

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")

    widget = VideoPlayerWidget()
    qtbot.addWidget(widget)
    busy = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: busy.append(a))

    # An endless synthetic input keeps ffmpeg busy until it is cancelled.
    class _EndlessJob(_TrimWorker):
        def _attempts(self, ffmpeg):
            return [([ffmpeg, "-y", "-re", "-f", "lavfi", "-i", "testsrc", str(self.dest_path)], 0)]

    dest = tmp_path / "out.mp4"
    assert widget._start_job(_EndlessJob(dest, dest, 0, 1), lambda p: None, lambda e: None)
    assert widget.is_job_running()
    assert not widget._start_job(_EndlessJob(dest, dest, 0, 1), lambda p: None, lambda e: None)
    assert len(busy) == 1

    thread = widget._job_thread
    widget.shutdown()
    assert not widget.is_job_running()
    assert thread.isFinished()
    assert not dest.exists()


def _whatsapp_like_jpeg(path: Path) -> None:
    # Already heavily compressed (q75, 4:2:0), like images received over messaging apps.
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (320, 240), "white")
    draw = ImageDraw.Draw(img)
    for i in range(0, 320, 8):
        draw.line([(i, 0), (320 - i, 240)], fill=(i % 255, 90, 200 - i % 200), width=3)
    img.save(path, quality=75, subsampling=2)


def test_lossless_jpeg_without_jpegtran_refuses_instead_of_inflating(tmp_path: Path, monkeypatch) -> None:
    from hakuview import image_ops

    src = tmp_path / "chat photo.jpeg"
    _whatsapp_like_jpeg(src)
    monkeypatch.setattr(image_ops.shutil, "which", lambda name: None)
    doc = ImageDocument(src, load_qimage_fast(src))

    with pytest.raises(image_ops.CompressionError, match="jpegtran"):
        image_ops.compress_image_as_variant(doc, "lossless")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["chat photo.jpeg"]


def test_compression_never_keeps_a_larger_variant(tmp_path: Path) -> None:
    from hakuview import image_ops

    src = tmp_path / "photo.jpeg"
    _whatsapp_like_jpeg(src)
    doc = ImageDocument(src, load_qimage_fast(src))

    # Re-encoding a q75 JPEG at q85 can only grow it.
    with pytest.raises(image_ops.CompressionError, match="smaller"):
        image_ops.compress_image_as_variant(doc, "smart")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photo.jpeg"]


def test_edited_jpeg_lossless_variant_keeps_source_quality(tmp_path: Path) -> None:
    from PIL import Image, JpegImagePlugin
    from hakuview import image_ops

    src = tmp_path / "photo.jpeg"
    _whatsapp_like_jpeg(src)
    doc = ImageDocument(src, load_qimage_fast(src))
    doc.crop(QRect(0, 0, 300, 220))

    out, orig_bytes, new_bytes = image_ops.compress_image_as_variant(doc, "lossless")
    with Image.open(src) as a, Image.open(out) as b:
        assert b.quantization == a.quantization
        assert JpegImagePlugin.get_sampling(b) == JpegImagePlugin.get_sampling(a)
    assert new_bytes < orig_bytes


def test_video_color_adjustment_changes_decoded_frames(tmp_path: Path, qtbot) -> None:
    import shutil
    import subprocess
    from hakuview.video_view import VideoPlayerWidget

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    clip = tmp_path / "grey.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=0x404040:size=160x120:rate=15:duration=3",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )

    widget = VideoPlayerWidget()
    qtbot.addWidget(widget)
    widget.resize(320, 240)
    widget.show()
    widget.load_video(clip)
    try:
        qtbot.waitUntil(lambda: widget.video_widget.videoSink().videoFrame().isValid(), timeout=5000)
    except Exception:
        pytest.skip("no video decoding backend available")

    widget.set_color_scaling(60, 0)
    qtbot.waitUntil(lambda: widget.adjusted_view._image is not None, timeout=5000)
    before = widget.adjusted_view._source.pixelColor(5, 5).red()
    after = widget.adjusted_view._image.pixelColor(5, 5).red()
    assert after > before + 50


def test_color_export_bakes_preview_lut_into_new_video(tmp_path: Path, qtbot) -> None:
    import shutil
    import subprocess
    from hakuview.image_ops import color_lut
    from hakuview.video_view import _ColorExportWorker

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    src = tmp_path / "clip.mkv"
    subprocess.run(
        [ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=1",
         "-pix_fmt", "yuv420p", str(src)],
        check=True,
    )

    worker = _ColorExportWorker(src, tmp_path / "clip_color.mp4", brightness=-30, contrast=50)
    done = []
    worker.finished.connect(done.append)
    worker.run()
    assert done == [tmp_path / "clip_color.mp4"]

    # The ffmpeg expression must reproduce the preview LUT exactly (checked on raw RGB, no codec noise).
    def first_frame_rgb(extra_filter: str) -> bytes:
        vf = ["-vf", extra_filter] if extra_filter else []
        return subprocess.run(
            [ffmpeg, "-loglevel", "error", "-i", str(src), "-frames:v", "1", *vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True,
        ).stdout

    expr = worker.lut_expression()
    raw = first_frame_rgb("")
    filtered = first_frame_rgb(f"format=rgb24,lutrgb=r={expr}:g={expr}:b={expr}")
    lut = color_lut(-30, 50)
    assert bytes(lut[x] for x in raw) == filtered


def test_color_bar_offers_video_export_only_for_videos(tmp_path: Path, qtbot, monkeypatch) -> None:
    win = _video_window(tmp_path, qtbot)
    win.toggle_color_bar()
    assert win.color_bar.btn_save_video.isVisible()
    assert win.color_bar.btn_save.text() == "🖼 Save Frame"

    started = []
    monkeypatch.setattr(win.video_view, "export_color_adjusted", lambda b, c: started.append((b, c)) or tmp_path / "x.mp4")
    win.export_adjusted_video()  # neutral sliders -> nothing to export
    assert started == []
    win.color_bar.slider_brightness.setValue(20)
    win.color_bar.btn_save_video.click()
    assert started == [(20, 0)]
    assert not win.color_bar.btn_save_video.isEnabled()  # shows "Exporting..." until the job reports back
    win._on_color_export_done(tmp_path / "x.mp4")
    assert win.color_bar.btn_save_video.isEnabled()

    _create_sample_image(tmp_path / "zz.png")
    win.open_path(tmp_path / "zz.png")
    assert not win.color_bar.btn_save_video.isVisible()
    assert win.color_bar.btn_save.text() == "💾 Save as New Image"


def test_more_button_stays_visible_when_toolbar_overflows(tmp_path: Path, qtbot) -> None:
    _create_sample_image(tmp_path / "img1.png")
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    win.resize(700, 500)
    win.show()
    qtbot.waitExposed(win)
    assert win.toolbar.sizeHint().width() > win.width()  # really overflowing
    assert win.btn_more.isVisible()


def test_fullscreen_hides_bars_and_shows_controls(tmp_path: Path, qtbot) -> None:
    _create_sample_image(tmp_path / "img1.png")
    win = HakuViewWindow(initial_path=tmp_path / "img1.png")
    qtbot.addWidget(win)
    win.show()
    win.toggle_fullscreen()
    assert not win.top_bar.isVisible() and not win.status.isVisible()
    assert win.fs_overlay.isVisible()
    # Styled backing must actually paint, or the light icons vanish over bright images.
    assert win.fs_overlay.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    win.toggle_fullscreen()
    assert win.top_bar.isVisible() and not win.fs_overlay.isVisible()


def test_version_comes_from_package_metadata_not_a_literal() -> None:
    import hakuview

    assert hakuview.__version__ not in ("", "0+unknown")
    assert hakuview.__version__ in hakuview.display_version()
