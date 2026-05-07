"""Tests for structured path sampling state."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.path_model import (
    PathSet,
    export_line_sampling_frames,
    export_line_sampling_paths,
    export_line_sampling_samples,
    normalize_path_state,
    serialize_path_state,
)


class PathModelTest(unittest.TestCase):
    def test_combined_ass_path_is_split_into_structured_segments(self) -> None:
        path_set = PathSet.from_ass_path(
            "m 0 0 l 10 0 m 20 0 b 25 0 30 0 35 0"
        )

        self.assertEqual(len(path_set.paths), 2)
        self.assertEqual(
            path_set.to_ass_path(),
            "m 0 0 l 10 0 m 20 0 b 25 0 30 0 35 0",
        )

    def test_sampling_frame_round_trips_through_raw_state(self) -> None:
        path_set = PathSet.from_ass_path("m 1 2 l 3 4", sampling_frame=42)
        path_set.sampled_points = [
            (0, 1, 2, "112233"),
            (0, 3, 4, "AABBCC"),
        ]

        self.assertEqual(path_set.sampling_frame, 42)
        self.assertEqual(path_set.to_raw()["sampling_frame"], 42)
        self.assertEqual(
            path_set.to_raw()["sampled_points"],
            [[0, 1, 2, "112233"], [0, 3, 4, "AABBCC"]],
        )

        restored = PathSet.from_raw(path_set.to_raw())
        self.assertEqual(restored.sampling_frame, 42)
        self.assertEqual(
            restored.sampled_points,
            [(0, 1, 2, "112233"), (0, 3, 4, "AABBCC")],
        )
        self.assertEqual(export_line_sampling_frames({"1c": restored}), {"1c": 42})
        self.assertEqual(
            export_line_sampling_samples({"1c": restored}),
            {"1c": restored.to_raw()},
        )

    def test_saved_raw_samples_reproject_for_current_direction(self) -> None:
        path_set = PathSet.from_raw({
            "paths": [{"ass_path": "m 0 0 l 2 0 m 0 10 l 1 10"}],
            "sampled_points": [
                [0, 0, 0, "000001"],
                [0, 1, 0, "000002"],
                [0, 2, 0, "000003"],
                [1, 0, 10, "000004"],
                [1, 1, 10, "000005"],
            ],
        })

        horizontal_map, horizontal_keys = path_set.sampled_color_result(1.0, 0.0)
        vertical_map, vertical_keys = path_set.sampled_color_result(0.0, 1.0)

        self.assertEqual([horizontal_map[key] for key in horizontal_keys], [
            "000001",
            "000002",
            "000003",
            "000004",
            "000005",
        ])
        self.assertEqual([vertical_map[key] for key in vertical_keys], [
            "000001",
            "000004",
        ])

    def test_removed_state_exports_empty_engine_override(self) -> None:
        state = normalize_path_state({0: {"1c": {"removed_original": True}}})

        self.assertTrue(state[0]["1c"].removed_original)
        self.assertEqual(export_line_sampling_paths(state[0]), {"1c": ""})

    def test_serialized_state_round_trips_to_engine_mapping(self) -> None:
        state = normalize_path_state({
            "2": {
                "1c": {
                    "paths": [
                        {"ass_path": "m 1 2 l 3 4", "label": "A"},
                        {"ass_path": "m 5 6 l 7 8"},
                    ],
                    "sampling_frame": 99,
                },
                "3c": {"removed_original": True, "paths": []},
            }
        })

        raw = serialize_path_state(state)
        restored = normalize_path_state(raw)

        self.assertEqual(
            export_line_sampling_paths(restored[2]),
            {"1c": "m 1 2 l 3 4 m 5 6 l 7 8", "3c": ""},
        )
        self.assertEqual(export_line_sampling_frames(restored[2]), {"1c": 99})


if __name__ == "__main__":
    unittest.main(verbosity=2)
