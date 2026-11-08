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


def _all_menu_actions(menu):
    """Actions of a menu including those inside its submenus."""
    for act in menu.actions():
        yield act
        if act.menu() is not None:
            yield from _all_menu_actions(act.menu())


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
    win.set_compression_mode("lossless_webp")
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
    action_texts = [a.text() for a in _all_menu_actions(menu_img)]
    assert any("Border Crop" in t for t in action_texts)
    assert any("Media Inspector" in t for t in action_texts)

    # Video mode
    win.current_is_video = True
    menu_vid = win.show_more_options_menu(exec_menu=False)
    assert menu_vid is not None
    vid_actions = [a.text() for a in _all_menu_actions(menu_vid)]
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

    # Combo box popups (e.g. in Settings) must follow the dark theme, not the white system default
    from hakuview.settings_dialog import SettingsDialog

    dlg = SettingsDialog(win.app_settings, win.update_settings, win)
    qtbot.addWidget(dlg)
    dlg.show()
    view = dlg.combo_sort.view()
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
    actions = [a.text() for a in _all_menu_actions(menu)]
    assert any("Random File" in t for t in actions)
    assert any("Brightness && Contrast" in t for t in actions)
    assert len(menu.actions()) <= 20  # top level stays short; the rest is grouped in submenus


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


def test_version_parsing_and_comparison() -> None:
    from hakuview.updater import is_newer, latest_version, parse_version

    assert parse_version("v1.2.3") < parse_version("v1.10.0")
    assert parse_version("0.2.1.dev4+gabc") < parse_version("0.2.1")  # dev builds come before the release
    assert latest_version(["v0.1.0", "v0.10.0", "v0.9.9", "nightly", "v1.0.0.dev3"]) == "0.10.0"
    assert latest_version([]) is None

    assert is_newer("0.3.0", current="0.2.0")
    assert is_newer("0.2.1", current="0.2.1.dev7+gdeadbee")
    assert not is_newer("0.2.0", current="0.2.0")
    assert not is_newer("0.2.0", current="0.2.1.dev1+gabc")
    assert not is_newer("9.9.9", current="0.0.0")  # unknown local version: never nag


def test_startup_update_check_is_throttled_and_can_be_disabled() -> None:
    from hakuview.updater import STARTUP_CHECK_INTERVAL_SEC, UpdateSettings

    settings = UpdateSettings()
    t0 = 1_800_000_000.0
    assert settings.check_on_startup and settings.startup_check_due(now=t0)  # never checked yet
    settings.mark_checked(now=t0)
    assert not settings.startup_check_due(now=t0 + 60)
    assert settings.startup_check_due(now=t0 + STARTUP_CHECK_INTERVAL_SEC)
    settings.check_on_startup = False
    assert not UpdateSettings().startup_check_due(now=10**10)  # persisted


def _window_with_fake_update_source(qtbot, monkeypatch, latest: str = "", error: str = ""):
    win = HakuViewWindow()
    qtbot.addWidget(win)

    def fake_fetch():
        if error:
            raise OSError(error)
        return latest

    monkeypatch.setattr(win.update_checker, "_fetch", fake_fetch)
    return win


def test_update_prompt_offers_update_and_remembers_skip(qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    win = _window_with_fake_update_source(qtbot, monkeypatch, latest="99.0.0")
    prompts = []

    def fake_exec(box):
        prompts.append(box.text())
        skip = next(b for b in box.buttons() if b.text() == "Skip This Version")
        box.checkBox().setChecked(False)  # user also turns off the startup check
        skip.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    win.check_for_updates(manual=False)
    qtbot.waitUntil(lambda: bool(prompts), timeout=3000)
    assert "99.0.0" in prompts[0]
    assert win.update_settings.skipped_version == "99.0.0"
    assert not win.update_settings.check_on_startup

    # A skipped version doesn't pop up again on automatic checks...
    win.check_for_updates(manual=False)
    qtbot.waitUntil(lambda: not win.update_checker.running, timeout=3000)
    assert len(prompts) == 1
    # ...but an explicit "Check for Updates..." still shows it.
    win.check_for_updates(manual=True)
    qtbot.waitUntil(lambda: len(prompts) == 2, timeout=3000)


def test_manual_update_check_reports_up_to_date_and_offline(qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text: shown.append(text))

    win = _window_with_fake_update_source(qtbot, monkeypatch, latest="0.0.1")
    win.check_for_updates(manual=True)
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)
    assert "up to date" in shown[-1]

    win = _window_with_fake_update_source(qtbot, monkeypatch, error="Network is unreachable")
    win.check_for_updates(manual=True)
    qtbot.waitUntil(lambda: len(shown) == 2, timeout=3000)
    assert "Network is unreachable" in shown[-1]

    # No tags on GitHub at all is not the same as being up to date.
    win = _window_with_fake_update_source(qtbot, monkeypatch, latest="")
    win.check_for_updates(manual=True)
    qtbot.waitUntil(lambda: len(shown) == 3, timeout=3000)
    assert "No releases" in shown[-1]
    shown.pop()

    # Automatic checks stay silent when offline.
    win.check_for_updates(manual=False)
    qtbot.waitUntil(lambda: not win.update_checker.running, timeout=3000)
    assert len(shown) == 2


def test_update_menu_entries_and_startup_toggle(qtbot) -> None:
    win = HakuViewWindow()
    qtbot.addWidget(win)
    menu = win.show_more_options_menu(exec_menu=False)
    actions = {a.text(): a for a in _all_menu_actions(menu)}
    assert "🔄 Check for Updates..." in actions
    toggle = actions["Check for Updates on Startup"]
    assert toggle.isCheckable() and toggle.isChecked()
    toggle.trigger()
    assert not win.update_settings.check_on_startup


def test_windows_never_check_for_updates_unless_asked(qtbot, monkeypatch) -> None:
    from hakuview import updater

    calls = []
    monkeypatch.setattr(updater, "fetch_latest_version", lambda *a, **k: calls.append(1) or "")
    win = HakuViewWindow()
    qtbot.addWidget(win)
    qtbot.wait(100)
    assert calls == [] and not win.update_checker.running


def test_light_and_dark_themes_switch_palette_and_stylesheet(tmp_path: Path, qtbot) -> None:
    from hakuview import theme

    win = HakuViewWindow()
    qtbot.addWidget(win)
    assert theme.is_dark()  # default keeps HakuView's classic dark look
    dark_qss = win.styleSheet()

    win.app_settings.theme = "light"
    win.apply_settings()
    assert not theme.is_dark()
    assert win.styleSheet() != dark_qss and theme.LIGHT["panel"] in win.styleSheet()
    assert QColor(theme.PALETTE["canvas"]).lightness() > 200

    # Dark-only stylesheets (Media Inspector) are auto-converted: dark bg -> light, light text -> dark.
    assert QColor(theme.adapt("#171b23")).lightness() > 200
    assert QColor(theme.adapt("#f1f5f9")).lightness() < 60
    win.app_settings.theme = "dark"
    win.apply_settings()
    assert theme.adapt("#171b23") == "#171b23"


