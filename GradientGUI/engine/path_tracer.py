import math
from collections import OrderedDict
from typing import Tuple, List, Dict, Iterable, Mapping, Any

RawPathSample = Tuple[int, int, int, str]

def record_coord(ix: float, iy: float, cosA: float, sinA: float, coord_map: Dict[int, Tuple[int, int]]):
    """Record coordinates for the gradient path, deduplicating along the gradient axis."""
    ix = math.floor(ix + 0.5)
    iy = math.floor(iy + 0.5)
    key = math.floor(ix * cosA + iy * sinA + 0.5)
    if key not in coord_map:
        coord_map[key] = (int(ix), int(iy))

def record_raw_coord(
    ix: float,
    iy: float,
    segment_index: int,
    points: list[tuple[int, int, int]],
    seen: set[tuple[int, int]],
):
    ix = int(math.floor(ix + 0.5))
    iy = int(math.floor(iy + 0.5))
    key = (ix, iy)
    if key not in seen:
        seen.add(key)
        points.append((int(segment_index), ix, iy))

def trace_line_seg(x0: float, y0: float, x1: float, y1: float, cosA: float, sinA: float, coord_map: Dict[int, Tuple[int, int]]):
    dx = x1 - x0
    dy = y1 - y0
    dist = math.sqrt(dx*dx + dy*dy)
    if dist < 0.0001:
        return

    steps = max(math.ceil(dist * 3), 1)
    for i in range(steps + 1):
        t = i / steps
        record_coord(x0 + t*dx, y0 + t*dy, cosA, sinA, coord_map)

def trace_line_seg_raw(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    segment_index: int,
    points: list[tuple[int, int, int]],
    seen: set[tuple[int, int]],
):
    dx = x1 - x0
    dy = y1 - y0
    dist = math.sqrt(dx*dx + dy*dy)
    if dist < 0.0001:
        return

    steps = max(math.ceil(dist * 3), 1)
    for i in range(steps + 1):
        t = i / steps
        record_raw_coord(x0 + t*dx, y0 + t*dy, segment_index, points, seen)

def trace_bezier_seg(x0: float, y0: float, cx1: float, cy1: float, cx2: float, cy2: float, x1: float, y1: float, cosA: float, sinA: float, coord_map: Dict[int, Tuple[int, int]]):
    len_approx = (math.sqrt((cx1-x0)**2 + (cy1-y0)**2) + 
                  math.sqrt((cx2-cx1)**2 + (cy2-cy1)**2) + 
                  math.sqrt((x1-cx2)**2 + (y1-cy2)**2))
    steps = max(math.ceil(len_approx * 5), 200)
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        fx = u**3 * x0 + 3 * u**2 * t * cx1 + 3 * u * t**2 * cx2 + t**3 * x1
        fy = u**3 * y0 + 3 * u**2 * t * cy1 + 3 * u * t**2 * cy2 + t**3 * y1
        record_coord(fx, fy, cosA, sinA, coord_map)

def trace_bezier_seg_raw(
    x0: float,
    y0: float,
    cx1: float,
    cy1: float,
    cx2: float,
    cy2: float,
    x1: float,
    y1: float,
    segment_index: int,
    points: list[tuple[int, int, int]],
    seen: set[tuple[int, int]],
):
    len_approx = (math.sqrt((cx1-x0)**2 + (cy1-y0)**2) +
                  math.sqrt((cx2-cx1)**2 + (cy2-cy1)**2) +
                  math.sqrt((x1-cx2)**2 + (y1-cy2)**2))
    steps = max(math.ceil(len_approx * 5), 200)
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        fx = u**3 * x0 + 3 * u**2 * t * cx1 + 3 * u * t**2 * cx2 + t**3 * x1
        fy = u**3 * y0 + 3 * u**2 * t * cy1 + 3 * u * t**2 * cy2 + t**3 * y1
        record_raw_coord(fx, fy, segment_index, points, seen)

def split_path_segments(path_str: str) -> List[str]:
    """Split a drawing path string into sub-paths at each 'm' command."""
    segments = []
    current = []
    tokens = path_str.split()
    for tok in tokens:
        if tok in ("m", "n"):
            if current:
                segments.append(" ".join(current))
            current = [tok]
        else:
            current.append(tok)
    if current:
        segments.append(" ".join(current))
    return segments

