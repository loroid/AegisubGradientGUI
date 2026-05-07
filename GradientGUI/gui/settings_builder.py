"""Build GradientSettings from GUI state."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Optional

from engine.api import GradientMode, GradientSettings
from engine.ass_parser import ASSFile, ASSEvent, time_to_seconds
from engine.interpolation import CurveNode, InterpolationMode
from engine.path_model import (
    PathSet,
    export_line_sampling_frames,
    export_line_sampling_paths,
    export_line_sampling_samples,
)
from engine.tag_parser import TAG_INFO
from gui.bounds_controller import BoundsController, BoundsRect
from gui.curve_keys import curve_key


MODE_MAP = {
    "Horizontal": GradientMode.HORIZONTAL,
    "Vertical": GradientMode.VERTICAL,
    "Angled": GradientMode.ANGLED,
    "GBC": GradientMode.GBC,
}


def resolve_video_path(ass_file: Optional[ASSFile], input_path: Optional[str]) -> Optional[str]:
    if not ass_file:
        return None
    video_path = ass_file.video_file
    if not video_path or video_path.startswith("?"):
        return None
    if os.path.exists(video_path):
        return video_path
    if input_path:
        candidate = Path(input_path).parent / video_path
        if candidate.exists():
            return str(candidate)
    return None


def current_video_time(
    ass_file: Optional[ASSFile],
    evt: ASSEvent,
    video_fps: Optional[float],
) -> float:
    try:
        frame_num = ass_file.video_position if ass_file else -1
        if video_fps and video_fps > 0 and frame_num >= 0:
            return frame_num / video_fps
    except Exception:
        pass
    return time_to_seconds(evt.start)


def sampling_paths_for_event(
    sampling_paths: dict[int, dict[str, PathSet]],
    active_event_idx: int,
    idx: int,
) -> dict[str, str]:
    paths = export_line_sampling_paths(sampling_paths.get(active_event_idx, {}))
    paths.update(export_line_sampling_paths(sampling_paths.get(idx, {})))
    return paths


def sampling_frames_for_event(
    sampling_paths: dict[int, dict[str, PathSet]],
    active_event_idx: int,
    idx: int,
) -> dict[str, int]:
    frames = export_line_sampling_frames(sampling_paths.get(active_event_idx, {}))
    frames.update(export_line_sampling_frames(sampling_paths.get(idx, {})))
    return frames


def sampling_samples_for_event(
    sampling_paths: dict[int, dict[str, PathSet]],
    active_event_idx: int,
    idx: int,
) -> dict[str, dict[str, object]]:
    samples = export_line_sampling_samples(sampling_paths.get(active_event_idx, {}))
    samples.update(export_line_sampling_samples(sampling_paths.get(idx, {})))
    return samples


def build_base_settings(
    *,
    ass_file: ASSFile,
    active_event: ASSEvent,
    active_event_idx: int,
    input_path: Optional[str],
    video_fps: Optional[float],
    mode_text: str,
    angle: float,
    step: float,
    tag_panel,
    tag_curves: dict[str, list[CurveNode]],
    tag_modes: dict[str, InterpolationMode],
    curve_mirrors: dict[str, tuple[bool, bool]],
    sampling_paths: dict[int, dict[str, PathSet]],
    bounds: BoundsController,
    animation_settings=None,
) -> GradientSettings:
    settings = GradientSettings(
        mode=MODE_MAP.get(mode_text, GradientMode.HORIZONTAL),
        angle=angle,
        step=step,
        color_space=tag_panel.get_color_space(),
    )

    bounds.apply_event_bounds_to_settings(settings, active_event_idx)

    vid_path = ass_file.video_file
    if vid_path and not vid_path.startswith("?"):
        settings.video_path = resolve_video_path(ass_file, input_path) or vid_path
        frame_num = ass_file.video_position
        if frame_num >= 0:
            settings.video_frame = frame_num
        settings.video_time = current_video_time(ass_file, active_event, video_fps)

    settings.sampling_paths = sampling_paths_for_event(
        sampling_paths, active_event_idx, active_event_idx
    )
    settings.sampling_path_frames = sampling_frames_for_event(
        sampling_paths, active_event_idx, active_event_idx
    )
    settings.sampling_path_samples = sampling_samples_for_event(
        sampling_paths, active_event_idx, active_event_idx
    )
    settings.path_sampling_smooth = tag_panel.get_path_smooth_map()
    settings.path_sampling_smooth_strength = tag_panel.get_path_smooth_strength_map()
    settings.path_sampling_mirror = {
        tag: bool(curve_mirrors.get(tag, (False, False))[0])
        for tag in ("1c", "2c", "3c", "4c")
    }
    if animation_settings is not None:
        settings.animation = animation_settings

    configs = tag_panel.get_all_configs()
    for tag, config in configs.items():
        if TAG_INFO.get(tag, {}).get("type") == "coord":
            x_key = curve_key(tag, "x")
            y_key = curve_key(tag, "y")
            if x_key in tag_curves:
                config.nodes = tag_curves[x_key]
            if y_key in tag_curves:
                config.coord_y_nodes = tag_curves[y_key]
            if x_key in tag_modes:
                config.mode = tag_modes[x_key]
            if y_key in tag_modes:
                config.coord_y_mode = tag_modes[y_key]
        else:
            if tag in tag_curves:
                config.nodes = tag_curves[tag]
            if tag in tag_modes:
                config.mode = tag_modes[tag]
        settings.tags[tag] = config

    return settings


def build_event_settings(
    *,
    base_settings: GradientSettings,
    idx: int,
    active_event_idx: int,
    sampling_paths: dict[int, dict[str, PathSet]],
    bounds: BoundsController,
    group_range_bounds: Optional[BoundsRect],
    group_range_tags: set[str],
) -> GradientSettings:
    settings = copy.deepcopy(base_settings)
    bounds.apply_event_bounds_to_settings(settings, idx)
    settings.sampling_paths = sampling_paths_for_event(
        sampling_paths, active_event_idx, idx
    )
    settings.sampling_path_frames = sampling_frames_for_event(
        sampling_paths, active_event_idx, idx
    )
    settings.sampling_path_samples = sampling_samples_for_event(
        sampling_paths, active_event_idx, idx
    )
    if group_range_bounds and group_range_tags:
        settings.group_range_bounds = group_range_bounds
        settings.group_range_tags = set(group_range_tags)
    else:
        settings.group_range_bounds = None
        settings.group_range_tags = set()
    return settings