def test_settings_dialog_saves_and_applies_preferences(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog
    from hakuview.settings import AppSettings
    from hakuview.settings_dialog import SettingsDialog

    for name, size in (("a.png", 50), ("b.png", 400), ("c.png", 120)):
        _create_sample_image(tmp_path / name, width=size, height=size)
    win = HakuViewWindow(initial_path=tmp_path / "a.png")
    qtbot.addWidget(win)

    def fill_and_accept(dlg):
        dlg.combo_sort.setCurrentIndex(dlg.combo_sort.findData("size"))
        dlg.combo_wheel.setCurrentIndex(dlg.combo_wheel.findData("navigate"))
        dlg.combo_compress.setCurrentIndex(dlg.combo_compress.findData("webp_q80"))
        dlg.spin_interval.setValue(7.5)
        dlg.chk_loop.setChecked(False)
        dlg.chk_updates.setChecked(False)
        dlg.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", fill_and_accept)
    win.show_settings_dialog()

    assert [p.name for p in win.navigator.files] == ["b.png", "c.png", "a.png"]  # largest first
    assert win.navigator.current_file.name == "a.png"  # selection kept
    assert win.canvas.wheel_navigates
    assert win.compress_mode == "webp_q80"
    assert win.spin_interval.value() == 7.5
    assert not win.video_view.loop
    assert not win.update_settings.check_on_startup

    fresh = AppSettings()  # persisted
    assert (fresh.sort_mode, fresh.wheel_action, fresh.compression_mode) == ("size", "navigate", "webp_q80")


def test_session_restores_geometry_last_folder_and_volume(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QFileDialog

    _create_sample_image(tmp_path / "a.png")
    win = HakuViewWindow(initial_path=tmp_path / "a.png")
    qtbot.addWidget(win)
    win.show()
    win.resize(700, 480)  # must fit the 800x600 offscreen test screen, or Qt clamps it
    saved_size = win.size().toTuple()
    win.video_view.set_volume(35)
    win.close()

    again = HakuViewWindow()
    qtbot.addWidget(again)
    assert again.size().toTuple() == saved_size
    assert again.video_view.volume == 35
    seen = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda parent, title, start, filt: seen.append(start) or ("", ""))
    again.open_file_dialog()
    assert seen == [str(tmp_path)]

    # Explicit CLI --interval beats the saved slideshow interval.
    assert HakuViewWindow(slideshow_interval=9.0).slideshow_interval_sec == 9.0


def test_unreadable_image_explains_why_on_the_canvas(tmp_path: Path, qtbot) -> None:
    from hakuview.image_ops import describe_load_failure

    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"this is not a jpeg")
    (tmp_path / "empty.png").write_bytes(b"")
    assert "valid image" in describe_load_failure(broken)
    assert "empty" in describe_load_failure(tmp_path / "empty.png")

    win = HakuViewWindow(initial_path=broken)
    qtbot.addWidget(win)
    assert win.canvas._message is not None and "broken.jpg" in win.canvas._message
    assert win.lbl_dimensions.text() == ""  # not mislabelled as VIDEO
    win.next_item()  # moving on to a good file clears the message
    assert win.canvas._message is None or "empty.png" in win.canvas._message


def test_mouse_wheel_can_navigate_files(tmp_path: Path, qtbot) -> None:
    from PySide6.QtCore import QPoint, QPointF
    from PySide6.QtGui import QWheelEvent

    for i in range(3):
        _create_sample_image(tmp_path / f"img{i}.png")
    win = HakuViewWindow(initial_path=tmp_path / "img0.png")
    qtbot.addWidget(win)

    def wheel(delta_y: int, mods=Qt.KeyboardModifier.NoModifier) -> None:
        ev = QWheelEvent(QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, delta_y),
                         Qt.MouseButton.NoButton, mods, Qt.ScrollPhase.NoScrollPhase, False)
        win.canvas.wheelEvent(ev)

    zoom = win.canvas.zoom_scale
    wheel(-120)
    assert win.navigator.index == 0 and win.canvas.zoom_scale != zoom  # default: wheel zooms

    win.canvas.wheel_navigates = True
    wheel(-120)
    assert win.navigator.current_file.name == "img1.png"
    wheel(60)
    wheel(60)  # two half-notches (touchpad) = one step back
    assert win.navigator.current_file.name == "img0.png"
    zoom = win.canvas.zoom_scale
    wheel(120, Qt.KeyboardModifier.ControlModifier)  # Ctrl+wheel still zooms
    assert win.canvas.zoom_scale != zoom and win.navigator.current_file.name == "img0.png"


def test_toolbar_fits_default_window_width(qtbot) -> None:
    win = HakuViewWindow()
    qtbot.addWidget(win)
    assert win.toolbar.sizeHint().width() < 1180 - 60


def test_copy_image_and_path_to_clipboard(tmp_path: Path, qtbot) -> None:
    from PySide6.QtWidgets import QApplication

    _create_sample_image(tmp_path / "pic.png", width=64, height=32, color=QColor(100, 100, 100))
    win = HakuViewWindow(initial_path=tmp_path / "pic.png")
    qtbot.addWidget(win)
    win.color_bar.slider_brightness.setValue(50)  # what's on screen is what gets copied
    assert win.copy_image_to_clipboard()
    copied = QApplication.clipboard().image()
    assert (copied.width(), copied.height()) == (64, 32)
    assert copied.pixelColor(5, 5).red() > 150
    win.copy_file_path()
    assert QApplication.clipboard().text() == str(tmp_path / "pic.png")


