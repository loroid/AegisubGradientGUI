"""
Curve interpolation algorithms.

Supports linear, smooth (Catmull-Rom), stepped, and cubic bezier modes.
Also provides color interpolation in RGB, HSL, and OKLab spaces.

v2: Per-segment interpolation mode — each node stores the mode for its
    outgoing segment (node[i] → node[i+1]).
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Enums ─────────────────────────────────────────────────────────────────────

class InterpolationMode(Enum):
    LINEAR = "linear"
    SMOOTH = "smooth"
    STEPPED = "stepped"
    BEZIER = "bezier"


class ColorSpace(Enum):
    RGB = "RGB"
    HSL = "HSL"
    OKLAB = "OKLab"


# ── Curve Node ────────────────────────────────────────────────────────────────

@dataclass
class CurveNode:
    """A keyframe node on the interpolation curve."""
    x: float  # position along gradient (0.0 to 100.0)
    y: float = 0.0  # exact numeric value (for numeric tags)
    value_str: str = ""  # color hex string (for color tags)

    # Bezier control handles (absolute coordinates, not relative)
    handle_in_x: float = 0.0
    handle_in_y: float = 0.0
    handle_out_x: float = 0.0
    handle_out_y: float = 0.0

    # Per-segment interpolation mode (this node → next node).
    # If None, uses the global/default mode.
    segment_mode: Optional[InterpolationMode] = None

    def __post_init__(self):
        # Default handles: 10 units away horizontally
        if self.handle_in_x == 0.0 and self.handle_in_y == 0.0:
            self.handle_in_x = self.x - 10.0
            self.handle_in_y = self.y
        if self.handle_out_x == 0.0 and self.handle_out_y == 0.0:
            self.handle_out_x = self.x + 10.0
            self.handle_out_y = self.y

    def get_segment_mode(self, default: InterpolationMode) -> InterpolationMode:
        """Get the mode for the segment starting at this node."""
        return self.segment_mode if self.segment_mode is not None else default


def make_default_nodes(start_y: float = 0.0, end_y: float = 1.0, start_color: str = "FFFFFF", end_color: str = "FFFFFF") -> list[CurveNode]:
    """Create a default 2-node curve (start → end)."""
    n0 = CurveNode(x=0.0, y=start_y, value_str=start_color)
    n0.handle_in_x = -10.0
    n0.handle_in_y = start_y
    n0.handle_out_x = 33.33
    n0.handle_out_y = start_y
    n1 = CurveNode(x=100.0, y=end_y, value_str=end_color)
    n1.handle_in_x = 66.67
    n1.handle_in_y = end_y
    n1.handle_out_x = 110.0
    n1.handle_out_y = end_y
    return [n0, n1]


# ── Core interpolation ───────────────────────────────────────────────────────

def interpolate(
    nodes: list[CurveNode],
    x_pos: float,
    default_mode: InterpolationMode = InterpolationMode.LINEAR,
    is_color: bool = False,
    color_space: ColorSpace = ColorSpace.RGB,
) -> object:
    """
    Get interpolated value at position x_pos (0..100).
    Returns float for numeric, str (hex) for color.
    """
    if not nodes:
        return "FFFFFF" if is_color else 0.0
    if len(nodes) == 1:
        return nodes[0].value_str if is_color else nodes[0].y

    nodes = sorted(nodes, key=lambda node: node.x)
    x_min = nodes[0].x
    x_max = nodes[-1].x
    x_pos = max(x_min, min(x_max, x_pos))

    if x_pos <= nodes[0].x:
        return nodes[0].value_str if is_color else nodes[0].y
    if x_pos >= nodes[-1].x:
        return nodes[-1].value_str if is_color else nodes[-1].y

    idx = 0
    for i in range(len(nodes) - 1):
        if nodes[i].x <= x_pos <= nodes[i + 1].x:
            idx = i
            break

    n0 = nodes[idx]
    n1 = nodes[idx + 1]
    seg_len = n1.x - n0.x
    if seg_len < 1e-9:
        return n0.value_str if is_color else n0.y

    local_t = (x_pos - n0.x) / seg_len  # 0..1 within segment
    mode = n0.get_segment_mode(default_mode)

    if is_color:
        if mode == InterpolationMode.STEPPED:
            return n0.value_str
        elif mode == InterpolationMode.SMOOTH:
            return _catmull_rom_color(nodes, idx, local_t, color_space)
        else:
            return interpolate_color(n0.value_str, n1.value_str, local_t, color_space)
    else:
        if mode == InterpolationMode.STEPPED:
            return n0.y
        elif mode == InterpolationMode.SMOOTH:
            return _catmull_rom(nodes, idx, local_t)
        elif mode == InterpolationMode.BEZIER:
            return _cubic_bezier_segment(n0, n1, local_t)
        else:
            return _lerp(n0.y, n1.y, local_t)


# ── Linear ────────────────────────────────────────────────────────────────────

def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


# ── Catmull-Rom Spline ────────────────────────────────────────────────────────

def _catmull_rom(nodes: list[CurveNode], idx: int, t: float) -> float:
    """Catmull-Rom spline interpolation between nodes[idx] and nodes[idx+1]."""
    # Get 4 control points (clamp at edges)
    p0 = nodes[max(0, idx - 1)].y
    p1 = nodes[idx].y
    p2 = nodes[min(len(nodes) - 1, idx + 1)].y
    p3 = nodes[min(len(nodes) - 1, idx + 2)].y

    t2 = t * t
    t3 = t2 * t

    # Catmull-Rom matrix
    return 0.5 * (
        (2 * p1)
        + (-p0 + p2) * t
        + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
        + (-p0 + 3 * p1 - 3 * p2 + p3) * t3
    )


# ── Cubic Bezier ──────────────────────────────────────────────────────────────

def _cubic_bezier_segment(n0: CurveNode, n1: CurveNode, t: float) -> float:
    """
    Evaluate cubic bezier between two nodes.

    The curve is defined by 4 control points:
      P0 = (n0.x, n0.y)
      P1 = (n0.handle_out_x, n0.handle_out_y)
      P2 = (n1.handle_in_x, n1.handle_in_y)
      P3 = (n1.x, n1.y)

    Since t is already in local segment space (0..1 between n0.x and n1.x),
    we need to find the bezier parameter u such that B_x(u) maps correctly.
    For simplicity and speed, we can normalize handles to local space and
    evaluate directly if handles are "well-behaved" (monotonic in x).
    """
    # Normalize all to [0,1] segment space
    seg_x = n1.x - n0.x
    if seg_x < 1e-9:
        return n0.y

    # Control points in local x coords (0..1)
    p0x = 0.0
    p1x = (n0.handle_out_x - n0.x) / seg_x
    p2x = (n1.handle_in_x - n0.x) / seg_x
    p3x = 1.0

    p0y = n0.y
    p1y = n0.handle_out_y
    p2y = n1.handle_in_y
    p3y = n1.y

    # Find bezier parameter u where B_x(u) = t using Newton's method
    u = t  # initial guess
    for _ in range(10):
        bx = _bezier3(p0x, p1x, p2x, p3x, u)
        dbx = _bezier3_deriv(p0x, p1x, p2x, p3x, u)
        if abs(dbx) < 1e-9:
            break
        u = u - (bx - t) / dbx
        u = max(0.0, min(1.0, u))

    return _bezier3(p0y, p1y, p2y, p3y, u)


def _bezier3(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Evaluate cubic bezier at parameter t."""
    u = 1.0 - t
    return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3


