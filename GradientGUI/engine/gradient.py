"""
Gradient calculation and strip generation.

Generates ASS dialogue lines with gradient tag overrides and clip masks.
Supports Horizontal, Vertical, Angled, and GBC (Gradient-by-Character) modes.
"""

from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Optional

from .ass_parser import ASSEvent, ASSStyle, ASSFile
from .tag_parser import (
    parse_tags_from_text, get_tag_value, strip_tags,
    build_tag_string, remove_specific_tag,
)
from .frame_sampler import FrameSampler
from .path_tracer import get_color_by_ratio, project_bounds
from .path_sampling_cache import (
    frame_key_from_video_position,
    get_cached_path_color_map,
)
from .vector_clip import extract_source_vector_clip, vector_clip_tag_for_strip
from .tag_parser import extract_clip_bounds, extract_clip_tags, get_bord_sizes, get_shad_offsets
from .models import (
    ChannelSamplingData,
    GradientMode,
    GradientSettings,
    TagGradientConfig,
)
from .path_model import PathSet
from .range_calc import (
    GEOMETRY_TAGS,
    SHADOW_TAGS,
    build_geometry_context as _range_build_geometry_context,
    build_shadow_override_tags as _build_shadow_override_tags,
    calculate_range_plan,
    coord_sample_points as _range_coord_sample_points,
    compute_dynamic_geometry_bounds as _range_compute_dynamic_geometry_bounds,
    get_interpolated_value as _range_get_interpolated_value,
    get_interpolated_value as _get_interpolated_value,
    insert_tags as _insert_tags,
    tags_to_remove_for_overrides as _tags_to_remove_for_overrides,
)

# Global sampler instance to cache the current frame
_global_sampler = FrameSampler()
_COLOR_TAGS = {"1c", "2c", "3c", "4c"}
_MOVE_RE = re.compile(r"\\move\s*\(", re.IGNORECASE)


def _default_position_from_style(
    event: ASSEvent,
    style: Optional[ASSStyle],
    ass_file: Optional[ASSFile],
    alignment: int,
) -> Optional[tuple[float, float]]:
    if ass_file is None:
        return None
    res_x = float(ass_file.play_res_x or 1920)
    res_y = float(ass_file.play_res_y or 1080)
    ml = event.margin_l or (style.margin_l if style else 0)
    mr = event.margin_r or (style.margin_r if style else 0)
    mv = event.margin_v or (style.margin_v if style else 0)

    halign = (alignment - 1) % 3
    valign = (alignment - 1) // 3
    if halign == 0:
        pos_x = float(ml)
    elif halign == 1:
        pos_x = (res_x - float(mr) + float(ml)) / 2.0
    else:
        pos_x = res_x - float(mr)

    if valign == 0:
        pos_y = res_y - float(mv)
    elif valign == 1:
        pos_y = res_y / 2.0
    else:
        pos_y = float(mv)
    return pos_x, pos_y


def _position_from_bounds_meta(base_bounds_meta: Optional[str]) -> Optional[tuple[float, float]]:
    if not base_bounds_meta:
        return None
    try:
        parts = base_bounds_meta.split(",")
        return float(parts[5]), float(parts[6])
    except (IndexError, TypeError, ValueError):
        return None


def _implicit_static_position(
    event: ASSEvent,
    style: Optional[ASSStyle],
    ass_file: Optional[ASSFile],
    parsed: dict,
    base_bounds_meta: Optional[str],
) -> Optional[tuple[float, float]]:
    if parsed.get("pos") or _MOVE_RE.search(event.text or ""):
        return None
    meta_pos = _position_from_bounds_meta(base_bounds_meta)
    if meta_pos is not None:
        return meta_pos
    alignment = int(parsed.get("an") or (style.alignment if style else 2) or 2)
    return _default_position_from_style(event, style, ass_file, alignment)


def _sampling_frame_key_for_tag(
    settings: GradientSettings,
    tag_name: str,
):
    frame_map = getattr(settings, "sampling_path_frames", {}) or {}
    if settings.video_path and tag_name in frame_map:
        try:
            return ("frame", settings.video_path, int(frame_map[tag_name]))
        except (TypeError, ValueError):
            pass
    return frame_key_from_video_position(
        settings.video_path,
        settings.video_frame,
        settings.video_time,
    )


