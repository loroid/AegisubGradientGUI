"""Validate persisted effect data before it can change any widgets or history."""

from __future__ import annotations

import math
import re
from typing import Any

from engine.interpolation import ColorSpace, InterpolationMode
from engine.tag_parser import TAG_INFO


_MODES = tuple(mode.value for mode in InterpolationMode)
_COLOR_RE = re.compile(r"[0-9a-fA-F]{6}")


def _fail(path: str, expected: str) -> None:
    raise ValueError(f"Invalid preset field {path}: expected {expected}.")


def _mapping(value: Any, path: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        _fail(path, "an object with string keys")
    return value


def _sequence(value: Any, path: str) -> list | tuple:
    if not isinstance(value, (list, tuple)):
        _fail(path, "an array")
    return value


def _string(value: Any, path: str) -> None:
    if not isinstance(value, str):
        _fail(path, "a string")


def _boolean(value: Any, path: str) -> None:
    if type(value) is not bool:
        _fail(path, "true or false")


def _number(value: Any, path: str, *, integer=False, minimum=None, maximum=None) -> None:
    valid = type(value) in (int, float)
    if valid:
        try:
            valid = math.isfinite(value)
        except OverflowError:
            valid = False
    if not valid:
        _fail(path, "a finite number")
    if integer and int(value) != value:
        _fail(path, "an integer")
    if minimum is not None and value < minimum:
        _fail(path, f"a number >= {minimum}")
    if maximum is not None and value > maximum:
        _fail(path, f"a number <= {maximum}")


def _choice(value: Any, path: str, choices: tuple) -> None:
    _string(value, path)
    if value not in choices:
        _fail(path, " / ".join(choices))


def _color(value: Any, path: str) -> None:
    _string(value, path)
    if not _COLOR_RE.fullmatch(value):
        _fail(path, "a six-digit BGR hexadecimal color")


def _nodes(value: Any, path: str, *, color=False, animation=False) -> None:
    for index, raw in enumerate(_sequence(value, path)):
        node_path = f"{path}[{index}]"
        node = _mapping(raw, node_path)
        for field in ("x", "y", "hix", "hiy", "hox", "hoy"):
            _number(node.get(field), f"{node_path}.{field}")
        _number(node["x"], f"{node_path}.x", integer=animation,
                minimum=0, maximum=None if animation else 100)
        text = node.get("value_str", "")
        (_color if color else _string)(text, f"{node_path}.value_str")
        if node.get("seg_mode") is not None:
            _choice(node["seg_mode"], f"{node_path}.seg_mode", _MODES)
        if "step_from_right" in node:
            _boolean(node["step_from_right"], f"{node_path}.step_from_right")


def _curve_section(value: Any, path: str, *, animation=False) -> None:
    section = _mapping(value, path)
    for key, nodes in _mapping(section.get("nodes", {}), f"{path}.nodes").items():
        color = not animation and TAG_INFO.get(key, {}).get("type") == "color"
        _nodes(nodes, f"{path}.nodes.{key}", color=color, animation=animation)
    for key, mode in _mapping(section.get("modes", {}), f"{path}.modes").items():
        _choice(mode, f"{path}.modes.{key}", _MODES)
    for key, mirror in _mapping(section.get("mirrors", {}), f"{path}.mirrors").items():
        field = f"{path}.mirrors.{key}"
        pair = _sequence(mirror, field)
        if len(pair) != 2:
            _fail(field, "two boolean values")
        for index, value in enumerate(pair):
            _boolean(value, f"{field}[{index}]")


def _string_list(value: Any, path: str) -> None:
    for index, item in enumerate(_sequence(value, path)):
        _string(item, f"{path}[{index}]")


def _sampling_paths(value: Any) -> None:
    root = "path_sampling.paths"
    for line, tags in _mapping(value, root).items():
        path = f"{root}.{line}"
        if not line.isascii() or not line.isdigit():
            _fail(path, "a non-negative line index")
        for tag, raw in _mapping(tags, path).items():
            tag_path = f"{path}.{tag}"
            data = _mapping(raw, tag_path)
            for key in ("removed_original", "removed"):
                if key in data:
                    _boolean(data[key], f"{tag_path}.{key}")
            if data.get("sampling_frame") is not None:
                _number(data["sampling_frame"], f"{tag_path}.sampling_frame", integer=True, minimum=0)
            for key in ("ass_path", "path"):
                if key in data:
                    _string(data[key], f"{tag_path}.{key}")
            for index, raw_path in enumerate(_sequence(data.get("paths", []), f"{tag_path}.paths")):
                field = f"{tag_path}.paths[{index}]"
                entry = _mapping(raw_path, field)
                _string(entry.get("ass_path", entry.get("path", "")), f"{field}.ass_path")
                if "label" in entry:
                    _string(entry["label"], f"{field}.label")
            for key, size in (("sampled_points", 4), ("sampled_pixels", 4),
                              ("sampled_colors", 2), ("samples", 2)):
                for index, sample in enumerate(_sequence(data.get(key, []), f"{tag_path}.{key}")):
                    field = f"{tag_path}.{key}[{index}]"
                    # Canonical saves use arrays; the path model also accepts named samples.
                    if isinstance(sample, dict):
                        if size == 4:
                            sample = [sample.get("segment", sample.get("segment_index", 0)),
                                      sample.get("x"), sample.get("y"),
                                      sample.get("color", sample.get("value", ""))]
                        else:
                            sample = [sample.get("key", sample.get("index")),
                                      sample.get("color", sample.get("value", ""))]
                    sample = _sequence(sample, field)
                    if size == 4 and len(sample) == 3:
                        sample = [0, *sample]
                    if len(sample) != size:
                        _fail(field, f"{size} sample fields")
                    for coordinate, number in enumerate(sample[:-1]):
                        _number(number, f"{field}[{coordinate}]", integer=True,
                                minimum=0 if size == 4 and coordinate == 0 else None)
                    _color(sample[-1], f"{field}[{size - 1}]")


def validate_preset_sections(data: dict[str, Any]) -> None:
    """Check known fields without mutating values or rejecting optional omissions."""
    for tag, raw in _mapping(data.get("tags", {}), "tags").items():
        path = f"tags.{tag}"
        config = _mapping(raw, path)
        _boolean(config.get("enabled"), f"{path}.enabled")
        _nodes(config.get("nodes", []), f"{path}.nodes",
               color=TAG_INFO.get(tag, {}).get("type") == "color")
        _nodes(config.get("coord_y_nodes", []), f"{path}.coord_y_nodes")
        if "coord_y_mode" in config:
            _choice(config["coord_y_mode"], f"{path}.coord_y_mode", _MODES)

    _curve_section(data.get("curves", {}), "curves")
    sampling = _mapping(data.get("path_sampling", {}), "path_sampling")
    _sampling_paths(sampling.get("paths", {}))
    for key, value in _mapping(sampling.get("smooth", {}), "path_sampling.smooth").items():
        _boolean(value, f"path_sampling.smooth.{key}")
    for key, value in _mapping(sampling.get("smooth_strength", {}), "path_sampling.smooth_strength").items():
        _number(value, f"path_sampling.smooth_strength.{key}", minimum=0, maximum=1)

    ranges = _mapping(data.get("range_settings", {}), "range_settings")
    if "merge_selected_lines" in ranges:
        _boolean(ranges["merge_selected_lines"], "range_settings.merge_selected_lines")
    _string_list(ranges.get("group_range_tags", []), "range_settings.group_range_tags")

    animation = _mapping(data.get("animation", {}), "animation")
    settings = _mapping(animation.get("settings", {}), "animation.settings")
    for field in ("enabled", "use_transform"):
        if field in settings:
            _boolean(settings[field], f"animation.settings.{field}")
    _string_list(settings.get("enabled_tags", []), "animation.settings.enabled_tags")
    for field, minimum, maximum in (("frame_step", 1, 999), ("seam_blend_length", 0, 999),
                                     ("preview_frame", 0, 2147483647)):
        if field in settings:
            _number(settings[field], f"animation.settings.{field}", integer=True,
                    minimum=minimum, maximum=maximum)
    for field, minimum in (("frame_steps", 1), ("seam_blend_lengths", 0)):
        for tag, value in _mapping(settings.get(field, {}), f"animation.settings.{field}").items():
            _number(value, f"animation.settings.{field}.{tag}", integer=True, minimum=minimum, maximum=999)
    _curve_section(animation.get("curves", {}), "animation.curves", animation=True)

    ui = _mapping(data.get("ui_state", {}), "ui_state")
    for field, choices in (("mode", ("Horizontal", "Vertical", "Angled", "GBC")),
                           ("color_space", tuple(space.value for space in ColorSpace))):
        if field in ui:
            _choice(ui[field], f"ui_state.{field}", choices)
    for field, minimum, maximum in (("angle", -360, 360), ("step", 0.05, 100)):
        if field in ui:
            _number(ui[field], f"ui_state.{field}", minimum=minimum, maximum=maximum)
    for field in ("active_curve_key", "description"):
        if field in ui:
            _string(ui[field], f"ui_state.{field}")
    if "active_event_idx" in ui:
        _number(ui["active_event_idx"], "ui_state.active_event_idx", integer=True, minimum=0)
    for index, value in enumerate(_sequence(ui.get("selected_lines", []), "ui_state.selected_lines")):
        _number(value, f"ui_state.selected_lines[{index}]", integer=True, minimum=0)
