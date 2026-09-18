"""Saved path state must remain coherent across lines and video changes."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.ass_parser import ASSEvent, ASSStyle
from engine.interpolation import make_default_nodes
from engine.path_model import PathSet
from gui.settings_builder import (
    sampling_frames_for_event,
    sampling_paths_for_event,
    sampling_samples_for_event,
)


class PathStateRegressionTest(unittest.TestCase):
    def _path(self, *, frame=17, sampled=True):
        return PathSet.from_ass_path(
            "m 0 0 l 3 3", sampling_frame=frame,
            sampled_points=[
                (0, i, i, color)
                for i, color in enumerate(("0000FF", "00FF00", "FF0000", "00FFFF"))
            ] if sampled else [],
        )

    def _settings(self, mode, smooth=False):
        paths = {tag: self._path() for tag in ("1c", "2c", "3c", "4c")}
        return GradientSettings(
            mode=mode, angle=40 if mode == GradientMode.ANGLED else 0,
            step=5, text_x1=0, text_y1=0, text_x2=40, text_y2=40,
            video_path="synthetic-video.mp4",
            sampling_paths={tag: path.to_ass_path() for tag, path in paths.items()},
            sampling_path_samples={tag: path.to_raw() for tag, path in paths.items()},
            path_sampling_smooth={tag: smooth for tag in paths},
            tags={tag: TagGradientConfig(
                tag=tag, enabled=True,
                nodes=make_default_nodes(start_color="FFFFFF", end_color="FFFFFF"),
            ) for tag in paths},
        )

    def test_saved_samples_do_not_require_an_attached_video(self):
        event = ASSEvent(text=r"{\pos(20,20)}ABCD")
        style = ASSStyle(name="Default", outline=0, shadow=0)
        with patch("engine.gradient._load_sampling_frame_for_key") as read_frame:
            read_frame.side_effect = AssertionError("saved pixels must not be resampled")
            for mode in GradientMode:
                for smooth in (False, True):
                    for shift in (0, 1):
                        with self.subTest(mode=mode, smooth=smooth, shift=shift):
                            settings = self._settings(mode, smooth)
                            settings.color_shift_steps = shift
                            expected = generate_gradient(event, style, settings)
                            self.assertTrue(expected)
                            self.assertIn(r"\1c&H0000FF&", "".join(e.text for e in expected))
                            settings.video_path = ""
                            self.assertEqual(generate_gradient(event, style, settings), expected)
            read_frame.assert_not_called()

    def test_path_without_samples_or_video_keeps_manual_colors(self):
        event = ASSEvent(text="ABCD")
        with patch("engine.gradient._load_sampling_frame_for_key") as read_frame:
            for mode in GradientMode:
                with self.subTest(mode=mode):
                    settings = self._settings(mode)
                    settings.video_path = ""
                    settings.sampling_path_samples = {}
                    output = generate_gradient(event, None, settings)
                    settings.sampling_paths = {}
                    self.assertEqual(output, generate_gradient(event, None, settings))
            read_frame.assert_not_called()

    def test_local_path_does_not_inherit_another_paths_sampling_frame(self):
        state = {0: {"1c": self._path()}, 1: {"1c": self._path(frame=None, sampled=False)}}
        original = copy.deepcopy(state)
        self.assertEqual(sampling_frames_for_event(state, 0, 1), {})
        self.assertEqual(sampling_samples_for_event(state, 0, 1), {"1c": state[1]["1c"].to_raw()})
        self.assertEqual(state, original)

    def test_removed_path_does_not_keep_inherited_sampling_metadata(self):
        state = {0: {"1c": self._path()}, 1: {"1c": PathSet.removed()}}
        self.assertEqual(sampling_paths_for_event(state, 0, 1), {"1c": ""})
        self.assertEqual(sampling_frames_for_event(state, 0, 1), {})
        self.assertEqual(sampling_samples_for_event(state, 0, 1), {"1c": PathSet.removed().to_raw()})

    def test_missing_local_path_inherits_one_complete_active_state(self):
        state = {0: {"1c": self._path()}, 1: {"3c": self._path(frame=29)}}
        self.assertEqual(sampling_frames_for_event(state, 0, 1), {"1c": 17, "3c": 29})
        self.assertEqual(sampling_samples_for_event(state, 0, 1), {
            "1c": state[0]["1c"].to_raw(), "3c": state[1]["3c"].to_raw(),
        })


if __name__ == "__main__":
    unittest.main()
