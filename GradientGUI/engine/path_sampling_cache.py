"""LRU cache for path-based color sampling."""

from __future__ import annotations

from collections import OrderedDict
from typing import Callable, Hashable, Optional

from .path_tracer import build_color_map_from_path


PixelGetter = Callable[[int, int], Optional[str]]
FrameCacheKey = Hashable
ColorMapResult = tuple[dict[int, str], list[int]]


class PathSamplingCache:
    """Cache sampled path colors for a specific video frame and direction."""

    def __init__(self, max_entries: int = 128):
        self.max_entries = max(1, int(max_entries))
        self._items: OrderedDict[tuple[FrameCacheKey, str, float, float], ColorMapResult] = (
            OrderedDict()
        )

    def clear(self) -> None:
        self._items.clear()

    def get_or_build(
        self,
        frame_key: Optional[FrameCacheKey],
        path_str: str,
        cos_a: float,
        sin_a: float,
        pixel_getter: PixelGetter,
    ) -> ColorMapResult:
        if frame_key is None:
            return build_color_map_from_path(path_str, cos_a, sin_a, pixel_getter)

        key = (
            frame_key,
            path_str.strip(),
            round(float(cos_a), 6),
            round(float(sin_a), 6),
        )
        cached = self._items.get(key)
        if cached is not None:
            self._items.move_to_end(key)
            return cached

        result = build_color_map_from_path(path_str, cos_a, sin_a, pixel_getter)
        self._items[key] = result
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
        return result


GLOBAL_PATH_SAMPLING_CACHE = PathSamplingCache()


def get_cached_path_color_map(
    frame_key: Optional[FrameCacheKey],
    path_str: str,
    cos_a: float,
    sin_a: float,
    pixel_getter: PixelGetter,
) -> ColorMapResult:
    return GLOBAL_PATH_SAMPLING_CACHE.get_or_build(
        frame_key, path_str, cos_a, sin_a, pixel_getter
    )


def frame_key_from_video_position(
    video_path: Optional[str],
    video_frame: Optional[int],
    video_time: Optional[float],
) -> Optional[FrameCacheKey]:
    if not video_path:
        return None
    if video_frame is not None:
        return ("frame", video_path, int(video_frame))
    if video_time is not None:
        return ("time", video_path, round(float(video_time), 3))
    return None