def _load_sampling_frame_for_key(
    settings: GradientSettings,
    frame_key,
) -> bool:
    if not settings.video_path or frame_key is None:
        return False
    kind = frame_key[0] if isinstance(frame_key, tuple) and frame_key else None
    frame_value = frame_key[2] if isinstance(frame_key, tuple) and len(frame_key) > 2 else None
    if kind == "frame":
        return _global_sampler.load_frame_number(settings.video_path, int(frame_value))
    if kind == "time":
        return _global_sampler.load_frame(settings.video_path, float(frame_value))
    return False


class GradientTagError(RuntimeError):
    """Generation failure with the tag/strip that triggered it."""

    def __init__(
        self,
        tag: str,
        *,
        value: object = None,
        clip: str | None = None,
        strip_index: int | None = None,
        position: float | None = None,
        original: Exception | None = None,
    ):
        self.tag = tag
        self.value = value
        self.clip = clip
        self.strip_index = strip_index
        self.position = position
        self.original = original
        message = f"tag \\{tag} 生成失败"
        if strip_index is not None:
            message += f" (strip {strip_index})"
        if original:
            message += f": {original}"
        super().__init__(message)


# ── Gradient settings helpers ────────────────────────────────────────────────


def _path_sampling_smooth_for(settings: GradientSettings, tag_name: str) -> bool:
    smooth = settings.path_sampling_smooth
    if isinstance(smooth, dict):
        return bool(smooth.get(tag_name, False))
    return bool(smooth)


def _path_sampling_smooth_strength_for(settings: GradientSettings, tag_name: str) -> float:
    strength = getattr(settings, "path_sampling_smooth_strength", {})
    if isinstance(strength, dict):
        strength = strength.get(tag_name, 1.0)
    try:
        return max(0.0, min(1.0, float(strength)))
    except (TypeError, ValueError):
        return 1.0


def _path_sampling_mirrored_for(settings: GradientSettings, tag_name: str) -> bool:
    mirror = settings.path_sampling_mirror
    if isinstance(mirror, dict):
        return bool(mirror.get(tag_name, False))
    return bool(mirror)


def _saved_path_sampling_for_tag(
    settings: GradientSettings,
    tag_name: str,
    cos_a: float = 1.0,
    sin_a: float = 0.0,
):
    sample_map = getattr(settings, "sampling_path_samples", {}) or {}
    raw = sample_map.get(tag_name)
    if not raw:
        return None
    try:
        path_set = PathSet.from_raw(raw)
    except Exception:
        return None
    color_map, keys = path_set.sampled_color_result(cos_a, sin_a)
    if not keys:
        return None
    return color_map, keys


