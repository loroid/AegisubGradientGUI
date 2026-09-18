"""Exercise curve dragging through viewport mouse events, including groups."""

from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from engine.interpolation import CurveNode, InterpolationMode
from gui.curve_editor import CurveEditorView


class CurveDraggingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self, *, animation=False):
        view = CurveEditorView(pin_endpoints_to_range=not animation)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.set_integer_x(animation)
        view.set_y_range(0, 10)
        view.set_nodes([CurveNode(x=x, y=y) for x, y in ((0, 0), (20, 3), (40, 6), (100, 10))])
        view.resize(670, 310)
        view.show()
        self.app.processEvents()
        for item in view._node_items[1:3]:
            item.setSelected(True)
        return view

    def mouse(self, view, event_type, position, modifiers=Qt.KeyboardModifier.NoModifier):
        viewport = view.viewport()
        event = QMouseEvent(
            event_type, QPointF(position), QPointF(viewport.mapToGlobal(position)),
            Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton,
            modifiers,
        )
        self.app.sendEvent(viewport, event)

    def drag(self, view, delta, modifiers=Qt.KeyboardModifier.NoModifier, during_move=None):
        start = view.mapFromScene(view._node_items[1].pos())
        self.mouse(view, QEvent.Type.MouseButtonPress, start, modifiers)
        try:
            self.mouse(view, QEvent.Type.MouseMove, start + delta, modifiers)
            if during_move:
                during_move()
        finally:
            self.mouse(view, QEvent.Type.MouseButtonRelease, start + delta, modifiers)

    def test_shift_locks_x_for_every_selected_node(self):
        view = self.view()
        before = copy.deepcopy(view.current_nodes())
        self.drag(view, QPoint(30, -20), Qt.KeyboardModifier.ShiftModifier)
        for index in (1, 2):
            self.assertEqual(view.current_nodes()[index].x, before[index].x)
            self.assertGreater(view.current_nodes()[index].y, before[index].y)

    def test_ctrl_locks_y_and_keeps_the_group_selected(self):
        view = self.view()
        before = copy.deepcopy(view.current_nodes())
        self.drag(view, QPoint(30, -20), Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(sum(item.isSelected() for item in view._node_items), 2)
        for index in (1, 2):
            self.assertEqual(view.current_nodes()[index].y, before[index].y)
            self.assertGreater(view.current_nodes()[index].x, before[index].x)

    def test_animation_group_moves_on_integer_frames_even_during_drag(self):
        view = self.view(animation=True)
        def check():
            nodes = view.current_nodes()
            self.assertEqual(nodes[1].x, round(nodes[1].x))
            self.assertEqual(nodes[2].x - nodes[1].x, 20)
        self.drag(view, QPoint(41, 0), during_move=check)
        check()

    def test_every_change_notification_contains_a_complete_group_move(self):
        view = self.view()
        snapshots = []
        view.curve_changed.connect(lambda: snapshots.append(copy.deepcopy(view.current_nodes())))
        self.drag(view, QPoint(30, -20))
        self.assertTrue(snapshots)
        for nodes in snapshots:
            self.assertAlmostEqual(nodes[2].x - nodes[1].x, 20)
            self.assertAlmostEqual(nodes[2].y - nodes[1].y, 3)

    def test_group_stops_at_the_edge_without_squeezing_node_spacing(self):
        view = self.view()
        self.drag(view, QPoint(-400, 0))
        nodes = view.current_nodes()
        self.assertAlmostEqual(nodes[2].x - nodes[1].x, 20)
        self.assertGreaterEqual(nodes[1].x, 0)

    def test_node_drag_translates_its_bezier_handles_with_it(self):
        view = self.view()
        before = copy.deepcopy(view.current_nodes())
        self.drag(view, QPoint(30, -20))
        for index in (1, 2):
            node, old = view.current_nodes()[index], before[index]
            for prefix in ("handle_in", "handle_out"):
                self.assertAlmostEqual(getattr(node, prefix + "_x") - node.x, getattr(old, prefix + "_x") - old.x)
                self.assertAlmostEqual(getattr(node, prefix + "_y") - node.y, getattr(old, prefix + "_y") - old.y)

    def test_range_refresh_during_drag_keeps_mouse_grab_and_selection(self):
        view = self.view(animation=True)
        item = view._node_items[1]
        start = view.mapFromScene(item.pos())
        self.mouse(view, QEvent.Type.MouseButtonPress, start)
        self.mouse(view, QEvent.Type.MouseMove, start + QPoint(41, 0))
        view.set_y_range(-10, 10)
        self.assertIs(view.scene().mouseGrabberItem(), item)
        self.mouse(view, QEvent.Type.MouseMove, start + QPoint(70, 0))
        self.mouse(view, QEvent.Type.MouseButtonRelease, start + QPoint(70, 0))
        expected = copy.deepcopy(view.current_nodes())
        notifications = []
        view.curve_changed.connect(lambda: notifications.append(True))
        self.app.processEvents()
        self.assertFalse(view._drag_active)
        self.assertFalse(view._redraw_pending)
        self.assertEqual(view.current_nodes(), expected)
        self.assertEqual(view.current_nodes()[2].x - view.current_nodes()[1].x, 20)
        self.assertGreater(view.current_nodes()[1].x, 27)
        self.assertEqual(sum(item.isSelected() for item in view._node_items), 2)
        self.assertEqual(notifications, [])

    def test_losing_mouse_grab_does_not_leave_editor_stuck_dragging(self):
        view = self.view()
        start = view.mapFromScene(view._node_items[1].pos())
        self.mouse(view, QEvent.Type.MouseButtonPress, start)
        self.mouse(view, QEvent.Type.MouseMove, start + QPoint(30, -20))
        view.set_y_range(-10, 10)
        view.scene().mouseGrabberItem().ungrabMouse()
        self.app.processEvents()
        self.assertFalse(view._drag_active)
        self.assertFalse(view._redraw_pending)
        self.assertEqual(sum(item.isSelected() for item in view._node_items), 2)

    def test_bezier_handle_drag_obeys_event_modifiers_and_updates_connector(self):
        for modifier in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier):
            with self.subTest(modifier=modifier):
                view = self.view(animation=True)
                view.set_mode(InterpolationMode.BEZIER)
                node = view.current_nodes()[1]
                item = view._node_items[1].handle_out
                original = copy.deepcopy(node)
                start = view.mapFromScene(item.pos())
                self.mouse(view, QEvent.Type.MouseButtonPress, start, modifier)
                self.mouse(view, QEvent.Type.MouseMove, start + QPoint(41, -20), modifier)
                self.mouse(view, QEvent.Type.MouseButtonRelease, start + QPoint(41, -20), modifier)
                self.assertEqual(item.line.line().p2(), item.pos())
                self.assertEqual((node.x, node.y), (original.x, original.y))
                if modifier == Qt.KeyboardModifier.ControlModifier:
                    self.assertEqual(node.handle_out_y, original.handle_out_y)
                    self.assertEqual(node.handle_out_x, round(node.handle_out_x))
                    self.assertGreater(node.handle_out_x, original.handle_out_x)
                else:
                    self.assertEqual(node.handle_out_x, original.handle_out_x)
                    self.assertGreater(node.handle_out_y, original.handle_out_y)

    def test_group_with_pinned_endpoint_keeps_all_x_coordinates(self):
        view = self.view()
        view._node_items[0].setSelected(True)
        self.drag(view, QPoint(30, -20))
        self.assertEqual([node.x for node in view.current_nodes()], [0, 20, 40, 100])

    def test_click_without_motion_preserves_values_and_does_not_publish_an_edit(self):
        view = self.view(animation=True)
        before = copy.deepcopy(view.current_nodes())
        notifications = []
        view.curve_changed.connect(lambda: notifications.append(True))
        self.drag(view, QPoint(0, 0))
        self.assertEqual(view.current_nodes(), before)
        self.assertEqual(notifications, [])

    def test_replacing_curve_during_drag_does_not_edit_the_replacement(self):
        view = self.view(animation=True)
        start = view.mapFromScene(view._node_items[1].pos())
        self.mouse(view, QEvent.Type.MouseButtonPress, start)
        view.set_y_range(-10, 10)
        replacement = [CurveNode(x=0, y=1), CurveNode(x=50, y=2)]
        view.set_nodes(copy.deepcopy(replacement))
        self.mouse(view, QEvent.Type.MouseMove, start + QPoint(41, -20))
        self.mouse(view, QEvent.Type.MouseButtonRelease, start + QPoint(41, -20))
        self.app.processEvents()
        self.assertEqual(view.current_nodes(), replacement)
        self.assertFalse(view._drag_active)
        self.assertFalse(view._redraw_pending)

    def test_handle_losing_mouse_grab_flushes_pending_redraw(self):
        view = self.view(animation=True)
        view.set_mode(InterpolationMode.BEZIER)
        item = view._node_items[1].handle_out
        start = view.mapFromScene(item.pos())
        self.mouse(view, QEvent.Type.MouseButtonPress, start)
        self.mouse(view, QEvent.Type.MouseMove, start + QPoint(41, -20))
        view.set_y_range(-10, 10)
        view.scene().mouseGrabberItem().ungrabMouse()
        expected = copy.deepcopy(view.current_nodes())
        self.app.processEvents()
        self.assertFalse(view._drag_active)
        self.assertFalse(view._redraw_pending)
        self.assertEqual(view.current_nodes(), expected)

    def test_rubber_band_selection_can_be_dragged_as_a_group(self):
        view = self.view()
        view.scene().clearSelection()
        start = view.mapFromScene(view.value_x_to_scene(10), view.value_y_to_scene(2))
        end = view.mapFromScene(view.value_x_to_scene(50), view.value_y_to_scene(7))
        self.mouse(view, QEvent.Type.MouseButtonPress, start)
        self.mouse(view, QEvent.Type.MouseMove, end)
        self.mouse(view, QEvent.Type.MouseButtonRelease, end)
        self.assertEqual([i for i, item in enumerate(view._node_items) if item.isSelected()], [1, 2])
        self.drag(view, QPoint(30, -20))
        self.assertGreater(view.current_nodes()[1].x, 20)
        self.assertAlmostEqual(view.current_nodes()[2].x - view.current_nodes()[1].x, 20)


if __name__ == "__main__":
    unittest.main()
