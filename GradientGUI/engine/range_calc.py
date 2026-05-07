"""Debuggable range calculation for gradient clip generation."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from .ass_parser import ASSEvent, ASSStyle, ASSFile
from .interpolation import interpolate, make_default_nodes
from .libass_bounds import (
    IMAGE_TYPE_CHARACTER,
    IMAGE_TYPE_OUTLINE,
    IMAGE_TYPE_SHADOW,
    LibassBoundsError,
    measure_event_bounds,
)
from .path_tracer import project_bounds
from .tag_parser import (
    TAG_INFO,
    build_tag_string,
    get_bord_sizes,
    get_shad_offsets,
    get_tag_value,
    remove_specific_tag,
    strip_tags,
)


Rect = tuple[float, float, float, float]
ProjectedRange = tuple[float, float, float, float]


GEOMETRY_TAGS = ("fscx", "fscy", "fs", "fsp", "fax", "fay", "frx", "fry", "frz")
BORDER_SIZE_TAGS = ("bord", "xbord", "ybord")
SHADOW_TAGS = ("shad", "xshad", "yshad")
EXTENT_EXPANDING_TAGS = BORDER_SIZE_TAGS + SHADOW_TAGS + ("blur", "be")
VISIBLE_RANGE_TAGS = GEOMETRY_TAGS + (
    *BORDER_SIZE_TAGS, *SHADOW_TAGS,
    "alpha", "1a", "2a", "3a", "4a",
    "blur", "be",
)


@dataclass(frozen=True)
class GradientDirection:
    effective_angle: float
    cos_a: float
    sin_a: float
    is_horizontal: bool
    is_vertical: bool

    @property
    def is_axis_aligned(self) -> bool:
        return self.is_horizontal or self.is_vertical


@dataclass(frozen=True)
class EffectExtents:
    max_xbord: float = 0.0
    max_ybord: float = 0.0
    min_xshad: float = 0.0
    max_xshad: float = 0.0
    min_yshad: float = 0.0
    max_yshad: float = 0.0
    has_border: bool = False
    has_shadow: bool = False

    def expand_for_enabled(self, rect: Rect) -> Rect:
        expanded = rect
        if self.has_border:
            expanded = self.expand_for_border(expanded)
        if self.has_shadow:
            expanded = self.expand_for_shadow(expanded)
        return expanded

    def expand_for_tag(self, tag_name: str, rect: Rect) -> Rect:
        if tag_name == "alpha":
            return self.expand_for_alpha(rect)
        if tag_name in ("3c", "3a", *BORDER_SIZE_TAGS):
            return self.expand_for_border(rect)
        if tag_name in ("4c", "4a", *SHADOW_TAGS):
            return self.expand_for_shadow(rect)
        return rect

    def expand_for_alpha(self, rect: Rect) -> Rect:
        rx1, ry1, rx2, ry2 = rect
        return (
            min(rx1, rx1 - self.max_xbord, rx1 - self.max_xbord + self.min_xshad),
            min(ry1, ry1 - self.max_ybord, ry1 - self.max_ybord + self.min_yshad),
            max(rx2, rx2 + self.max_xbord, rx2 + self.max_xbord + self.max_xshad),
            max(ry2, ry2 + self.max_ybord, ry2 + self.max_ybord + self.max_yshad),
        )

    def expand_for_border(self, rect: Rect) -> Rect:
        rx1, ry1, rx2, ry2 = rect
        return (
            rx1 - self.max_xbord,
            ry1 - self.max_ybord,
            rx2 + self.max_xbord,
            ry2 + self.max_ybord,
        )

    def expand_for_shadow(self, rect: Rect) -> Rect:
        rx1, ry1, rx2, ry2 = rect
        return (
            rx1 - self.max_xbord + self.min_xshad,
            ry1 - self.max_ybord + self.min_yshad,
            rx2 + self.max_xbord + self.max_xshad,
            ry2 + self.max_ybord + self.max_yshad,
        )


@dataclass
class RangeDebug:
    enabled: bool = False
    auto_dump: bool = False
    steps: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> "RangeDebug":
        value = os.environ.get("GRADIENTGUI_DEBUG_RANGE", "")
        enabled = value.lower() in {"1", "true", "yes", "on"}
        return cls(enabled=enabled, auto_dump=enabled)

    def add(self, name: str, **values: Any) -> None:
        if self.enabled:
            self.steps.append((name, values))

    def dump(self, prefix: str = "[RANGE]") -> None:
        if not self.enabled:
            return
        for name, values in self.steps:
            print(f"{prefix} {name}: {values}")


@dataclass
class RangePlan:
    direction: GradientDirection
    base_rect: Rect
    expanded_rect: Rect
    clip_rect: Rect
    event_projected_range: ProjectedRange
    tag_projected_ranges: dict[str, ProjectedRange]
    group_projected_ranges: dict[str, ProjectedRange]
    geometry_range: Optional[ProjectedRange] = None
    rendered_range: Optional[ProjectedRange] = None
    source_clip_bounds: Optional[Rect] = None
    needs_rendered_range: bool = False
    range_source: str = "expanded"
    debug: RangeDebug = field(default_factory=RangeDebug)

    @property
    def g_min(self) -> float:
        return self.event_projected_range[0]

    @property
    def g_max(self) -> float:
        return self.event_projected_range[1]

    @property
    def p_min(self) -> float:
        return self.event_projected_range[2]

    @property
    def p_max(self) -> float:
        return self.event_projected_range[3]

    @property
    def has_projected_range(self) -> bool:
        return self.range_source in {"geometry", "libass", "refined"} or bool(
            self.group_projected_ranges
        )

    def t_for_tag(self, tag_name: str, axis_pos: float) -> float:
        tag_g_min, tag_g_max, _, _ = self.tag_projected_ranges.get(
            tag_name, self.event_projected_range
        )
        return (axis_pos - tag_g_min) / max(tag_g_max - tag_g_min, 1.0)


def compute_gradient_direction(mode: Any, angle: float) -> GradientDirection:
    mode_value = str(getattr(mode, "value", mode)).lower()
    if mode_value == "horizontal":
        effective_angle = angle
    elif mode_value == "vertical":
        effective_angle = 90.0 + angle
    else:
        effective_angle = angle

    rad = math.radians(effective_angle)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    angle_mod = effective_angle % 360
    return GradientDirection(
        effective_angle=effective_angle,
        cos_a=cos_a,
        sin_a=sin_a,
        is_horizontal=abs(angle_mod) < 0.01 or abs(angle_mod - 360) < 0.01,
        is_vertical=abs(angle_mod - 90) < 0.01 or abs(angle_mod - 270) < 0.01,
    )


def compute_effect_extents(
    event_text: str,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
) -> EffectExtents:
    base_xbord, base_ybord = get_bord_sizes(event_text, style)
    base_xshad, base_yshad = get_shad_offsets(event_text, style)

    def tag_min_max(
        tag_name: str,
        base_val: float,
        min_value: Optional[float] = None,
    ) -> tuple[float, float]:
        cfg = enabled_tags.get(tag_name)
        if not cfg or not getattr(cfg, "nodes", None):
            if min_value is not None:
                base_val = max(float(min_value), float(base_val))
            return base_val, base_val
        y_vals = [float(n.y) for n in cfg.nodes]
        if min_value is not None:
            y_vals = [max(float(min_value), v) for v in y_vals]
        return min(y_vals), max(y_vals)

    bord_min, bord_max = tag_min_max("bord", base_xbord, min_value=0.0)
    xbord_min, xbord_max = tag_min_max("xbord", base_xbord, min_value=0.0)
    ybord_min, ybord_max = tag_min_max("ybord", base_ybord, min_value=0.0)
    del bord_min, xbord_min, ybord_min

    shad_min, shad_max = tag_min_max("shad", base_xshad)
    xshad_min, xshad_max = tag_min_max("xshad", base_xshad)
    yshad_min, yshad_max = tag_min_max("yshad", base_yshad)

    return EffectExtents(
        max_xbord=max(bord_max, xbord_max),
        max_ybord=max(bord_max, ybord_max),
        min_xshad=min(shad_min, xshad_min),
        max_xshad=max(shad_max, xshad_max),
        min_yshad=min(shad_min, yshad_min),
        max_yshad=max(shad_max, yshad_max),
        has_border=any(
            t in enabled_tags for t in ("3c", "3a", "bord", "xbord", "ybord")
        ),
        has_shadow=any(
            t in enabled_tags for t in ("4c", "4a", "shad", "xshad", "yshad")
        ),
    )


def calculate_range_plan(
    event: ASSEvent,
    ass_file: Optional[ASSFile],
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
    base_rect: Rect,
    source_clip_bounds: Optional[Rect],
    geom_context: Optional[dict[str, float]],
    debug: Optional[RangeDebug] = None,
) -> RangePlan:
    debug = debug or RangeDebug.from_env()
    direction = compute_gradient_direction(settings.mode, settings.angle)
    extents = compute_effect_extents(event.text, style, settings, enabled_tags)
    tx1, ty1, tx2, ty2 = base_rect
    expanded_rect = extents.expand_for_enabled(base_rect)

    debug.add(
        "direction",
        effective_angle=direction.effective_angle,
        cos=direction.cos_a,
        sin=direction.sin_a,
        axis_aligned=direction.is_axis_aligned,
    )
    debug.add("base_rect", rect=base_rect)
    debug.add("effect_extents", extents=extents)
    debug.add("expanded_rect", rect=expanded_rect)

    if source_clip_bounds is not None:
        tx1, ty1, tx2, ty2 = source_clip_bounds
        expanded_rect = source_clip_bounds
        debug.add("source_clip_override", rect=source_clip_bounds)

    group_projected_ranges: dict[str, ProjectedRange] = {}
    group_bounds = getattr(settings, "group_range_bounds", None)
    group_tags = set(getattr(settings, "group_range_tags", set()) or set())
    if group_bounds:
        for tag_name in enabled_tags:
            if tag_name in group_tags:
                tag_rect = extents.expand_for_tag(tag_name, group_bounds)
                group_projected_ranges[tag_name] = project_bounds(
                    *tag_rect, direction.cos_a, direction.sin_a
                )
        debug.add("group_projected_ranges", ranges=group_projected_ranges)

    geometry_range: Optional[ProjectedRange] = None
    if (
        source_clip_bounds is None
        and geom_context is not None
        and any(t in enabled_tags for t in GEOMETRY_TAGS)
    ):
        geometry_base_rect = extents.expand_for_enabled(
            (
                geom_context["bx1"],
                geom_context["by1"],
                geom_context["bx2"],
                geom_context["by2"],
            )
        )
        geometry_range = compute_geometry_projected_range(
            event, ass_file, geometry_base_rect, geom_context, parsed, style,
            settings, enabled_tags, direction.cos_a, direction.sin_a,
        )
        debug.add("geometry_range", base_rect=geometry_base_rect, range=geometry_range)

    expanded_range = project_bounds(*expanded_rect, direction.cos_a, direction.sin_a)
    event_range = expanded_range
    range_source = "expanded"
    if geometry_range:
        event_range = geometry_range
        range_source = "geometry"

    rendered_range = None
    needs_rendered_range = any(tag in enabled_tags for tag in VISIBLE_RANGE_TAGS)
    if source_clip_bounds is None and ass_file is not None and needs_rendered_range:
        rendered_range = compute_libass_projected_range(
            event, ass_file, parsed, style, settings, enabled_tags,
            direction.cos_a, direction.sin_a,
        )
        if rendered_range is not None:
            event_range = rendered_range
            range_source = "libass"
        debug.add("libass_range", range=rendered_range)

    if (
        ass_file is not None
        and direction.is_axis_aligned
        and needs_rendered_range
        and source_clip_bounds is None
        and not any(tag in enabled_tags for tag in EXTENT_EXPANDING_TAGS)
    ):
        g_min, g_max, p_min, p_max = event_range
        for _ in range(4):
            refined_range = refine_libass_axis_range_with_strips(
                event, ass_file, parsed, style, settings, enabled_tags,
                g_min, g_max, p_min, p_max, direction.cos_a, direction.sin_a,
            )
            debug.add("refine_pass", input=(g_min, g_max), result=refined_range)
            if refined_range is None:
                break
            new_g_min, new_g_max = refined_range
            if abs(new_g_min - g_min) < 0.01 and abs(new_g_max - g_max) < 0.01:
                break
            g_min, g_max = new_g_min, new_g_max
            event_range = (g_min, g_max, p_min, p_max)
            range_source = "refined"

    clip_rect = _clip_rect_from_range(expanded_rect, event_range, direction)
    tag_projected_ranges = {
        tag_name: event_range
        for tag_name in enabled_tags
    }
    tag_projected_ranges.update(group_projected_ranges)

    plan = RangePlan(
        direction=direction,
        base_rect=(tx1, ty1, tx2, ty2),
        expanded_rect=expanded_rect,
        clip_rect=clip_rect,
        event_projected_range=event_range,
        tag_projected_ranges=tag_projected_ranges,
        group_projected_ranges=group_projected_ranges,
        geometry_range=geometry_range,
        rendered_range=rendered_range,
        source_clip_bounds=source_clip_bounds,
        needs_rendered_range=needs_rendered_range,
        range_source=range_source,
        debug=debug,
    )
    debug.add(
        "final",
        source=range_source,
        event_range=event_range,
        clip_rect=clip_rect,
        has_projected_range=plan.has_projected_range,
    )
    if debug.auto_dump:
        debug.dump()
    return plan


def _clip_rect_from_range(
    expanded_rect: Rect,
    event_range: ProjectedRange,
    direction: GradientDirection,
) -> Rect:
    clip_tx1, clip_ty1, clip_tx2, clip_ty2 = expanded_rect
    g_min, g_max, p_min, p_max = event_range
    del g_min, g_max
    if direction.is_horizontal:
        clip_ty1, clip_ty2 = p_min, p_max
    elif direction.is_vertical:
        x_from_p1 = -p_min * direction.sin_a
        x_from_p2 = -p_max * direction.sin_a
        clip_tx1, clip_tx2 = min(x_from_p1, x_from_p2), max(x_from_p1, x_from_p2)
    return clip_tx1, clip_ty1, clip_tx2, clip_ty2


def compute_dynamic_geometry_bounds(
    event: ASSEvent,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
    parsed: dict,
    bx1: float,
    by1: float,
    bx2: float,
    by2: float,
    meta_pos_x: float,
    meta_pos_y: float,
    render_x: float,
    render_y: float,
    org_x: float,
    org_y: float,
) -> Rect:
    """Return the union of text bounds after geometry-changing tag gradients."""

    base_fscx = numeric_tag_base("fscx", parsed, style, 100.0)
    base_fscy = numeric_tag_base("fscy", parsed, style, 100.0)
    base_fs = numeric_tag_base("fs", parsed, style, style.fontsize if style else 48.0)
    base_fsp = numeric_tag_base("fsp", parsed, style, style.spacing if style else 0.0)
    base_fax = numeric_tag_base("fax", parsed, style, 0.0)
    base_fay = numeric_tag_base("fay", parsed, style, 0.0)
    base_frx = numeric_tag_base("frx", parsed, style, 0.0)
    base_fry = numeric_tag_base("fry", parsed, style, 0.0)
    base_frz = numeric_tag_base("frz", parsed, style, style.angle if style else 0.0)

    base_w = max(bx2 - bx1, 1.0)
    base_h = max(by2 - by1, 1.0)
    char_count = visible_char_count(event.text)
    corners = [(bx1, by1), (bx2, by1), (bx2, by2), (bx1, by2)]

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    for t in geometry_sample_points(enabled_tags):
        fscx = numeric_tag_at_t("fscx", t, parsed, style, settings, enabled_tags, base_fscx)
        fscy = numeric_tag_at_t("fscy", t, parsed, style, settings, enabled_tags, base_fscy)
        fs = numeric_tag_at_t("fs", t, parsed, style, settings, enabled_tags, base_fs)
        fsp = numeric_tag_at_t("fsp", t, parsed, style, settings, enabled_tags, base_fsp)
        fax = numeric_tag_at_t("fax", t, parsed, style, settings, enabled_tags, base_fax) - base_fax
        fay = numeric_tag_at_t("fay", t, parsed, style, settings, enabled_tags, base_fay) - base_fay
        frx = numeric_tag_at_t("frx", t, parsed, style, settings, enabled_tags, base_frx) - base_frx
        fry = numeric_tag_at_t("fry", t, parsed, style, settings, enabled_tags, base_fry) - base_fry
        frz = numeric_tag_at_t("frz", t, parsed, style, settings, enabled_tags, base_frz) - base_frz

        scale_x = safe_ratio(fscx, base_fscx) * safe_ratio(fs, base_fs)
        scale_y = safe_ratio(fscy, base_fscy) * safe_ratio(fs, base_fs)

        if char_count > 1 and fsp != base_fsp:
            spacing_delta = (fsp - base_fsp) * (char_count - 1)
            scale_x *= max((base_w + spacing_delta) / base_w, 0.02)

        angle = math.radians(frz)
        cos_r = math.cos(angle)
        sin_r = math.sin(angle)

        transformed = [
            transform_geometry_point(
                x, y, meta_pos_x, meta_pos_y, render_x, render_y,
                org_x, org_y, scale_x, scale_y, fax, fay, cos_r, sin_r,
            )
            for x, y in corners
        ]

        pad_x = abs(math.sin(math.radians(fry))) * base_h * max(abs(scale_y), 1.0) * 0.75
        pad_y = abs(math.sin(math.radians(frx))) * base_w * max(abs(scale_x), 1.0) * 0.75

        for x, y in transformed:
            min_x = min(min_x, x - pad_x)
            min_y = min(min_y, y - pad_y)
            max_x = max(max_x, x + pad_x)
            max_y = max(max_y, y + pad_y)

    if not math.isfinite(min_x) or not math.isfinite(min_y):
        return bx1, by1, bx2, by2

    antialias_pad = 2.0
    return (
        min_x - antialias_pad,
        min_y - antialias_pad,
        max_x + antialias_pad,
        max_y + antialias_pad,
    )


def build_geometry_context(
    event: ASSEvent,
    style: Optional[ASSStyle],
    parsed: dict,
    bx1: float,
    by1: float,
    bx2: float,
    by2: float,
    meta_pos_x: float,
    meta_pos_y: float,
    render_x: float,
    render_y: float,
    org_x: float,
    org_y: float,
) -> dict[str, float]:
    base_fs = numeric_tag_base("fs", parsed, style, style.fontsize if style else 48.0)
    return {
        "bx1": bx1,
        "by1": by1,
        "bx2": bx2,
        "by2": by2,
        "meta_pos_x": meta_pos_x,
        "meta_pos_y": meta_pos_y,
        "render_x": render_x,
        "render_y": render_y,
        "org_x": org_x,
        "org_y": org_y,
        "base_w": max(bx2 - bx1, 1.0),
        "base_h": max(by2 - by1, 1.0),
        "char_count": float(visible_char_count(event.text)),
        "base_fscx": numeric_tag_base("fscx", parsed, style, 100.0),
        "base_fscy": numeric_tag_base("fscy", parsed, style, 100.0),
        "base_fs": base_fs,
        "base_fsp": numeric_tag_base("fsp", parsed, style, style.spacing if style else 0.0),
        "base_fax": numeric_tag_base("fax", parsed, style, 0.0),
        "base_fay": numeric_tag_base("fay", parsed, style, 0.0),
        "base_frz": numeric_tag_base("frz", parsed, style, style.angle if style else 0.0),
        "base_frx": numeric_tag_base("frx", parsed, style, 0.0),
        "base_fry": numeric_tag_base("fry", parsed, style, 0.0),
    }


def transform_geometry_points(
    points: list[tuple[float, float]],
    t: float,
    context: dict[str, float],
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
) -> list[tuple[float, float]]:
    scale_x, scale_y, fax, fay, cos_r, sin_r = geometry_transform_values(
        t, context, parsed, style, settings, enabled_tags
    )
    return [
        transform_geometry_point(
            x, y,
            context["meta_pos_x"], context["meta_pos_y"],
            context["render_x"], context["render_y"],
            context["org_x"], context["org_y"],
            scale_x, scale_y, fax, fay, cos_r, sin_r,
        )
        for x, y in points
    ]


def compute_geometry_projected_range(
    event: ASSEvent,
    ass_file: Optional[ASSFile],
    base_bounds: Rect,
    context: dict[str, float],
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
    cos_a: float,
    sin_a: float,
) -> Optional[ProjectedRange]:
    """Return directional gradient-axis bounds for geometry tag gradients."""
    del event, ass_file

    bx1, by1, bx2, by2 = base_bounds
    corners = [(bx1, by1), (bx2, by1), (bx2, by2), (bx1, by2)]
    base_w = max(bx2 - bx1, 1.0)
    base_h = max(by2 - by1, 1.0)

    def project_at(t: float) -> ProjectedRange:
        points = transform_geometry_points(
            corners, t, context, parsed, style, settings, enabled_tags
        )
        g_vals = [x * cos_a + y * sin_a for x, y in points]
        p_vals = [-x * sin_a + y * cos_a for x, y in points]

        scale_x, scale_y, _, _, _, _ = geometry_transform_values(
            t, context, parsed, style, settings, enabled_tags
        )
        frx = (
            numeric_tag_at_t("frx", t, parsed, style, settings, enabled_tags, context["base_frx"])
            - context["base_frx"]
        )
        fry = (
            numeric_tag_at_t("fry", t, parsed, style, settings, enabled_tags, context["base_fry"])
            - context["base_fry"]
        )
        pad_x = abs(math.sin(math.radians(fry))) * base_h * max(abs(scale_y), 1.0) * 0.75
        pad_y = abs(math.sin(math.radians(frx))) * base_w * max(abs(scale_x), 1.0) * 0.75
        g_pad = abs(cos_a) * pad_x + abs(sin_a) * pad_y
        p_pad = abs(sin_a) * pad_x + abs(cos_a) * pad_y

        return (
            min(g_vals) - g_pad,
            max(g_vals) + g_pad,
            min(p_vals) - p_pad,
            max(p_vals) + p_pad,
        )

    g_min = float("inf")
    g_max = float("-inf")
    p_min = float("inf")
    p_max = float("-inf")
    for t in geometry_sample_points(enabled_tags):
        sample_g_min, sample_g_max, sample_p_min, sample_p_max = project_at(t)
        g_min = min(g_min, sample_g_min)
        g_max = max(g_max, sample_g_max)
        p_min = min(p_min, sample_p_min)
        p_max = max(p_max, sample_p_max)

    if not all(math.isfinite(v) for v in (g_min, g_max, p_min, p_max)):
        return None

    antialias_pad = 2.0
    if g_max - g_min < 1e-6:
        g_max = g_min + 1.0
    if p_max - p_min < 1e-6:
        p_max = p_min + 1.0

    return (
        g_min - antialias_pad,
        g_max + antialias_pad,
        p_min - antialias_pad,
        p_max + antialias_pad,
    )


def compute_libass_projected_range(
    event: ASSEvent,
    ass_file: ASSFile,
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
    cos_a: float,
    sin_a: float,
) -> Optional[ProjectedRange]:
    image_types = measurement_image_types(enabled_tags)

    def project_bounds_at(t: float) -> Optional[ProjectedRange]:
        measured_event = event_with_interpolated_geometry(
            event, parsed, style, settings, enabled_tags, t
        )
        bounds = measure_event_bounds(ass_file, measured_event, image_types=image_types)
        if bounds is None:
            return None

        corners = [
            (bounds.x1, bounds.y1),
            (bounds.x2, bounds.y1),
            (bounds.x2, bounds.y2),
            (bounds.x1, bounds.y2),
        ]
        g_vals = [x * cos_a + y * sin_a for x, y in corners]
        p_vals = [-x * sin_a + y * cos_a for x, y in corners]
        return min(g_vals), max(g_vals), min(p_vals), max(p_vals)

    try:
        start = project_bounds_at(0.0)
        end = project_bounds_at(1.0)
        if start is None or end is None:
            return None

        g_min = min(start[0], start[1], end[0], end[1])
        g_max = max(start[0], start[1], end[0], end[1])
        p_min = min(start[2], end[2])
        p_max = max(start[3], end[3])
        for t in render_measure_sample_points(enabled_tags):
            measured = project_bounds_at(t)
            if measured is None:
                continue
            g_min = min(g_min, measured[0])
            g_max = max(g_max, measured[1])
            p_min = min(p_min, measured[2])
            p_max = max(p_max, measured[3])
        if not all(math.isfinite(v) for v in (g_min, g_max, p_min, p_max)):
            return None
        if g_max - g_min < 1e-6:
            g_max = g_min + 1.0
        if p_max - p_min < 1e-6:
            p_max = p_min + 1.0
        return g_min, g_max, p_min, p_max
    except (LibassBoundsError, OSError, ValueError):
        return None


def refine_libass_axis_range_with_strips(
    event: ASSEvent,
    ass_file: ASSFile,
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
    g_min: float,
    g_max: float,
    p_min: float,
    p_max: float,
    cos_a: float,
    sin_a: float,
) -> Optional[tuple[float, float]]:
    is_horiz = abs(sin_a) < 0.01 and cos_a > 0
    is_vert = abs(cos_a) < 0.01 and abs(sin_a) > 0.99
    if not (is_horiz or is_vert):
        return None

    step = max(settings.step, 0.05)
    span = g_max - g_min
    if span <= step:
        return None

    strip_count = max(1, int(math.ceil(span / step)))
    last_idx = strip_count - 1
    cache: dict[int, bool] = {}
    image_types = measurement_image_types(enabled_tags)

    if is_vert:
        x_from_p1 = -p_min * sin_a
        x_from_p2 = -p_max * sin_a
        clip_x1, clip_x2 = min(x_from_p1, x_from_p2), max(x_from_p1, x_from_p2)
    else:
        clip_x1 = clip_x2 = 0.0

    def strip_visible(idx: int) -> bool:
        idx = max(0, min(last_idx, idx))
        if idx in cache:
            return cache[idx]

        j = g_min + idx * step
        strip_g1 = min(j + step, g_max)
        t = (j - g_min) / max(g_max - g_min, 1.0)
        measured_event = event_with_interpolated_geometry(
            event, parsed, style, settings, enabled_tags, t
        )
        if is_horiz:
            clip_str = f"\\clip({j:.1f},{p_min:.1f},{strip_g1:.1f},{p_max:.1f})"
        else:
            clip_str = f"\\clip({clip_x1:.1f},{j:.1f},{clip_x2:.1f},{strip_g1:.1f})"
        measured_event = replace(
            measured_event,
            text=insert_tags(measured_event.text, clip_str),
        )

        try:
            cache[idx] = measure_event_bounds(
                ass_file, measured_event, image_types=image_types
            ) is not None
        except (LibassBoundsError, OSError, ValueError):
            cache[idx] = False
        return cache[idx]

    first_idx = 0
    if not strip_visible(0):
        offset = 1
        first_visible: Optional[int] = None
        while offset <= last_idx:
            if strip_visible(offset):
                first_visible = offset
                break
            offset *= 2

        if first_visible is None:
            if strip_visible(last_idx):
                first_visible = last_idx
            else:
                return None

        lo = max(0, offset // 2)
        hi = first_visible
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if strip_visible(mid):
                hi = mid
            else:
                lo = mid
        first_idx = hi

    last_visible_idx = last_idx
    if not strip_visible(last_idx):
        offset = 1
        found_visible: Optional[int] = None
        while last_idx - offset >= first_idx:
            idx = last_idx - offset
            if strip_visible(idx):
                found_visible = idx
                break
            offset *= 2

        if found_visible is None:
            found_visible = first_idx

        lo = found_visible
        hi = last_idx
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if strip_visible(mid):
                lo = mid
            else:
                hi = mid
        last_visible_idx = lo

    refined_min = g_min + first_idx * step
    refined_max = min(g_min + (last_visible_idx + 1) * step, g_max)
    if refined_max <= refined_min:
        return None
    if abs(refined_min - g_min) < 0.01 and abs(refined_max - g_max) < 0.01:
        return None
    return refined_min, refined_max


def measurement_image_types(enabled_tags: dict[str, Any]) -> tuple[int, ...]:
    image_types = {IMAGE_TYPE_CHARACTER}
    if any(t in enabled_tags for t in ("alpha", "3c", "3a", "bord", "xbord", "ybord")):
        image_types.add(IMAGE_TYPE_OUTLINE)
    if any(t in enabled_tags for t in ("alpha", "4c", "4a", "shad", "xshad", "yshad")):
        image_types.add(IMAGE_TYPE_SHADOW)
    return tuple(sorted(image_types))


def render_measure_sample_points(enabled_tags: dict[str, Any]) -> list[float]:
    points = {0.0, 1.0}
    for i in range(1, 8):
        points.add(i / 8.0)
    for cfg in enabled_tags.values():
        if cfg and getattr(cfg, "nodes", None):
            for node in cfg.nodes:
                points.add(max(0.0, min(1.0, node.x / 100.0)))
        if cfg and getattr(cfg, "coord_y_nodes", None):
            for node in cfg.coord_y_nodes:
                points.add(max(0.0, min(1.0, node.x / 100.0)))
    return sorted(points)


def coord_sample_points(cfg: Any) -> list[float]:
    points = {0.0, 1.0}
    for i in range(41):
        points.add(i / 40.0)
    for node in getattr(cfg, "nodes", []) or []:
        points.add(max(0.0, min(1.0, node.x / 100.0)))
    for node in getattr(cfg, "coord_y_nodes", []) or []:
        points.add(max(0.0, min(1.0, node.x / 100.0)))
    return sorted(points)


def event_with_interpolated_geometry(
    event: ASSEvent,
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
    t: float,
) -> ASSEvent:
    text = re.sub(r"\\i?clip\([^)]*\)", "", event.text)
    tag_str = ""
    for tag_name in tags_to_remove_for_overrides(enabled_tags):
        text = remove_specific_tag(text, tag_name)
    for tag_name, cfg in enabled_tags.items():
        if tag_name in SHADOW_TAGS:
            continue
        value = get_interpolated_value(tag_name, cfg, t, parsed, style, settings)
        tag_str += build_tag_string(tag_name, value)
    tag_str += build_shadow_override_tags(
        enabled_tags, parsed, style, settings, lambda _tag: t
    )

    if tag_str:
        text = insert_tags(text, tag_str)
    return replace(event, text=text)


def geometry_transform_values(
    t: float,
    context: dict[str, float],
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
) -> tuple[float, float, float, float, float, float]:
    fscx = numeric_tag_at_t("fscx", t, parsed, style, settings, enabled_tags, context["base_fscx"])
    fscy = numeric_tag_at_t("fscy", t, parsed, style, settings, enabled_tags, context["base_fscy"])
    fs = numeric_tag_at_t("fs", t, parsed, style, settings, enabled_tags, context["base_fs"])
    fsp = numeric_tag_at_t("fsp", t, parsed, style, settings, enabled_tags, context["base_fsp"])
    fax = numeric_tag_at_t("fax", t, parsed, style, settings, enabled_tags, context["base_fax"]) - context["base_fax"]
    fay = numeric_tag_at_t("fay", t, parsed, style, settings, enabled_tags, context["base_fay"]) - context["base_fay"]
    frz = numeric_tag_at_t("frz", t, parsed, style, settings, enabled_tags, context["base_frz"]) - context["base_frz"]

    scale_x = safe_ratio(fscx, context["base_fscx"]) * safe_ratio(fs, context["base_fs"])
    scale_y = safe_ratio(fscy, context["base_fscy"]) * safe_ratio(fs, context["base_fs"])

    if context["char_count"] > 1 and fsp != context["base_fsp"]:
        spacing_delta = (fsp - context["base_fsp"]) * (context["char_count"] - 1)
        scale_x *= max((context["base_w"] + spacing_delta) / context["base_w"], 0.02)

    angle = math.radians(frz)
    return scale_x, scale_y, fax, fay, math.cos(angle), math.sin(angle)


def transform_geometry_point(
    x: float,
    y: float,
    meta_pos_x: float,
    meta_pos_y: float,
    render_x: float,
    render_y: float,
    org_x: float,
    org_y: float,
    scale_x: float,
    scale_y: float,
    fax: float,
    fay: float,
    cos_r: float,
    sin_r: float,
) -> tuple[float, float]:
    dx = x - meta_pos_x
    dy = y - meta_pos_y

    sx = render_x + dx * scale_x
    sy = render_y + dy * scale_y

    shx = sx + fax * (sy - render_y)
    shy = sy + fay * (sx - render_x)

    ox = shx - org_x
    oy = shy - org_y
    return org_x + ox * cos_r - oy * sin_r, org_y + ox * sin_r + oy * cos_r


def geometry_sample_points(enabled_tags: dict[str, Any]) -> list[float]:
    if not any(tag in enabled_tags for tag in GEOMETRY_TAGS):
        return [0.0, 1.0]

    points = {0.0, 1.0}
    for i in range(41):
        points.add(i / 40.0)
    for tag in GEOMETRY_TAGS:
        cfg = enabled_tags.get(tag)
        if cfg and getattr(cfg, "nodes", None):
            for node in cfg.nodes:
                points.add(max(0.0, min(1.0, node.x / 100.0)))
    return sorted(points)


def numeric_tag_base(
    tag_name: str,
    parsed: dict,
    style: Optional[ASSStyle],
    fallback: float,
) -> float:
    try:
        return float(get_tag_value(tag_name, parsed, style))
    except (TypeError, ValueError):
        return float(fallback)


def numeric_tag_at_t(
    tag_name: str,
    t: float,
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    enabled_tags: dict[str, Any],
    fallback: float,
) -> float:
    cfg = enabled_tags.get(tag_name)
    if cfg and getattr(cfg, "nodes", None):
        try:
            return float(get_interpolated_value(tag_name, cfg, t, parsed, style, settings))
        except (TypeError, ValueError):
            return float(fallback)
    return float(fallback)


def safe_ratio(value: float, base: float) -> float:
    if abs(base) < 1e-6:
        return 1.0
    return value / base


def visible_char_count(text: str) -> int:
    plain = strip_tags(text)
    plain = plain.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return sum(1 for ch in plain if ch != "\n")


def tags_to_remove_for_overrides(enabled_tags: dict[str, Any]) -> set[str]:
    tags = set(enabled_tags)
    if tags.intersection(SHADOW_TAGS):
        tags.update(SHADOW_TAGS)
    return tags


def base_shadow_offsets(parsed: dict, style: Optional[ASSStyle]) -> tuple[float, float]:
    style_shad = style.shadow if style else 0.0
    shad = parsed.get("shad")
    xshad = parsed.get("xshad", shad if shad is not None else style_shad)
    yshad = parsed.get("yshad", shad if shad is not None else style_shad)
    return float(xshad), float(yshad)


def build_shadow_override_tags(
    enabled_tags: dict[str, Any],
    parsed: dict,
    style: Optional[ASSStyle],
    settings: Any,
    t_for_tag,
) -> str:
    if not any(tag in enabled_tags for tag in SHADOW_TAGS):
        return ""

    xshad, yshad = base_shadow_offsets(parsed, style)
    shad_cfg = enabled_tags.get("shad")
    if shad_cfg:
        shad_val = get_interpolated_value(
            "shad", shad_cfg, t_for_tag("shad"), parsed, style, settings
        )
        xshad = yshad = float(shad_val)

    x_cfg = enabled_tags.get("xshad")
    if x_cfg:
        xshad = float(get_interpolated_value(
            "xshad", x_cfg, t_for_tag("xshad"), parsed, style, settings
        ))

    y_cfg = enabled_tags.get("yshad")
    if y_cfg:
        yshad = float(get_interpolated_value(
            "yshad", y_cfg, t_for_tag("yshad"), parsed, style, settings
        ))

    return build_tag_string("xshad", xshad) + build_tag_string("yshad", yshad)


def get_interpolated_value(
    tag_name: str,
    cfg: Any,
    t: float,
    parsed_tags: dict,
    style: Optional[ASSStyle],
    settings: Any,
) -> object:
    """Get the interpolated value for a tag at position t."""
    info = TAG_INFO.get(tag_name, {})
    tag_type = info.get("type", "numeric")

    if not getattr(cfg, "nodes", None):
        return get_tag_value(tag_name, parsed_tags, style)

    x_pos = t * 100.0

    if tag_type == "color":
        return interpolate(
            cfg.nodes,
            x_pos,
            cfg.mode,
            is_color=True,
            color_space=cfg.color_space,
        )
    if tag_type == "alpha":
        y_val = interpolate(cfg.nodes, x_pos, cfg.mode, is_color=False)
        a = max(0, min(255, int(y_val + 0.5)))
        return f"{a:02X}"
    if tag_type == "text":
        nodes = sorted(cfg.nodes, key=lambda node: node.x)
        chosen = nodes[0]
        for node in nodes:
            if x_pos >= node.x:
                chosen = node
            else:
                break
        return chosen.value_str or get_tag_value(tag_name, parsed_tags, style)
    if tag_type == "coord":
        base = get_tag_value(tag_name, parsed_tags, style)
        if isinstance(base, tuple) and len(base) >= 2:
            base_x, base_y = float(base[0]), float(base[1])
        else:
            base_x, base_y = 0.0, 0.0

        x_nodes = cfg.nodes or make_default_nodes(start_y=base_x, end_y=base_x)
        y_nodes = cfg.coord_y_nodes or make_default_nodes(start_y=base_y, end_y=base_y)
        x_val = interpolate(x_nodes, x_pos, cfg.mode, is_color=False)
        y_val = interpolate(y_nodes, x_pos, cfg.coord_y_mode, is_color=False)
        return float(x_val), float(y_val)

    return interpolate(cfg.nodes, x_pos, cfg.mode, is_color=False)


def insert_tags(text: str, tags: str) -> str:
    """Insert override tags into the first override block of text."""
    if text.startswith("{"):
        idx = text.index("}")
        return text[:idx] + tags + text[idx:]
    return "{" + tags + "}" + text
