"""Slicing a gradient must not restart the source subtitle's own animation."""

from __future__ import annotations

import copy
import ctypes
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.api import AnimationSettings, GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.ass_parser import ASSEvent, ASSFile, ASSStyle
from engine.ass_timing import retime_event_text
from engine.interpolation import CurveNode, InterpolationMode, make_default_nodes, mirror_nodes_horizontal
from engine.libass_bounds import _load_libass, _configure_library
from engine.path_model import PathSet
from gui.gradient_generation import _animation_segments, generate_gradient_events


class SourceTimingTest(unittest.TestCase):
    def setUp(self):
        self.style = ASSStyle(name="Default", fontname="Arial", fontsize=24, alignment=7)
        self.ass = ASSFile(
            script_info={"PlayResX": "320", "PlayResY": "180"},
            styles=[self.style],
        )
        self.lib = _load_libass()
        self.library = self.lib.ass_library_init()
        self.assertTrue(self.library)
        _configure_library(self.lib, self.library)
        self.renderer = self.lib.ass_renderer_init(self.library)
        self.assertTrue(self.renderer)
        self.lib.ass_set_frame_size(self.renderer, 320, 180)
        self.lib.ass_set_storage_size(self.renderer, 320, 180)
        self.lib.ass_set_fonts(self.renderer, None, b"Arial", 1, None, 1)
        self.addCleanup(self.close)

    def close(self):
        self.lib.ass_renderer_done(self.renderer)
        self.lib.ass_library_done(self.library)

    def render(self, events, times):
        data = self.ass.to_render_string(events).encode("utf-8")
        buffer = ctypes.create_string_buffer(data)
        track = self.lib.ass_read_memory(self.library, buffer, len(data), b"UTF-8")
        self.assertTrue(track)
        frames = []
        try:
            for now in times:
                frame = Image.new("RGBA", (320, 180))
                changed = ctypes.c_int()
                current = self.lib.ass_render_frame(self.renderer, track, now, ctypes.byref(changed))
                while current:
                    item = current.contents
                    if item.w > 0 and item.h > 0:
                        address = ctypes.cast(item.bitmap, ctypes.c_void_p).value
                        bitmap = b"".join(
                            ctypes.string_at(address + y * item.stride, item.w)
                            for y in range(item.h)
                        )
                        mask = Image.frombytes("L", (item.w, item.h), bitmap)
                        opacity = 255 - (item.color & 255)
                        mask = mask.point([value * opacity // 255 for value in range(256)])
                        color = tuple((item.color >> shift) & 255 for shift in (24, 16, 8))
                        layer = Image.new("RGBA", mask.size, (*color, 255))
                        layer.putalpha(mask)
                        frame.alpha_composite(layer, (item.dst_x, item.dst_y))
                    current = item.next
                frames.append(frame)
        finally:
            self.lib.ass_free_track(track)
        return frames

    def settings(self, mode, use_transform=False):
        return GradientSettings(
            mode=mode, step=320,
            text_x1=0, text_y1=0, text_x2=320, text_y2=180,
            tags={"1c": TagGradientConfig(
                tag="1c", enabled=True,
                nodes=make_default_nodes(start_color="FFFFFF", end_color="FFFFFF"),
            )},
            animation=AnimationSettings(
                enabled=True, enabled_tags={"1c"}, use_transform=use_transform,
                fps=10, shift_start=0, shift_end=0,
            ),
        )

    def test_stepped_movement_render_matches_independent_frame_shifts(self):
        self.assert_stepped_movement_render(mirrored=False)

    def test_mirrored_stepped_movement_render_matches_independent_frame_shifts(self):
        self.assert_stepped_movement_render(mirrored=True)

    def assert_stepped_movement_render(self, *, mirrored):
        event = ASSEvent(start="0:00:05.00", end="0:00:05.80", text=(
            r"{\pos(0,0)\an7\bord0\shad0\p1}m 0 0 l 320 0 320 180 0 180"
        ))
        shifts = [0, 0, -2, -4, 0, 4, 5, 6] if mirrored else [0, 1, 2, 6, 10, 8, 6, 6]
        times = [5050 + frame * 100 for frame in range(len(shifts))]
        for path_sampling in (False, True):
            for transform in (False, True):
                with self.subTest(path_sampling=path_sampling, transform=transform):
                    settings = self.settings(GradientMode.HORIZONTAL, transform)
                    settings.step = 64
                    settings.tags["1c"].nodes = make_default_nodes(start_color="0000FF", end_color="FF0000")
                    settings.animation.shift_curves = {"1c": [
                        CurveNode(x=x, y=y) for x, y in ((0, 1), (2, 4), (4, -2), (6, 0))
                    ]}
                    if mirrored:
                        settings.animation.shift_curves["1c"] = mirror_nodes_horizontal(settings.animation.shift_curves["1c"])
                    settings.animation.shift_modes = {"1c": InterpolationMode.STEPPED}
                    settings.animation.event_first_frame = 50
                    settings.animation.event_last_frame = 57
                    settings.animation.event_start_ms = 5000
                    settings.animation.event_end_ms = 5800
                    settings.animation.frame_time_ms = {frame: frame * 100 for frame in range(50, 59)}
                    if path_sampling:
                        path = PathSet.from_ass_path("m 0 0 l 4 0", sampling_frame=0, sampled_points=[
                            (0, i, 0, color) for i, color in enumerate(("0000FF", "00FF00", "FF0000", "00FFFF", "FFFFFF"))
                        ])
                        settings.sampling_paths = {"1c": path.to_ass_path()}
                        settings.sampling_path_samples = {"1c": path.to_raw()}
                    animated = generate_gradient_events(
                        [event], [0], self.ass, settings, lambda base, _idx: base, lambda _idx: None,
                    )
                    frames = self.render(animated, times)
                    self.assertGreater(len({frame.tobytes() for frame in frames}), 2)
                    for now, shift, frame in zip(times, shifts, frames):
                        static_settings = copy.deepcopy(settings)
                        static_settings.animation.enabled = False
                        static_settings.color_shift_steps_by_tag = {"1c": shift}
                        static = generate_gradient(event, self.style, static_settings, None, self.ass)
                        expected = self.render(static, [now])[0]
                        self.assertTrue(expected.getbbox())
                        self.assertEqual(frame.tobytes(), expected.tobytes(), f"frame time {now}")

    def assert_render_preserved(self, source, mode, use_transform=False):
        event = ASSEvent(start="0:00:01.00", end="0:00:02.00", text=source)
        settings = self.settings(mode, use_transform)
        expected = generate_gradient(event, self.style, copy.deepcopy(settings), None, self.ass)
        actual = generate_gradient_events(
            [event], [0], self.ass, settings,
            lambda base, _idx: base, lambda _idx: None,
        )
        times = [1000, 1050, 1100, 1250, 1400, 1499, 1500, 1501, 1750, 1999]
        reference_frames = self.render(expected, times)
        actual_frames = self.render(actual, times)
        self.assertTrue(any(frame.getbbox() for frame in reference_frames))
        for now, reference, rendered in zip(times, reference_frames, actual_frames):
            with self.subTest(now=now):
                self.assertIsNone(
                    ImageChops.difference(reference, rendered).getbbox(alpha_only=False),
                    f"source timing changed at {now} ms",
                )

    def test_split_preserves_source_transforms(self):
        for mode in (GradientMode.HORIZONTAL, GradientMode.GBC):
            for transform in (
                r"\t(\fscx150)", r"\t(2,\fscx150)",
                r"\t(0,1000,\fscx150)", r"\t(200,500,2,\fscx150)",
                r"\t(200,0,\fscx150)", r"\t(0,500,\frz15)\t(500,1000,\frz0)",
                r"\t(500,500,\frz15)",
            ):
                with self.subTest(mode=mode, transform=transform):
                    self.assert_render_preserved(
                        r"{\an7\pos(40,30)\clip(0,0,320,180)" + transform + r"}AB\NCD", mode,
                    )

    def test_split_preserves_source_movement_and_fades(self):
        for mode in (GradientMode.HORIZONTAL, GradientMode.GBC):
            for tags in (
                r"\move(40,30,120,70)", r"\move(40,30,120,70,200,500)",
                r"\move(40,30,120,70,0,0)", r"\move(40,30,120,70,500,200)",
                r"\move(40,30,120,70,500,500)",
                r"\pos(40,30)\fad(300,400)",
                r"\pos(40,30)\fade(255,0,128,100,300,600,900)",
            ):
                with self.subTest(mode=mode, tags=tags):
                    self.assert_render_preserved(
                        r"{\an7\clip(0,0,320,180)" + tags + "}ABCD", mode,
                    )

    def test_split_preserves_karaoke_progress(self):
        for mode in (GradientMode.HORIZONTAL, GradientMode.GBC):
            for text in (r"{\kf50}AB{\kf50}CD", r"{\kt10\ko20}AB{\kt60\k20}CD"):
                with self.subTest(mode=mode, text=text):
                    self.assert_render_preserved(
                        r"{\an7\pos(40,30)\2c&H0000FF&\clip(0,0,320,180)}" + text, mode,
                    )

    def test_transform_output_keeps_source_animation_clock(self):
        self.assert_render_preserved(
            r"{\an7\move(40,30,120,70)\fad(300,400)\t(2,\frz20)\clip(0,0,320,180)}ABCD",
            GradientMode.HORIZONTAL, use_transform=True,
        )

    def test_drawing_and_parenthesized_transform_content_keep_their_clock(self):
        self.assert_render_preserved(
            r"{\an7\pos(40,30)\clip(0,0,320,180)\t(2,\frz20)\p1}"
            r"m 0 0 l 30 0 30 30 0 30{\p0}AB\NCD",
            GradientMode.GBC,
        )
        self.assert_render_preserved(
            r"{\an7\pos(40,30)\clip(0,0,320,180)\t(0,1000,\clip(0,0,60,90)\frz10)}ABCD",
            GradientMode.GBC,
        )

    def test_changing_gradient_phase_and_prefix_suffix_preserve_source_clock(self):
        for mode in (GradientMode.HORIZONTAL, GradientMode.GBC):
            for fade_gradient in (False, True):
                with self.subTest(mode=mode, fade_gradient=fade_gradient):
                    event = ASSEvent(
                        start="0:00:01.00", end="0:00:02.00",
                        text=r"{\an7\move(40,30,120,70)\fad(200,200)\t(2,\frz10)\clip(0,0,320,180)}ABCD",
                    )
                    settings = self.settings(mode)
                    settings.step = 20
                    settings.tags["1c"].nodes = make_default_nodes(start_color="0000FF", end_color="FF0000")
                    settings.animation.shift_start = settings.animation.shift_end = 1
                    settings.animation.start_frame = 2
                    settings.animation.end_frame = 7
                    if fade_gradient:
                        settings.tags["fad"] = TagGradientConfig(
                            tag="fad", enabled=True,
                            nodes=make_default_nodes(start_y=100, end_y=400),
                            coord_y_nodes=make_default_nodes(start_y=400, end_y=100),
                        )
                        settings.animation.enabled_tags.add("fad")
                    actual = generate_gradient_events(
                        [event], [0], self.ass, settings,
                        lambda base, _idx: base, lambda _idx: None,
                    )
                    segments = _animation_segments(event, settings)
                    times = [1050, 1250, 1550, 1850, 1999]
                    rendered = self.render(actual, times)
                    for now, image in zip(times, rendered):
                        segment = next(segment for segment in segments if segment.start_ms <= now < segment.end_ms)
                        shifted = copy.deepcopy(settings)
                        shifted.color_shift_steps_by_tag = segment.shift_by_tag.copy()
                        expected = generate_gradient(event, self.style, shifted, None, self.ass)
                        with self.subTest(now=now):
                            reference = self.render(expected, [now])[0]
                            self.assertIsNone(ImageChops.difference(reference, image).getbbox(alpha_only=False))

    def test_retimer_does_not_edit_literal_text_or_unrelated_tags(self):
        source = r"AB \t(100,200,\fs40) {\fnArial\pos(1,2)\pbo5}CD\N"
        self.assertEqual(retime_event_text(source, 200, 1000), source)
        malformed = r"{\t(bad,500,\fs40)\move(1,2,3)\fad(bad,3)}AB"
        self.assertEqual(retime_event_text(malformed, 200, 1000), malformed)

    def test_split_retiming_uses_serialized_centisecond_boundaries(self):
        event = ASSEvent(
            start="0:00:01.03", end="0:00:01.47",
            text=r"{\an7\move(40,30,120,70)\fad(100,150)\t(2,\frz20)\clip(0,0,320,180)}ABCD",
        )
        settings = self.settings(GradientMode.HORIZONTAL)
        settings.animation.event_first_frame = 73
        settings.animation.event_last_frame = 76
        settings.animation.event_start_ms = 1030
        settings.animation.event_end_ms = 1470
        settings.animation.frame_time_ms = {73: 1030, 74: 1113, 75: 1237, 76: 1359, 77: 1470}
        expected = generate_gradient(event, self.style, settings, None, self.ass)
        actual = generate_gradient_events(
            [event], [0], self.ass, settings,
            lambda base, _idx: base, lambda _idx: None,
        )
        times = [1030, 1070, 1110, 1113, 1240, 1360, 1469]
        for now, reference, rendered in zip(times, self.render(expected, times), self.render(actual, times)):
            with self.subTest(now=now):
                self.assertIsNone(ImageChops.difference(reference, rendered).getbbox(alpha_only=False))


if __name__ == "__main__":
    unittest.main()
