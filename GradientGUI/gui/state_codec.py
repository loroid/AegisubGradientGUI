"""Serialization helpers for undo/preset UI state."""

from __future__ import annotations

from typing import Any

from engine.api import TagGradientConfig
from engine.interpolation import CurveNode, InterpolationMode
from engine.path_model import normalize_path_state, serialize_path_state
from gui.undo_manager import UndoState


PRESET_FORMAT = "GradientGUI Preset"
PRESET_SCHEMA_VERSION = 4


def serialize_node(node: CurveNode) -> dict[str, Any]:
    return {
        "x": node.x,
        "y": node.y,
        "value_str": node.value_str,
        "hix": node.handle_in_x,
        "hiy": node.handle_in_y,
        "hox": node.handle_out_x,
        "hoy": node.handle_out_y,
        "seg_mode": node.segment_mode.value if node.segment_mode else None,
    }


def deserialize_node(data: dict[str, Any]) -> CurveNode:
    node = CurveNode(
        x=data["x"],
        y=data["y"],
        value_str=data.get("value_str", ""),
    )
    node.handle_in_x = data["hix"]
    node.handle_in_y = data["hiy"]
    node.handle_out_x = data["hox"]
    node.handle_out_y = data["hoy"]
    seg = data.get("seg_mode")
    node.segment_mode = InterpolationMode(seg) if seg else None
    return node


def serialize_nodes(nodes: list[CurveNode]) -> list[dict[str, Any]]:
    return [serialize_node(node) for node in nodes]


def deserialize_nodes(node_data: list[dict[str, Any]]) -> list[CurveNode]:
    return [deserialize_node(data) for data in node_data]


def serialize_tag_config(config: TagGradientConfig) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "nodes": serialize_nodes(config.nodes),
        "coord_y_nodes": serialize_nodes(config.coord_y_nodes),
        "coord_y_mode": config.coord_y_mode.value,
    }


def serialize_tag_panel_configs(tag_panel) -> dict[str, dict[str, Any]]:
    return {
        tag: serialize_tag_config(row.get_config())
        for tag, row in tag_panel._rows.items()
    }


def deserialize_tag_config(tag: str, data: dict[str, Any]) -> TagGradientConfig:
    config = TagGradientConfig(
        tag=tag,
        enabled=data["enabled"],
    )
    config.nodes = deserialize_nodes(data.get("nodes", []))
    config.coord_y_nodes = deserialize_nodes(data.get("coord_y_nodes", []))
    coord_y_mode = data.get("coord_y_mode")
    if coord_y_mode:
        config.coord_y_mode = InterpolationMode(coord_y_mode)
    return config


def restore_tag_panel_configs(tag_panel, serialized_configs: dict[str, dict[str, Any]]) -> None:
    for tag, data in serialized_configs.items():
        row = tag_panel.get_row(tag)
        if row:
            row.set_config(deserialize_tag_config(tag, data))


def serialize_curve_store(
    curves: dict[str, list[CurveNode]],
) -> dict[str, list[dict[str, Any]]]:
    return {
        tag: serialize_nodes(nodes)
        for tag, nodes in curves.items()
    }


def deserialize_curve_store(
    serialized_curves: dict[str, list[dict[str, Any]]],
) -> dict[str, list[CurveNode]]:
    return {
        tag: deserialize_nodes(node_data)
        for tag, node_data in serialized_curves.items()
    }


def serialize_curve_modes(modes: dict[str, InterpolationMode]) -> dict[str, str]:
    return {
        tag: mode.value
        for tag, mode in modes.items()
    }


def deserialize_curve_modes(modes: dict[str, str]) -> dict[str, InterpolationMode]:
    return {
        tag: InterpolationMode(value)
        for tag, value in modes.items()
    }