def test_rename_updates_disk_folder_list_and_unsaved_edits(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    for name in ("a.png", "b.png", "c.png"):
        _create_sample_image(tmp_path / name, width=40, height=20)
    win = HakuViewWindow(initial_path=tmp_path / "a.png")
    qtbot.addWidget(win)
    win._prompt_unsaved = False
    win.rotate_right()  # unsaved edit must survive the rename

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    assert win.rename_current_file("b.png") is None and "already exists" in warnings[-1]
    assert win.rename_current_file("x/y.png") is None and "can't contain" in warnings[-1]

    target = win.rename_current_file("zebra.png")
    assert target == tmp_path / "zebra.png" and target.exists() and not (tmp_path / "a.png").exists()
    assert win.navigator.current_file == target
    assert [p.name for p in win.navigator.files] == ["b.png", "c.png", "zebra.png"]  # re-sorted
    assert win.document.path == target and win.document.modified

    # F2 opens a dialog pre-filled with the current name.
    seen = []

    def fake_exec(dlg):
        seen.append(dlg.textValue())
        dlg.setTextValue("final.png")
        return 1

    monkeypatch.setattr(QInputDialog, "exec", fake_exec)
    assert win.rename_current_file() == tmp_path / "final.png"
    assert seen == ["zebra.png"]


def test_show_in_file_manager_selects_file_via_dbus_with_folder_fallback(tmp_path: Path, qtbot, monkeypatch) -> None:
    import sys as _sys
    from PySide6.QtCore import QProcess
    from PySide6.QtGui import QDesktopServices

    if not _sys.platform.startswith("linux"):
        pytest.skip("Linux D-Bus path")
    _create_sample_image(tmp_path / "it's here.png")
    win = HakuViewWindow(initial_path=tmp_path / "it's here.png")
    qtbot.addWidget(win)

    started, opened = [], []
    monkeypatch.setattr(QProcess, "start", lambda self, prog, args: started.append((self, prog, args)))
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    win.show_in_file_manager()
    proc, prog, args = started[0]
    assert prog == "gdbus" and "org.freedesktop.FileManager1.ShowItems" in args
    assert "it%27s%20here.png" in args[-2]  # quoted safely for the GVariant string
    proc.finished.emit(1, QProcess.ExitStatus.NormalExit)  # no FileManager1 service -> open the folder
    assert opened == [str(tmp_path)]


def test_video_frame_step_and_speed_keys(tmp_path: Path, qtbot) -> None:
    import shutil
    import subprocess
    from PySide6.QtTest import QTest

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=3",
         "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    win = HakuViewWindow(initial_path=clip)
    qtbot.addWidget(win)
    _show_active(win, qtbot)
    player = win.video_view.player
    try:
        qtbot.waitUntil(lambda: player.duration() > 0, timeout=5000)
    except Exception:
        pytest.skip("no video decoding backend available")

    player.pause()
    player.setPosition(1000)
    qtbot.waitUntil(lambda: abs(player.position() - 1000) < 50, timeout=3000)
    QTest.keyClick(win, Qt.Key.Key_Period)
    qtbot.waitUntil(lambda: player.position() > 1000, timeout=3000)
    assert 80 <= player.position() - 1000 <= 120  # one frame at 10 fps
    assert player.playbackState() != player.PlaybackState.PlayingState

    zoom = win.canvas.zoom_scale
    QTest.keyClick(win, Qt.Key.Key_Plus)
    assert player.playbackRate() == 1.25 and win.canvas.zoom_scale == zoom  # speed, not zoom
    QTest.keyClick(win, Qt.Key.Key_Minus)
    QTest.keyClick(win, Qt.Key.Key_Minus)
    assert player.playbackRate() == 0.75


def test_auto_reload_on_external_change_and_folder_rescan(tmp_path: Path, qtbot) -> None:
    import os as _os

    _create_sample_image(tmp_path / "a.png", width=40, height=20)
    _create_sample_image(tmp_path / "b.png", width=40, height=20)
    win = HakuViewWindow(initial_path=tmp_path / "a.png")
    qtbot.addWidget(win)

    # Another program rewrites the file -> reloaded.
    img = QImage(80, 60, QImage.Format.Format_RGB32)
    img.fill(QColor("red"))
    img.save(str(tmp_path / "a.png"))
    qtbot.waitUntil(lambda: win.document.current.width() == 80, timeout=4000)

    # New files in the folder show up in navigation.
    _create_sample_image(tmp_path / "c.png")
    qtbot.waitUntil(lambda: win.navigator.count == 3, timeout=4000)
    assert win.navigator.current_file.name == "a.png"

    # Unsaved edits are never thrown away by a reload.
    win.rotate_right()
    img.fill(QColor("blue"))
    img.save(str(tmp_path / "a.png"))
    qtbot.waitUntil(lambda: "unsaved edits were kept" in win.status.currentMessage(), timeout=4000)
    assert win.document.modified

    # The shown file is deleted elsewhere -> the viewer moves on instead of showing a ghost.
    win.document.reset()
    _os.remove(tmp_path / "a.png")
    qtbot.waitUntil(lambda: win.navigator.current_file is not None and win.navigator.current_file.name != "a.png", timeout=4000)
    assert win.navigator.count == 2


def _phone_jpeg(path: Path, size=(320, 240)) -> None:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for i in range(0, size[0], 6):
        draw.line([(i, 0), (size[0] - i, size[1])], fill=(i % 255, 120, 255 - i % 255), width=2)
    img.save(path, quality=75, subsampling=2)


def test_saving_rotated_jpeg_keeps_original_quality_without_jpegtran(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PIL import Image
    from hakuview import image_ops

    monkeypatch.setattr(image_ops.shutil, "which", lambda name: None)
    src = tmp_path / "photo.jpg"
    _phone_jpeg(src)
    before_size = src.stat().st_size
    with Image.open(src) as im:
        tables = im.quantization

    win = HakuViewWindow(initial_path=src)
    qtbot.addWidget(win)
    win.rotate_right()
    assert win.save_current()
    assert "kept original JPEG quality" in win.status.currentMessage()
    with Image.open(src) as im:
        assert im.size == (240, 320) and im.quantization == tables
    assert src.stat().st_size < before_size * 1.3  # was ~2x with the old fixed quality=95


def test_rotate_only_jpeg_edit_uses_lossless_jpegtran(tmp_path: Path, qtbot, monkeypatch) -> None:
    import stat
    import sys as _sys

    # Stand-in jpegtran: records its arguments and copies input to -outfile.
    log = tmp_path / "jpegtran-args.txt"
    fake = tmp_path / "bin" / "jpegtran"
    fake.parent.mkdir()
    fake.write_text(
        f"#!{_sys.executable}\n"
        "import shutil, sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "shutil.copy(args[-1], args[args.index('-outfile') + 1])\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake.parent}:{_os_environ_path()}")

    src = tmp_path / "photo.jpg"
    _phone_jpeg(src)
    win = HakuViewWindow(initial_path=src)
    qtbot.addWidget(win)
    win.rotate_right()
    win.rotate_right()
    win.flip_horizontal()  # = flip vertical overall
    assert win.save_current()
    assert "lossless rotate" in win.status.currentMessage()
    assert log.read_text().split()[:5] == ["-perfect", "-copy", "all", "-flip", "vertical"]

    # A crop can't be done by jpegtran -> falls back to the matched re-encode.
    log.write_text("")
    win.canvas._selection = QRect(0, 0, 100, 100)
    win.apply_crop()
    assert win.save_current()
    assert log.read_text() == "" and "kept original JPEG quality" in win.status.currentMessage()


def _os_environ_path() -> str:
    import os as _os

    return _os.environ.get("PATH", "")


@pytest.mark.parametrize("mode", ["dark", "light"])
def test_checkbox_and_radio_state_is_visible(mode: str, qtbot) -> None:
    """Regression: Fusion drew check marks grey-on-grey under the theme, so clicks looked dead."""
    from PySide6.QtWidgets import QCheckBox, QRadioButton, QVBoxLayout, QWidget

    win = HakuViewWindow()
    qtbot.addWidget(win)
    win.app_settings.theme = mode
    win.apply_settings()

    host = QWidget(win, Qt.WindowType.Window)  # child of the window, so the theme applies
    layout = QVBoxLayout(host)
    widgets = [QCheckBox("off"), QCheckBox("on"), QRadioButton("off"), QRadioButton("on")]
    for w in widgets:
        layout.addWidget(w)
    widgets[1].setChecked(True)
    widgets[3].setChecked(True)
    host.show()
    qtbot.waitExposed(host)

    from hakuview import theme

    accent = QColor(theme.PALETTE["accent_strong"])

    def accent_pixels(w) -> int:
        img = w.grab().toImage()
        mid = img.height() // 2
        return sum(
            1
            for x in range(0, 20)
            for y in range(mid - 8, mid + 8)
            if abs(img.pixelColor(x, y).red() - accent.red()) + abs(img.pixelColor(x, y).green() - accent.green())
            + abs(img.pixelColor(x, y).blue() - accent.blue()) < 60
        )

    for off, on in ((widgets[0], widgets[1]), (widgets[2], widgets[3])):
        assert accent_pixels(off) < 5, f"unchecked {type(off).__name__} should not look selected ({mode})"
        assert accent_pixels(on) > 8, f"checked {type(on).__name__} must clearly show it ({mode})"


def test_goto_matching_numbers_and_names() -> None:
    from hakuview.goto_dialog import match_files

    files = [Path(n) for n in ("IMG_0001.jpg", "beach.png", "WhatsApp Image 2.jpeg", "img_party.jpg", "clip.mp4")]
    assert match_files(files, "") == [0, 1, 2, 3, 4]
    assert match_files(files, "img") == [0, 3]  # case-insensitive
    assert match_files(files, "image") == [2]
    assert match_files(files, "c") == [4, 1]  # "clip" starts with it, so it comes before "beach"
    assert match_files(files, "2") == [1, 2]  # position 2, then names containing "2"
    assert match_files(files, "99") == []
    assert match_files(files, "zzz") == []


def test_goto_dialog_jumps_to_chosen_file(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog

    for name in ("a.png", "b.png", "sunset.png", "z.png"):
        _create_sample_image(tmp_path / name, width=20, height=20)
    win = HakuViewWindow(initial_path=tmp_path / "a.png")
    qtbot.addWidget(win)

    from hakuview import goto_dialog

    def fake_exec(dlg):
        assert dlg.list.currentRow() == 0  # starts on the current file
        QTest.keyClicks(dlg.edit, "suns")
        assert dlg.list.count() == 1
        dlg.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(goto_dialog.GoToFileDialog, "exec", fake_exec)
    assert win.show_goto_dialog() == tmp_path / "sunset.png"
    assert win.navigator.current_file == tmp_path / "sunset.png"
    assert win.go_to_index(99) is None


def test_save_video_frame_writes_png_next_to_video(tmp_path: Path, qtbot) -> None:
    win = _video_window(tmp_path, qtbot)
    frame = QImage(64, 36, QImage.Format.Format_RGB32)
    frame.fill(QColor(100, 100, 100))
    win.video_view.grab_current_frame = lambda: frame
    win.color_bar.slider_brightness.setValue(50)  # saved as seen on screen

    target = win.save_video_frame()
    assert target is not None and target.parent == tmp_path
    assert target.name == "clip_frame_00m00s000.png"
    saved = QImage(str(target))
    assert (saved.width(), saved.height()) == (64, 36)
    assert saved.pixelColor(5, 5).red() > 150
    assert target in win.navigator.files  # shows up in ←/→ browsing
    assert win.save_video_frame().name == "clip_frame_00m00s000_2.png"  # never overwrites


def test_recent_folders_menu(tmp_path: Path, qtbot) -> None:
    from hakuview.settings import MAX_RECENT_FOLDERS

    folders = []
    for i in range(3):
        d = tmp_path / f"f{i}"
        d.mkdir()
        _create_sample_image(d / "x.png", width=20, height=20)
        folders.append(d)
    win = HakuViewWindow(initial_path=folders[0])
    qtbot.addWidget(win)
    win.open_path(folders[1] / "x.png")
    win.open_path(folders[0])
    assert win.app_settings.recent_folders[:2] == [str(folders[0]), str(folders[1])]  # newest first, no dupes

    menu = win.show_more_options_menu(exec_menu=False)
    recent = next(a.menu() for a in menu.actions() if a.text() == "🕘 Recent Folders")
    entry = next(a for a in recent.actions() if a.text() == str(folders[1]))
    entry.trigger()
    assert win.navigator.folder == folders[1]

    folders[2].joinpath("x.png").unlink()
    folders[2].rmdir()
    win.app_settings.add_recent_folder(str(folders[2]))
    assert not win.open_recent_folder(str(folders[2]))  # gone: dropped from the list
    assert str(folders[2]) not in win.app_settings.recent_folders

    for i in range(MAX_RECENT_FOLDERS + 3):
        win.app_settings.add_recent_folder(f"/tmp/nowhere{i}")
    assert len(win.app_settings.recent_folders) == MAX_RECENT_FOLDERS
    win.clear_recent_folders()
    assert win.app_settings.recent_folders == []


def test_windows_open_with_registers_hakuview_icon(monkeypatch, tmp_path: Path) -> None:
    """The 'Open with' dialog takes a ProgID's icon from its Application subkey (fake registry)."""
    import sys
    import types

    from hakuview import installer

    keys: dict = {}

    class _Key:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def create_key(_root, path):
        parts = path.split("\\")
        for i in range(1, len(parts) + 1):  # like the real registry, intermediate keys are created too
            keys.setdefault("\\".join(parts[:i]), {})
        return _Key(path)

    def open_key(_root, path, *_args):
        if path not in keys:
            raise OSError(path)
        return _Key(path)

    def query_value(key, name):
        if name not in keys[key.path]:
            raise OSError(name)
        return keys[key.path][name], 1

    def enum_key(key, i):
        prefix = key.path + "\\"
        children = sorted({p[len(prefix):].split("\\")[0] for p in keys if p.startswith(prefix)})
        if i >= len(children):
            raise OSError
        return children[i]

    def delete_key(_root, path):
        if any(p.startswith(path + "\\") for p in keys):
            raise OSError("has subkeys")
        del keys[path]

    def delete_value(key, name):
        del keys[key.path][name]

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), REG_SZ=1, REG_DWORD=4, REG_NONE=0, KEY_READ=1, KEY_WRITE=2, KEY_SET_VALUE=2,
        CreateKey=create_key, OpenKey=open_key, QueryValueEx=query_value, EnumKey=enum_key,
        DeleteKey=delete_key, DeleteValue=delete_value,
        SetValueEx=lambda key, name, _r, _t, value: keys[key.path].__setitem__(name, value),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    ico = tmp_path / "Hakan's Apps" / "hakuview.ico"
    monkeypatch.setattr(installer, "ensure_hakuview_ico", lambda: ico)
    gui = tmp_path / "Scripts" / "hakuview-gui.exe"
    gui.parent.mkdir()
    gui.write_bytes(b"")
    monkeypatch.setattr(installer.shutil, "which", lambda name: str(gui) if name == "hakuview-gui" else None)
    monkeypatch.setattr(installer, "_refresh_windows_shell", lambda: None)

    installer._install_windows_integration()
    icon = f"{ico},0"  # unquoted "path,index", even with spaces in the path
    for progid in ("HakuView.Image", "HakuView.Video"):
        app = keys[rf"Software\Classes\{progid}\Application"]
        assert app["ApplicationName"] == "HakuView" and app["ApplicationIcon"] == icon
        assert keys[rf"Software\Classes\{progid}\DefaultIcon"][""] == icon
        assert keys[rf"Software\Classes\{progid}\shell\open\command"][""] == f'"{gui.resolve()}" "%1"'  # no console window
    assert "HakuView.Image" in keys[r"Software\Classes\.jpg\OpenWithProgids"]
    assert keys[r"Software\Classes\Applications\hakuview-gui.exe\DefaultIcon"][""] == icon

    installer._uninstall_windows_integration()
    left = [p for p in keys if "HakuView" in p or "hakuview" in p]
    assert not left, "\n".join(left)
    assert "HakuView.Image" not in keys[r"Software\Classes\.jpg\OpenWithProgids"]


def test_color_sliders_preview_then_render_full_resolution(tmp_path: Path, qtbot) -> None:
    """Dragging recolours a screen-sized preview; the full-size result arrives once the slider rests."""
    _create_sample_image(tmp_path / "big.png", width=3000, height=2000, color=QColor(100, 100, 100))
    win = HakuViewWindow(initial_path=tmp_path / "big.png")
    qtbot.addWidget(win)
    win._prompt_unsaved = False
    win.resize(700, 500)
    _show_active(win, qtbot)
    assert win.canvas.zoom_scale < 0.5  # fit-to-window: the photo is shown much smaller than its pixels
    win.canvas.select_all()
    selection = win.canvas.selection

    win.color_bar.slider_brightness.setValue(40)
    preview = win.canvas._pixmap
    assert preview.width() < 1500  # recoloured at screen size, not 3000 px wide
    assert preview.toImage().pixelColor(5, 5).red() > 130
    assert win.canvas.selection == selection  # adjusting no longer drops the crop box
    assert win.canvas.image.width() == 3000  # geometry still refers to the real image

    qtbot.waitUntil(lambda: win.canvas._pixmap.width() == 3000, timeout=5000)
    assert win.canvas._pixmap.toImage().pixelColor(5, 5).red() > 130

    # A stale background result (sliders moved again meanwhile) is ignored.
    gen = win._color_generation
    win.color_bar.slider_brightness.setValue(0)
    win._on_full_color_rendered(gen, QImage(3000, 2000, QImage.Format.Format_RGB32))
    assert win.canvas._pixmap.toImage().pixelColor(5, 5).red() == 100  # back to the original

    # Edits keep the adjustment on screen.
    win.color_bar.slider_brightness.setValue(40)
    win.rotate_right()
    assert win.canvas._pixmap.toImage().pixelColor(5, 5).red() > 130


def test_gui_child_processes_never_open_console_windows_on_windows() -> None:
    """Every subprocess.run/Popen the GUI makes passes **no_window(); otherwise Windows pops up a
    cmd window for each ffprobe/ffmpeg/git call (e.g. on every video opened)."""
    import ast

    src = Path(__file__).resolve().parents[1] / "src" / "hakuview"
    offenders = []
    for path in sorted(src.glob("*.py")):
        if path.name in ("installer.py", "cli.py"):  # run from a terminal: output must reach that console
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("run", "Popen", "call", "check_output", "check_call")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
            ):
                hidden = any(
                    kw.arg is None and isinstance(kw.value, ast.Call) and getattr(kw.value.func, "id", "") == "no_window"
                    for kw in node.keywords
                )
                if not hidden:
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_no_window_flags(monkeypatch, tmp_path: Path) -> None:
    import sys

    from hakuview import proc

    monkeypatch.setattr(sys, "platform", "linux")
    assert proc.no_window() == {}
    monkeypatch.setattr(sys, "platform", "win32")
    assert proc.no_window() == {"creationflags": 0x08000000}

    python = tmp_path / "python.exe"
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(python))
    assert proc.gui_python() == str(tmp_path / "pythonw.exe")  # relaunch without a console


