"""Y-axis wheel zoom is a viewport operation, never a curve edit."""

from __future__ import annotations

import copy
import math
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QApplication

from engine.interpolation import CurveNode, InterpolationMode
from gui.curve_editor import CurveEditorView, MIN_Y_VIEW_SPAN, MAX_Y_VIEW_SPAN


class CurveYZoomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self, *, animation=False):
        view = CurveEditorView(pin_endpoints_to_range=not animation)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.set_integer_x(animation)
        if animation:
            view.set_y_range(-10, 10)
        view.set_nodes([
            CurveNode(x=0, y=0, handle_in_x=0, handle_in_y=0,
                      handle_out_x=20, handle_out_y=0.2),
            CurveNode(x=50, y=0.5, handle_in_x=30, handle_in_y=0.3,
                      handle_out_x=70, handle_out_y=0.8),
            CurveNode(x=100, y=1, handle_in_x=80, handle_in_y=0.9,
                      handle_out_x=100, handle_out_y=1),
        ])
        view.set_mode(InterpolationMode.BEZIER)
        view.resize(800, view.height())
        view.show()
        self.app.processEvents()
        view._node_items[1].setSelected(True)
        return view

    def wheel(self, view, delta, *, pos=None, ctrl=True, pixel=False):
        if pos is None:
            pos = view.mapFromScene(view.plot_rect().center())
        event = QWheelEvent(
            QPointF(pos), QPointF(view.viewport().mapToGlobal(pos)),
            QPoint(0, delta) if pixel else QPoint(),
            QPoint() if pixel else QPoint(0, delta),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
        self.app.sendEvent(view.viewport(), event)
        self.app.processEvents()
        return event

    def mouse(self, view, event_type, position, modifiers=Qt.KeyboardModifier.NoModifier):
        event = QMouseEvent(
            event_type, QPointF(position), QPointF(view.viewport().mapToGlobal(position)),
            Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton,
            modifiers,
        )
        self.app.sendEvent(view.viewport(), event)

    def test_wheel_zoom_changes_only_y_view_and_preserves_selection(self):
        for animation in (False, True):
            with self.subTest(animation=animation):
                view = self.view(animation=animation)
                original = copy.deepcopy(view.current_nodes())
                x_positions = [item.pos().x() for item in view._node_items]
                rect = view.sceneRect()
                span = view._y_max - view._y_min
                edits = []
                view.curve_changed.connect(lambda: edits.append(True))
                event = self.wheel(view, -120)
                self.assertTrue(event.isAccepted())
                self.assertAlmostEqual(view._y_max - view._y_min, span * 1.2)
                self.assertEqual(view.current_nodes(), original)
                self.assertEqual([item.pos().x() for item in view._node_items], x_positions)
                self.assertEqual(view.sceneRect(), rect)
                self.assertEqual(view.mapFromScene(QPointF(0, 0)), QPoint(0, 0))
                self.assertEqual([i for i, item in enumerate(view._node_items) if item.isSelected()], [1])
                self.assertEqual(edits, [])

    def test_mouse_y_value_stays_anchored_and_opposite_wheel_restores_range(self):
        view = self.view()
        rect = view.plot_rect()
        pos = view.mapFromScene(QPointF(rect.center().x(), rect.top() + rect.height() * 0.25))
        original_range = (view._y_min, view._y_max)
        anchor = view.scene_y_to_value(view.mapToScene(pos).y())
        self.wheel(view, -240, pos=pos)
        self.assertAlmostEqual(view.scene_y_to_value(view.mapToScene(pos).y()), anchor)
        self.wheel(view, 240, pos=pos)
        self.assertAlmostEqual(view._y_min, original_range[0])
        self.assertAlmostEqual(view._y_max, original_range[1])

    def test_zoom_out_allows_bezier_handle_drag_beyond_original_y_range(self):
        view = self.view()
        old_max = view._y_max
        self.wheel(view, -720)
        zoom_range = (view._y_min, view._y_max)
        item = view._node_items[1].handle_out
        node = item.node_item.curve_node
        original = copy.copy(node)
        start = view.mapFromScene(item.pos())
        end = view.mapFromScene(QPointF(item.pos().x(), view.value_y_to_scene(1.8)))
        for event_type, pos in ((QEvent.Type.MouseButtonPress, start),
                                (QEvent.Type.MouseMove, end),
                                (QEvent.Type.MouseButtonRelease, end)):
            self.mouse(view, event_type, pos, Qt.KeyboardModifier.ShiftModifier)
        self.assertGreater(node.handle_out_y, old_max)
        self.assertAlmostEqual(node.handle_out_y, 1.8, delta=0.02)
        self.assertEqual((node.x, node.y, node.handle_out_x),
                         (original.x, original.y, original.handle_out_x))
        view.recalculate_bounds()
        view._redraw_all()
        self.assertEqual((view._y_min, view._y_max), zoom_range)

    def test_animation_range_refresh_and_resize_do_not_cancel_zoom(self):
        view = self.view(animation=True)
        self.wheel(view, -360)
        zoom_range = (view._y_min, view._y_max)
        original = copy.deepcopy(view.current_nodes())
        for width in (670, 2048, 800):
            view.set_y_range(-10, 10)
            view.resize(width, view.height())
            self.app.processEvents()
            self.assertEqual((view._y_min, view._y_max), zoom_range)
            self.assertEqual(view.current_nodes(), original)
            self.assertEqual(view.mapFromScene(QPointF(0, 0)), QPoint(0, 0))

    def test_wheel_during_node_drag_does_not_destroy_mouse_grabber(self):
        view = self.view()
        item = view._node_items[1]
        pos = view.mapFromScene(item.pos())
        original_range = (view._y_min, view._y_max)
        self.mouse(view, QEvent.Type.MouseButtonPress, pos)
        self.wheel(view, -120)
        self.assertIs(view.scene().mouseGrabberItem(), item)
        self.assertEqual((view._y_min, view._y_max), original_range)
        self.mouse(view, QEvent.Type.MouseButtonRelease, pos)
        self.assertFalse(view._drag_active)

    def test_plain_wheel_and_color_or_text_curves_do_not_zoom(self):
        view = self.view()
        original_range = (view._y_min, view._y_max)
        self.wheel(view, -120, ctrl=False)
        self.assertEqual((view._y_min, view._y_max), original_range)
        for color, text in ((True, False), (False, True)):
            view.set_tag_type(color, text)
            view.recalculate_bounds()
            original = copy.deepcopy(view.current_nodes())
            event = self.wheel(view, -120)
            self.assertTrue(event.isAccepted())
            self.assertEqual((view._y_min, view._y_max), (0, 1))
            self.assertEqual(view.current_nodes(), original)

    def test_ctrl_double_click_resets_zoom_without_inserting_a_node_or_edit(self):
        view = self.view()
        original_range = (view._y_min, view._y_max)
        original = copy.deepcopy(view.current_nodes())
        edits = []
        view.curve_changed.connect(lambda: edits.append(True))
        self.wheel(view, -480)
        pos = view.mapFromScene(view.plot_rect().center())
        self.mouse(view, QEvent.Type.MouseButtonDblClick, pos, Qt.KeyboardModifier.ControlModifier)
        self.assertIsNone(view._manual_y_range)
        self.assertEqual((view._y_min, view._y_max), original_range)
        self.assertEqual(view.current_nodes(), original)
        self.assertEqual(edits, [])

    def test_new_curve_uses_its_own_auto_range(self):
        view = self.view()
        self.wheel(view, -480)
        view.set_nodes([CurveNode(x=0, y=100), CurveNode(x=100, y=200)])
        self.assertIsNone(view._manual_y_range)
        self.assertLessEqual(view._y_min, 100)
        self.assertGreaterEqual(view._y_max, 200)

    def test_pixel_delta_is_supported_and_zoom_limits_stay_finite(self):
        view = self.view()
        span = view._y_max - view._y_min
        self.wheel(view, -60, pixel=True)
        self.assertGreater(view._y_max - view._y_min, span)
        original = copy.deepcopy(view.current_nodes())
        for delta in (-2400, 2400):
            for _ in range(20):
                self.wheel(view, delta)
                self.assertTrue(math.isfinite(view._y_min) and math.isfinite(view._y_max))
                self.assertGreater(view._y_max, view._y_min)
                self.assertLessEqual(view._y_max - view._y_min, MAX_Y_VIEW_SPAN * 1.000001)
            self.assertGreaterEqual(view._y_max - view._y_min, MIN_Y_VIEW_SPAN * 0.999999)
        self.assertEqual(view.current_nodes(), original)


if __name__ == "__main__":
    unittest.main()
