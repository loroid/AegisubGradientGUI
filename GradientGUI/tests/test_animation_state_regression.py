"""Animation output must reflect saved edits and retain numeric precision."""

from __future__ import annotations

import copy
import sys
import unittest
from dataclasses import replace
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.api import AnimationSettings, GradientMode, GradientSettings, TagGradientConfig
from engine.ass_parser import ASSEvent, ASSFile, ASSStyle
from engine.interpolation import CurveNode, InterpolationMode, make_default_nodes
from engine.path_model import PathSet
from gui import gradient_generation as generation


class AnimationStateRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        generation._GROUP_RESOLVER_CACHE.clear()
        self.addCleanup(generation._GROUP_RESOLVER_CACHE.clear)
        self.event = ASSEvent(end="0:00:00.30", text=r"{\pos(20,10)}Demo")
        self.style = ASSStyle(name="Default", fontname="Arial", fontsize=12)
        self.ass = ASSFile(styles=[self.style], events=[self.event])

    def _settings(self) -> GradientSettings:
        path = "m 0 0 l 3 0"
        return GradientSettings(
            mode=GradientMode.HORIZONTAL,
            step=10,
            text_x1=0, text_y1=0, text_x2=40, text_y2=20,
            tags={"1c": TagGradientConfig(
                tag="1c", enabled=True,
                nodes=make_default_nodes(start_color="0000FF", end_color="FF0000"),
            )},
            video_path="synthetic-video.mp4",
            sampling_paths={"1c": path},
            sampling_path_samples={"1c": PathSet.from_ass_path(
                path, sampling_frame=0,
                sampled_points=[
                    (0, i, 0, color)
                    for i, color in enumerate(["0000FF", "00FF00", "FF0000", "FFFFFF"])
                ],
            ).to_raw()},
            group_range_bounds=(0, 0, 40, 20),
            group_range_tags={"1c"},
            animation=AnimationSettings(
                enabled=True, enabled_tags={"1c"}, fps=10,
                shift_start=1, shift_end=1,
            ),
        )

    def _generate(self, settings):
        return generation.generate_gradient_events(
            [self.event], [0], self.ass, settings,
            lambda base, _idx: base,
            lambda _idx: "0,0,40,20,5,20,10,20,10",
        )

    def test_stepped_movement_changes_at_the_authored_relative_frame(self):
        settings = self._settings()
        settings.animation.shift_curves = {"1c": [
            CurveNode(x=x, y=y) for x, y in ((0, 1), (2, 4), (4, -2), (6, 0))
        ]}
        settings.animation.shift_modes = {"1c": InterpolationMode.STEPPED}
        settings.animation.event_first_frame = 50
        settings.animation.event_last_frame = 57
        settings.animation.event_start_ms = 5000
        settings.animation.event_end_ms = 5800
        settings.animation.frame_time_ms = {frame: frame * 100 for frame in range(50, 59)}
        evt = ASSEvent(start="0:00:05.00", end="0:00:05.80", text="ABCD")
        segments = generation._animation_segments(evt, settings)
        self.assertEqual([s.start_ms for s in segments], list(range(5000, 5800, 100)))
        self.assertEqual([s.shift_by_tag["1c"] for s in segments], [0, 1, 2, 6, 10, 8, 6, 6])

    def test_saved_sample_edits_have_identical_warm_and_cold_animation_output(self):
        for use_transform in (True, False):
            for edit in ("color", "delete", "reorder"):
                with self.subTest(use_transform=use_transform, edit=edit):
                    generation._GROUP_RESOLVER_CACHE.clear()
                    settings = self._settings()
                    settings.animation.use_transform = use_transform
                    initial = self._generate(settings)
                    self.assertTrue(generation._GROUP_RESOLVER_CACHE)
                    points = settings.sampling_path_samples["1c"]["sampled_points"]
                    if edit == "color":
                        points[-1][-1] = "FF00FF"
                    elif edit == "delete":
                        points.pop()
                    else:
                        points[0][-1], points[-1][-1] = points[-1][-1], points[0][-1]

                    warm = self._generate(settings)
                    generation._GROUP_RESOLVER_CACHE.clear()
                    cold = self._generate(settings)

                    self.assertNotEqual(initial, cold)
                    self.assertEqual(warm, cold)

    def test_resolver_cache_tracks_style_values_and_saved_sampling_frames(self):
        settings = self._settings()
        baseline = generation._group_transform_resolver_cache_key(
            self.event, self.style, settings, None, ["1c"],
        )
        changed_style = replace(self.style, fontsize=24)
        self.assertNotEqual(baseline, generation._group_transform_resolver_cache_key(
            self.event, changed_style, settings, None, ["1c"],
        ))
        changed_settings = copy.deepcopy(settings)
        changed_settings.sampling_path_frames["1c"] = 5
        self.assertNotEqual(baseline, generation._group_transform_resolver_cache_key(
            self.event, self.style, changed_settings, None, ["1c"],
        ))

    def test_mirrored_step_direction_invalidates_cached_animation_targets(self):
        for transform in (False, True):
            with self.subTest(transform=transform):
                generation._GROUP_RESOLVER_CACHE.clear()
                settings = self._settings()
                settings.animation.use_transform = transform
                settings.sampling_paths = {}
                settings.sampling_path_samples = {}
                settings.tags["1c"].nodes = [
                    CurveNode(x=x, value_str=color, segment_mode=InterpolationMode.STEPPED)
                    for x, color in ((0, "0000FF"), (50, "00FF00"), (100, "FF0000"))
                ]
                before = self._generate(settings)
                self.assertTrue(generation._GROUP_RESOLVER_CACHE)
                settings.tags["1c"].nodes[0].step_from_right = True
                warm = self._generate(settings)
                generation._GROUP_RESOLVER_CACHE.clear()
                cold = self._generate(settings)
                self.assertNotEqual(before, cold)
                self.assertEqual(warm, cold)

    def test_shear_animation_seam_retains_sub_cent_precision(self):
        for tag in ("fax", "fay"):
            with self.subTest(tag=tag):
                blended = generation._with_seam_blend_sequences(
                    {tag: [f"\\{tag}0", f"\\{tag}0.003000"]},
                    AnimationSettings(seam_blend_lengths={tag: 2}),
                )
                self.assertEqual(blended[tag][-2:], [f"\\{tag}0.002000", f"\\{tag}0.001000"])

    def test_shadow_seam_blends_signed_offsets_in_both_directions(self):
        for head, tail, expected in (
            (r"\shad6", r"\xshad-6\yshad-6", (-2, 2)),
            (r"\xshad-6\yshad-6", r"\shad6", (2, -2)),
            (r"\xshad-6\yshad-6", r"\xshad-12\yshad-12", (-10, -8)),
        ):
            with self.subTest(head=head, tail=tail):
                blended = generation._with_seam_blend_sequences(
                    {"shad": [head, tail]},
                    AnimationSettings(seam_blend_lengths={"shad": 2}),
                )["shad"]
                self.assertEqual(len(blended), 4)
                for value, offset in zip(blended[2:], expected):
                    self.assertEqual(generation._extract_numeric_tag_value("xshad", value), offset)
                    self.assertEqual(generation._extract_numeric_tag_value("yshad", value), offset)


if __name__ == "__main__":
    unittest.main()
