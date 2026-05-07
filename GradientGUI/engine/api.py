"""Stable public engine entry points for the GUI."""

from __future__ import annotations

from .gradient import GradientTagError, generate_gradient
from .models import AnimationSettings, GradientMode, GradientSettings, TagGradientConfig
from .path_sampling_cache import get_cached_path_color_map
from .path_tracer import (
    get_color_by_ratio,
    project_sampled_path_points,
    sample_path_points_from_path,
)


def _build_preview_stops_from_color_map(
    color_map: dict[int, str],
    keys: list[int],
    *,
    mirrored: bool = False,
    max_stops: int = 256,
    smooth: bool = False,
    smooth_strength: float = 1.0,
) -> list[tuple[float, str]]:
    if not keys:
        return []

    sorted_keys = sorted(keys)
    lo, hi = sorted_keys[0], sorted_keys[-1]
    span = max(hi - lo, 1)

    def _with_mirror(pos: float) -> float:
        return 1.0 - pos if mirrored else pos

    try:
        strength = float(smooth_strength)
    except (TypeError, ValueError):
        strength = 1.0
    strength = max(0.0, min(1.0, strength))
    if smooth and strength > 0.0:
        count = max(2, min(max_stops, max(2, len(sorted_keys) * 2 - 1)))
        stops = []
        for idx in range(count):
            t = idx / max(count - 1, 1)
            color = get_color_by_ratio(
                color_map,
                sorted_keys,
                t,
                smooth=True,
                smooth_strength=strength,
            )
            stops.append((_with_mirror(t), color))
        stops.sort(key=lambda item: item[0])
        return stops

    if len(sorted_keys) > max_stops:
        stride = max(1, len(sorted_keys) // max(1, max_stops - 1))
        sampled_keys = sorted_keys[::stride]
        if sampled_keys[-1] != sorted_keys[-1]:
            sampled_keys.append(sorted_keys[-1])
    else:
        sampled_keys = sorted_keys

    positions: list[tuple[float, str]] = []
    for key in sampled_keys:
        color = color_map.get(key)
        if not color:
            continue
        pos = (key - lo) / span
        positions.append((pos, color))

    if not positions:
        return []

    stops: list[tuple[float, str]] = [(positions[0][0], positions[0][1])]
    for (prev_pos, prev_color), (pos, color) in zip(positions, positions[1:]):
        boundary = (prev_pos + pos) / 2.0
        stops.append((boundary, prev_color))
        stops.append((boundary, color))
    stops.append((positions[-1][0], positions[-1][1]))
    stops = [(_with_mirror(pos), color) for pos, color in stops]
    stops.sort(key=lambda item: item[0])
    return stops


def build_path_color_preview_stops_from_sampled_colors(
    sampled_colors: list[tuple[int, str]],
    mirrored: bool = False,
    max_stops: int = 256,
    smooth: bool = False,
    smooth_strength: float = 1.0,
) -> list[tuple[float, str]]:
    color_map: dict[int, str] = {}
    keys: list[int] = []
    for key, color in sampled_colors:
        try:
            sample_key = int(key)
        except (TypeError, ValueError):
            continue
        sample_color = str(color or "").strip()
        if not sample_color:
            continue
        color_map[sample_key] = sample_color
        keys.append(sample_key)
    return _build_preview_stops_from_color_map(
        color_map,
        sorted(set(keys)),
        mirrored=mirrored,
        max_stops=max_stops,
        smooth=smooth,
        smooth_strength=smooth_strength,
    )


def build_path_color_preview_stops_from_sampled_points(
    sampled_points: list[tuple[int, int, int, str]],
    cos_a: float,
    sin_a: float,
    mirrored: bool = False,
    max_stops: int = 256,
    smooth: bool = False,
    smooth_strength: float = 1.0,
) -> list[tuple[float, str]]:
    color_map, keys = project_sampled_path_points(sampled_points, cos_a, sin_a)
    return _build_preview_stops_from_color_map(
        color_map,
        keys,
        mirrored=mirrored,
        max_stops=max_stops,
        smooth=smooth,
        smooth_strength=smooth_strength,
    )


def get_path_color_sample_count(
    path: str,
    cos_a: float,
    sin_a: float,
    pixel_getter,
    frame_cache_key=None,
) -> int:
    """Return the true number of sampled integer colors for an ASS path."""
    _color_map, keys = get_cached_path_color_map(
        frame_cache_key, path, cos_a, sin_a, pixel_getter
    )
    return len(keys)


def build_path_color_preview_stops(
    path: str,
    cos_a: float,
    sin_a: float,
    pixel_getter,
    mirrored: bool = False,
    max_stops: int = 256,
    frame_cache_key=None,
    smooth: bool = False,
    smooth_strength: float = 1.0,
) -> list[tuple[float, str]]:
    """Return normalized color stops sampled from an ASS path."""
    color_map, keys = get_cached_path_color_map(
        frame_cache_key, path, cos_a, sin_a, pixel_getter
    )
    return _build_preview_stops_from_color_map(
        color_map,
        keys,
        mirrored=mirrored,
        max_stops=max_stops,
        smooth=smooth,
        smooth_strength=smooth_strength,
    )

__all__ = [
    "GradientMode",
    "GradientSettings",
    "AnimationSettings",
    "TagGradientConfig",
    "GradientTagError",
    "build_path_color_preview_stops",
    "build_path_color_preview_stops_from_sampled_colors",
    "build_path_color_preview_stops_from_sampled_points",
    "get_path_color_sample_count",
    "sample_path_points_from_path",
    "generate_gradient",
]
