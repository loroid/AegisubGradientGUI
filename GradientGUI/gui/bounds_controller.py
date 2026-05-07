"""Rendered subtitle bounds cache and range helpers."""

from __future__ import annotations

from typing import Callable, Optional

from engine.api import GradientSettings
from engine.ass_parser import ASSFile, ASSEvent
from engine.libass_bounds import LibassBoundsError, measure_event_bounds


BoundsRect = tuple[float, float, float, float]
StatusCallback = Callable[[str], None]


class BoundsController:
    """Own libass bounds lookup, cache, and merged range calculation."""

    def __init__(self, status_callback: Optional[StatusCallback] = None):
        self._status_callback = status_callback
        self._ass_file: Optional[ASSFile] = None
        self._source_events: list[ASSEvent] = []
        self._cache: dict[int, str] = {}

    def set_status_callback(self, callback: Optional[StatusCallback]) -> None:
        self._status_callback = callback

    def set_source(self, ass_file: ASSFile, source_events: list[ASSEvent]) -> None:
        self._ass_file = ass_file
        self._source_events = source_events
        self._cache.clear()

    def get_meta(self, idx: int) -> Optional[str]:
        if idx in self._cache:
            return self._cache[idx]
        if not self._ass_file or idx < 0 or idx >= len(self._source_events):
            return None

        evt = self._source_events[idx]
        try:
            bounds = measure_event_bounds(self._ass_file, evt)
        except LibassBoundsError as exc:
            print(f"[DEBUG] libass bounds failed: {exc}")
            self._show_status(f"libass 边界测量失败: {exc}")
            return None

        if not bounds:
            self._show_status("libass 未渲染出可见字幕像素")
            return None

        meta = bounds.to_meta()
        self._cache[idx] = meta
        print(
            f"[DEBUG] libass bounds line {idx + 1}: "
            f"x1={bounds.x1:.1f}, y1={bounds.y1:.1f}, "
            f"x2={bounds.x2:.1f}, y2={bounds.y2:.1f}"
        )
        return meta

    def apply_event_bounds_to_settings(
        self,
        settings: GradientSettings,
        idx: int,
        bounds_meta: Optional[str] = None,
    ) -> None:
        if not self._ass_file:
            return

        res_x = self._ass_file.play_res_x
        res_y = self._ass_file.play_res_y
        settings.text_x1 = res_x * 0.1
        settings.text_y1 = res_y * 0.7
        settings.text_x2 = res_x * 0.9
        settings.text_y2 = res_y * 0.95

        base_meta = bounds_meta or self.get_meta(idx)
        if base_meta:
            self.apply_bounds_meta(settings, base_meta)

    def merged_rect(self, indices: list[int]) -> Optional[BoundsRect]:
        rects: list[BoundsRect] = []
        for idx in indices:
            meta = self.get_meta(idx)
            rect = self.parse_meta_rect(meta)
            if rect:
                rects.append(rect)
        if not rects:
            return None
        return (
            min(rect[0] for rect in rects),
            min(rect[1] for rect in rects),
            max(rect[2] for rect in rects),
            max(rect[3] for rect in rects),
        )

    @staticmethod
    def apply_bounds_meta(settings: GradientSettings, base_meta: str) -> None:
        rect = BoundsController.parse_meta_rect(base_meta)
        if not rect:
            return
        settings.text_x1, settings.text_y1, settings.text_x2, settings.text_y2 = rect

    @staticmethod
    def parse_meta_rect(meta: Optional[str]) -> Optional[BoundsRect]:
        if not meta:
            return None
        try:
            x1, y1, x2, y2 = (float(value) for value in meta.split(",")[:4])
        except (ValueError, IndexError):
            return None
        return x1, y1, x2, y2

    def _show_status(self, message: str) -> None:
        if self._status_callback:
            self._status_callback(message)
