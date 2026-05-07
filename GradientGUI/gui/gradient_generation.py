"""GUI-side helpers for running gradient generation."""

from __future__ import annotations

import copy
import math
import re
from bisect import bisect_left
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Optional

from engine.api import GradientSettings, GradientTagError, generate_gradient
from engine.ass_parser import ASSFile, ASSEvent, seconds_to_time, time_to_seconds
from engine.interpolation import InterpolationMode, interpolate
from engine.models import GradientMode
from engine.range_calc import GEOMETRY_TAGS, compute_gradient_direction
from engine.tag_parser import (
    ALPHA_TAGS,
    NUMERIC_TAGS,
    TAG_INFO,
    extract_clip_bounds,
    get_tag_value,
    parse_tags_from_text,
)
from gui.preview_cache import stable_preview_key


BoundsResolver = Callable[[int], Optional[str]]
SettingsFactory = Callable[[GradientSettings, int], GradientSettings]
COLOR_TAGS = {"1c", "2c", "3c", "4c"}
TRANSFORMABLE_ANIMATION_TAGS = COLOR_TAGS | set(ALPHA_TAGS) | set(NUMERIC_TAGS)
TRANSFORM_TARGET_CACHE_CONTEXT_TAGS = set(GEOMETRY_TAGS)
_GROUP_RESOLVER_CACHE_MAX = 32
_GROUP_RESOLVER_CACHE: OrderedDict[str, _GroupTransformTargetResolver] = OrderedDict()


@dataclass
class _AnimationSegment:
    event: ASSEvent
    shift: float
    start_ms: int
    end_ms: int
    shift_by_tag: dict[str, float] = field(default_factory=dict)


@dataclass
class _AnimationFramePlan:
    first_frame: int
    last_frame: int
    source_start_ms: int
    source_end_ms: int
    frame_time_ms: dict[int, int]
    fps: float


@dataclass
class _TagTransformSequence:
    positions: list[float]
    values: list[str]

    def value_at_axis_pos(self, axis_pos: Optional[float], shift: float) -> str:
        if axis_pos is None or not self.positions or not self.values:
            return ""
        idx = _nearest_position_index(self.positions, axis_pos)
        source_index = (idx - _round_shift(shift)) % len(self.values)
        return self.values[source_index]


@dataclass
class _GroupTransformTargetResolver:
    sequences: dict[str, _TagTransformSequence]
    direction: object

    def handles(self, tag: str) -> bool:
        return tag in self.sequences

    def values_for(
        self,
        text: str,
        shift_map: dict[str, float],
    ) -> dict[str, str]:
        axis_pos = _clip_axis_position(text, self.direction)
        values: dict[str, str] = {}
        for tag, sequence in self.sequences.items():
            value = sequence.value_at_axis_pos(axis_pos, shift_map.get(tag, 0.0))
            if value:
                values[tag] = value
        return values


class GradientGenerationError(RuntimeError):
    """Wrap a generation failure with its source line index."""

    def __init__(
        self,
        line_index: int,
        original: Exception,
        context: Optional[dict[str, object]] = None,
    ):
        super().__init__(str(original))
        self.line_index = line_index
        self.original = original
        self.context = context or {}


def generate_gradient_events(
    source_events: list[ASSEvent],
    indices: list[int],
    ass_file: ASSFile,
    base_settings: GradientSettings,
    settings_factory: SettingsFactory,
    bounds_resolver: BoundsResolver,
    *,
    debug: bool = False,
) -> list[ASSEvent]:
    """Generate gradient replacement events for the requested source indices."""

    result_events: list[ASSEvent] = []
    for idx in indices:
        evt = source_events[idx]
        style = ass_file.get_style(evt.style)
        base_meta = bounds_resolver(idx)
        settings = settings_factory(base_settings, idx)

        if debug:
            print(f"[DEBUG] preview line {idx + 1} base_meta: {base_meta}")
            print(
                "[DEBUG] settings bounds: "
                f"x1={settings.text_x1}, y1={settings.text_y1}, "
                f"x2={settings.text_x2}, y2={settings.text_y2}"
            )
            if style:
                print(
                    "[DEBUG] style: "
                    f"align={style.alignment}, fs={style.fontsize}, "
                    f"outline={style.outline}, shadow={style.shadow}, "
                    f"scale_x={style.scale_x}, scale_y={style.scale_y}"
                )

        try:
            result_events.extend(_generate_event_with_optional_animation(
                evt, style, settings, base_meta, ass_file
            ))
        except Exception as exc:
            context = _generation_error_context(idx, evt, style, settings, base_meta, exc)
            raise GradientGenerationError(idx, exc, context) from exc

    if debug and result_events:
        print(f"[DEBUG] First strip clip: {result_events[0].text[:200]}")
        print(f"[DEBUG] Last strip clip: {result_events[-1].text[:200]}")
        print(f"[DEBUG] Total strips: {len(result_events)}")

    return result_events


def _generate_event_with_optional_animation(
    evt: ASSEvent,
    style,
    settings: GradientSettings,
    base_meta: Optional[str],
    ass_file: ASSFile,
) -> list[ASSEvent]:
    if not _should_animate_gradient_bands(settings):
        return generate_gradient(evt, style, settings, base_meta, ass_file)
    if _should_use_transform_animation(settings):
        return _generate_event_with_transform_animation(
            evt, style, settings, base_meta, ass_file
        )
    return _generate_event_with_split_animation(evt, style, settings, base_meta, ass_file)


