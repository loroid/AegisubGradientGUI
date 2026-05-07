"""
Undo/Redo manager for GradientGUI.

Maintains a stack of serialized state snapshots.
Each snapshot captures all tag configs, curve nodes, and settings.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Optional, Any, Callable


@dataclass
class UndoState:
    """A single state snapshot."""
    tag_configs: dict[str, Any]      # serialized TagGradientConfig per tag
    tag_curves: dict[str, list]      # serialized CurveNode list per tag
    tag_modes: dict[str, str]        # InterpolationMode.value per tag
    curve_mirrors: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    sampling_paths: dict[str, dict[str, Any]] = field(default_factory=dict)
    selected_lines: list[int] = field(default_factory=list)
    active_event_idx: int = 0
    active_curve_key: str = ""
    mode: str = "Horizontal"         # GradientMode name
    angle: float = 0.0
    step: float = 1.0
    color_space: str = "RGB"
    path_sampling_smooth: dict[str, bool] = field(default_factory=dict)
    path_sampling_smooth_strength: dict[str, float] = field(default_factory=dict)
    merge_selected_lines: bool = False
    group_range_tags: list[str] = field(default_factory=list)
    animation_state: dict[str, Any] = field(default_factory=dict)
    animation_curves: dict[str, list] = field(default_factory=dict)
    animation_modes: dict[str, str] = field(default_factory=dict)
    animation_curve_mirrors: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    description: str = ""


class UndoManager:
    """Manages undo/redo state stacks."""

    MAX_HISTORY = 500

    def __init__(self):
        self._undo_stack: list[UndoState] = []
        self._redo_stack: list[UndoState] = []
        self._on_change: Optional[Callable] = None

    def set_change_callback(self, callback: Callable):
        """Set callback to invoke when undo/redo state changes."""
        self._on_change = callback

    def push(self, state: UndoState):
        """Push a new state. Clears redo stack."""
        self._undo_stack.append(state)
        self._redo_stack.clear()
        if len(self._undo_stack) > self.MAX_HISTORY:
            self._undo_stack.pop(0)
        if self._on_change:
            self._on_change()

    def undo(self) -> Optional[UndoState]:
        """Undo: pop from undo stack, push current to redo."""
        if len(self._undo_stack) < 2:
            return None  # Need at least 2 states (current + previous)
        current = self._undo_stack.pop()
        self._redo_stack.append(current)
        if self._on_change:
            self._on_change()
        return self._undo_stack[-1] if self._undo_stack else None

    def redo(self) -> Optional[UndoState]:
        """Redo: pop from redo stack, push to undo."""
        if not self._redo_stack:
            return None
        state = self._redo_stack.pop()
        self._undo_stack.append(state)
        if self._on_change:
            self._on_change()
        return state

    @property
    def can_undo(self) -> bool:
        return len(self._undo_stack) >= 2

    @property
    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    @property
    def undo_count(self) -> int:
        return max(0, len(self._undo_stack) - 1)

    @property
    def redo_count(self) -> int:
        return len(self._redo_stack)
