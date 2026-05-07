"""Preset file dialogs and JSON IO."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QFileDialog

from gui.i18n import tr


PRESET_FILTER = "GradientGUI Preset (*.ggpreset);;JSON (*.json);;All Files (*)"
RECENT_PRESETS_PATH = Path.home() / ".gradientgui_recent_presets.json"
MAX_RECENT_PRESETS = 12


def select_save_path(parent, default_path: str) -> str:
    path, _ = QFileDialog.getSaveFileName(
        parent,
        tr("保存预设"),
        default_path,
        PRESET_FILTER,
    )
    if not path:
        return ""
    if Path(path).suffix.lower() not in {".ggpreset", ".json"}:
        path += ".ggpreset"
    return path


def write_preset(path: str, preset_data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(preset_data, f, ensure_ascii=False, indent=2)


def read_preset(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError(tr("预设格式不正确。"))
    return payload


def select_and_read_preset(parent, default_dir: str) -> tuple[str, dict[str, Any]] | None:
    path, _ = QFileDialog.getOpenFileName(
        parent,
        tr("加载预设"),
        default_dir,
        PRESET_FILTER,
    )
    if not path:
        return None

    return path, read_preset(path)


def read_recent_presets() -> list[str]:
    try:
        with open(RECENT_PRESETS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in payload:
        path = str(item)
        key = path.lower()
        if key not in seen and Path(path).exists():
            result.append(path)
            seen.add(key)
    return result[:MAX_RECENT_PRESETS]


def remember_recent_preset(path: str) -> None:
    if not path:
        return
    recent = [p for p in read_recent_presets() if p.lower() != path.lower()]
    recent.insert(0, path)
    recent = recent[:MAX_RECENT_PRESETS]
    try:
        with open(RECENT_PRESETS_PATH, "w", encoding="utf-8") as f:
            json.dump(recent, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def describe_preset(data: dict[str, Any]) -> str:
    tags = data.get("tags", {}) if isinstance(data, dict) else {}
    enabled_tags = [
        tag for tag, cfg in tags.items()
        if isinstance(cfg, dict) and cfg.get("enabled")
    ]
    paths = data.get("path_sampling", {}).get("paths", {}) if isinstance(data, dict) else {}
    path_count = 0
    if isinstance(paths, dict):
        for line_paths in paths.values():
            if isinstance(line_paths, dict):
                path_count += sum(1 for value in line_paths.values() if value)
    ui_state = data.get("ui_state", {}) if isinstance(data, dict) else {}
    range_settings = data.get("range_settings", {}) if isinstance(data, dict) else {}
    animation = data.get("animation", {}) if isinstance(data, dict) else {}
    animation_settings = animation.get("settings", {}) if isinstance(animation, dict) else {}
    return "\n".join(
        [
            f"{tr('版本')}: {data.get('version', '-')}",
            f"{tr('模式')}: {ui_state.get('mode', '-')}",
            f"{tr('角度')}: {ui_state.get('angle', '-')}",
            f"{tr('步长')}: {ui_state.get('step', '-')}",
            f"{tr('启用 tag')}: {', '.join(enabled_tags) if enabled_tags else '-'}",
            f"{tr('路径采色')}: {path_count} {tr('项')}",
            f"{tr('整体范围')}: {tr('开' if range_settings.get('merge_selected_lines') else '关')}",
            f"{tr('色带动画')}: {tr('开' if animation_settings.get('enabled') else '关')}",
        ]
    )
