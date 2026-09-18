"""Synthetic curve extrema and real libass clip coverage regressions."""

from __future__ import annotations

import copy
import math
import random
import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.ass_parser import parse_ass_file
from engine.interpolation import (
    CurveNode, InterpolationMode, interpolate, make_default_nodes,
    mirror_nodes_horizontal, numeric_curve_bounds, numeric_curve_sample_positions,
)
from engine.libass_bounds import measure_event_bounds
from engine.path_tracer import project_bounds
from engine.range_calc import (
    GEOMETRY_TAGS, calculate_range_plan, compute_effect_extents, compute_libass_projected_range, coord_sample_points,
    event_with_interpolated_geometry, geometry_sample_points, position_range,
    render_measure_sample_points,
)
from engine.tag_parser import extract_clip_bounds, parse_tags_from_text


def peak_nodes(sign=1.0, start=0.0, end=12.0):
    span = end - start
    return [
        CurveNode(start, 2 * sign, handle_out_x=start + span / 3,
                  handle_out_y=82 * sign, segment_mode=InterpolationMode.BEZIER),
        CurveNode(end, 2 * sign, handle_in_x=end - span / 3, handle_in_y=82 * sign),
        CurveNode(100, 2 * sign),
    ]


def config(tag, nodes=None, mode=InterpolationMode.LINEAR):
    return TagGradientConfig(tag=tag, enabled=True, nodes=nodes or peak_nodes(), mode=mode)


def rect(bounds):
    return None if bounds is None else (bounds.x1, bounds.y1, bounds.x2, bounds.y2)


