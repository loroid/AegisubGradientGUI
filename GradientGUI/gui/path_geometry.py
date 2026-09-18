"""Editable path geometry shared by the canvas drawing and ASS serialization."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath


Controls = tuple[QPointF, QPointF] | None


def _number(value: float) -> str:
    # Preserve pixel-rounding boundaries without writing exponent notation.
    text = format(Decimal(str(value)), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _smooth_controls(points: list[QPointF]) -> list[Controls]:
    controls = []
    for i in range(len(points) - 1):
        p0 = points[max(0, i - 1)]
        p1, p2 = points[i:i + 2]
        p3 = points[min(len(points) - 1, i + 2)]
        controls.append((p1 + (p2 - p0) / 6.0, p2 - (p3 - p1) / 6.0))
    return controls


@dataclass
class EditablePath:
    points: list[QPointF] = field(default_factory=list)
    # None means a newly drawn, automatically smoothed path. Each None edge
    # inside a saved controls list is a straight line, not an automatic curve.
    controls: list[Controls] | None = None
    move_command: str = "m"

    def __post_init__(self):
        self.points = [QPointF(point) for point in self.points]
        if self.controls is not None:
            self.controls = [
                None if pair is None else (QPointF(pair[0]), QPointF(pair[1]))
                for pair in self.controls
            ]

    def edge_controls(self) -> list[Controls]:
        return _smooth_controls(self.points) if self.controls is None else self.controls

    def to_ass_path(self) -> str:
        if not self.points:
            return ""
        parts = [self.move_command, _number(self.points[0].x()), _number(self.points[0].y())]
        for end, pair in zip(self.points[1:], self.edge_controls()):
            parts.append("l" if pair is None else "b")
            for point in ([end] if pair is None else [*pair, end]):
                parts.extend((_number(point.x()), _number(point.y())))
        return " ".join(parts)

    def painter_path(self) -> QPainterPath:
        if not self.points:
            return QPainterPath()
        path = QPainterPath(self.points[0])
        for end, pair in zip(self.points[1:], self.edge_controls()):
            if pair is None:
                path.lineTo(end)
            else:
                path.cubicTo(*pair, end)
        return path

    def append_point(self, point: QPointF) -> None:
        self.points.append(QPointF(point))
        if self.controls is not None and len(self.points) >= 2:
            self.controls.append(_smooth_controls(self.points)[-1])

    def move_point(self, index: int, point: QPointF, *, origin: EditablePath | None = None) -> bool:
        if point == self.points[index]:
            return False
        source = self if origin is None else origin
        delta = point - source.points[index]
        if self.controls is not None:
            if index > 0 and source.controls[index - 1] is not None:
                start, end = source.controls[index - 1]
                self.controls[index - 1] = (start, end + delta)
            if index < len(self.controls) and source.controls[index] is not None:
                start, end = source.controls[index]
                self.controls[index] = (start + delta, end)
        self.points[index] = QPointF(point)
        return True

    def remove_point(self, index: int) -> None:
        if self.controls:
            if index == 0:
                self.controls.pop(0)
            elif index == len(self.points) - 1:
                self.controls.pop()
            else:
                before, after = self.controls[index - 1:index + 1]
                joined = None
                if before is not None or after is not None:
                    start, end = self.points[index - 1], self.points[index + 1]
                    joined = (
                        before[0] if before is not None else start + (end - start) / 3.0,
                        after[1] if after is not None else end - (end - start) / 3.0,
                    )
                self.controls[index - 1:index + 1] = [joined]
        self.points.pop(index)


def parse_editable_paths(path: str) -> list[EditablePath]:
    tokens = path.split()
    paths: list[EditablePath] = []
    current = None
    command = None
    index = 0
    while index < len(tokens):
        token = tokens[index].lower()
        if token in {"m", "n", "l", "b", "s", "c", "p"}:
            command = token
            index += 1
            continue
        count = 6 if command == "b" else 2 if command in {"m", "n", "l"} else 0
        if not count or index + count > len(tokens):
            index += 1
            continue
        try:
            values = [float(value) for value in tokens[index:index + count]]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Non-finite path coordinate")
        except ValueError:
            index += 1
            continue
        points = [QPointF(*values[i:i + 2]) for i in range(0, count, 2)]
        if command in {"m", "n"}:
            current = EditablePath(points, controls=[], move_command=command)
            paths.append(current)
        else:
            if current is None:
                current = EditablePath([QPointF(0, 0)], controls=[])
                paths.append(current)
            current.points.append(points[-1])
            current.controls.append(tuple(points[:2]) if command == "b" else None)
        index += count
    return paths
