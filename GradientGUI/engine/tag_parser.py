"""
ASS override tag parser.

Extracts tag values from ASS dialogue text and provides defaults from styles.
Supports all 26 gradient-capable tags.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple, Dict
import re

from .ass_parser import ASSStyle


# ── Tag categories ────────────────────────────────────────────────────────────

COLOR_TAGS = ("1c", "2c", "3c", "4c")
ALPHA_TAGS = ("alpha", "1a", "2a", "3a", "4a")
NUMERIC_TAGS = (
    "fscx", "fscy", "fax", "fay",
    "frx", "fry", "frz",
    "bord", "xbord", "ybord",
    "shad", "xshad", "yshad",
    "fs", "fsp",
    "blur", "be",
)
COORD_TAGS = ("pos", "org", "fad")
TEXT_TAGS = ("fn",)

ALL_GRADIENT_TAGS = COLOR_TAGS + ALPHA_TAGS + NUMERIC_TAGS + COORD_TAGS + TEXT_TAGS

# ── Tag metadata ──────────────────────────────────────────────────────────────

TAG_INFO = {
    # Colors (BGR hex, 6 chars)
    "1c":    {"type": "color", "label": "\\1c  主色 (Fill)",         "group": "Color"},
    "2c":    {"type": "color", "label": "\\2c  二色 (Secondary)",   "group": "Color"},
    "3c":    {"type": "color", "label": "\\3c  边框 (Border)",      "group": "Color"},
    "4c":    {"type": "color", "label": "\\4c  阴影 (Shadow)",      "group": "Color"},
    # Alpha (hex 00-FF)
    "alpha": {"type": "alpha", "label": "\\alpha  全通道透明",       "group": "Alpha"},
    "1a":    {"type": "alpha", "label": "\\1a  主色透明",            "group": "Alpha"},
    "2a":    {"type": "alpha", "label": "\\2a  二色透明",            "group": "Alpha"},
    "3a":    {"type": "alpha", "label": "\\3a  边框透明",            "group": "Alpha"},
    "4a":    {"type": "alpha", "label": "\\4a  阴影透明",            "group": "Alpha"},
    # Scale / Shearing
    "fscx":  {"type": "numeric", "label": "\\fscx  X缩放",  "group": "Transform", "default": 100},
    "fscy":  {"type": "numeric", "label": "\\fscy  Y缩放",  "group": "Transform", "default": 100},
    "fax":   {"type": "numeric", "label": "\\fax  X错切",   "group": "Transform", "default": 0},
    "fay":   {"type": "numeric", "label": "\\fay  Y错切",   "group": "Transform", "default": 0},
    # Rotation
    "frx":   {"type": "numeric", "label": "\\frx  X旋转",   "group": "Transform", "default": 0},
    "fry":   {"type": "numeric", "label": "\\fry  Y旋转",   "group": "Transform", "default": 0},
    "frz":   {"type": "numeric", "label": "\\frz  Z旋转",   "group": "Transform", "default": 0},
    # Border / Shadow
    "bord":  {"type": "numeric", "label": "\\bord  边框",    "group": "Border/Shadow", "default": 0},
    "xbord": {"type": "numeric", "label": "\\xbord X边框",   "group": "Border/Shadow", "default": 0},
    "ybord": {"type": "numeric", "label": "\\ybord Y边框",   "group": "Border/Shadow", "default": 0},
    "shad":  {"type": "numeric", "label": "\\shad  阴影",    "group": "Border/Shadow", "default": 0},
    "xshad": {"type": "numeric", "label": "\\xshad X阴影",   "group": "Border/Shadow", "default": 0},
    "yshad": {"type": "numeric", "label": "\\yshad Y阴影",   "group": "Border/Shadow", "default": 0},
    # Size / Spacing
    "fs":    {"type": "numeric", "label": "\\fs   字号",     "group": "Size", "default": 48},
    "fsp":   {"type": "numeric", "label": "\\fsp  字距",     "group": "Size", "default": 0},
    # Effects
    "blur":  {"type": "numeric", "label": "\\blur 高斯模糊",  "group": "Effect", "default": 0},
    "be":    {"type": "numeric", "label": "\\be   边缘模糊",  "group": "Effect", "default": 0},
    "fad":   {"type": "coord",   "label": "\\fad  淡入淡出",  "group": "Effect", "default": (0.0, 0.0)},
    "fn":    {"type": "text",    "label": "\\fn   字体",      "group": "Style", "default": ""},
    # Position (x, y pairs)
    "pos":   {"type": "coord",   "label": "\\pos  位置",     "group": "Position", "default": (0.0, 0.0)},
    "org":   {"type": "coord",   "label": "\\org  旋转原点",  "group": "Position", "default": (0.0, 0.0)},
}


# ── Parse tags from dialogue text ─────────────────────────────────────────────

def parse_tags_from_text(text: str) -> dict:
    """
    Extract override tag values from ASS dialogue text.

    Returns dict of tag_name -> value:
        - color tags: "BBGGRR" (6-char hex string)
        - alpha tags: "FF" (2-char hex string)
        - numeric tags: float
        - coord tags: (x: float, y: float)
    """
    result: dict = {}

    # Collect all override block contents
    blocks = re.findall(r"\{([^}]*)\}", text)
    if not blocks:
        return result
    all_overrides = "".join(blocks)

    # Color tags: \1c&HBBGGRR& or \c&HBBGGRR&
    for tag in COLOR_TAGS:
        m = re.search(rf"\\{tag}&H([0-9A-Fa-f]{{6}})&", all_overrides)
        if m:
            result[tag] = m.group(1).upper()
    # \c is alias for \1c
    if "1c" not in result:
        m = re.search(r"\\c&H([0-9A-Fa-f]{6})&", all_overrides)
        if m:
            result["1c"] = m.group(1).upper()

    # Alpha tags: \alpha&HFF& or \1a&HFF&
    for tag in ALPHA_TAGS:
        m = re.search(rf"\\{tag}&H([0-9A-Fa-f]{{2}})&", all_overrides)
        if m:
            result[tag] = m.group(1).upper()

    # Numeric tags
    for tag in NUMERIC_TAGS:
        m = re.search(rf"\\{tag}(-?\d+\.?\d*)", all_overrides)
        if m:
            result[tag] = float(m.group(1))

    # Coordinate tags: \pos(x,y) \org(x,y)
    for tag in COORD_TAGS:
        m = re.search(rf"\\{tag}\(\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*\)", all_overrides)
        if m:
            result[tag] = (float(m.group(1)), float(m.group(2)))

    # Font name: \fnFontName (captures until \ or end of overrides)
    m = re.search(r"\\fn([^\\}]+)", all_overrides)
    if m:
        result["fn"] = m.group(1).strip()

    # Bold: \b0 or \b1 (or \b100-900 for weight)
    m = re.search(r"\\b(\d+)", all_overrides)
    if m:
        result["b"] = int(m.group(1)) > 0

    # Alignment: \an1-9
    m = re.search(r"\\an(\d)", all_overrides)
    if m:
        result["an"] = int(m.group(1))

    return result


def get_default_from_style(tag: str, style: Optional[ASSStyle]) -> object:
    """
    Get the default value for a tag from the style definition.
    Falls back to TAG_INFO defaults or zero.
    """
    if style is None:
        info = TAG_INFO.get(tag, {})
        return info.get("default", 0)

    style_map = {
        "1c":    style.primary_color,
        "2c":    style.secondary_color,
        "3c":    style.outline_color,
        "4c":    style.back_color,
        "fscx":  style.scale_x,
        "fscy":  style.scale_y,
        "fs":    style.fontsize,
        "fsp":   style.spacing,
        "bord":  style.outline,
        "shad":  style.shadow,
        "frz":   style.angle,
        "fn":    style.fontname,
    }

    if tag in style_map:
        val = style_map[tag]
        # Parse ASS color format &HAABBGGRR or &HBBGGRR&
        if tag in COLOR_TAGS and isinstance(val, str):
            return _parse_ass_color(val)
        return val

    # Alpha defaults (from style colors)
    alpha_style_map = {
        "1a": style.primary_color,
        "2a": style.secondary_color,
        "3a": style.outline_color,
        "4a": style.back_color,
    }
    if tag in alpha_style_map:
        return _parse_ass_alpha(alpha_style_map[tag])
    if tag == "alpha":
        return "00"

    info = TAG_INFO.get(tag, {})
    return info.get("default", 0)


def get_tag_value(tag: str, parsed_tags: dict, style: Optional[ASSStyle]) -> object:
    """Get effective tag value: override if present, else style default."""
    if tag in parsed_tags:
        return parsed_tags[tag]
    return get_default_from_style(tag, style)


# ── Extract Clip Paths ────────────────────────────────────────────────────────

def extract_clip_tags(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Extracts \1xlip() to \4xlip() custom tags for color sampling.
    Returns (cleaned_text, clips_dict).
    """
    clips = {}
    
    def replacer(match):
        num = match.group(1)
        path = match.group(2)
        clips[num + "c"] = path
        return ""
        
    cleaned_text = re.sub(r"\\([1-4])xlip\((.*?)\)", replacer, text)
    return cleaned_text, clips


