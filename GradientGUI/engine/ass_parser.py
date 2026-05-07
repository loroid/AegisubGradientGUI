"""
ASS subtitle file parser and writer.

Handles [Script Info], [Aegisub Project Garbage], [V4+ Styles], and [Events].
Faithfully preserves all sections for consistent rendering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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

    @classmethod
    def from_line(cls, line: str) -> ASSStyle:
        """Parse a 'Style: ...' line."""
        s = cls(raw=line)
        prefix = "Style:"
        if not line.startswith(prefix):
            return s
        parts = line[len(prefix):].strip().split(",")
        if len(parts) < 23:
            parts.extend([""] * (23 - len(parts)))

        s.name = parts[0].strip()
        s.fontname = parts[1].strip()
        s.fontsize = _float(parts[2], 48)
        s.primary_color = parts[3].strip()
        s.secondary_color = parts[4].strip()
        s.outline_color = parts[5].strip()
        s.back_color = parts[6].strip()
        s.bold = _int(parts[7])
        s.italic = _int(parts[8])
        s.underline = _int(parts[9])
        s.strikeout = _int(parts[10])
        s.scale_x = _float(parts[11], 100)
        s.scale_y = _float(parts[12], 100)
        s.spacing = _float(parts[13])
        s.angle = _float(parts[14])
        s.border_style = _int(parts[15], 1)
        s.outline = _float(parts[16])
        s.shadow = _float(parts[17])
        s.alignment = _int(parts[18], 2)
        s.margin_l = _int(parts[19])
        s.margin_r = _int(parts[20])
        s.margin_v = _int(parts[21])
        s.encoding = _int(parts[22], 1)
        return s


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

    @classmethod
    def from_line(cls, line: str) -> Optional[ASSEvent]:
        """Parse a 'Dialogue: ...' or 'Comment: ...' line."""
        is_comment = line.startswith("Comment:")
        is_dialogue = line.startswith("Dialogue:")
        if not is_comment and not is_dialogue:
            return None

        prefix = "Comment:" if is_comment else "Dialogue:"
        content = line[len(prefix):].strip()
        # Split into at most 10 fields (last field Text can contain commas)
        parts = content.split(",", 9)
        if len(parts) < 10:
            parts.extend([""] * (10 - len(parts)))

        return cls(
            layer=_int(parts[0]),
            start=parts[1].strip(),
            end=parts[2].strip(),
            style=parts[3].strip(),
            name=parts[4].strip(),
            margin_l=_int(parts[5]),
            margin_r=_int(parts[6]),
            margin_v=_int(parts[7]),
            effect=parts[8].strip(),
            text=parts[9],
            comment=is_comment,
        )

    def to_ass_line(self) -> str:
        """Serialize back to ASS event line."""
        prefix = "Comment" if self.comment else "Dialogue"
        return (
            f"{prefix}: {self.layer},{self.start},{self.end},"
            f"{self.style},{self.name},{self.margin_l},{self.margin_r},"
            f"{self.margin_v},{self.effect},{self.text}"
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
    # Preserve unknown sections and raw ordering
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
        return _int(self.script_info.get("PlayResX", "1920"), 1920)

    @property
    def play_res_y(self) -> int:
        return _int(self.script_info.get("PlayResY", "1080"), 1080)

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
        with open(path, "w", encoding="utf-8-sig") as f:
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
            lines.append(s.raw)
        lines.append("")

        # [Events]
        lines.append("[Events]")
        if self.events_format:
            lines.append(self.events_format)
        else:
            lines.append("Format: " + ", ".join(EVENT_FIELDS))
        for e in self.events:
            lines.append(e.to_ass_line())
        lines.append("")

        return "\n".join(lines)

    def write_events_only(self, path: str | Path) -> None:
        """Write only the [Events] section (for GUI output)."""
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("[Events]\n")
            if self.events_format:
                f.write(self.events_format + "\n")
            else:
                f.write("Format: " + ", ".join(EVENT_FIELDS) + "\n")
            for e in self.events:
                f.write(e.to_ass_line() + "\n")


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

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue

        # Section header
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            continue

        if current_section == "Script Info":
            k, _, v = line.partition(":")
            if v or ":" in line:
                ass.script_info[k.strip()] = v.strip()

        elif current_section in ("Aegisub Project Garbage",):
            k, _, v = line.partition(":")
            if v or ":" in line:
                ass.project_garbage[k.strip()] = v.strip()

        elif current_section == "GradientGUI Metadata":
            k, _, v = line.partition(":")
            if v or ":" in line:
                ass.gradient_metadata[k.strip()] = v.strip()

        elif current_section in ("V4+ Styles", "V4 Styles"):
            if line.startswith("Format:"):
                ass.styles_format = line
            elif line.startswith("Style:"):
                ass.styles.append(ASSStyle.from_line(line))

        elif current_section == "Events":
            if line.startswith("Format:"):
                ass.events_format = line
            elif line.startswith("Dialogue:") or line.startswith("Comment:"):
                evt = ASSEvent.from_line(line)
                if evt:
                    ass.events.append(evt)

    return ass


# ── Helpers ───────────────────────────────────────────────────────────────────

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