def _generation_error_context(
    line_index: int,
    evt: ASSEvent,
    style,
    settings: GradientSettings,
    base_meta: Optional[str],
    exc: Exception,
) -> dict[str, object]:
    parsed = parse_tags_from_text(evt.text)
    active_tags = _active_animation_tags(settings)
    if not active_tags:
        active_tags = [
            tag for tag, cfg in settings.tags.items()
            if tag in TAG_INFO and getattr(cfg, "enabled", False)
        ]

    tag = getattr(exc, "tag", None)
    if not tag and len(active_tags) == 1:
        tag = active_tags[0]
    tag_values = _tag_value_context(active_tags, parsed, style, settings)
    current_value = getattr(exc, "value", None)
    if current_value is None and tag in tag_values:
        current_value = tag_values[tag].get("current")

    return {
        "line_index": line_index,
        "line_number": line_index + 1,
        "tag": tag,
        "tag_label": TAG_INFO.get(tag, {}).get("label") if tag else None,
        "tag_value": _json_value(current_value),
        "enabled_tags": active_tags,
        "tag_values": tag_values,
        "clip_range": {
            "generated_bounds": [
                settings.text_x1,
                settings.text_y1,
                settings.text_x2,
                settings.text_y2,
            ],
            "source_clip_bounds": extract_clip_bounds(evt.text),
            "base_bounds_meta": base_meta,
            "tag_clip": getattr(exc, "clip", None),
        },
        "strip_index": getattr(exc, "strip_index", None),
        "position": getattr(exc, "position", None),
        "mode": settings.mode.value if hasattr(settings.mode, "value") else str(settings.mode),
        "angle": settings.angle,
        "step": settings.step,
        "animation": {
            "enabled": bool(getattr(settings.animation, "enabled", False)),
            "enabled_tags": sorted(
                set(getattr(settings.animation, "enabled_tags", set()) or [])
            ),
            "use_transform": bool(getattr(settings.animation, "use_transform", True)),
            "frame_step": getattr(settings.animation, "frame_step", None),
            "frame_steps": getattr(settings.animation, "frame_steps", None),
            "event_first_frame": getattr(settings.animation, "event_first_frame", None),
            "event_last_frame": getattr(settings.animation, "event_last_frame", None),
        },
        "source_event": {
            "start": evt.start,
            "end": evt.end,
            "style": evt.style,
            "text": evt.text,
        },
    }


def _tag_value_context(
    tags: list[str],
    parsed: dict,
    style,
    settings: GradientSettings,
) -> dict[str, dict[str, object]]:
    values: dict[str, dict[str, object]] = {}
    for tag in tags:
        cfg = settings.tags.get(tag)
        if cfg is None:
            continue
        try:
            current = get_tag_value(tag, parsed, style)
        except Exception as exc:
            current = f"<读取失败: {exc}>"
        values[tag] = {
            "label": TAG_INFO.get(tag, {}).get("label", tag),
            "current": _json_value(current),
            "start": _json_value(_node_debug_value(tag, cfg, first=True)),
            "end": _json_value(_node_debug_value(tag, cfg, first=False)),
            "mode": getattr(getattr(cfg, "mode", None), "value", str(getattr(cfg, "mode", ""))),
            "path_sampling": bool(settings.sampling_paths.get(tag)),
            "path_smooth": bool(settings.path_sampling_smooth.get(tag, False)),
            "path_smooth_strength": settings.path_sampling_smooth_strength.get(tag),
        }
    return values


def _node_debug_value(tag: str, cfg, *, first: bool):
    nodes = getattr(cfg, "nodes", []) or []
    if not nodes:
        return None
    node = nodes[0] if first else nodes[-1]
    tag_type = TAG_INFO.get(tag, {}).get("type", "numeric")
    if tag_type in {"color", "text"}:
        return node.value_str
    if tag_type == "coord":
        y_nodes = getattr(cfg, "coord_y_nodes", []) or []
        y_node = y_nodes[0] if first and y_nodes else y_nodes[-1] if y_nodes else None
        return [node.y, y_node.y if y_node else None]
    return node.y


def _json_value(value):
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _generate_event_with_split_animation(
    evt: ASSEvent,
    style,
    settings: GradientSettings,
    base_meta: Optional[str],
    ass_file: ASSFile,
) -> list[ASSEvent]:
    segments = _animation_segments(evt, settings)
    active_tags = _active_animation_tags(settings)
    if _can_fast_split_animation(settings, active_tags):
        fast_result = _generate_event_with_fast_split_animation(
            evt, style, settings, base_meta, ass_file, segments, active_tags
        )
        if fast_result is not None:
            return fast_result

    return _generate_event_with_slow_split_animation(
        style,
        settings,
        base_meta,
        ass_file,
        segments,
    )


def _generate_event_with_slow_split_animation(
    style,
    settings: GradientSettings,
    base_meta: Optional[str],
    ass_file: ASSFile,
    segments: list[_AnimationSegment],
) -> list[ASSEvent]:
    result: list[ASSEvent] = []
    for segment in segments:
        segment_settings = copy.deepcopy(settings)
        _apply_segment_shift(segment_settings, segment)
        result.extend(
            generate_gradient(segment.event, style, segment_settings, base_meta, ass_file)
        )
    return result


def _can_fast_split_animation(settings: GradientSettings, active_tags: list[str]) -> bool:
    if settings.mode == GradientMode.GBC:
        return False
    return bool(active_tags) and all(tag in TRANSFORMABLE_ANIMATION_TAGS for tag in active_tags)