def extract_clip_bounds(text: str) -> Optional[Tuple[float, float, float, float]]:
    """Return the bounding rectangle of the last literal \\clip tag, if any."""
    bounds: Optional[Tuple[float, float, float, float]] = None
    for match in re.finditer(r"\\clip\(([^)]*)\)", text):
        content = match.group(1).strip()
        if not content:
            continue

        normalized = content.replace(",", " ")
        tokens = normalized.split()
        has_path_cmd = any(tok.lower() in {"m", "n", "l", "b", "s", "p", "c"} for tok in tokens)

        try:
            if not has_path_cmd:
                nums = [float(tok) for tok in tokens[:4]]
                if len(nums) == 4:
                    x1, y1, x2, y2 = nums
                    bounds = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
                continue
        except ValueError:
            pass

        coord_tokens = tokens
        scale_factor = 1.0
        if coord_tokens:
            try:
                scale = float(coord_tokens[0])
                if len(coord_tokens) > 1 and coord_tokens[1].lower() in {"m", "n", "l", "b", "s", "p", "c"}:
                    scale_factor = float(2 ** max(int(scale) - 1, 0))
                    coord_tokens = coord_tokens[1:]
            except ValueError:
                pass

        coords: list[float] = []
        for tok in coord_tokens:
            if tok.lower() in {"m", "n", "l", "b", "s", "p", "c"}:
                continue
            try:
                coords.append(float(tok) / scale_factor)
            except ValueError:
                continue

        xs = coords[0::2]
        ys = coords[1::2]
        if xs and ys:
            bounds = (min(xs), min(ys), max(xs), max(ys))
    return bounds