def _color_shift_for(settings: GradientSettings, tag_name: Optional[str] = None) -> float:
    shift_map = getattr(settings, "color_shift_steps_by_tag", {}) or {}
    if tag_name and isinstance(shift_map, dict) and tag_name in shift_map:
        raw_shift = shift_map.get(tag_name, 0.0)
    else:
        raw_shift = getattr(settings, "color_shift_steps", 0.0)
    try:
        return float(raw_shift or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _color_shifted_t(
    t: float,
    settings: GradientSettings,
    shift_divisor: float,
    tag_name: Optional[str] = None,
) -> float:
    """Apply cyclic color-band phase shift measured in generated strip cells."""

    shift = _color_shift_for(settings, tag_name)
    t = max(0.0, min(1.0, float(t)))
    if abs(shift) < 1e-9:
        return t
    divisor = max(float(shift_divisor), 1.0)
    shifted = t - shift / divisor
    return shifted - math.floor(shifted)


def _color_shifted_strip_t(
    t: float,
    settings: GradientSettings,
    strip_index: int,
    strip_slots: int,
    *,
    include_endpoint: bool,
    force_discrete: bool = False,
    tag_name: Optional[str] = None,
) -> float:
    """Shift color t by discrete generated strip positions."""

    shift = _color_shift_for(settings, tag_name)
    slots = max(1, int(strip_slots))
    if abs(shift) < 1e-9:
        if not force_discrete:
            return max(0.0, min(1.0, float(t)))
        divisor = slots - 1 if include_endpoint and slots > 1 else slots
        clamped_index = max(0.0, min(float(strip_index), float(divisor)))
        return max(0.0, min(1.0, clamped_index / max(float(divisor), 1.0)))

    shifted_index = (float(strip_index) - shift) % float(slots)
    divisor = slots - 1 if include_endpoint and slots > 1 else slots
    shifted_t = shifted_index / max(float(divisor), 1.0)
    return max(0.0, min(1.0, shifted_t))


# ── Core gradient generation ─────────────────────────────────────────────────

def generate_gradient(
    event: ASSEvent,
    style: Optional[ASSStyle],
    settings: GradientSettings,
    base_bounds_meta: Optional[str] = None,
    ass_file: Optional[ASSFile] = None,
) -> list[ASSEvent]:
    """
    Generate gradient ASS lines from a source event.

    Returns a list of ASSEvent lines with gradient tag overrides and clips.
    """
    enabled_tags = {
        name: cfg for name, cfg in settings.tags.items() if cfg.enabled
    }
    if not enabled_tags:
        return [event]

    if settings.mode == GradientMode.GBC:
        return _generate_gbc(event, style, settings, enabled_tags, base_bounds_meta)
    else:
        return _generate_strips(event, style, settings, enabled_tags, base_bounds_meta, ass_file)


# ── Strip-based gradient (H / V / Angled) ────────────────────────────────────

def _generate_strips(
    event: ASSEvent,
    style: Optional[ASSStyle],
    settings: GradientSettings,
    enabled_tags: dict[str, TagGradientConfig],
    base_bounds_meta: Optional[str] = None,
    ass_file: Optional[ASSFile] = None,
) -> list[ASSEvent]:
    """Generate gradient using clip strips."""

    # Base text bounds (Fallback to settings)
    tx1, ty1 = settings.text_x1, settings.text_y1
    tx2, ty2 = settings.text_x2, settings.text_y2

    parsed = parse_tags_from_text(event.text) or {}
    source_clip_bounds = extract_clip_bounds(event.text)
    source_vector_clip = extract_source_vector_clip(event.text)
    implicit_pos = _implicit_static_position(
        event, style, ass_file, parsed, base_bounds_meta
    )
    if implicit_pos is not None:
        parsed = dict(parsed)
        parsed["pos"] = implicit_pos

    # ── Use libass-rendered pixel bounds from the Python launcher ────────
    # The rectangle is the tight alpha-bitmap bounds for fill/character
    # images, so gradient strips match the actual visible glyph area.
    actual_pos = parsed.get('pos')
    geom_context = None

    if base_bounds_meta:
        try:
            parts = base_bounds_meta.split(",")
            bx1, by1, bx2, by2 = float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])
            meta_pos_x, meta_pos_y = float(parts[5]), float(parts[6])
            if actual_pos:
                render_x, render_y = actual_pos
            else:
                render_x, render_y = meta_pos_x, meta_pos_y

            meta_org_x = float(parts[7]) if len(parts) > 7 else meta_pos_x
            meta_org_y = float(parts[8]) if len(parts) > 8 else meta_pos_y
            geom_context = _range_build_geometry_context(
                event, style, parsed,
                bx1, by1, bx2, by2,
                meta_pos_x, meta_pos_y,
                render_x, render_y,
                meta_org_x, meta_org_y,
            )

            if any(t in enabled_tags for t in GEOMETRY_TAGS):
                nx1, ny1, nx2, ny2 = _range_compute_dynamic_geometry_bounds(
                    event, style, settings, enabled_tags, parsed,
                    bx1, by1, bx2, by2,
                    meta_pos_x, meta_pos_y, render_x, render_y,
                    meta_org_x, meta_org_y,
                )
            else:
                nx1, ny1, nx2, ny2 = bx1, by1, bx2, by2

            # Try to account for position animation
            def _get_pos_range():
                cfg = enabled_tags.get("pos")
                if cfg and cfg.nodes:
                    xs: list[float] = []
                    ys: list[float] = []
                    for t in _range_coord_sample_points(cfg):
                        value = _range_get_interpolated_value(
                            "pos", cfg, t, parsed, style, settings
                        )
                        if isinstance(value, tuple) and len(value) >= 2:
                            xs.append(float(value[0]))
                            ys.append(float(value[1]))
                    if xs and ys:
                        return min(xs), max(xs), min(ys), max(ys)
                return render_x, render_x, render_y, render_y
            
            min_px, max_px, min_py, max_py = _get_pos_range()
            dx_min, dx_max = min_px - render_x, max_px - render_x
            dy_min, dy_max = min_py - render_y, max_py - render_y

            # Final dynamic base bounds
            tx1 = nx1 + dx_min
            tx2 = nx2 + dx_max
            ty1 = ny1 + dy_min
            ty2 = ny2 + dy_max

        except Exception:
            pass



    range_plan = calculate_range_plan(
        event=event,
        ass_file=ass_file,
        parsed=parsed,
        style=style,
        settings=settings,
        enabled_tags=enabled_tags,
        base_rect=(tx1, ty1, tx2, ty2),
        source_clip_bounds=source_clip_bounds,
        geom_context=geom_context,
    )
    direction = range_plan.direction
    eff_angle = direction.effective_angle
    cos_a = direction.cos_a
    sin_a = direction.sin_a
    is_horiz = direction.is_horizontal
    is_vert = direction.is_vertical
    is_axis_aligned = direction.is_axis_aligned
    tx1, ty1, tx2, ty2 = range_plan.base_rect
    g_min, g_max, p_min, p_max = range_plan.event_projected_range
    clip_tx1, clip_ty1, clip_tx2, clip_ty2 = range_plan.clip_rect
    group_projected_ranges = range_plan.group_projected_ranges
    step = max(settings.step, 0.05)

    def _tag_t_for(tag_name: str, axis_pos: float) -> float:
        return range_plan.t_for_tag(tag_name, axis_pos)

    # Check for xlip custom tags
    clean_text, clips = extract_clip_tags(event.text)
    if settings.sampling_paths:
        for tag, path in settings.sampling_paths.items():
            if path:
                clips[tag] = path
            else:
                clips.pop(tag, None)
    sampling_data: dict[str, ChannelSamplingData] = {}
    
    if clips and settings.video_path:
        xbord, ybord = get_bord_sizes(clean_text, style)
        xshad, yshad = get_shad_offsets(clean_text, style)

        for tag_name in enabled_tags:
            if tag_name not in _COLOR_TAGS:
                continue
            path_str = clips.get(tag_name)
            if not path_str:
                continue

            saved_sampling = _saved_path_sampling_for_tag(settings, tag_name, cos_a, sin_a)
            if saved_sampling is not None:
                cmap, keys = saved_sampling
            else:
                frame_cache_key = _sampling_frame_key_for_tag(settings, tag_name)
                if frame_cache_key is None:
                    continue
                if not _load_sampling_frame_for_key(settings, frame_cache_key):
                    continue

                # Trace path and extract colors
                cmap, keys = get_cached_path_color_map(
                    frame_cache_key,
                    path_str,
                    cos_a,
                    sin_a,
                    _global_sampler.get_pixel_bgr,
                )
            if not keys:
                continue
            smooth = _path_sampling_smooth_for(settings, tag_name)
            smooth_strength = _path_sampling_smooth_strength_for(settings, tag_name)

            # Compute specific bounding box for this channel
            if tag_name in {"1c", "2c"}:
                bx1, by1, bx2, by2 = tx1, ty1, tx2, ty2
            elif tag_name == "3c":
                bx1 = tx1 - xbord
                by1 = ty1 - ybord
                bx2 = tx2 + xbord
                by2 = ty2 + ybord
            else:  # 4c
                bx1 = tx1 - xbord + xshad
                by1 = ty1 - ybord + yshad
                bx2 = tx2 + xbord + xshad
                by2 = ty2 + ybord + yshad

            if tag_name in group_projected_ranges:
                cg_min, cg_max, _, _ = group_projected_ranges[tag_name]
            else:
                cg_min, cg_max, _, _ = project_bounds(bx1, by1, bx2, by2, cos_a, sin_a)
            if cg_min >= cg_max:
                cg_max = cg_min + 1

            sampling_data[tag_name] = ChannelSamplingData(
                color_map=cmap,
                keys=keys,
                g_min=cg_min,
                g_max=cg_max,
                smooth=smooth,
                smooth_strength=smooth_strength,
            )

    path_sampling = bool(sampling_data)
    original_path_sampling = path_sampling and any(not sd.smooth for sd in sampling_data.values())
    original_path_strip_count = (
        max(len(sd.keys) for sd in sampling_data.values() if not sd.smooth)
        if original_path_sampling
        else 0
    )
    original_path_group_ranges = [
        group_projected_ranges[tag_name]
        for tag_name, sd in sampling_data.items()
        if not sd.smooth and tag_name in group_projected_ranges
    ]
    use_group_original_path_strips = bool(
        original_path_sampling
        and is_axis_aligned
        and original_path_group_ranges
    )
    if use_group_original_path_strips:
        # Sampled color ranges can be narrower than the final visible range when
        # another enabled tag expands the subtitle, e.g. \bord in Vertical mode.
        original_path_loop_g_min = min(
            g_min,
            *(rng[0] for rng in original_path_group_ranges),
        )
        original_path_loop_g_max = max(
            g_max,
            *(rng[1] for rng in original_path_group_ranges),
        )
    else:
        original_path_loop_g_min = g_min
        original_path_loop_g_max = g_max
    color_shift_divisor = max((g_max - g_min) / step, 1.0)
    color_shift_slots = max(
        1,
        original_path_strip_count
        if original_path_sampling
        else int(math.floor(color_shift_divisor + 1e-6)) + (1 if path_sampling else 0),
    )

    def _shift_index_and_slots_for_tag(
        tag_name: str,
        axis_pos: float,
        default_index: int,
        default_slots: int,
    ) -> tuple[int, int]:
        projected = group_projected_ranges.get(tag_name)
        if not projected:
            return default_index, default_slots
        rg_min, rg_max, _, _ = projected
        span = max(rg_max - rg_min, step, 1.0)
        slots = max(
            1,
            int(math.floor(span / step + 1e-6)) + (1 if path_sampling else 0),
        )
        index = max(0, int(round((axis_pos - rg_min) / step)))
        return index, slots

    def _shifted_strip_t_for_tag(
        tag_name: str,
        t: float,
        axis_pos: float,
        default_index: int,
        default_slots: int,
        *,
        include_endpoint: bool,
        force_discrete: bool = False,
    ) -> float:
        shift_index, shift_slots = _shift_index_and_slots_for_tag(
            tag_name,
            axis_pos,
            default_index,
            default_slots,
        )
        return _color_shifted_strip_t(
            t,
            settings,
            shift_index,
            shift_slots,
            include_endpoint=include_endpoint,
            force_discrete=force_discrete,
            tag_name=tag_name,
        )

    # Clean the source text: remove tags that will be overridden
    for tag_name in _tags_to_remove_for_overrides(enabled_tags):
        clean_text = remove_specific_tag(clean_text, tag_name)
    # Remove existing clip tags
    clean_text = re.sub(r"\\i?clip\([^)]*\)", "", clean_text)

    # Remove empty override blocks
    clean_text = re.sub(r"\{\s*\}", "", clean_text)

    # Ensure there's an opening override block
    if not clean_text.startswith("{"):
        clean_text = "{}" + clean_text

    results: list[ASSEvent] = []
    padding = 200
    p_lo = p_min - padding
    p_hi = p_max + padding
    overlap = 0.5

    j = g_min
    strip_index = 0
    while True:
        if original_path_sampling:
            if strip_index >= original_path_strip_count:
                break
            span = max(original_path_loop_g_max - original_path_loop_g_min, 1.0)
            j = original_path_loop_g_min + span * strip_index / original_path_strip_count
            strip_g1 = original_path_loop_g_min + span * (strip_index + 1) / original_path_strip_count
            if use_group_original_path_strips and (strip_g1 <= g_min + 1e-6 or j >= g_max - 1e-6):
                strip_index += 1
                continue
        else:
            if not (j < g_max or (path_sampling and j <= g_max)):
                break
            strip_index = max(0, int(round((j - g_min) / step)))
            strip_g1 = j + step if path_sampling else min(j + step, g_max)
            if not path_sampling and strip_g1 <= j + 1e-4:
                break

        clip_g0 = max(j, g_min) if use_group_original_path_strips else j
        clip_g1 = min(strip_g1, g_max) if use_group_original_path_strips else strip_g1
        value_pos = clip_g0 if use_group_original_path_strips else j
        if clip_g1 <= clip_g0 + 1e-4:
            if original_path_sampling:
                strip_index += 1
            else:
                j += step
            continue

        # Build override tags for this strip
        tag_str = ""
        for tag_name, cfg in enabled_tags.items():
            if tag_name in SHADOW_TAGS:
                continue
            val = None
            try:
                tag_t = _tag_t_for(tag_name, value_pos)
                if tag_name in sampling_data:
                    # Sampled color path
                    sd = sampling_data[tag_name]
                    if original_path_sampling and not sd.smooth:
                        if tag_name in group_projected_ranges:
                            c_range = max(sd.g_max - sd.g_min, 1.0)
                            raw_t = max(0.0, min(1.0, (j - sd.g_min) / c_range))
                            sample_index = int(round(raw_t * max(len(sd.keys) - 1, 0)))
                            sample_t = _color_shifted_strip_t(
                                0.0,
                                settings,
                                sample_index,
                                max(len(sd.keys), 1),
                                include_endpoint=True,
                                force_discrete=True,
                                tag_name=tag_name,
                            )
                        else:
                            sample_t = _color_shifted_strip_t(
                                0.0,
                                settings,
                                strip_index,
                                color_shift_slots,
                                include_endpoint=True,
                                force_discrete=True,
                                tag_name=tag_name,
                            )
                    else:
                        c_range = max(sd.g_max - sd.g_min, 1)
                        sample_t = (j - sd.g_min) / c_range
                        sample_t = _shifted_strip_t_for_tag(
                            tag_name,
                            sample_t,
                            j,
                            strip_index,
                            color_shift_slots,
                            include_endpoint=path_sampling,
                        )
                    if _path_sampling_mirrored_for(settings, tag_name):
                        sample_t = 1.0 - sample_t
                    val = get_color_by_ratio(
                        sd.color_map, sd.keys, sample_t,
                        smooth=sd.smooth,
                        smooth_strength=sd.smooth_strength,
                    )
                else:
                    tag_t = _shifted_strip_t_for_tag(
                        tag_name,
                        tag_t,
                        value_pos,
                        strip_index,
                        color_shift_slots,
                        include_endpoint=path_sampling,
                        force_discrete=original_path_sampling,
                    )
                    val = _get_interpolated_value(tag_name, cfg, tag_t, parsed, style, settings)
                tag_str += build_tag_string(tag_name, val)
            except Exception as exc:
                raise GradientTagError(
                    tag_name,
                    value=val,
                    strip_index=strip_index,
                    position=j,
                    original=exc,
                ) from exc
        try:
            tag_str += _build_shadow_override_tags(
                enabled_tags,
                parsed,
                style,
                settings,
                lambda tag: _shifted_strip_t_for_tag(
                    tag,
                    _tag_t_for(tag, value_pos),
                    value_pos,
                    strip_index,
                    color_shift_slots,
                    include_endpoint=path_sampling,
                    force_discrete=original_path_sampling,
                ),
            )
        except Exception as exc:
            shadow_tag = next(
                (tag for tag in ("shad", "xshad", "yshad") if tag in enabled_tags),
                "shadow",
            )
            raise GradientTagError(
                shadow_tag,
                strip_index=strip_index,
                position=j,
                original=exc,
            ) from exc

        # Handle \pos offset for strip if pos is being gradiented
        pos_offset = None
        if "pos" in enabled_tags:
            interp_pos = None
            try:
                base_pos = get_tag_value("pos", parsed, style)
                pos_t = _shifted_strip_t_for_tag(
                    "pos",
                    _tag_t_for("pos", value_pos),
                    value_pos,
                    strip_index,
                    color_shift_slots,
                    include_endpoint=path_sampling,
                    force_discrete=original_path_sampling,
                )
                interp_pos = _get_interpolated_value(
                    "pos", enabled_tags["pos"], pos_t,
                    parsed, style, settings,
                )
                if isinstance(base_pos, tuple) and isinstance(interp_pos, tuple):
                    pos_offset = (interp_pos[0] - base_pos[0], interp_pos[1] - base_pos[1])
            except Exception as exc:
                raise GradientTagError(
                    "pos",
                    value=interp_pos,
                    strip_index=strip_index,
                    position=j,
                    original=exc,
                ) from exc

        # Build clip tag
        strip_polygon = None
        if is_axis_aligned:
            if is_horiz:
                x1, y1, x2, y2 = clip_g0, clip_ty1, clip_g1, clip_ty2
                clip_str = f"\\clip({clip_g0:.1f},{clip_ty1:.1f},{clip_g1:.1f},{clip_ty2:.1f})"
            else:
                x1, y1, x2, y2 = clip_tx1, clip_g0, clip_tx2, clip_g1
                clip_str = f"\\clip({clip_tx1:.1f},{clip_g0:.1f},{clip_tx2:.1f},{clip_g1:.1f})"

            # Offset clip if pos is being gradiented
            if pos_offset:
                dx, dy = pos_offset
                if is_horiz:
                    x1, y1, x2, y2 = clip_g0 + dx, clip_ty1 + dy, clip_g1 + dx, clip_ty2 + dy
                    clip_str = f"\\clip({x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f})"
                else:
                    x1, y1, x2, y2 = clip_tx1 + dx, clip_g0 + dy, clip_tx2 + dx, clip_g1 + dy
                    clip_str = f"\\clip({x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f})"
            strip_polygon = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        else:
            g0 = j - overlap
            g1 = strip_g1 + overlap
            cx1 = g0 * cos_a - p_lo * sin_a
            cy1 = g0 * sin_a + p_lo * cos_a
            cx2 = g0 * cos_a - p_hi * sin_a
            cy2 = g0 * sin_a + p_hi * cos_a
            cx3 = g1 * cos_a - p_hi * sin_a
            cy3 = g1 * sin_a + p_hi * cos_a
            cx4 = g1 * cos_a - p_lo * sin_a
            cy4 = g1 * sin_a + p_lo * cos_a

            if pos_offset:
                dx, dy = pos_offset
                cx1 += dx; cy1 += dy
                cx2 += dx; cy2 += dy
                cx3 += dx; cy3 += dy
                cx4 += dx; cy4 += dy

            clip_str = (
                f"\\clip(1,m {cx1:.0f} {cy1:.0f} "
                f"l {cx2:.0f} {cy2:.0f} "
                f"l {cx3:.0f} {cy3:.0f} "
                f"l {cx4:.0f} {cy4:.0f})"
            )
            strip_polygon = [(cx1, cy1), (cx2, cy2), (cx3, cy3), (cx4, cy4)]

        if source_vector_clip is not None and strip_polygon is not None:
            clip_str = vector_clip_tag_for_strip(
                source_vector_clip,
                strip_polygon,
                source_offset=pos_offset or (0.0, 0.0),
            )
            if not clip_str:
                if original_path_sampling:
                    strip_index += 1
                else:
                    j += step
                continue

        # Insert tags and clip into the text
        if implicit_pos is not None and "pos" not in enabled_tags:
            tag_str += build_tag_string("pos", implicit_pos)
        strip_text = _insert_tags(clean_text, tag_str + clip_str)

        new_event = ASSEvent(
            layer=event.layer,
            start=event.start,
            end=event.end,
            style=event.style,
            name=event.name,
            margin_l=event.margin_l,
            margin_r=event.margin_r,
            margin_v=event.margin_v,
            effect=event.effect,
            text=strip_text,
            comment=False,
        )
        results.append(new_event)
        if original_path_sampling:
            strip_index += 1
        else:
            j += step

    return results


