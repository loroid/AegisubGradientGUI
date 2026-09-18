"""Path editing must preserve the geometry used for saved pixel sampling."""

from __future__ import annotations

import os
import random
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsPathItem

from engine.path_tracer import sample_path_points_from_path, trace_path_points
from gui.path_geometry import EditablePath, parse_editable_paths
from gui.path_sampler import PathSamplerDialog


class PathSamplerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, path=""):
        dialog = PathSamplerDialog("1c", initial_path=path, canvas_size=(200, 150))
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def assert_samples_equal(self, before, after):
        def pixel(x, y):
            return f"{(x * 17 + y * 13) % 256:02X}{y % 256:02X}{x % 256:02X}"

        self.assertEqual(trace_path_points(before), trace_path_points(after))
        self.assertEqual(
            sample_path_points_from_path(before, pixel),
            sample_path_points_from_path(after, pixel),
        )

    def test_reopen_save_preserves_bezier_line_and_mixed_paths(self):
        cases = (
            "m 10 20 b 10 130 150 130 150 20",
            "m 10 20 l 10 130 150 130",
            "m 10.49999 20.50001 b 80.1234567 130.7654321 120.9 30.7 150.6 20.8 l 170 80",
            "m 10 20 b 10 130 150 130 150 20 n 20 30 l 20 110 170 110",
        )
        for initial in cases:
            with self.subTest(path=initial):
                dialog = self.dialog(initial)
                dialog.accept()
                self.assert_samples_equal(initial, dialog.path())

    def test_canvas_renders_the_imported_controls(self):
        dialog = self.dialog("m 10 20 b 10 130 150 130 150 20")
        item = next(item for item in dialog._canvas._items if isinstance(item, QGraphicsPathItem))
        self.assertEqual(item.path().pointAtPercent(0.5), QPointF(80, 102.5))

    def test_copy_current_and_all_paths_preserves_sampling(self):
        paths = ["m 10 20 b 10 130 150 130 150 20", "n 20 30 l 20 110 170 110"]
        dialog = self.dialog(" ".join(paths))
        dialog._canvas.set_active_path(1)
        dialog._apply_current_path_to("2c")
        dialog._apply_all_paths_to("3c")
        self.assert_samples_equal(paths[1], dialog.applied_paths["2c"])
        self.assert_samples_equal(" ".join(paths), dialog.applied_paths["3c"])
        dialog._canvas.remove_current_path()
        self.assert_samples_equal(paths[0], dialog.path())

    def test_no_path_is_created_when_opening_empty_editor(self):
        dialog = self.dialog()
        self.assertEqual(dialog._canvas.path_count(), 0)
        dialog.accept()
        self.assertEqual(dialog.path(), "")

    def mouse(self, view, kind, position):
        viewport = view.viewport()
        event = QMouseEvent(
            kind, QPointF(position), QPointF(viewport.mapToGlobal(position)),
            Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton if kind == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.app.sendEvent(viewport, event)

    def test_point_click_without_movement_preserves_fractional_geometry(self):
        initial = "m 10.49999 20.50001 b 10 130 150 130 150 20"
        dialog = self.dialog(initial)
        dialog.show()
        self.app.processEvents()
        view = dialog._canvas
        position = view.mapFromScene(view.points()[0]) + QPoint(3, 2)
        for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease):
            self.mouse(view, kind, position)
        self.assert_samples_equal(initial, dialog.path())

    def test_drag_moves_only_adjacent_handles_and_preserves_other_paths(self):
        paths = ["m 10 20 b 10 130 80 130 80 20 b 80 100 150 100 150 20", "n 10 110 l 150 110"]
        dialog = self.dialog(" ".join(paths))
        dialog.show()
        self.app.processEvents()
        view = dialog._canvas
        before = view._paths[0].points[1]
        start = view.mapFromScene(before) + QPoint(2, 1)
        end = start + QPoint(30, 20)
        delta = view.mapToScene(end) - view.mapToScene(start)
        self.mouse(view, QEvent.Type.MouseButtonPress, start)
        self.mouse(view, QEvent.Type.MouseMove, end)
        self.mouse(view, QEvent.Type.MouseButtonRelease, end)
        geometry = view._paths[0]
        self.assertEqual(geometry.points[1], before + delta)
        self.assertEqual(geometry.controls[0], (QPointF(10, 130), QPointF(80, 130) + delta))
        self.assertEqual(geometry.controls[1], (QPointF(80, 100) + delta, QPointF(150, 100)))
        self.assertEqual(geometry.points[0], QPointF(10, 20))
        self.assertEqual(geometry.points[2], QPointF(150, 20))
        self.assert_samples_equal(paths[1], view.path_strings()[1])
        self.assert_samples_equal(dialog.path(), self.dialog(dialog.path()).path())

    def test_append_delete_and_new_path_do_not_reshape_existing_edges(self):
        initial = "m 10 20 b 10 130 150 130 150 20"
        dialog = self.dialog(initial)
        view = dialog._canvas
        view._paths[0].append_point(QPointF(175, 100))
        self.assertEqual(view._paths[0].controls[0], (QPointF(10, 130), QPointF(150, 130)))
        view.undo_point()
        self.assert_samples_equal(initial, dialog.path())
        view.add_path()
        view._paths[1].append_point(QPointF(15, 80))
        view._paths[1].append_point(QPointF(30, 40))
        view._paths[1].append_point(QPointF(60, 80))
        self.assertEqual(view.path_count(), 2)
        self.assertEqual(view.total_point_count(), 5)
        self.assert_samples_equal(initial, view.path_strings()[0])
        self.assert_samples_equal(view.ass_path(), self.dialog(view.ass_path()).path())
        view.remove_current_path()
        self.assert_samples_equal(initial, dialog.path())
        view.undo_point()
        self.assertFalse(view.has_valid_path())
        view.clear_points()
        self.assertEqual(view.ass_path(), "")

    def test_deleting_anchor_keeps_path_topology_and_remaining_handles(self):
        cases = (
            ("m 10 20 l 40 40 80 20", "m 10 20 l 80 20"),
            ("m 10 20 b 15 40 30 50 40 20 b 50 70 60 90 80 20", "m 10 20 b 15 40 60 90 80 20"),
        )
        for initial, expected in cases:
            with self.subTest(initial=initial):
                path = parse_editable_paths(initial)[0]
                path.remove_point(1)
                self.assert_samples_equal(expected, path.to_ass_path())
                path.remove_point(0)
                self.assertEqual(len(path.controls), 0)
                self.assertEqual(path.points, [QPointF(80, 20)])
                path.remove_point(0)
                self.assertEqual(path.to_ass_path(), "")
                path.append_point(QPointF(10, 10))
                path.append_point(QPointF(30, 30))
                self.assertEqual(len(path.controls), 1)

    def test_random_mixed_paths_keep_exact_samples_and_canvas_geometry(self):
        rng = random.Random(412)
        for index in range(30):
            parts = ["m", str(rng.uniform(0, 150)), str(rng.uniform(0, 100))]
            for _ in range(5):
                count = rng.choice((2, 6))
                parts.append("l" if count == 2 else "b")
                parts.extend(str(rng.uniform(0, 100)) for _ in range(count))
            with self.subTest(index=index):
                initial = " ".join(parts)
                path = parse_editable_paths(initial)[0]
                for _ in range(3):
                    saved = path.to_ass_path()
                    reloaded = parse_editable_paths(saved)[0]
                    self.assertEqual(path.painter_path(), reloaded.painter_path())
                    self.assert_samples_equal(initial, saved)
                    path = reloaded

    def test_automatic_new_curve_and_reloaded_preview_match(self):
        points = [QPointF(10.001, 20.49999), QPointF(80, 130), QPointF(150, 20)]
        path = EditablePath(points)
        reloaded = parse_editable_paths(path.to_ass_path())[0]
        self.assertEqual(path.painter_path(), reloaded.painter_path())
        self.assertNotIn("e-", EditablePath([QPointF(1e-9, 0)]).to_ass_path())
        points[0].setX(99)
        self.assertEqual(path.points[0].x(), 10.001)

    def test_drag_away_and_back_keeps_exact_handles(self):
        initial = "m 10.49999 20.50001 b 10.23456 130.76543 150 130 150 20"
        dialog = self.dialog(initial)
        dialog.show()
        self.app.processEvents()
        view = dialog._canvas
        before = dialog.path()
        start = view.mapFromScene(view.points()[0])
        self.mouse(view, QEvent.Type.MouseButtonPress, start)
        for delta in (QPoint(20, 12), QPoint(43, 22), QPoint()):
            self.mouse(view, QEvent.Type.MouseMove, start + delta)
        self.mouse(view, QEvent.Type.MouseButtonRelease, start)
        self.assertEqual(dialog.path(), before)

    def test_view_changes_and_path_selection_do_not_change_sampling(self):
        from PIL import Image

        initial = "m 10.49999 20.50001 b 10 130 150 130 150 20 n 20 30 l 20 110 170 110"
        dialog = self.dialog(initial)
        dialog.show()
        self.app.processEvents()
        dialog._canvas.scale(2, 2)
        dialog._canvas.horizontalScrollBar().setValue(50)
        dialog._canvas.verticalScrollBar().setValue(30)
        dialog._canvas.set_active_path(1)
        dialog.set_frame_image(Image.new("RGB", (200, 150), "white"), frame_number=13)
        dialog._canvas.reset_view()
        dialog.resize(1000, 750)
        self.app.processEvents()
        dialog.accept()
        self.assert_samples_equal(initial, dialog.path())
        self.assertEqual(dialog.sampling_frame_number, 13)


if __name__ == "__main__":
    unittest.main()