def get_bord_sizes(text: str, style: Optional[ASSStyle]) -> Tuple[float, float]:
    """Get effective border sizes (xbord, ybord)."""
    parsed = parse_tags_from_text(text)
    style_bord = style.outline if style else 0
    bord = parsed.get("bord")
    xbord = parsed.get("xbord", bord if bord is not None else style_bord)
    ybord = parsed.get("ybord", bord if bord is not None else style_bord)
    return max(0.0, float(xbord)), max(0.0, float(ybord))


def get_shad_offsets(text: str, style: Optional[ASSStyle]) -> Tuple[float, float]:
    """Get effective shadow offsets (xshad, yshad)."""
    parsed = parse_tags_from_text(text)
    style_shad = style.shadow if style else 0
    shad = parsed.get("shad")
    xshad = parsed.get("xshad", shad if shad is not None else style_shad)
    yshad = parsed.get("yshad", shad if shad is not None else style_shad)
    return xshad, yshad


# ── Strip text of override tags ───────────────────────────────────────────────

def strip_tags(text: str) -> str:
    """Remove all override blocks {...} from ASS text."""
    return re.sub(r"\{[^}]*\}", "", text)


def remove_specific_tag(text: str, tag: str) -> str:
    """Remove a specific override tag from ASS text."""
    if tag in COORD_TAGS:
        return re.sub(rf"\\{tag}\([^)]*\)", "", text)
    elif tag in TEXT_TAGS:
        return re.sub(rf"\\{tag}[^\\}}]*", "", text)
    elif tag in COLOR_TAGS:
        return re.sub(rf"\\{tag}&H[0-9A-Fa-f]*&", "", text)
    elif tag in ALPHA_TAGS:
        return re.sub(rf"\\{tag}&H[0-9A-Fa-f]*&", "", text)
    else:
        return re.sub(rf"\\{tag}-?\d+\.?\d*", "", text)


