"""Read-only overview for enabled tag gradient curves."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from engine.interpolation import ColorSpace, CurveNode, InterpolationMode, interpolate
from gui.i18n import is_english, tr


@dataclass
class TagOverviewRow:
    """One enabled curve key in the tag overview."""

    key: str
    tag: str
    label: str
    group: str
    tag_type: str
    value_text: str
    source_text: str
    nodes: list[CurveNode]
    mode: InterpolationMode
    color_space: ColorSpace = ColorSpace.RGB
    active: bool = False


class TagOverviewWidget(QWidget):
    """Scrollable global view of enabled tag curve state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._rows: list[TagOverviewRow] = []
        self._color_space_label = ""

        self._summary = QLabel(tr("标签总览"))
        self._summary.setStyleSheet(
            "color: #cfd4ff; font-weight: bold; padding: 4px 6px;"
        )
        layout.addWidget(self._summary)

        self._canvas = _TagOverviewCanvas()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._canvas)
        layout.addWidget(scroll, stretch=1)

    def set_rows(
        self,
        rows: list[TagOverviewRow],
        *,
        color_space_label: str,
    ) -> None:
        self._rows = list(rows)
        self._color_space_label = color_space_label
        self._canvas.set_rows(self._rows)
        self._update_summary()

    def retranslate_ui(self) -> None:
        self._update_summary()
        self._canvas.update()

    def _update_summary(self) -> None:
        if not self._rows:
            self._summary.setText(f"{tr('标签总览')} · {tr('当前没有启用的 tag')}")
            return
        tag_count = len({row.tag for row in self._rows})
        if is_english():
            self._summary.setText(
                f"{tr('标签总览')} · {tag_count} {tr('个启用 tag')} · "
                f"{len(self._rows)} {tr('条曲线')} · "
                f"{tr('颜色空间')} {self._color_space_label}"
            )
        else:
            self._summary.setText(
                f"{tr('标签总览')} · {tag_count} {tr('个启用 tag')} · "
                f"{len(self._rows)} {tr('条曲线')} · "
                f"{tr('颜色空间')} {self._color_space_label}"
            )