def trace_path_str(path_str: str, cosA: float, sinA: float, coord_map: Dict[int, Tuple[int, int]]):
    curX, curY = 0.0, 0.0
    tokens = path_str.split()
    n = len(tokens)
    i = 0
    cmd = None
    
    while i < n:
        tok = tokens[i]
        if tok in ("m", "n", "l", "b", "s", "c", "p"):
            cmd = tok
            i += 1
        elif cmd in ("m", "n"):
            try:
                x, y = float(tokens[i]), float(tokens[i+1])
                curX, curY = x, y
                record_coord(x, y, cosA, sinA, coord_map)
                i += 2
            except (ValueError, IndexError):
                i += 1
        elif cmd == "l":
            try:
                x, y = float(tokens[i]), float(tokens[i+1])
                trace_line_seg(curX, curY, x, y, cosA, sinA, coord_map)
                curX, curY = x, y
                i += 2
            except (ValueError, IndexError):
                i += 1
        elif cmd == "b":
            if i + 5 < n:
                try:
                    bx1, by1 = float(tokens[i]), float(tokens[i+1])
                    bx2, by2 = float(tokens[i+2]), float(tokens[i+3])
                    ex, ey = float(tokens[i+4]), float(tokens[i+5])
                    trace_bezier_seg(curX, curY, bx1, by1, bx2, by2, ex, ey, cosA, sinA, coord_map)
                    curX, curY = ex, ey
                    i += 6
                except ValueError:
                    i += 1
            else:
                i += 1
        else:
            i += 1

def trace_path_str_raw(
    path_str: str,
    segment_index: int,
    points: list[tuple[int, int, int]],
):
    curX, curY = 0.0, 0.0
    tokens = path_str.split()
    n = len(tokens)
    i = 0
    cmd = None
    seen: set[tuple[int, int]] = set()

    while i < n:
        tok = tokens[i]
        if tok in ("m", "n", "l", "b", "s", "c", "p"):
            cmd = tok
            i += 1
        elif cmd in ("m", "n"):
            try:
                x, y = float(tokens[i]), float(tokens[i+1])
                curX, curY = x, y
                record_raw_coord(x, y, segment_index, points, seen)
                i += 2
            except (ValueError, IndexError):
                i += 1
        elif cmd == "l":
            try:
                x, y = float(tokens[i]), float(tokens[i+1])
                trace_line_seg_raw(curX, curY, x, y, segment_index, points, seen)
                curX, curY = x, y
                i += 2
            except (ValueError, IndexError):
                i += 1
        elif cmd == "b":
            if i + 5 < n:
                try:
                    bx1, by1 = float(tokens[i]), float(tokens[i+1])
                    bx2, by2 = float(tokens[i+2]), float(tokens[i+3])
                    ex, ey = float(tokens[i+4]), float(tokens[i+5])
                    trace_bezier_seg_raw(
                        curX, curY, bx1, by1, bx2, by2, ex, ey,
                        segment_index, points, seen,
                    )
                    curX, curY = ex, ey
                    i += 6
                except ValueError:
                    i += 1
            else:
                i += 1
        else:
            i += 1

def project_bounds(bx1: float, by1: float, bx2: float, by2: float, cosA: float, sinA: float) -> Tuple[float, float, float, float]:
    """Projects an axis-aligned bounding box onto gradient / perpendicular axes.
    Returns gMin, gMax (gradient range), pMin, pMax (perpendicular range)."""
    gMin, gMax = float('inf'), float('-inf')
    pMin, pMax = float('inf'), float('-inf')
    
    corners = [(bx1, by1), (bx2, by1), (bx2, by2), (bx1, by2)]
    for cx, cy in corners:
        g = cx * cosA + cy * sinA
        p = -cx * sinA + cy * cosA
        gMin = min(gMin, g)
        gMax = max(gMax, g)
        pMin = min(pMin, p)
        pMax = max(pMax, p)
        
    return gMin, gMax, pMin, pMax

def build_color_map_from_path(path_str: str, cosA: float, sinA: float, get_pixel_fn) -> Tuple[Dict[int, str], List[int]]:
    """
    Builds a list of sampled colors along the gradient path.
    get_pixel_fn: function(x, y) -> str ("BBGGRR")
    Returns color_map and sorted keys.
    """
    samples = sample_path_points_from_path(path_str, get_pixel_fn)
    return project_sampled_path_points(samples, cosA, sinA)


def trace_path_points(path_str: str) -> list[tuple[int, int, int]]:
    """Trace an ASS path into integer video coordinates without sampling colors."""
    points: list[tuple[int, int, int]] = []
    for segment_index, segment in enumerate(split_path_segments(path_str)):
        trace_path_str_raw(segment, segment_index, points)
    return points


