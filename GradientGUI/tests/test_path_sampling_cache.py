"""Tests for path color sampling cache."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.path_sampling_cache import PathSamplingCache, frame_key_from_video_position


class PathSamplingCacheTest(unittest.TestCase):
    def test_same_frame_path_and_direction_reuses_sampled_colors(self) -> None:
        cache = PathSamplingCache()
        calls: list[tuple[int, int]] = []

        def get_pixel(x: int, y: int) -> str:
            calls.append((x, y))
            return f"{x % 256:02X}{y % 256:02X}00"

        path = "m 0 0 l 12 0"
        first = cache.get_or_build(("frame", "video.mp4", 10), path, 1.0, 0.0, get_pixel)
        call_count = len(calls)
        second = cache.get_or_build(("frame", "video.mp4", 10), path, 1.0, 0.0, get_pixel)

        self.assertGreater(call_count, 0)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), call_count)

    def test_frame_or_direction_change_invalidates_cache_entry(self) -> None:
        cache = PathSamplingCache()
        calls: list[tuple[int, int]] = []

        def get_pixel(x: int, y: int) -> str:
            calls.append((x, y))
            return "000000"

        path = "m 0 0 l 12 0"
        cache.get_or_build(("frame", "video.mp4", 10), path, 1.0, 0.0, get_pixel)
        first_count = len(calls)
        cache.get_or_build(("frame", "video.mp4", 11), path, 1.0, 0.0, get_pixel)
        second_count = len(calls)
        cache.get_or_build(("frame", "video.mp4", 11), path, 0.0, 1.0, get_pixel)

        self.assertGreater(second_count, first_count)
        self.assertGreater(len(calls), second_count)

    def test_frame_key_prefers_whole_video_frame_over_time_hint(self) -> None:
        key = frame_key_from_video_position("video.mp4", 123, 4.56)

        self.assertEqual(key, ("frame", "video.mp4", 123))


if __name__ == "__main__":
    unittest.main(verbosity=2)
