"""Failed or cancelled path capture must not damage saved sampling state."""

from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PIL import Image
from PySide6.QtWidgets import QApplication

from engine.ass_parser import ASSFile, ASSEvent, ASSStyle
from engine.path_model import PathSet
from engine.path_tracer import sample_path_points_from_path
from gui.dependency_health import HealthReport
from gui.main_window import MainWindow
from gui.path_sampler import PathSamplerDialog


class PathCaptureWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        source = ASSFile(
            styles=[ASSStyle(name="Default", fontname="Arial", fontsize=12)],
            events=[ASSEvent(end="0:00:01.00", text=r"{\pos(20,10)\bord0}Sample")],
        )
        with (
            patch("gui.main_window.parse_ass_file", return_value=source),
            patch("gui.main_window.run_startup_health_check", return_value=HealthReport([])),
            patch.object(MainWindow, "_start_update_check"),
        ):
            self.window = MainWindow(input_path=__file__)
        w = self.window
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.close)
        self.addCleanup(w._preview_timer.stop)
        self.addCleanup(w._undo_timer.stop)
        self.path = "m 1 1 l 8 1"
        self.frame = Image.new("RGB", (10, 10), "red")
        self.old_samples = sample_path_points_from_path(self.path, lambda x, y: "FF0000")
        w._sampling_paths = {0: {"1c": PathSet.from_ass_path(
            self.path, sampling_frame=0, sampled_points=self.old_samples,
        )}}
        w._tag_panel.get_row("1c")._check.setChecked(True)
        w._refresh_path_buttons()
        w._update_curve_color_preview()
        w._push_undo("Initial samples")

    def open_path(self, edit, *, available=True):
        w = self.window
        with (
            patch.object(w, "_resolve_video_path", return_value="synthetic.mp4"),
            patch.object(w, "_load_path_preview_frame", return_value=available),
            patch.object(w._path_frame_sampler, "get_image_copy", return_value=self.frame if available else None),
            patch.object(PathSamplerDialog, "exec", edit),
        ):
            w._on_path_sample_requested("1c")

    @staticmethod
    def accept(dialog):
        dialog.accept()
        return dialog.result()

    def snapshot(self):
        w = self.window
        return copy.deepcopy((
            w._capture_state(), w._sampling_paths,
            w._undo._undo_stack, w._undo._redo_stack,
            w._current_color_preview_stops, w._current_color_sample_colors,
        ))

    def test_cancel_does_not_add_empty_line_path_state(self):
        self.window._sampling_paths.clear()
        before = self.snapshot()

        def cancel(dialog):
            dialog.reject()
            return dialog.result()

        self.open_path(cancel)
        self.assertEqual(self.snapshot(), before)

    def test_unavailable_frame_keeps_existing_samples_and_history(self):
        w = self.window
        before = self.snapshot()
        with (
            patch.object(w._path_frame_sampler, "load_frame", return_value=False),
            patch.object(w._path_frame_sampler, "load_frame_number", return_value=False),
            patch("gui.main_window.QMessageBox.warning") as warning,
        ):
            self.open_path(self.accept, available=False)
        warning.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_missing_video_does_not_erase_existing_samples(self):
        before = self.snapshot()
        with (
            patch.object(self.window, "_resolve_video_path", return_value=None),
            patch.object(PathSamplerDialog, "exec", self.accept),
            patch("gui.main_window.QMessageBox.warning") as warning,
        ):
            self.window._on_path_sample_requested("1c")
        warning.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_failure_on_copied_tag_is_atomic(self):
        before = self.snapshot()

        def edit(dialog):
            dialog._apply_all_paths_to("2c")
            return self.accept(dialog)

        with (
            patch.object(self.window, "_sample_path_points", side_effect=[self.old_samples, RuntimeError("capture failed")]),
            patch("gui.main_window.QMessageBox.warning") as warning,
        ):
            self.open_path(edit)
        warning.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_copy_then_remove_original_commits_both_in_one_undo_step(self):
        before = self.snapshot()
        undo_count = len(self.window._undo._undo_stack)

        def edit(dialog):
            dialog._apply_all_paths_to("2c")
            dialog._canvas.clear_points()
            return self.accept(dialog)

        self.open_path(edit)
        paths = self.window._sampling_paths[0]
        self.assertTrue(paths["1c"].removed_original)
        self.assertIn("2c", paths)
        self.assertEqual(paths["2c"].sampled_points, sample_path_points_from_path(self.path, lambda x, y: "0000FF"))
        self.assertTrue(self.window._tag_panel.get_row("2c").get_config().enabled)
        self.assertEqual(len(self.window._undo._undo_stack), undo_count + 1)
        after = self.window._capture_state()
        self.window._do_undo()
        self.assertEqual(self.window._capture_state(), before[0])
        self.window._do_redo()
        self.assertEqual(self.window._capture_state(), after)

    def test_removing_path_does_not_require_a_video_frame(self):
        def remove(dialog):
            dialog._remove_path()
            return dialog.result()

        with patch("gui.main_window.QMessageBox.warning") as warning:
            self.open_path(remove, available=False)
        warning.assert_not_called()
        self.assertTrue(self.window._sampling_paths[0]["1c"].removed_original)

    def test_failed_copy_does_not_partially_remove_original(self):
        before = self.snapshot()

        def edit(dialog):
            dialog._apply_all_paths_to("2c")
            dialog._remove_path()
            return dialog.result()

        with patch("gui.main_window.QMessageBox.warning") as warning:
            self.open_path(edit, available=False)
        warning.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_partial_pixel_read_failure_does_not_replace_complete_snapshot(self):
        before = self.snapshot()
        with (
            patch.object(self.frame, "getpixel", side_effect=[(255, 0, 0), OSError("pixel read failed")]),
            patch("gui.main_window.QMessageBox.warning") as warning,
        ):
            self.open_path(self.accept)
        warning.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_empty_capture_does_not_replace_complete_snapshot(self):
        before = self.snapshot()
        with (
            patch.object(self.window, "_sample_path_points", return_value=[]),
            patch("gui.main_window.QMessageBox.warning") as warning,
        ):
            self.open_path(self.accept)
        warning.assert_called_once()
        self.assertEqual(self.snapshot(), before)

    def test_first_save_enables_color_editor_and_tab(self):
        from PySide6.QtCore import QPointF

        w = self.window
        w._sampling_paths.clear()
        w._tag_panel.get_row("1c")._check.setChecked(False)

        def edit(dialog):
            self.assertEqual(dialog._canvas.path_count(), 0)
            dialog._canvas.set_points([QPointF(1, 1), QPointF(8, 1)])
            return self.accept(dialog)

        self.open_path(edit)
        self.assertTrue(w._sampling_paths[0]["1c"].is_active)
        self.assertTrue(w._tag_panel.get_row("1c").get_config().enabled)
        self.assertEqual(w._active_curve_key, "1c")
        self.assertEqual(w._curve_tabs.tabData(w._curve_tabs.currentIndex()), "1c")
        self.assertTrue(w._curve_editor._sample_editor_btn.isEnabled())
        self.assertTrue(w._current_color_preview_stops)
        self.assertEqual(w._curve_editor._sample_preview_count, 8)

    def test_recapture_replaces_edited_colors_and_records_new_frame_with_undo(self):
        w = self.window
        path = w._sampling_paths[0]["1c"]
        path.sampled_points = []
        path.sampled_colors = [(1, "FF0000"), (2, "00FF00")]
        w._update_curve_color_preview()
        w._push_undo("Edited colors")
        before = self.snapshot()
        with patch.object(w, "_current_preview_video_frame", return_value=7):
            self.open_path(self.accept)
        saved = w._sampling_paths[0]["1c"]
        self.assertEqual(saved.sampling_frame, 7)
        self.assertEqual(saved.sampled_colors, [])
        self.assertEqual(saved.sampled_points, sample_path_points_from_path(self.path, lambda x, y: "0000FF"))
        self.assertEqual(w._curve_editor._sample_preview_count, 8)
        after = copy.deepcopy(saved)
        w._do_undo()
        self.assertEqual(w._capture_state(), before[0])
        self.assertEqual(w._curve_editor._sample_preview_count, 2)
        w._do_redo()
        self.assertEqual(w._sampling_paths[0]["1c"], after)
        self.assertEqual(w._curve_editor._sample_preview_count, 8)

    def test_captured_image_is_independent_of_later_shared_frame_changes(self):
        def edit(dialog):
            self.window._path_frame_sampler._image = Image.new("RGB", (10, 10), "blue")
            return self.accept(dialog)

        self.open_path(edit)
        self.assertEqual(self.window._sampling_paths[0]["1c"].sampled_points,
                         sample_path_points_from_path(self.path, lambda x, y: "0000FF"))

    def test_snapshot_sampler_converts_image_modes_without_skipping_pixels(self):
        for image, expected in ((Image.new("RGBA", (10, 10), (1, 2, 3, 4)), "030201"),
                                (Image.new("L", (10, 10), 90), "5A5A5A")):
            with self.subTest(mode=image.mode):
                samples = self.window._sample_path_points(self.path, frame_image=image)
                self.assertEqual(samples, sample_path_points_from_path(self.path, lambda x, y: expected))

    def test_saved_dialog_frame_is_used_even_if_video_file_becomes_unavailable(self):
        w = self.window

        def edit(dialog):
            self.assertEqual(dialog.sampling_frame_number, 0)
            return self.accept(dialog)

        with (
            patch.object(w, "_resolve_video_path", side_effect=["synthetic.mp4", None]),
            patch.object(w, "_load_path_preview_frame", return_value=True),
            patch.object(w._path_frame_sampler, "get_image_copy", return_value=self.frame),
            patch.object(PathSamplerDialog, "exec", edit),
        ):
            w._on_path_sample_requested("1c")
        self.assertEqual(w._sampling_paths[0]["1c"].sampled_points, sample_path_points_from_path(self.path, lambda x, y: "0000FF"))

    def test_screen_picker_revalidates_shared_frame_after_path_editor_load(self):
        w = self.window
        sampler = w._path_frame_sampler

        def load_frame(frame, color):
            sampler._current_video = "synthetic.mp4"
            sampler._current_frame = frame
            sampler._current_time = None
            sampler._image = Image.new("RGB", (10, 10), color)
            return True

        with (
            patch.object(w, "_resolve_video_path", return_value="synthetic.mp4"),
            patch.object(w, "_current_preview_video_frame", return_value=0),
            patch.object(w, "_load_path_preview_frame", side_effect=lambda *_: load_frame(0, "red")) as load,
        ):
            self.assertTrue(w._ensure_color_picker_frame(force_load=True))
            for _ in range(5):
                self.assertTrue(w._ensure_color_picker_frame(force_load=True))
            self.assertEqual(load.call_count, 1)
            load_frame(1, "blue")
            self.assertFalse(w._ensure_color_picker_frame())
            self.assertTrue(w._ensure_color_picker_frame(force_load=True))
            self.assertEqual(sampler.get_pixel_bgr(0, 0), "0000FF")
            self.assertEqual(load.call_count, 2)


if __name__ == "__main__":
    unittest.main()
