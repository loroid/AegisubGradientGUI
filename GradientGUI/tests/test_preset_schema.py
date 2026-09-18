"""Tests for the formal GradientGUI preset schema."""

from __future__ import annotations

import sys
import copy
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from gui.state_codec import (
    PRESET_FORMAT,
    PRESET_SCHEMA_VERSION,
    preset_from_state,
    state_from_preset_data,
)
from gui.undo_manager import UndoState


class PresetSchemaTest(unittest.TestCase):
    def _sample_state(self) -> UndoState:
        return UndoState(
            tag_configs={
                "1c": {
                    "enabled": True,
                    "nodes": [],
                    "coord_y_nodes": [],
                    "coord_y_mode": "linear",
                },
                "pos": {
                    "enabled": True,
                    "nodes": [],
                    "coord_y_nodes": [],
                    "coord_y_mode": "smooth",
                },
            },
            tag_curves={
                "1c": [
                    {
                        "x": 0.0,
                        "y": 0.0,
                        "value_str": "FFFFFF",
                        "hix": -10.0,
                        "hiy": 0.0,
                        "hox": 33.33,
                        "hoy": 0.0,
                        "seg_mode": None,
                    }
                ],
                "pos:y": [
                    {
                        "x": 100.0,
                        "y": 250.0,
                        "value_str": "",
                        "hix": 90.0,
                        "hiy": 250.0,
                        "hox": 110.0,
                        "hoy": 250.0,
                        "seg_mode": "linear",
                    }
                ],
            },
            tag_modes={"1c": "linear", "pos:y": "smooth"},
            curve_mirrors={"1c": (True, False), "pos:y": (False, True)},
            sampling_paths={
                "2": {
                    "1c": {
                        "paths": [{"ass_path": "m 1 2 l 3 4", "label": "A"}],
                        "sampling_frame": 12,
                        "sampled_points": [[0, 1, 2, "112233"], [0, 3, 4, "445566"]],
                    },
                    "3c": {"removed_original": True, "paths": []},
                }
            },
            selected_lines=[0, 2, 4],
            active_event_idx=2,
            active_curve_key="1c",
            mode="Angled",
            angle=40.0,
            step=1.0,
            color_space="OKLab",
            path_sampling_smooth={"1c": True, "3c": False},
            merge_selected_lines=True,
            group_range_tags=["1c", "bord"],
            animation_state={
                "enabled": True,
                "enabled_tags": ["1c"],
                "use_transform": True,
                "frame_step": 1,
                "frame_steps": {"1c": 2},
                "seam_blend_length": 2,
                "seam_blend_lengths": {"1c": 2},
                "preview_frame": 4,
            },
            animation_curves={
                "1c": [
                    {
                        "x": 0.0,
                        "y": 1.0,
                        "value_str": "",
                        "hix": -10.0,
                        "hiy": 1.0,
                        "hox": 33.33,
                        "hoy": 1.0,
                        "seg_mode": None,
                    },
                    {
                        "x": 100.0,
                        "y": 2.0,
                        "value_str": "",
                        "hix": 66.67,
                        "hiy": 2.0,
                        "hox": 110.0,
                        "hoy": 2.0,
                        "seg_mode": "smooth",
                    },
                ]
            },
            animation_modes={"1c": "linear"},
            animation_curve_mirrors={"1c": (True, True)},
            description="测试预设",
        )

    def test_preset_has_formal_top_level_sections(self) -> None:
        preset = preset_from_state(self._sample_state())

        self.assertEqual(
            set(preset.keys()),
            {
                "format",
                "version",
                "tags",
                "curves",
                "path_sampling",
                "range_settings",
                "animation",
                "ui_state",
            },
        )
        self.assertEqual(preset["format"], PRESET_FORMAT)
        self.assertEqual(preset["version"], PRESET_SCHEMA_VERSION)
        self.assertEqual(set(preset["animation"].keys()), {"settings", "curves"})

    def test_preset_round_trips_to_undo_state(self) -> None:
        original = self._sample_state()
        restored = state_from_preset_data(preset_from_state(original))

        self.assertEqual(restored.tag_configs, original.tag_configs)
        self.assertEqual(restored.tag_curves, original.tag_curves)
        self.assertEqual(restored.tag_modes, original.tag_modes)
        self.assertEqual(restored.curve_mirrors, original.curve_mirrors)
        self.assertEqual(
            restored.sampling_paths,
            {
                "2": {
                    "1c": {
                        "removed_original": False,
                        "paths": [{"ass_path": "m 1 2 l 3 4", "label": "A"}],
                        "sampling_frame": 12,
                        "sampled_points": [[0, 1, 2, "112233"], [0, 3, 4, "445566"]],
                    },
                    "3c": {"paths": [], "removed_original": True},
                }
            },
        )
        self.assertEqual(restored.path_sampling_smooth, original.path_sampling_smooth)
        self.assertEqual(restored.merge_selected_lines, original.merge_selected_lines)
        self.assertEqual(restored.group_range_tags, original.group_range_tags)
        self.assertEqual(restored.animation_state, original.animation_state)
        self.assertEqual(restored.animation_curves, original.animation_curves)
        self.assertEqual(restored.animation_modes, original.animation_modes)
        self.assertEqual(restored.animation_curve_mirrors, original.animation_curve_mirrors)
        self.assertEqual(restored.mode, original.mode)
        self.assertEqual(restored.angle, original.angle)
        self.assertEqual(restored.color_space, original.color_space)
        self.assertEqual(restored.active_curve_key, original.active_curve_key)

    def test_unknown_preset_version_is_rejected(self) -> None:
        preset = preset_from_state(self._sample_state())
        preset["version"] = 999

        with self.assertRaisesRegex(ValueError, "不支持的预设版本"):
            state_from_preset_data(preset)

    def test_step_direction_is_preserved_and_requires_a_boolean(self):
        stores = (("tags", "1c", "nodes"), ("curves", "nodes", "1c"),
                  ("animation", "curves", "nodes", "1c"))
        for store in stores:
            for value in (True, False, "false", 0, 1, None, []):
                with self.subTest(store=store, value=value):
                    preset = preset_from_state(self._sample_state())
                    preset["tags"]["1c"]["nodes"] = copy.deepcopy(preset["curves"]["nodes"]["1c"])
                    nodes = preset
                    for key in store:
                        nodes = nodes[key]
                    nodes[0]["step_from_right"] = value
                    if type(value) is not bool:
                        with self.assertRaisesRegex(ValueError, "step_from_right"):
                            state_from_preset_data(preset)
                        continue
                    restored = preset_from_state(state_from_preset_data(preset))
                    for key in store:
                        restored = restored[key]
                    self.assertIs(restored[0]["step_from_right"], value)

    def assert_invalid_field(self, location, value):
        preset = preset_from_state(self._sample_state())
        parent = preset
        for key in location[:-1]:
            parent = parent[key]
        parent[location[-1]] = value
        with self.assertRaises(ValueError) as error:
            state_from_preset_data(preset)
        self.assertIn(str(location[-1]), str(error.exception))

    def test_all_curve_coordinates_reject_invalid_numbers_before_restore(self):
        for store in (("curves", "nodes", "1c"), ("curves", "nodes", "pos:y"),
                      ("animation", "curves", "nodes", "1c")):
            for field in ("x", "y", "hix", "hiy", "hox", "hoy"):
                for value in (float("nan"), float("inf"), -float("inf"), "2.5", None, True, 10 ** 400):
                    with self.subTest(store=store, field=field, value=value):
                        self.assert_invalid_field((*store, 0, field), value)

    def test_tag_panel_coordinates_are_checked_even_when_tag_is_disabled(self):
        for field in ("nodes", "coord_y_nodes"):
            preset = preset_from_state(self._sample_state())
            node = copy.deepcopy(preset["curves"]["nodes"]["pos:y"][0])
            node["hox"] = float("nan")
            preset["tags"]["pos"]["enabled"] = False
            preset["tags"]["pos"][field] = [node]
            with self.subTest(field=field), self.assertRaises(ValueError) as error:
                state_from_preset_data(preset)
            self.assertIn(f"tags.pos.{field}[0].hox", str(error.exception))

    def test_unknown_modes_and_wrong_container_types_are_rejected(self):
        cases = (
            (("curves", "nodes"), []),
            (("curves", "nodes", "1c"), {}),
            (("curves", "nodes", "1c", 0), "node"),
            (("curves", "nodes", "1c", 0, "seg_mode"), "unknown"),
            (("curves", "modes", "1c"), "unknown"),
            (("animation", "curves", "modes", "1c"), "unknown"),
            (("curves", "mirrors", "1c"), [True]),
            (("curves", "mirrors", "1c"), [True, "false"]),
            (("animation", "curves", "mirrors", "1c"), {}),
            (("tags", "pos", "coord_y_mode"), "unknown"),
            (("tags", "1c", "enabled"), "false"),
            (("tags", "1c", "nodes"), None),
            (("ui_state", "mode"), "unknown"),
            (("ui_state", "color_space"), "unknown"),
            (("range_settings", "merge_selected_lines"), "false"),
            (("range_settings", "group_range_tags"), "1c"),
        )
        for location, value in cases:
            with self.subTest(location=location, value=value):
                self.assert_invalid_field(location, value)

    def test_global_parameters_are_finite_and_within_supported_domain(self):
        cases = (
            (("ui_state", "angle"), float("nan")),
            (("ui_state", "step"), float("inf")),
            (("ui_state", "step"), 0),
            (("ui_state", "step"), -0.05),
            (("path_sampling", "smooth", "1c"), "false"),
            (("path_sampling", "smooth_strength"), {"1c": float("nan")}),
            (("path_sampling", "smooth_strength"), {"1c": -0.1}),
            (("path_sampling", "smooth_strength"), {"1c": 1.1}),
            (("animation", "settings", "enabled"), "false"),
            (("animation", "settings", "enabled_tags"), "1c"),
            (("animation", "settings", "use_transform"), 1),
            (("animation", "settings", "frame_steps", "1c"), 1.5),
            (("animation", "settings", "frame_steps", "1c"), 0),
            (("animation", "settings", "seam_blend_lengths", "1c"), -1),
            (("animation", "settings", "seam_blend_lengths", "1c"), float("inf")),
            (("animation", "settings", "preview_frame"), -1),
            (("ui_state", "selected_lines"), [0, 1.5]),
        )
        for location, value in cases:
            with self.subTest(location=location, value=value):
                self.assert_invalid_field(location, value)

    def test_sample_data_is_not_silently_discarded_or_coerced(self):
        cases = (
            (("path_sampling", "paths"), []),
            (("path_sampling", "paths", "2", "1c", "removed_original"), "false"),
            (("path_sampling", "paths", "2", "1c", "paths"), "m 0 0 l 1 1"),
            (("path_sampling", "paths", "2", "1c", "sampling_frame"), -1),
            (("path_sampling", "paths", "2", "1c", "sampled_points"), [[0, 1, 2]]),
            (("path_sampling", "paths", "2", "1c", "sampled_points"), [[0, 1.5, 2, "112233"]]),
            (("path_sampling", "paths", "2", "1c", "sampled_points"), [[0, 1, 2, "GGGGGG"]]),
            (("path_sampling", "paths", "2", "1c", "sampled_colors"), [[0, "FF"]]),
        )
        for location, value in cases:
            with self.subTest(location=location, value=value):
                self.assert_invalid_field(location, value)

    def test_manual_colors_are_valid_hex_but_animation_y_is_not_a_color(self):
        for value in ("GGGGGG", "12345", "1234567", "#123456", 123456):
            with self.subTest(value=value):
                self.assert_invalid_field(("curves", "nodes", "1c", 0, "value_str"), value)
        original = self._sample_state()
        preset = preset_from_state(original)
        restored = state_from_preset_data(json.loads(json.dumps(preset)))
        self.assertEqual(restored.animation_curves, original.animation_curves)

    def test_valid_precise_values_and_missing_optional_sections_still_load(self):
        preset = preset_from_state(self._sample_state())
        node = preset["curves"]["nodes"]["pos:y"][0]
        node.update(y=-0.000001, hix=-500.000001, hox=900.000001)
        original = copy.deepcopy(preset)
        restored = state_from_preset_data(preset)
        self.assertEqual(restored.tag_curves["pos:y"][0], node)
        self.assertEqual(preset, original)
        minimal = {"format": PRESET_FORMAT, "version": PRESET_SCHEMA_VERSION}
        restored = state_from_preset_data(minimal)
        self.assertEqual(restored.mode, "Horizontal")
        self.assertEqual(restored.step, 1.0)

    def test_named_and_implicit_segment_samples_match_path_model(self):
        preset = preset_from_state(self._sample_state())
        samples = [[1, 2, "112233"], {"segment_index": 1, "x": 3, "y": 4, "value": "445566"}]
        preset["path_sampling"]["paths"]["2"]["1c"]["sampled_points"] = samples
        restored = state_from_preset_data(preset)
        self.assertEqual(restored.sampling_paths["2"]["1c"]["sampled_points"],
                         [[0, 1, 2, "112233"], [1, 3, 4, "445566"]])
        samples[1]["segment_index"] = float("nan")
        with self.assertRaises(ValueError) as error:
            state_from_preset_data(preset)
        self.assertIn("sampled_points[1]", str(error.exception))

    def test_json_overflow_is_rejected_and_noninteger_version_is_not_accepted(self):
        preset = preset_from_state(self._sample_state())
        raw = json.dumps(preset).replace('"step": 1.0', '"step": 1e999')
        with self.assertRaises(ValueError) as error:
            state_from_preset_data(json.loads(raw))
        self.assertIn("ui_state.step", str(error.exception))
        preset["version"] = float(PRESET_SCHEMA_VERSION)
        with self.assertRaises(ValueError):
            state_from_preset_data(preset)

    def test_missing_required_node_fields_have_precise_errors(self):
        for field in ("x", "y", "hix", "hiy", "hox", "hoy"):
            preset = preset_from_state(self._sample_state())
            del preset["curves"]["nodes"]["pos:y"][0][field]
            with self.subTest(field=field), self.assertRaises(ValueError) as error:
                state_from_preset_data(preset)
            self.assertIn(f"curves.nodes.pos:y[0].{field}", str(error.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
