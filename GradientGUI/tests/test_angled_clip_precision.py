"""Measure angled strip coverage, not just the union of clip bounding boxes."""

from __future__ import annotations

import math
import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_animation_source_timing as render_support
from engine.api import GradientMode, TagGradientConfig, generate_gradient
from engine.ass_parser import ASSEvent
from engine.interpolation import make_default_nodes
from engine.range_calc import compute_gradient_direction
from engine.vector_clip import parse_vector_clip_content
from gui.debug_overlay import parse_clip_shape
from gui.gradient_generation import _clip_axis_position


class AngledClipPrecisionTest(unittest.TestCase):
    def setUp(self):
        self.rendering = render_support.SourceTimingTest()
        self.rendering.setUp()
        self.addCleanup(self.rendering.doCleanups)
        self.event = ASSEvent(end="0:00:01.00", text=(
            r"{\an7\pos(0,0)\p1\bord0\shad0\blur0\1c&HFFFFFF&}"
            "m 0 0 l 320 0 320 180 0 180"
        ))

    def settings(self, angle, step, start=100, end=100):
        settings = self.rendering.settings(GradientMode.ANGLED)
        settings.animation.enabled = False
        settings.angle, settings.step = angle, step
        settings.tags = {"fscy": TagGradientConfig(
            tag="fscy", enabled=True, nodes=make_default_nodes(start, end),
        )}
        return settings

    def generate(self, settings, event=None):
        return generate_gradient(
            event or self.event, self.rendering.style, settings, None, self.rendering.ass,
        )

    def assert_solid_interior(self, events, crop=(5, 5, 315, 175)):
        frame = self.rendering.render(events, [100])[0]
        # Ignore the drawing's own antialiased edges. Only internal seams count.
        minimum, maximum = frame.getchannel("A").crop(crop).getextrema()
        self.assertEqual(maximum, 255)
        self.assertGreaterEqual(minimum, 254, "a clip seam reduces opaque interior coverage")

    def test_fscy_at_45_degrees_step_2_has_no_dark_internal_seams(self):
        for start, end in ((100, 100), (100, 120), (120, 100), (100, 150), (150, 100)):
            with self.subTest(start=start, end=end):
                self.assert_solid_interior(self.generate(self.settings(45, 2, start, end)))

    def test_non_unit_steps_and_other_angles_have_no_dark_internal_seams(self):
        for angle in (15, 30, 40, 45, 75, -45, 135):
            for step in (0.5, 1, 1.5, 2, 5):
                with self.subTest(angle=angle, step=step):
                    self.assert_solid_interior(self.generate(self.settings(angle, step)))

    def test_source_vector_and_inverse_clips_preserve_coverage_and_holes(self):
        for clip in (r"\clip(m 0 0 l 320 0 320 180 0 180)",
                     r"\iclip(m 140 60 l 180 60 180 120 140 120)",
                     r"\iclip(140,60,180,120)"):
            with self.subTest(clip=clip):
                event = ASSEvent(end="0:00:01.00", text=self.event.text.replace("}", clip + "}", 1))
                events = self.generate(self.settings(45, 2), event)
                self.assert_solid_interior(events, (5, 5, 135, 175))
                self.assert_solid_interior(events, (185, 5, 315, 175))
                if "iclip" in clip:
                    frame = self.rendering.render(events, [100])[0]
                    # Quantizing intersected hole edges can leave faint AA
                    # residue; whole-pixel rounding previously leaked up to 192.
                    self.assertLessEqual(
                        frame.getchannel("A").crop((145, 65, 175, 115)).getextrema()[1], 8,
                    )

    def test_fscy_text_has_no_clip_seams_where_all_scaled_glyphs_are_opaque(self):
        event = replace(self.event, text=r"{\an7\pos(20,20)\fs64\b1\bord0\shad0\blur0}TEST")
        for start, end in ((100, 120), (120, 100)):
            with self.subTest(start=start, end=end):
                events = self.generate(self.settings(45, 2, start, end), event)
                common = None
                for strip in events:
                    unclipped = replace(strip, text=re.sub(r"\\clip\([^)]*\)", "", strip.text))
                    alpha = self.rendering.render([unclipped], [100])[0].getchannel("A")
                    common = alpha if common is None else ImageChops.darker(common, alpha)
                mask = common.point([0] * 255 + [255])
                self.assertGreater(mask.histogram()[255], 100, "glyph intersection must not be empty")
                actual = self.rendering.render(events, [100])[0].getchannel("A")
                missing = ImageChops.multiply(ImageChops.invert(actual), mask)
                self.assertLessEqual(missing.getextrema()[1], 1)

    def test_serialized_vertices_keep_subpixel_step_and_angle(self):
        angle = math.radians(45)
        c, s = math.cos(angle), math.sin(angle)
        for step in (0.05, 0.5, 2, 5):
            with self.subTest(step=step):
                settings = self.settings(45, step)
                # No renderer is needed to check serialization precision.
                settings.text_x2 = settings.text_y2 = 8
                events = generate_gradient(self.event, self.rendering.style, settings)
                previous = None
                for event in events[:-1]:
                    content = re.search(r"\\clip\(([^)]*)\)", event.text).group(1)
                    points = parse_vector_clip_content(content).polygons[0]
                    projected = [x * c + y * s for x, y in points]
                    self.assertAlmostEqual(projected[0], projected[1], delta=0.025)
                    self.assertAlmostEqual(projected[2], projected[3], delta=0.025)
                    self.assertAlmostEqual(projected[2] - projected[0], step + 1, delta=0.025)
                    if previous is not None:
                        self.assertAlmostEqual(projected[0] - previous, step, delta=0.025)
                    previous = projected[0]

    def test_scaled_clip_projection_and_debug_overlay_use_script_coordinates(self):
        content = "7,m -640 1280 l 0 640 640 1280 0 1920"
        expected_points = [(-10, 20), (0, 10), (10, 20), (0, 30)]
        self.assertEqual(parse_clip_shape(content)["points"], expected_points)
        direction = compute_gradient_direction(GradientMode.ANGLED, 45)
        self.assertAlmostEqual(
            _clip_axis_position(r"{\clip(" + content + ")}Test", direction),
            -10 * direction.cos_a + 20 * direction.sin_a,
        )
        # Animation target lookup may compare original scale-1 clips with
        # generated scale-7 clips; both must report the same physical position.
        unscaled = "m -10 20 l 0 10 10 20 0 30"
        for mode in (GradientMode.HORIZONTAL, GradientMode.VERTICAL, GradientMode.ANGLED):
            for angle in (0, 45, -45):
                direction = compute_gradient_direction(mode, angle)
                self.assertEqual(
                    _clip_axis_position(r"\clip(" + content + ")", direction),
                    _clip_axis_position(r"\clip(" + unscaled + ")", direction),
                )

    def test_axis_aligned_clips_remain_rectangular_and_unchanged(self):
        for mode in (GradientMode.HORIZONTAL, GradientMode.VERTICAL):
            with self.subTest(mode=mode):
                settings = self.settings(0, 2)
                settings.mode = mode
                events = self.generate(settings)
                for event in events:
                    self.assertRegex(event.text, r"\\clip\([-\d.]+,[-\d.]+,[-\d.]+,[-\d.]+\)")
                actual = self.rendering.render(events, [100])[0]
                expected = self.rendering.render([self.event], [100])[0]
                self.assertIsNone(ImageChops.difference(actual, expected).getbbox(alpha_only=False))


if __name__ == "__main__":
    unittest.main()
