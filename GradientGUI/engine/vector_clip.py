"""Vector clip splitting helpers.

ASS only allows one clip tag per line, so preserving a source vector clip/iclip
while generating gradient strips requires combining the source mask with each
generated strip shape.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional

Point = tuple[float, float]
Polygon = list[Point]

_TOKEN_RE = re.compile(r"[mnlbspcMNLBSPC]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)")
_COMMANDS = {"m", "n", "l", "b", "s", "p", "c"}
_EPS = 1e-6


@dataclass(frozen=True)
class SourceVectorClip:
    polygons: tuple[tuple[Point, ...], ...]
    inverse: bool = False


def extract_source_vector_clip(text: str) -> Optional[SourceVectorClip]:
    r"""Return the last vector \clip/\iclip from an ASS event, if any."""

    source: Optional[SourceVectorClip] = None
    for match in re.finditer(r"\\(i?)clip\(([^)]*)\)", text or ""):
        inverse = bool(match.group(1))
        parsed = parse_vector_clip_content(match.group(2), inverse=inverse)
        if parsed is None and inverse:
            parsed = _parse_rect_clip_content(match.group(2), inverse=True)
        if parsed is not None:
            source = parsed
    return source


def parse_vector_clip_content(
    content: str,
    inverse: bool = False,
) -> Optional[SourceVectorClip]:
    tokens = _TOKEN_RE.findall((content or "").replace(",", " "))
    if not any(_is_command(tok) for tok in tokens):
        return None

    index = 0
    factor = 1.0
    if len(tokens) >= 2 and _is_number(tokens[0]) and _is_command(tokens[1]):
        try:
            scale = int(float(tokens[0]))
        except ValueError:
            scale = 1
        factor = float(2 ** max(scale - 1, 0))
        index = 1

    polygons: list[Polygon] = []
    current: Polygon = []
    cur_x = cur_y = 0.0
    start: Point | None = None
    cmd: str | None = None

    def finish_current() -> None:
        nonlocal current, start
        cleaned = _clean_polygon(current)
        if len(cleaned) >= 3 and abs(_signed_area(cleaned)) > 0.1:
            polygons.append(cleaned)
        current = []
        start = None

    while index < len(tokens):
        tok = tokens[index]
        if _is_command(tok):
            cmd = tok.lower()
            index += 1
            if cmd == "c":
                if start is not None and current:
                    current.append(start)
                finish_current()
                cmd = None
            continue

        if cmd in {"m", "n"}:
            point = _read_point(tokens, index, factor)
            if point is None:
                index += 1
                continue
            if current:
                finish_current()
            cur_x, cur_y = point
            start = point
            current = [point]
            index += 2
            cmd = "l"
            continue

        if cmd in {"l", "s", "p"}:
            point = _read_point(tokens, index, factor)
            if point is None:
                index += 1
                continue
            cur_x, cur_y = point
            current.append(point)
            index += 2
            continue

        if cmd == "b":
            if index + 5 >= len(tokens):
                break
            values = _read_numbers(tokens, index, 6, factor)
            if values is None:
                index += 1
                continue
            cx1, cy1, cx2, cy2, ex, ey = values
            current.extend(
                _flatten_cubic(
                    (cur_x, cur_y),
                    (cx1, cy1),
                    (cx2, cy2),
                    (ex, ey),
                )
            )
            cur_x, cur_y = ex, ey
            index += 6
            continue

        index += 1

    if current:
        finish_current()

    if not polygons:
        return None
    return SourceVectorClip(tuple(tuple(poly) for poly in polygons), inverse=bool(inverse))


def vector_clip_tag_for_strip(
    source: SourceVectorClip,
    strip_polygon: Iterable[Point],
    *,
    source_offset: Point = (0.0, 0.0),
) -> str | None:
    """Build a vector clip for source vector clip/iclip combined with one strip."""

    clipper = _clean_polygon(list(strip_polygon))
    if len(clipper) < 3 or abs(_signed_area(clipper)) <= _EPS:
        return None

    if source.inverse:
        return _inverse_vector_clip_tag_for_strip(source, clipper, source_offset)

    dx, dy = source_offset
    clipped_polygons: list[Polygon] = []
    for polygon in source.polygons:
        shifted = [(x + dx, y + dy) for x, y in polygon]
        clipped = _clip_polygon_to_convex(shifted, clipper)
        clipped = _clean_polygon(clipped)
        if len(clipped) >= 3 and abs(_signed_area(clipped)) > 0.1:
            clipped_polygons.append(clipped)

    return _vector_clip_from_polygons(clipped_polygons)


def _inverse_vector_clip_tag_for_strip(
    source: SourceVectorClip,
    strip_polygon: Polygon,
    source_offset: Point,
) -> str | None:
    """Build strip minus source-vector area as a vector clip with holes."""

    dx, dy = source_offset
    holes: list[Polygon] = []
    strip_area = abs(_signed_area(strip_polygon))
    hole_area = 0.0
    for polygon in source.polygons:
        shifted = [(x + dx, y + dy) for x, y in polygon]
        clipped = _clip_polygon_to_convex(shifted, strip_polygon)
        clipped = _clean_polygon(clipped)
        area = abs(_signed_area(clipped)) if len(clipped) >= 3 else 0.0
        if area > 0.1:
            holes.append(clipped)
            hole_area += area

    if not holes:
        return _vector_clip_from_polygons([strip_polygon])
    if hole_area >= strip_area - 0.1:
        return None

    outer = _with_orientation(strip_polygon, positive=True)
    polygons = [outer] + [
        _with_orientation(hole, positive=False)
        for hole in holes
    ]
    return _vector_clip_from_polygons(polygons)


def _parse_rect_clip_content(
    content: str,
    inverse: bool,
) -> Optional[SourceVectorClip]:
    tokens = (content or "").replace(",", " ").split()
    if any(tok.lower() in _COMMANDS for tok in tokens):
        return None
    try:
        nums = [float(tok) for tok in tokens[:4]]
    except ValueError:
        return None
    if len(nums) != 4:
        return None
    x1, y1, x2, y2 = nums
    left, right = min(x1, x2), max(x1, x2)
    top, bottom = min(y1, y2), max(y1, y2)
    if right <= left + _EPS or bottom <= top + _EPS:
        return None
    polygon: Polygon = [(left, top), (right, top), (right, bottom), (left, bottom)]
    return SourceVectorClip((tuple(polygon),), inverse=bool(inverse))


def _is_command(token: str) -> bool:
    return token.lower() in _COMMANDS


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def _read_point(tokens: list[str], index: int, factor: float) -> Point | None:
    values = _read_numbers(tokens, index, 2, factor)
    if values is None:
        return None
    return values[0], values[1]


def _read_numbers(
    tokens: list[str],
    index: int,
    count: int,
    factor: float,
) -> tuple[float, ...] | None:
    if index + count > len(tokens):
        return None
    values: list[float] = []
    for token in tokens[index:index + count]:
        if _is_command(token):
            return None
        try:
            values.append(float(token) / max(factor, 1.0))
        except ValueError:
            return None
    return tuple(values)


def _flatten_cubic(p0: Point, p1: Point, p2: Point, p3: Point) -> list[Point]:
    length = (
        math.dist(p0, p1)
        + math.dist(p1, p2)
        + math.dist(p2, p3)
    )
    steps = max(8, min(64, int(math.ceil(length / 8.0))))
    points: list[Point] = []
    for idx in range(1, steps + 1):
        t = idx / steps
        u = 1.0 - t
        x = (
            u * u * u * p0[0]
            + 3 * u * u * t * p1[0]
            + 3 * u * t * t * p2[0]
            + t * t * t * p3[0]
        )
        y = (
            u * u * u * p0[1]
            + 3 * u * u * t * p1[1]
            + 3 * u * t * t * p2[1]
            + t * t * t * p3[1]
        )
        points.append((x, y))
    return points


def _clean_polygon(points: Polygon) -> Polygon:
    cleaned: Polygon = []
    for x, y in points:
        point = (float(x), float(y))
        if not cleaned or math.dist(cleaned[-1], point) > _EPS:
            cleaned.append(point)
    if len(cleaned) > 1 and math.dist(cleaned[0], cleaned[-1]) <= _EPS:
        cleaned.pop()
    return cleaned


def _with_orientation(points: Polygon, positive: bool) -> Polygon:
    polygon = _clean_polygon(points)
    if len(polygon) < 3:
        return polygon
    area = _signed_area(polygon)
    if (area >= 0.0) != positive:
        polygon = list(reversed(polygon))
    return polygon


def _signed_area(points: Polygon) -> float:
    area = 0.0
    for idx, (x1, y1) in enumerate(points):
        x2, y2 = points[(idx + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _clip_polygon_to_convex(subject: Polygon, clipper: Polygon) -> Polygon:
    output = list(subject)
    clip_area = _signed_area(clipper)
    if abs(clip_area) <= _EPS:
        return []

    for idx, edge_start in enumerate(clipper):
        edge_end = clipper[(idx + 1) % len(clipper)]
        input_points = output
        output = []
        if not input_points:
            break
        previous = input_points[-1]
        previous_inside = _inside(previous, edge_start, edge_end, clip_area)
        for current in input_points:
            current_inside = _inside(current, edge_start, edge_end, clip_area)
            if current_inside:
                if not previous_inside:
                    output.append(_line_intersection(previous, current, edge_start, edge_end))
                output.append(current)
            elif previous_inside:
                output.append(_line_intersection(previous, current, edge_start, edge_end))
            previous = current
            previous_inside = current_inside
    return output


def _inside(point: Point, edge_start: Point, edge_end: Point, clip_area: float) -> bool:
    cross = (
        (edge_end[0] - edge_start[0]) * (point[1] - edge_start[1])
        - (edge_end[1] - edge_start[1]) * (point[0] - edge_start[0])
    )
    return cross >= -_EPS if clip_area > 0 else cross <= _EPS


def _line_intersection(p1: Point, p2: Point, q1: Point, q2: Point) -> Point:
    dx1 = p2[0] - p1[0]
    dy1 = p2[1] - p1[1]
    dx2 = q2[0] - q1[0]
    dy2 = q2[1] - q1[1]
    den = dx1 * dy2 - dy1 * dx2
    if abs(den) <= _EPS:
        return p2
    t = ((q1[0] - p1[0]) * dy2 - (q1[1] - p1[1]) * dx2) / den
    return p1[0] + t * dx1, p1[1] + t * dy1


def _vector_clip_from_polygons(polygons: list[Polygon]) -> str | None:
    path_parts: list[str] = []
    for polygon in polygons:
        polygon = _clean_polygon(polygon)
        if len(polygon) < 3 or abs(_signed_area(polygon)) <= 0.1:
            continue
        first_x, first_y = polygon[0]
        path_parts.append(f"m {_fmt(first_x)} {_fmt(first_y)}")
        for x, y in polygon[1:]:
            path_parts.append(f"l {_fmt(x)} {_fmt(y)}")
    if not path_parts:
        return None
    return r"\clip(1," + " ".join(path_parts) + ")"


def _fmt(value: float) -> str:
    return str(int(round(value)))