class CurveExtremaTest(unittest.TestCase):
    def test_narrow_bezier_peak_is_not_limited_to_fixed_grid_or_handle_height(self):
        for start, end in ((0, 12), (49.001, 49.003)):
            for sign in (1, -1):
                with self.subTest(start=start, sign=sign):
                    nodes = peak_nodes(sign, start, end)
                    before = copy.deepcopy(nodes)
                    points = numeric_curve_sample_positions(nodes)
                    self.assertTrue(any(abs(x - (start + end) / 2) < 1e-10 for x in points))
                    expected = (2, 62) if sign > 0 else (-62, -2)
                    for actual, target in zip(numeric_curve_bounds(nodes), expected):
                        self.assertAlmostEqual(actual, target)
                    self.assertEqual(nodes, before)

    def test_bezier_x_handles_determine_peak_position(self):
        nodes = peak_nodes()
        nodes[0].handle_out_x = 1
        nodes[1].handle_in_x = 2
        # Bx(1/2) = (0 + 3*1 + 3*2 + 12) / 8, not the segment midpoint.
        self.assertIn(21 / 8, numeric_curve_sample_positions(nodes))
        self.assertAlmostEqual(numeric_curve_bounds(nodes)[1], 62)

    def test_bezier_with_both_peak_and_valley(self):
        nodes = make_default_nodes(0, 0)
        nodes[0].handle_out_y = 100
        nodes[1].handle_in_y = -100
        lo, hi = numeric_curve_bounds(nodes, InterpolationMode.BEZIER)
        self.assertAlmostEqual(lo, -100 / (2 * math.sqrt(3)))
        self.assertAlmostEqual(hi, 100 / (2 * math.sqrt(3)))

    def test_smooth_curve_overshoots_node_values(self):
        for sign in (1, -1):
            nodes = [CurveNode(x, sign * y) for x, y in ((0, 80), (4, 0), (12, 0), (100, 80))]
            self.assertIn(8, numeric_curve_sample_positions(nodes, InterpolationMode.SMOOTH))
            expected = (-10, 80) if sign > 0 else (-80, 10)
            self.assertEqual(numeric_curve_bounds(nodes, InterpolationMode.SMOOTH), expected)

    def test_segment_override_default_mode_and_mirroring(self):
        nodes = peak_nodes()
        for default in InterpolationMode:
            with self.subTest(default=default):
                bounds = numeric_curve_bounds(nodes, default)
                self.assertAlmostEqual(bounds[1], 62)
                mirrored = numeric_curve_bounds(mirror_nodes_horizontal(nodes), default)
                for actual, expected in zip(mirrored, bounds):
                    self.assertAlmostEqual(actual, expected)
        nodes[0].segment_mode = InterpolationMode.LINEAR
        self.assertEqual(numeric_curve_bounds(nodes, InterpolationMode.BEZIER), (2, 2))
        nodes[0].segment_mode = InterpolationMode.STEPPED
        self.assertEqual(numeric_curve_bounds(nodes), (2, 2))

    def test_empty_single_duplicate_and_flat_curves(self):
        for nodes, expected in (
            ([], (0, 0)), ([CurveNode(50, 7)], (7, 7)),
            ([CurveNode(0, 3), CurveNode(0, 3), CurveNode(100, 3)], (3, 3)),
            (make_default_nodes(3, 3), (3, 3)),
        ):
            for mode in InterpolationMode:
                with self.subTest(nodes=nodes, mode=mode):
                    self.assertEqual(numeric_curve_bounds(nodes, mode), expected)

    def test_extrema_bound_dense_independent_queries(self):
        rng = random.Random(6731)
        for mode in (InterpolationMode.BEZIER, InterpolationMode.SMOOTH):
            for _ in range(20):
                nodes = [CurveNode(x, rng.uniform(-100, 100)) for x in (0, 7, 19, 83, 100)]
                for left, right in zip(nodes, nodes[1:]):
                    left.handle_out_x = rng.uniform(left.x, right.x)
                    right.handle_in_x = rng.uniform(left.x, right.x)
                    left.handle_out_y = rng.uniform(-200, 200)
                    right.handle_in_y = rng.uniform(-200, 200)
                lo, hi = numeric_curve_bounds(nodes, mode)
                for i in range(1001):
                    value = interpolate(nodes, i / 10, mode)
                    self.assertGreaterEqual(value, lo - 1e-8)
                    self.assertLessEqual(value, hi + 1e-8)

    def test_all_effect_extent_tags_include_interior_extrema(self):
        fields = {
            "bord": ("max_xbord", "max_ybord"), "xbord": ("max_xbord",),
            "ybord": ("max_ybord",), "shad": ("max_xshad", "max_yshad"),
            "xshad": ("max_xshad",), "yshad": ("max_yshad",),
        }
        for tag, attrs in fields.items():
            extents = compute_effect_extents("TEST", None, GradientSettings(), {tag: config(tag)})
            for attr in attrs:
                with self.subTest(tag=tag, attr=attr):
                    self.assertAlmostEqual(getattr(extents, attr), 62)
            # Fill and secondary color ranges must not inherit outline/shadow extents.
            for fill_tag in ("1c", "2c", "1a", "2a"):
                self.assertEqual(extents.expand_for_tag(fill_tag, (10, 20, 30, 40)), (10, 20, 30, 40))
        for tag, factor in (("blur", 1.5), ("be", 2)):
            extents = compute_effect_extents("TEST", None, GradientSettings(), {tag: config(tag)})
            self.assertAlmostEqual(extents.max_blur_pad, 62 * factor)

    def test_signed_shadow_valleys_and_nonnegative_border_blur(self):
        for tag, attrs in {
            "shad": ("min_xshad", "min_yshad"), "xshad": ("min_xshad",), "yshad": ("min_yshad",),
        }.items():
            extents = compute_effect_extents("TEST", None, GradientSettings(), {tag: config(tag, peak_nodes(-1))})
            for attr in attrs:
                self.assertAlmostEqual(getattr(extents, attr), -62)
        for tag in ("bord", "xbord", "ybord", "blur", "be"):
            extents = compute_effect_extents("TEST", None, GradientSettings(), {tag: config(tag, peak_nodes(-1))})
            self.assertEqual(extents.expand_for_enabled((0, 0, 100, 100)), (0, 0, 100, 100))

    def test_render_geometry_and_coordinate_samplers_share_extrema(self):
        for tag in (*GEOMETRY_TAGS, "bord", "xbord", "ybord", "shad", "xshad", "yshad", "blur", "be"):
            cfg = config(tag)
            with self.subTest(tag=tag):
                self.assertIn(.06, render_measure_sample_points({tag: cfg}))
                if tag in GEOMETRY_TAGS:
                    self.assertIn(.06, geometry_sample_points({tag: cfg}))
        for tag in ("pos", "org", "pos:x", "pos:y", "org:x", "org:y"):
            cfg = config(tag)
            self.assertIn(.06, coord_sample_points(cfg))
        cfg = config("pos", make_default_nodes(100, 100))
        cfg.coord_y_nodes = peak_nodes()
        cfg.coord_y_nodes[0].segment_mode = None
        cfg.coord_y_mode = InterpolationMode.BEZIER
        self.assertIn(.06, render_measure_sample_points({"pos": cfg}))
        actual = position_range({"pos": cfg}, {}, None, GradientSettings(), (0, 0))
        for value, expected in zip(actual, (100, 100, 2, 62)):
            self.assertAlmostEqual(value, expected)


class CurveExtremaRenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ass = parse_ass_file(PROJECT_DIR / "tests" / "test.ass")
        source = next(event for event in cls.ass.events if not event.comment)
        cls.event = replace(source, text=r"{\an7\pos(600,300)\bord0\shad0\blur0\p1}m 0 0 l 320 0 320 200 0 200")
        cls.style = cls.ass.get_style(cls.event.style)
        cls.bounds = measure_event_bounds(cls.ass, cls.event)
        if cls.bounds is None:
            raise RuntimeError("synthetic drawing did not render")

    def settings(self, mode=GradientMode.HORIZONTAL, angle=0, group=False, path=False):
        settings = GradientSettings(mode=mode, angle=angle, step=2, tags={
            "bord": config("bord"),
            "1c": config("1c", make_default_nodes(start_color="0000FF", end_color="FF0000")),
        })
        settings.text_x1, settings.text_y1, settings.text_x2, settings.text_y2 = rect(self.bounds)
        if group:
            settings.group_range_bounds = rect(self.bounds)
            settings.group_range_tags = set(settings.tags)
        if path:
            settings.sampling_paths = {"1c": "m 10 10 l 10 85"}
            settings.sampling_path_samples = {"1c": {
                "paths": [{"ass_path": "m 10 10 l 10 85"}],
                "sampled_colors": [[i, "0000FF" if i < 38 else "FF0000"] for i in range(76)],
            }}
            settings.path_sampling_smooth = {"1c": False}
        return settings

    def test_peak_strips_do_not_lose_pixels_at_perpendicular_clip_edges(self):
        for mode in (GradientMode.HORIZONTAL, GradientMode.VERTICAL):
            for group, path in ((False, False), (True, False), (True, True)):
                with self.subTest(mode=mode, group=group, path=path):
                    settings = self.settings(mode, group=group, path=path)
                    output = generate_gradient(self.event, self.style, settings, self.bounds.to_meta(), self.ass)
                    ranked = sorted(output, key=lambda event: parse_tags_from_text(event.text).get("bord", 0), reverse=True)
                    self.assertGreater(parse_tags_from_text(ranked[0].text)["bord"], 61)
                    for strip in ranked[:3]:
                        x1, y1, x2, y2 = extract_clip_bounds(strip.text)
                        if mode == GradientMode.HORIZONTAL:
                            y1, y2 = 0, self.ass.play_res_y
                        else:
                            x1, x2 = 0, self.ass.play_res_x
                        relaxed = replace(strip, text=re.sub(
                            r"\\clip\([^)]*\)", lambda _: f"\\clip({x1},{y1},{x2},{y2})", strip.text,
                        ))
                        actual = measure_event_bounds(self.ass, strip, image_types=(0, 1))
                        expected = measure_event_bounds(self.ass, relaxed, image_types=(0, 1))
                        self.assertIsNotNone(expected)
                        self.assertEqual(rect(actual), rect(expected))

    def test_projected_range_measures_real_peak_in_every_direction(self):
        for mode, angle in (
            (GradientMode.HORIZONTAL, 0), (GradientMode.VERTICAL, 0),
            (GradientMode.HORIZONTAL, 40), (GradientMode.VERTICAL, 40), (GradientMode.ANGLED, 45),
        ):
            for group in (False, True):
                with self.subTest(mode=mode, angle=angle, group=group):
                    settings = self.settings(mode, angle, group)
                    parsed = parse_tags_from_text(self.event.text)
                    plan = calculate_range_plan(
                        event=self.event, ass_file=self.ass, style=self.style, parsed=parsed,
                        settings=settings, enabled_tags=settings.tags, base_rect=rect(self.bounds),
                        source_clip_bounds=None, geom_context=None,
                    )
                    peak = event_with_interpolated_geometry(self.event, parsed, self.style, settings, settings.tags, .06)
                    actual = measure_event_bounds(self.ass, peak, image_types=(0, 1))
                    expected = project_bounds(*rect(actual), plan.direction.cos_a, plan.direction.sin_a)
                    self.assertIsNotNone(plan.rendered_range)
                    for minimum in (0, 2):
                        self.assertLessEqual(plan.event_projected_range[minimum], expected[minimum])
                        self.assertGreaterEqual(plan.event_projected_range[minimum + 1], expected[minimum + 1])
                    if group:
                        expanded = (self.bounds.x1 - 62, self.bounds.y1 - 62, self.bounds.x2 + 62, self.bounds.y2 + 62)
                        self.assertEqual(plan.group_projected_ranges["bord"], project_bounds(*expanded, plan.direction.cos_a, plan.direction.sin_a))

    def test_invisible_endpoints_do_not_skip_visible_interior_peak(self):
        for tag in ("pos:x", "fscy"):
            nodes = peak_nodes()
            for node in nodes:
                for attr in ("y", "handle_in_y", "handle_out_y"):
                    value = getattr(node, attr)
                    setattr(node, attr, 2400 - (value - 2) * 30 if tag == "pos:x" else (value - 2) * 2)
            settings = GradientSettings(tags={tag: config(tag, nodes)})
            parsed = parse_tags_from_text(self.event.text)
            for t in (0, 1):
                endpoint = event_with_interpolated_geometry(self.event, parsed, self.style, settings, settings.tags, t)
                self.assertIsNone(measure_event_bounds(self.ass, endpoint))
            peak = event_with_interpolated_geometry(self.event, parsed, self.style, settings, settings.tags, .06)
            expected = rect(measure_event_bounds(self.ass, peak))
            self.assertIsNotNone(expected)
            measured = compute_libass_projected_range(
                self.event, self.ass, parsed, self.style, settings, settings.tags, 1, 0,
            )
            self.assertEqual(measured, project_bounds(*expected, 1, 0))

    def test_entirely_invisible_curve_has_no_rendered_bounds(self):
        settings = GradientSettings(tags={"fscy": config("fscy", make_default_nodes(0, 0))})
        self.assertIsNone(compute_libass_projected_range(
            self.event, self.ass, parse_tags_from_text(self.event.text), self.style,
            settings, settings.tags, 1, 0,
        ))


if __name__ == "__main__":
    unittest.main()