def test_whatsapp_bitrate_budget_and_worker(tmp_path: Path) -> None:
    import shutil
    import subprocess
    from hakuview.video_view import (
        WHATSAPP_DEFAULT_MAXRATE_KBPS,
        WHATSAPP_MAX_MB,
        _WhatsAppWorker,
        whatsapp_target_bitrate_kbps,
    )

    # 1. Long video (240s) must cap bitrate so total output stays under WHATSAPP_MAX_MB (15 MiB)
    big_stub = tmp_path / "big.mp4"
    big_stub.write_bytes(b"\0" * (40 * 1024 * 1024))
    v_kbps, a_kbps = whatsapp_target_bitrate_kbps(big_stub, duration_sec=240.0)
    total_mb = (v_kbps + a_kbps) * 240.0 / (8 * 1024)
    assert total_mb <= WHATSAPP_MAX_MB
    assert v_kbps < WHATSAPP_DEFAULT_MAXRATE_KBPS

    # 2. Already medium-bitrate video (e.g. 2 MB over 10s = 1600 kbps) gets its bitrate lowered (~65%)
    med_stub = tmp_path / "med.mp4"
    med_stub.write_bytes(b"\0" * (2 * 1024 * 1024))
    v_med, _ = whatsapp_target_bitrate_kbps(med_stub, duration_sec=10.0)
    assert v_med < 1200

    # 3. End-to-end FFmpeg worker produces an H.264 yuv420p MP4 with even dimensions and smaller size
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    src = tmp_path / "odd_clip.mkv"
    dest = tmp_path / "odd_clip_whatsapp.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=2:size=321x241:rate=20",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            str(src),
        ],
        check=True,
    )
    done = []
    worker = _WhatsAppWorker(src, dest, duration_sec=2.0)
    worker.finished.connect(done.append)
    worker.run()
    assert done == [dest]
    assert dest.exists() and dest.stat().st_size < src.stat().st_size


