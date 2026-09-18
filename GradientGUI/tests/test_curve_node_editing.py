"""Synthetic curve data must survive editing without changing its geometry."""

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

from PySide6.QtWidgets import QApplication, QDialog

from engine.interpolation import CurveNode, InterpolationMode, interpolate, make_default_nodes
from gui.curve_editor import CurveEditorView, CurveEditorWidget
from gui.curve_node_table import CurveNodeTableDialog
from gui.main_window import MainWindow


class CurveNodeEditingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, nodes=None, **kwargs):
        dialog = CurveNodeTableDialog(nodes or make_default_nodes(), **kwargs)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test_explicit_zero_handles_are_not_defaults(self):
        node = CurveNode(
            x=40, y=12, handle_in_x=0, handle_in_y=0,
            handle_out_x=0, handle_out_y=0,
        )
        self.assertEqual(
            (node.handle_in_x, node.handle_in_y, node.handle_out_x, node.handle_out_y),
            (0, 0, 0, 0),
        )

    def test_omitted_handles_keep_defaults(self):
        node = CurveNode(x=40, y=12)
        self.assertEqual(
            (node.handle_in_x, node.handle_in_y, node.handle_out_x, node.handle_out_y),
            (30, 12, 50, 12),
        )

    def test_main_window_node_clone_preserves_zero_handles(self):
        nodes = make_default_nodes(0, 12)
        nodes[0].handle_out_x = nodes[0].handle_out_y = 0
        nodes[1].handle_in_x = nodes[1].handle_in_y = 0
        cloned = MainWindow._clone_nodes(None, nodes)
        self.assertEqual(cloned, nodes)
        self.assertIsNot(cloned[0], nodes[0])
        for x in range(101):
            self.assertEqual(
                interpolate(cloned, x, InterpolationMode.BEZIER),
                interpolate(nodes, x, InterpolationMode.BEZIER),
            )

    def test_mirroring_twice_preserves_handle_geometry(self):
        nodes = make_default_nodes(0, 0)
        nodes[-1].segment_mode = InterpolationMode.SMOOTH
        nodes[0].handle_out_x = 0
        nodes[1].handle_in_x = 100
        view = CurveEditorView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.set_nodes(copy.deepcopy(nodes))
        view.mirror_horizontal()
        view.mirror_horizontal()
        self.assertEqual(view.current_nodes(), nodes)

    def test_loading_notifies_once_but_visual_redraw_does_not_publish_an_edit(self):
        nodes = make_default_nodes(0.123456789123, 1.987654321987)
        nodes[0].handle_in_x = -30
        nodes[0].handle_out_y = 45
        nodes[1].handle_out_x = 135
        nodes[1].handle_in_y = -30
        for animation in (False, True):
            with self.subTest(animation=animation):
                view = CurveEditorView()
                self.addCleanup(view.deleteLater)
                self.addCleanup(view.close)
                if animation:
                    view.set_y_range(-10, 10)
                    view.set_integer_x(True)
                source = copy.deepcopy(nodes)
                if animation:
                    source[0].handle_in_x = 0
                    source[0].handle_out_x = 33
                    source[1].handle_in_x = 67
                    source[1].handle_out_x = 100
                notifications = []
                view.curve_changed.connect(lambda: notifications.append(True))
                view.set_nodes(copy.deepcopy(source))
                self.assertEqual(view.current_nodes(), source)
                self.assertEqual(len(notifications), 1)
                for width in (160, 1024, 300):
                    notifications.clear()
                    view._width = width
                    view._redraw_all()
                    self.assertEqual(view.current_nodes(), source)
                    self.assertEqual(len(notifications), 0)
                self.assertLessEqual(view._y_min, -30)
                self.assertGreaterEqual(view._y_max, 45)

    def test_add_remove_and_batch_operations_each_publish_one_complete_edit(self):
        view = CurveEditorView()
        self.addCleanup(view.deleteLater)
        notifications = []
        view.curve_changed.connect(lambda: notifications.append(copy.deepcopy(view.current_nodes())))
        view.add_node_at(25, 0.25)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(len(notifications[-1]), 3)
        notifications.clear()
        view.add_batch_nodes(3, 40, 10, "fixed", 2, 0, "fixed")
        self.assertEqual(len(notifications), 1)
        self.assertEqual(len(notifications[-1]), 6)
        notifications.clear()
        view.remove_node(view.current_nodes()[1])
        self.assertEqual(len(notifications), 1)
        self.assertEqual(len(notifications[-1]), 5)

    def test_applying_unchanged_nodes_or_mode_does_not_publish_an_edit(self):
        widget = CurveEditorWidget()
        self.addCleanup(widget.deleteLater)
        notifications = []
        widget.curve_changed.connect(lambda: notifications.append(True))
        widget.set_mode(InterpolationMode.BEZIER)
        self.assertEqual(len(notifications), 1)
        notifications.clear()
        widget.set_mode(InterpolationMode.BEZIER)
        widget.set_nodes(copy.deepcopy(widget.get_nodes()))
        widget.set_y_range(-20, 20)
        widget.view._redraw_all()
        self.assertEqual(notifications, [])

    def test_unchanged_node_table_confirmation_does_not_publish_an_edit(self):
        widget = CurveEditorWidget()
        self.addCleanup(widget.deleteLater)
        nodes = copy.deepcopy(widget.get_nodes())
        notifications = []
        widget.curve_changed.connect(lambda: notifications.append(True))
        with patch("gui.curve_editor.CurveNodeTableDialog") as dialog_type:
            dialog_type.return_value.exec.return_value = QDialog.DialogCode.Accepted
            dialog_type.return_value.nodes.return_value = nodes
            widget._open_node_table_dialog()
        self.assertEqual(notifications, [])
        self.assertEqual(widget.get_nodes(), nodes)

    def test_resize_after_blocked_curve_load_does_not_republish_the_loaded_state(self):
        view = CurveEditorView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.show()
        self.app.processEvents()
        nodes = make_default_nodes(3.125, 7.625)
        notifications = []
        view.curve_changed.connect(lambda: notifications.append(copy.deepcopy(view.current_nodes())))
        view.blockSignals(True)
        try:
            view.set_nodes(copy.deepcopy(nodes))
            view.set_mode(InterpolationMode.BEZIER)
        finally:
            view.blockSignals(False)
        for width in (700, 980, 500):
            view.resize(width, view.height())
            self.app.processEvents()
        self.assertEqual(notifications, [])
        self.assertEqual(view.current_nodes(), nodes)
        view.current_nodes()[0].handle_out_y = 4.375
        view.update_curve()
        view.update_curve()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0][0].handle_out_y, 4.375)

    def test_graphics_item_movement_still_updates_curve(self):
        view = CurveEditorView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        nodes = [CurveNode(x=0), CurveNode(x=50, y=5), CurveNode(x=100, y=10)]
        view.set_nodes(nodes)
        item = view._node_items[1]
        item.setPos(view.value_x_to_scene(60), view.value_y_to_scene(7))
        self.assertAlmostEqual(nodes[1].x, 60)
        self.assertAlmostEqual(nodes[1].y, 7)
        item.handle_out.setPos(view.value_x_to_scene(75), view.value_y_to_scene(9))
        self.assertAlmostEqual(nodes[1].handle_out_x, 75)
        self.assertAlmostEqual(nodes[1].handle_out_y, 9)

    def test_loading_nodes_pins_endpoints_without_rewriting_handles(self):
        view = CurveEditorView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        nodes = [CurveNode(x=20, y=2), CurveNode(x=40, y=4)]
        handles = [(n.handle_in_x, n.handle_out_x) for n in nodes]
        view.set_nodes(nodes)
        self.assertEqual([n.x for n in nodes], [0, 100])
        view.set_x_max(80)
        self.assertEqual([n.x for n in nodes], [0, 80])
        self.assertEqual([(n.handle_in_x, n.handle_out_x) for n in nodes], handles)

    def test_table_noop_keeps_numeric_precision(self):
        nodes = make_default_nodes(0.12345678912345, -0.98765432101234)
        nodes[0].handle_out_x = 100 / 3
        nodes[0].handle_out_y = 0.0000000321987654
        nodes[1].handle_in_x = 200 / 3
        nodes[1].handle_in_y = -1.23456789012345
        nodes[0].segment_mode = InterpolationMode.BEZIER
        dialog = self.dialog(nodes)
        dialog._sort_table()
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.nodes(), nodes)
        for x in range(101):
            self.assertEqual(interpolate(dialog.nodes(), x), interpolate(nodes, x))

    def test_table_accepts_explicit_zero_handles(self):
        dialog = self.dialog(make_default_nodes(2, 5))
        for row in range(2):
            for col in (2, 3, 4, 5):
                dialog._table.item(row, col).setText("0")
        dialog.accept()
        for node in dialog.nodes():
            self.assertEqual(
                (node.handle_in_x, node.handle_in_y, node.handle_out_x, node.handle_out_y),
                (0, 0, 0, 0),
            )

    def test_table_rejects_nonfinite_numbers_in_every_numeric_column(self):
        for integer_x, integer_y in ((False, False), (True, False), (True, True)):
            dialog = self.dialog(integer_x=integer_x, integer_y=integer_y)
            for col in range(6):
                item = dialog._table.item(0, col)
                original = item.text()
                for value in ("nan", "NaN", "inf", "-inf", "1e999"):
                    with self.subTest(integer_x=integer_x, integer_y=integer_y, col=col, value=value):
                        item.setText(value)
                        with self.assertRaises(ValueError):
                            dialog._read_table()
                item.setText(original)

    def test_invalid_table_accept_keeps_dialog_open_and_nodes_unchanged(self):
        dialog = self.dialog(integer_y=True)
        original = dialog.nodes()
        dialog._table.item(0, 1).setText("inf")
        with patch("gui.curve_node_table.QMessageBox.warning") as warning:
            dialog.accept()
        warning.assert_called_once()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(dialog.nodes(), original)

    def test_color_table_rejects_non_hex_digits(self):
        dialog = self.dialog(is_color=True)
        for value in ("GGGGGG", "12ZZ56", "+12345", "0x1234", "12#3456", "FFFFF", "FFFFFFF"):
            with self.subTest(value=value):
                dialog._table.item(0, 1).setText(value)
                with self.assertRaises(ValueError):
                    dialog._read_table()

    def test_color_table_normalizes_valid_hex(self):
        dialog = self.dialog(is_color=True)
        dialog._table.item(0, 1).setText("#a1b2c3")
        dialog._table.item(1, 1).setText("00fF12")
        dialog.accept()
        self.assertEqual([node.value_str for node in dialog.nodes()], ["A1B2C3", "00FF12"])

    def test_color_and_text_tables_preserve_unshown_coordinates(self):
        for is_color in (False, True):
            with self.subTest(is_color=is_color):
                nodes = make_default_nodes(0, 1)
                nodes[0].handle_out_x = 100 / 3
                nodes[1].handle_in_y = 0.123456789123
                dialog = self.dialog(nodes, is_color=is_color, is_text=not is_color)
                dialog._sort_table()
                dialog.accept()
                self.assertEqual(dialog.nodes(), nodes)

    def test_excel_paste_keeps_animation_y_fractional(self):
        dialog = self.dialog(x_max=12, integer_x=True)
        dialog._table.setCurrentCell(0, 0)
        clipboard = self.app.clipboard()
        original = clipboard.text()
        self.addCleanup(clipboard.setText, original)
        clipboard.setText(
            "0\t0.125\t0\t0\t4\t-0.375\tbezier\r\n"
            "6.4\t-0.625\t5\t-1.5\t8\t0.25\tlinear\r\n"
            "12\t0.875\t10\t0.875\t14\t0.875\t\r\n"
        )
        dialog._paste_from_clipboard()
        dialog.accept()
        nodes = dialog.nodes()
        self.assertEqual([node.x for node in nodes], [0, 6, 12])
        self.assertEqual([node.y for node in nodes], [0.125, -0.625, 0.875])
        self.assertEqual(nodes[0].segment_mode, InterpolationMode.BEZIER)
        self.assertEqual((nodes[0].handle_in_x, nodes[0].handle_in_y), (0, 0))


if __name__ == "__main__":
    unittest.main()
