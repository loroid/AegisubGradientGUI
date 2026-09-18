"""
Visual curve editor widget.

v3.1: Single curve per tag with independent Y-axis scaling.
      Color tags have no Y-axis and display a preview band.
"""

from __future__ import annotations
import copy
import math
from typing import Optional

from PySide6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsPathItem,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QMenu, QPushButton, QSizePolicy, QApplication, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QSpinBox,
)
from PySide6.QtCore import Qt, QEvent, QRectF, QPointF, Signal, QLineF
from PySide6.QtGui import (
    QPen, QBrush, QColor, QPainterPath, QPainter, QFont, QAction,
    QImage, QPixmap, QTransform,
)

from engine.interpolation import (
    ColorSpace, CurveNode, InterpolationMode, interpolate, make_default_nodes, mirror_nodes_horizontal,
)
from gui.curve_node_table import CurveNodeTableDialog
from gui.i18n import fit_button_width, set_button_text, tr

# ── Constants ─────────────────────────────────────────────────────────────────
MARGIN_LEFT = 50
MARGIN_RIGHT = 20
MARGIN_TOP = 20
MARGIN_BOTTOM = 40
NODE_RADIUS = 7
HANDLE_RADIUS = 5
MIN_Y_VIEW_SPAN = 1e-6
MAX_Y_VIEW_SPAN = 1e12

COLOR_BG = QColor(24, 24, 42)
COLOR_GRID = QColor(50, 50, 80)
COLOR_GRID_MAJOR = QColor(70, 70, 110)
COLOR_TEXT = QColor(160, 160, 200)
COLOR_NODE = QColor(255, 180, 50)
COLOR_HANDLE = QColor(180, 180, 255, 180)
COLOR_HANDLE_LINE = QColor(120, 120, 200, 150)
CURVE_COLOR = QColor(100, 200, 255)  # cyan

INTERP_MODE_NAMES = {
    InterpolationMode.LINEAR: "Linear",
    InterpolationMode.SMOOTH: "Smooth",
    InterpolationMode.STEPPED: "Stepped",
    InterpolationMode.BEZIER: "Bezier",
}


def _locked_drag_delta(delta: QPointF, modifiers) -> QPointF:
    x, y = delta.x(), delta.y()
    ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
    shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
    if ctrl and shift:
        if abs(x) >= abs(y):
            y = 0.0
        else:
            x = 0.0
    elif ctrl:
        y = 0.0
    elif shift:
        x = 0.0
    return QPointF(x, y)