def sample_path_points_from_path(path_str: str, get_pixel_fn) -> list[RawPathSample]:
    """Sample colors at integer path coordinates and keep the raw coordinates.

    The saved raw samples are direction-independent. Horizontal, Vertical, and
    angled gradients can reproject the same snapshot without reopening the
    path editor or reading the video frame again.
    """
    samples: list[RawPathSample] = []
    for segment_index, x, y in trace_path_points(path_str):
        color_bgr = get_pixel_fn(x, y)
        if color_bgr:
            samples.append((int(segment_index), int(x), int(y), str(color_bgr)))
    return samples


def _coerce_raw_sample(sample: Any) -> RawPathSample | None:
    if isinstance(sample, Mapping):
        segment = sample.get("segment", sample.get("segment_index", 0))
        x = sample.get("x")
        y = sample.get("y")
        color = sample.get("color", sample.get("value", ""))
    elif isinstance(sample, (list, tuple)) and len(sample) >= 4:
        segment, x, y, color = sample[0], sample[1], sample[2], sample[3]
    elif isinstance(sample, (list, tuple)) and len(sample) >= 3:
        segment, x, y, color = 0, sample[0], sample[1], sample[2]
    else:
        return None
    try:
        segment_i = int(segment)
        x_i = int(x)
        y_i = int(y)
    except (TypeError, ValueError):
        return None
    color_text = str(color or "").strip()
    if not color_text:
        return None
    return segment_i, x_i, y_i, color_text


def normalize_raw_path_samples(samples: Iterable[Any]) -> list[RawPathSample]:
    normalized: list[RawPathSample] = []
    for sample in samples or []:
        parsed = _coerce_raw_sample(sample)
        if parsed is not None:
            normalized.append(parsed)
    return normalized


def project_sampled_path_points(
    samples: Iterable[Any],
    cosA: float,
    sinA: float,
) -> Tuple[Dict[int, str], List[int]]:
    """Project saved raw samples onto the current gradient direction."""
    normalized = normalize_raw_path_samples(samples)
    if not normalized:
        return {}, []

    by_segment: OrderedDict[int, OrderedDict[int, str]] = OrderedDict()
    for segment, x, y, color in normalized:
        coord_map = by_segment.setdefault(int(segment), OrderedDict())
        key = math.floor(int(x) * cosA + int(y) * sinA + 0.5)
        if key not in coord_map:
            coord_map[key] = color

    all_colors: list[str] = []
    for coord_map in by_segment.values():
        for key in sorted(coord_map):
            all_colors.append(coord_map[key])

    color_map: Dict[int, str] = {}
    keys: List[int] = []
    for i, color in enumerate(all_colors):
        color_map[i + 1] = color
        keys.append(i + 1)
    return color_map, keys


def _clamp_smooth_strength(strength: float) -> float:
    try:
        value = float(strength)
    except (TypeError, ValueError):
        value = 1.0
    return max(0.0, min(1.0, value))


def _hex_to_rgb(hx: str) -> tuple[int, int, int]:
    return int(hx[4:6], 16), int(hx[2:4], 16), int(hx[0:2], 16)


def _rgb_to_bgr(r: int, g: int, b: int) -> str:
    r = max(0, min(255, int(r)))
    g = max(0, min(255, int(g)))
    b = max(0, min(255, int(b)))
    return f"{b:02X}{g:02X}{r:02X}"


def get_color_by_ratio(
    color_map: Dict[int, str],
    keys: List[int],
    t: float,
    smooth: bool,
    smooth_strength: float = 1.0,
) -> str:
    """Gets the BGR color from the sampled path color map for ratio t [0, 1]."""
    n = len(keys)
    if n == 0:
        return "FFFFFF"
    if n == 1:
        return color_map[keys[0]]
        
    t = max(0.0, min(1.0, t))
    fidx = t * (n - 1) + 1
    lo = max(1, min(n, math.floor(fidx)))
    hi = min(n, lo + 1)
    
    chosen = lo if (fidx - lo) < 0.5 else hi
    nearest = color_map[keys[chosen - 1]]

    strength = _clamp_smooth_strength(smooth_strength)
    if not smooth or strength <= 0.0:
        return nearest

    frac = fidx - lo
    c1 = color_map[keys[lo - 1]]
    c2 = color_map[keys[hi - 1]]
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    smooth_r = r1 + frac * (r2 - r1)
    smooth_g = g1 + frac * (g2 - g1)
    smooth_b = b1 + frac * (b2 - b1)

    nr, ng, nb = _hex_to_rgb(nearest)
    r = nr + (smooth_r - nr) * strength
    g = ng + (smooth_g - ng) * strength
    b = nb + (smooth_b - nb) * strength
    return _rgb_to_bgr(round(r), round(g), round(b))
