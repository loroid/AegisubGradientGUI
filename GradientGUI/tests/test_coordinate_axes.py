"""Independent position/origin controls must still emit paired ASS tags."""

from __future__ import annotations

import copy
import os
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from engine.ass_parser import ASSFile, ASSEvent, ASSStyle
from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.interpolation import InterpolationMode, make_default_nodes
from engine.libass_bounds import measure_event_bounds
from engine.models import AnimationSettings
from engine.range_calc import event_with_interpolated_geometry
from engine.tag_parser import COORD_AXIS_TAGS, parse_tags_from_text
from gui.dependency_health import HealthReport
from gui.gradient_generation import generate_gradient_events
from gui.group_range_dialog import GroupRangeSettingsDialog
from gui.i18n import language, set_language
from gui.main_window import MainWindow
from gui.state_codec import (
    preset_from_state, serialize_nodes, split_coordinate_state, state_from_preset_data,
)
from gui.undo_manager import UndoState


def config(tag, start, end, mode=InterpolationMode.LINEAR):
    return TagGradientConfig(tag=tag, enabled=True,
                             nodes=make_default_nodes(start_y=start, end_y=end), mode=mode)


class CoordinateAxisEngineTest(unittest.TestCase):
    def setUp(self):
        self.style = ASSStyle(name="Default", fontname="Arial", fontsize=28, outline=2, shadow=0)
        self.event = ASSEvent(end="0:00:00.40", text=r"{\pos(320,180)\org(310,175)}Axis test")
        self.ass = ASSFile(script_info={"PlayResX": "640", "PlayResY": "360"},
                           styles=[self.style], events=[self.event])

    def settings(self, *configs, mode=GradientMode.HORIZONTAL, angle=0):
        return GradientSettings(mode=mode, angle=angle, step=4,
                                text_x1=260, text_y1=145, text_x2=380, text_y2=190,
                                tags={cfg.tag: cfg for cfg in configs})

    def generate(self, settings, event=None, *, measured=False):
        event = event or self.event
        bounds = measure_event_bounds(self.ass, event) if measured else None
        if measured:
            self.assertIsNotNone(bounds)
        return generate_gradient(event, self.style, settings,
                                 bounds.to_meta() if bounds else None, self.ass)

    def test_each_axis_preserves_other_coordinate_per_source_line(self):
        for tag, (parent, axis) in COORD_AXIS_TAGS.items():
            for mode in GradientMode:
                for source in (self.event, replace(self.event, text=r"{\pos(300,210)\org(290,195)}Other")):
                    with self.subTest(tag=tag, mode=mode, source=source.text):
                        values = parse_tags_from_text(source.text)
                        settings = self.settings(config(tag, 50, 90), mode=mode, angle=30)
                        output = self.generate(settings, source)
                        self.assertTrue(output)
                        for event in output:
                            pair = parse_tags_from_text(event.text)[parent]
                            self.assertEqual(pair[1-axis], values[parent][1-axis])
                            self.assertNotIn(f"\\{parent}:", event.text)
                            if mode != GradientMode.GBC:
                                self.assertEqual(event.text.count(f"\\{parent}("), 1)
                        other = "pos" if parent == "org" else "org"
                        self.assertEqual(parse_tags_from_text(output[0].text)[other], values[other])

    def test_split_axes_match_paired_engine_with_real_libass_bounds(self):
        for mode, angle in ((GradientMode.HORIZONTAL, 0), (GradientMode.VERTICAL, 0),
                            (GradientMode.HORIZONTAL, 30), (GradientMode.VERTICAL, 30),
                            (GradientMode.ANGLED, 40)):
            for grouped in (False, True):
                with self.subTest(mode=mode, angle=angle, grouped=grouped):
                    paired = self.settings(config("pos", 320, 334), config("org", 310, 316),
                                           config("fscx", 100, 110), mode=mode, angle=angle)
                    paired.tags["pos"].coord_y_nodes = make_default_nodes(start_y=180, end_y=190)
                    paired.tags["org"].coord_y_nodes = make_default_nodes(start_y=175, end_y=185)
                    paired.tags["org"].coord_y_mode = InterpolationMode.SMOOTH
                    axes = copy.deepcopy(paired)
                    for parent in ("pos", "org"):
                        cfg = axes.tags.pop(parent)
                        axes.tags[parent + ":x"] = replace(cfg, tag=parent + ":x", coord_y_nodes=[])
                        axes.tags[parent + ":y"] = replace(cfg, tag=parent + ":y", nodes=cfg.coord_y_nodes,
                                                          mode=cfg.coord_y_mode, coord_y_nodes=[])
                    if grouped:
                        paired.group_range_bounds = axes.group_range_bounds = (240, 100, 400, 240)
                        paired.group_range_tags = set(paired.tags)
                        axes.group_range_tags = set(axes.tags)
                    expected = self.generate(paired, measured=True)
                    actual = self.generate(axes, measured=True)
                    self.assertEqual(len(actual), len(expected))
                    for a, b in zip(actual, expected):
                        self.assertEqual(parse_tags_from_text(a.text), parse_tags_from_text(b.text))
                        self.assertEqual(a.text[a.text.index("\\clip"):], b.text[b.text.index("\\clip"):])

    def test_origin_without_override_defaults_to_source_position(self):
        event = replace(self.event, text=r"{\pos(320,180)}Origin")
        for axis, value, expected in (("x", 350, (350, 180)), ("y", 205, (320, 205))):
            with self.subTest(axis=axis):
                output = self.generate(self.settings(config("org:" + axis, value, value)), event)
                self.assertEqual(parse_tags_from_text(output[0].text)["org"], expected)

    def test_implicit_position_retains_other_axis_and_has_no_duplicate_pos(self):
        event = replace(self.event, text=r"{\an7}Implicit", margin_l=42, margin_v=55)
        for tag in COORD_AXIS_TAGS:
            with self.subTest(tag=tag):
                parent, axis = COORD_AXIS_TAGS[tag]
                output = self.generate(self.settings(config(tag, 80, 80)), event)
                values = parse_tags_from_text(output[0].text)
                self.assertEqual(values[parent][1-axis], (42, 55)[1-axis])
                self.assertEqual(output[0].text.count(r"\pos("), 1)

    def test_geometry_measurement_uses_the_same_composed_coordinates(self):
        settings = self.settings(config("pos:x", 340, 360), config("org:y", 190, 210))
        parsed = parse_tags_from_text(self.event.text)
        measured = event_with_interpolated_geometry(self.event, parsed, self.style, settings, settings.tags, .5)
        values = parse_tags_from_text(measured.text)
        self.assertEqual(values["pos"], (350, 180))
        self.assertEqual(values["org"], (310, 200))
        self.assertEqual(measured.text.count(r"\pos("), 1)
        self.assertEqual(measured.text.count(r"\org("), 1)

    def test_group_range_can_target_only_one_axis(self):
        event = replace(self.event, text=r"{\pos(100,200)\org(100,200)\clip(100,200,200,300)}Group")
        for parent in ("pos", "org"):
            with self.subTest(parent=parent):
                settings = self.settings(config(parent + ":x", 100, 300), config(parent + ":y", 200, 400),
                                         mode=GradientMode.VERTICAL)
                settings.step = 50
                settings.group_range_bounds = (100, 200, 300, 400)
                settings.group_range_tags = {parent + ":x"}
                x_grouped = self.generate(settings, event)
                settings.group_range_tags = {parent + ":y"}
                y_grouped = self.generate(settings, event)
                self.assertEqual(parse_tags_from_text(x_grouped[-1].text)[parent], (150, 300))
                self.assertEqual(parse_tags_from_text(y_grouped[-1].text)[parent], (200, 250))

    def test_animation_only_shifts_enabled_axis_and_falls_back_from_transform(self):
        for parent in ("pos", "org"):
            for use_transform in (True, False):
                with self.subTest(parent=parent, transform=use_transform):
                    event = replace(self.event, text=r"{\pos(100,200)\org(100,200)\clip(100,200,140,220)}Motion")
                    settings = self.settings(config(parent + ":x", 100, 130), config(parent + ":y", 200, 260))
                    settings.step = 10
                    settings.animation = AnimationSettings(
                        enabled=True, enabled_tags={parent + ":x"}, use_transform=use_transform,
                        fps=10, frame_steps={parent + ":x": 1}, shift_start=1, shift_end=1,
                        event_first_frame=0, event_last_frame=3,
                        frame_time_ms={i: i * 100 for i in range(5)},
                    )
                    output = generate_gradient_events([event], [0], self.ass, settings,
                                                      lambda settings, _idx: settings, lambda _idx: None)
                    by_frame = {}
                    for evt in output:
                        self.assertNotIn(r"\t(", evt.text)
                        self.assertNotIn(f"\\{parent}:", evt.text)
                        by_frame.setdefault(evt.start, []).append(parse_tags_from_text(evt.text)[parent])
                    self.assertEqual(len(by_frame), 4)
                    frames = list(by_frame.values())
                    self.assertNotEqual([p[0] for p in frames[0]], [p[0] for p in frames[1]])
                    self.assertEqual([p[1] for p in frames[0]], [p[1] for p in frames[1]])


class CoordinateAxisGuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.source = ASSFile(styles=[ASSStyle(name="Default", fontname="Arial")],
                              events=[ASSEvent(end="0:00:01.00", text=r"{\pos(320,180)\org(310,175)}Controls")])
        with (patch("gui.main_window.parse_ass_file", return_value=self.source),
              patch("gui.main_window.run_startup_health_check", return_value=HealthReport([])),
              patch.object(MainWindow, "_start_update_check")):
            self.w = MainWindow(input_path=__file__)
        self.addCleanup(self.w.deleteLater)
        self.addCleanup(self.w.close)
        self.addCleanup(self.w._preview_timer.stop)
        self.addCleanup(self.w._undo_timer.stop)

    def test_four_rows_and_only_enabled_axis_tabs(self):
        w = self.w
        self.assertIsNone(w._tag_panel.get_row("pos"))
        self.assertIsNone(w._tag_panel.get_row("org"))
        for key, value in zip(COORD_AXIS_TAGS, (320, 180, 310, 175)):
            row = w._tag_panel.get_row(key)
            self.assertEqual(row._inputs[0].value(), value)
            self.assertEqual(row._inputs[0]._spin.width(), 65)
            row._check.setChecked(True)
            self.assertEqual(w._active_curve_key, key)
            tabs = w._curve_tabs
            self.assertEqual([tabs.tabData(i) for i in range(tabs.count())
                              if tabs.tabText(i).startswith("\\")], [key])
            row._check.setChecked(False)

    def test_group_dialog_lists_axes_independently(self):
        dialog = GroupRangeSettingsDialog(list(COORD_AXIS_TAGS), {"pos:x", "org:y"}, self.w)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(dialog.selected_tags(), {"pos:x", "org:y"})
        self.assertEqual(dialog._tag_list.item(0).text(), r"\pos X")
        dialog._invert()
        self.assertEqual(dialog.selected_tags(), {"pos:y", "org:x"})

    def test_unpositioned_line_controls_use_measured_source_anchor(self):
        self.w._source_events[0].text = "Layout anchor"
        with patch.object(self.w._bounds, "get_meta", return_value="100,200,140,240,0,120,235,120,235"):
            self.w._load_event_tags(0)
        for key, value in zip(COORD_AXIS_TAGS, (120, 235, 120, 235)):
            self.assertEqual(self.w._tag_panel.get_row(key)._inputs[0].value(), value)

    def test_axis_labels_fit_in_both_languages(self):
        original = language()
        self.addCleanup(set_language, original)
        for lang in ("en", "zh"):
            set_language(lang)
            self.w._tag_panel.retranslate_ui()
            for key, (parent, axis) in COORD_AXIS_TAGS.items():
                label = self.w._tag_panel.get_row(key)._label
                self.assertEqual(label.text(), f"\\{parent} {'XY'[axis]}")
                self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()), label.width())

    def test_presets_and_undo_restore_independent_axis_state(self):
        w = self.w
        for key in COORD_AXIS_TAGS:
            w._tag_panel.get_row(key)._check.setChecked(key in {"pos:x", "org:y"})
            w._tag_panel.get_row(key)._inputs[1]._spin.setValue(400)
        w._group_range_tags = {"pos:x"}
        w._on_curve_key_selected("pos:x")
        w._animation_panel._frame_step.setValue(2)
        w._on_curve_key_selected("org:y")
        w._animation_panel._enabled.setChecked(True)
        w._animation_panel._frame_step.setValue(3)
        w._animation_panel._seam_blend_length.setValue(4)
        saved = w._capture_state("Axes")
        w._undo._undo_stack.clear()
        w._undo._redo_stack.clear()
        w._undo.push(saved)
        w._tag_panel.get_row("pos:y")._check.setChecked(True)
        w._undo.push(w._capture_state("Enable Y"))
        w._undo_timer.stop()
        w._do_undo()
        self.assertEqual(w._tag_panel.get_enabled_tags(), ["pos:x", "org:y"])
        w._do_redo()
        self.assertIn("pos:y", w._tag_panel.get_enabled_tags())
        w._restore_state(state_from_preset_data(preset_from_state(saved)))
        self.assertEqual(w._tag_panel.get_enabled_tags(), ["pos:x", "org:y"])
        self.assertEqual(w._animation_enabled_tags, {"org:y"})
        self.assertEqual(w._animation_frame_steps, {"org:y": 3, "pos:x": 2})
        self.assertEqual(w._animation_seam_blend_lengths, {"org:y": 4})
        self.assertEqual(w._group_range_tags, {"pos:x"})
        for key in COORD_AXIS_TAGS:
            self.assertEqual(w._build_settings().tags[key].nodes[-1].y, 400)

    def test_legacy_paired_preset_expands_curves_animation_and_range(self):
        x = serialize_nodes(make_default_nodes(start_y=320, end_y=350))
        y = serialize_nodes(make_default_nodes(start_y=180, end_y=210))
        state = UndoState(
            tag_configs={"pos": {"enabled": True, "nodes": x, "coord_y_nodes": y, "coord_y_mode": "smooth"}},
            tag_curves={}, tag_modes={"pos:x": "bezier"}, active_curve_key="pos:y",
            group_range_tags=["pos"], animation_state={"enabled_tags": ["pos"], "frame_steps": {"pos": 3},
                                                    "seam_blend_lengths": {"pos": 4}},
            animation_curves={"pos": x}, animation_modes={"pos": "linear"},
            animation_curve_mirrors={"pos": (True, False)},
        )
        original = copy.deepcopy(state)
        normalized = split_coordinate_state(state)
        self.assertEqual(state, original)
        self.assertEqual(normalized.animation_curves, {"pos:x": x, "pos:y": x})
        self.w._restore_state(state)
        settings = self.w._build_settings()
        self.assertEqual(settings.tags["pos:x"].nodes, config("pos:x", 320, 350).nodes)
        self.assertEqual(settings.tags["pos:y"].nodes, config("pos:y", 180, 210).nodes)
        self.assertEqual(settings.tags["pos:x"].mode, InterpolationMode.BEZIER)
        self.assertEqual(settings.tags["pos:y"].mode, InterpolationMode.SMOOTH)
        self.assertEqual(self.w._animation_enabled_tags, {"pos:x", "pos:y"})
        self.assertEqual(self.w._group_range_tags, {"pos:x", "pos:y"})


if __name__ == "__main__":
    unittest.main()