class _TagOverviewCanvas(QWidget):
    """Custom painted mini curve list."""

    _HEADER_H = 34
    _ROW_H = 82
    _LEFT_W = 330
    _RIGHT_PAD = 18
    _TOP_PAD = 8
    _BOTTOM_PAD = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[TagOverviewRow] = []
        self.setMinimumHeight(180)

    def set_rows(self, rows: list[TagOverviewRow]) -> None:
        self._rows = list(rows)
        height = (
            self._TOP_PAD
            + self._HEADER_H
            + max(1, len(self._rows)) * self._ROW_H
            + self._BOTTOM_PAD
        )
        self.setMinimumHeight(height)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#121226"))

        body = self.rect().adjusted(8, self._TOP_PAD, -8, -self._BOTTOM_PAD)
        if not self._rows:
            self._draw_empty(painter, QRectF(body))
            return

        self._draw_header(
            painter,
            QRectF(body.left(), body.top(), body.width(), self._HEADER_H),
        )
        y = body.top() + self._HEADER_H
        for idx, row in enumerate(self._rows):
            rect = QRectF(body.left(), y, body.width(), self._ROW_H - 6)
            self._draw_row(painter, rect, row, idx)
            y += self._ROW_H

    def _draw_empty(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QColor("#8b91ba"))
        font = painter.font()
        font.setPointSize(12)
        painter.setFont(font)
        painter.drawText(
            rect,
            Qt.AlignmentFlag.AlignCenter,
            tr("启用 tag 后，这里会显示每个 tag 的取值、路径状态和曲线概览。"),
        )

    def _draw_header(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QColor("#8b91ba"))
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)

        painter.drawText(
            QRectF(rect.left() + 12, rect.top(), 160, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            tr("Tag"),
        )
        painter.drawText(
            QRectF(rect.left() + 170, rect.top(), 70, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            tr("来源"),
        )
        painter.drawText(
            QRectF(rect.left() + 238, rect.top(), 90, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            tr("取值"),
        )
        painter.drawText(
            QRectF(
                rect.left() + self._LEFT_W,
                rect.top(),
                rect.width() - self._LEFT_W,
                rect.height(),
            ),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            tr("曲线 / 色带"),
        )

    def _draw_row(
        self,
        painter: QPainter,
        rect: QRectF,
        row: TagOverviewRow,
        index: int,
    ) -> None:
        bg = QColor("#1a1a34" if index % 2 == 0 else "#17172e")
        if row.active:
            bg = QColor("#252552")
        painter.setPen(QPen(QColor("#38385a"), 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 5, 5)

        left_rect = QRectF(rect.left() + 12, rect.top(), self._LEFT_W - 24, rect.height())
        self._draw_tag_info(painter, left_rect, row)

        plot_rect = QRectF(
            rect.left() + self._LEFT_W,
            rect.top() + 10,
            max(60.0, rect.width() - self._LEFT_W - self._RIGHT_PAD),
            rect.height() - 20,
        )
        self._draw_plot(painter, plot_rect, row)

    def _draw_tag_info(self, painter: QPainter, rect: QRectF, row: TagOverviewRow) -> None:
        font = painter.font()
        font.setBold(True)
        font.setPointSize(11)
        painter.setFont(font)
        painter.setPen(QColor("#eef1ff"))
        painter.drawText(
            QRectF(rect.left(), rect.top() + 6, 156, 24),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            row.label,
        )

        source_rect = QRectF(rect.left() + 162, rect.top() + 9, 76, 20)
        path_source = (
            row.source_text.startswith("路径")
            or row.source_text.startswith("Path")
            or row.source_text in {"原路径", "Source Clip"}
        )
        painter.setPen(QPen(QColor("#5960aa" if path_source else "#4b4b68"), 1))
        painter.setBrush(QColor("#22334a" if path_source else "#24243a"))
        painter.drawRoundedRect(source_rect, 4, 4)
        painter.setPen(QColor("#9fffd0" if path_source else "#b8bddf"))
        font.setBold(False)
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(source_rect, Qt.AlignmentFlag.AlignCenter, row.source_text)

        painter.setPen(QColor("#9aa0c8"))
        painter.drawText(
            QRectF(rect.left(), rect.top() + 34, 120, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            row.group,
        )
        painter.drawText(
            QRectF(rect.left() + 120, rect.top() + 34, 190, 20),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            row.value_text,
        )

    def _draw_plot(self, painter: QPainter, rect: QRectF, row: TagOverviewRow) -> None:
        painter.setPen(QPen(QColor("#2c2c4a"), 1))
        painter.setBrush(QColor("#0f0f23"))
        painter.drawRoundedRect(rect, 4, 4)

        painter.setPen(QPen(QColor("#25254a"), 1))
        for i in range(1, 6):
            x = rect.left() + rect.width() * i / 6.0
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))

        if row.tag_type == "color":
            self._draw_color_strip(painter, rect.adjusted(8, 14, -8, -26), row)
        else:
            y_min, y_max = self._value_range(row.nodes)
            zero_y = self._value_to_y(0.0, rect, y_min, y_max)
            painter.drawLine(int(rect.left()), int(zero_y), int(rect.right()), int(zero_y))
            self._draw_curve(painter, rect, row, y_min, y_max)
            self._draw_nodes(painter, rect, row.nodes, y_min, y_max)
            self._draw_axis_labels(painter, rect, y_min, y_max)

        self._draw_x_labels(painter, rect)

    def _draw_color_strip(self, painter: QPainter, rect: QRectF, row: TagOverviewRow) -> None:
        if not row.nodes:
            return
        width = max(1, int(rect.width()))
        for i in range(width):
            x_value = 100.0 * i / max(1, width - 1)
            try:
                color = str(
                    interpolate(
                        row.nodes,
                        x_value,
                        row.mode,
                        is_color=True,
                        color_space=row.color_space,
                    )
                )
            except Exception:
                color = row.nodes[0].value_str or "FFFFFF"
            painter.setPen(QPen(_bgr_to_qcolor(color), 1))
            x = rect.left() + i
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))

        painter.setPen(QPen(QColor("#4c527a"), 1))
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawRoundedRect(rect, 3, 3)

        painter.setPen(QPen(QColor("#ffe0a3"), 1))
        painter.setBrush(QColor("#ffb84d"))
        for node in row.nodes:
            x = self._x_to_pixel(node.x, rect)
            painter.drawEllipse(QRectF(x - 4, rect.center().y() - 4, 8, 8))

    def _draw_curve(
        self,
        painter: QPainter,
        rect: QRectF,
        row: TagOverviewRow,
        y_min: float,
        y_max: float,
    ) -> None:
        if not row.nodes:
            return
        samples = max(2, min(220, int(rect.width())))
        path = QPainterPath()
        first = True
        for i in range(samples + 1):
            x_value = 100.0 * i / samples
            try:
                y_value = float(interpolate(row.nodes, x_value, row.mode))
            except Exception:
                y_value = 0.0
            x = self._x_to_pixel(x_value, rect)
            y = self._value_to_y(y_value, rect, y_min, y_max)
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        painter.setPen(QPen(QColor("#5fd4ff"), 2))
        painter.drawPath(path)

    def _draw_nodes(
        self,
        painter: QPainter,
        rect: QRectF,
        nodes: list[CurveNode],
        y_min: float,
        y_max: float,
    ) -> None:
        painter.setPen(QPen(QColor("#ffe0a3"), 1))
        painter.setBrush(QColor("#ffb84d"))
        for node in nodes:
            x = self._x_to_pixel(node.x, rect)
            y = self._value_to_y(node.y, rect, y_min, y_max)
            painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))

    def _draw_x_labels(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QColor("#787fa8"))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(
            QRectF(rect.left() + 6, rect.bottom() - 18, 80, 14),
            Qt.AlignmentFlag.AlignLeft,
            "0",
        )
        painter.drawText(
            QRectF(rect.right() - 86, rect.bottom() - 18, 80, 14),
            Qt.AlignmentFlag.AlignRight,
            "100",
        )

    def _draw_axis_labels(self, painter: QPainter, rect: QRectF, y_min: float, y_max: float) -> None:
        painter.setPen(QColor("#787fa8"))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(
            QRectF(rect.right() - 72, rect.top() + 4, 66, 14),
            Qt.AlignmentFlag.AlignRight,
            f"{y_max:g}",
        )
        painter.drawText(
            QRectF(rect.right() - 72, rect.bottom() - 32, 66, 14),
            Qt.AlignmentFlag.AlignRight,
            f"{y_min:g}",
        )

    def _x_to_pixel(self, x_value: float, rect: QRectF) -> float:
        return rect.left() + rect.width() * max(0.0, min(100.0, float(x_value))) / 100.0

    def _value_to_y(self, value: float, rect: QRectF, y_min: float, y_max: float) -> float:
        if abs(y_max - y_min) < 1e-6:
            return rect.center().y()
        t = (float(value) - y_min) / (y_max - y_min)
        return rect.bottom() - rect.height() * max(0.0, min(1.0, t))

    def _value_range(self, nodes: list[CurveNode]) -> tuple[float, float]:
        values = [0.0]
        for node in nodes:
            values.extend(
                [
                    float(node.y),
                    float(node.handle_in_y),
                    float(node.handle_out_y),
                ]
            )
        y_min = min(values)
        y_max = max(values)
        if abs(y_max - y_min) < 1e-6:
            y_min -= 1.0
            y_max += 1.0
        margin = max(1.0, (y_max - y_min) * 0.08)
        return y_min - margin, y_max + margin


def _bgr_to_qcolor(bgr: str) -> QColor:
    bgr = str(bgr or "FFFFFF").ljust(6, "0")[:6]
    try:
        b = int(bgr[0:2], 16)
        g = int(bgr[2:4], 16)
        r = int(bgr[4:6], 16)
    except ValueError:
        r = g = b = 255
    return QColor(r, g, b)