# ── Draggable Node Item ──────────────────────────────────────────────────────
class NodeItem(QGraphicsEllipseItem):
    def __init__(
        self,
        curve_node: CurveNode,
        editor: "CurveEditorView",
        *,
        is_start_endpoint: bool = False,
        is_end_endpoint: bool = False,
    ):
        super().__init__(-NODE_RADIUS, -NODE_RADIUS, NODE_RADIUS * 2, NODE_RADIUS * 2)
        self.curve_node = curve_node
        self.editor = editor
        self._syncing_position = False
        self._is_start_endpoint = bool(is_start_endpoint)
        self._is_end_endpoint = bool(is_end_endpoint)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(10)
        self.setBrush(QBrush(COLOR_NODE))
        self.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.handle_in: Optional[HandleItem] = None
        self.handle_out: Optional[HandleItem] = None
        self._dragging = False
        self._mouse_origin = QPointF()
        self._drag_nodes = []
        self._last_drag_delta = (0.0, 0.0)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if not self.isSelected():
            if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self.editor.scene().clearSelection()
            self.setSelected(True)
        self._drag_nodes = [
            (item, copy.copy(item.curve_node))
            for item in self.editor._node_items if item.isSelected()
        ]
        self._dragging = True
        self._mouse_origin = event.scenePos()
        self._last_drag_delta = (0.0, 0.0)
        rect = self.editor.plot_rect()
        self._drag_scale = (
            self.editor.x_max() / rect.width(),
            (self.editor._y_max - self.editor._y_min) / rect.height(),
        )
        self.editor._begin_drag()
        event.accept()

    def mouseMoveEvent(self, event):
        if not self._dragging:
            super().mouseMoveEvent(event)
            return
        delta = _locked_drag_delta(event.scenePos() - self._mouse_origin, event.modifiers())
        dx, dy = delta.x() * self._drag_scale[0], -delta.y() * self._drag_scale[1]
        if self.editor._integer_x:
            dx = float(round(dx))
        if self.editor._integer_y:
            dy = float(round(dy))
        if self.editor._is_color or self.editor._is_text:
            dy = 0.0
        x_lo = max(-node.x for _, node in self._drag_nodes)
        x_hi = min(self.editor.x_max() - node.x for _, node in self._drag_nodes)
        dx = max(x_lo, min(x_hi, dx))
        if any(item._is_start_endpoint or item._is_end_endpoint for item, _ in self._drag_nodes):
            dx = 0.0
        if not (self.editor._is_color or self.editor._is_text):
            y_lo = max(self.editor._y_min - node.y for _, node in self._drag_nodes)
            y_hi = min(self.editor._y_max - node.y for _, node in self._drag_nodes)
            dy = max(y_lo, min(y_hi, dy))
        if (dx, dy) != self._last_drag_delta:
            # Update all model coordinates before publishing a preview/undo change.
            for item, origin in self._drag_nodes:
                node = item.curve_node
                node.x, node.y = origin.x + dx, origin.y + dy
                node.handle_in_x, node.handle_in_y = origin.handle_in_x + dx, origin.handle_in_y + dy
                node.handle_out_x, node.handle_out_y = origin.handle_out_x + dx, origin.handle_out_y + dy
            for item, _ in self._drag_nodes:
                item.update_position()
                if item.handle_in:
                    item.handle_in.update_position()
                if item.handle_out:
                    item.handle_out.update_position()
            self._last_drag_delta = (dx, dy)
            self.editor._nodes.sort(key=lambda node: node.x)
            self.editor.update_curve()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self._drag_nodes = []
            event.accept()
            self.editor._end_drag()
        else:
            super().mouseReleaseEvent(event)

    def sceneEvent(self, event):
        if event.type() == QEvent.Type.UngrabMouse and self._dragging:
            self._dragging = False
            self._drag_nodes = []
            self.editor._end_drag()
        return super().sceneEvent(event)

    def _endpoint_x(self) -> float:
        if not self.editor._pin_endpoints_to_range:
            return self.curve_node.x
        return 0.0 if self._is_start_endpoint else self.editor.x_max()

    def update_position(self):
        self._syncing_position = True
        try:
            self.setPos(
                self.editor.value_x_to_scene(self.curve_node.x),
                self.editor.value_y_to_scene(self.curve_node.y),
            )
        finally:
            self._syncing_position = False

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            if bool(value):
                self.setPen(QPen(QColor(255, 255, 255), 2.5))
            else:
                self.setPen(QPen(QColor(255, 255, 255, 200), 1.5))
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            if self._syncing_position:
                return super().itemChange(change, value)
            new_pos = value
            rect = self.editor.plot_rect()
            x, y = new_pos.x(), new_pos.y()
            x = max(rect.left(), min(rect.right(), x))
            y = max(rect.top(), min(rect.bottom(), y))
            if self.editor._is_color or self.editor._is_text:
                y = self.editor.value_y_to_scene(0.0)
            if self._is_start_endpoint or self._is_end_endpoint:
                x = self.editor.value_x_to_scene(self._endpoint_x())
            value_x = self.editor.scene_x_to_value(x)
            x = self.editor.value_x_to_scene(value_x)
            new_pos = QPointF(x, y)
            self.curve_node.x = value_x
            if not (self.editor._is_color or self.editor._is_text):
                value_y = self.editor.scene_y_to_value(y)
                if self.editor._integer_y:
                    value_y = float(round(value_y))
                    y = self.editor.value_y_to_scene(value_y)
                    new_pos = QPointF(x, y)
                self.curve_node.y = value_y
            if self.handle_in:
                self.handle_in.update_position()
            if self.handle_out:
                self.handle_out.update_position()
            self.editor.update_curve()
            return new_pos
        return super().itemChange(change, value)

    def contextMenuEvent(self, event):
        nodes = self.editor.current_nodes()
        menu = QMenu()

        set_val_action = None
        set_color_action = None
        pick_color_action = None

        if self.editor._is_color:
            set_color_action = menu.addAction(tr("设置颜色..."))
            pick_color_action = menu.addAction(tr("拾取屏幕颜色..."))
        elif self.editor._is_text:
            set_val_action = menu.addAction(tr("设置文本..."))
        else:
            set_val_action = menu.addAction(tr("设置数值..."))

        # Per-segment interpolation mode submenu
        if nodes and self.curve_node is not nodes[-1]:
            seg_menu = menu.addMenu(tr("此段插值类型"))
            current_seg_mode = self.curve_node.segment_mode
            for mode in InterpolationMode:
                action = seg_menu.addAction(INTERP_MODE_NAMES[mode])
                action.setCheckable(True)
                action.setChecked(current_seg_mode == mode)
                action.setData(mode)

        # Delete node (not endpoints)
        if nodes and self.curve_node is not nodes[0] and self.curve_node is not nodes[-1]:
            menu.addSeparator()
            delete_action = menu.addAction(tr("删除节点"))
        else:
            delete_action = None

        action = menu.exec(event.screenPos())
        if action and action == delete_action:
            self.editor.remove_node(self.curve_node)
        elif action and action == set_val_action:
            from PySide6.QtWidgets import QInputDialog
            if self.editor._is_text:
                val, ok = QInputDialog.getText(None, tr("设置文本"), tr("输入此节点文本:"), text=self.curve_node.value_str)
                if ok:
                    self.curve_node.value_str = val
                    self.editor.update_curve()
            else:
                if self.editor._integer_y:
                    val, ok = QInputDialog.getInt(
                        None,
                        tr("设置数值"),
                        tr("输入此节点的整数移动格数:"),
                        int(round(self.curve_node.y)),
                        -9999,
                        9999,
                    )
                    val = float(val)
                else:
                    val, ok = QInputDialog.getDouble(
                        None,
                        tr("设置数值"),
                        tr("输入此节点的数值:"),
                        self.curve_node.y,
                        -9999,
                        9999,
                        decimals=self.editor._value_decimals,
                    )
                if ok:
                    self.curve_node.y = val
                    self.editor.recalculate_bounds()
                    self.editor._redraw_all()
        elif action and action == set_color_action:
            from PySide6.QtWidgets import QColorDialog
            c_str = self.curve_node.value_str.ljust(6, '0') if self.curve_node.value_str else "FFFFFF"
            c = QColor(int(c_str[4:6], 16), int(c_str[2:4], 16), int(c_str[0:2], 16))
            dialog = QColorDialog(c, self.editor)
            dialog.setWindowTitle(tr("选择颜色"))
            dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                color = dialog.selectedColor()
                if color.isValid():
                    r, g, b = color.red(), color.green(), color.blue()
                    self.curve_node.value_str = f"{b:02X}{g:02X}{r:02X}"
                    self.editor.update_curve()
        elif action and action == pick_color_action:
            self.editor.pick_color_for_node(self.curve_node)
        elif action and action.data() is not None:
            self.curve_node.segment_mode = action.data()
            self.editor.update_curve()


class HandleItem(QGraphicsEllipseItem):
    def __init__(self, node_item: NodeItem, is_in: bool, editor: "CurveEditorView"):
        super().__init__(-HANDLE_RADIUS, -HANDLE_RADIUS, HANDLE_RADIUS * 2, HANDLE_RADIUS * 2)
        self.node_item = node_item
        self.is_in = is_in
        self.editor = editor
        self._syncing_position = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setZValue(9)
        self.setBrush(QBrush(COLOR_HANDLE))
        self.setPen(QPen(QColor(255, 255, 255, 120), 1))
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.line = QGraphicsLineItem()
        self.line.setPen(QPen(COLOR_HANDLE_LINE, 1, Qt.PenStyle.DashLine))
        self.line.setZValue(8)
        self._dragging = False
        self._mouse_origin = QPointF()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._mouse_origin = event.scenePos()
            node = self.node_item.curve_node
            self._drag_origin_values = (
                (node.handle_in_x, node.handle_in_y) if self.is_in
                else (node.handle_out_x, node.handle_out_y)
            )
            rect = self.editor.plot_rect()
            self._drag_scale = (
                self.editor.x_max() / rect.width(),
                (self.editor._y_max - self.editor._y_min) / rect.height(),
            )
            self.editor._begin_drag()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = _locked_drag_delta(event.scenePos() - self._mouse_origin, event.modifiers())
            vx, vy = self._drag_origin_values
            if delta.x():
                vx = self.editor._snap_x_value(vx + delta.x() * self._drag_scale[0])
            if delta.y():
                vy = max(self.editor._y_min, min(self.editor._y_max, vy - delta.y() * self._drag_scale[1]))
                if self.editor._integer_y:
                    vy = float(round(vy))
            node = self.node_item.curve_node
            previous = (node.handle_in_x, node.handle_in_y) if self.is_in else (node.handle_out_x, node.handle_out_y)
            if (vx, vy) != previous:
                if self.is_in:
                    node.handle_in_x, node.handle_in_y = vx, vy
                else:
                    node.handle_out_x, node.handle_out_y = vx, vy
                self.update_position()
                self.editor.update_curve()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
            self.editor._end_drag()
        else:
            super().mouseReleaseEvent(event)

    def sceneEvent(self, event):
        if event.type() == QEvent.Type.UngrabMouse and self._dragging:
            self._dragging = False
            self.editor._end_drag()
        return super().sceneEvent(event)

    def update_position(self):
        cn = self.node_item.curve_node
        if self.is_in:
            sx = self.editor.value_x_to_scene(cn.handle_in_x)
            sy = self.editor.value_y_to_scene(cn.handle_in_y)
        else:
            sx = self.editor.value_x_to_scene(cn.handle_out_x)
            sy = self.editor.value_y_to_scene(cn.handle_out_y)
        self._syncing_position = True
        try:
            self.setPos(sx, sy)
        finally:
            self._syncing_position = False
        self._update_line()

    def _update_line(self):
        self.line.setLine(QLineF(self.node_item.pos(), self.pos()))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            if self._syncing_position:
                return super().itemChange(change, value)
            new_pos = value
            rect = self.editor.plot_rect()
            x, y = new_pos.x(), new_pos.y()
            x = max(rect.left() - 20, min(rect.right() + 20, x))
            y = max(rect.top() - 20, min(rect.bottom() + 20, y))
            new_pos = QPointF(x, y)
            cn = self.node_item.curve_node
            vx = self.editor.scene_x_to_value(x)
            x = self.editor.value_x_to_scene(vx)
            new_pos = QPointF(x, y)
            vy = self.editor.scene_y_to_value(y)
            if self.editor._integer_y:
                vy = float(round(vy))
                y = self.editor.value_y_to_scene(vy)
                new_pos = QPointF(x, y)
            if self.is_in:
                cn.handle_in_x = vx
                cn.handle_in_y = vy
            else:
                cn.handle_out_x = vx
                cn.handle_out_y = vy
            self.line.setLine(QLineF(self.node_item.pos(), new_pos))
            self.editor.update_curve()
            return new_pos
        return super().itemChange(change, value)


