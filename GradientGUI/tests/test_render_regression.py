"""Render regression tests for GradientGUI.

These tests exercise the real gradient generator and libass bounds renderer.
They are intentionally focused on bugs that are easy to miss with normal unit
tests: clip ranges that trim border/shadow/blur output, invalid negative tag
values, and path-sampling removal state.
"""

from __future__ import annotations

import re
import os
import shutil
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from PIL import ImageChops

ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.ass_parser import ASSEvent, parse_ass_file
from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.interpolation import make_default_nodes
from engine.libass_bounds import (
    IMAGE_TYPE_CHARACTER,
    IMAGE_TYPE_OUTLINE,
    IMAGE_TYPE_SHADOW,
    measure_event_bounds,
)
from engine.frame_sampler import FrameSampler
from engine.range_calc import RangeDebug, calculate_range_plan
from engine.tag_parser import extract_clip_bounds, parse_tags_from_text, remove_specific_tag


SAMPLE_ASS = ROOT / "GradientGUI" / "tests" / "test.ass"
SAMPLE_VIDEO = ROOT / "GradientGUI" / "tests" / "test.mp4"
FFMPEG_EXE = (
    ROOT.parent / "ffmpeg" / "ffmpeg.exe"
    if os.name == "nt"
    else shutil.which("ffmpeg")
)


class RenderRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SAMPLE_ASS.exists():
            raise RuntimeError(f"missing sample ASS fixture: {SAMPLE_ASS}")
        if not SAMPLE_VIDEO.exists():
            raise RuntimeError(f"missing sample video fixture: {SAMPLE_VIDEO}")
        if not FFMPEG_EXE or not Path(FFMPEG_EXE).exists():
            raise RuntimeError(f"missing bundled ffmpeg: {FFMPEG_EXE}")

        cls.ass = parse_ass_file(SAMPLE_ASS)
        cls.event = next(evt for evt in cls.ass.events if not evt.comment)
        cls.style = cls.ass.get_style(cls.event.style)
        cls.base_bounds = measure_event_bounds(cls.ass, cls.event)
        if cls.base_bounds is None:
            raise RuntimeError("sample subtitle did not render")
        if Path(cls.ass.video_file).name.lower() != SAMPLE_VIDEO.name.lower():
            raise RuntimeError(
                f"sample ASS should reference {SAMPLE_VIDEO.name}, got {cls.ass.video_file!r}"
            )
        cls.frame_sampler = FrameSampler(str(FFMPEG_EXE))

    def _settings(self, tag: str, start, end, mode: GradientMode = GradientMode.VERTICAL) -> GradientSettings:
        return self._settings_for_configs(
            [self._config(tag, start, end)],
            mode=mode,
        )

    def _settings_for_configs(
        self,
        configs: list[TagGradientConfig],
        mode: GradientMode = GradientMode.VERTICAL,
        angle: float = 0.0,
        step: float = 1.0,
    ) -> GradientSettings:
        settings = GradientSettings(
            mode=mode,
            angle=angle,
            step=step,
            tags={cfg.tag: cfg for cfg in configs},
        )
        settings.text_x1 = self.base_bounds.x1
        settings.text_y1 = self.base_bounds.y1
        settings.text_x2 = self.base_bounds.x2
        settings.text_y2 = self.base_bounds.y2
        return settings

    def _config(self, tag: str, start, end) -> TagGradientConfig:
        if tag in {"1c", "2c", "3c", "4c"}:
            nodes = make_default_nodes(start_color=str(start), end_color=str(end))
            return TagGradientConfig(tag=tag, enabled=True, nodes=nodes)
        if tag in {"pos", "org"}:
            sx, sy = start
            ex, ey = end
            return TagGradientConfig(
                tag=tag,
                enabled=True,
                nodes=make_default_nodes(start_y=float(sx), end_y=float(ex)),
                coord_y_nodes=make_default_nodes(start_y=float(sy), end_y=float(ey)),
            )
        nodes = make_default_nodes(start_y=float(start), end_y=float(end))
        return TagGradientConfig(tag=tag, enabled=True, nodes=nodes)

    def _generate(self, tag: str, start, end) -> list[ASSEvent]:
        settings = self._settings(tag, start, end)
        return self._generate_with_settings(settings)

    def _generate_with_settings(
        self,
        settings: GradientSettings,
        event: ASSEvent | None = None,
    ) -> list[ASSEvent]:
        event = event or self.event
        return generate_gradient(
            event,
            self.style,
            settings,
            self.base_bounds.to_meta(),
            self.ass,
        )

    def _event_with_tags(self, tag_text: str, remove_tags: tuple[str, ...]) -> ASSEvent:
        text = self.event.text
        for tag in remove_tags:
            text = remove_specific_tag(text, tag)
        return replace(self.event, text=f"{{{tag_text}}}" + text)

    def _clip_union(self, events: list[ASSEvent]) -> tuple[float, float, float, float]:
        rects = [extract_clip_bounds(evt.text) for evt in events]
        rects = [rect for rect in rects if rect is not None]
        self.assertTrue(rects, "generated events should contain clip rectangles")
        return (
            min(r[0] for r in rects),
            min(r[1] for r in rects),
            max(r[2] for r in rects),
            max(r[3] for r in rects),
        )

    def _render_union(
        self,
        events: list[ASSEvent],
        image_types: tuple[int, ...],
    ) -> tuple[float, float, float, float]:
        rects = []
        for evt in events:
            bounds = measure_event_bounds(self.ass, evt, image_types=image_types)
            if bounds is not None:
                rects.append((bounds.x1, bounds.y1, bounds.x2, bounds.y2))
        self.assertTrue(rects, "generated strip events should render visible pixels")
        return (
            min(r[0] for r in rects),
            min(r[1] for r in rects),
            max(r[2] for r in rects),
            max(r[3] for r in rects),
        )

    def assertCovers(self, outer, inner, tolerance: float = 1.5) -> None:
        self.assertLessEqual(outer[0], inner[0] + tolerance)
        self.assertLessEqual(outer[1], inner[1] + tolerance)
        self.assertGreaterEqual(outer[2], inner[2] - tolerance)
        self.assertGreaterEqual(outer[3], inner[3] - tolerance)

    def assertInside(self, inner, outer, tolerance: float = 1.5) -> None:
        self.assertGreaterEqual(inner[0], outer[0] - tolerance)
        self.assertGreaterEqual(inner[1], outer[1] - tolerance)
        self.assertLessEqual(inner[2], outer[2] + tolerance)
        self.assertLessEqual(inner[3], outer[3] + tolerance)

    def test_vertical_bord_growing_keeps_expanded_clip_envelope(self) -> None:
        generated = self._generate("bord", 0, 10)
        reference = measure_event_bounds(
            self.ass,
            self._event_with_tags(r"\bord10", ("bord", "xbord", "ybord")),
            image_types=(IMAGE_TYPE_CHARACTER, IMAGE_TYPE_OUTLINE),
        )
        self.assertIsNotNone(reference)

        clip_union = self._clip_union(generated)
        reference_rect = (reference.x1, reference.y1, reference.x2, reference.y2)
        self.assertCovers(clip_union, reference_rect)

        render_union = self._render_union(
            generated, (IMAGE_TYPE_CHARACTER, IMAGE_TYPE_OUTLINE)
        )
        self.assertGreaterEqual(render_union[3], self.base_bounds.y2 - 1.0)

    def test_sample_fixture_metadata_and_rendered_events_are_consistent(self) -> None:
        visible_events = [evt for evt in self.ass.events if not evt.comment]
        self.assertEqual(len(visible_events), 3)
        self.assertEqual(Path(self.ass.video_file).name.lower(), SAMPLE_VIDEO.name.lower())

        for index, evt in enumerate(visible_events):
            with self.subTest(event_index=index, text=evt.text):
                bounds = measure_event_bounds(self.ass, evt)
                self.assertIsNotNone(bounds, f"fixture event {index + 1} should render")

    def test_sample_video_decodes_and_changes_between_frames(self) -> None:
        sampler = self.frame_sampler
        self.assertTrue(sampler.load_frame_number(str(SAMPLE_VIDEO), 0), sampler.last_error)
        frame0 = sampler.get_image_copy()
        self.assertIsNotNone(frame0)
        self.assertEqual(frame0.size, (1920, 1080))
        self.assertEqual(sampler.frame_cache_key(), ("frame", str(SAMPLE_VIDEO), 0))

        self.assertTrue(sampler.load_frame_number(str(SAMPLE_VIDEO), 1), sampler.last_error)
        frame1 = sampler.get_image_copy()
        self.assertIsNotNone(frame1)
        self.assertEqual(frame1.size, frame0.size)
        self.assertEqual(sampler.frame_cache_key(), ("frame", str(SAMPLE_VIDEO), 1))

        diff = ImageChops.difference(frame0, frame1)
        self.assertIsNotNone(diff.getbbox(), "adjacent sample video frames should not be identical")

    def test_range_plan_can_be_debugged_without_generating_strips(self) -> None:
        settings = self._settings("bord", 0, 10)
        debug = RangeDebug(enabled=True)
        plan = calculate_range_plan(
            event=self.event,
            ass_file=self.ass,
            parsed=parse_tags_from_text(self.event.text),
            style=self.style,
            settings=settings,
            enabled_tags=settings.tags,
            base_rect=(
                self.base_bounds.x1,
                self.base_bounds.y1,
                self.base_bounds.x2,
                self.base_bounds.y2,
            ),
            source_clip_bounds=None,
            geom_context=None,
            debug=debug,
        )

        self.assertTrue(plan.direction.is_vertical)
        self.assertGreater(plan.g_max, plan.g_min)
        self.assertGreater(plan.p_max, plan.p_min)
        self.assertTrue(any(name == "final" for name, _ in debug.steps))

    def test_vertical_bord_shrinking_uses_same_expanded_envelope(self) -> None:
        growing = self._clip_union(self._generate("bord", 0, 10))
        shrinking = self._clip_union(self._generate("bord", 10, 0))
        for a, b in zip(growing, shrinking):
            self.assertAlmostEqual(a, b, delta=0.6)

    def test_source_clip_bounds_are_used_as_generation_envelope(self) -> None:
        source_clip = (700.0, 760.0, 1180.0, 850.0)
        event = replace(
            self.event,
            text=r"{\clip(700,760,1180,850)}" + self.event.text,
        )
        generated = self._generate_with_settings(
            self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL),
            event=event,
        )

        clip_union = self._clip_union(generated)
        self.assertInside(clip_union, source_clip, tolerance=0.6)
        self.assertCovers(clip_union, source_clip, tolerance=0.6)

    def test_fill_color_range_excludes_border_but_border_color_expands(self) -> None:
        event = self._event_with_tags(r"\bord20", ("bord", "xbord", "ybord"))
        fill_generated = self._generate_with_settings(
            self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL),
            event=event,
        )
        border_generated = self._generate_with_settings(
            self._settings("3c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL),
            event=event,
        )

        fill_clip = self._clip_union(fill_generated)
        border_clip = self._clip_union(border_generated)
        self.assertLess(border_clip[0], fill_clip[0] - 10.0)
        self.assertLess(border_clip[1], fill_clip[1] - 10.0)
        self.assertGreater(border_clip[2], fill_clip[2] + 10.0)
        self.assertGreater(border_clip[3], fill_clip[3] + 10.0)

    def test_negative_border_values_are_clamped_to_zero(self) -> None:
        generated = self._generate("bord", 0, -10)
        text = "\n".join(evt.text for evt in generated)
        self.assertNotRegex(text, r"\\(?:bord|xbord|ybord)-")
        self.assertRegex(text, r"\\bord0(?:\.00)?")

        clip_union = self._clip_union(generated)
        base_rect = (
            self.base_bounds.x1,
            self.base_bounds.y1,
            self.base_bounds.x2,
            self.base_bounds.y2,
        )
        self.assertCovers(clip_union, base_rect)

    def test_vertical_negative_shadow_keeps_shifted_clip_envelope(self) -> None:
        generated = self._generate("shad", 0, -10)
        reference = measure_event_bounds(
            self.ass,
            self._event_with_tags(r"\xshad-10\yshad-10", ("shad", "xshad", "yshad")),
            image_types=(IMAGE_TYPE_CHARACTER, IMAGE_TYPE_SHADOW),
        )
        self.assertIsNotNone(reference)

        clip_union = self._clip_union(generated)
        reference_rect = (reference.x1, reference.y1, reference.x2, reference.y2)
        self.assertCovers(clip_union, reference_rect)

    def test_group_range_vertical_shadow_growing_covers_shifted_shadow(self) -> None:
        settings = self._settings("shad", 0, -10)
        settings.group_range_bounds = (
            self.base_bounds.x1,
            self.base_bounds.y1,
            self.base_bounds.x2,
            self.base_bounds.y2,
        )
        settings.group_range_tags = {"shad"}
        generated = self._generate_with_settings(settings)
        reference = measure_event_bounds(
            self.ass,
            self._event_with_tags(r"\xshad-10\yshad-10", ("shad", "xshad", "yshad")),
            image_types=(IMAGE_TYPE_CHARACTER, IMAGE_TYPE_SHADOW),
        )
        self.assertIsNotNone(reference)

        self.assertCovers(
            self._clip_union(generated),
            (reference.x1, reference.y1, reference.x2, reference.y2),
        )

    def test_vertical_blur_growing_keeps_expanded_clip_envelope(self) -> None:
        generated = self._generate("blur", 0, 10)
        reference = measure_event_bounds(
            self.ass,
            self._event_with_tags(r"\blur10", ("blur",)),
            image_types=(IMAGE_TYPE_CHARACTER, IMAGE_TYPE_OUTLINE),
        )
        self.assertIsNotNone(reference)
        self.assertCovers(
            self._clip_union(generated),
            (reference.x1, reference.y1, reference.x2, reference.y2),
            tolerance=3.0,
        )

    def test_angled_group_range_with_extent_tags_renders_visible_pixels(self) -> None:
        settings = self._settings_for_configs(
            [
                self._config("1c", "0000FF", "FF0000"),
                self._config("bord", 0, 5),
                self._config("shad", 0, -8),
                self._config("blur", 0, 4),
            ],
            mode=GradientMode.ANGLED,
            angle=40.0,
        )
        settings.group_range_bounds = (
            self.base_bounds.x1,
            self.base_bounds.y1,
            self.base_bounds.x2,
            self.base_bounds.y2,
        )
        settings.group_range_tags = set(settings.tags)

        generated = self._generate_with_settings(settings)
        render_union = self._render_union(
            generated,
            (IMAGE_TYPE_CHARACTER, IMAGE_TYPE_OUTLINE, IMAGE_TYPE_SHADOW),
        )
        base_rect = (
            self.base_bounds.x1,
            self.base_bounds.y1,
            self.base_bounds.x2,
            self.base_bounds.y2,
        )
        self.assertCovers(render_union, base_rect, tolerance=6.0)

    def test_pos_gradient_generates_visible_shifted_strips(self) -> None:
        start_pos = (self.base_bounds.pos_x, self.base_bounds.pos_y)
        end_pos = (self.base_bounds.pos_x + 80.0, self.base_bounds.pos_y - 45.0)
        settings = self._settings_for_configs([
            self._config("pos", start_pos, end_pos),
        ])
        generated = self._generate_with_settings(settings)
        rendered = self._render_union(generated, (IMAGE_TYPE_CHARACTER,))
        clips = self._clip_union(generated)

        self.assertRegex("\n".join(evt.text for evt in generated), r"\\pos\(")
        self.assertCovers(clips, rendered, tolerance=2.0)
        self.assertNotAlmostEqual(rendered[0], self.base_bounds.x1, delta=1.0)

    def test_removed_sampling_path_suppresses_source_xlip(self) -> None:
        import engine.gradient as gradient_module

        event = replace(
            self.event,
            text=r"{\1xlip(m 0 0 b 10 0 20 0 30 0)}" + self.event.text,
        )
        settings = self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL)
        settings.video_path = str(SAMPLE_VIDEO)
        settings.video_frame = 0
        settings.sampling_paths = {"1c": ""}

        original_loader = gradient_module._global_sampler.load_frame_number

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("removed xlip path should not trigger frame sampling")

        gradient_module._global_sampler.load_frame_number = fail_if_called
        try:
            generated = generate_gradient(
                event,
                self.style,
                settings,
                self.base_bounds.to_meta(),
                self.ass,
            )
        finally:
            gradient_module._global_sampler.load_frame_number = original_loader

        self.assertTrue(generated)
        self.assertNotIn("xlip", "\n".join(evt.text for evt in generated))

    def test_path_sampling_prefers_exact_frame_over_time_hint(self) -> None:
        import engine.gradient as gradient_module

        settings = self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL)
        settings.video_path = str(SAMPLE_VIDEO)
        settings.video_frame = 5
        settings.video_time = 123.456
        settings.sampling_paths = {"1c": "m 0 0 l 7 0"}

        calls = []
        color_map = {i: f"{i:06X}" for i in range(1, 8)}
        keys = list(color_map)
        original_load_frame = gradient_module._global_sampler.load_frame
        original_load_frame_number = gradient_module._global_sampler.load_frame_number
        original_cache = gradient_module.get_cached_path_color_map

        def load_frame_number(path, frame_number):
            calls.append(("frame", path, frame_number))
            return True

        def fail_time_load(*_args, **_kwargs):
            raise AssertionError("path sampling should prefer video_frame over video_time")

        gradient_module._global_sampler.load_frame = fail_time_load
        gradient_module._global_sampler.load_frame_number = load_frame_number
        gradient_module.get_cached_path_color_map = (
            lambda *_args, **_kwargs: (color_map, keys)
        )
        try:
            generated = generate_gradient(
                self.event,
                self.style,
                settings,
                self.base_bounds.to_meta(),
                self.ass,
            )
        finally:
            gradient_module._global_sampler.load_frame = original_load_frame
            gradient_module._global_sampler.load_frame_number = original_load_frame_number
            gradient_module.get_cached_path_color_map = original_cache

        self.assertTrue(generated)
        self.assertEqual(calls, [("frame", str(SAMPLE_VIDEO), 5)])

    def test_path_sampling_uses_saved_sampling_frame(self) -> None:
        import engine.gradient as gradient_module

        settings = self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL)
        settings.video_path = str(SAMPLE_VIDEO)
        settings.video_frame = 5
        settings.video_time = 123.456
        settings.sampling_paths = {"1c": "m 0 0 l 7 0"}
        settings.sampling_path_frames = {"1c": 17}

        calls = []
        color_map = {i: f"{i:06X}" for i in range(1, 8)}
        keys = list(color_map)
        original_load_frame = gradient_module._global_sampler.load_frame
        original_load_frame_number = gradient_module._global_sampler.load_frame_number
        original_cache = gradient_module.get_cached_path_color_map

        def load_frame_number(path, frame_number):
            calls.append(("frame", path, frame_number))
            return True

        def fail_time_load(*_args, **_kwargs):
            raise AssertionError("saved path frame should override preview frame/time")

        gradient_module._global_sampler.load_frame = fail_time_load
        gradient_module._global_sampler.load_frame_number = load_frame_number
        gradient_module.get_cached_path_color_map = (
            lambda *_args, **_kwargs: (color_map, keys)
        )
        try:
            generated = generate_gradient(
                self.event,
                self.style,
                settings,
                self.base_bounds.to_meta(),
                self.ass,
            )
        finally:
            gradient_module._global_sampler.load_frame = original_load_frame
            gradient_module._global_sampler.load_frame_number = original_load_frame_number
            gradient_module.get_cached_path_color_map = original_cache

        self.assertTrue(generated)
        self.assertEqual(calls, [("frame", str(SAMPLE_VIDEO), 17)])

    def test_path_sampling_uses_saved_sample_snapshot_without_reloading_frame(self) -> None:
        import engine.gradient as gradient_module

        settings = self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL)
        settings.video_path = str(SAMPLE_VIDEO)
        settings.video_frame = 5
        settings.video_time = 123.456
        settings.sampling_paths = {"1c": "m 0 0 l 7 0"}
        settings.sampling_path_samples = {
            "1c": {
                "paths": [{"ass_path": "m 0 0 l 7 0"}],
                "sampling_frame": 17,
                "sampled_points": [[0, 0, 0, "010203"], [0, 7, 0, "040506"]],
            }
        }

        original_load_frame = gradient_module._global_sampler.load_frame
        original_load_frame_number = gradient_module._global_sampler.load_frame_number
        original_cache = gradient_module.get_cached_path_color_map

        def fail_load(*_args, **_kwargs):
            raise AssertionError("saved path samples should avoid frame reloading")

        gradient_module._global_sampler.load_frame = fail_load
        gradient_module._global_sampler.load_frame_number = fail_load
        gradient_module.get_cached_path_color_map = fail_load
        try:
            generated = generate_gradient(
                self.event,
                self.style,
                settings,
                self.base_bounds.to_meta(),
                self.ass,
            )
        finally:
            gradient_module._global_sampler.load_frame = original_load_frame
            gradient_module._global_sampler.load_frame_number = original_load_frame_number
            gradient_module.get_cached_path_color_map = original_cache

        self.assertTrue(generated)
        self.assertIn(r"\1c&H010203&", generated[0].text)
        self.assertIn(r"\1c&H040506&", generated[-1].text)

    def test_unsmoothed_path_sampling_uses_sample_count_for_strips(self) -> None:
        import engine.gradient as gradient_module

        settings = self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL)
        settings.step = 25.0
        settings.video_path = str(SAMPLE_VIDEO)
        settings.video_frame = 0
        settings.sampling_paths = {"1c": "m 0 0 l 7 0"}
        settings.path_sampling_smooth = {"1c": False}

        color_map = {i: f"{i:06X}" for i in range(1, 8)}
        keys = list(color_map)
        original_loader = gradient_module._global_sampler.load_frame_number
        original_cache = gradient_module.get_cached_path_color_map

        gradient_module._global_sampler.load_frame_number = lambda *_args, **_kwargs: True
        gradient_module.get_cached_path_color_map = (
            lambda *_args, **_kwargs: (color_map, keys)
        )
        try:
            generated = generate_gradient(
                self.event,
                self.style,
                settings,
                self.base_bounds.to_meta(),
                self.ass,
            )
        finally:
            gradient_module._global_sampler.load_frame_number = original_loader
            gradient_module.get_cached_path_color_map = original_cache

        self.assertEqual(len(generated), len(keys))
        self.assertIn(r"\1c&H000001&", generated[0].text)
        self.assertIn(r"\1c&H000007&", generated[-1].text)

    def test_unsmoothed_path_sampling_respects_group_range(self) -> None:
        import engine.gradient as gradient_module

        settings = self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL)
        settings.step = 25.0
        settings.video_path = str(SAMPLE_VIDEO)
        settings.video_frame = 0
        settings.sampling_paths = {"1c": "m 0 0 l 7 0"}
        settings.path_sampling_smooth = {"1c": False}
        width = self.base_bounds.x2 - self.base_bounds.x1
        settings.group_range_bounds = (
            self.base_bounds.x1 - width,
            self.base_bounds.y1,
            self.base_bounds.x2 + width,
            self.base_bounds.y2,
        )
        settings.group_range_tags = {"1c"}

        color_map = {i: f"{i:06X}" for i in range(1, 8)}
        keys = list(color_map)
        original_loader = gradient_module._global_sampler.load_frame_number
        original_cache = gradient_module.get_cached_path_color_map

        gradient_module._global_sampler.load_frame_number = lambda *_args, **_kwargs: True
        gradient_module.get_cached_path_color_map = (
            lambda *_args, **_kwargs: (color_map, keys)
        )
        try:
            generated = generate_gradient(
                self.event,
                self.style,
                settings,
                self.base_bounds.to_meta(),
                self.ass,
            )
        finally:
            gradient_module._global_sampler.load_frame_number = original_loader
            gradient_module.get_cached_path_color_map = original_cache

        self.assertGreater(len(generated), 0)
        self.assertLess(len(generated), len(keys))
        self.assertNotIn(r"\1c&H000001&", generated[0].text)
        self.assertNotIn(r"\1c&H000007&", generated[-1].text)

    def test_shifted_path_sampling_uses_group_range_phase(self) -> None:
        import engine.gradient as gradient_module

        settings = self._settings("1c", "0000FF", "FF0000", mode=GradientMode.HORIZONTAL)
        settings.step = 25.0
        settings.video_path = str(SAMPLE_VIDEO)
        settings.video_frame = 0
        settings.sampling_paths = {"1c": "m 0 0 l 7 0"}
        settings.path_sampling_smooth = {"1c": True}
        settings.color_shift_steps_by_tag = {"1c": 1.0}
        width = self.base_bounds.x2 - self.base_bounds.x1
        settings.group_range_bounds = (
            self.base_bounds.x1 - width,
            self.base_bounds.y1,
            self.base_bounds.x2 + width,
            self.base_bounds.y2,
        )
        settings.group_range_tags = {"1c"}

        color_map = {i: f"{i:06X}" for i in range(1, 8)}
        keys = list(color_map)
        original_loader = gradient_module._global_sampler.load_frame_number
        original_cache = gradient_module.get_cached_path_color_map

        gradient_module._global_sampler.load_frame_number = lambda *_args, **_kwargs: True
        gradient_module.get_cached_path_color_map = (
            lambda *_args, **_kwargs: (color_map, keys)
        )
        try:
            generated = generate_gradient(
                self.event,
                self.style,
                settings,
                self.base_bounds.to_meta(),
                self.ass,
            )
        finally:
            gradient_module._global_sampler.load_frame_number = original_loader
            gradient_module.get_cached_path_color_map = original_cache

        self.assertGreater(len(generated), len(keys))
        self.assertNotIn(r"\1c&H000001&", generated[0].text)
        self.assertNotIn(r"\1c&H000007&", generated[0].text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
