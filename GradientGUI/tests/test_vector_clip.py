from __future__ import annotations

import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.ass_parser import ASSEvent, ASSStyle
from engine.interpolation import make_default_nodes
from engine.tag_parser import extract_clip_bounds
from engine.vector_clip import (
    extract_source_vector_clip,
    parse_vector_clip_content,
    vector_clip_tag_for_strip,
)


class VectorClipTest(unittest.TestCase):
    def test_vector_clip_is_split_by_axis_aligned_strip(self) -> None:
        source = parse_vector_clip_content("m 0 0 l 100 0 l 100 100 l 0 100")
        self.assertIsNotNone(source)

        clip = vector_clip_tag_for_strip(
            source,
            [(25, -10), (75, -10), (75, 110), (25, 110)],
        )

        self.assertIsNotNone(clip)
        self.assertIn(r"\clip(1,m", clip)
        self.assertIn("25", clip)
        self.assertIn("75", clip)

    def test_inverse_vector_clip_subtracts_shape_from_strip(self) -> None:
        source = extract_source_vector_clip(
            r"{\iclip(m 40 0 l 60 0 l 60 100 l 40 100)}Text"
        )
        self.assertIsNotNone(source)
        self.assertTrue(source.inverse)

        clip = vector_clip_tag_for_strip(
            source,
            [(0, 0), (100, 0), (100, 100), (0, 100)],
        )

        self.assertIsNotNone(clip)
        self.assertEqual(clip.count("m "), 2)
        self.assertIn("40", clip)
        self.assertIn("60", clip)

    def test_inverse_rectangle_clip_is_converted_to_split_vector_clip(self) -> None:
        source = extract_source_vector_clip(r"{\iclip(40,0,60,100)}Text")
        self.assertIsNotNone(source)
        self.assertTrue(source.inverse)

        clip = vector_clip_tag_for_strip(
            source,
            [(0, 0), (100, 0), (100, 100), (0, 100)],
        )

        self.assertIsNotNone(clip)
        self.assertEqual(clip.count("m "), 2)

    def test_scaled_vector_clip_bounds_use_effective_coordinates(self) -> None:
        bounds = extract_clip_bounds(r"{\clip(4,m 80 80 l 180 80 l 180 140 l 80 140)}Text")
        self.assertEqual(bounds, (10.0, 10.0, 22.5, 17.5))

    def test_generated_strips_preserve_source_vector_clip_shape(self) -> None:
        event = ASSEvent(
            layer=0,
            start="0:00:00.00",
            end="0:00:01.00",
            style="Default",
            name="",
            margin_l=0,
            margin_r=0,
            margin_v=0,
            effect="",
            text=r"{\pos(100,100)\clip(m 80 80 l 180 80 l 180 140 l 80 140)}Test",
            comment=False,
        )
        style = ASSStyle(name="Default")
        settings = GradientSettings(
            mode=GradientMode.HORIZONTAL,
            step=30.0,
            text_x1=80.0,
            text_y1=80.0,
            text_x2=180.0,
            text_y2=140.0,
            tags={
                "1c": TagGradientConfig(
                    tag="1c",
                    enabled=True,
                    nodes=make_default_nodes(start_color="0000FF", end_color="FF0000"),
                )
            },
        )

        generated = generate_gradient(event, style, settings)

        self.assertGreater(len(generated), 1)
        for strip in generated:
            self.assertRegex(strip.text, r"\\clip\(1,m ")
            self.assertNotRegex(strip.text, r"\\clip\(\d+(?:\.\d+)?,\d+(?:\.\d+)?,")

    def test_generated_strips_preserve_source_inverse_vector_clip(self) -> None:
        event = ASSEvent(
            layer=0,
            start="0:00:00.00",
            end="0:00:01.00",
            style="Default",
            name="",
            margin_l=0,
            margin_r=0,
            margin_v=0,
            effect="",
            text=r"{\pos(100,100)\iclip(m 118 90 l 130 90 l 130 130 l 118 130)}Test",
            comment=False,
        )
        style = ASSStyle(name="Default")
        settings = GradientSettings(
            mode=GradientMode.HORIZONTAL,
            step=30.0,
            text_x1=80.0,
            text_y1=80.0,
            text_x2=180.0,
            text_y2=140.0,
            tags={
                "1c": TagGradientConfig(
                    tag="1c",
                    enabled=True,
                    nodes=make_default_nodes(start_color="0000FF", end_color="FF0000"),
                )
            },
        )

        generated = generate_gradient(event, style, settings)

        self.assertGreater(len(generated), 1)
        self.assertTrue(any(strip.text.count("m ") >= 2 for strip in generated))
        for strip in generated:
            self.assertRegex(strip.text, r"\\clip\(1,m ")
            self.assertNotIn(r"\iclip", strip.text)


if __name__ == "__main__":
    unittest.main()
