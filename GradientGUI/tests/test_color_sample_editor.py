"""Sample-result edits must be validated without silently changing colors."""

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

from gui.color_sample_editor import ColorSampleEditorDialog
from engine.api import generate_gradient
from engine.ass_parser import ASSFile, ASSEvent, ASSStyle
from engine.interpolation import interpolate
from engine.path_model import PathSet
from gui.dependency_health import HealthReport
from gui.main_window import MainWindow


class ColorSampleEditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def dialog(self, stops=None):
        dialog = ColorSampleEditorDialog("1c", stops or [
            (0, "0000FF"), (.25, "00FF00"), (.5, "FF0000"), (1, "FFFFFF"),
        ])
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test_invalid_positions_and_colors_reject_both_accept_actions(self):
        for column, values in (
            (0, ("nan", "inf", "-inf", "1e999", "text", "", "-0.01", "1.01")),
            (1, ("GGGGGG", "0x1234", "FFFFFF0", "FFFFF", "+12345", "")),
        ):
            for action in ("_accept_confirm", "_accept_as_curve"):
                for value in values:
                    with self.subTest(column=column, action=action, value=value):
                        dialog = self.dialog()
                        before = copy.deepcopy(dialog._stops)
                        dialog._table.item(1, column).setText(value)
                        with patch("gui.color_sample_editor.QMessageBox.warning") as warning:
                            getattr(dialog, action)()
                        warning.assert_called_once()
                        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
                        self.assertEqual(dialog._stops, before)
                        self.assertEqual(dialog._table.item(1, column).text(), value)

    def test_structural_edits_reject_invalid_draft_without_partially_changing_rows(self):
        for action in ("_delete_selected", "_insert_after_selected", "_merge_similar", "_reverse"):
            with self.subTest(action=action):
                dialog = self.dialog()
                before = copy.deepcopy(dialog._stops)
                dialog._table.item(1, 1).setText("INVALID")
                dialog._table.selectRow(0)
                rows = [[dialog._table.item(r, c).text() for c in (0, 1)] for r in range(4)]
                with patch("gui.color_sample_editor.QMessageBox.warning") as warning:
                    getattr(dialog, action)()
                warning.assert_called_once()
                self.assertEqual(dialog._stops, before)
                self.assertEqual(dialog._table.rowCount(), 4)
                self.assertEqual([[dialog._table.item(r, c).text() for c in (0, 1)] for r in range(4)], rows)

    def test_unedited_positions_keep_full_precision(self):
        stops = [(0, "0000FF"), (1 / 7, "00FF00"), (100 / 197, "FF0000"), (1, "FFFFFF")]
        dialog = self.dialog(stops)
        for row in range(dialog._table.rowCount()):
            text_width = dialog._table.fontMetrics().horizontalAdvance(dialog._table.item(row, 0).text())
            self.assertGreaterEqual(dialog._table.columnWidth(0), text_width + 4)
        dialog.accept()
        self.assertEqual(dialog.stops(), stops)

    def test_invalid_selected_row_can_be_deleted_to_correct_the_draft(self):
        dialog = self.dialog()
        dialog._table.item(1, 0).setText("nan")
        dialog._table.item(1, 1).setText("INVALID")
        dialog._table.selectRow(1)
        with patch("gui.color_sample_editor.QMessageBox.warning") as warning:
            dialog._delete_selected()
            dialog.accept()
        warning.assert_not_called()
        self.assertEqual(dialog.sampled_colors(), [(1, "0000FF"), (2, "FF0000"), (3, "FFFFFF")])

    def test_valid_color_text_updates_the_swatch_and_normalizes_on_accept(self):
        dialog = self.dialog()
        dialog._table.item(1, 1).setText("#a1B2c3")
        self.assertIn("rgb(195, 178, 161)", dialog._table.cellWidget(1, 2).styleSheet())
        dialog.accept()
        self.assertEqual(dialog.sampled_colors()[1], (2, "A1B2C3"))

    def test_insertion_follows_the_selected_color_after_positions_are_edited(self):
        dialog = self.dialog()
        dialog._table.item(0, 0).setText(".75")
        dialog._table.selectRow(0)
        dialog._insert_after_selected()
        dialog.accept()
        self.assertEqual(dialog.stops(), [
            (.25, "00FF00"), (.5, "FF0000"), (.75, "0000FF"), (.875, "0000FF"), (1, "FFFFFF"),
        ])

    def test_appending_a_color_keeps_distinct_positions_for_curve_conversion(self):
        dialog = self.dialog()
        dialog._table.selectRow(3)
        dialog._insert_after_selected()
        dialog._table.item(4, 1).setText("123456")
        dialog._accept_as_curve()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        stops = dialog.stops()
        self.assertEqual(len(stops), 5)
        self.assertEqual(len({pos for pos, _ in stops}), 5)
        self.assertEqual([color for _, color in stops], ["0000FF", "00FF00", "FF0000", "FFFFFF", "123456"])

    def test_duplicate_positions_cannot_silently_drop_a_color_during_conversion(self):
        dialog = self.dialog()
        dialog._table.item(2, 0).setText(".25")
        with patch("gui.color_sample_editor.QMessageBox.warning") as warning:
            dialog._accept_as_curve()
        warning.assert_called_once()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_cancelling_edits_does_not_mutate_the_supplied_samples(self):
        stops = [(0, "0000FF"), (.5, "00FF00"), (1, "FF0000")]
        before = copy.deepcopy(stops)
        dialog = self.dialog(stops)
        dialog._reverse()
        dialog._table.item(1, 1).setText("123456")
        dialog.reject()
        self.assertEqual(stops, before)

    def test_curve_conversion_preserves_distinct_nearby_positions_and_edge_colors(self):
        stops = [(0.000001, "0000FF"), (.5, "00FF00"), (.500001, "FF0000"), (.999999, "FFFFFF")]
        nodes = MainWindow._color_stops_to_nodes(None, stops)
        self.assertEqual([node.x for node in nodes], [0] + [pos * 100 for pos, _ in stops] + [100])
        self.assertEqual([node.value_str for node in nodes], ["0000FF", "0000FF", "00FF00", "FF0000", "FFFFFF", "FFFFFF"])


class ColorSampleWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.source = ASSFile(
            styles=[ASSStyle(name="Default", fontname="Arial", fontsize=12)],
            events=[ASSEvent(end="0:00:01.00", text=r"{\pos(20,10)\bord0}Sample")],
        )
        with (
            patch("gui.main_window.parse_ass_file", return_value=self.source),
            patch("gui.main_window.run_startup_health_check", return_value=HealthReport([])),
            patch.object(MainWindow, "_start_update_check"),
        ):
            self.window = MainWindow(input_path=__file__)
        w = self.window
        self.addCleanup(w.deleteLater)
        self.addCleanup(w.close)
        self.addCleanup(w._preview_timer.stop)
        self.addCleanup(w._undo_timer.stop)
        self.colors = ["0000FF", "00FF00", "FF0000", "FFFFFF"]
        w._sampling_paths = {0: {"1c": PathSet.from_ass_path(
            "m 0 0 l 3 0", sampling_frame=0,
            sampled_points=[(0, i, 0, color) for i, color in enumerate(self.colors)],
        )}}
        w._tag_panel.get_row("1c")._check.setChecked(True)
        w._refresh_path_buttons()
        w._update_curve_color_preview()
        w._push_undo("Initial samples")

    def output(self):
        return [event.to_ass_line() for event in generate_gradient(
            self.source.events[0], self.source.styles[0], self.window._build_settings(),
        )]

    def test_confirm_deletion_updates_count_output_and_undo_redo(self):
        w = self.window
        before = self.output()

        def edit(dialog):
            dialog._table.selectRow(1)
            dialog._delete_selected()
            dialog.accept()
            return dialog.result()

        with patch.object(ColorSampleEditorDialog, "exec", edit):
            w._edit_sampled_colors()
        path = w._sampling_paths[0]["1c"]
        self.assertEqual(path.sampled_points, [])
        self.assertEqual(path.sampled_colors, [(1, "0000FF"), (2, "FF0000"), (3, "FFFFFF")])
        self.assertEqual(w._curve_editor._sample_preview_count, 3)
        self.assertTrue(w._curve_editor._sample_editor_btn.isEnabled())
        after = self.output()
        self.assertEqual((len(before), len(after)), (4, 3))
        w._do_undo()
        self.assertEqual(w._curve_editor._sample_preview_count, 4)
        self.assertEqual(self.output(), before)
        w._do_redo()
        self.assertEqual(w._curve_editor._sample_preview_count, 3)
        self.assertEqual(self.output(), after)

    def test_cancel_keeps_path_output_and_history(self):
        w = self.window
        before = w._capture_state()
        output = self.output()
        history = copy.deepcopy((w._undo._undo_stack, w._undo._redo_stack))

        def edit(dialog):
            dialog._reverse()
            dialog._table.item(0, 1).setText("123456")
            dialog.reject()
            return dialog.result()

        with patch.object(ColorSampleEditorDialog, "exec", edit):
            w._edit_sampled_colors()
        self.assertEqual(w._capture_state(), before)
        self.assertEqual(self.output(), output)
        self.assertEqual((w._undo._undo_stack, w._undo._redo_stack), history)

    def test_apply_curve_keeps_inserted_color_and_current_mirror_direction(self):
        w = self.window
        w._curve_editor._mirror_h_btn.setChecked(True)

        def edit(dialog):
            dialog._table.selectRow(dialog._table.rowCount() - 1)
            dialog._insert_after_selected()
            dialog._table.item(dialog._table.rowCount() - 1, 1).setText("123456")
            dialog._accept_as_curve()
            return dialog.result()

        with patch.object(ColorSampleEditorDialog, "exec", edit):
            w._edit_sampled_colors()
        self.assertFalse(w._sampling_paths[0]["1c"].is_active)
        self.assertFalse(w._curve_editor._sample_editor_btn.isEnabled())
        nodes = w._curve_editor.get_nodes()
        self.assertEqual([node.value_str for node in nodes], list(reversed(self.colors + ["123456"])))
        self.assertTrue(w._curve_editor.mirror_state()[0])
        self.assertEqual(w._build_settings().tags["1c"].nodes, nodes)
        self.assertEqual(interpolate(nodes, 0, is_color=True), "123456")
        self.assertEqual(interpolate(nodes, 100, is_color=True), "0000FF")


if __name__ == "__main__":
    unittest.main()
