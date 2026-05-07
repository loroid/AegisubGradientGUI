"""Tests for color-band cyclic animation generation."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.api import AnimationSettings, GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.ass_parser import ASSEvent, ASSFile, ASSStyle
from engine.interpolation import CurveNode, InterpolationMode, make_default_nodes
from gui.gradient_generation import (
    _animation_segments,
    _build_group_transform_target_resolver,
    _group_transform_resolver_cache_key,
    _with_seam_blend_sequences,
    generate_gradient_events,
)


class ColorBandAnimationTest(unittest.TestCase):
    def _settings(self) -> GradientSettings:
        config = TagGradientConfig(
            tag="1c",
            enabled=True,
            nodes=make_default_nodes(start_color="0000FF", end_color="FF0000"),
        )
        settings = GradientSettings(
            mode=GradientMode.HORIZONTAL,
            step=10.0,
            tags={"1c": config},
        )
        settings.text_x1 = 0.0
        settings.text_y1 = 0.0
        settings.text_x2 = 40.0
        settings.text_y2 = 20.0
        settings.animation = AnimationSettings(
            enabled=True,
            frame_step=1,
            shift_start=1.0,
            shift_end=1.0,
            direction=1,
            fps=10.0,
        )
        return settings

    def test_ass_file_reads_exported_frame_metadata(self) -> None:
        ass = ASSFile(gradient_metadata={
            "Event Start MS 1": "50",
            "Event End MS 1": "450",
            "Event Frame Start 1": "100",
            "Event Frame End 1": "103",
            "Event Frame Times 1": "100:50,101:133,102:240,103:360,104:450",
        })

        info = ass.animation_frame_info(0)

        self.assertIsNotNone(info)
        self.assertEqual(info["first_frame"], 100)
        self.assertEqual(info["last_frame"], 103)
        self.assertEqual(info["event_start_ms"], 50)
        self.assertEqual(info["event_end_ms"], 450)
        self.assertEqual(info["frame_time_ms"][102], 240)

    def test_color_band_animation_uses_transform_by_default(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            self._settings(),
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertEqual(len(result), 4)
        self.assertEqual({evt.start for evt in result}, {"0:00:00.00"})
        self.assertEqual({evt.end for evt in result}, {"0:00:00.20"})
        self.assertIn(r"\t(100,101,\1c&HBF0040&)", result[0].text)

        first_segment_first_color = self._first_color(result[0])
        self.assertEqual(first_segment_first_color, "0000FF")

    def test_color_band_animation_can_split_events_when_transform_is_disabled(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        settings = self._settings()
        settings.animation.use_transform = False

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertEqual(len(result), 8)
        self.assertEqual({evt.start for evt in result[:4]}, {"0:00:00.00"})
        self.assertEqual({evt.end for evt in result[:4]}, {"0:00:00.10"})
        self.assertEqual({evt.start for evt in result[4:]}, {"0:00:00.10"})
        self.assertEqual({evt.end for evt in result[4:]}, {"0:00:00.20"})
        self.assertEqual(self._first_color(result[4]), "BF0040")
        self.assertNotIn(r"\t(", "\n".join(evt.text for evt in result))

    def test_strip_generation_adds_static_pos_for_layout_positioned_lines(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            margin_l=10,
            margin_r=20,
            margin_v=30,
            text=r"{\an2}Test",
        )
        style = ASSStyle(name="Default", alignment=2, margin_l=100, margin_r=100, margin_v=100)
        ass = ASSFile(
            script_info={"PlayResX": "1920", "PlayResY": "1080"},
            styles=[style],
            events=[event],
        )

        result = generate_gradient(event, style, self._settings(), None, ass)

        self.assertGreater(len(result), 1)
        self.assertTrue(all(r"\pos(955.00,1050.00)" in evt.text for evt in result))

    def test_color_band_animation_respects_relative_start_and_end_frames(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.40",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        settings = self._settings()
        settings.animation.start_frame = 1
        settings.animation.end_frame = 2

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertEqual(len(result), 4)
        self.assertEqual({evt.start for evt in result}, {"0:00:00.00"})
        self.assertEqual({evt.end for evt in result}, {"0:00:00.40"})
        self.assertEqual(self._first_color(result[0]), "0000FF")
        self.assertNotIn(r"\t(100,101", result[0].text)
        self.assertIn(r"\t(200,201,\1c&HBF0040&)", result[0].text)

    def test_color_band_animation_can_use_shift_curve(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        settings = self._settings()
        settings.animation.shift_curves = {
            "1c": [
                CurveNode(x=0.0, y=2.0),
                CurveNode(x=1.0, y=2.0),
            ]
        }
        settings.animation.shift_modes = {"1c": InterpolationMode.LINEAR}

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertIn(r"\t(100,101,\1c&H800080&)", result[0].text)

    def test_animation_frame_step_can_be_set_per_tag(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.40",
            style="Default",
            text="{}Test",
        )
        settings = self._settings()
        settings.tags["3c"] = TagGradientConfig(
            tag="3c",
            enabled=True,
            nodes=make_default_nodes(start_color="000000", end_color="FFFFFF"),
        )
        settings.animation.enabled_tags = {"1c", "3c"}
        settings.animation.frame_steps = {"1c": 1, "3c": 2}

        segments = _animation_segments(event, settings)
        shifts = [segment.shift_by_tag for segment in segments]

        self.assertEqual(
            shifts,
            [
                {"1c": 0.0, "3c": 0.0},
                {"1c": 1.0, "3c": 0.0},
                {"1c": 2.0, "3c": 1.0},
                {"1c": 3.0, "3c": 1.0},
            ],
        )

    def test_transform_animation_uses_group_range_phase_for_targets(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        config = TagGradientConfig(
            tag="1c",
            enabled=True,
            nodes=make_default_nodes(start_color="000000", end_color="FFFFFF"),
        )
        settings = GradientSettings(
            mode=GradientMode.VERTICAL,
            step=1.0,
            tags={"1c": config},
        )
        settings.text_x1 = 0.0
        settings.text_y1 = 10.0
        settings.text_x2 = 10.0
        settings.text_y2 = 12.0
        settings.group_range_bounds = (0.0, 0.0, 10.0, 100.0)
        settings.group_range_tags = {"1c"}
        settings.animation = AnimationSettings(
            enabled=True,
            frame_step=1,
            fps=10.0,
            shift_curves={
                "1c": [
                    CurveNode(x=0.0, y=1.0),
                    CurveNode(x=1.0, y=1.0),
                ],
            },
            shift_modes={"1c": InterpolationMode.LINEAR},
        )

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertIn(r"\1c&H1A1A1A&", result[0].text)
        self.assertIn(r"\t(100,101,\1c&H171717&)", result[0].text)
        self.assertNotIn(r"\t(100,101,\1c&H1C1C1C&)", result[0].text)

    def test_split_animation_uses_group_range_phase_for_targets(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        config = TagGradientConfig(
            tag="1c",
            enabled=True,
            nodes=make_default_nodes(start_color="000000", end_color="FFFFFF"),
        )
        settings = GradientSettings(
            mode=GradientMode.VERTICAL,
            step=1.0,
            tags={"1c": config},
        )
        settings.text_x1 = 0.0
        settings.text_y1 = 10.0
        settings.text_x2 = 10.0
        settings.text_y2 = 12.0
        settings.group_range_bounds = (0.0, 0.0, 10.0, 100.0)
        settings.group_range_tags = {"1c"}
        settings.animation = AnimationSettings(
            enabled=True,
            use_transform=False,
            frame_step=1,
            fps=10.0,
            shift_curves={
                "1c": [
                    CurveNode(x=0.0, y=1.0),
                    CurveNode(x=1.0, y=1.0),
                ],
            },
            shift_modes={"1c": InterpolationMode.LINEAR},
        )

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        joined = "\n".join(evt.text for evt in result)
        self.assertIn(r"\1c&H1A1A1A&", joined)
        self.assertIn(r"\1c&H171717&", result[2].text)
        self.assertIn(r"\1c&H1A1A1A&", result[3].text)
        self.assertNotIn(r"\1c&H1C1C1C&", "\n".join(evt.text for evt in result[2:]))
        self.assertNotIn(r"\t(", joined)

    def test_transform_animation_applies_seam_blend_to_group_range_targets(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        config = TagGradientConfig(
            tag="1c",
            enabled=True,
            nodes=make_default_nodes(start_color="000000", end_color="FFFFFF"),
        )
        settings = GradientSettings(
            mode=GradientMode.VERTICAL,
            step=1.0,
            tags={"1c": config},
        )
        settings.text_x1 = 0.0
        settings.text_y1 = 0.0
        settings.text_x2 = 10.0
        settings.text_y2 = 1.0
        settings.group_range_bounds = (0.0, 0.0, 10.0, 4.0)
        settings.group_range_tags = {"1c"}
        settings.animation = AnimationSettings(
            enabled=True,
            frame_step=1,
            fps=10.0,
            seam_blend_lengths={"1c": 2},
            shift_curves={
                "1c": [
                    CurveNode(x=0.0, y=1.0),
                    CurveNode(x=1.0, y=1.0),
                ],
            },
            shift_modes={"1c": InterpolationMode.LINEAR},
        )

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertIn(r"\t(100,101,\1c&H404040&)", result[0].text)
        self.assertNotIn(r"\t(100,101,\1c&HBFBFBF&)", result[0].text)

    def test_group_transform_cache_key_tracks_enabled_geometry_tags(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        style = ASSStyle(name="Default")
        settings = GradientSettings(
            mode=GradientMode.HORIZONTAL,
            step=1.0,
            tags={
                "1c": TagGradientConfig(
                    tag="1c",
                    enabled=True,
                    nodes=make_default_nodes(start_color="000000", end_color="FFFFFF"),
                ),
            },
        )
        settings.group_range_bounds = (0.0, 0.0, 100.0, 20.0)
        settings.group_range_tags = {"1c", "fscx"}
        settings.animation = AnimationSettings(enabled=True, enabled_tags={"1c"})
        base_meta = "0,0,100,20,5,50,10,50,10"

        base_key = _group_transform_resolver_cache_key(
            event,
            style,
            settings,
            base_meta,
            ["1c"],
        )
        settings.tags["fscx"] = TagGradientConfig(
            tag="fscx",
            enabled=True,
            nodes=make_default_nodes(start_y=100.0, end_y=150.0),
        )
        with_geometry_key = _group_transform_resolver_cache_key(
            event,
            style,
            settings,
            base_meta,
            ["1c"],
        )

        self.assertNotEqual(base_key, with_geometry_key)

    def test_static_geometry_tag_does_not_pollute_group_transform_color_sequence(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text=r"{\pos(5,11)}Test",
        )
        style = ASSStyle(name="Default", fontsize=20.0, alignment=5)
        ass = ASSFile(styles=[style], events=[event])
        settings = self._group_range_settings_with_static_fscx()
        base_meta = "0,10,10,12,5,5,11,5,11"
        with_geometry = _build_group_transform_target_resolver(
            event,
            style,
            settings,
            base_meta,
            ["1c"],
        )
        settings_without_geometry = self._group_range_settings_with_static_fscx()
        settings_without_geometry.tags["fscx"].enabled = False
        without_geometry = _build_group_transform_target_resolver(
            event,
            style,
            settings_without_geometry,
            base_meta,
            ["1c"],
        )

        del ass
        self.assertIsNotNone(with_geometry)
        self.assertIsNotNone(without_geometry)
        self.assertEqual(
            with_geometry.sequences["1c"].values,
            without_geometry.sequences["1c"].values,
        )

    def test_color_band_animation_can_blend_cyclic_seam(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        settings = self._settings()
        settings.animation.seam_blend_length = 2

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertIn(r"\t(100,101,\1c&H4000BF&)", result[0].text)
        self.assertNotIn(r"\t(100,101,\1c&HBF0040&)", result[0].text)

    def test_seam_blend_length_can_be_set_per_tag(self) -> None:
        animation = AnimationSettings(seam_blend_lengths={"1c": 2})
        sequences = {
            "1c": [r"\1c&H0000FF&", r"\1c&HFF0000&"],
            "3c": [r"\3c&H000000&", r"\3c&HFFFFFF&"],
        }

        blended = _with_seam_blend_sequences(sequences, animation)

        self.assertEqual(
            blended["1c"],
            [
                r"\1c&H0000FF&",
                r"\1c&HFF0000&",
                r"\1c&HAA0055&",
                r"\1c&H5500AA&",
            ],
        )
        self.assertEqual(blended["3c"], sequences["3c"])

    def test_transform_animation_does_not_reslice_when_numeric_tags_are_enabled(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        style = ASSStyle(name="Default")
        ass = ASSFile(styles=[style], events=[event])
        settings = self._settings()
        settings.tags["bord"] = TagGradientConfig(
            tag="bord",
            enabled=True,
            nodes=make_default_nodes(start_y=0.0, end_y=5.0),
        )

        settings.animation.enabled = False
        base_events = generate_gradient(event, style, settings, None, ass)
        settings.animation.enabled = True

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertEqual(len(result), len(base_events))
        self.assertTrue(any(r"\t(" in evt.text for evt in result))
        self.assertIn(r"\bord", result[0].text)

    def test_animation_enabled_tags_limit_which_tags_shift(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        settings = self._settings()
        settings.tags["bord"] = TagGradientConfig(
            tag="bord",
            enabled=True,
            nodes=make_default_nodes(start_y=0.0, end_y=5.0),
        )
        settings.animation.enabled_tags = {"1c"}

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        joined = "\n".join(evt.text for evt in result)
        self.assertIn(r"\bord", joined)
        self.assertIn(r"\t(100,101,\1c", result[0].text)
        self.assertNotRegex(joined, r"\\t\([^)]*\\bord")

    def test_numeric_tag_animation_uses_transform_by_default(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        style = ASSStyle(name="Default")
        ass = ASSFile(styles=[style], events=[event])
        settings = GradientSettings(
            mode=GradientMode.HORIZONTAL,
            step=10.0,
            tags={
                "bord": TagGradientConfig(
                    tag="bord",
                    enabled=True,
                    nodes=make_default_nodes(start_y=0.0, end_y=9.0),
                )
            },
        )
        settings.text_x1 = 0.0
        settings.text_y1 = 0.0
        settings.text_x2 = 40.0
        settings.text_y2 = 20.0
        settings.animation = AnimationSettings(
            enabled=True,
            frame_step=1,
            shift_start=1.0,
            shift_end=1.0,
            fps=10.0,
        )

        base_events = generate_gradient(event, style, settings, None, ass)
        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertEqual(len(result), len(base_events))
        self.assertIn(r"\t(100,101,\bord", result[0].text)

    def test_non_transformable_tag_animation_falls_back_to_split_events(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.20",
            style="Default",
            text="{}Test",
        )
        style = ASSStyle(name="Default")
        ass = ASSFile(styles=[style], events=[event])
        pos_cfg = TagGradientConfig(
            tag="pos",
            enabled=True,
            nodes=make_default_nodes(start_y=0.0, end_y=30.0),
            coord_y_nodes=make_default_nodes(start_y=0.0, end_y=0.0),
        )
        settings = GradientSettings(
            mode=GradientMode.HORIZONTAL,
            step=10.0,
            tags={"pos": pos_cfg},
        )
        settings.text_x1 = 0.0
        settings.text_y1 = 0.0
        settings.text_x2 = 40.0
        settings.text_y2 = 20.0
        settings.animation = AnimationSettings(
            enabled=True,
            frame_step=1,
            shift_start=1.0,
            shift_end=1.0,
            fps=10.0,
        )

        base_events = generate_gradient(event, style, settings, None, ass)
        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertGreater(len(result), len(base_events))
        self.assertNotIn(r"\t(", "\n".join(evt.text for evt in result))
        self.assertEqual({evt.start for evt in result[:len(base_events)]}, {"0:00:00.00"})
        self.assertEqual({evt.start for evt in result[len(base_events):]}, {"0:00:00.10"})
        self.assertIn(r"\pos", result[0].text)
        self.assertIn(r"\pos", result[len(base_events)].text)
        self.assertNotEqual(result[0].text, result[len(base_events)].text)

    def test_color_band_animation_uses_exported_video_frame_boundaries(self) -> None:
        event = ASSEvent(
            start="0:00:00.05",
            end="0:00:00.45",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        settings = self._settings()
        settings.animation.start_frame = 1
        settings.animation.end_frame = 2
        settings.animation.event_first_frame = 100
        settings.animation.event_last_frame = 103
        settings.animation.event_start_ms = 50
        settings.animation.event_end_ms = 450
        settings.animation.frame_time_ms = {
            100: 50,
            101: 133,
            102: 240,
            103: 360,
            104: 450,
        }

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertEqual(len(result), 4)
        self.assertIn(r"\t(190,191,\1c&HBF0040&)", result[0].text)
        self.assertNotIn(r"\t(200,201", result[0].text)

    def test_end_frame_zero_means_first_frame_not_last_frame(self) -> None:
        event = ASSEvent(
            start="0:00:00.00",
            end="0:00:00.30",
            style="Default",
            text="{}Test",
        )
        ass = ASSFile(styles=[ASSStyle(name="Default")], events=[event])
        settings = self._settings()
        settings.animation.end_frame = 0

        result = generate_gradient_events(
            [event],
            [0],
            ass,
            settings,
            lambda settings, _idx: settings,
            lambda _idx: None,
        )

        self.assertEqual(len(result), 4)
        self.assertFalse(any(r"\t(" in evt.text for evt in result))

    def _first_color(self, event: ASSEvent) -> str:
        match = re.search(r"\\1c&H([0-9A-F]{6})&", event.text)
        self.assertIsNotNone(match)
        return match.group(1)

    def _group_range_settings_with_static_fscx(self) -> GradientSettings:
        settings = GradientSettings(
            mode=GradientMode.VERTICAL,
            step=1.0,
            tags={
                "1c": TagGradientConfig(
                    tag="1c",
                    enabled=True,
                    nodes=make_default_nodes(start_color="000000", end_color="FFFFFF"),
                ),
                "fscx": TagGradientConfig(
                    tag="fscx",
                    enabled=True,
                    nodes=make_default_nodes(start_y=100.0, end_y=150.0),
                ),
            },
        )
        settings.text_x1 = 0.0
        settings.text_y1 = 10.0
        settings.text_x2 = 10.0
        settings.text_y2 = 12.0
        settings.group_range_bounds = (0.0, 0.0, 10.0, 100.0)
        settings.group_range_tags = {"1c", "fscx"}
        settings.animation = AnimationSettings(
            enabled=True,
            enabled_tags={"1c"},
            frame_step=1,
            fps=10.0,
            shift_curves={
                "1c": [
                    CurveNode(x=0.0, y=1.0),
                    CurveNode(x=1.0, y=1.0),
                ],
            },
            shift_modes={"1c": InterpolationMode.LINEAR},
        )
        return settings


if __name__ == "__main__":
    unittest.main(verbosity=2)