# ── Curve Editor View ─────────────────────────────────────────────────────────
class CurveEditorView(QGraphicsView):
    curve_changed = Signal()
    _redraw_after_drag = Signal()

    def __init__(self, parent=None, *, pin_endpoints_to_range: bool = True):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setStyleSheet("border: 1px solid #333; background: #18182a;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.ItemSelectionMode.IntersectsItemShape)

        self._width = 600
        self._height = 250
        self._x_max = 100.0
        self._pin_endpoints_to_range = bool(pin_endpoints_to_range)
        self._active_x_max: Optional[float] = None
        self._drag_active = False
        self._redraw_pending = False
        self._redraw_after_drag.connect(self._flush_drag_redraw, Qt.ConnectionType.QueuedConnection)
        self.setFixedHeight(self._height + MARGIN_TOP + MARGIN_BOTTOM)

        self._default_mode = InterpolationMode.LINEAR
        self._color_space = ColorSpace.RGB
        self._nodes: list[CurveNode] = make_default_nodes()
        self._notified_curve_state = None
        self._node_items: list[NodeItem] = []
        self._curve_path: Optional[QGraphicsPathItem] = None
        self._color_band_item: Optional[QGraphicsItem] = None
        self._color_preview_stops: Optional[list[tuple[float, str]]] = None

        self._is_color = False
        self._is_text = False
        self._integer_x = False
        self._integer_y = False
        self._value_decimals = 2
        self._fixed_y_range: Optional[tuple[float, float]] = None
        self._manual_y_range: Optional[tuple[float, float]] = None
        self._y_min = 0.0
        self._y_max = 1.0
        self._screen_color_picker = None

        self._sync_scene_rect()
        self._draw_grid()
        self._rebuild_items()

    def current_nodes(self) -> list[CurveNode]:
        return self._nodes

    def x_max(self) -> float:
        return self._x_max

    def set_x_max(self, x_max: float):
        try:
            value = float(x_max)
        except (TypeError, ValueError):
            value = 100.0
        self._x_max = max(1.0, value)
        self._pin_endpoints()
        self._redraw_all()

    def set_active_x_max(self, x_max: Optional[float]):
        value = None if x_max is None else max(0.0, float(x_max))
        if value != self._active_x_max:
            self._active_x_max = value
            self._redraw_all()

    def recalculate_bounds(self):
        if self._drag_active:
            self._redraw_pending = True
            return
        if self._is_color or self._is_text:
            self._y_min = 0.0
            self._y_max = 1.0
        elif self._manual_y_range is not None:
            self._y_min, self._y_max = self._manual_y_range
        elif self._fixed_y_range is not None:
            fixed_min, fixed_max = self._fixed_y_range
            values = [fixed_min, fixed_max]
            for node in self._nodes:
                values.extend([node.y, node.handle_in_y, node.handle_out_y])
            y_min = min(values)
            y_max = max(values)
            if y_max <= y_min + 1e-5:
                y_max = y_min + 1.0
            if y_min < fixed_min or y_max > fixed_max:
                padding = (y_max - y_min) * 0.05
                y_min -= padding
                y_max += padding
            self._y_min = y_min
            self._y_max = y_max
        else:
            values = [v for n in self._nodes for v in (n.y, n.handle_in_y, n.handle_out_y)]
            y_min = min(values, default=0.0)
            y_max = max(values, default=1.0)
            if y_max <= y_min + 1e-5:
                y_max = y_min + 1.0
            padding = (y_max - y_min) * 0.1
            self._y_min = y_min - padding
            self._y_max = y_max + padding

    def set_nodes(self, nodes: list[CurveNode]):
        self._drag_active = False
        self._redraw_pending = False
        self._manual_y_range = None
        self._nodes = nodes if nodes else make_default_nodes()
        if self._integer_x:
            for node in self._nodes:
                if self._pin_endpoints_to_range:
                    self._snap_node_x(node)
                else:
                    node.x = float(round(node.x))
        self._pin_endpoints()
        self.recalculate_bounds()
        self._redraw_all()

    def _pin_endpoints(self):
        # Apply endpoint constraints on load/range changes, not during painting.
        self._nodes.sort(key=lambda node: node.x)
        if self._nodes and self._pin_endpoints_to_range:
            self._nodes[0].x = 0.0
            if len(self._nodes) > 1:
                self._nodes[-1].x = self._x_max

    def _redraw_all(self):
        if self._drag_active:
            self._redraw_pending = True
            return
        selected = {id(item.curve_node) for item in self._node_items if item.isSelected()}
        self._scene.clear()
        self._node_items.clear()
        self._curve_path = None
        self._color_band_item = None
        self._sync_scene_rect()
        self._draw_grid()
        self._rebuild_items(selected_nodes=selected)

    def _sync_scene_rect(self):
        # Hidden/off-range handles must not expand Qt's scrollable canvas.
        self._scene.setSceneRect(QRectF(
            0, 0, self._width + MARGIN_LEFT + MARGIN_RIGHT,
            self._height + MARGIN_TOP + MARGIN_BOTTOM,
        ))

    def _begin_drag(self):
        self._drag_active = True

    def _end_drag(self):
        self._drag_active = False
        if self._redraw_pending:
            self._redraw_pending = False
            self._redraw_after_drag.emit()

    def _flush_drag_redraw(self):
        self.recalculate_bounds()
        self._redraw_all()

    def set_tag_type(self, is_color: bool, is_text: bool = False):
        self._is_color = is_color
        self._is_text = is_text

    def set_integer_y(self, enabled: bool):
        self._integer_y = bool(enabled)
        if self._integer_y and not (self._is_color or self._is_text):
            for node in self._nodes:
                node.y = float(round(node.y))
                node.handle_in_y = float(round(node.handle_in_y))
                node.handle_out_y = float(round(node.handle_out_y))
        self.recalculate_bounds()
        self._redraw_all()

    def set_value_precision(self, decimals: int):
        self._value_decimals = max(0, min(10, int(decimals)))

    def set_integer_x(self, enabled: bool):
        self._integer_x = bool(enabled)
        if self._integer_x:
            for node in self._nodes:
                self._snap_node_x(node)
        self._redraw_all()

    def set_y_range(self, y_min: Optional[float], y_max: Optional[float]):
        previous = self._fixed_y_range
        if y_min is None or y_max is None:
            self._fixed_y_range = None
        else:
            lo = float(y_min)
            hi = float(y_max)
            if hi <= lo + 1e-5:
                hi = lo + 1.0
            self._fixed_y_range = (lo, hi)
        if self._fixed_y_range != previous:
            self._manual_y_range = None
        self.recalculate_bounds()
        self._redraw_all()

    def _snap_x_value(self, value: float) -> float:
        value = max(0.0, min(self._x_max, float(value)))
        if self._integer_x:
            value = float(round(value))
            value = max(0.0, min(self._x_max, value))
        return value

    def _snap_node_x(self, node: CurveNode) -> None:
        old_x = float(node.x)
        new_x = self._snap_x_value(old_x)
        delta = new_x - old_x
        node.x = new_x
        node.handle_in_x = self._snap_x_value(node.handle_in_x + delta)
        node.handle_out_x = self._snap_x_value(node.handle_out_x + delta)

    def set_color_preview_stops(self, stops: Optional[list[tuple[float, str]]]):
        self._color_preview_stops = stops
        self._draw_color_band()
        self.viewport().update()

    def set_color_space(self, color_space: ColorSpace):
        if self._color_space != color_space:
            self._color_space = color_space
            self._draw_color_band()

    def pick_color_for_node(self, node: CurveNode):
        from gui.tag_panel import ScreenColorPicker, _qcolor_to_bgr

        picker = ScreenColorPicker(self.window())
        self._screen_color_picker = picker

        def apply_color(color: QColor):
            node.value_str = _qcolor_to_bgr(color)
            self.update_curve()
            self._screen_color_picker = None

        def clear_picker():
            self._screen_color_picker = None

        picker.color_picked.connect(apply_color)
        picker.cancelled.connect(clear_picker)
        picker.destroyed.connect(lambda *_args: clear_picker())
        picker.start()

    def set_mode(self, mode: InterpolationMode):
        self._default_mode = mode
        self.update_curve()

    def get_mode(self) -> InterpolationMode:
        return self._default_mode

    # ── Coordinate transforms ────────────────────────────────────────────
    def plot_rect(self) -> QRectF:
        return QRectF(MARGIN_LEFT, MARGIN_TOP, self._width, self._height)

    def value_x_to_scene(self, vx: float) -> float:
        return MARGIN_LEFT + (vx / self._x_max) * self._width

    def value_y_to_scene(self, vy: float) -> float:
        if self._is_color or self._is_text:
            return MARGIN_TOP + self._height / 2
        t = (vy - self._y_min) / (self._y_max - self._y_min)
        return MARGIN_TOP + (1.0 - t) * self._height

    def scene_x_to_value(self, sx: float, *, snap: bool = True) -> float:
        value = max(0.0, min(self._x_max, ((sx - MARGIN_LEFT) / self._width) * self._x_max))
        return self._snap_x_value(value) if snap else value

    def scene_y_to_value(self, sy: float) -> float:
        if self._is_color or self._is_text:
            return 0.0
        t = max(0.0, min(1.0, 1.0 - (sy - MARGIN_TOP) / self._height))
        return self._y_min + t * (self._y_max - self._y_min)

    # ── Grid ─────────────────────────────────────────────────────────────
    def _draw_grid(self):
        rect = self.plot_rect()
        bg = self._scene.addRect(rect, QPen(Qt.PenStyle.NoPen), QBrush(COLOR_BG))
        bg.setZValue(-2)
        # Keep off-range curves/handles out of the axis labels when zoomed in.
        edge = NODE_RADIUS + 2
        self._plot_layer = self._scene.addRect(
            rect.adjusted(-edge, -edge, edge, edge),
            QPen(Qt.PenStyle.NoPen), QBrush(Qt.BrushStyle.NoBrush),
        )
        self._plot_layer.setFlag(QGraphicsItem.GraphicsItemFlag.ItemClipsChildrenToShape)
        self._plot_layer.setZValue(2)
        if self._active_x_max is not None and self._active_x_max < self._x_max:
            x = self.value_x_to_scene(self._active_x_max)
            inactive = self._scene.addRect(
                QRectF(x, rect.top(), rect.right() - x, rect.height()),
                QPen(Qt.PenStyle.NoPen), QBrush(QColor(255, 255, 255, 12)),
            )
            inactive.setZValue(-1.5)
            boundary = self._scene.addLine(
                x, rect.top(), x, rect.bottom(),
                QPen(COLOR_TEXT, 1, Qt.PenStyle.DashLine),
            )
            boundary.setZValue(-0.5)
            boundary.setToolTip(f"{tr('当前字幕末帧')}: {self._active_x_max:g}")
        font = QFont("Segoe UI", 8)

        # X-axis
        for i in range(11):
            t = (self._x_max * i) / 10.0
            pen = QPen(COLOR_GRID_MAJOR if i % 5 == 0 else COLOR_GRID, 0.5)
            x = self.value_x_to_scene(t)
            self._scene.addLine(x, rect.top(), x, rect.bottom(), pen).setZValue(-1)
            if i % 2 == 0:
                label = f"{t:.0f}" if abs(t - round(t)) < 1e-6 else f"{t:.1f}"
                lbl = self._scene.addText(label, font)
                lbl.setDefaultTextColor(COLOR_TEXT)
                lbl.setPos(x - 10, rect.bottom() + 2)

        # Y-axis
        if self._is_color or self._is_text:
            y = self.value_y_to_scene(0.0)
            pen = QPen(COLOR_GRID_MAJOR, 1.0, Qt.PenStyle.DashLine)
            self._scene.addLine(rect.left(), y, rect.right(), y, pen).setZValue(-1)
        else:
            y_range = self._y_max - self._y_min
            for i in range(11):
                vy = self._y_min + (i / 10.0) * y_range
                pen = QPen(COLOR_GRID_MAJOR if i % 5 == 0 else COLOR_GRID, 0.5)
                y = self.value_y_to_scene(vy)
                self._scene.addLine(rect.left(), y, rect.right(), y, pen).setZValue(-1)
                if i % 2 == 0:
                    decimals = min(8, max(1, math.ceil(-math.log10(y_range / 5))))
                    label = f"{vy:.{decimals}f}"
                    lbl = self._scene.addText(label, font)
                    for precision in (2, 1):
                        if lbl.boundingRect().width() <= MARGIN_LEFT - 4:
                            break
                        lbl.setPlainText(f"{vy:.{precision}g}")
                    lbl.setDefaultTextColor(COLOR_TEXT)
                    lbl.setPos(2, y - 8)

    # ── Node management ──────────────────────────────────────────────────
    def _rebuild_items(self, *, selected_nodes: Optional[set[int]] = None):
        if selected_nodes is None:
            selected_nodes = {id(item.curve_node) for item in self._node_items if item.isSelected()}
        for ni in self._node_items:
            if ni.handle_in:
                self._scene.removeItem(ni.handle_in.line)
                self._scene.removeItem(ni.handle_in)
            if ni.handle_out:
                self._scene.removeItem(ni.handle_out.line)
                self._scene.removeItem(ni.handle_out)
            self._scene.removeItem(ni)
        self._node_items.clear()
        self._nodes.sort(key=lambda n: n.x)

        last_idx = len(self._nodes) - 1
        for idx, cn in enumerate(self._nodes):
            ni = NodeItem(
                cn,
                self,
                is_start_endpoint=idx == 0,
                is_end_endpoint=idx == last_idx,
            )
            ni.update_position()
            ni.setParentItem(self._plot_layer)
            ni.setSelected(id(cn) in selected_nodes)
            h_in = HandleItem(ni, True, self)
            h_out = HandleItem(ni, False, self)
            ni.handle_in = h_in
            ni.handle_out = h_out
            h_in.setParentItem(self._plot_layer)
            h_in.line.setParentItem(self._plot_layer)
            h_out.setParentItem(self._plot_layer)
            h_out.line.setParentItem(self._plot_layer)
            h_in.update_position()
            h_out.update_position()
            self._node_items.append(ni)

        self._update_handle_visibility()
        self.update_curve()

    def _update_handle_visibility(self):
        for ni in self._node_items:
            cn = ni.curve_node
            seg_mode = cn.get_segment_mode(self._default_mode)
            show_out = seg_mode == InterpolationMode.BEZIER
            # For incoming handle, check previous node's segment mode
            nodes = self._nodes
            idx = nodes.index(cn) if cn in nodes else -1
            show_in = False
            if idx > 0:
                prev_mode = nodes[idx - 1].get_segment_mode(self._default_mode)
                show_in = prev_mode == InterpolationMode.BEZIER
            if ni.handle_in:
                ni.handle_in.setVisible(show_in)
                ni.handle_in.line.setVisible(show_in)
            if ni.handle_out:
                ni.handle_out.setVisible(show_out)
                ni.handle_out.line.setVisible(show_out)

    def _text_value_at(self, vx: float) -> str:
        return interpolate(self._nodes, vx, is_text=True)

    def _inherit_segment(self, node: CurveNode) -> None:
        previous = next((n for n in sorted(self._nodes, key=lambda n: n.x, reverse=True) if n.x < node.x), None)
        if previous is not None:
            node.segment_mode = previous.segment_mode
            node.step_from_right = previous.step_from_right

    def add_node_at(self, vx: float, vy: float):
        vx = self._snap_x_value(vx)
        if self._integer_y and not (self._is_color or self._is_text):
            vy = float(round(vy))
        cn = CurveNode(x=vx, y=vy)
        
        # Determine value_str if color mode by interpolating existing nodes
        if self._is_color:
            color_val = interpolate(
                self._nodes, vx, self._default_mode, is_color=True, color_space=self._color_space,
            )
            cn.value_str = color_val
        elif self._is_text:
            cn.y = 0.0
            cn.value_str = self._text_value_at(vx)

        cn.handle_in_x = self._snap_x_value(vx - 5.0)
        cn.handle_in_y = vy
        cn.handle_out_x = self._snap_x_value(vx + 5.0)
        cn.handle_out_y = vy
        self._inherit_segment(cn)
        self._nodes.append(cn)
        self._nodes.sort(key=lambda n: n.x)
        self._rebuild_items()

    def add_batch_nodes(
        self,
        count: int,
        start_x: float,
        interval: float,
        interval_rule: str,
        start_y: float,
        value_step: float,
        value_rule: str,
    ):
        count = max(1, int(count))
        interval = max(0.0, float(interval))
        positions: list[float] = []
        x = self._snap_x_value(float(start_x))
        for i in range(count):
            if i == 0:
                positions.append(x)
                continue
            if interval_rule == "递增":
                delta = interval * (i + 1)
            elif interval_rule == "递减":
                delta = interval / max(i + 1, 1)
            else:
                delta = interval
            x = self._snap_x_value(x + delta)
            positions.append(x)

        new_nodes: list[CurveNode] = []
        existing = list(self._nodes)
        for i, vx in enumerate(positions):
            if vx <= 0.0001 or vx >= self._x_max - 0.0001:
                continue
            if any(abs(n.x - vx) < 0.001 for n in existing + new_nodes):
                continue
            if self._is_color:
                value = interpolate(
                    self._nodes, vx, self._default_mode, is_color=True, color_space=self._color_space,
                )
                node = CurveNode(x=vx, y=0.0, value_str=str(value))
            elif self._is_text:
                node = CurveNode(x=vx, y=0.0, value_str=self._text_value_at(vx))
            else:
                if value_rule == "递增":
                    vy = start_y + i * value_step
                elif value_rule == "递减":
                    vy = start_y - i * value_step
                else:
                    vy = start_y
                if self._integer_y:
                    vy = float(round(vy))
                node = CurveNode(x=vx, y=vy)
            node.handle_in_x = self._snap_x_value(vx - 5.0)
            node.handle_in_y = node.y
            node.handle_out_x = self._snap_x_value(vx + 5.0)
            node.handle_out_y = node.y
            self._inherit_segment(node)
            new_nodes.append(node)

        if not new_nodes:
            return
        self._nodes.extend(new_nodes)
        self._nodes.sort(key=lambda n: n.x)
        self.recalculate_bounds()
        self._rebuild_items()

    def remove_node(self, cn: CurveNode):
        if cn in self._nodes and cn is not self._nodes[0] and cn is not self._nodes[-1]:
            self._nodes.remove(cn)
            self._rebuild_items()

    # ── Curve drawing ────────────────────────────────────────────────────
    def _draw_color_band(self):
        if not self._is_color:
            if self._color_band_item:
                self._scene.removeItem(self._color_band_item)
                self._color_band_item = None
            return

        if self._color_band_item:
            self._scene.removeItem(self._color_band_item)
            
        rect = QRectF(MARGIN_LEFT, MARGIN_TOP + self._height - 25, self._width, 25)
        from PySide6.QtGui import QLinearGradient
        gradient = QLinearGradient(rect.left(), 0, rect.right(), 0)

        preview_stops = self._color_preview_stops or []
        if preview_stops:
            for pos, c_str in preview_stops:
                c_str = c_str.ljust(6, '0')
                b, g, r = int(c_str[0:2], 16), int(c_str[2:4], 16), int(c_str[4:6], 16)
                gradient.setColorAt(max(0.0, min(1.0, pos)), QColor(r, g, b))
        elif self._nodes:
            # Sample each displayed column using the engine, including stepped
            # segments; a Qt gradient would blend them again in RGB.
            width = max(1, math.ceil(rect.width()))
            image = QImage(width, 1, QImage.Format.Format_RGB32)
            for pixel in range(width):
                c_str = interpolate(
                    self._nodes, (pixel + 0.5) / width * self._x_max,
                    self._default_mode, is_color=True, color_space=self._color_space,
                ) or "FFFFFF"
                image.setPixelColor(pixel, 0, QColor(
                    int(c_str[4:6], 16), int(c_str[2:4], 16), int(c_str[0:2], 16),
                ))
            self._color_band_item = self._scene.addPixmap(QPixmap.fromImage(image))
            self._color_band_item.setPos(rect.topLeft())
            self._color_band_item.setTransform(QTransform.fromScale(rect.width() / width, rect.height()))
            self._color_band_item.setZValue(1)
            self.viewport().update()
            return

        self._color_band_item = self._scene.addRect(rect, QPen(Qt.PenStyle.NoPen), QBrush(gradient))
        self._color_band_item.setZValue(1)
        self.viewport().update()

    def mirror_horizontal(self):
        if not self._nodes:
            return
        self._nodes = mirror_nodes_horizontal(self._nodes)
        self.recalculate_bounds()
        self._redraw_all()

    def mirror_vertical(self):
        if self._is_color or self._is_text or not self._nodes:
            return
        values = []
        for n in self._nodes:
            values.extend([n.y, n.handle_in_y, n.handle_out_y])
        lo = min(values)
        hi = max(values)
        center = lo + hi
        for n in self._nodes:
            n.y = center - n.y
            n.handle_in_y = center - n.handle_in_y
            n.handle_out_y = center - n.handle_out_y
        self.recalculate_bounds()
        self._redraw_all()

    def update_curve(self):
        if self._curve_path:
            self._scene.removeItem(self._curve_path)
            self._curve_path = None

        path = QPainterPath()
        steps = max(int(self._width), 200)
        first = True
        
        if self._is_color or self._is_text:
            for cn in self._nodes:
                sx = self.value_x_to_scene(cn.x)
                sy = self.value_y_to_scene(0.0)
                if first:
                    path.moveTo(sx, sy)
                    first = False
                else:
                    path.lineTo(sx, sy)
        else:
            for i in range(steps + 1):
                t = (i / steps) * self._x_max
                y = interpolate(self._nodes, t, self._default_mode, is_color=False)
                sx = self.value_x_to_scene(t)
                sy = self.value_y_to_scene(y)
                if first:
                    path.moveTo(sx, sy)
                    first = False
                else:
                    path.lineTo(sx, sy)

        pen = QPen(CURVE_COLOR, 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        self._curve_path = self._scene.addPath(path, pen)
        self._curve_path.setParentItem(self._plot_layer)
        self._curve_path.setZValue(5)
        self._update_handle_visibility()
        self._draw_color_band()
        self._notify_curve_change()

    def _notify_curve_change(self):
        # Geometry refreshes are not edits. Record blocked loads too, so the
        # next resize cannot publish a tag switch as a new user modification.
        state = (
            self._is_color, self._is_text, self._default_mode,
            tuple(tuple(vars(node).items()) for node in self._nodes),
        )
        if state != self._notified_curve_state:
            self._notified_curve_state = state
            self.curve_changed.emit()

    def wheelEvent(self, event):
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            super().wheelEvent(event)
            return
        event.accept()
        if self._is_color or self._is_text or self._drag_active:
            return
        delta = event.angleDelta().y() or event.pixelDelta().y()
        span = self._y_max - self._y_min
        if not delta or not math.isfinite(span) or span <= 0:
            return
        steps = max(-20.0, min(20.0, delta / 120.0))
        new_span = max(MIN_Y_VIEW_SPAN, min(MAX_Y_VIEW_SPAN, span * 1.2 ** -steps))
        anchor = self.scene_y_to_value(self.mapToScene(event.position().toPoint()).y())
        fraction = (anchor - self._y_min) / span
        lo = anchor - fraction * new_span
        hi = anchor + (1.0 - fraction) * new_span
        if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
            return
        self._manual_y_range = (lo, hi)
        self.recalculate_bounds()
        self._redraw_all()

    def mouseDoubleClickEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.accept()
            if not self._drag_active:
                self._manual_y_range = None
                self.recalculate_bounds()
                self._redraw_all()
            return
        pos = self.mapToScene(event.pos())
        rect = self.plot_rect()
        if rect.contains(pos):
            self.add_node_at(self.scene_x_to_value(pos.x()), self.scene_y_to_value(pos.y()))
        else:
            super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        viewport = self.viewport().size()
        size = (max(1, viewport.width() - MARGIN_LEFT - MARGIN_RIGHT),
                max(1, viewport.height() - MARGIN_TOP - MARGIN_BOTTOM))
        if size != (self._width, self._height):
            self._width, self._height = size
            self._sync_scene_rect()
            self._redraw_all()


class BatchNodesDialog(QDialog):
    """Dialog for inserting multiple curve nodes at once."""

    def __init__(
        self,
        is_color: bool,
        is_text: bool = False,
        x_max: float = 100.0,
        integer_x: bool = False,
        integer_y: bool = False,
        parent=None,
        value_decimals: int = 2,
        value_single_step: float = 0.1,
    ):
        super().__init__(parent)
        is_value_string = is_color or is_text
        self._integer_x = bool(integer_x)
        self._integer_y = bool(integer_y)
        self._value_decimals = max(0, min(10, int(value_decimals)))
        self._value_single_step = max(
            float(value_single_step),
            10 ** (-self._value_decimals),
        )
        self._x_max = max(1.0, float(x_max or 100.0))
        self.setWindowTitle(tr("批量增加控制点"))
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 500)
        self.count_spin.setValue(5)
        form.addRow(tr("控制点数量:"), self.count_spin)

        self.start_x_spin = QDoubleSpinBox()
        self.start_x_spin.setRange(0.0, self._x_max)
        self.start_x_spin.setDecimals(0 if self._integer_x else 2)
        self.start_x_spin.setSingleStep(1.0 if self._integer_x else 0.1)
        self.start_x_spin.setValue(min(10.0, self._x_max))
        form.addRow(tr("起始位置:"), self.start_x_spin)

        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.0, self._x_max)
        self.interval_spin.setDecimals(0 if self._integer_x else 2)
        self.interval_spin.setSingleStep(1.0 if self._integer_x else 0.1)
        self.interval_spin.setValue(min(10.0, self._x_max))
        form.addRow(tr("间隔:"), self.interval_spin)

        self.interval_rule_combo = QComboBox()
        for rule in ("固定", "递增", "递减"):
            self.interval_rule_combo.addItem(tr(rule), rule)
        form.addRow(tr("间隔规则:"), self.interval_rule_combo)

        self.value_spin = QDoubleSpinBox()
        self.value_spin.setRange(-9999.0, 9999.0)
        self.value_spin.setDecimals(0 if self._integer_y else self._value_decimals)
        self.value_spin.setSingleStep(1.0 if self._integer_y else self._value_single_step)
        self.value_spin.setValue(0.0)
        self.value_spin.setEnabled(not is_value_string)
        form.addRow(tr("数值:"), self.value_spin)

        self.value_step_spin = QDoubleSpinBox()
        self.value_step_spin.setRange(0.0, 9999.0)
        self.value_step_spin.setDecimals(0 if self._integer_y else self._value_decimals)
        self.value_step_spin.setSingleStep(1.0 if self._integer_y else self._value_single_step)
        self.value_step_spin.setValue(1.0)
        self.value_step_spin.setEnabled(not is_value_string)
        form.addRow(tr("数值变化量:"), self.value_step_spin)

        self.value_rule_combo = QComboBox()
        for rule in ("固定", "递增", "递减"):
            self.value_rule_combo.addItem(tr(rule), rule)
        self.value_rule_combo.setEnabled(not is_value_string)
        form.addRow(tr("数值规则:"), self.value_rule_combo)

        layout.addLayout(form)
        if is_color:
            hint = QLabel(tr("颜色曲线会在新增位置按当前曲线采样颜色。"))
            hint.setStyleSheet("color: #999;")
            layout.addWidget(hint)
        elif is_text:
            hint = QLabel(tr("文本曲线会在新增位置继承前一个文本值。"))
            hint.setStyleSheet("color: #999;")
            layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {
            "count": self.count_spin.value(),
            "start_x": self.start_x_spin.value(),
            "interval": self.interval_spin.value(),
            "interval_rule": self.interval_rule_combo.currentData() or self.interval_rule_combo.currentText(),
            "start_y": self.value_spin.value(),
            "value_step": self.value_step_spin.value(),
            "value_rule": self.value_rule_combo.currentData() or self.value_rule_combo.currentText(),
        }


