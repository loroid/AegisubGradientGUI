"""Tests for preview generation cache helpers."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.ass_parser import ASSEvent
from gui.preview_cache import PreviewGenerationCache, stable_preview_key


class PreviewCacheTest(unittest.TestCase):
    def test_stable_key_ignores_dict_order(self) -> None:
        left = stable_preview_key({"b": 2, "a": [1, {"x": "y"}]})
        right = stable_preview_key({"a": [1, {"x": "y"}], "b": 2})

        self.assertEqual(left, right)

    def test_cache_returns_cloned_events(self) -> None:
        cache = PreviewGenerationCache(max_entries=2)
        event = ASSEvent(start="0:00:00.00", end="0:00:01.00", text="{}A")
        cache.put("one", [event])

        cached = cache.get("one")
        self.assertIsNotNone(cached)
        cached[0].text = "{}B"

        again = cache.get("one")
        self.assertEqual(again[0].text, "{}A")

    def test_cache_evicts_oldest_entry(self) -> None:
        cache = PreviewGenerationCache(max_entries=1)
        cache.put("one", [ASSEvent(text="one")])
        cache.put("two", [ASSEvent(text="two")])

        self.assertIsNone(cache.get("one"))
        self.assertEqual(cache.get("two")[0].text, "two")


if __name__ == "__main__":
    unittest.main()