def _generate_event_with_fast_split_animation(
    evt: ASSEvent,
    style,
    settings: GradientSettings,
    base_meta: Optional[str],
    ass_file: ASSFile,
    segments: list[_AnimationSegment],
    active_tags: list[str],
) -> Optional[list[ASSEvent]]:
    if not segments:
        return generate_gradient(evt, style, settings, base_meta, ass_file)

    base_settings = copy.deepcopy(settings)
    _apply_segment_shift(base_settings, segments[0])
    base_events = generate_gradient(
        segments[0].event, style, base_settings, base_meta, ass_file
    )
    if len(segments) <= 1 or not base_events:
        return base_events

    base_shift_map = _segment_shift_map(segments[0], active_tags)
    group_resolver = _build_group_transform_target_resolver(
        evt,
        style,
        settings,
        base_meta,
        active_tags,
    )
    if _requires_group_transform_targets(settings, active_tags) and not group_resolver:
        return None
    sequence_tags = [
        tag for tag in active_tags
        if not group_resolver or not group_resolver.handles(tag)
    ]
    tag_sequences = {}
    if sequence_tags:
        tag_sequences = _transform_tag_sequences_from_events(
            base_events,
            sequence_tags,
            settings.animation,
        )

    result: list[ASSEvent] = []
    for segment in segments:
        shift_map = _segment_shift_map(segment, active_tags)
        same_shift = _same_shift_map(shift_map, base_shift_map)
        for idx, base_event in enumerate(base_events):
            event_copy = copy.copy(base_event)
            event_copy.start = segment.event.start
            event_copy.end = segment.event.end
            if not same_shift:
                tag_values = {}
                if group_resolver:
                    tag_values.update(group_resolver.values_for(base_event.text, shift_map))
                if sequence_tags:
                    tag_values.update(_rotated_transform_tag_values(
                        tag_sequences,
                        shift_map,
                        base_shift_map,
                        idx,
                    ))
                event_copy.text = _replace_or_append_first_override(
                    event_copy.text,
                    tag_values,
                )
            result.append(event_copy)
    return result


def _generate_event_with_transform_animation(
    evt: ASSEvent,
    style,
    settings: GradientSettings,
    base_meta: Optional[str],
    ass_file: ASSFile,
) -> list[ASSEvent]:
    segments = _animation_segments(evt, settings)
    if not segments:
        return generate_gradient(evt, style, settings, base_meta, ass_file)

    base_settings = copy.deepcopy(settings)
    _apply_segment_shift(base_settings, segments[0])
    base_events = generate_gradient(evt, style, base_settings, base_meta, ass_file)
    if len(segments) <= 1 or not base_events:
        return base_events

    source_start_ms = _event_source_start_ms(evt, settings.animation)
    source_end_ms = _event_source_end_ms(evt, settings.animation)
    duration_ms = max(1, source_end_ms - source_start_ms)
    active_tags = [
        tag for tag in _active_animation_tags(settings)
        if tag in TRANSFORMABLE_ANIMATION_TAGS
    ]
    if not active_tags:
        return base_events

    base_shift_map = _segment_shift_map(segments[0], active_tags)
    last_shift_map = base_shift_map
    group_resolver = _build_group_transform_target_resolver(
        evt,
        style,
        settings,
        base_meta,
        active_tags,
    )
    sequence_tags = [
        tag for tag in active_tags
        if not group_resolver or not group_resolver.handles(tag)
    ]
    tag_sequences = {}
    if sequence_tags:
        tag_sequences = _transform_tag_sequences_from_events(
            base_events,
            sequence_tags,
            settings.animation,
        )
    transformed_events = base_events
    for segment in segments[1:]:
        shift_map = _segment_shift_map(segment, active_tags)
        if _same_shift_map(shift_map, last_shift_map):
            continue

        t1 = segment.start_ms - source_start_ms
        t1 = max(0, min(duration_ms, t1))
        t2 = min(duration_ms, t1 + 1)
        if t2 <= t1:
            t2 = t1

        for idx, base_event in enumerate(transformed_events):
            tag_values = {}
            if group_resolver:
                tag_values.update(group_resolver.values_for(base_event.text, shift_map))
            if sequence_tags:
                tag_values.update(_rotated_transform_tag_values(
                    tag_sequences,
                    shift_map,
                    base_shift_map,
                    idx,
                ))
            if not tag_values:
                continue
            transform = _transform_tag(t1, t2, tag_values)
            base_event.text = _append_to_first_override(base_event.text, transform)
        last_shift_map = shift_map

    return transformed_events


def _requires_group_transform_targets(
    settings: GradientSettings,
    active_tags: list[str],
) -> bool:
    tags = set(active_tags)
    group_tags = set(getattr(settings, "group_range_tags", set()) or set())
    return bool(
        getattr(settings, "group_range_bounds", None)
        and tags
        and (not group_tags or tags & group_tags)
    )


