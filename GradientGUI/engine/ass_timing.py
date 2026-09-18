"""Preserve ASS's event-relative clocks when slicing an event."""

from __future__ import annotations

import re


_TIMED_TAG = re.compile(r"\\(t|move|fade|fad)\s*\(")
_KARAOKE_TAG = re.compile(r"\\(?:kt|kf|ko|[kK])(?=[^A-Za-z]|$)")
_KARAOKE_START = re.compile(r"\\kt(?=[^A-Za-z]|$)\s*([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))?")


def retime_event_text(text: str, offset_ms: int, duration_ms: int) -> str:
    """Shift time tags to a slice's start, retaining the full source duration."""
    if not (_TIMED_TAG.search(text) or _KARAOKE_TAG.search(text)):
        return text
    karaoke = False

    def retime_block(match):
        nonlocal karaoke
        block = match.group(1)
        karaoke |= bool(_KARAOKE_TAG.search(block))
        block = _retime_timed_tags(block, offset_ms, duration_ms)
        block = _KARAOKE_START.sub(
            lambda tag: r"\kt" + _centiseconds(
                int(float(tag.group(1) or 0) * 10) - offset_ms
            ),
            block,
        )
        return "{" + block + "}"

    result = re.sub(r"\{([^}]*)\}", retime_block, text)
    if karaoke and offset_ms:
        # Explicit \kt tags below override this initial implicit karaoke clock.
        result = r"{\kt" + _centiseconds(-offset_ms) + "}" + result
    return result


def _centiseconds(milliseconds: int) -> str:
    return f"{milliseconds / 10:.1f}".rstrip("0").rstrip(".")


def _retime_timed_tags(block: str, offset: int, duration: int) -> str:
    parts = []
    position = 0
    while match := _TIMED_TAG.search(block, position):
        opening = match.end() - 1
        closing = _closing_parenthesis(block, opening)
        if closing is None:
            break
        parts.append(block[position:match.start()])
        original = block[match.start():closing + 1]
        arguments = _arguments(block[opening + 1:closing])
        try:
            replacement = _retime_tag(match.group(1), arguments, offset, duration)
        except (ValueError, OverflowError):
            replacement = None
        parts.append(original if replacement is None else replacement)
        position = closing + 1
    parts.append(block[position:])
    return "".join(parts)


def _closing_parenthesis(text: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _arguments(text: str) -> list[str]:
    parts = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    parts.append(text[start:].strip())
    return parts


def _retime_tag(tag: str, args: list[str], offset: int, duration: int) -> str | None:
    if tag == "t" and 1 <= len(args) <= 4 and "\\" in args[-1]:
        start, end = (int(float(value)) for value in args[:2]) if len(args) >= 3 else (0, 0)
        end = end or duration
        start -= offset
        end -= offset
        # libass treats an end time of 0 as "the end of this event".
        end = -1 if end == 0 else end
        accel = [args[-2]] if len(args) in (2, 4) else []
        inner = _retime_timed_tags(args[-1], offset, duration)
        return r"\t(" + ",".join([str(start), str(end), *accel, inner]) + ")"

    if tag == "move" and len(args) in (4, 6):
        start, end = sorted(int(float(value)) for value in args[4:]) if len(args) == 6 else (0, 0)
        if start <= 0 and end <= 0:
            start, end = 0, duration
        if offset >= end:
            if start == end == offset:
                return r"\move(" + ",".join([*args[:4], "0", "1"]) + ")"
            return r"\pos(" + ",".join(args[2:4]) + ")"
        return r"\move(" + ",".join([*args[:4], str(start - offset), str(end - offset)]) + ")"

    if tag in {"fad", "fade"} and len(args) in (2, 7):
        if len(args) == 2:
            colors = ["255", "0", "255"]
            fade_in, fade_out = (int(float(value)) for value in args)
            times = [0, fade_in, duration - fade_out, duration]
        else:
            colors = args[:3]
            times = [int(float(value)) for value in args[3:]]
            if times[0] == times[3] == -1:
                times = [0, times[1], duration - times[2], duration]
        times = [value - offset for value in times]
        # Avoid the legacy "short fade" sentinel after shifting explicit times.
        if times[0] == times[3] == -1:
            times[0] = -2
        return r"\fade(" + ",".join([*colors, *map(str, times)]) + ")"
    return None