def _bezier3_deriv(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Derivative of cubic bezier at parameter t."""
    u = 1.0 - t
    return 3 * u * u * (p1 - p0) + 6 * u * t * (p2 - p1) + 3 * t * t * (p3 - p2)


def _catmull_rom_color(nodes: list[CurveNode], idx: int, t: float, space: ColorSpace) -> str:
    p0 = nodes[max(0, idx - 1)].value_str
    p1 = nodes[idx].value_str
    p2 = nodes[min(len(nodes) - 1, idx + 1)].value_str
    p3 = nodes[min(len(nodes) - 1, idx + 2)].value_str

    def _hex_to_rgb(h: str):
        h = h.ljust(6, '0')
        return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16) # R, G, B

    c0 = _hex_to_rgb(p0)
    c1 = _hex_to_rgb(p1)
    c2 = _hex_to_rgb(p2)
    c3 = _hex_to_rgb(p3)

    if space == ColorSpace.OKLAB:
        c0 = _rgb_to_oklab(*c0)
        c1 = _rgb_to_oklab(*c1)
        c2 = _rgb_to_oklab(*c2)
        c3 = _rgb_to_oklab(*c3)

    def _spline(v0, v1, v2, v3):
        t2 = t * t
        t3 = t2 * t
        return 0.5 * ((2 * v1) + (-v0 + v2) * t + (2 * v0 - 5 * v1 + 4 * v2 - v3) * t2 + (-v0 + 3 * v1 - 3 * v2 + v3) * t3)

    v1 = _spline(c0[0], c1[0], c2[0], c3[0])
    v2 = _spline(c0[1], c1[1], c2[1], c3[1])
    v3 = _spline(c0[2], c1[2], c2[2], c3[2])

    if space == ColorSpace.OKLAB:
        r, g, b = _oklab_to_rgb(v1, v2, v3)
    else:
        r, g, b = int(v1 + 0.5), int(v2 + 0.5), int(v3 + 0.5)

    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return f"{b:02X}{g:02X}{r:02X}"

# ── Color interpolation ──────────────────────────────────────────────────────

def interpolate_color(
    color1_bgr: str,
    color2_bgr: str,
    t: float,
    space: ColorSpace = ColorSpace.RGB,
) -> str:
    """
    Interpolate between two BGR hex colors.

    Args:
        color1_bgr: "BBGGRR" start color
        color2_bgr: "BBGGRR" end color
        t: interpolation factor (0.0 = color1, 1.0 = color2)
        space: color interpolation space

    Returns:
        Interpolated "BBGGRR" hex string
    """
    t = max(0.0, min(1.0, t))

    b1 = int(color1_bgr[0:2], 16)
    g1 = int(color1_bgr[2:4], 16)
    r1 = int(color1_bgr[4:6], 16)
    b2 = int(color2_bgr[0:2], 16)
    g2 = int(color2_bgr[2:4], 16)
    r2 = int(color2_bgr[4:6], 16)

    if space == ColorSpace.RGB:
        r = int(r1 + (r2 - r1) * t + 0.5)
        g = int(g1 + (g2 - g1) * t + 0.5)
        b = int(b1 + (b2 - b1) * t + 0.5)

    elif space == ColorSpace.HSL:
        r, g, b = _interpolate_hsl(r1, g1, b1, r2, g2, b2, t)

    elif space == ColorSpace.OKLAB:
        r, g, b = _interpolate_oklab(r1, g1, b1, r2, g2, b2, t)

    else:
        r = int(r1 + (r2 - r1) * t + 0.5)
        g = int(g1 + (g2 - g1) * t + 0.5)
        b = int(b1 + (b2 - b1) * t + 0.5)

    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))
    return f"{b:02X}{g:02X}{r:02X}"





# ── HSL interpolation ────────────────────────────────────────────────────────

def _interpolate_hsl(
    r1: int, g1: int, b1: int,
    r2: int, g2: int, b2: int,
    t: float,
) -> tuple[int, int, int]:
    h1, l1, s1 = colorsys.rgb_to_hls(r1 / 255, g1 / 255, b1 / 255)
    h2, l2, s2 = colorsys.rgb_to_hls(r2 / 255, g2 / 255, b2 / 255)

    # Shortest path hue interpolation
    dh = h2 - h1
    if dh > 0.5:
        dh -= 1.0
    elif dh < -0.5:
        dh += 1.0
    h = (h1 + dh * t) % 1.0
    l = l1 + (l2 - l1) * t
    s = s1 + (s2 - s1) * t

    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)


# ── OKLab interpolation ──────────────────────────────────────────────────────

def _srgb_to_linear(c: float) -> float:
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


def _rgb_to_oklab(r: int, g: int, b: int) -> tuple[float, float, float]:
    r_lin = _srgb_to_linear(r / 255)
    g_lin = _srgb_to_linear(g / 255)
    b_lin = _srgb_to_linear(b / 255)

    l_ = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
    m_ = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
    s_ = 0.0883024619 * r_lin + 0.2817188376 * g_lin + 0.6299787005 * b_lin

    l_ = l_ ** (1 / 3) if l_ >= 0 else -((-l_) ** (1 / 3))
    m_ = m_ ** (1 / 3) if m_ >= 0 else -((-m_) ** (1 / 3))
    s_ = s_ ** (1 / 3) if s_ >= 0 else -((-s_) ** (1 / 3))

    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_val = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, a, b_val


def _oklab_to_rgb(L: float, a: float, b_val: float) -> tuple[int, int, int]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b_val
    m_ = L - 0.1055613458 * a - 0.0638541728 * b_val
    s_ = L - 0.0894841775 * a - 1.2914855480 * b_val

    l_ = l_ * l_ * l_
    m_ = m_ * m_ * m_
    s_ = s_ * s_ * s_

    r_lin = +4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_
    g_lin = -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_
    b_lin = -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_

    r = _linear_to_srgb(max(0, r_lin))
    g = _linear_to_srgb(max(0, g_lin))
    b = _linear_to_srgb(max(0, b_lin))

    return (
        max(0, min(255, int(r * 255 + 0.5))),
        max(0, min(255, int(g * 255 + 0.5))),
        max(0, min(255, int(b * 255 + 0.5))),
    )


def _interpolate_oklab(
    r1: int, g1: int, b1: int,
    r2: int, g2: int, b2: int,
    t: float,
) -> tuple[int, int, int]:
    L1, a1, b1_val = _rgb_to_oklab(r1, g1, b1)
    L2, a2, b2_val = _rgb_to_oklab(r2, g2, b2)

    L = L1 + (L2 - L1) * t
    a = a1 + (a2 - a1) * t
    b_val = b1_val + (b2_val - b1_val) * t

    return _oklab_to_rgb(L, a, b_val)
