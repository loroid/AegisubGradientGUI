"""Tests for the formal GradientGUI preset schema."""

from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
