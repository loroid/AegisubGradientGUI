"""Read-only overview for per-tag animation timelines."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget

from engine.interpolation import CurveNode, InterpolationMode, interpolate
from gui.i18n import is_english, tr


@dataclass
class AnimationOverviewRow:
    """One enabled tag in the animation overview."""

    tag: str
    label: str
    frame_range: str
    output_mode: str
    nodes: list[CurveNode]
    mode: InterpolationMode
    active: bool = False


class AnimationOverviewWidget(QWidget):
    """Scrollable global view of enabled tag animation curves."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._rows: list[AnimationOverviewRow] = []
        self._animation_enabled = False
        self._use_transform = True
        self._preview_frame = 0

        self._summary = QLabel(tr("动画总览"))
        self._summary.setStyleSheet(
            "color: #cfd4ff; font-weight: bold; padding: 4px 6px;"
        )
        layout.addWidget(self._summary)

        self._canvas = _AnimationOverviewCanvas()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(self._canvas)
        layout.addWidget(scroll, stretch=1)

    def set_rows(
        self,
        rows: list[AnimationOverviewRow],
        *,
        x_max: float,
        animation_enabled: bool,
        use_transform: bool,
        preview_frame: int,
    ) -> None:
        self._rows = list(rows)
        self._animation_enabled = bool(animation_enabled)
        self._use_transform = bool(use_transform)
        self._preview_frame = int(preview_frame)
        self._canvas.set_rows(self._rows, x_max=x_max, preview_frame=preview_frame)
        self._update_summary()

    def retranslate_ui(self) -> None:
        self._update_summary()
        self._canvas.update()

    def _update_summary(self) -> None:
        if not self._rows:
            self._summary.setText(f"{tr('动画总览')} · {tr('当前没有启用的 tag')}")
            return
        state = tr("开启" if self._animation_enabled else "关闭")
        if not self._animation_enabled:
            output = tr("未输出动画")
        else:
            output = tr(r"\t 优先") if self._use_transform else "split"
        if is_english():
            self._summary.setText(
                f"{tr('动画总览')} · {len(self._rows)} {tr('个启用 tag')} · "
                f"{tr('动画')}: {state} · {output} · "
                f"{tr('预览帧')} {self._preview_frame}"
            )
        else:
            self._summary.setText(
                f"{tr('动画总览')} · {len(self._rows)} {tr('个启用 tag')} · "
                f"动画{state} · {output} · {tr('预览帧')} {self._preview_frame}"
            )


class _AnimationOverviewCanvas(QWidget):
    """Custom painted mini timeline list."""

    _HEADER_H = 34
    _ROW_H = 76
    _LEFT_W = 270
    _RIGHT_PAD = 18
    _TOP_PAD = 8
    _BOTTOM_PAD = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[AnimationOverviewRow] = []
        self._x_max = 1.0
        self._preview_frame = 0
        self.setMinimumHeight(180)

    def set_rows(
        self,
        rows: list[AnimationOverviewRow],
        *,
        x_max: float,
        preview_frame: int,
    ) -> None:
        self._rows = list(rows)
        self._x_max = max(1.0, float(x_max))
        self._preview_frame = max(0, int(preview_frame))
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

        self._draw_header(painter, QRectF(body.left(), body.top(), body.width(), self._HEADER_H))
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
            tr("启用 tag 后，这里会显示每个 tag 的动画曲线、帧范围和输出方式。"),
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
            tr("输出"),
        )
        painter.drawText(
            QRectF(rect.left() + 225, rect.top(), 70, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            tr("帧范围"),
        )
        painter.drawText(
            QRectF(rect.left() + self._LEFT_W, rect.top(), rect.width() - self._LEFT_W, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            tr("移动曲线"),
        )

    def _draw_row(
        self,
        painter: QPainter,
        rect: QRectF,
        row: AnimationOverviewRow,
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

    def _draw_tag_info(self, painter: QPainter, rect: QRectF, row: AnimationOverviewRow) -> None:
        font = painter.font()
        font.setBold(True)
        font.setPointSize(11)
        painter.setFont(font)
        painter.setPen(QColor("#eef1ff"))
        painter.drawText(
            QRectF(rect.left(), rect.top() + 6, 150, 24),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            row.label,
        )

        pill_rect = QRectF(rect.left() + 156, rect.top() + 9, 42, 20)
        use_transform = row.output_mode == r"\t"
        painter.setPen(QPen(QColor("#5960aa" if use_transform else "#6a5944"), 1))
        painter.setBrush(QColor("#243162" if use_transform else "#403326"))
        painter.drawRoundedRect(pill_rect, 4, 4)
        painter.setPen(QColor("#9fffd0" if use_transform else "#ffd79a"))
        font.setBold(False)
        font.setPointSize(9)
        painter.setFont(font)
        painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, row.output_mode)

        painter.setPen(QColor("#9aa0c8"))
        painter.drawText(
            QRectF(rect.left(), rect.top() + 34, 210, 22),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            row.frame_range,
        )

    def _draw_plot(self, painter: QPainter, rect: QRectF, row: AnimationOverviewRow) -> None:
        painter.setPen(QPen(QColor("#2c2c4a"), 1))
        painter.setBrush(QColor("#0f0f23"))
        painter.drawRoundedRect(rect, 4, 4)

        y_min, y_max = self._value_range(row.nodes)
        zero_y = self._value_to_y(0.0, rect, y_min, y_max)

        painter.setPen(QPen(QColor("#25254a"), 1))
        for i in range(1, 6):
            x = rect.left() + rect.width() * i / 6.0
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
        painter.drawLine(int(rect.left()), int(zero_y), int(rect.right()), int(zero_y))

        self._draw_preview_line(painter, rect)
        self._draw_curve(painter, rect, row, y_min, y_max)
        self._draw_nodes(painter, rect, row.nodes, y_min, y_max)

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
            f"{int(round(self._x_max))}",
        )
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

    def _draw_preview_line(self, painter: QPainter, rect: QRectF) -> None:
        x = self._x_to_pixel(self._preview_frame, rect)
        painter.setPen(QPen(QColor("#ffb84d"), 1.5))
        painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))

    def _draw_curve(
        self,
        painter: QPainter,
        rect: QRectF,
        row: AnimationOverviewRow,
        y_min: float,
        y_max: float,
    ) -> None:
        if not row.nodes:
            return
        samples = max(2, min(220, int(rect.width())))
        path = QPainterPath()
        first = True
        for i in range(samples + 1):
            x_value = self._x_max * i / samples
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

    def _x_to_pixel(self, x_value: float, rect: QRectF) -> float:
        return rect.left() + rect.width() * max(0.0, min(self._x_max, float(x_value))) / self._x_max

    def _value_to_y(self, value: float, rect: QRectF, y_min: float, y_max: float) -> float:
        if abs(y_max - y_min) < 1e-6:
            return rect.center().y()
        t = (float(value) - y_min) / (y_max - y_min)
        return rect.bottom() - rect.height() * max(0.0, min(1.0, t))

    def _value_range(self, nodes: list[CurveNode]) -> tuple[float, float]:
        values = [-10.0, 10.0]
        for node in nodes:
            values.extend([
                float(node.y),
                float(node.handle_in_y),
                float(node.handle_out_y),
            ])
        y_min = min(values)
        y_max = max(values)
        if abs(y_max - y_min) < 1e-6:
            y_min -= 1.0
            y_max += 1.0
        margin = max(1.0, (y_max - y_min) * 0.08)
        return y_min - margin, y_max + margin
