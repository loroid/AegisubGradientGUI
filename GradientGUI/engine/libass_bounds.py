"""
libass-backed subtitle bounds.

The gradient engine needs the pixels a viewer can actually see, not a font
layout box. This module calls libass through ctypes, renders a single event,
and scans libass' alpha bitmaps for a tight visible pixel rectangle.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from .ass_parser import ASSFile, ASSEvent, EVENT_FIELDS, STYLE_FIELDS, time_to_seconds
from .tag_parser import parse_tags_from_text


IMAGE_TYPE_CHARACTER = 0
IMAGE_TYPE_OUTLINE = 1
IMAGE_TYPE_SHADOW = 2
VISIBLE_ALPHA_THRESHOLD = 8


class LibassBoundsError(RuntimeError):
    """Raised when libass cannot be loaded or cannot render a measurement."""


class _ASSImage(ctypes.Structure):
    pass


_ASSImagePtr = ctypes.POINTER(_ASSImage)
_ASSImage._fields_ = [
    ("w", ctypes.c_int),
    ("h", ctypes.c_int),
    ("stride", ctypes.c_int),
    ("bitmap", ctypes.POINTER(ctypes.c_ubyte)),
    ("color", ctypes.c_uint32),
    ("dst_x", ctypes.c_int),
    ("dst_y", ctypes.c_int),
    ("next", _ASSImagePtr),
    ("type", ctypes.c_int),
]

_ASSMessageCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_int,
    ctypes.c_char_p,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


def _quiet_message_callback(level, fmt, args, data) -> None:
    return None


_QUIET_MESSAGE_CALLBACK = _ASSMessageCallback(_quiet_message_callback)


@dataclass(frozen=True)
class RenderBounds:
    """A libass-measured text rectangle plus its ASS positioning anchor."""

    x1: float
    y1: float
    x2: float
    y2: float
    alignment: int
    pos_x: float
    pos_y: float
    org_x: float
    org_y: float

    def to_meta(self) -> str:
        return (
            f"{self.x1:.6f},{self.y1:.6f},{self.x2:.6f},{self.y2:.6f},"
            f"{self.alignment},{self.pos_x:.6f},{self.pos_y:.6f},"
            f"{self.org_x:.6f},{self.org_y:.6f}"
        )


_DLL_DIR_HANDLES = []


def measure_event_bounds(
    ass_file: ASSFile,
    event: ASSEvent,
    now_ms: Optional[int] = None,
    image_types: Iterable[int] = (IMAGE_TYPE_CHARACTER,),
    alpha_threshold: int = VISIBLE_ALPHA_THRESHOLD,
) -> Optional[RenderBounds]:
    """
    Render one event with libass and return the tight visible-pixel bounds.

    By default only fill/character images are scanned. The gradient engine then
    expands this base rectangle for border and shadow channels as needed.
    """

    lib = _load_libass()
    width = max(int(ass_file.play_res_x or 0), 1)
    height = max(int(ass_file.play_res_y or 0), 1)
    render_ms = int(time_to_seconds(event.start) * 1000) if now_ms is None else int(now_ms)
    ass_text = _build_single_event_ass(ass_file, event)

    library = lib.ass_library_init()
    if not library:
        raise LibassBoundsError("ass_library_init failed")

    renderer = None
    track = None
    try:
        _configure_library(lib, library)

        renderer = lib.ass_renderer_init(library)
        if not renderer:
            raise LibassBoundsError("ass_renderer_init failed")

        lib.ass_set_frame_size(renderer, width, height)
        lib.ass_set_storage_size(renderer, width, height)
        lib.ass_set_fonts(renderer, None, b"Arial", 1, None, 1)

        data = ass_text.encode("utf-8-sig")
        buffer = ctypes.create_string_buffer(data)
        track = lib.ass_read_memory(library, buffer, len(data), b"UTF-8")
        if not track:
            raise LibassBoundsError("ass_read_memory failed")

        changed = ctypes.c_int(0)
        image_list = lib.ass_render_frame(renderer, track, render_ms, ctypes.byref(changed))

        bounds = _scan_image_list(image_list, set(image_types), alpha_threshold)
        if bounds is None:
            bounds = _scan_image_list(image_list, None, alpha_threshold)
        if bounds is None:
            return None

        alignment, pos_x, pos_y, org_x, org_y = _event_anchor(ass_file, event, render_ms)
        x1, y1, x2, y2 = bounds
        return RenderBounds(x1, y1, x2, y2, alignment, pos_x, pos_y, org_x, org_y)
    finally:
        if track:
            lib.ass_free_track(track)
        if renderer:
            lib.ass_renderer_done(renderer)
        lib.ass_library_done(library)


def check_libass_available() -> str:
    """Load libass and create/destroy a library handle without checking fonts."""

    lib = _load_libass()
    library = lib.ass_library_init()
    if not library:
        raise LibassBoundsError("ass_library_init failed")
    lib.ass_library_done(library)
    return str(_find_ass_dll())


@lru_cache(maxsize=1)
def _load_libass():
    dll_path = _find_ass_dll()
    dll_dir = dll_path.parent

    if hasattr(os, "add_dll_directory"):
        for path in _dll_search_dirs(dll_dir):
            if path.exists():
                _DLL_DIR_HANDLES.append(os.add_dll_directory(str(path)))

    os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")

    try:
        lib = ctypes.CDLL(str(dll_path))
    except OSError as exc:
        raise LibassBoundsError(f"failed to load {dll_path}: {exc}") from exc

    _bind_libass(lib)
    return lib


def _bind_libass(lib) -> None:
    lib.ass_library_init.argtypes = []
    lib.ass_library_init.restype = ctypes.c_void_p
    lib.ass_library_done.argtypes = [ctypes.c_void_p]
    lib.ass_library_done.restype = None

    lib.ass_set_fonts_dir.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    lib.ass_set_fonts_dir.restype = None
    lib.ass_set_extract_fonts.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.ass_set_extract_fonts.restype = None
    lib.ass_set_message_cb.argtypes = [ctypes.c_void_p, _ASSMessageCallback, ctypes.c_void_p]
    lib.ass_set_message_cb.restype = None

    lib.ass_renderer_init.argtypes = [ctypes.c_void_p]
    lib.ass_renderer_init.restype = ctypes.c_void_p
    lib.ass_renderer_done.argtypes = [ctypes.c_void_p]
    lib.ass_renderer_done.restype = None

    lib.ass_set_frame_size.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.ass_set_frame_size.restype = None
    lib.ass_set_storage_size.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.ass_set_storage_size.restype = None
    lib.ass_set_fonts.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ]
    lib.ass_set_fonts.restype = None

    lib.ass_read_memory.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    lib.ass_read_memory.restype = ctypes.c_void_p
    lib.ass_free_track.argtypes = [ctypes.c_void_p]
    lib.ass_free_track.restype = None

    lib.ass_render_frame.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_longlong,
        ctypes.POINTER(ctypes.c_int),
    ]
    lib.ass_render_frame.restype = _ASSImagePtr


def _find_ass_dll() -> Path:
    if os.name == "nt":
        names = ("ass.dll", "libass.dll")
    elif sys.platform == "darwin":
        names = ("libass.dylib", "libass.9.dylib")
    else:
        names = ("libass.so", "libass.so.9")

    for directory in _candidate_dll_dirs():
        for name in names:
            path = directory / name
            if path.is_file():
                return path

    if os.name != "nt":
        found = ctypes.util.find_library("ass")
        if found:
            return Path(found)

    raise LibassBoundsError(_libass_not_found_message())


def _libass_not_found_message() -> str:
    if os.name == "nt":
        return (
            "ass.dll not found. Expected GradientGUI\\libass\\ass.dll "
            "or a copy next to GradientGUI.exe/main.py."
        )
    if sys.platform == "darwin":
        return (
            "libass.dylib not found. Install libass or bundle libass.dylib "
            "next to GradientGUI."
        )
    return (
        "libass.so not found. Install libass, for example with "
        "apt install libass-dev, or bundle libass.so next to GradientGUI."
    )


def _candidate_dll_dirs() -> list[Path]:
    engine_dir = Path(__file__).resolve().parent
    app_dir = engine_dir.parent
    dirs = [
        app_dir / "libass",
        app_dir,
        app_dir / "bin",
    ]

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        bundle_dir = Path(getattr(sys, "_MEIPASS", exe_dir))
        dirs[:0] = [
            bundle_dir / "libass",
            exe_dir / "libass",
            bundle_dir,
            exe_dir,
        ]

    seen: set[Path] = set()
    unique: list[Path] = []
    for directory in dirs:
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _dll_search_dirs(primary: Path) -> list[Path]:
    dirs = [primary]
    dirs.extend(_candidate_dll_dirs())
    return dirs


def _configure_library(lib, library) -> None:
    lib.ass_set_message_cb(library, _QUIET_MESSAGE_CALLBACK, None)
    lib.ass_set_extract_fonts(library, 1)


def _build_single_event_ass(ass_file: ASSFile, event: ASSEvent) -> str:
    lines: list[str] = ["[Script Info]"]
    script_info = dict(ass_file.script_info)
    script_info.setdefault("ScriptType", "v4.00+")
    script_info.setdefault("PlayResX", str(ass_file.play_res_x or 1920))
    script_info.setdefault("PlayResY", str(ass_file.play_res_y or 1080))
    for key, value in script_info.items():
        lines.append(f"{key}: {value}")

    lines.extend(["", "[V4+ Styles]"])
    lines.append(ass_file.styles_format or "Format: " + ", ".join(STYLE_FIELDS))
    for style in ass_file.styles:
        if style.raw:
            lines.append(style.raw)

    lines.extend(["", "[Events]"])
    lines.append(ass_file.events_format or "Format: " + ", ".join(EVENT_FIELDS))
    lines.append(event.to_ass_line())
    lines.append("")
    return "\n".join(lines)


def _scan_image_list(
    image_list: _ASSImagePtr,
    allowed_types: Optional[set[int]],
    alpha_threshold: int,
) -> Optional[tuple[float, float, float, float]]:
    min_x: Optional[int] = None
    min_y: Optional[int] = None
    max_x: Optional[int] = None
    max_y: Optional[int] = None

    current = image_list
    while current:
        image = current.contents
        if (
            image.w > 0
            and image.h > 0
            and image.bitmap
            and (allowed_types is None or image.type in allowed_types)
        ):
            local = _scan_bitmap(image, alpha_threshold)
            if local:
                x1, y1, x2, y2 = local
                min_x = x1 if min_x is None else min(min_x, x1)
                min_y = y1 if min_y is None else min(min_y, y1)
                max_x = x2 if max_x is None else max(max_x, x2)
                max_y = y2 if max_y is None else max(max_y, y2)
        current = image.next

    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None
    return float(min_x), float(min_y), float(max_x + 1), float(max_y + 1)


def _scan_bitmap(image: _ASSImage, alpha_threshold: int) -> Optional[tuple[int, int, int, int]]:
    base_addr = ctypes.addressof(image.bitmap.contents)
    min_x: Optional[int] = None
    min_y: Optional[int] = None
    max_x: Optional[int] = None
    max_y: Optional[int] = None

    for row in range(image.h):
        row_bytes = ctypes.string_at(base_addr + row * image.stride, image.w)
        left: Optional[int] = None
        right: Optional[int] = None
        for col, alpha in enumerate(row_bytes):
            if alpha > alpha_threshold:
                if left is None:
                    left = col
                right = col

        if left is not None and right is not None:
            screen_y = image.dst_y + row
            screen_left = image.dst_x + left
            screen_right = image.dst_x + right
            min_x = screen_left if min_x is None else min(min_x, screen_left)
            min_y = screen_y if min_y is None else min(min_y, screen_y)
            max_x = screen_right if max_x is None else max(max_x, screen_right)
            max_y = screen_y if max_y is None else max(max_y, screen_y)

    if min_x is None or min_y is None or max_x is None or max_y is None:
        return None
    return min_x, min_y, max_x, max_y


def _event_anchor(
    ass_file: ASSFile,
    event: ASSEvent,
    now_ms: int,
) -> tuple[int, float, float, float, float]:
    style = ass_file.get_style(event.style)
    parsed = parse_tags_from_text(event.text)
    alignment = int(parsed.get("an") or (style.alignment if style else 2) or 2)

    pos = parsed.get("pos")
    if not pos:
        pos = _move_position(event, now_ms)
    if not pos:
        pos = _default_position(ass_file, event, alignment)

    org = parsed.get("org", pos)
    return alignment, float(pos[0]), float(pos[1]), float(org[0]), float(org[1])


def _default_position(ass_file: ASSFile, event: ASSEvent, alignment: int) -> tuple[float, float]:
    style = ass_file.get_style(event.style)
    res_x = float(ass_file.play_res_x or 1920)
    res_y = float(ass_file.play_res_y or 1080)

    ml = event.margin_l or (style.margin_l if style else 0)
    mr = event.margin_r or (style.margin_r if style else 0)
    mv = event.margin_v or (style.margin_v if style else 0)

    halign = (alignment - 1) % 3
    valign = (alignment - 1) // 3

    if halign == 0:
        pos_x = float(ml)
    elif halign == 1:
        pos_x = (res_x - float(mr) + float(ml)) / 2.0
    else:
        pos_x = res_x - float(mr)

    if valign == 0:
        pos_y = res_y - float(mv)
    elif valign == 1:
        pos_y = res_y / 2.0
    else:
        pos_y = float(mv)

    return pos_x, pos_y


_MOVE_RE = re.compile(
    r"\\move\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,"
    r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)"
    r"(?:\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?))?\s*\)"
)


def _move_position(event: ASSEvent, now_ms: int) -> Optional[tuple[float, float]]:
    match = _MOVE_RE.search(event.text)
    if not match:
        return None

    x1, y1, x2, y2 = (float(match.group(i)) for i in range(1, 5))
    start_ms = int(time_to_seconds(event.start) * 1000)
    end_ms = int(time_to_seconds(event.end) * 1000)
    elapsed = max(0, now_ms - start_ms)

    if match.group(5) is not None and match.group(6) is not None:
        move_start = float(match.group(5))
        move_end = float(match.group(6))
    else:
        move_start = 0.0
        move_end = max(float(end_ms - start_ms), 1.0)

    if elapsed <= move_start:
        t = 0.0
    elif elapsed >= move_end:
        t = 1.0
    else:
        t = (elapsed - move_start) / max(move_end - move_start, 1.0)

    return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
