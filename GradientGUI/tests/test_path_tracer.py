"""Tests for path color tracing helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.path_tracer import get_color_by_ratio, project_sampled_path_points
from engine.api import (
    build_path_color_preview_stops,
    build_path_color_preview_stops_from_sampled_points,
)


class PathTracerTest(unittest.TestCase):
    def test_color_lookup_interpolates_between_adjacent_samples(self) -> None:
        color_map = {
            1: "000000",
            2: "FFFFFF",
        }

        self.assertEqual(
            get_color_by_ratio(color_map, [1, 2], 0.5, smooth=True),
            "808080",
        )

    def test_zero_smooth_strength_keeps_nearest_sample_lookup(self) -> None:
        color_map = {
            1: "000000",
            2: "FFFFFF",
        }

        self.assertEqual(
            get_color_by_ratio(
                color_map,
                [1, 2],
                0.5,
                smooth=True,
                smooth_strength=0.0,
            ),
            "FFFFFF",
        )

    def test_preview_stops_reflect_smooth_strength(self) -> None:
        def get_pixel(x: int, y: int) -> str:
            return "FFFFFF" if x == 1 else "000000"

        raw = build_path_color_preview_stops(
            "m 0 0 l 2 0",
            1.0,
            0.0,
            get_pixel,
            smooth=False,
        )
        smoothed = build_path_color_preview_stops(
            "m 0 0 l 2 0",
            1.0,
            0.0,
            get_pixel,
            smooth=True,
            smooth_strength=1.0,
        )

        self.assertNotEqual(raw, smoothed)

    def test_saved_path_points_can_be_reprojected_for_direction_changes(self) -> None:
        samples = [
            (0, 0, 0, "000001"),
            (0, 1, 0, "000002"),
            (0, 2, 0, "000003"),
            (0, 0, 4, "000004"),
        ]

        horizontal_map, horizontal_keys = project_sampled_path_points(samples, 1.0, 0.0)
        vertical_map, vertical_keys = project_sampled_path_points(samples, 0.0, 1.0)

        self.assertEqual(
            [horizontal_map[key] for key in horizontal_keys],
            ["000001", "000002", "000003"],
        )
        self.assertEqual(
            [vertical_map[key] for key in vertical_keys],
            ["000001", "000004"],
        )

    def test_preview_stops_from_saved_points_tracks_direction(self) -> None:
        samples = [
            (0, 0, 0, "000001"),
            (0, 1, 0, "000002"),
            (0, 2, 0, "000003"),
            (0, 0, 4, "000004"),
        ]

        horizontal = build_path_color_preview_stops_from_sampled_points(
            samples,
            1.0,
            0.0,
            smooth=False,
        )
        vertical = build_path_color_preview_stops_from_sampled_points(
            samples,
            0.0,
            1.0,
            smooth=False,
        )

        self.assertNotEqual(horizontal, vertical)


if __name__ == "__main__":
    unittest.main(verbosity=2)
