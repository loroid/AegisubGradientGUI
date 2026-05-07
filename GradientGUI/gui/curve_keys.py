"""Helpers for per-tag curve tab keys."""

from __future__ import annotations

from typing import Optional

from engine.tag_parser import TAG_INFO
from gui.i18n import tr


def curve_key(tag: str, axis: Optional[str] = None) -> str:
    return f"{tag}:{axis}" if axis else tag


def curve_key_tag(key: Optional[str]) -> Optional[str]:
    if not key:
        return None
    return key.split(":", 1)[0]


def curve_key_axis(key: Optional[str]) -> Optional[str]:
    if not key or ":" not in key:
        return None
    return key.split(":", 1)[1]


def curve_keys_for_tag(tag: str) -> list[str]:
    if TAG_INFO.get(tag, {}).get("type") == "coord":
        return [curve_key(tag, "x"), curve_key(tag, "y")]
    return [tag]


def curve_label(key: str) -> str:
    tag = curve_key_tag(key) or key
    axis = curve_key_axis(key)
    if axis:
        if tag == "fad":
            return tr("\\fad 淡入") if axis == "x" else tr("\\fad 淡出")
        return f"\\{tag} {axis.upper()}"
    return f"\\{tag}"
