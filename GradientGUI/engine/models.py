"""Public data models shared by the GUI and the rendering engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .interpolation import CurveNode, InterpolationMode, ColorSpace, make_default_nodes


class GradientMode(Enum):
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    ANGLED = "angled"
    GBC = "gbc"


@dataclass
class TagGradientConfig:
    """Configuration for a single ASS tag's gradient."""

    tag: str
    enabled: bool = False
    nodes: list[CurveNode] = field(default_factory=list)
    coord_y_nodes: list[CurveNode] = field(default_factory=list)
    mode: InterpolationMode = InterpolationMode.LINEAR
    coord_y_mode: InterpolationMode = InterpolationMode.LINEAR
    color_space: ColorSpace = ColorSpace.RGB

    def __post_init__(self):
        if not self.nodes:
            self.nodes = make_default_nodes()


@dataclass
class ChannelSamplingData:
    """Sampled color map for one color channel."""

    color_map: dict[int, str]
    keys: list[int]
    g_min: float
    g_max: float
    smooth: bool = False
    smooth_strength: float = 1.0


@dataclass
class AnimationSettings:
    """Optional time-sliced animation settings."""

    enabled: bool = False
    enabled_tags: set[str] = field(default_factory=set)
    use_transform: bool = True
    frame_step: int = 1
    frame_steps: dict[str, int] = field(default_factory=dict)
    seam_blend_length: int = 0
    seam_blend_lengths: dict[str, int] = field(default_factory=dict)
    start_frame: int = 0
    end_frame: int = -1
    shift_start: float = 1.0
    shift_end: float = 1.0
    direction: int = 1
    fps: float = 23.976
    event_first_frame: Optional[int] = None
    event_last_frame: Optional[int] = None
    event_start_ms: Optional[int] = None
    event_end_ms: Optional[int] = None
    frame_time_ms: dict[int, int] = field(default_factory=dict)
    shift_curves: dict[str, list[CurveNode]] = field(default_factory=dict)
    shift_modes: dict[str, InterpolationMode] = field(default_factory=dict)


@dataclass
class GradientSettings:
    """Complete gradient generation settings."""

    mode: GradientMode = GradientMode.HORIZONTAL
    angle: float = 0.0
    step: float = 1.0
    tags: dict[str, TagGradientConfig] = field(default_factory=dict)
    color_space: ColorSpace = ColorSpace.RGB

    text_x1: float = 0
    text_y1: float = 0
    text_x2: float = 0
    text_y2: float = 0

    video_path: Optional[str] = None
    video_time: Optional[float] = None
    video_frame: Optional[int] = None
    sampling_paths: dict[str, str] = field(default_factory=dict)
    sampling_path_frames: dict[str, int] = field(default_factory=dict)
    sampling_path_samples: dict[str, dict[str, Any]] = field(default_factory=dict)
    path_sampling_smooth: dict[str, bool] = field(default_factory=dict)
    path_sampling_smooth_strength: dict[str, float] = field(default_factory=dict)
    path_sampling_mirror: dict[str, bool] = field(default_factory=dict)
    group_range_bounds: Optional[tuple[float, float, float, float]] = None
    group_range_tags: set[str] = field(default_factory=set)
    animation: AnimationSettings = field(default_factory=AnimationSettings)
    color_shift_steps: float = 0.0
    color_shift_steps_by_tag: dict[str, float] = field(default_factory=dict)