def _build_group_transform_target_resolver(
    evt: ASSEvent,
    style,
    settings: GradientSettings,
    base_meta: Optional[str],
    active_tags: list[str],
) -> Optional[_GroupTransformTargetResolver]:
    if not _requires_group_transform_targets(settings, active_tags):
        return None

    group_bounds = getattr(settings, "group_range_bounds", None)
    if not group_bounds:
        return None

    cache_key = _group_transform_resolver_cache_key(
        evt,
        style,
        settings,
        base_meta,
        active_tags,
    )
    cached = _GROUP_RESOLVER_CACHE.get(cache_key)
    if cached is not None:
        _GROUP_RESOLVER_CACHE.move_to_end(cache_key)
        return cached

    group_tags = set(getattr(settings, "group_range_tags", set()) or set())
    direction = compute_gradient_direction(settings.mode, settings.angle)
    sequences: dict[str, _TagTransformSequence] = {}
    for tag in active_tags:
        if group_tags and tag not in group_tags:
            continue
        cfg = settings.tags.get(tag)
        if cfg is None:
            continue
        sequence_settings = copy.deepcopy(settings)
        sequence_settings.tags = {tag: copy.deepcopy(cfg)}
        sequence_settings.text_x1 = float(group_bounds[0])
        sequence_settings.text_y1 = float(group_bounds[1])
        sequence_settings.text_x2 = float(group_bounds[2])
        sequence_settings.text_y2 = float(group_bounds[3])
        sequence_settings.group_range_bounds = tuple(float(v) for v in group_bounds)
        sequence_settings.group_range_tags = {tag}
        sequence_settings.color_shift_steps = 0.0
        sequence_settings.color_shift_steps_by_tag = {}
        sequence_settings.animation.enabled = False

        try:
            sequence_events = generate_gradient(
                evt,
                style,
                sequence_settings,
                _group_sequence_base_meta(group_bounds, base_meta),
                None,
            )
        except Exception:
            continue

        positions: list[float] = []
        values: list[str] = []
        for sequence_event in sequence_events:
            axis_pos = _clip_axis_position(sequence_event.text, direction)
            tag_values = _extract_transform_tag_values(sequence_event.text, [tag])
            value = tag_values.get(tag, "")
            if axis_pos is None or not value:
                continue
            positions.append(axis_pos)
            values.append(value)

        if not positions or not values:
            continue

        ordered = sorted(zip(positions, values), key=lambda item: item[0])
        ordered_positions = [pos for pos, _value in ordered]
        ordered_values = [value for _pos, value in ordered]
        blended_values = _with_seam_blend_sequences(
            {tag: ordered_values},
            settings.animation,
        ).get(tag, ordered_values)
        sequences[tag] = _TagTransformSequence(
            positions=ordered_positions,
            values=blended_values,
        )

    if not sequences:
        return None
    resolver = _GroupTransformTargetResolver(sequences=sequences, direction=direction)
    _GROUP_RESOLVER_CACHE[cache_key] = resolver
    _GROUP_RESOLVER_CACHE.move_to_end(cache_key)
    while len(_GROUP_RESOLVER_CACHE) > _GROUP_RESOLVER_CACHE_MAX:
        _GROUP_RESOLVER_CACHE.popitem(last=False)
    return resolver


def _group_transform_resolver_cache_key(
    evt: ASSEvent,
    style,
    settings: GradientSettings,
    base_meta: Optional[str],
    active_tags: list[str],
) -> str:
    tags = sorted(set(active_tags))
    context_tags = _transform_target_cache_context_tags(settings, tags)
    tag_configs = {
        tag: settings.tags.get(tag)
        for tag in context_tags
        if settings.tags.get(tag) is not None
    }
    animation = getattr(settings, "animation", None)
    seam_lengths = getattr(animation, "seam_blend_lengths", {}) or {}
    return stable_preview_key(
        {
            "version": 2,
            "event_text": evt.text,
            "event_style": evt.style,
            "base_meta": base_meta,
            "style": getattr(style, "raw", None) or getattr(style, "name", None),
            "mode": getattr(settings.mode, "value", str(settings.mode)),
            "angle": settings.angle,
            "step": settings.step,
            "color_space": getattr(settings.color_space, "value", str(settings.color_space)),
            "group_range_bounds": settings.group_range_bounds,
            "group_range_tags": sorted(set(getattr(settings, "group_range_tags", set()) or [])),
            "active_tags": tags,
            "context_tags": context_tags,
            "tag_configs": tag_configs,
            "video_path": settings.video_path,
            "video_frame": settings.video_frame,
            "video_time": settings.video_time,
            "sampling_paths": {
                tag: (getattr(settings, "sampling_paths", {}) or {}).get(tag, "")
                for tag in tags
            },
            "path_smooth": {
                tag: (getattr(settings, "path_sampling_smooth", {}) or {}).get(tag, False)
                for tag in tags
            },
            "path_smooth_strength": {
                tag: (getattr(settings, "path_sampling_smooth_strength", {}) or {}).get(tag, 1.0)
                for tag in tags
            },
            "path_mirror": {
                tag: (getattr(settings, "path_sampling_mirror", {}) or {}).get(tag, False)
                for tag in tags
            },
            "seam_blend_length": getattr(animation, "seam_blend_length", 0),
            "seam_blend_lengths": {
                tag: seam_lengths.get(tag, 0)
                for tag in tags
            },
        }
    )


def _transform_target_cache_context_tags(
    settings: GradientSettings,
    active_tags: list[str],
) -> list[str]:
    """Tags that invalidate cached targets without joining the color sequence."""

    tags = set(active_tags)
    for tag, cfg in settings.tags.items():
        if tag not in TRANSFORM_TARGET_CACHE_CONTEXT_TAGS:
            continue
        if getattr(cfg, "enabled", False):
            tags.add(tag)
    return sorted(tags)


def _group_sequence_base_meta(
    group_bounds,
    base_meta: Optional[str],
) -> str:
    x1, y1, x2, y2 = (float(value) for value in group_bounds)
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    align = "5"
    pos_x = cx
    pos_y = cy
    org_x = cx
    org_y = cy
    if base_meta:
        parts = [part.strip() for part in base_meta.split(",")]
        if len(parts) > 4 and parts[4]:
            align = parts[4]
        try:
            if len(parts) > 6:
                pos_x = float(parts[5])
                pos_y = float(parts[6])
        except (TypeError, ValueError):
            pos_x = cx
            pos_y = cy
        try:
            if len(parts) > 8:
                org_x = float(parts[7])
                org_y = float(parts[8])
            else:
                org_x = pos_x
                org_y = pos_y
        except (TypeError, ValueError):
            org_x = pos_x
            org_y = pos_y
    return (
        f"{x1},{y1},{x2},{y2},"
        f"{align},{pos_x},{pos_y},{org_x},{org_y}"
    )