def build_tag_string(tag: str, value) -> str:
    """Build an ASS override tag string from a tag name and value."""
    info = TAG_INFO.get(tag, {})
    tag_type = info.get("type", "numeric")

    if tag_type == "color":
        if isinstance(value, str):
            return f"\\{tag}&H{value}&"
        # value is (b, g, r) tuple
        b, g, r = int(value[0]), int(value[1]), int(value[2])
        return f"\\{tag}&H{b:02X}{g:02X}{r:02X}&"

    elif tag_type == "alpha":
        if isinstance(value, str):
            return f"\\{tag}&H{value}&"
        return f"\\{tag}&H{int(value):02X}&"

    elif tag_type == "coord":
        x, y = value
        if tag == "fad":
            return f"\\fad({int(round(float(x)))},{int(round(float(y)))})"
        return f"\\{tag}({x:.2f},{y:.2f})"

    elif tag_type == "text":
        value = "" if value is None else str(value)
        return f"\\{tag}{value}"

    else:  # numeric
        def _fmt_numeric(v) -> str:
            if isinstance(v, float) and v == int(v):
                return str(int(v))
            return f"{float(v):.2f}"

        if tag in {"bord", "xbord", "ybord", "blur", "be"}:
            value = max(0.0, float(value))
        elif tag == "fs":
            value = max(1.0, float(value))
        if tag == "shad" and float(value) < 0:
            value_str = _fmt_numeric(value)
            return f"\\xshad{value_str}\\yshad{value_str}"
        return f"\\{tag}{_fmt_numeric(value)}"


# ── Color helpers ─────────────────────────────────────────────────────────────

def _parse_ass_color(s: str) -> str:
    """
    Parse ASS color (&HAABBGGRR or &HBBGGRR& or &HBBGGRR) → "BBGGRR".
    """
    s = s.strip().replace("&", "").replace("H", "").replace("h", "")
    if len(s) >= 8:
        return s[2:8].upper()  # skip AA
    if len(s) >= 6:
        return s[:6].upper()
    return s.ljust(6, "0").upper()


def _parse_ass_alpha(s: str) -> str:
    """
    Extract alpha component from ASS color (&HAABBGGRR) → "AA".
    """
    s = s.strip().replace("&", "").replace("H", "").replace("h", "")
    if len(s) >= 8:
        return s[:2].upper()
    return "00"


def ass_color_to_rgb(bgr_hex: str) -> tuple[int, int, int]:
    """Convert "BBGGRR" to (R, G, B)."""
    bgr = bgr_hex.ljust(6, "0")
    b = int(bgr[0:2], 16)
    g = int(bgr[2:4], 16)
    r = int(bgr[4:6], 16)
    return (r, g, b)


def rgb_to_ass_color(r: int, g: int, b: int) -> str:
    """Convert (R, G, B) to "BBGGRR"."""
    return f"{b:02X}{g:02X}{r:02X}"