def test_whatsapp_button_and_shortcut_in_video_mode(tmp_path: Path, qtbot, monkeypatch) -> None:
    win = _video_window(tmp_path, qtbot)
    assert hasattr(win.video_view, "btn_whatsapp")
    assert win.video_view.btn_whatsapp.isEnabled()
    assert win.btn_compress.isEnabled()

    started = []

    def fake_start(job, on_ok, on_err):
        started.append(job.dest_path)
        return True

    monkeypatch.setattr(win.video_view, "_start_job", fake_start)
    win.video_view.btn_whatsapp.click()
    assert len(started) == 1
    assert started[0].name == "clip_whatsapp.mp4"
    assert not win.video_view.btn_whatsapp.isEnabled()
    assert "WhatsApp" in win.video_view.btn_whatsapp.text()

    # Simulate completion -> button re-enabled and new file added to folder navigation
    started[0].write_bytes(b"\x00\x00\x00\x18ftypmp42")
    win.video_view._on_whatsapp_success(started[0])
    assert win.video_view.btn_whatsapp.isEnabled()
    assert started[0] in win.navigator.files
    assert "WhatsApp video saved" in win.status.currentMessage()


def test_video_resize_dominant_color_dialog_and_worker(tmp_path: Path, qtbot, monkeypatch) -> None:
    import json
    import shutil
    import subprocess
    from PySide6.QtWidgets import QDialog
    from hakuview.video_view import (
        VideoResizeDialog,
        _ResizeWorker,
        build_resize_vf,
        extract_dominant_color,
        render_resize_preview,
    )

    # 1. Dominant color extraction ignores black bars and picks the saturated foreground color
    vertical_frame = QImage(90, 160, QImage.Format.Format_RGB32)
    vertical_frame.fill(QColor(0, 0, 0))
    for y in range(20, 140):
        for x in range(10, 80):
            vertical_frame.setPixelColor(x, y, QColor(30, 110, 220))
    dom = extract_dominant_color(vertical_frame)
    assert dom.blue() > dom.red() + 40

    # 2. Dialog presets, custom dimensions (auto-evened), swap W/H, and live preview
    dlg = VideoResizeDialog(frame=vertical_frame)
    qtbot.addWidget(dlg)
    assert (dlg.target_width, dlg.target_height) == (1920, 1080)
    assert dlg.bg_style == "dominant_blur"
    assert dlg.lbl_preview.pixmap() is not None and not dlg.lbl_preview.pixmap().isNull()

    dlg.swap_dimensions()
    assert (dlg.target_width, dlg.target_height) == (1080, 1920)

    dlg.spin_width.setValue(1281)  # odd input is rounded down to even for H.264
    dlg.spin_height.setValue(721)
    assert (dlg.target_width, dlg.target_height) == (1280, 720)
    assert dlg.combo_preset.currentData()[0] == "16:9_720p"

    # All background styles produce valid preview images and filtergraphs
    for style in ("dominant_blur", "ambient_glow", "video_blur", "dominant_solid", "crop"):
        vf = build_resize_vf(640, 360, bg_style=style, dominant_hex=dom.name(), blur_sigma=28, fg_scale_pct=92)
        assert "640" in vf and "360" in vf and "yuv420p" in vf
        prev = render_resize_preview(vertical_frame, 640, 360, bg_style=style, dominant_color=dom, blur_sigma=28, fg_scale_pct=92)
        assert not prev.isNull() and prev.width() > prev.height()

    # 3. Window integration via btn_resize
    win = _video_window(tmp_path, qtbot)
    assert hasattr(win.video_view, "btn_resize")
    assert win.video_view.btn_resize.isEnabled()

    def accept_720p(self):
        self.spin_width.setValue(640)
        self.spin_height.setValue(360)
        return QDialog.DialogCode.Accepted

    started = []
    monkeypatch.setattr(VideoResizeDialog, "exec", accept_720p)
    monkeypatch.setattr(win.video_view, "_start_job", lambda job, ok, err: started.append(job) or True)
    win.video_view.btn_resize.click()
    assert len(started) == 1
    assert started[0].dest_path.name == "clip_640x360.mp4"
    assert not win.video_view.btn_resize.isEnabled()

    started[0].dest_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    win.video_view._on_resize_success(started[0].dest_path)
    assert win.video_view.btn_resize.isEnabled()
    assert started[0].dest_path in win.navigator.files

    # 4. End-to-end FFmpeg _ResizeWorker converts a 180x320 vertical clip into a 320x180 landscape MP4
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not available")
    src = tmp_path / "vertical.mp4"
    dest = tmp_path / "vertical_320x180.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=1:size=180x320:rate=15",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )
    done = []
    worker = _ResizeWorker(src, dest, width=320, height=180, bg_style="dominant_blur", dominant_hex="#1e40af", blur_sigma=24, fg_scale_pct=94)
    worker.finished.connect(done.append)
    worker.run()
    assert done == [dest] and dest.exists()
    probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(dest)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert (probe["streams"][0]["width"], probe["streams"][0]["height"]) == (320, 180)