def _nearest_position_index(positions: list[float], axis_pos: float) -> int:
    if len(positions) <= 1:
        return 0
    idx = bisect_left(positions, axis_pos)
    if idx <= 0:
        return 0
    if idx >= len(positions):
        return len(positions) - 1
    left = positions[idx - 1]
    right = positions[idx]
    return idx - 1 if abs(axis_pos - left) <= abs(right - axis_pos) else idx


def _clip_axis_position(text: str, direction) -> Optional[float]:
    matches = list(re.finditer(r"\\clip\(([^)]*)\)", text))
    if not matches:
        return None
    content = matches[-1].group(1).strip()
    if not content:
        return None

    normalized = content.replace(",", " ")
    tokens = normalized.split()
    has_path_cmd = any(tok.lower() in {"m", "n", "l", "b", "s", "p", "c"} for tok in tokens)
    if not has_path_cmd:
        try:
            x1, y1, x2, y2 = [float(tok) for tok in tokens[:4]]
        except (TypeError, ValueError):
            return None
        if direction.is_horizontal:
            return min(x1, x2)
        if direction.is_vertical:
            return min(y1, y2)
        return min(
            x1 * direction.cos_a + y1 * direction.sin_a,
            x2 * direction.cos_a + y2 * direction.sin_a,
        )

    coord_tokens = tokens
    if coord_tokens:
        try:
            float(coord_tokens[0])
            if len(coord_tokens) > 1 and coord_tokens[1].lower() in {"m", "n", "l", "b", "s", "p", "c"}:
                coord_tokens = coord_tokens[1:]
        except ValueError:
            pass

    for idx, tok in enumerate(coord_tokens):
        if tok.lower() not in {"m", "n", "l", "b", "s", "p", "c"}:
            continue
        if idx + 2 >= len(coord_tokens):
            continue
        try:
            x = float(coord_tokens[idx + 1])
            y = float(coord_tokens[idx + 2])
        except ValueError:
            continue
        return x * direction.cos_a + y * direction.sin_a
    return None

def _transform_tag_sequences_from_events(
    events: list[ASSEvent],
    active_tags: list[str],
    animation=None,
) -> dict[str, list[str]]:
    sequences: dict[str, list[str]] = {tag: [] for tag in active_tags}
    for event in events:
        values = _extract_transform_tag_values(event.text, active_tags)
        for tag in active_tags:
            sequences[tag].append(values.get(tag, ""))
    return _with_seam_blend_sequences(sequences, animation)


def _with_seam_blend_sequences(
    sequences: dict[str, list[str]],
    animation,
) -> dict[str, list[str]]:
    per_tag_lengths = getattr(animation, "seam_blend_lengths", {}) or {}
    if not isinstance(per_tag_lengths, dict):
        per_tag_lengths = {}
    try:
        fallback_length = int(getattr(animation, "seam_blend_length", 0) or 0)
    except (TypeError, ValueError):
        fallback_length = 0
    if fallback_length <= 0 and not per_tag_lengths:
        return sequences

    blended: dict[str, list[str]] = {}
    for tag, sequence in sequences.items():
        try:
            blend_length = int(per_tag_lengths.get(tag, fallback_length) or 0)
        except (TypeError, ValueError):
            blend_length = fallback_length
        clean_sequence = list(sequence)
        if blend_length > 0 and len(clean_sequence) >= 2 and clean_sequence[0] and clean_sequence[-1]:
            clean_sequence.extend(
                _seam_blend_values(tag, clean_sequence[-1], clean_sequence[0], blend_length)
            )
        blended[tag] = clean_sequence
    return blended


def _seam_blend_values(tag: str, tail: str, head: str, length: int) -> list[str]:
    if length <= 0:
        return []
    values: list[str] = []
    for idx in range(length):
        t = (idx + 1) / (length + 1)
        value = _interpolate_tag_value(tag, tail, head, t)
        if value:
            values.append(value)
    return values


def _interpolate_tag_value(tag: str, left: str, right: str, t: float) -> str:
    t = max(0.0, min(1.0, float(t)))
    if tag in COLOR_TAGS:
        left_color = _extract_hex_tag_value(left, 6)
        right_color = _extract_hex_tag_value(right, 6)
        if left_color and right_color:
            return f"\\{tag}&H{_interpolate_hex(left_color, right_color, t)}&"
        return ""
    if tag in ALPHA_TAGS:
        left_alpha = _extract_hex_tag_value(left, 2)
        right_alpha = _extract_hex_tag_value(right, 2)
        if left_alpha and right_alpha:
            return f"\\{tag}&H{_interpolate_hex(left_alpha, right_alpha, t)}&"
        return ""
    if tag in NUMERIC_TAGS:
        left_num = _extract_numeric_tag_value(tag, left)
        right_num = _extract_numeric_tag_value(tag, right)
        if left_num is not None and right_num is not None:
            value = left_num + (right_num - left_num) * t
            return f"\\{tag}{_format_number(value)}"
    return ""


def _extract_hex_tag_value(text: str, digits: int) -> Optional[str]:
    match = re.search(rf"&H([0-9A-Fa-f]{{{digits}}})&", text)
    return match.group(1).upper() if match else None


def _interpolate_hex(left: str, right: str, t: float) -> str:
    channels = []
    for i in range(0, len(left), 2):
        a = int(left[i:i + 2], 16)
        b = int(right[i:i + 2], 16)
        channels.append(max(0, min(255, int(round(a + (b - a) * t)))))
    return "".join(f"{channel:02X}" for channel in channels)


