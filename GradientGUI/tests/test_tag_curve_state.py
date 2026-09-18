"""Real tag controls must not erase curves or mutate generation snapshots."""

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

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from engine.api import generate_gradient
from engine.ass_parser import ASSFile, ASSEvent, ASSStyle
from engine.interpolation import CurveNode, InterpolationMode, mirror_nodes_horizontal
from engine.tag_parser import TAG_INFO, GUI_TAGS
from gui.curve_keys import curve_keys_for_tag, curve_key_tag, curve_key_axis
from gui.dependency_health import HealthReport
from gui.main_window import MainWindow
from gui.state_codec import preset_from_state, state_from_preset_data


class TagCurveStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.source = ASSFile(styles=[ASSStyle(name="Default", fontname="Arial")],
                              events=[ASSEvent(end="0:00:02.00", text="ABCD")])
        with (
            patch("gui.main_window.parse_ass_file", return_value=self.source),
            patch("gui.main_window.run_startup_health_check", return_value=HealthReport([])),
            patch.object(MainWindow, "_start_update_check"),
        ):
            self.window = MainWindow(input_path=__file__)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window._preview_timer.stop)
        self.addCleanup(self.window._undo_timer.stop)

    def seed_curve(self, key):
        w = self.window
        tag = curve_key_tag(key)
        info = TAG_INFO[tag]
        row = w._tag_panel.get_row(tag)
        row._check.setChecked(True)
        w._on_curve_key_selected(key)
        colors = ["0000FF", "00FF00", "FF0000", "00FFFF"]
        nodes = [CurveNode(
            x=x, y=y,
            value_str=colors[i] if info["type"] == "color" else f"TestFont{i}" if info["type"] == "text" else "",
            segment_mode=list(InterpolationMode)[i],
        ) for i, (x, y) in enumerate(zip((0, 25, 70, 100), (12.345678, 30.123456, 8.987654, 60.765432)))]
        w._curve_editor.set_nodes(nodes, info["type"] == "color", info["type"] == "text")
        w._curve_editor.set_mode(InterpolationMode.BEZIER)
        w._on_curve_changed()
        return copy.deepcopy(w._tag_curves[key])

    def assert_curve(self, key, nodes):
        self.assertEqual(self.window._tag_curves[key], nodes, key)
        self.assertEqual(self.window._tag_modes[key], InterpolationMode.BEZIER, key)

    def add_different_line(self):
        w = self.window
        self.source.events[0].text = r"{\pos(100,200)\org(50,75)\fad(100,200)}ABCD"
        self.source.styles.append(ASSStyle(
            name="Alternate", fontname="Courier New", fontsize=40,
            primary_color="&H00123456", scale_x=150, outline=5,
        ))
        event = ASSEvent(
            style="Alternate", start="0:00:01.00", end="0:00:04.00",
            text=r"{\pos(300,400)\org(150,175)\fad(300,400)}EFGH",
        )
        self.source.events.append(event)
        w._source_events.append(event)
        w._bounds.set_source(self.source, w._source_events)
        w._line_panel.set_events(w._source_events)
        w._load_event_tags(0)

    def test_switching_lines_preserves_authored_tag_panel_values_for_every_tag(self):
        w = self.window
        self.add_different_line()
        for tag in GUI_TAGS:
            for key in curve_keys_for_tag(tag):
                self.seed_curve(key)
        expected = {
            tag: [inp.value() for inp in row._inputs]
            for tag, row in w._tag_panel._rows.items()
        }
        curves = copy.deepcopy(w._tag_curves)
        for index in (1, 0):
            w._on_active_line_changed(index)
            for tag, values in expected.items():
                with self.subTest(index=index, tag=tag):
                    self.assertEqual([inp.value() for inp in w._tag_panel.get_row(tag)._inputs], values)
            self.assertEqual(w._tag_curves, curves)

    def test_line_switch_preserves_disabled_authored_values(self):
        w = self.window
        self.add_different_line()
        curve = self.seed_curve("fscx")
        row = w._tag_panel.get_row("fscx")
        values = [inp.value() for inp in row._inputs]
        row._check.setChecked(False)
        w._on_active_line_changed(1)
        self.assertEqual([inp.value() for inp in row._inputs], values)
        row._check.setChecked(True)
        self.assertEqual(w._build_settings().tags["fscx"].nodes, curve)

    def test_unvisited_coordinate_axis_does_not_change_generated_output_on_line_switch(self):
        w = self.window
        self.add_different_line()
        w._mode_combo.setCurrentText("GBC")
        for tag in ("pos:x", "pos:y", "org:x", "org:y", "fad"):
            with self.subTest(tag=tag):
                w._on_active_line_changed(0)
                row = w._tag_panel.get_row(tag)
                row._check.setChecked(True)
                self.assertEqual(w._active_curve_key, curve_keys_for_tag(tag)[0])
                baseline = w._build_settings()
                expected = [event.to_ass_line() for event in generate_gradient(
                    self.source.events[0], self.source.styles[0], baseline,
                )]
                w._on_active_line_changed(1)
                settings = w._build_settings()
                self.assertEqual(settings.tags[tag], baseline.tags[tag])
                actual = [event.to_ass_line() for event in generate_gradient(
                    self.source.events[0], self.source.styles[0], settings,
                )]
                self.assertEqual(actual, expected)

    def test_unconfigured_tags_still_load_current_line_defaults(self):
        w = self.window
        self.add_different_line()
        self.assertEqual(w._tag_panel.get_row("fs")._inputs[0].value(), 48)
        w._on_active_line_changed(1)
        self.assertEqual(w._tag_panel.get_row("fs")._inputs[0].value(), 40)
        self.assertEqual(w._tag_panel.get_row("fn")._inputs[0].value(), "Courier New")

    def test_enable_disable_preserves_curves_for_every_tag(self):
        w = self.window
        for tag in GUI_TAGS:
            with self.subTest(tag=tag):
                expected = {key: self.seed_curve(key) for key in curve_keys_for_tag(tag)}
                row = w._tag_panel.get_row(tag)
                row._check.setChecked(False)
                for key, nodes in expected.items():
                    self.assert_curve(key, nodes)
                row._check.setChecked(True)
                for key, nodes in expected.items():
                    self.assert_curve(key, nodes)

    def test_mirrored_segment_direction_survives_settings_presets_and_undo(self):
        w = self.window
        for key in ("1c", "alpha", "fscx", "pos:x", "pos:y", "fn"):
            with self.subTest(key=key):
                original = self.seed_curve(key)
                expected = mirror_nodes_horizontal(original)
                w._curve_editor._mirror_h_btn.setChecked(True)
                self.assert_curve(key, expected)
                settings = w._build_settings()
                config = settings.tags[curve_key_tag(key)]
                self.assertEqual(config.coord_y_nodes if curve_key_axis(key) == "y" else config.nodes, expected)
                saved = w._capture_state("Mirrored curve")
                w._undo._undo_stack.clear()
                w._undo._redo_stack.clear()
                w._undo.push(saved)
                w._curve_editor._mirror_h_btn.setChecked(False)
                w._undo.push(w._capture_state("Unmirrored curve"))
                w._undo_timer.stop()
                w._show_tag_overview_tab()
                w._do_undo()
                self.assert_curve(key, expected)
                w._restore_state(state_from_preset_data(preset_from_state(saved)))
                self.assert_curve(key, expected)
                self.assertEqual(w._curve_editor.get_nodes(), expected)

    def test_path_smoothing_controls_do_not_reset_manual_color_curves(self):
        for tag in ("1c", "2c", "3c", "4c"):
            with self.subTest(tag=tag):
                original = self.seed_curve(tag)
                row = self.window._tag_panel.get_row(tag)
                row._path_smooth.setChecked(True)
                self.assert_curve(tag, original)
                row._path_smooth_strength.setValue(.3)
                self.assert_curve(tag, original)
                row._path_smooth.setChecked(False)
                self.assert_curve(tag, original)

    def test_color_endpoint_edits_preserve_interior_nodes_and_modes(self):
        for tag in ("1c", "2c", "3c", "4c"):
            with self.subTest(tag=tag):
                expected = self.seed_curve(tag)
                row = self.window._tag_panel.get_row(tag)
                row._inputs[0]._set_color(QColor("#123456"))
                expected[0].value_str = "563412"
                self.assert_curve(tag, expected)
                row._inputs[1]._set_color(QColor("#abcdef"))
                expected[-1].value_str = "EFCDAB"
                self.assert_curve(tag, expected)

    def test_numeric_endpoint_changes_do_not_round_the_other_endpoint(self):
        for tag in ("fscx", "fay", "bord", "alpha"):
            with self.subTest(tag=tag):
                expected = self.seed_curve(tag)
                row = self.window._tag_panel.get_row(tag)
                row._inputs[0]._spin.setValue(27)
                expected[0].y = 27
                self.assert_curve(tag, expected)

    def test_text_endpoint_edits_preserve_curve_and_mode(self):
        expected = self.seed_curve("fn")
        self.window._tag_panel.get_row("fn")._inputs[1]._text.setText("UpdatedFont")
        expected[-1].value_str = "UpdatedFont"
        self.assert_curve("fn", expected)

    def test_coordinate_edits_change_only_one_endpoint_axis(self):
        for tag in ("pos", "org", "fad"):
            for endpoint in (0, 1):
                for axis in ("x", "y"):
                    with self.subTest(tag=tag, endpoint=endpoint, axis=axis):
                        expected = {key: self.seed_curve(key) for key in curve_keys_for_tag(tag)}
                        if tag == "fad":
                            widget = self.window._tag_panel.get_row(tag)._inputs[endpoint]
                            getattr(widget, "_c" + axis).setValue(27)
                        else:
                            widget = self.window._tag_panel.get_row(f"{tag}:{axis}")._inputs[endpoint]
                            widget._spin.setValue(27)
                        expected[f"{tag}:{axis}"][0 if endpoint == 0 else -1].y = 27
                        for key, nodes in expected.items():
                            self.assert_curve(key, nodes)

    def test_editing_inactive_tag_preserves_active_editor(self):
        expected = self.seed_curve("fscx")
        active = self.seed_curve("1c")
        self.window._tag_panel.get_row("fscx")._inputs[1]._spin.setValue(71)
        expected[-1].y = 71
        self.assert_curve("fscx", expected)
        self.assert_curve("1c", active)
        self.assertEqual(self.window._curve_editor.get_nodes(), active)

    def test_row_config_reads_return_independent_values(self):
        row = self.window._tag_panel.get_row("fscx")
        row._inputs[0]._spin.setValue(27)
        stored = copy.deepcopy(row._config)
        first = row.get_config()
        self.assertEqual(row._config, stored)
        first.nodes[0].y = 500
        first.enabled = True
        self.assertEqual(row.get_config().nodes[0].y, 27)
        self.assertFalse(row.get_config().enabled)

    def test_enabled_tag_query_does_not_copy_node_configs(self):
        w = self.window
        w._tag_panel.get_row("fscx")._check.setChecked(True)
        with patch.object(w._tag_panel.get_row("fscx"), "get_config", side_effect=AssertionError("not needed")):
            self.assertEqual(w._tag_panel.get_enabled_tags(), ["fscx"])

    def test_building_settings_does_not_mutate_widgets_or_curves(self):
        w = self.window
        for key in ("1c", "fscx", "pos:x", "pos:y"):
            self.seed_curve(key)
        nodes = copy.deepcopy(w._tag_curves)
        configs = {tag: copy.deepcopy(row._config) for tag, row in w._tag_panel._rows.items()}
        for _ in range(3):
            w._build_settings()
            self.assertEqual(w._tag_curves, nodes)
            self.assertEqual({tag: row._config for tag, row in w._tag_panel._rows.items()}, configs)

    def test_generated_settings_snapshot_is_not_changed_by_later_edits(self):
        w = self.window
        self.seed_curve("fscx")
        self.seed_curve("1c")
        before = w._build_settings()
        expected = copy.deepcopy(before)
        w._tag_panel.get_row("1c")._inputs[0]._set_color(QColor("#123456"))
        w._tag_panel.get_row("fscx")._inputs[1]._spin.setValue(75)
        w._build_settings()
        self.assertEqual(before, expected)

    def test_editing_settings_snapshot_does_not_change_live_curves(self):
        w = self.window
        original = self.seed_curve("1c")
        settings = w._build_settings()
        settings.tags["1c"].nodes[1].value_str = "123456"
        self.assert_curve("1c", original)

    def test_gbc_output_survives_disable_enable_and_preview_builds(self):
        w = self.window
        original = self.seed_curve("1c")
        w._mode_combo.setCurrentText("GBC")
        initial = generate_gradient(self.source.events[0], self.source.styles[0], w._build_settings())
        row = w._tag_panel.get_row("1c")
        row._check.setChecked(False)
        w._build_settings()
        row._check.setChecked(True)
        self.assert_curve("1c", original)
        self.assertEqual(generate_gradient(self.source.events[0], self.source.styles[0], w._build_settings()), initial)

    def test_color_endpoint_edits_from_overviews_keep_the_full_curve(self):
        w = self.window
        for show_overview in (w._show_tag_overview_tab, w._show_animation_overview_tab):
            with self.subTest(page=show_overview.__name__):
                expected = self.seed_curve("1c")
                show_overview()
                w._tag_panel.get_row("1c")._inputs[1]._set_color(QColor("#123456"))
                expected[-1].value_str = "563412"
                self.assert_curve("1c", expected)
                self.assertEqual(w._curve_editor.get_nodes(), expected)

    def test_cancelling_live_color_preview_restores_original_gbc_output(self):
        w = self.window
        original = self.seed_curve("1c")
        w._mode_combo.setCurrentText("GBC")
        initial = generate_gradient(self.source.events[0], None, w._build_settings())
        widget = w._tag_panel.get_row("1c")._inputs[0]
        widget._preview_color(QColor("#123456"))
        preview = generate_gradient(self.source.events[0], None, w._build_settings())
        self.assertNotEqual(preview, initial)
        self.assert_curve("1c", original)
        widget.preview_color_cancelled.emit()
        self.assertEqual(generate_gradient(self.source.events[0], None, w._build_settings()), initial)
        self.assert_curve("1c", original)


if __name__ == "__main__":
    unittest.main()