def test_job_progress_bar_updates_and_cancel_button(tmp_path: Path, qtbot) -> None:
    import shutil
    from hakuview.video_view import _TrimWorker

    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")

    win = _video_window(tmp_path, qtbot)
    vv = win.video_view
    assert not vv.job_bar.isVisible()
    assert not win.status_progress.isVisible()

    class _SlowJob(_TrimWorker):
        label = "Video resize"

        def _attempts(self, ffmpeg):
            return [([ffmpeg, "-y", "-re", "-f", "lavfi", "-i", "testsrc=duration=10", str(self.dest_path)], 0)]

    dest = tmp_path / "out_resize.mp4"
    job = _SlowJob(dest, dest, 0, 10000)
    vv.btn_resize.setEnabled(False)
    vv.btn_resize.setText("⏳ Resizing...")
    assert vv._start_job(job, lambda p: None, lambda e: None)
    assert vv.job_bar.isVisible()
    assert win.status_progress.isVisible()
    assert win.lbl_progress.isVisible()

    # Simulate progress updates (0 -> 45 -> 100)
    vv._on_job_progress(45)
    assert vv.job_progress.value() == 45
    assert vv.lbl_job_pct.text() == "45%"
    assert win.status_progress.value() == 45
    assert "45%" in win.lbl_progress.text()

    # Clicking Cancel stops the job, hides both progress bars, and restores button state
    vv.btn_cancel_job.click()
    assert not vv.is_job_running()
    assert not vv.job_bar.isVisible()
    assert not win.status_progress.isVisible()
    assert vv.btn_resize.isEnabled() and vv.btn_resize.text() == "📐 Resize"
    assert "Cancelled" in win.status.currentMessage()


def test_video_rotate_flip_and_strip_audio(tmp_path: Path, qtbot, monkeypatch) -> None:
    import json
    import shutil
    import subprocess
    from hakuview.video_view import _StripAudioWorker, _TransformVideoWorker

    win = _video_window(tmp_path, qtbot)
    assert win.btn_edit.isEnabled()
    assert win.act_strip_audio.isVisible()
    assert win.act_resize_video.isVisible()

    started = []
    monkeypatch.setattr(win.video_view, "_start_job", lambda job, ok, err: started.append(job) or True)

    win.rotate_right()
    assert isinstance(started[-1], _TransformVideoWorker) and started[-1].transform == "rot_right"
    assert started[-1].dest_path.name == "clip_rot90.mp4"

    win.rotate_left()
    assert isinstance(started[-1], _TransformVideoWorker) and started[-1].transform == "rot_left"
    assert started[-1].dest_path.name == "clip_rot270.mp4"

    win.flip_horizontal()
    assert isinstance(started[-1], _TransformVideoWorker) and started[-1].transform == "flip_h"

    win.flip_vertical()
    assert isinstance(started[-1], _TransformVideoWorker) and started[-1].transform == "flip_v"

    win.strip_video_audio()
    assert isinstance(started[-1], _StripAudioWorker)
    assert started[-1].dest_path.name == "clip_muted.mp4"

    # Completion signals add the new videos into folder navigation
    rot_out = tmp_path / "clip_rot90.mp4"
    rot_out.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    win.video_view.transformCompleted.emit(rot_out, "Rotate 90° right")
    assert rot_out in win.navigator.files
    assert "Rotate 90° right" in win.status.currentMessage()

    mute_out = tmp_path / "clip_muted.mp4"
    mute_out.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    win.video_view.stripAudioCompleted.emit(mute_out)
    assert mute_out in win.navigator.files
    assert "Audio-stripped video saved" in win.status.currentMessage()

    # End-to-end FFmpeg verification for rotate 90° and lossless strip audio
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not available")

    src = tmp_path / "av_clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=1:size=160x100:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src),
        ],
        check=True,
    )

    rot_dest = tmp_path / "av_clip_rot_right.mp4"
    rot_worker = _TransformVideoWorker(src, rot_dest, "rot_right")
    rot_done = []
    rot_worker.finished.connect(rot_done.append)
    rot_worker.run()
    assert rot_done == [rot_dest] and rot_dest.exists()
    v_probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(rot_dest)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert (v_probe["streams"][0]["width"], v_probe["streams"][0]["height"]) == (100, 160)

    mute_dest = tmp_path / "av_clip_mute.mp4"
    mute_worker = _StripAudioWorker(src, mute_dest)
    mute_done = []
    mute_worker.finished.connect(mute_done.append)
    mute_worker.run()
    assert mute_done == [mute_dest] and mute_dest.exists()
    a_probe = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "json", str(mute_dest)],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert a_probe.get("streams", []) == []


def test_image_caption_dialog_styles_undo_and_save_copy(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog
    from hakuview.caption_dialog import (
        CaptionDialog,
        CaptionSpec,
        apply_caption_to_image,
        render_caption_overlay,
    )

    base = QImage(320, 240, QImage.Format.Format_RGB32)
    base.fill(QColor(20, 30, 45))

    # 1. All background styles and positions render non-empty overlays
    for bg_style in ("pill", "banner", "outline", "none"):
        for pos in ("bottom", "center", "top", "bottom_left", "bottom_right"):
            spec = CaptionSpec(
                text="Hello HakuView!\nSecond line",
                font_family="Serif",
                font_size=48,
                bold=True,
                italic=False,
                text_color="#fde047",
                bg_style=bg_style,
                bg_color="#000000",
                bg_opacity=200,
                position=pos,
                margin_pct=10,
            )
            ovl = render_caption_overlay(320, 240, spec)
            assert not ovl.isNull() and (ovl.width(), ovl.height()) == (320, 240)
            out = apply_caption_to_image(base, spec)
            assert out != base

    # 2. Dialog presets, font/size controls, and live preview
    dlg = CaptionDialog(frame=base, is_video=False)
    qtbot.addWidget(dlg)
    dlg.text_edit.setPlainText("Summer 2026")
    dlg.slider_size.setValue(54)
    assert dlg.spin_size.value() == 54
    dlg.combo_preset.setCurrentIndex(2)  # Viral Highlight (Yellow Pill)
    spec_from_dlg = dlg.spec()
    assert spec_from_dlg.text == "Summer 2026"
    assert spec_from_dlg.font_size == 54
    assert spec_from_dlg.bg_color.lower() == "#facc15"
    assert dlg.lbl_preview.pixmap() is not None and not dlg.lbl_preview.pixmap().isNull()

    # 3. Window integration: Apply to Image (with Undo) + Save as New Image copy
    img_path = tmp_path / "photo.png"
    base.save(str(img_path))
    win = HakuViewWindow(initial_path=img_path)
    qtbot.addWidget(win)
    win._prompt_unsaved = False

    def accept_apply(self):
        self.text_edit.setPlainText("Apply In Memory")
        self.slider_size.setValue(46)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CaptionDialog, "exec", accept_apply)
    win.btn_caption.click()
    assert win.document.modified
    assert "caption" in win.document.ops
    assert win.document.current != base

    # Undo restores exact original image
    win.undo_edit()
    assert not win.document.modified
    assert win.document.current == base

    # Save as New Image writes <name>_caption.png and adds it to folder navigation
    def accept_copy(self):
        self.text_edit.setPlainText("Saved Copy Caption")
        self.save_as_copy = True
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CaptionDialog, "exec", accept_copy)
    saved_copy = win.open_caption_dialog()
    assert saved_copy == tmp_path / "photo_caption.png"
    assert saved_copy.exists()
    assert saved_copy in win.navigator.files
    assert not win.document.modified  # original document untouched