def _extract_numeric_tag_value(tag: str, text: str) -> Optional[float]:
    match = re.search(rf"\\{re.escape(tag)}(-?\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return f"{value:.2f}"


def _rotated_transform_tag_values(
    sequences: dict[str, list[str]],
    shift_map: dict[str, float],
    base_shift_map: dict[str, float],
    event_index: int,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for tag, sequence in sequences.items():
        if not sequence:
            continue
        delta = _round_shift(
            float(shift_map.get(tag, 0.0)) - float(base_shift_map.get(tag, 0.0))
        )
        source_index = (int(event_index) - delta) % len(sequence)
        value = sequence[source_index]
        if value:
            values[tag] = value
    return values


def _apply_segment_shift(settings: GradientSettings, segment: _AnimationSegment) -> None:
    if segment.shift_by_tag:
        settings.color_shift_steps = 0.0
        settings.color_shift_steps_by_tag = dict(segment.shift_by_tag)
    else:
        settings.color_shift_steps = segment.shift
        settings.color_shift_steps_by_tag = {}


def _segment_shift_map(segment: _AnimationSegment, tags: list[str]) -> dict[str, float]:
    if segment.shift_by_tag:
        return {tag: float(segment.shift_by_tag.get(tag, segment.shift)) for tag in tags}
    return {tag: float(segment.shift) for tag in tags}


def _same_shift_map(left: dict[str, float], right: dict[str, float]) -> bool:
    tags = set(left) | set(right)
    return all(abs(float(left.get(tag, 0.0)) - float(right.get(tag, 0.0))) < 1e-9 for tag in tags)


def _should_use_transform_animation(settings: GradientSettings) -> bool:
    animation = getattr(settings, "animation", None)
    if not bool(getattr(animation, "use_transform", True)):
        return False
    # GBC emits many inline override blocks inside one event; keep the legacy
    # split mode there until per-character transform merging is implemented.
    if settings.mode == GradientMode.GBC:
        return False
    active_tags = _active_animation_tags(settings)
    if not active_tags:
        return False
    return all(tag in TRANSFORMABLE_ANIMATION_TAGS for tag in active_tags)


def _should_animate_gradient_bands(settings: GradientSettings) -> bool:
    animation = getattr(settings, "animation", None)
    if not animation or not getattr(animation, "enabled", False):
        return False
    return bool(_active_animation_tags(settings))


def _active_animation_tags(settings: GradientSettings) -> list[str]:
    active_tags = [
        tag for tag, cfg in settings.tags.items()
        if tag in TAG_INFO and getattr(cfg, "enabled", False)
    ]
    selected_tags = set(getattr(settings.animation, "enabled_tags", set()) or [])
    if selected_tags:
        active_tags = [tag for tag in active_tags if tag in selected_tags]
    return active_tags


def _extract_transform_tag_values(text: str, active_tags: list[str]) -> dict[str, str]:
    tags = set(active_tags)
    values: dict[str, str] = {}
    for match in re.finditer(r"\\([1-4]c)&H([0-9A-Fa-f]{6})&", text):
        tag = match.group(1)
        if tag in tags:
            values[tag] = f"\\{tag}&H{match.group(2).upper()}&"
    for match in re.finditer(r"\\(alpha|[1-4]a)&H([0-9A-Fa-f]{2})&", text):
        tag = match.group(1)
        if tag in tags:
            values[tag] = f"\\{tag}&H{match.group(2).upper()}&"
    for tag in set(NUMERIC_TAGS) - {"shad"}:
        if tag not in tags:
            continue
        match = re.search(rf"\\{re.escape(tag)}(-?\d+(?:\.\d+)?)", text)
        if match:
            values[tag] = f"\\{tag}{match.group(1)}"
    if "shad" in tags:
        match = re.search(r"\\shad(-?\d+(?:\.\d+)?)", text)
        if match:
            values["shad"] = f"\\shad{match.group(1)}"
        else:
            x_match = re.search(r"\\xshad(-?\d+(?:\.\d+)?)", text)
            y_match = re.search(r"\\yshad(-?\d+(?:\.\d+)?)", text)
            parts = []
            if x_match:
                parts.append(f"\\xshad{x_match.group(1)}")
            if y_match:
                parts.append(f"\\yshad{y_match.group(1)}")
            if parts:
                values["shad"] = "".join(parts)
    return values


def _transform_tag(t1: int, t2: int, tag_values: dict[str, str]) -> str:
    inner = "".join(
        tag_values[tag]
        for tag in sorted(tag_values, key=_tag_order)
        if tag in tag_values
    )
    return f"\\t({int(t1)},{int(t2)},{inner})"


def _tag_order(tag: str) -> int:
    try:
        return list(TAG_INFO).index(tag)
    except ValueError:
        return len(TAG_INFO)


def _append_to_first_override(text: str, tags: str) -> str:
    if not tags:
        return text
    if text.startswith("{"):
        end = text.find("}")
        if end >= 0:
            return text[:end] + tags + text[end:]
    return "{" + tags + "}" + text


def _replace_or_append_first_override(text: str, tag_values: dict[str, str]) -> str:
    if not tag_values:
        return text
    replacement = "".join(
        tag_values[tag]
        for tag in sorted(tag_values, key=_tag_order)
        if tag_values.get(tag)
    )
    if not replacement:
        return text

    if text.startswith("{"):
        end = text.find("}")
        if end >= 0:
            inner = text[1:end]
            inner = _remove_override_tags(inner, set(tag_values))
            return "{" + inner + replacement + "}" + text[end + 1:]
    return "{" + replacement + "}" + text


def _remove_override_tags(text: str, tags: set[str]) -> str:
    cleaned = text
    for tag in tags:
        if tag in COLOR_TAGS:
            cleaned = re.sub(rf"\\{re.escape(tag)}&H[0-9A-Fa-f]{{6}}&", "", cleaned)
        elif tag in ALPHA_TAGS:
            cleaned = re.sub(rf"\\{re.escape(tag)}&H[0-9A-Fa-f]{{2}}&", "", cleaned)
        elif tag == "shad":
            cleaned = re.sub(r"\\shad-?\d+(?:\.\d+)?", "", cleaned)
            cleaned = re.sub(r"\\xshad-?\d+(?:\.\d+)?", "", cleaned)
            cleaned = re.sub(r"\\yshad-?\d+(?:\.\d+)?", "", cleaned)
        elif tag in NUMERIC_TAGS:
            cleaned = re.sub(rf"\\{re.escape(tag)}-?\d+(?:\.\d+)?", "", cleaned)
    return cleaned


def _animation_segments(
    evt: ASSEvent,
    settings: GradientSettings,
) -> list[_AnimationSegment]:
    animation = settings.animation
    plan = _animation_frame_plan(evt, animation)
    start_ms = plan.source_start_ms
    end_ms = plan.source_end_ms
    if end_ms <= start_ms:
        return [_AnimationSegment(evt, 0.0, start_ms, end_ms)]

    first_frame = plan.first_frame
    last_frame = plan.last_frame
    frame_count = max(1, last_frame - first_frame + 1)
    try:
        start_setting = int(getattr(animation, "start_frame", 0))
    except (TypeError, ValueError):
        start_setting = 0
    try:
        end_setting = int(getattr(animation, "end_frame", -1))
    except (TypeError, ValueError):
        end_setting = -1
    start_offset = max(0, min(start_setting, frame_count - 1))
    end_offset = frame_count - 1 if end_setting < 0 else max(start_offset, min(end_setting, frame_count - 1))
    animation_start_frame = first_frame + start_offset
    animation_end_frame = first_frame + end_offset
    direction = 1 if int(getattr(animation, "direction", 1) or 1) >= 0 else -1
    active_tags = _active_animation_tags(settings)
    frame_steps = {
        tag: _animation_frame_step_for_tag(animation, tag)
        for tag in active_tags
    }
    animated_frame_count = max(1, animation_end_frame - animation_start_frame + 1)
    segment_counts = {
        tag: max(1, int(math.ceil(animated_frame_count / step)))
        for tag, step in frame_steps.items()
    }
    tag_interval_indices = {tag: 0 for tag in active_tags}

    segments: list[_AnimationSegment] = []
    if animation_start_frame > first_frame:
        prefix_end_ms = min(end_ms, _frame_boundary_ms(plan, animation_start_frame))
        if prefix_end_ms > start_ms:
            zero_map = {tag: 0.0 for tag in active_tags}
            segments.append(_AnimationSegment(
                _copy_event_with_ms(evt, start_ms, prefix_end_ms),
                0.0,
                start_ms,
                prefix_end_ms,
                zero_map,
            ))

    cumulative_shifts = {tag: 0.0 for tag in active_tags}
    boundaries = {animation_start_frame, animation_end_frame + 1}
    for tag, step in frame_steps.items():
        tag_count = segment_counts[tag]
        for index in range(tag_count + 1):
            frame = min(animation_end_frame + 1, animation_start_frame + index * step)
            boundaries.add(frame)
    ordered_boundaries = sorted(boundaries)
    last_segment_shift_map = {tag: 0.0 for tag in active_tags}

    for segment_start_frame, segment_end_frame in zip(ordered_boundaries, ordered_boundaries[1:]):
        if segment_end_frame <= segment_start_frame:
            continue
        segment_start_ms = max(start_ms, _frame_boundary_ms(plan, segment_start_frame))
        segment_end_ms = min(end_ms, _frame_boundary_ms(plan, segment_end_frame))
        if segment_end_ms <= segment_start_ms:
            continue

        segment_shift_map = {
            tag: cumulative_shifts.get(tag, 0.0) * direction
            for tag in active_tags
        }
        first_shift = next(iter(segment_shift_map.values()), 0.0)
        segments.append(_AnimationSegment(
            _copy_event_with_ms(evt, segment_start_ms, segment_end_ms),
            first_shift,
            segment_start_ms,
            segment_end_ms,
            segment_shift_map,
        ))
        last_segment_shift_map = dict(segment_shift_map)

        for tag in active_tags:
            step = frame_steps.get(tag, 1)
            tag_count = segment_counts.get(tag, 1)
            while tag_interval_indices[tag] < tag_count:
                interval_start = animation_start_frame + tag_interval_indices[tag] * step
                next_interval_start = min(
                    animation_end_frame + 1,
                    animation_start_frame + (tag_interval_indices[tag] + 1) * step,
                )
                if next_interval_start > segment_end_frame:
                    break
                relative_frame = max(0, interval_start - first_frame)
                cumulative_shifts[tag] = cumulative_shifts.get(tag, 0.0) + _animation_move_for_tag(
                    animation,
                    tag,
                    relative_frame,
                    tag_interval_indices[tag],
                    tag_count,
                )
                tag_interval_indices[tag] += 1

    if animation_end_frame < last_frame:
        suffix_start_ms = max(start_ms, _frame_boundary_ms(plan, animation_end_frame + 1))
        if suffix_start_ms < end_ms:
            final_shift = next(iter(last_segment_shift_map.values()), 0.0)
            final_shift_map = dict(last_segment_shift_map) if segments else {
                tag: 0.0 for tag in active_tags
            }
            segments.append(_AnimationSegment(
                _copy_event_with_ms(evt, suffix_start_ms, end_ms),
                final_shift,
                suffix_start_ms,
                end_ms,
                final_shift_map,
            ))

    return segments or [_AnimationSegment(evt, 0.0, start_ms, end_ms)]


def _animation_frame_step_for_tag(animation, tag: str) -> int:
    frame_steps = getattr(animation, "frame_steps", {}) or {}
    raw_value = None
    if isinstance(frame_steps, dict):
        raw_value = frame_steps.get(tag)
    if raw_value is None:
        raw_value = getattr(animation, "frame_step", 1)
    try:
        return max(1, int(raw_value or 1))
    except (TypeError, ValueError):
        return 1


def _animation_move_for_tag(
    animation,
    tag: str,
    relative_frame: int,
    segment_index: int,
    segment_count: int,
) -> float:
    curves = getattr(animation, "shift_curves", {}) or {}
    nodes = curves.get(tag) if isinstance(curves, dict) else None
    if nodes:
        modes = getattr(animation, "shift_modes", {}) or {}
        mode = (
            modes.get(tag, InterpolationMode.LINEAR)
            if isinstance(modes, dict)
            else InterpolationMode.LINEAR
        )
        if not isinstance(mode, InterpolationMode):
            try:
                mode = InterpolationMode(str(mode))
            except ValueError:
                mode = InterpolationMode.LINEAR
        try:
            return float(_round_shift(interpolate(nodes, float(relative_frame), mode, is_color=False)))
        except (TypeError, ValueError):
            return 0.0

    move_t = segment_index / max(segment_count - 2, 1)
    move_t = max(0.0, min(1.0, move_t))
    return float(_round_shift(animation.shift_start + (animation.shift_end - animation.shift_start) * move_t))


def _round_shift(value) -> int:
    value = float(value)
    if value >= 0:
        return int(math.floor(value + 0.5))
    return int(math.ceil(value - 0.5))


def _animation_frame_plan(evt: ASSEvent, animation) -> _AnimationFramePlan:
    fps = _animation_fps(animation)
    source_start_ms = _event_source_start_ms(evt, animation)
    source_end_ms = _event_source_end_ms(evt, animation)
    if source_end_ms <= source_start_ms:
        source_end_ms = _event_time_to_ms(evt.end)

    first_frame = _optional_int(getattr(animation, "event_first_frame", None))
    last_frame = _optional_int(getattr(animation, "event_last_frame", None))
    frame_times = {
        int(frame): int(ms)
        for frame, ms in dict(getattr(animation, "frame_time_ms", {}) or {}).items()
    }
    if first_frame is not None and last_frame is not None:
        first_frame = max(0, int(first_frame))
        last_frame = max(first_frame, int(last_frame))
    else:
        first_frame, last_frame = _event_frame_bounds(
            source_start_ms / 1000.0, source_end_ms / 1000.0, fps
        )
        for frame in range(first_frame, last_frame + 2):
            frame_times.setdefault(frame, int(round(frame * 1000.0 / fps)))

    return _AnimationFramePlan(
        first_frame=first_frame,
        last_frame=last_frame,
        source_start_ms=source_start_ms,
        source_end_ms=source_end_ms,
        frame_time_ms=frame_times,
        fps=fps,
    )


def _frame_boundary_ms(plan: _AnimationFramePlan, frame: int) -> int:
    frame = int(frame)
    if frame in plan.frame_time_ms:
        return int(plan.frame_time_ms[frame])
    if frame <= plan.first_frame:
        return plan.source_start_ms
    if frame > plan.last_frame:
        return plan.source_end_ms
    return int(round(frame * 1000.0 / plan.fps))


def _event_source_start_ms(evt: ASSEvent, animation) -> int:
    value = _optional_int(getattr(animation, "event_start_ms", None))
    return value if value is not None else _event_time_to_ms(evt.start)


def _event_source_end_ms(evt: ASSEvent, animation) -> int:
    value = _optional_int(getattr(animation, "event_end_ms", None))
    return value if value is not None else _event_time_to_ms(evt.end)


def _event_time_to_ms(time_text: str) -> int:
    return int(round(time_to_seconds(time_text) * 1000.0))


def _optional_int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _animation_fps(animation) -> float:
    try:
        fps = float(animation.fps)
    except (TypeError, ValueError):
        fps = 23.976
    return max(1.0, min(240.0, fps))


def _event_frame_bounds(start_sec: float, end_sec: float, fps: float) -> tuple[int, int]:
    first_frame = max(0, int(math.ceil(start_sec * fps - 1e-6)))
    last_frame = max(first_frame, int(math.ceil(end_sec * fps - 1e-6)))
    return first_frame, last_frame


def _copy_event_with_time(evt: ASSEvent, start_sec: float, end_sec: float) -> ASSEvent:
    segment_evt = copy.copy(evt)
    segment_evt.start = seconds_to_time(start_sec)
    segment_evt.end = seconds_to_time(end_sec)
    return segment_evt


def _copy_event_with_ms(evt: ASSEvent, start_ms: int, end_ms: int) -> ASSEvent:
    return _copy_event_with_time(evt, start_ms / 1000.0, end_ms / 1000.0)


def build_preview_ass(ass_file: ASSFile, events: list[ASSEvent]) -> str:
    """Build a minimal ASS document for preview rendering."""

    lines: list[str] = []
    lines.append("[Script Info]")
    for key, value in ass_file.script_info.items():
        lines.append(f"{key}: {value}")
    lines.append("")
    lines.append("[V4+ Styles]")
    if ass_file.styles_format:
        lines.append(ass_file.styles_format)
    for style in ass_file.styles:
        lines.append(style.raw)
    lines.append("")
    lines.append("[Events]")
    if ass_file.events_format:
        lines.append(ass_file.events_format)
    for event in events:
        lines.append(event.to_ass_line())
    lines.append("")
    return "\n".join(lines)
