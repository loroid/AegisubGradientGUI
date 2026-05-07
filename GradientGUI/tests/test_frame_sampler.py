from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.frame_sampler import FrameSampler
from gui.dependency_health import HealthItem, _check_video_frame, run_startup_health_check


class FrameSamplerTest(unittest.TestCase):
    def test_successful_frame_number_load_reads_and_removes_temp_file(self) -> None:
        sampler = FrameSampler("ffmpeg.exe")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("engine.frame_sampler.tempfile.gettempdir", return_value=temp_dir):
                temp_img_path = sampler._temp_frame_path()

                def fake_run(cmd, **_kwargs):
                    Image.new("RGB", (3, 4), (1, 2, 3)).save(Path(cmd[-1]))
                    return subprocess.CompletedProcess(cmd, 0)

                with patch("engine.frame_sampler.subprocess.run", side_effect=fake_run):
                    self.assertTrue(sampler.load_frame_number("video.mp4", 12), sampler.last_error)

                image = sampler.get_image_copy()
                self.assertIsNotNone(image)
                self.assertEqual(image.size, (3, 4))
                self.assertEqual(sampler.frame_cache_key(), ("frame", "video.mp4", 12))
                self.assertFalse(temp_img_path.exists())

    def test_recent_frame_is_reused_from_lru_cache_without_ffmpeg(self) -> None:
        sampler = FrameSampler("ffmpeg.exe", max_cached_frames=2)
        calls = []

        def fake_run(cmd, **_kwargs):
            calls.append(Path(cmd[-1]).name)
            frame_name = Path(cmd[-1]).stem
            image = Image.new("RGB", (2, 2), (len(calls), 0, 0))
            image.save(Path(cmd[-1]))
            return subprocess.CompletedProcess(cmd, 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("engine.frame_sampler.tempfile.gettempdir", return_value=temp_dir):
                with patch("engine.frame_sampler.subprocess.run", side_effect=fake_run):
                    self.assertTrue(sampler.load_frame_number("video.mp4", 12))
                    self.assertTrue(sampler.load_frame_number("video.mp4", 13))
                    self.assertTrue(sampler.load_frame_number("video.mp4", 12))

        self.assertEqual(len(calls), 2)
        self.assertEqual(sampler.frame_cache_key(), ("frame", "video.mp4", 12))

    def test_frame_number_load_ignores_stale_temp_output(self) -> None:
        sampler = FrameSampler("ffmpeg.exe")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("engine.frame_sampler.tempfile.gettempdir", return_value=temp_dir):
                stale_path = sampler._temp_frame_path()
                Image.new("RGB", (2, 2), (255, 0, 0)).save(stale_path)

                with patch(
                    "engine.frame_sampler.subprocess.run",
                    return_value=subprocess.CompletedProcess([], 0),
                ):
                    self.assertFalse(sampler.load_frame_number("video.mp4", 999))

                self.assertFalse(stale_path.exists())

        self.assertIsNone(sampler.get_image_copy())
        self.assertIsNone(sampler.frame_cache_key())
        self.assertIn("did not create", sampler.last_error)

    def test_failed_frame_number_load_clears_previous_cached_frame(self) -> None:
        sampler = FrameSampler("ffmpeg.exe")
        sampler._current_video = "old.mp4"
        sampler._current_frame = 3
        sampler._image = Image.new("RGB", (1, 1), (5, 6, 7))

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("engine.frame_sampler.tempfile.gettempdir", return_value=temp_dir):
                with patch(
                    "engine.frame_sampler.subprocess.run",
                    side_effect=subprocess.CalledProcessError(1, "ffmpeg", stderr="boom"),
                ):
                    with patch("builtins.print"):
                        self.assertFalse(sampler.load_frame_number("new.mp4", 4))

        self.assertIsNone(sampler.get_image_copy())
        self.assertIsNone(sampler.get_pixel_bgr(0, 0))
        self.assertIsNone(sampler.frame_cache_key())
        self.assertIn("boom", sampler.last_error)

    def test_health_check_prefers_frame_number_over_time_hint(self) -> None:
        calls = []

        class FakeSampler:
            last_error = ""

            def load_frame_number(self, video_path, frame_number):
                calls.append(("frame", video_path, frame_number))
                return True

            def load_frame(self, video_path, time_sec):
                calls.append(("time", video_path, time_sec))
                return True

            def frame_cache_key(self):
                return ("frame", "video.mp4", 8)

        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            video_path = temp_file.name

        try:
            item = _check_video_frame(video_path, 9.5, 8, FakeSampler())
        finally:
            Path(video_path).unlink(missing_ok=True)

        self.assertTrue(item.ok)
        self.assertEqual(calls, [("frame", video_path, 8)])

    def test_startup_health_check_can_skip_video_frame_probe(self) -> None:
        class FakeSampler:
            ffmpeg_path = "ffmpeg.exe"

            def load_frame_number(self, _video_path, _frame_number):
                raise AssertionError("startup fast path must not read video frames")

            def load_frame(self, _video_path, _time_sec):
                raise AssertionError("startup fast path must not read video frames")

        sampler = FakeSampler()
        with patch(
            "gui.dependency_health._check_libass",
            return_value=HealthItem("libass", True, "ok"),
        ), patch(
            "gui.dependency_health._check_mpv",
            return_value=HealthItem("mpv", True, "ok"),
        ), patch(
            "gui.dependency_health._find_ffmpeg",
            return_value="C:/tools/ffmpeg.exe",
        ), patch(
            "gui.dependency_health._check_ffmpeg",
            return_value=HealthItem("ffmpeg", True, "ok"),
        ):
            report = run_startup_health_check(
                video_path="video.mp4",
                video_time=1.0,
                video_frame=12,
                frame_sampler=sampler,
                check_video_frame=False,
            )

        self.assertTrue(report.ok)
        self.assertEqual([item.name for item in report.items], ["libass", "mpv", "ffmpeg"])
        self.assertEqual(sampler.ffmpeg_path, "C:/tools/ffmpeg.exe")


if __name__ == "__main__":
    unittest.main(verbosity=2)
