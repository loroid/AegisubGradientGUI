"""Curve viewport bounds must not depend on hidden handles or resize history."""

from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from engine.interpolation import CurveNode, InterpolationMode, make_default_nodes
from gui.curve_editor import COLOR_NODE, MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, CurveEditorView
from gui.main_window import MainWindow


class CurveLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self, *, animation=True, width=3839):
        view = CurveEditorView(pin_endpoints_to_range=not animation)
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        if animation:
            view.set_integer_x(True)
            view.set_x_max(243)
            view.set_y_range(-10, 10)
            nodes = MainWindow._default_animation_curve_nodes(
                SimpleNamespace(_animation_curve_x_max=lambda: 243),
            )
        else:
            nodes = make_default_nodes(1, 1)
        view.set_nodes(nodes)
        view.resize(width, view.height())
        view.show()
        self.app.processEvents()
        return view

    def assert_aligned(self, view):
        viewport = view.viewport().rect()
        plot = view.mapFromScene(view.plot_rect()).boundingRect()
        self.assertEqual(view.mapFromScene(QPointF(0, 0)), QPoint(0, 0))
        self.assertEqual(plot.left(), MARGIN_LEFT)
        self.assertEqual(plot.top(), MARGIN_TOP)
        self.assertAlmostEqual(viewport.width() - plot.right(), MARGIN_RIGHT, delta=1)
        self.assertTrue(viewport.contains(plot))
        for node in view._node_items:
            self.assertTrue(viewport.contains(view.mapFromScene(node.sceneBoundingRect()).boundingRect()))
        self.assertEqual(view.horizontalScrollBar().minimum(), view.horizontalScrollBar().maximum())
        self.assertEqual(view.verticalScrollBar().minimum(), view.verticalScrollBar().maximum())

    def test_default_animation_hidden_handles_do_not_shift_viewport(self):
        view = self.view()
        self.assertLess(view.current_nodes()[0].handle_in_x, 0)
        self.assertGreater(view.current_nodes()[-1].handle_out_x, view.x_max())
        self.assert_aligned(view)

    def test_grow_shrink_and_repaint_preserve_geometry_and_emit_no_edit(self):
        for animation in (False, True):
            with self.subTest(animation=animation):
                view = self.view(animation=animation)
                before = copy.deepcopy(view.current_nodes())
                edits = []
                view.curve_changed.connect(lambda: edits.append(True))
                for width in (800, 2048, 3839, 670, 1280):
                    view.resize(width, view.height())
                    self.app.processEvents()
                    view._redraw_all()
                    self.app.processEvents()
                    self.assert_aligned(view)
                    self.assertEqual(view.current_nodes(), before)
                self.assertEqual(edits, [])

    def test_handle_extents_and_range_changes_do_not_scroll_canvas(self):
        view = self.view(width=1200)
        nodes = view.current_nodes()
        nodes[0].handle_in_x = -10000
        nodes[-1].handle_out_x = 10000
        before = copy.deepcopy(nodes)
        view.set_mode(InterpolationMode.BEZIER)
        for x_max in (243, 900, 243):
            view.set_x_max(x_max)
            view.set_active_x_max(200)
            self.app.processEvents()
            self.assert_aligned(view)
            self.assertEqual(view.current_nodes(), before)

    def test_switching_hidden_tab_after_resize_keeps_endpoints_visible(self):
        tabs = QTabWidget()
        self.addCleanup(tabs.deleteLater)
        self.addCleanup(tabs.close)
        tabs.addTab(QWidget(), "Other")
        view = self.view()
        tabs.addTab(view, "Animation")
        tabs.resize(1800, 350)
        tabs.show()
        self.app.processEvents()
        before = copy.deepcopy(view.current_nodes())
        for width in (800, 2048, 1000):
            tabs.setCurrentIndex(0)
            tabs.resize(width, 350)
            self.app.processEvents()
            view.set_x_max(500)
            view.set_x_max(243)
            tabs.setCurrentIndex(1)
            self.app.processEvents()
            self.assert_aligned(view)
            self.assertEqual(view.current_nodes(), before)

    def test_endpoint_pixels_are_visible_after_shrinking(self):
        view = self.view()
        view.resize(800, view.height())
        self.app.processEvents()
        image = view.viewport().grab().toImage()
        dpr = image.devicePixelRatio()
        for item in view._node_items:
            point = view.mapFromScene(item.pos())
            self.assertTrue(view.viewport().rect().contains(point))
            pixel = image.pixelColor(round(point.x() * dpr), round(point.y() * dpr))
            self.assertEqual(pixel.rgb(), COLOR_NODE.rgb())

    def test_endpoint_drag_hits_the_visible_node_after_resize(self):
        view = self.view()
        view.resize(800, view.height())
        self.app.processEvents()
        node = view.current_nodes()[-1]
        before = copy.deepcopy(node)
        start = view.mapFromScene(view._node_items[-1].pos())
        self.assertTrue(view.viewport().rect().contains(start))
        for event_type, position, button, buttons in (
            (QEvent.Type.MouseButtonPress, start, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseMove, start + QPoint(0, -20), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton),
            (QEvent.Type.MouseButtonRelease, start + QPoint(0, -20), Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton),
        ):
            self.app.sendEvent(view.viewport(), QMouseEvent(
                event_type, QPointF(position), QPointF(view.viewport().mapToGlobal(position)),
                button, buttons, Qt.KeyboardModifier.ShiftModifier,
            ))
        self.assertEqual(node.x, before.x)
        self.assertGreater(node.y, before.y)


if __name__ == "__main__":
    unittest.main()