# ── Curve Editor Widget ───────────────────────────────────────────────────────
class CurveEditorWidget(QWidget):
    """Curve editor with mode selector and the canvas."""
    curve_changed = Signal()
    mirror_changed = Signal(bool, bool)
    sample_editor_requested = Signal()

    def __init__(
        self,
        parent=None,
        *,
        title: str = "曲线编辑器",
        show_sample_editor: bool = True,
        integer_x: bool = False,
        integer_y: bool = False,
        pin_endpoints_to_range: bool = True,
    ):
        super().__init__(parent)
        self._show_sample_editor = bool(show_sample_editor)
        self._title_text = title
        self._integer_x = bool(integer_x)
        self._integer_y = bool(integer_y)
        self._value_decimals = 2
        self._value_single_step = 0.1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Header row
        header = QHBoxLayout()
        self._title_label = QLabel(tr(title))
        header.addWidget(self._title_label)
        header.addStretch()
        self._mirror_h_btn = QPushButton(tr("横向镜像"))
        self._mirror_h_btn.setCheckable(True)
        fit_button_width(self._mirror_h_btn, minimum=78, padding=26, fixed=False)
        self._mirror_h_btn.setToolTip(tr("镜像当前曲线的渐变方向"))
        self._mirror_h_btn.toggled.connect(self._on_mirror_h_toggled)
        header.addWidget(self._mirror_h_btn)
        self._mirror_v_btn = QPushButton(tr("纵向镜像"))
        self._mirror_v_btn.setCheckable(True)
        fit_button_width(self._mirror_v_btn, minimum=78, padding=26, fixed=False)
        self._mirror_v_btn.setToolTip(tr("镜像当前曲线的数值方向"))
        self._mirror_v_btn.toggled.connect(self._on_mirror_v_toggled)
        header.addWidget(self._mirror_v_btn)
        self._sample_editor_btn = QPushButton(tr("采色结果"))
        self._sample_editor_btn.setEnabled(False)
        fit_button_width(self._sample_editor_btn, minimum=80, padding=26, fixed=False)
        self._sample_editor_btn.setToolTip(tr("编辑路径采样颜色，并应用为当前颜色曲线"))
        self._sample_editor_btn.clicked.connect(self.sample_editor_requested.emit)
        header.addWidget(self._sample_editor_btn)
        self._sample_editor_btn.setVisible(self._show_sample_editor)
        self._batch_btn = QPushButton(tr("批量加点"))
        fit_button_width(self._batch_btn, minimum=78, padding=26, fixed=False)
        self._batch_btn.setToolTip(tr("按数量、间隔和数值规则批量增加控制点"))
        self._batch_btn.clicked.connect(self._open_batch_nodes_dialog)
        header.addWidget(self._batch_btn)
        self._node_table_btn = QPushButton(tr("节点表格"))
        fit_button_width(self._node_table_btn, minimum=78, padding=26, fixed=False)
        self._node_table_btn.setToolTip(tr("打开表格编辑控制点；支持粘贴 Excel 单元格"))
        self._node_table_btn.clicked.connect(self._open_node_table_dialog)
        header.addWidget(self._node_table_btn)
        self._mode_label = QLabel(tr("默认插值:"))
        header.addWidget(self._mode_label)
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["Linear", "Smooth", "Stepped", "Bezier"])
        self._mode_combo.setFixedWidth(100)
        self._mode_combo.currentTextChanged.connect(self._on_mode_changed)
        header.addWidget(self._mode_combo)
        layout.addLayout(header)

        # Canvas
        self._view = CurveEditorView(pin_endpoints_to_range=pin_endpoints_to_range)
        self._view.set_integer_x(self._integer_x)
        self._view.set_integer_y(self._integer_y)
        self._view.curve_changed.connect(self.curve_changed.emit)
        layout.addWidget(self._view)
        self._syncing_mirror_buttons = False
        self._is_color_curve = False
        self._is_text_curve = False
        self._sample_preview_count = 0

    @property
    def view(self) -> CurveEditorView:
        return self._view

    def set_nodes(self, nodes: list[CurveNode], is_color: bool = False, is_text: bool = False):
        self._is_color_curve = is_color
        self._is_text_curve = is_text
        self._view.set_tag_type(is_color, is_text)
        self._view.set_nodes(nodes)
        self._mirror_v_btn.setEnabled(not (is_color or is_text))
        self._update_sample_editor_button()

    def retranslate_ui(self):
        self._title_label.setText(tr(self._title_text))
        set_button_text(self._mirror_h_btn, "横向镜像", minimum=78, padding=26, fixed=False)
        self._mirror_h_btn.setToolTip(tr("镜像当前曲线的渐变方向"))
        set_button_text(self._mirror_v_btn, "纵向镜像", minimum=78, padding=26, fixed=False)
        self._mirror_v_btn.setToolTip(tr("镜像当前曲线的数值方向"))
        self._sample_editor_btn.setToolTip(tr("编辑路径采样颜色，并应用为当前颜色曲线"))
        set_button_text(self._batch_btn, "批量加点", minimum=78, padding=26, fixed=False)
        self._batch_btn.setToolTip(tr("按数量、间隔和数值规则批量增加控制点"))
        set_button_text(self._node_table_btn, "节点表格", minimum=78, padding=26, fixed=False)
        self._node_table_btn.setToolTip(tr("打开表格编辑控制点；支持粘贴 Excel 单元格"))
        self._mode_label.setText(tr("默认插值:"))
        self._update_sample_editor_button()

    def set_x_max(self, x_max: float):
        self._view.set_x_max(x_max)

    def set_y_range(self, y_min: Optional[float], y_max: Optional[float]):
        self._view.set_y_range(y_min, y_max)

    def set_integer_y(self, enabled: bool):
        self._integer_y = bool(enabled)
        self._view.set_integer_y(enabled)

    def set_value_precision(self, decimals: int, single_step: float = 0.1):
        self._value_decimals = max(0, min(10, int(decimals)))
        self._value_single_step = max(
            float(single_step),
            10 ** (-self._value_decimals),
        )
        self._view.set_value_precision(self._value_decimals)

    def set_integer_x(self, enabled: bool):
        self._integer_x = bool(enabled)
        self._view.set_integer_x(enabled)

    def get_nodes(self) -> list[CurveNode]:
        return self._view.current_nodes()

    def set_color_preview_stops(
        self,
        stops: Optional[list[tuple[float, str]]],
        sample_count: Optional[int] = None,
    ):
        self._sample_preview_count = (
            max(0, int(sample_count))
            if sample_count is not None
            else len(stops or [])
        )
        self._view.set_color_preview_stops(stops)
        self._view.viewport().update()
        self._update_sample_editor_button()

    def set_color_space(self, color_space: ColorSpace):
        self._view.set_color_space(color_space)

    def _update_sample_editor_button(self):
        enabled = self._is_color_curve and self._sample_preview_count > 0
        self._sample_editor_btn.setEnabled(enabled)
        self._sample_editor_btn.setVisible(self._show_sample_editor)
        self._sample_editor_btn.setText(
            f"{tr('采色结果')}({self._sample_preview_count})" if enabled else tr("采色结果")
        )
        fit_button_width(self._sample_editor_btn, minimum=80, padding=26, fixed=False)

    def set_mirror_state(self, horizontal: bool, vertical: bool):
        self._syncing_mirror_buttons = True
        self._mirror_h_btn.setChecked(horizontal)
        self._mirror_v_btn.setChecked(vertical)
        self._syncing_mirror_buttons = False

    def mirror_state(self) -> tuple[bool, bool]:
        return self._mirror_h_btn.isChecked(), self._mirror_v_btn.isChecked()

    def set_mode(self, mode: InterpolationMode):
        self._view.set_mode(mode)
        mode_map = {
            InterpolationMode.LINEAR: "Linear",
            InterpolationMode.SMOOTH: "Smooth",
            InterpolationMode.STEPPED: "Stepped",
            InterpolationMode.BEZIER: "Bezier",
        }
        self._mode_combo.setCurrentText(mode_map.get(mode, "Linear"))

    def get_mode(self) -> InterpolationMode:
        return self._view.get_mode()

    def _on_mode_changed(self, text: str):
        mode_map = {
            "Linear": InterpolationMode.LINEAR,
            "Smooth": InterpolationMode.SMOOTH,
            "Stepped": InterpolationMode.STEPPED,
            "Bezier": InterpolationMode.BEZIER,
        }
        self._view.set_mode(mode_map.get(text, InterpolationMode.LINEAR))

    def _open_batch_nodes_dialog(self):
        dialog = BatchNodesDialog(
            self._is_color_curve,
            self._is_text_curve,
            self._view.x_max(),
            self._integer_x,
            self._integer_y,
            self,
            value_decimals=self._value_decimals,
            value_single_step=self._value_single_step,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        self._view.add_batch_nodes(**values)

    def _open_node_table_dialog(self):
        dialog = CurveNodeTableDialog(
            self._view.current_nodes(),
            is_color=self._is_color_curve,
            is_text=self._is_text_curve,
            x_max=self._view.x_max(),
            integer_x=self._integer_x,
            integer_y=self._integer_y,
            pin_endpoints_to_range=self._view._pin_endpoints_to_range,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._view.set_nodes(dialog.nodes())

    def _on_mirror_h_toggled(self, checked: bool):
        if self._syncing_mirror_buttons:
            return
        self._view.mirror_horizontal()
        self.mirror_changed.emit(*self.mirror_state())

    def _on_mirror_v_toggled(self, checked: bool):
        if self._syncing_mirror_buttons:
            return
        self._view.mirror_vertical()
        self.mirror_changed.emit(*self.mirror_state())
