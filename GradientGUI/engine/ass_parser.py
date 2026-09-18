"""
ASS subtitle file parser and writer.

Handles [Script Info], [Aegisub Project Garbage], [V4+ Styles], and [Events].
Faithfully preserves all sections for consistent rendering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from engine.file_io import atomic_text_writer


# ── Style field names (V4+ format order) ──────────────────────────────────────

STYLE_FIELDS = [
    "Name", "Fontname", "Fontsize",
    "PrimaryColour", "SecondaryColour", "OutlineColour", "BackColour",
    "Bold", "Italic", "Underline", "StrikeOut",
    "ScaleX", "ScaleY", "Spacing", "Angle",
    "BorderStyle", "Outline", "Shadow", "Alignment",
    "MarginL", "MarginR", "MarginV", "Encoding",
]

EVENT_FIELDS = [
    "Layer", "Start", "End", "Style", "Name",
    "MarginL", "MarginR", "MarginV", "Effect", "Text",
]

_STYLE_ATTRIBUTES = dict(zip((name.lower() for name in STYLE_FIELDS), (
    "name", "fontname", "fontsize", "primary_color", "secondary_color",
    "outline_color", "back_color", "bold", "italic", "underline", "strikeout",
    "scale_x", "scale_y", "spacing", "angle", "border_style", "outline",
    "shadow", "alignment", "margin_l", "margin_r", "margin_v", "encoding",
)))
_STYLE_FLOAT_ATTRIBUTES = {"fontsize", "scale_x", "scale_y", "spacing", "angle", "outline", "shadow"}
_EVENT_ATTRIBUTES = dict(zip((name.lower() for name in EVENT_FIELDS), (
    "layer", "start", "end", "style", "name", "margin_l", "margin_r",
    "margin_v", "effect", "text",
)))
_EVENT_ATTRIBUTES["actor"] = "name"


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ASSStyle:
    """A V4+ Style definition."""
    raw: str = ""
    name: str = ""
    fontname: str = ""
    fontsize: float = 48
    primary_color: str = "&H00FFFFFF"
    secondary_color: str = "&H000000FF"
    outline_color: str = "&H00000000"
    back_color: str = "&H00000000"
    bold: int = 0
    italic: int = 0
    underline: int = 0
    strikeout: int = 0
    scale_x: float = 100
    scale_y: float = 100
    spacing: float = 0
    angle: float = 0
    border_style: int = 1
    outline: float = 0
    shadow: float = 0
    alignment: int = 2
    margin_l: int = 0
    margin_r: int = 0
    margin_v: int = 0
    encoding: int = 1
    extra_fields: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    _source_format: str = field(default="", repr=False, compare=False)

    @classmethod
    def from_line(cls, line: str, format_line: str = "") -> ASSStyle:
        """Parse a 'Style: ...' line."""
        s = cls(raw=line, _source_format=format_line)
        prefix = "Style:"
        if not line.startswith(prefix):
            return s
        values = _record_values(line[len(prefix):], format_line, STYLE_FIELDS)
        for name, value in values.items():
            attribute = _STYLE_ATTRIBUTES.get(name)
            if attribute is None:
                s.extra_fields += ((name, value),)
                continue
            default = getattr(s, attribute)
            if attribute in _STYLE_FLOAT_ATTRIBUTES:
                value = _float(value, default)
            elif isinstance(default, int):
                value = _int(value, default)
            else:
                value = value.strip()
            setattr(s, attribute, value)
        return s

    def to_ass_line(self, format_line: str = "") -> str:
        fields = _format_fields(format_line or self._source_format, STYLE_FIELDS)
        if (
            self.raw
            and fields == _format_fields(self._source_format, STYLE_FIELDS)
            and self == self.from_line(self.raw, self._source_format)
        ):
            return self.raw
        values = dict(self.extra_fields)
        values.update({name: str(getattr(self, attribute)) for name, attribute in _STYLE_ATTRIBUTES.items()})
        return "Style: " + ",".join(values.get(name, "") for name in fields)


@dataclass
class ASSEvent:
    """A Dialogue or Comment event."""
    layer: int = 0
    start: str = "0:00:00.00"
    end: str = "0:00:00.00"
    style: str = "Default"
    name: str = ""
    margin_l: int = 0
    margin_r: int = 0
    margin_v: int = 0
    effect: str = ""
    text: str = ""
    comment: bool = False
    extra_fields: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    @classmethod
    def from_line(cls, line: str, format_line: str = "") -> Optional[ASSEvent]:
        """Parse a 'Dialogue: ...' or 'Comment: ...' line."""
        is_comment = line.startswith("Comment:")
        is_dialogue = line.startswith("Dialogue:")
        if not is_comment and not is_dialogue:
            return None

        prefix = "Comment:" if is_comment else "Dialogue:"
        values = _record_values(line[len(prefix):], format_line, EVENT_FIELDS)
        event = cls(comment=is_comment)
        for name, value in values.items():
            attribute = _EVENT_ATTRIBUTES.get(name)
            if attribute is None:
                event.extra_fields += ((name, value),)
            elif attribute == "text":
                event.text = value
            elif isinstance(getattr(event, attribute), int):
                setattr(event, attribute, _int(value))
            else:
                setattr(event, attribute, value.strip())
        return event

    def to_ass_line(self, format_line: str = "") -> str:
        """Serialize back to ASS event line."""
        prefix = "Comment" if self.comment else "Dialogue"
        values = dict(self.extra_fields)
        values.update({name: str(getattr(self, attribute)) for name, attribute in _EVENT_ATTRIBUTES.items()})
        return f"{prefix}: " + ",".join(
            values.get(name, "") for name in _format_fields(format_line, EVENT_FIELDS)
        )


@dataclass
class ASSFile:
    """Complete parsed ASS file."""
    script_info: dict[str, str] = field(default_factory=dict)
    project_garbage: dict[str, str] = field(default_factory=dict)
    gradient_metadata: dict[str, str] = field(default_factory=dict)
    styles_format: str = ""
    styles: list[ASSStyle] = field(default_factory=list)
    events_format: str = ""
    events: list[ASSEvent] = field(default_factory=list)
    # Preserve attachment data and other unrecognized sections.
    _raw_sections: list[tuple[str, list[str]]] = field(default_factory=list)

    # ── Convenience properties ────────────────────────────────────────────

    @property
    def video_file(self) -> str:
        return self.project_garbage.get("Video File", "")

    @property
    def video_position(self) -> int:
        return _int(self.project_garbage.get("Video Position", "0"))

    @property
    def play_res_x(self) -> int:
        return _int(_case_insensitive_value(self.script_info, "PlayResX", "1920"), 1920)

    @property
    def play_res_y(self) -> int:
        return _int(_case_insensitive_value(self.script_info, "PlayResY", "1080"), 1080)

    def get_style(self, name: str) -> Optional[ASSStyle]:
        """Find a style by name."""
        for s in self.styles:
            if s.name == name:
                return s
        return self.styles[0] if self.styles else None

    def animation_frame_info(self, event_index: int) -> Optional[dict[str, object]]:
        """Return Aegisub-exported frame timing metadata for an event."""
        meta_index = int(event_index) + 1
        first = _optional_int(self.gradient_metadata.get(f"Event Frame Start {meta_index}"))
        last = _optional_int(self.gradient_metadata.get(f"Event Frame End {meta_index}"))
        if first is None or last is None:
            return None

        frame_times: dict[int, int] = {}
        raw_times = self.gradient_metadata.get(f"Event Frame Times {meta_index}", "")
        for part in raw_times.split(","):
            frame_raw, sep, ms_raw = part.partition(":")
            if not sep:
                continue
            frame = _optional_int(frame_raw)
            ms = _optional_int(ms_raw)
            if frame is not None and ms is not None:
                frame_times[frame] = ms

        return {
            "first_frame": first,
            "last_frame": max(first, last),
            "event_start_ms": _optional_int(self.gradient_metadata.get(f"Event Start MS {meta_index}")),
            "event_end_ms": _optional_int(self.gradient_metadata.get(f"Event End MS {meta_index}")),
            "frame_time_ms": frame_times,
        }

    # ── Write ─────────────────────────────────────────────────────────────

    def write(self, path: str | Path) -> None:
        """Write the ASS file to disk."""
        with atomic_text_writer(path, encoding="utf-8-sig") as f:
            f.write(self.to_string())

    def to_string(self) -> str:
        """Serialize to full ASS text."""
        lines: list[str] = []

        # [Script Info]
        lines.append("[Script Info]")
        for k, v in self.script_info.items():
            lines.append(f"{k}: {v}")
        lines.append("")

        # [Aegisub Project Garbage]
        if self.project_garbage:
            lines.append("[Aegisub Project Garbage]")
            for k, v in self.project_garbage.items():
                lines.append(f"{k}: {v}")
            lines.append("")

        # [GradientGUI Metadata]
        if self.gradient_metadata:
            lines.append("[GradientGUI Metadata]")
            for k, v in self.gradient_metadata.items():
                lines.append(f"{k}: {v}")
            lines.append("")

        # [V4+ Styles]
        lines.append("[V4+ Styles]")
        if self.styles_format:
            lines.append(self.styles_format)
        else:
            lines.append("Format: " + ", ".join(STYLE_FIELDS))
        for s in self.styles:
            lines.append(s.to_ass_line(self.styles_format or "Format: " + ", ".join(STYLE_FIELDS)))
        lines.append("")

        # [Events]
        lines.append("[Events]")
        if self.events_format:
            lines.append(self.events_format)
        else:
            lines.append("Format: " + ", ".join(EVENT_FIELDS))
        for e in self.events:
            lines.append(e.to_ass_line(self.events_format))
        lines.append("")

        for name, raw_lines in self._raw_sections:
            body = list(raw_lines)
            while body and body[-1] == "":
                body.pop()
            lines.extend([f"[{name}]", *body, ""])

        return "\n".join(lines)

    def to_render_string(self, events: list[ASSEvent]) -> str:
        """Build the same render document for preview and bounds measurement."""
        info = dict(self.script_info)
        existing = {key.lower() for key in info}
        for key, value in (("ScriptType", "v4.00+"), ("PlayResX", str(self.play_res_x)), ("PlayResY", str(self.play_res_y))):
            if key.lower() not in existing:
                info[key] = value
        return replace(
            self, script_info=info, events=events,
            project_garbage={}, gradient_metadata={},
            _raw_sections=[section for section in self._raw_sections if section[0].lower() == "fonts"],
        ).to_string()

    def write_events_only(self, path: str | Path) -> None:
        """Write only the [Events] section (for GUI output)."""
        with atomic_text_writer(path, encoding="utf-8-sig") as f:
            f.write("[Events]\n")
            if self.events_format:
                f.write(self.events_format + "\n")
            else:
                f.write("Format: " + ", ".join(EVENT_FIELDS) + "\n")
            for e in self.events:
                f.write(e.to_ass_line(self.events_format) + "\n")


# ── Parser ────────────────────────────────────────────────────────────────────

def parse_ass_file(path: str | Path) -> ASSFile:
    """Parse an ASS file from disk."""
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    return parse_ass_string(text)


def parse_ass_string(text: str) -> ASSFile:
    """Parse ASS content from a string."""
    ass = ASSFile()
    current_section = ""
    extra_lines = None
    known_sections = {"script info", "aegisub project garbage", "gradientgui metadata", "v4+ styles", "v4 styles", "events"}

    for raw_line in text.removeprefix("\ufeff").split("\n"):
        raw_line = raw_line.removesuffix("\r")
        line = raw_line.strip()

        # Attachment encoding includes brackets and uppercase letters.
        is_header = line.startswith("[") and line.endswith("]")
        encoded_attachment = (
            is_header and current_section in {"fonts", "graphics"}
            and all(33 <= ord(char) <= 96 for char in line)
            and line[1:-1].lower() not in known_sections | {"fonts", "graphics"}
        )
        if is_header and not encoded_attachment:
            name = line[1:-1].strip()
            current_section = name.lower()
            extra_lines = None
            if current_section not in known_sections:
                extra_lines = []
                ass._raw_sections.append((name, extra_lines))
            continue

        if extra_lines is not None:
            extra_lines.append(raw_line)
            continue
        if not line or line.startswith(";"):
            continue

        if current_section == "script info":
            k, _, v = line.partition(":")
            if v or ":" in line:
                ass.script_info[k.strip()] = v.strip()

        elif current_section == "aegisub project garbage":
            k, _, v = line.partition(":")
            if v or ":" in line:
                ass.project_garbage[k.strip()] = v.strip()

        elif current_section == "gradientgui metadata":
            k, _, v = line.partition(":")
            if v or ":" in line:
                ass.gradient_metadata[k.strip()] = v.strip()

        elif current_section in ("v4+ styles", "v4 styles"):
            if line.startswith("Format:"):
                ass.styles_format = line
            elif line.startswith("Style:"):
                ass.styles.append(ASSStyle.from_line(line, ass.styles_format))

        elif current_section == "events":
            if line.startswith("Format:"):
                ass.events_format = line
            elif line.startswith("Dialogue:") or line.startswith("Comment:"):
                evt = ASSEvent.from_line(raw_line.lstrip(" \t"), ass.events_format)
                if evt:
                    ass.events.append(evt)

    return ass


# ── Helpers ───────────────────────────────────────────────────────────────────


def _format_fields(format_line: str, default: list[str]) -> list[str]:
    fields = format_line.partition(":")[2].split(",") if format_line else default
    names = [name.strip().lower() for name in fields if name.strip()]
    return names or [name.lower() for name in default]


def _case_insensitive_value(values: dict[str, str], name: str, default: str) -> str:
    return next((value for key, value in reversed(list(values.items())) if key.lower() == name.lower()), default)


def _record_values(content: str, format_line: str, default: list[str]) -> dict[str, str]:
    names = _format_fields(format_line, default)
    parts = content.lstrip(" \t").rstrip("\r\n").split(",", len(names) - 1)
    return dict(zip(names, parts))

def _int(s, default: int = 0) -> int:
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return default


def _optional_int(s) -> Optional[int]:
    try:
        return int(str(s).strip())
    except (ValueError, TypeError):
        return None


def _float(s, default: float = 0.0) -> float:
    try:
        return float(str(s).strip())
    except (ValueError, TypeError):
        return default


def time_to_seconds(t: str) -> float:
    """Convert ASS timestamp 'H:MM:SS.CC' to seconds."""
    m = re.match(r"(\d+):(\d+):(\d+)\.(\d+)", t.strip())
    if not m:
        return 0.0
    h, mi, s, cs = int(m[1]), int(m[2]), int(m[3]), int(m[4])
    return h * 3600 + mi * 60 + s + cs / 100.0


def seconds_to_time(sec: float) -> str:
    """Convert seconds to ASS timestamp 'H:MM:SS.CC'."""
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    sec -= h * 3600
    mi = int(sec // 60)
    sec -= mi * 60
    s = int(sec)
    cs = int(round((sec - s) * 100))
    if cs >= 100:
        s += 1
        cs -= 100
    if s >= 60:
        mi += 1
        s -= 60
    if mi >= 60:
        h += 1
        mi -= 60
    return f"{h}:{mi:02d}:{s:02d}.{cs:02d}"