def test_video_caption_dialog_and_ffmpeg_overlay_worker(tmp_path: Path, qtbot, monkeypatch) -> None:
    import shutil
    import subprocess
    from PySide6.QtWidgets import QDialog
    from hakuview.caption_dialog import CaptionDialog, CaptionSpec
    from hakuview.video_view import _CaptionVideoWorker

    win = _video_window(tmp_path, qtbot)
    assert win.btn_caption.isEnabled()
    win.video_view.player.duration = lambda: 8000
    win.video_view.set_trim_in(1000)
    win.video_view.set_trim_out(5000)

    started = []
    monkeypatch.setattr(win.video_view, "_start_job", lambda job, ok, err: started.append(job) or True)

    def accept_video_caption(self):
        assert self._is_video
        assert not self.chk_trim_range.isHidden() and self.chk_trim_range.isChecked()
        self.text_edit.setPlainText("Watch till the end!")
        self.slider_size.setValue(48)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CaptionDialog, "exec", accept_video_caption)
    target = win.open_caption_dialog()
    assert target == tmp_path / "clip_caption.mp4"
    assert len(started) == 1 and isinstance(started[-1], _CaptionVideoWorker)
    assert started[-1].trim_range_ms == (1000, 5000)

    target.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    win.video_view.captionCompleted.emit(target)
    assert target in win.navigator.files
    assert "Captioned video saved" in win.status.currentMessage()

    # End-to-end FFmpeg verification: burn caption overlay onto a video clip
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    src = tmp_path / "dark_clip.mp4"
    dest = tmp_path / "dark_clip_caption.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x101820:size=240x160:rate=15:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )
    spec = CaptionSpec(
        text="HakuView Video Caption",
        font_family="Sans Serif",
        font_size=52,
        bold=True,
        text_color="#111827",
        bg_style="pill",
        bg_color="#facc15",
        bg_opacity=245,
        position="center",
    )
    worker = _CaptionVideoWorker(src, dest, spec, fallback_size=(240, 160))
    done = []
    worker.finished.connect(done.append)
    worker.run()
    assert done == [dest] and dest.exists()
    assert not worker._overlay_path.exists()  # temporary overlay PNG cleaned up


def test_floating_paste_overlay_stamp_chain_stitch_and_undo(tmp_path: Path, qtbot) -> None:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    base = QImage(300, 200, QImage.Format.Format_RGB32)
    base.fill(QColor(25, 35, 50))
    base_path = tmp_path / "base.png"
    base.save(str(base_path))

    win = HakuViewWindow(initial_path=base_path)
    qtbot.addWidget(win)
    win._prompt_unsaved = False
    _show_active(win, qtbot)

    # 1. Paste a 80x40 red sticker from clipboard -> floating overlay appears
    sticker1 = QImage(80, 40, QImage.Format.Format_RGB32)
    sticker1.fill(QColor(220, 40, 40))
    QApplication.clipboard().setImage(sticker1)

    assert win.paste_image_overlay()
    assert win.canvas.has_overlay
    assert win.paste_bar.isVisible()
    assert (win.canvas.overlay_rect.width(), win.canvas.overlay_rect.height()) == (80, 40)

    # Rotating while floating rotates the overlay, not the base document
    win.rotate_right()
    assert not win.document.modified
    assert (win.canvas.overlay_rect.width(), win.canvas.overlay_rect.height()) == (40, 80)

    # Adjust opacity, rounded corners, and photo border
    win.paste_bar.slider_opacity.setValue(200)
    win.paste_bar.slider_rounded.setValue(20)
    win.paste_bar.chk_border.setChecked(True)
    assert win.canvas.overlay_opacity == 200
    assert win.canvas.overlay_rounded_pct == 20
    assert win.canvas.overlay_border

    # 2. Chained paste: pasting a 2nd image while 1st is floating auto-stamps the 1st!
    sticker2 = QImage(60, 60, QImage.Format.Format_RGB32)
    sticker2.fill(QColor(40, 200, 90))
    QApplication.clipboard().setImage(sticker2)
    assert win.paste_image_overlay()
    assert win.document.ops == ["stamp"]
    assert win.canvas.has_overlay
    assert (win.canvas.overlay_rect.width(), win.canvas.overlay_rect.height()) == (60, 60)

    # Press Enter to stamp the 2nd overlay
    QTest.keyClick(win, Qt.Key.Key_Return)
    assert not win.canvas.has_overlay
    assert not win.paste_bar.isVisible()
    assert win.document.ops == ["stamp", "stamp"]
    assert win.document.current != base

    # Two Ctrl+Z undos peel back both stamped images in reverse order
    win.undo_edit()
    assert win.document.ops == ["stamp"]
    win.undo_edit()
    assert not win.document.modified
    assert win.document.current == base

    # 3. Side-by-Side and Stack-Below stitching from the floating paste bar
    QApplication.clipboard().setImage(sticker2)
    assert win.paste_image_overlay()
    win.paste_bar.btn_stitch_h.click()
    assert not win.canvas.has_overlay
    assert win.document.ops == ["stitch"]
    assert (win.document.current.width(), win.document.current.height()) == (500, 200)
    win.undo_edit()

    QApplication.clipboard().setImage(sticker2)
    assert win.paste_image_overlay()
    win.paste_bar.btn_stitch_v.click()
    assert (win.document.current.width(), win.document.current.height()) == (300, 500)
    win.undo_edit()

    # 4. Esc cancels floating paste without modifying the document
    QApplication.clipboard().setImage(sticker1)
    assert win.paste_image_overlay()
    win._on_escape()
    assert not win.canvas.has_overlay
    assert not win.paste_bar.isVisible()
    assert not win.document.modified


