"""Bezier path editor for video-frame color sampling."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QGraphicsEllipseItem, QGraphicsPathItem,
    QGraphicsItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)

from gui.i18n import tr


def _fmt_num(value: float) -> str:
    rounded = round(value, 1)
    if abs(rounded - round(rounded)) < 1e-6:
        return str(int(round(rounded)))
    return f"{rounded:.1f}"


def _pil_to_pixmap(image) -> QPixmap:
    rgba = image.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg.copy())


def _parse_path_segments(path: str) -> list[list[QPointF]]:
    tokens = path.split()
    paths: list[list[QPointF]] = []
    current: list[QPointF] = []
    i = 0
    cmd: Optional[str] = None

    def append_point(x: float, y: float) -> None:
        nonlocal current
        if not current:
            current = []
            paths.append(current)
        current.append(QPointF(x, y))

    while i < len(tokens):
        tok = tokens[i].lower()
        if tok in {"m", "n", "l", "b"}:
            cmd = tok
            i += 1
            if cmd in {"m", "n"} and i + 1 < len(tokens):
                try:
                    current = [QPointF(float(tokens[i]), float(tokens[i + 1]))]
                    paths.append(current)
                    i += 2
                except ValueError:
                    pass
            continue
        try:
            if cmd in {"m", "n", "l"} and i + 1 < len(tokens):
                append_point(float(tokens[i]), float(tokens[i + 1]))
                i += 2
            elif cmd == "b" and i + 5 < len(tokens):
                append_point(float(tokens[i + 4]), float(tokens[i + 5]))
                i += 6
            else:
                i += 1
        except ValueError:
            i += 1
    return [path_points for path_points in paths if path_points]


def _bezier_controls(points: list[QPointF]) -> list[tuple[QPointF, QPointF, QPointF]]:
    segments: list[tuple[QPointF, QPointF, QPointF]] = []
    if len(points) < 2:
        return segments

    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else p2
        c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0, p1.y() + (p2.y() - p0.y()) / 6.0)
        c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0, p2.y() - (p3.y() - p1.y()) / 6.0)
        segments.append((c1, c2, p2))
    return segments


def points_to_ass_path(points: list[QPointF]) -> str:
    if not points:
        return ""
    parts = ["m", _fmt_num(points[0].x()), _fmt_num(points[0].y())]
    for c1, c2, end in _bezier_controls(points):
        parts.extend([
            "b",
            _fmt_num(c1.x()), _fmt_num(c1.y()),
            _fmt_num(c2.x()), _fmt_num(c2.y()),
            _fmt_num(end.x()), _fmt_num(end.y()),
        ])
    return " ".join(parts)


def paths_to_ass_path(paths: list[list[QPointF]]) -> str:
    return " ".join(
        part for part in (points_to_ass_path(points) for points in paths) if part
    )


class PathCanvas(QGraphicsView):
    changed = Signal()

    def __init__(
        self,
        pixmap: Optional[QPixmap],
        canvas_size: tuple[int, int],
        paths: Optional[list[list[QPointF]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)

        self._width, self._height = canvas_size
        self._paths = paths or []
        self._active_path = 0
        self._drag_ref: Optional[tuple[int, int]] = None
        self._view_zoom = 1.0
        self._panning = False
        self._pan_start = None
        self._items: list = []
        self._background_item = None

        self._set_background(pixmap, canvas_size)
        self._scene.setSceneRect(0, 0, self._width, self._height)
        self._redraw()

    def set_background_pixmap(
        self,
        pixmap: Optional[QPixmap],
        canvas_size: tuple[int, int],
    ) -> None:
        """Replace the preview frame without touching edited path points."""
        self._set_background(pixmap, canvas_size)
        self._scene.setSceneRect(0, 0, self._width, self._height)
        self._redraw()

    def _set_background(
        self,
        pixmap: Optional[QPixmap],
        canvas_size: tuple[int, int],
    ) -> None:
        if self._background_item is not None:
            self._scene.removeItem(self._background_item)
            self._background_item = None
        if pixmap:
            item = QGraphicsPixmapItem(pixmap)
            item.setZValue(-10)
            self._scene.addItem(item)
            self._background_item = item
            self._width = pixmap.width()
            self._height = pixmap.height()
        else:
            self._width, self._height = canvas_size
            bg = QGraphicsRectItem(QRectF(0, 0, self._width, self._height))
            bg.setBrush(QBrush(QColor("#1a1a2e")))
            bg.setPen(QPen(QColor("#33334a")))
            bg.setZValue(-10)
            self._scene.addItem(bg)
            self._background_item = bg

    def _normalize_active_path(self) -> None:
        if not self._paths:
            self._active_path = 0
            return
        self._active_path = max(0, min(self._active_path, len(self._paths) - 1))

    def points(self) -> list[QPointF]:
        if not self._paths:
            return []
        return list(self._paths[self._active_path])

    def path_count(self) -> int:
        return len(self._paths)

    def active_path_index(self) -> int:
        self._normalize_active_path()
        return self._active_path

    def path_strings(self) -> list[str]:
        return [points_to_ass_path(points) for points in self._paths]

    def current_path_string(self) -> str:
        if not self._paths:
            return ""
        return points_to_ass_path(self._paths[self._active_path])

    def current_path_is_valid(self) -> bool:
        return bool(self._paths and len(self._paths[self._active_path]) >= 2)

    def set_active_path(self, index: int) -> None:
        if not self._paths:
            return
        index = max(0, min(index, len(self._paths) - 1))
        if index == self._active_path:
            return
        self._active_path = index
        self._drag_ref = None
        self._redraw()
        self.changed.emit()

    def set_points(self, points: list[QPointF]) -> None:
        self._paths = [points] if points else []
        self._active_path = 0
        self._drag_ref = None
        self._redraw()
        self.changed.emit()

    def total_point_count(self) -> int:
        return sum(len(points) for points in self._paths)

    def has_valid_path(self) -> bool:
        return any(len(points) >= 2 for points in self._paths)

    def ass_path(self) -> str:
        return paths_to_ass_path(self._paths)

    def valid_ass_path(self) -> str:
        return paths_to_ass_path([points for points in self._paths if len(points) >= 2])

    def undo_point(self) -> None:
        if self._paths and self._paths[self._active_path]:
            self._paths[self._active_path].pop()
            if not self._paths[self._active_path] and len(self._paths) > 1:
                self._paths.pop(self._active_path)
                self._active_path = max(0, min(self._active_path, len(self._paths) - 1))
            self._redraw()
            self.changed.emit()

    def clear_points(self) -> None:
        self._paths.clear()
        self._active_path = 0
        self._redraw()
        self.changed.emit()

    def add_path(self) -> None:
        self._paths.append([])
        self._active_path = len(self._paths) - 1
        self._drag_ref = None
        self._redraw()
        self.changed.emit()

    def remove_current_path(self) -> None:
        if not self._paths:
            return
        self._paths.pop(self._active_path)
        self._normalize_active_path()
        self._drag_ref = None
        self._redraw()
        self.changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if abs(self._view_zoom - 1.0) < 1e-6:
            self._fit_view()

    def showEvent(self, event):
        super().showEvent(event)
        if abs(self._view_zoom - 1.0) < 1e-6:
            self._fit_view()

    def _fit_view(self):
        self.resetTransform()
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._view_zoom = 1.0

    def reset_view(self):
        self._fit_view()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        next_zoom = max(0.1, min(20.0, self._view_zoom * factor))
        factor = next_zoom / self._view_zoom
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.scale(factor, factor)
        self._view_zoom = next_zoom
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                pos = self._clamped_scene_pos(event.position().toPoint())
                ref = self._nearest_point_ref(pos)
                if ref is not None:
                    path_idx, point_idx = ref
                    self._paths[path_idx].pop(point_idx)
                    if not self._paths[path_idx] and len(self._paths) > 1:
                        self._paths.pop(path_idx)
                    self._active_path = max(0, min(path_idx, len(self._paths) - 1))
                    self._redraw()
                    self.changed.emit()
            else:
                self.reset_view()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            pos = self._clamped_scene_pos(event.position().toPoint())
            ref = self._nearest_point_ref(pos)
            if ref is None:
                if not self._paths:
                    self._paths.append([])
                    self._active_path = 0
                self._paths[self._active_path].append(pos)
                ref = (self._active_path, len(self._paths[self._active_path]) - 1)
            else:
                self._active_path = ref[0]
            self._drag_ref = ref
            self._redraw()
            self.changed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )
            event.accept()
            return
        if self._drag_ref is not None:
            path_idx, point_idx = self._drag_ref
            if 0 <= path_idx < len(self._paths) and 0 <= point_idx < len(self._paths[path_idx]):
                self._paths[path_idx][point_idx] = self._clamped_scene_pos(event.position().toPoint())
            self._redraw()
            self.changed.emit()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            event.accept()
            return
        self._drag_ref = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _clamped_scene_pos(self, view_pos) -> QPointF:
        p = self.mapToScene(view_pos)
        return QPointF(
            max(0.0, min(float(self._width - 1), p.x())),
            max(0.0, min(float(self._height - 1), p.y())),
        )

    def _nearest_point_ref(self, pos: QPointF) -> Optional[tuple[int, int]]:
        if not self._paths:
            return None
        scale = max(abs(self.transform().m11()), 0.01)
        threshold = 12.0 / scale
        best_ref: Optional[tuple[int, int]] = None
        best_dist = threshold
        for path_idx, points in enumerate(self._paths):
            for point_idx, point in enumerate(points):
                dist = math.hypot(point.x() - pos.x(), point.y() - pos.y())
                if dist <= best_dist:
                    best_dist = dist
                    best_ref = (path_idx, point_idx)
        return best_ref

    def _redraw(self) -> None:
        for item in self._items:
            self._scene.removeItem(item)
        self._items.clear()

        for path_idx, points in enumerate(self._paths):
            if not points:
                continue
            path = QPainterPath(points[0])
            for c1, c2, end in _bezier_controls(points):
                path.cubicTo(c1, c2, end)
            path_item = QGraphicsPathItem(path)
            color = QColor("#ff5a5f") if path_idx == self._active_path else QColor("#8a7cff")
            path_item.setPen(QPen(color, 3.0))
            path_item.setZValue(10)
            self._scene.addItem(path_item)
            self._items.append(path_item)

            for point in points:
                radius = 5.0
                item = QGraphicsEllipseItem(
                    -radius, -radius, radius * 2.0, radius * 2.0
                )
                item.setPos(point)
                item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
                fill = QColor("#ffd166") if path_idx == self._active_path else QColor("#b8b5ff")
                item.setBrush(QBrush(fill))
                item.setPen(QPen(QColor("#111111"), 1.5))
                item.setZValue(20)
                self._scene.addItem(item)
                self._items.append(item)


class PathSamplerDialog(QDialog):
    def __init__(
        self,
        tag: str,
        frame_image=None,
        canvas_size: tuple[int, int] = (1920, 1080),
        initial_path: str = "",
        default_points: Optional[list[tuple[float, float]]] = None,
        frame_number: Optional[int] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._tag = tag
        self._base_title = f"\\{tag} {tr('路径采色')}"
        self._canvas_size = canvas_size
        self._frame_number: Optional[int] = (
            int(frame_number) if frame_number is not None and int(frame_number) >= 0 else None
        )
        self._set_title_frame(self._frame_number)
        self.resize(960, 620)
        self._remove_requested = False
        self._applied_paths: dict[str, str] = {}
        self._apply_buttons: dict[str, QPushButton] = {}
        self._apply_all_buttons: dict[str, QPushButton] = {}
        self._syncing_path_list = False

        pixmap = _pil_to_pixmap(frame_image) if frame_image is not None else None
        paths = _parse_path_segments(initial_path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._canvas = PathCanvas(pixmap, canvas_size, paths)
        self._canvas.changed.connect(self._sync_path_list)
        layout.addWidget(self._canvas, stretch=1)

        path_layout = QVBoxLayout()
        path_layout.addWidget(QLabel(tr("路径列表:")))
        self._path_list = QListWidget()
        self._path_list.setMaximumHeight(120)
        self._path_list.currentRowChanged.connect(self._on_path_row_changed)
        path_layout.addWidget(self._path_list)
        layout.addLayout(path_layout)

        apply_layout = QHBoxLayout()
        apply_layout.addWidget(QLabel(tr("当前路径应用到:")))
        for target in ("1c", "2c", "3c", "4c"):
            if target == tag:
                continue
            btn = QPushButton(f"\\{target}")
            btn.setToolTip(f"{tr('当前路径应用到:')} \\{target}")
            btn.clicked.connect(lambda _checked=False, t=target: self._apply_current_path_to(t))
            apply_layout.addWidget(btn)
            self._apply_buttons[target] = btn
        apply_layout.addStretch()
        layout.addLayout(apply_layout)

        apply_all_layout = QHBoxLayout()
        apply_all_layout.addWidget(QLabel(tr("全部路径应用到:")))
        for target in ("1c", "2c", "3c", "4c"):
            if target == tag:
                continue
            btn = QPushButton(f"\\{target}")
            btn.setToolTip(f"{tr('全部路径应用到:')} \\{target}")
            btn.clicked.connect(lambda _checked=False, t=target: self._apply_all_paths_to(t))
            apply_all_layout.addWidget(btn)
            self._apply_all_buttons[target] = btn
        apply_all_layout.addStretch()
        layout.addLayout(apply_all_layout)

        controls = QHBoxLayout()
        undo_btn = QPushButton(tr("撤销点"))
        undo_btn.clicked.connect(self._canvas.undo_point)
        controls.addWidget(undo_btn)

        add_path_btn = QPushButton(tr("新增路径"))
        add_path_btn.clicked.connect(self._canvas.add_path)
        controls.addWidget(add_path_btn)

        remove_current_btn = QPushButton(tr("移除当前路径"))
        remove_current_btn.clicked.connect(self._canvas.remove_current_path)
        controls.addWidget(remove_current_btn)

        clear_btn = QPushButton(tr("清空全部"))
        clear_btn.clicked.connect(self._canvas.clear_points)
        controls.addWidget(clear_btn)

        remove_btn = QPushButton(tr("移除全部路径"))
        remove_btn.clicked.connect(self._remove_path)
        controls.addWidget(remove_btn)
        controls.addStretch()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        controls.addWidget(buttons)
        layout.addLayout(controls)

        self._sync_path_list()

    def set_frame_image(self, frame_image=None, frame_number: Optional[int] = None) -> None:
        pixmap = _pil_to_pixmap(frame_image) if frame_image is not None else None
        if frame_image is not None:
            self._canvas_size = (frame_image.width, frame_image.height)
        self._frame_number = (
            int(frame_number) if frame_number is not None and int(frame_number) >= 0 else None
        )
        self._canvas.set_background_pixmap(pixmap, self._canvas_size)
        self._set_title_frame(self._frame_number)

    def _set_title_frame(self, frame_number: Optional[int]) -> None:
        if frame_number is None or int(frame_number) < 0:
            self.setWindowTitle(self._base_title)
        else:
            self.setWindowTitle(f"{self._base_title} - {tr('视频帧')} {int(frame_number)}")

    @property
    def remove_requested(self) -> bool:
        return self._remove_requested

    def path(self) -> str:
        return self._canvas.valid_ass_path()

    @property
    def sampling_frame_number(self) -> Optional[int]:
        return self._frame_number

    @property
    def applied_paths(self) -> dict[str, str]:
        return dict(self._applied_paths)

    def accept(self):
        super().accept()

    def _remove_path(self):
        self._remove_requested = True
        super().accept()

    def _on_path_row_changed(self, row: int):
        if self._syncing_path_list or row < 0:
            return
        self._canvas.set_active_path(row)

    def _apply_current_path_to(self, target: str):
        if not self._canvas.current_path_is_valid():
            QMessageBox.warning(self, tr("路径不足"), tr("当前路径至少需要两个点。"))
            return
        self._applied_paths[target] = self._canvas.current_path_string()
        btn = self._apply_buttons.get(target)
        self._mark_apply_button(btn)

    def _apply_all_paths_to(self, target: str):
        path = self._canvas.valid_ass_path()
        if not path:
            QMessageBox.warning(self, tr("路径不足"), tr("至少需要一条包含两个点的路径。"))
            return
        self._applied_paths[target] = path
        btn = self._apply_all_buttons.get(target)
        self._mark_apply_button(btn)

    def _mark_apply_button(self, btn: Optional[QPushButton]):
        if btn:
            text = btn.text().rstrip(" ✓")
            btn.setText(f"{text} ✓")
            btn.setStyleSheet("QPushButton { color: #8cf0c8; font-weight: bold; }")

    def _sync_path_list(self):
        self._syncing_path_list = True
        self._path_list.clear()
        path_strings = self._canvas.path_strings()
        for idx, path_str in enumerate(path_strings):
            label = path_str if path_str else tr("(空路径，点击画面添加点)")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self._path_list.addItem(item)
        if path_strings:
            self._path_list.setCurrentRow(self._canvas.active_path_index())
        self._syncing_path_list = False
