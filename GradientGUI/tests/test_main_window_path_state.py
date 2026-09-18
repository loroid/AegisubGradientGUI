"""Path inheritance must not depend on which source rows were visited."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtWidgets import QApplication

from engine.ass_parser import ASSFile, ASSEvent, ASSStyle
from engine.gradient import generate_gradient
from engine.path_model import PathSet
from engine.path_tracer import sample_path_points_from_path
from gui.dependency_health import HealthReport
from gui.main_window import MainWindow
from gui.path_sampler import PathSamplerDialog


class MainWindowPathStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_original_paths_are_ready_before_visiting_each_line(self):
        paths = ["m 0 0 l 3 0", "m 0 20 l 3 20"]
        source = ASSFile(
            styles=[ASSStyle(name="Default")],
            events=[
                ASSEvent(end="0:00:02.00", text=rf"{{\1xlip({path})}}Item {idx}")
                for idx, path in enumerate(paths)
            ],
        )
        with (
            patch("gui.main_window.parse_ass_file", return_value=source),
            patch("gui.main_window.run_startup_health_check", return_value=HealthReport([])),
            patch.object(MainWindow, "_start_update_check"),
        ):
            window = MainWindow(input_path=__file__)
            self.addCleanup(window.deleteLater)
            self.addCleanup(window.close)
            self.addCleanup(window._preview_timer.stop)
            self.addCleanup(window._undo_timer.stop)
            before = window._sampling_paths_for_event(1)
            window._on_active_line_changed(1)
            window._on_active_line_changed(0)
            after = window._sampling_paths_for_event(1)
            self.assertEqual(before, after)
            self.assertEqual(before, {"1c": paths[1]})

    def test_confirming_unchanged_path_keeps_saved_samples_and_generated_output(self):
        from PIL import Image

        source = ASSFile(
            styles=[ASSStyle(name="Default", fontname="Arial", fontsize=12)],
            events=[ASSEvent(end="0:00:01.00", text=r"{\pos(20,10)\bord0}Sample")],
        )
        with (
            patch("gui.main_window.parse_ass_file", return_value=source),
            patch("gui.main_window.run_startup_health_check", return_value=HealthReport([])),
            patch.object(MainWindow, "_start_update_check"),
        ):
            window = MainWindow(input_path=__file__)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        self.addCleanup(window._preview_timer.stop)
        self.addCleanup(window._undo_timer.stop)
        path = "m 10 20 b 10 130 150 130 150 20 n 20 30 l 20 110 170 110"
        frame = Image.new("RGB", (200, 150))
        frame.putdata([(x % 256, y % 256, (x + y) % 256) for y in range(150) for x in range(200)])

        def pixel(x, y):
            r, g, b = frame.getpixel((x, y))
            return f"{b:02X}{g:02X}{r:02X}"

        samples = sample_path_points_from_path(path, pixel)
        window._sampling_paths = {0: {"1c": PathSet.from_ass_path(path, sampling_frame=0, sampled_points=samples)}}
        window._tag_panel.get_row("1c")._check.setChecked(True)
        window._update_curve_color_preview()
        window._push_undo("Initial path")

        def output():
            return [event.to_ass_line() for event in generate_gradient(
                source.events[0], source.styles[0], window._build_settings(),
            )]

        before = output()
        stops = window._current_color_preview_stops

        def confirm(dialog):
            dialog.accept()
            return dialog.result()

        with (
            patch.object(window, "_resolve_video_path", return_value="synthetic.mp4"),
            patch.object(window, "_load_path_preview_frame", return_value=True),
            patch.object(window._path_frame_sampler, "get_image_copy", return_value=frame),
            patch.object(PathSamplerDialog, "exec", confirm),
        ):
            window._on_path_sample_requested("1c")
        self.assertEqual(window._sampling_paths[0]["1c"].sampled_points, samples)
        self.assertEqual(window._current_color_preview_stops, stops)
        self.assertEqual(window._curve_editor._sample_preview_count, len(window._current_color_sample_colors))
        self.assertTrue(window._curve_editor._sample_editor_btn.isEnabled())
        self.assertEqual(output(), before)


if __name__ == "__main__":
    unittest.main()