def preset_from_state(state: UndoState) -> dict[str, Any]:
    """Serialize an UndoState into the formal preset schema."""

    return {
        "format": PRESET_FORMAT,
        "version": PRESET_SCHEMA_VERSION,
        "tags": state.tag_configs,
        "curves": {
            "nodes": state.tag_curves,
            "modes": state.tag_modes,
            "mirrors": {
                key: [bool(value[0]), bool(value[1])]
                for key, value in state.curve_mirrors.items()
            },
        },
        "path_sampling": {
            "paths": state.sampling_paths,
            "smooth": state.path_sampling_smooth,
            "smooth_strength": state.path_sampling_smooth_strength,
        },
        "range_settings": {
            "merge_selected_lines": state.merge_selected_lines,
            "group_range_tags": state.group_range_tags,
        },
        "animation": {
            "settings": state.animation_state,
            "curves": {
                "nodes": state.animation_curves,
                "modes": state.animation_modes,
                "mirrors": {
                    key: [bool(value[0]), bool(value[1])]
                    for key, value in state.animation_curve_mirrors.items()
                },
            },
        },
        "ui_state": {
            "mode": state.mode,
            "angle": state.angle,
            "step": state.step,
            "color_space": state.color_space,
            "selected_lines": state.selected_lines,
            "active_event_idx": state.active_event_idx,
            "active_curve_key": state.active_curve_key,
            "description": state.description,
        },
    }


def state_from_preset_data(data: dict[str, Any]) -> UndoState:
    """Build an UndoState from persisted preset JSON data."""

    if not isinstance(data, dict):
        raise ValueError("预设格式不正确。")

    if data.get("format") != PRESET_FORMAT:
        raise ValueError("不是 GradientGUI 预设文件。")

    version = data.get("version")
    if version != PRESET_SCHEMA_VERSION:
        raise ValueError(f"不支持的预设版本: {version}")

    tags = _dict_section(data, "tags")
    curves = _dict_section(data, "curves")
    path_sampling = _dict_section(data, "path_sampling")
    range_settings = _dict_section(data, "range_settings")
    animation = _dict_section(data, "animation")
    ui_state = _dict_section(data, "ui_state")

    sampling_paths = serialize_path_state(
        normalize_path_state(path_sampling.get("paths", {}))
    )

    curve_mirrors: dict[str, tuple[bool, bool]] = {}
    for key, value in curves.get("mirrors", {}).items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            curve_mirrors[key] = (bool(value[0]), bool(value[1]))

    animation_settings = _dict_section(animation, "settings")
    animation_curves = _dict_section(animation, "curves")
    animation_curve_mirrors: dict[str, tuple[bool, bool]] = {}
    for key, value in animation_curves.get("mirrors", {}).items():
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            animation_curve_mirrors[key] = (bool(value[0]), bool(value[1]))

    return UndoState(
        tag_configs=tags,
        tag_curves=curves.get("nodes", {}),
        tag_modes=curves.get("modes", {}),
        curve_mirrors=curve_mirrors,
        animation_curves=animation_curves.get("nodes", {}),
        animation_modes=animation_curves.get("modes", {}),
        animation_curve_mirrors=animation_curve_mirrors,
        sampling_paths=sampling_paths,
        selected_lines=[int(i) for i in ui_state.get("selected_lines", [])],
        active_event_idx=int(ui_state.get("active_event_idx", 0) or 0),
        active_curve_key=str(ui_state.get("active_curve_key", "") or ""),
        mode=str(ui_state.get("mode", "Horizontal") or "Horizontal"),
        angle=float(ui_state.get("angle", 0.0) or 0.0),
        step=float(ui_state.get("step", 1.0) or 1.0),
        color_space=str(ui_state.get("color_space", "RGB") or "RGB"),
        path_sampling_smooth=path_sampling.get("smooth", {}),
        path_sampling_smooth_strength=path_sampling.get("smooth_strength", {}),
        merge_selected_lines=bool(range_settings.get("merge_selected_lines", False)),
        group_range_tags=list(range_settings.get("group_range_tags", [])),
        animation_state=animation_settings,
        description=str(ui_state.get("description", "预设") or "预设"),
    )


def _dict_section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section = data.get(key, {})
    if not isinstance(section, dict):
        raise ValueError(f"预设字段 {key} 格式不正确。")
    return section