def test_image_erase_region_styles_delete_key_and_dialog(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog
    from hakuview.erase_dialog import (
        EraseRegionDialog,
        erase_image_region,
        sample_surrounding_color,
    )

    # Blue background with a detailed yellow/dark "face" box at (50, 50, 60, 60)
    base = QImage(200, 160, QImage.Format.Format_RGB32)
    base.fill(QColor(20, 80, 180))
    face_rect = QRect(50, 50, 60, 60)
    for y in range(50, 110):
        for x in range(50, 110):
            is_feature = (x + y) % 5 == 0
            base.setPixelColor(x, y, QColor(30, 20, 20) if is_feature else QColor(250, 220, 40))

    surround = sample_surrounding_color(base, face_rect)
    assert surround.blue() > surround.red() + 80

    # 1. All 5 erase styles alter the target region while keeping outside pixels intact
    for style in ("blur", "pixelate", "heal", "surround", "blackout"):
        out = erase_image_region(base, face_rect, style=style, strength=75)
        assert out != base
        assert out.pixelColor(5, 5) == QColor(20, 80, 180)
        if style == "blackout":
            assert out.pixelColor(80, 80) == QColor(0, 0, 0)
        elif style in ("heal", "surround"):
            # Center of the healed/surrounded region now matches the blue background instead of yellow
            c = out.pixelColor(80, 80)
            assert c.blue() > c.red() + 60

    # 2. Fast path: select region on canvas + press Delete -> erases immediately, Ctrl+Z undoes
    img_path = tmp_path / "portrait.png"
    base.save(str(img_path))
    win = HakuViewWindow(initial_path=img_path)
    qtbot.addWidget(win)
    win._prompt_unsaved = False

    win.canvas._selection = QRect(face_rect)
    assert win.delete_selected_region() == img_path
    assert win.canvas.selection.isNull()
    assert win.document.modified and win.document.ops == ["erase"]
    assert win.document.current != base

    win.undo_edit()
    assert not win.document.modified
    assert win.document.current == base

    # 3. Interactive EraseRegionDialog: spinboxes, style picker, and Save as New Image
    dlg = EraseRegionDialog(frame=base, initial_rect=face_rect, initial_style="heal")
    qtbot.addWidget(dlg)
    assert dlg.spec().rect == face_rect
    assert dlg.spec().style == "heal"
    dlg.spin_x.setValue(40)
    assert dlg.preview.region.x() == 40

    def accept_copy(self):
        self.combo_style.setCurrentIndex(self.combo_style.findData("heal"))
        self.save_as_copy = True
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(EraseRegionDialog, "exec", accept_copy)
    saved = win.open_erase_region_dialog()
    assert saved == tmp_path / "portrait_redacted.png"
    assert saved.exists() and saved in win.navigator.files


def test_video_erase_region_dialog_and_ffmpeg_worker(tmp_path: Path, qtbot, monkeypatch) -> None:
    import shutil
    import subprocess
    from PySide6.QtWidgets import QDialog
    from hakuview.erase_dialog import EraseRegionDialog, EraseRegionSpec, build_erase_video_vf
    from hakuview.video_view import _EraseVideoRegionWorker

    rect = QRect(30, 20, 60, 50)
    for style in ("blur", "pixelate", "heal", "surround", "blackout"):
        vf = build_erase_video_vf(rect, 320, 240, style=style, strength=70, trim_range_ms=(1000, 4000))
        assert "between(t,1.000,4.000)" in vf and "yuv420p" in vf

    win = _video_window(tmp_path, qtbot)
    win.video_view.player.duration = lambda: 6000
    win.video_view.set_trim_in(1000)
    win.video_view.set_trim_out(4000)

    started = []
    monkeypatch.setattr(win.video_view, "_start_job", lambda job, ok, err: started.append(job) or True)

    def accept_video_erase(self):
        assert self._is_video
        assert not self.chk_trim_range.isHidden() and self.chk_trim_range.isChecked()
        self.combo_style.setCurrentIndex(self.combo_style.findData("blur"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(EraseRegionDialog, "exec", accept_video_erase)
    target = win.open_erase_region_dialog()
    assert target == tmp_path / "clip_redacted.mp4"
    assert len(started) == 1 and isinstance(started[-1], _EraseVideoRegionWorker)
    assert started[-1].trim_range_ms == (1000, 4000)

    target.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    win.video_view.eraseCompleted.emit(target)
    assert target in win.navigator.files
    assert "Redacted video saved" in win.status.currentMessage()

    # End-to-end FFmpeg verification for both "blur" and "heal" (delogo)
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not available")
    src = tmp_path / "vid.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=duration=1:size=240x160:rate=15",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )
    for style in ("blur", "heal"):
        dest = tmp_path / f"vid_{style}.mp4"
        spec = EraseRegionSpec(rect=QRect(40, 30, 80, 60), style=style, strength=65)
        worker = _EraseVideoRegionWorker(src, dest, spec, frame_size=(240, 160))
        done = []
        worker.finished.connect(done.append)
        worker.run()
        assert done == [dest] and dest.exists()


def test_image_resize_dialog_scale_reframe_undo_and_save_copy(tmp_path: Path, qtbot, monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog
    from hakuview.image_ops import resize_qimage
    from hakuview.resize_dialog import ImageResizeDialog

    base = QImage(200, 100, QImage.Format.Format_RGB32)
    base.fill(QColor(30, 120, 210))
    for y in range(25, 75):
        for x in range(50, 150):
            base.setPixelColor(x, y, QColor(240, 200, 50))

    # 1. Direct resize_qimage across all 4 modes & filters
    scaled = resize_qimage(base, 100, 50, mode="scale", resample="lanczos")
    assert (scaled.width(), scaled.height()) == (100, 50)

    padded_blur = resize_qimage(base, 160, 160, mode="pad_blur", bg_color="#112233")
    assert (padded_blur.width(), padded_blur.height()) == (160, 160)

    padded_solid = resize_qimage(base, 160, 160, mode="pad_solid", bg_color="#ff0000")
    assert (padded_solid.width(), padded_solid.height()) == (160, 160)
    assert padded_solid.pixelColor(5, 5) == QColor("#ff0000")

    cropped = resize_qimage(base, 120, 120, mode="crop_fill", resample="nearest")
    assert (cropped.width(), cropped.height()) == (120, 120)

    # 2. ImageResizeDialog percentage lock, presets, and swap
    dlg = ImageResizeDialog(frame=base)
    qtbot.addWidget(dlg)
    assert (dlg.target_width, dlg.target_height) == (200, 100)
    dlg.set_percentage(50)
    assert (dlg.target_width, dlg.target_height) == (100, 50)
    dlg.spin_width.setValue(300)
    assert dlg.target_height == 150  # aspect ratio locked

    dlg.swap_dimensions()
    assert (dlg.target_width, dlg.target_height) == (150, 300)
    assert dlg.mode == "pad_blur"

    # 3. HakuViewWindow.open_resize_dialog (Ctrl+R) in-place with Ctrl+Z undo, plus Save as New Image
    img_path = tmp_path / "photo.png"
    base.save(str(img_path))
    win = HakuViewWindow(initial_path=img_path)
    qtbot.addWidget(win)
    win._prompt_unsaved = False

    def accept_half(self):
        self.set_percentage(50)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ImageResizeDialog, "exec", accept_half)
    assert win.open_resize_dialog() == img_path
    assert win.document.modified and win.document.ops == ["resize"]
    assert (win.document.current.width(), win.document.current.height()) == (100, 50)

    win.undo_edit()
    assert not win.document.modified
    assert (win.document.current.width(), win.document.current.height()) == (200, 100)

    def accept_save_copy(self):
        self.set_percentage(150)
        self._save_as_copy = True
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ImageResizeDialog, "exec", accept_save_copy)
    saved = win.open_resize_dialog()
    assert saved == tmp_path / "photo_300x150.png"
    assert saved.exists() and saved in win.navigator.files


def test_quick_draw_and_annotate_tools_undo_and_apply(tmp_path: Path, qtbot) -> None:
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest
    from hakuview.image_ops import DrawStroke

    base = QImage(240, 180, QImage.Format.Format_RGB32)
    base.fill(QColor(245, 245, 245))
    img_path = tmp_path / "canvas.png"
    base.save(str(img_path))

    win = HakuViewWindow(initial_path=img_path)
    qtbot.addWidget(win)
    win._prompt_unsaved = False
    win.show()

    # 1. Press D (toggle_draw_bar) to enter Draw & Annotate mode
    assert win.toggle_draw_bar()
    assert win.canvas.draw_mode
    assert win.draw_bar.isVisible()

    # Change tool, color, and size from DrawAnnotateBar
    win.draw_bar.set_tool("arrow")
    win.draw_bar.set_color("#ef4444")
    win.draw_bar.slider_size.setValue(8)
    assert win.canvas._draw_tool == "arrow"
    assert win.canvas._draw_color == "#ef4444"
    assert win.canvas._draw_size == 8

    # Add strokes for all 6 supported tools (pen, highlighter, arrow, rect, ellipse, line)
    for idx, tool in enumerate(("pen", "highlighter", "arrow", "rect", "ellipse", "line")):
        y = 20 + idx * 22
        win.canvas.add_draw_stroke(
            DrawStroke(
                tool=tool,
                color="#ef4444",
                size=6,
                points=[QPointF(30, y), QPointF(120, y + 10), QPointF(190, y + 14)],
            )
        )
    assert len(win.canvas.draw_strokes) == 6

    # 2. Undo while in draw mode pops only the last uncommitted stroke
    win.undo_edit()
    assert len(win.canvas.draw_strokes) == 5

    # 3. Press Enter to bake annotations onto the ImageDocument
    QTest.keyClick(win, Qt.Key.Key_Return)
    assert not win.canvas.draw_mode
    assert not win.draw_bar.isVisible()
    assert win.document.modified and win.document.ops == ["annotate"]
    assert win.document.current != base

    # 4. Ctrl+Z restores the clean un-annotated image
    win.undo_edit()
    assert not win.document.modified
    assert win.document.current == base