# ── GBC (Gradient by Character) ──────────────────────────────────────────────

def _generate_gbc(
    event: ASSEvent,
    style: Optional[ASSStyle],
    settings: GradientSettings,
    enabled_tags: dict[str, TagGradientConfig],
    base_bounds_meta: Optional[str] = None,
) -> list[ASSEvent]:
    """Generate per-character gradient."""
    # GBC also supports sampling!
    clean_text, clips = extract_clip_tags(event.text)
    if settings.sampling_paths:
        for tag, path in settings.sampling_paths.items():
            if path:
                clips[tag] = path
            else:
                clips.pop(tag, None)
    sampling_data: dict[str, ChannelSamplingData] = {}
    
    if clips and settings.video_path:
        for tag_name in enabled_tags:
            if tag_name not in _COLOR_TAGS:
                continue
            path_str = clips.get(tag_name)
            if not path_str:
                continue

            saved_sampling = _saved_path_sampling_for_tag(settings, tag_name, 1.0, 0.0)
            if saved_sampling is not None:
                cmap, keys = saved_sampling
            else:
                frame_cache_key = _sampling_frame_key_for_tag(settings, tag_name)
                if frame_cache_key is None:
                    continue
                if not _load_sampling_frame_for_key(settings, frame_cache_key):
                    continue

                # GBC maps colors strictly by text length, so angle=0 (horizontal)
                cmap, keys = get_cached_path_color_map(
                    frame_cache_key,
                    path_str,
                    1.0,
                    0.0,
                    _global_sampler.get_pixel_bgr,
                )
            if keys:
                smooth = _path_sampling_smooth_for(settings, tag_name)
                smooth_strength = _path_sampling_smooth_strength_for(settings, tag_name)
                sampling_data[tag_name] = ChannelSamplingData(
                    color_map=cmap,
                    keys=keys,
                    g_min=0,
                    g_max=1,
                    smooth=smooth,
                    smooth_strength=smooth_strength,
                )

    parsed = parse_tags_from_text(clean_text)

    # Strip override blocks and get plain text
    plain = strip_tags(clean_text)
    if not plain:
        return [event]

    total_chars = len(plain)
    if total_chars == 0:
        return [event]
    color_shift_divisor = max(total_chars - 1, 1)

    # Build character list with positions
    # For GBC, t = char_index / (total_chars - 1)
    # Build new text with per-character overrides
    sections = []
    # Parse the text into tag sections and text sections
    parts = re.split(r"(\{[^}]*\})", event.text)

    char_idx = 0
    new_parts = []
    for part in parts:
        if part.startswith("{") and part.endswith("}"):
            new_parts.append(part)
        else:
            for ch in part:
                t = char_idx / max(total_chars - 1, 1) if total_chars > 1 else 0.5
                tag_str = ""
                for tag_name, cfg in enabled_tags.items():
                    val = None
                    try:
                        tag_t = _color_shifted_t(t, settings, color_shift_divisor, tag_name)
                        if tag_name in sampling_data:
                            sd = sampling_data[tag_name]
                            sample_t = (
                                1.0 - tag_t
                                if _path_sampling_mirrored_for(settings, tag_name)
                                else tag_t
                            )
                            val = get_color_by_ratio(
                                sd.color_map, sd.keys, sample_t,
                                smooth=sd.smooth,
                                smooth_strength=sd.smooth_strength,
                            )
                        else:
                            val = _get_interpolated_value(tag_name, cfg, tag_t, parsed, style, settings)
                        tag_str += build_tag_string(tag_name, val)
                    except Exception as exc:
                        raise GradientTagError(
                            tag_name,
                            value=val,
                            strip_index=char_idx,
                            position=t,
                            original=exc,
                        ) from exc
                new_parts.append("{" + tag_str + "}" + ch)
                char_idx += 1

    new_text = "".join(new_parts)

    new_event = ASSEvent(
        layer=event.layer,
        start=event.start,
        end=event.end,
        style=event.style,
        name=event.name,
        margin_l=event.margin_l,
        margin_r=event.margin_r,
        margin_v=event.margin_v,
        effect=event.effect,
        text=new_text,
        comment=False,
    )
    return [new_event]
