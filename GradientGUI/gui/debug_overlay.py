"""Debug overlay helpers for the video preview."""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from engine.ass_parser import ASSEvent


Rect = tuple[float, float, float, float]
Point = tuple[float, float]


CLIP_RE = re.compile(r"\\i?clip\(([^)]*)\)")


def sampled_clip_shapes(events: Iterable[Any], max_shapes: int = 260) -> list[dict[str, Any]]:
    shapes: list[dict[str, Any]] = []
    for event in events:
        text = getattr(event, "text", "") or ""
        for match in CLIP_RE.finditer(text):
            shape = parse_clip_shape(match.group(1))
            if shape:
                shapes.append(shape)
    return _sample_evenly(shapes, max_shapes)


def parse_clip_shape(content: str) -> Optional[dict[str, Any]]:
    tokens = [tok for tok in re.split(r"[\s,]+", content.strip()) if tok]
    if len(tokens) >= 4 and _all_number(tokens[:4]):
        x1, y1, x2, y2 = (float(v) for v in tokens[:4])
        return {
            "type": "rect",
            "rect": (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
            "label": "clip",
            "color": "#ff4050",
        }

    if tokens and tokens[0].isdigit():
        tokens = tokens[1:]
    points: list[Point] = []
    i = 0
    cmd = ""
    while i < len(tokens):
        tok = tokens[i].lower()
        if tok in {"m", "n", "l", "b"}:
            cmd = tok
            i += 1
            continue
        if cmd in {"m", "n", "l"} and i + 1 < len(tokens) and _all_number(tokens[i:i + 2]):
            points.append((float(tokens[i]), float(tokens[i + 1])))
            i += 2
            continue
        if cmd == "b" and i + 5 < len(tokens) and _all_number(tokens[i:i + 6]):
            points.append((float(tokens[i + 4]), float(tokens[i + 5])))
            i += 6
            continue
        i += 1
    if len(points) >= 3:
        return {
            "type": "polygon",
            "points": points,
            "label": "clip",
            "color": "#ff4050",
        }
    return None


def rect_item(label: str, rect: Rect, color: str) -> dict[str, Any]:
    x1, y1, x2, y2 = rect
    return {
        "label": label,
        "rect": (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
        "color": color,
    }


def debug_overlay_ass_events(
    data: Optional[dict[str, Any]],
    base_event: Optional[ASSEvent] = None,
    max_events: int = 360,
) -> list[ASSEvent]:
    """Build translucent ASS drawing events for the preview debug overlay."""
    if not data or not data.get("enabled"):
        return []

    base = base_event or ASSEvent(start="0:00:00.00", end="0:00:10.00", style="Default")
    events: list[ASSEvent] = []
    layer = max(int(getattr(base, "layer", 0)) + 100, 100)

    def add(text: str) -> None:
        if len(events) >= max_events:
            return
        events.append(
            ASSEvent(
                layer=layer,
                start=base.start,
                end=base.end,
                style=base.style or "Default",
                name="GradientGUI Debug",
                margin_l=0,
                margin_r=0,
                margin_v=0,
                effect="",
                text=text,
                comment=False,
            )
        )

    for item in data.get("rects", []):
        rect = item.get("rect")
        if not rect or len(rect) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in rect[:4])
        path = _rect_path(x1, y1, x2, y2)
        add(_drawing_text(path, item.get("color", "#ffffff"), "C0"))
        label = str(item.get("label", ""))
        if label:
            add(_label_text(x1 + 3, y1 + 3, label, item.get("color", "#ffffff")))

    for shape in data.get("clips", []):
        color = shape.get("color", "#ff4050")
        if shape.get("type") == "rect":
            rect = shape.get("rect")
            if rect and len(rect) >= 4:
                add(_drawing_text(_rect_path(*[float(v) for v in rect[:4]]), color, "DA"))
        else:
            points = shape.get("points") or []
            if len(points) >= 3:
                add(_drawing_text(_polygon_path(points), color, "DA"))

    summary = list(data.get("summary", []))[:8]
    for row, line in enumerate(summary):
        add(_label_text(12, 12 + row * 21, line, "#ffffff", alpha="30"))

    return events


def _drawing_text(path: str, color: str, alpha: str) -> str:
    return (
        "{\\an7\\pos(0,0)\\bord0\\shad0"
        f"\\1c&H{_color_to_bgr(color)}&\\1a&H{alpha}&\\p1}}"
        f"{path}{{\\p0}}"
    )


def _label_text(x: float, y: float, label: str, color: str, alpha: str = "20") -> str:
    safe = label.replace("{", "").replace("}", "")
    return (
        f"{{\\an7\\pos({x:.1f},{y:.1f})\\fs18\\bord2\\shad0"
        f"\\1c&H{_color_to_bgr(color)}&\\3c&H000000&\\1a&H{alpha}&"
        "}"
        f"{safe}"
    )


def _rect_path(x1: float, y1: float, x2: float, y2: float) -> str:
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    return f"m {x1:.1f} {y1:.1f} l {x2:.1f} {y1:.1f} l {x2:.1f} {y2:.1f} l {x1:.1f} {y2:.1f}"


def _polygon_path(points: Iterable[Point]) -> str:
    pts = [(float(x), float(y)) for x, y in points]
    if not pts:
        return ""
    first = pts[0]
    parts = [f"m {first[0]:.1f} {first[1]:.1f}"]
    for x, y in pts[1:]:
        parts.append(f"l {x:.1f} {y:.1f}")
    return " ".join(parts)


def _color_to_bgr(color: str) -> str:
    c = str(color or "#ffffff").strip()
    if c.startswith("#"):
        c = c[1:]
    c = c.ljust(6, "f")[:6]
    return f"{c[4:6]}{c[2:4]}{c[0:2]}".upper()


def _sample_evenly(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0 or len(items) <= limit:
        return items
    sampled: list[dict[str, Any]] = []
    last_index = len(items) - 1
    for i in range(limit):
        idx = round(i * last_index / max(limit - 1, 1))
        sampled.append(items[idx])
    return sampled


def _all_number(tokens: list[str]) -> bool:
    for token in tokens:
        try:
            float(token)
        except ValueError:
            return False
    return True


class DebugOverlayWidget(QWidget):
    """Transparent ASS-coordinate overlay drawn above the preview surface."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: Optional[dict[str, Any]] = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def set_data(self, data: Optional[dict[str, Any]]) -> None:
        self._data = data
        self.setVisible(bool(data and data.get("enabled")))
        self.update()

    def paintEvent(self, event):
        if not self._data or not self._data.get("enabled"):
            return
        play_res = self._data.get("play_res") or (0, 0)
        try:
            play_w, play_h = float(play_res[0]), float(play_res[1])
        except (TypeError, ValueError, IndexError):
            return
        if play_w <= 0 or play_h <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        target = self._content_rect(play_w, play_h)

        def map_point(point: Point) -> QPointF:
            x, y = point
            return QPointF(
                target.left() + float(x) / play_w * target.width(),
                target.top() + float(y) / play_h * target.height(),
            )

        for rect in self._data.get("rects", []):
            self._draw_rect(painter, target, play_w, play_h, rect)

        for shape in self._data.get("clips", []):
            self._draw_shape(painter, map_point, shape)

        self._draw_legend(painter)

    def _content_rect(self, play_w: float, play_h: float) -> QRectF:
        view = QRectF(0, 0, self.width(), self.height())
        view_ratio = view.width() / max(view.height(), 1.0)
        play_ratio = play_w / max(play_h, 1.0)
        if view_ratio > play_ratio:
            h = view.height()
            w = h * play_ratio
        else:
            w = view.width()
            h = w / play_ratio
        return QRectF(
            view.left() + (view.width() - w) / 2.0,
            view.top() + (view.height() - h) / 2.0,
            w,
            h,
        )

    def _draw_rect(
        self,
        painter: QPainter,
        target: QRectF,
        play_w: float,
        play_h: float,
        item: dict[str, Any],
    ) -> None:
        rect = item.get("rect")
        if not rect or len(rect) < 4:
            return
        x1, y1, x2, y2 = (float(v) for v in rect[:4])
        mapped = QRectF(
            target.left() + x1 / play_w * target.width(),
            target.top() + y1 / play_h * target.height(),
            (x2 - x1) / play_w * target.width(),
            (y2 - y1) / play_h * target.height(),
        ).normalized()
        color = QColor(item.get("color", "#ffffff"))
        color.setAlpha(230)
        painter.setPen(QPen(color, 1.6, Qt.PenStyle.SolidLine))
        fill = QColor(color)
        fill.setAlpha(28)
        painter.setBrush(fill)
        painter.drawRect(mapped)
        label = str(item.get("label", ""))
        if label:
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(color)
            painter.drawText(mapped.adjusted(3, 2, -2, -2), label)

    def _draw_shape(self, painter: QPainter, map_point, shape: dict[str, Any]) -> None:
        color = QColor(shape.get("color", "#ff4050"))
        color.setAlpha(120)
        painter.setPen(QPen(color, 0.8, Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if shape.get("type") == "rect":
            rect = shape.get("rect")
            if rect and len(rect) >= 4:
                p1 = map_point((rect[0], rect[1]))
                p2 = map_point((rect[2], rect[3]))
                painter.drawRect(QRectF(p1, p2).normalized())
            return
        points = shape.get("points") or []
        if len(points) >= 2:
            qpoints = [map_point(point) for point in points]
            for i in range(len(qpoints)):
                painter.drawLine(qpoints[i], qpoints[(i + 1) % len(qpoints)])

    def _draw_legend(self, painter: QPainter) -> None:
        lines = list(self._data.get("summary", []))[:8]
        if not lines:
            return
        painter.setFont(QFont("Segoe UI", 9))
        width = min(max((len(line) for line in lines), default=0) * 8 + 18, self.width() - 20)
        height = len(lines) * 18 + 12
        box = QRectF(10, 10, width, height)
        painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
        painter.setBrush(QColor(10, 10, 20, 190))
        painter.drawRoundedRect(box, 4, 4)
        painter.setPen(QColor(235, 235, 245))
        y = box.top() + 20
        for line in lines:
            painter.drawText(QPointF(box.left() + 8, y), line)
            y += 18
