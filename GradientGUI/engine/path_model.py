"""Structured state for path-based color sampling."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from engine.path_tracer import (
    normalize_raw_path_samples,
    project_sampled_path_points,
    split_path_segments,
)


def _clean_path(value: Any) -> str:
    return str(value or "").strip()


@dataclass
class SamplingPath:
    """One drawable ASS path segment."""

    ass_path: str = ""
    label: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(_clean_path(self.ass_path))

    def copy(self) -> "SamplingPath":
        return SamplingPath(self.ass_path, self.label)

    def to_raw(self) -> dict[str, str]:
        data = {"ass_path": _clean_path(self.ass_path)}
        if self.label:
            data["label"] = str(self.label)
        return data

    @classmethod
    def from_raw(cls, raw: Any) -> "SamplingPath":
        if isinstance(raw, SamplingPath):
            return raw.copy()
        if isinstance(raw, Mapping):
            return cls(
                _clean_path(raw.get("ass_path", raw.get("path", ""))),
                str(raw.get("label", "") or ""),
            )
        return cls()


@dataclass
class PathSet:
    """All paths assigned to one color tag on one subtitle line."""

    paths: list[SamplingPath] = field(default_factory=list)
    removed_original: bool = False
    sampling_frame: int | None = None
    sampled_points: list[tuple[int, int, int, str]] = field(default_factory=list)
    sampled_colors: list[tuple[int, str]] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return not self.removed_original and any(path.is_valid for path in self.paths)

    def copy(self) -> "PathSet":
        return PathSet(
            [path.copy() for path in self.paths],
            self.removed_original,
            self.sampling_frame,
            [
                (int(segment), int(x), int(y), str(color))
                for segment, x, y, color in self.sampled_points
            ],
            [(int(key), str(color)) for key, color in self.sampled_colors],
        )

    def to_ass_path(self) -> str:
        if self.removed_original:
            return ""
        return " ".join(path.ass_path for path in self.paths if path.is_valid)

    def to_raw(self) -> dict[str, Any]:
        data = {
            "removed_original": bool(self.removed_original),
            "paths": [path.to_raw() for path in self.paths if path.is_valid],
        }
        if self.sampling_frame is not None and int(self.sampling_frame) >= 0:
            data["sampling_frame"] = int(self.sampling_frame)
        if self.sampled_points:
            data["sampled_points"] = [
                [int(segment), int(x), int(y), str(color)]
                for segment, x, y, color in self.sampled_points
                if str(color).strip()
            ]
        elif self.sampled_colors:
            data["sampled_colors"] = [
                [int(key), str(color)]
                for key, color in self.sampled_colors
                if str(color).strip()
            ]
        return data

    def sampled_color_result(
        self,
        cos_a: float | None = None,
        sin_a: float | None = None,
    ) -> tuple[dict[int, str], list[int]]:
        if self.sampled_points:
            if cos_a is None:
                cos_a = 1.0
            if sin_a is None:
                sin_a = 0.0
            return project_sampled_path_points(self.sampled_points, float(cos_a), float(sin_a))
        color_map: dict[int, str] = {}
        for key, color in self.sampled_colors:
            try:
                sample_key = int(key)
            except (TypeError, ValueError):
                continue
            sample_color = str(color or "").strip()
            if not sample_color:
                continue
            color_map[sample_key] = sample_color
        return color_map, sorted(color_map)

    @classmethod
    def empty(cls) -> "PathSet":
        return cls()

    @classmethod
    def removed(cls) -> "PathSet":
        return cls(removed_original=True)

    @classmethod
    def from_ass_path(
        cls,
        path: Any,
        removed_original: bool = False,
        sampling_frame: int | None = None,
        sampled_points: list[tuple[int, int, int, str]] | None = None,
        sampled_colors: list[tuple[int, str]] | None = None,
    ) -> "PathSet":
        if sampling_frame is not None:
            try:
                sampling_frame = int(sampling_frame)
            except (TypeError, ValueError):
                sampling_frame = None
            if sampling_frame is not None and sampling_frame < 0:
                sampling_frame = None
        text = _clean_path(path)
        if not text:
            return cls(
                removed_original=removed_original,
                sampling_frame=sampling_frame,
                sampled_points=normalize_raw_path_samples(sampled_points or []),
                sampled_colors=[
                    (int(key), str(color))
                    for key, color in (sampled_colors or [])
                ],
            )
        segments = [segment.strip() for segment in split_path_segments(text) if segment.strip()]
        if not segments:
            segments = [text]
        return cls(
            [SamplingPath(segment) for segment in segments],
            removed_original,
            sampling_frame,
            normalize_raw_path_samples(sampled_points or []),
            [(int(key), str(color)) for key, color in (sampled_colors or [])],
        )

    @classmethod
    def from_raw(cls, raw: Any) -> "PathSet":
        if isinstance(raw, PathSet):
            return raw.copy()
        if isinstance(raw, Mapping):
            removed = bool(raw.get("removed_original", raw.get("removed", False)))
            raw_paths = raw.get("paths", [])
            paths: list[SamplingPath] = []
            if isinstance(raw_paths, list):
                paths = [
                    path for path in (SamplingPath.from_raw(item) for item in raw_paths)
                    if path.is_valid
                ]
            ass_path = raw.get("ass_path", raw.get("path", ""))
            sampling_frame = raw.get("sampling_frame")
            try:
                sampling_frame_int = None if sampling_frame is None else int(sampling_frame)
            except (TypeError, ValueError):
                sampling_frame_int = None
            if sampling_frame_int is not None and sampling_frame_int < 0:
                sampling_frame_int = None
            sampled_colors_raw = raw.get("sampled_colors", raw.get("samples", []))
            sampled_points_raw = raw.get("sampled_points", raw.get("sampled_pixels", []))
            sampled_points = (
                normalize_raw_path_samples(sampled_points_raw)
                if isinstance(sampled_points_raw, list)
                else []
            )
            sampled_colors: list[tuple[int, str]] = []
            if isinstance(sampled_colors_raw, list):
                for item in sampled_colors_raw:
                    if isinstance(item, Mapping):
                        key = item.get("key", item.get("index"))
                        color = item.get("color", item.get("value", ""))
                    elif isinstance(item, (list, tuple)) and len(item) >= 2:
                        key, color = item[0], item[1]
                    else:
                        continue
                    try:
                        key_int = int(key)
                    except (TypeError, ValueError):
                        continue
                    color_text = _clean_path(color)
                    if color_text:
                        sampled_colors.append((key_int, color_text))
            if not paths and ass_path:
                return cls.from_ass_path(
                    ass_path,
                    removed_original=removed,
                    sampling_frame=sampling_frame_int,
                    sampled_points=sampled_points,
                    sampled_colors=sampled_colors,
                )
            return cls(paths, removed, sampling_frame_int, sampled_points, sampled_colors)
        return cls.empty()


LinePathState = dict[str, PathSet]
PathSamplingState = dict[int, LinePathState]


def normalize_line_path_state(raw: Any) -> LinePathState:
    if not isinstance(raw, Mapping):
        return {}
    normalized: LinePathState = {}
    for tag, value in raw.items():
        tag_name = str(tag)
        path_set = PathSet.from_raw(value)
        if path_set.is_active or path_set.removed_original:
            normalized[tag_name] = path_set
    return normalized


def normalize_path_state(raw: Any) -> PathSamplingState:
    if not isinstance(raw, Mapping):
        return {}
    normalized: PathSamplingState = {}
    for key, value in raw.items():
        try:
            idx = int(key)
        except (TypeError, ValueError):
            continue
        line_state = normalize_line_path_state(value)
        if line_state:
            normalized[idx] = line_state
    return normalized


def export_line_sampling_paths(line_state: Any) -> dict[str, str]:
    exported: dict[str, str] = {}
    for tag, path_set in normalize_line_path_state(line_state).items():
        if path_set.removed_original:
            exported[tag] = ""
        elif path_set.is_active:
            exported[tag] = path_set.to_ass_path()
    return exported


def export_line_sampling_frames(line_state: Any) -> dict[str, int]:
    exported: dict[str, int] = {}
    for tag, path_set in normalize_line_path_state(line_state).items():
        if path_set.is_active and path_set.sampling_frame is not None and int(path_set.sampling_frame) >= 0:
            exported[tag] = int(path_set.sampling_frame)
    return exported


def export_line_sampling_samples(line_state: Any) -> dict[str, dict[str, Any]]:
    exported: dict[str, dict[str, Any]] = {}
    for tag, path_set in normalize_line_path_state(line_state).items():
        if path_set.is_active or path_set.removed_original:
            raw = path_set.to_raw()
            if raw:
                exported[tag] = raw
    return exported


def serialize_path_state(state: Any) -> dict[str, dict[str, dict[str, Any]]]:
    serialized: dict[str, dict[str, dict[str, Any]]] = {}
    for idx, line_state in normalize_path_state(state).items():
        raw_line: dict[str, dict[str, Any]] = {}
        for tag, path_set in line_state.items():
            if path_set.is_active or path_set.removed_original:
                raw_line[tag] = path_set.to_raw()
        if raw_line:
            serialized[str(idx)] = raw_line
    return serialized
