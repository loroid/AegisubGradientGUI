"""Small LRU cache for generated preview subtitle events."""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import asdict, is_dataclass, replace
from enum import Enum
from typing import Any

from engine.ass_parser import ASSEvent


def stable_preview_key(data: Any) -> str:
    """Return a stable hash for nested preview state data."""

    payload = json.dumps(
        _normalize(data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PreviewGenerationCache:
    """LRU cache that stores cloned generated ASS events."""

    def __init__(self, max_entries: int = 48):
        self._max_entries = max(1, int(max_entries))
        self._entries: OrderedDict[str, list[ASSEvent]] = OrderedDict()

    def get(self, key: str) -> list[ASSEvent] | None:
        events = self._entries.get(key)
        if events is None:
            return None
        self._entries.move_to_end(key)
        return _clone_events(events)

    def put(self, key: str, events: list[ASSEvent]) -> None:
        self._entries[key] = _clone_events(events)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    @property
    def size(self) -> int:
        return len(self._entries)


def _clone_events(events: list[ASSEvent]) -> list[ASSEvent]:
    return [replace(event) for event in events]


def _normalize(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {
            str(key): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize(item) for item in value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
