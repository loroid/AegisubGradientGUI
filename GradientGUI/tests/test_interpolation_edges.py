"""Independent mathematical checks for synthetic interpolation edge cases."""

from __future__ import annotations

import colorsys
import math
import random
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.interpolation import ColorSpace, CurveNode, InterpolationMode, interpolate, make_default_nodes
from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.ass_parser import ASSEvent


def hue_color(hue):
    r, g, b = colorsys.hls_to_rgb((hue % 360) / 360, 0.5, 1)
    return f"{int(b * 255 + .5):02X}{int(g * 255 + .5):02X}{int(r * 255 + .5):02X}"


def bezier_nodes(h1, h2):
    nodes = make_default_nodes(0, 1)
    nodes[0].handle_out_x = h1 * 100
    nodes[0].handle_out_y = 1 / 3
    nodes[1].handle_in_x = h2 * 100
    nodes[1].handle_in_y = 2 / 3
    return nodes


def bezier_x(u, h1, h2):
    # De Casteljau evaluation, independent of the production Bernstein formula.
    a, b, c = h1 * u, h1 * (1 - u) + h2 * u, h2 * (1 - u) + u
    d, e = a * (1 - u) + b * u, b * (1 - u) + c * u
    return d * (1 - u) + e * u


class InterpolationEdgesTest(unittest.TestCase):
    def test_stepped_segment_uses_the_new_value_at_its_end_node(self):
        nodes = [CurveNode(x=x, y=y, value_str=color) for x, y, color in (
            (0, 1.25, "0000FF"), (10, -3.5, "00FF00"),
            (20, 7.75, "FF0000"), (30, 2.5, "FFFFFF"),
        )]
        for node in nodes:
            node.segment_mode = InterpolationMode.STEPPED
        for index, node in enumerate(nodes):
            for space in ColorSpace:
                with self.subTest(index=index, space=space):
                    self.assertEqual(interpolate(nodes, node.x), node.y)
                    self.assertEqual(interpolate(nodes, node.x, is_color=True, color_space=space), node.value_str)
                    if index:
                        self.assertEqual(interpolate(nodes, node.x - 0.000001), nodes[index - 1].y)
                    if index < len(nodes) - 1:
                        self.assertEqual(interpolate(nodes, node.x + 0.000001), node.y)

    def test_mixed_modes_always_hit_the_exact_node_value(self):
        for incoming in InterpolationMode:
            for outgoing in InterpolationMode:
                nodes = [CurveNode(x=x, y=y, value_str=color) for x, y, color in (
                    (0, 1, "0000FF"), (50, -4.5, "12AB34"), (100, 9, "FFFFFF"),
                )]
                nodes[0].segment_mode = incoming
                nodes[1].segment_mode = outgoing
                for space in ColorSpace:
                    with self.subTest(incoming=incoming, outgoing=outgoing, space=space):
                        self.assertEqual(interpolate(nodes, 50), -4.5)
                        self.assertEqual(interpolate(nodes, 50, is_color=True, color_space=space), "12AB34")

    def test_gbc_stepped_color_changes_on_the_node_character(self):
        settings = GradientSettings(mode=GradientMode.GBC, tags={
            "1c": TagGradientConfig(tag="1c", enabled=True, mode=InterpolationMode.STEPPED,
                nodes=[CurveNode(x=x, value_str=color) for x, color in (
                    (0, "0000FF"), (50, "00FF00"), (100, "FF0000"),
                )]),
        })
        output = generate_gradient(ASSEvent(text="ABC"), None, settings)
        self.assertEqual(output[0].text, r"{\1c&H0000FF&}A{\1c&H00FF00&}B{\1c&HFF0000&}C")

    def test_flat_endpoint_bezier_matches_cube_root(self):
        for reverse in (False, True):
            nodes = bezier_nodes(1 if reverse else 0, 1 if reverse else 0)
            for t in (1e-10, 1e-8, 1e-6, .0001, .001, .01, .25, .5):
                x = 1 - t if reverse else t
                expected = 1 - (1 - x) ** (1 / 3) if reverse else x ** (1 / 3)
                with self.subTest(reverse=reverse, x=x):
                    self.assertAlmostEqual(
                        interpolate(nodes, x * 100, InterpolationMode.BEZIER), expected, delta=1e-7,
                    )

    def test_stationary_midpoint_bezier_matches_shifted_cube_root(self):
        nodes = bezier_nodes(1, 0)
        for offset in (-1e-3, -1e-5, -1e-6, -1e-7, 0, 1e-7, 1e-6, 1e-5, 1e-3):
            with self.subTest(offset=offset):
                x = .5 + offset
                expected = .5 + math.copysign((abs(offset) / 4) ** (1 / 3), offset)
                self.assertAlmostEqual(
                    interpolate(nodes, x * 100, InterpolationMode.BEZIER), expected, delta=1e-7,
                )

    def test_bezier_matches_independent_bisection_across_handle_grid(self):
        for h1 in (0, .1, .5, .9, 1):
            for h2 in (0, .1, .5, .9, 1):
                nodes = bezier_nodes(h1, h2)
                for x in (.0001, .01, .2, .49999, .7, .99, .9999):
                    lo, hi = 0., 1.
                    for _ in range(60):
                        mid = (lo + hi) / 2
                        if bezier_x(mid, h1, h2) < x:
                            lo = mid
                        else:
                            hi = mid
                    with self.subTest(h1=h1, h2=h2, x=x):
                        self.assertAlmostEqual(
                            interpolate(nodes, x * 100, InterpolationMode.BEZIER),
                            (lo + hi) / 2, delta=1e-7,
                        )

    def test_outside_segment_handles_still_resolve_a_valid_root(self):
        rng = random.Random(1294)
        for _ in range(150):
            h1, h2, x = rng.uniform(-2, 3), rng.uniform(-2, 3), rng.uniform(.001, .999)
            nodes = bezier_nodes(h1, h2)
            actual = interpolate(nodes, x * 100, InterpolationMode.BEZIER)
            with self.subTest(h1=h1, h2=h2, x=x):
                self.assertTrue(0 <= actual <= 1)
                self.assertAlmostEqual(bezier_x(actual, h1, h2), x, delta=1e-8)
                self.assertEqual(nodes[0].handle_out_x, h1 * 100)
                self.assertEqual(nodes[1].handle_in_x, h2 * 100)

    def test_smooth_hsl_red_to_green_passes_through_yellow(self):
        nodes = make_default_nodes(start_color="0000FF", end_color="00FF00")
        self.assertEqual(interpolate(nodes, 50, InterpolationMode.SMOOTH, True, ColorSpace.HSL), "00FFFF")
        self.assertEqual(interpolate(nodes, 50, InterpolationMode.SMOOTH, True, ColorSpace.RGB), "008080")
        self.assertEqual(interpolate(nodes, 50, InterpolationMode.SMOOTH, True, ColorSpace.OKLAB), "00A8D0")

    def test_smooth_hsl_uses_adjacent_hues_across_wrap(self):
        nodes = [CurveNode(x=x, value_str=hue_color(h)) for x, h in zip((0, 25, 75, 100), (300, 0, 60, 120))]
        for x in (25, 37.5, 50, 62.5, 75):
            with self.subTest(x=x):
                actual = interpolate(nodes, x, InterpolationMode.SMOOTH, True, ColorSpace.HSL)
                expected = hue_color(60 * (x - 25) / 50)
                for channel in (0, 2, 4):
                    self.assertAlmostEqual(int(actual[channel:channel+2], 16), int(expected[channel:channel+2], 16), delta=1)

    def test_smooth_hsl_shortest_hue_path_and_reversal(self):
        nodes = make_default_nodes(start_color=hue_color(350), end_color=hue_color(10))
        self.assertEqual(interpolate(nodes, 50, InterpolationMode.SMOOTH, True, ColorSpace.HSL), "0000FF")
        reverse = make_default_nodes(start_color=nodes[-1].value_str, end_color=nodes[0].value_str)
        for x in (0, 10, 25, 50, 75, 90, 100):
            self.assertEqual(
                interpolate(nodes, x, InterpolationMode.SMOOTH, True, ColorSpace.HSL),
                interpolate(reverse, 100 - x, InterpolationMode.SMOOTH, True, ColorSpace.HSL),
            )

    def test_smooth_color_overshoot_stays_in_gamut(self):
        nodes = [CurveNode(x=x, value_str=c) for x, c in zip((0, 25, 75, 100), ("FFFFFF", "0000FF", "00FF00", "000000"))]
        for space in ColorSpace:
            for x in range(101):
                self.assertRegex(interpolate(nodes, x, InterpolationMode.SMOOTH, True, space), r"^[0-9A-F]{6}$")

    def test_gbc_generated_color_uses_smooth_hsl(self):
        settings = GradientSettings(mode=GradientMode.GBC, tags={
            "1c": TagGradientConfig(tag="1c", enabled=True,
                nodes=make_default_nodes(start_color="0000FF", end_color="00FF00"),
                mode=InterpolationMode.SMOOTH, color_space=ColorSpace.HSL),
        })
        generated = generate_gradient(ASSEvent(text="ABC"), None, settings)
        self.assertEqual(generated[0].text, r"{\1c&H0000FF&}A{\1c&H00FFFF&}B{\1c&H00FF00&}C")


if __name__ == "__main__":
    unittest.main()
