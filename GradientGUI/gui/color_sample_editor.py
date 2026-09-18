"""Editable preview for colors sampled from video paths."""

from __future__ import annotations

import math
import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QColorDialog,
)
from gui.i18n import is_english, tr


def bgr_to_qcolor(bgr: str) -> QColor:
    bgr = (bgr or "FFFFFF").ljust(6, "0")[:6]
    try:
        b = int(bgr[0:2], 16)
        g = int(bgr[2:4], 16)
        r = int(bgr[4:6], 16)
    except ValueError:
        return QColor("#ffffff")
    return QColor(r, g, b)


def qcolor_to_bgr(color: QColor) -> str:
    return f"{color.blue():02X}{color.green():02X}{color.red():02X}"


class ColorSampleEditorDialog(QDialog):
    """Dialog that edits sampled color stops before converting them to a curve."""

    def __init__(
        self,
        tag: str,
        stops: list[tuple[float, str]],
        sampled_colors: Optional[list[tuple[int, str]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"\\{tag} {tr('采色结果')}")
        self.resize(620, 460)
        self._tag = tag
        self._stops = (
            self._stops_from_sampled_colors(sampled_colors)
            if sampled_colors
            else self._normalize_stops(stops)
        )
        self._action = "confirm"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(QLabel(tr("编辑路径采样得到的颜色点；确定会保存采样结果，应用为曲线会转换为手动曲线。")))

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([tr("位置"), "BGR", tr("颜色")])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._table, stretch=1)

        controls = QHBoxLayout()
        delete_btn = QPushButton(tr("删除选中"))
        delete_btn.clicked.connect(self._delete_selected)
        controls.addWidget(delete_btn)

        insert_btn = QPushButton(tr("插入"))
        insert_btn.clicked.connect(self._insert_after_selected)
        controls.addWidget(insert_btn)

        controls.addWidget(QLabel(tr("合并阈值:")))
        self._threshold = QSpinBox()
        self._threshold.setRange(0, 255)
        self._threshold.setValue(10)
        self._threshold.setFixedWidth(58)
        controls.addWidget(self._threshold)

        merge_btn = QPushButton(tr("合并相近"))
        merge_btn.clicked.connect(self._merge_similar)
        controls.addWidget(merge_btn)

        reverse_btn = QPushButton(tr("反转"))
        reverse_btn.clicked.connect(self._reverse)
        controls.addWidget(reverse_btn)
        controls.addStretch()
        layout.addLayout(controls)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok_btn = buttons.addButton(tr("确定"), QDialogButtonBox.ButtonRole.AcceptRole)
        apply_btn = buttons.addButton(tr("应用为曲线"), QDialogButtonBox.ButtonRole.ActionRole)
        ok_btn.clicked.connect(self._accept_confirm)
        apply_btn.clicked.connect(self._accept_as_curve)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._reload_table()

    @property
    def action(self) -> str:
        return self._action

    def stops(self) -> list[tuple[float, str]]:
        return list(self._stops)

    def sampled_colors(self) -> list[tuple[int, str]]:
        return [
            (idx + 1, color)
            for idx, (_pos, color) in enumerate(self._stops)
        ]

    def accept(self) -> None:
        self._accept_confirm()

    def _accept_confirm(self):
        stops = self._validated_table()
        if stops is None:
            return
        self._stops = self._normalize_stops(stops)
        self._action = "confirm"
        super().accept()

    def _accept_as_curve(self):
        stops = self._validated_table()
        if stops is None:
            return
        if len(stops) < 2:
            QMessageBox.warning(self, tr("采色结果不足"), tr("至少需要两个颜色点。"))
            return
        if len({pos for pos, _ in stops}) != len(stops):
            QMessageBox.warning(self, tr("错误"), tr("转换为曲线时，颜色点的位置不能重复。"))
            return
        self._stops = self._normalize_stops(stops)
        self._action = "apply_curve"
        super().accept()

    def _reload_table(self) -> None:
        previous = self._table.blockSignals(True)
        try:
            self._table.setRowCount(0)
            for pos, color in self._stops:
                self._append_row(pos, color)
            self._table.resizeColumnToContents(0)
        finally:
            self._table.blockSignals(previous)

    def _append_row(self, pos: float, color: str) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)

        pos_item = QTableWidgetItem(repr(float(pos)))
        pos_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, 0, pos_item)

        color = (color or "FFFFFF").ljust(6, "0")[:6].upper()
        color_item = QTableWidgetItem(color)
        color_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, 1, color_item)

        btn = QPushButton()
        btn.clicked.connect(lambda _checked=False, r=row: self._edit_color(r))
        self._set_button_color(btn, color)
        self._table.setCellWidget(row, 2, btn)

    def _read_table(self, *, excluded_rows: Optional[set[int]] = None) -> list[tuple[float, str]]:
        stops: list[tuple[float, str]] = []
        for row in range(self._table.rowCount()):
            if excluded_rows and row in excluded_rows:
                continue
            pos_item = self._table.item(row, 0)
            color_item = self._table.item(row, 1)
            try:
                pos = float(pos_item.text() if pos_item else "")
                if not math.isfinite(pos) or not 0.0 <= pos <= 1.0:
                    raise ValueError
            except ValueError:
                message = (f"Row {row + 1} position must be a finite number from 0 to 1."
                           if is_english() else f"第 {row + 1} 行位置必须是 0 到 1 之间的有限数值。")
                raise ValueError(message) from None
            color = _valid_bgr(color_item.text() if color_item else "")
            if color is None:
                message = (f"Row {row + 1} BGR color must be 6 hex digits."
                           if is_english() else f"第 {row + 1} 行 BGR 颜色应为 6 位十六进制。")
                raise ValueError(message)
            stops.append((pos, color))
        return stops

    def _validated_table(self, *, excluded_rows: Optional[set[int]] = None) -> Optional[list[tuple[float, str]]]:
        try:
            return self._read_table(excluded_rows=excluded_rows)
        except ValueError as error:
            QMessageBox.warning(self, tr("错误"), str(error))
            return None

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != 1:
            return
        color = _valid_bgr(item.text())
        button = self._table.cellWidget(item.row(), 2)
        if color is not None and isinstance(button, QPushButton):
            self._set_button_color(button, color)

    def _delete_selected(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        stops = self._validated_table(excluded_rows=set(rows))
        if stops is None:
            return
        if self._table.rowCount() - len(rows) < 2:
            QMessageBox.warning(self, tr("采色结果不足"), tr("至少保留两个颜色点。"))
            return
        self._stops = self._normalize_stops(stops)
        self._reload_table()

    def _insert_after_selected(self) -> None:
        rows = self._validated_table()
        if rows is None:
            return
        selected_rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        order = sorted(range(len(rows)), key=lambda index: rows[index][0])
        insert_at = order.index(selected_rows[-1]) + 1 if selected_rows else len(rows)
        stops = [rows[index] for index in order]

        if not stops:
            pos = 0.0
            color = "FFFFFF"
        elif insert_at <= 0:
            pos = stops[0][0] / 2.0
            color = stops[0][1]
        elif insert_at >= len(stops):
            pos = (stops[-1][0] + 1.0) / 2.0
            color = stops[-1][1]
            if stops[-1][0] == 1.0:
                # Leave room for an appended endpoint instead of creating a
                # duplicate position that disappears during curve conversion.
                previous = stops[-2][0] if len(stops) > 1 else 0.0
                stops[-1] = ((previous + 1.0) / 2.0, color)
        else:
            prev_pos, prev_color = stops[insert_at - 1]
            next_pos, _next_color = stops[insert_at]
            pos = (prev_pos + next_pos) / 2.0
            color = prev_color

        stops.insert(insert_at, (pos, color))
        self._stops = stops
        self._reload_table()
        self._table.selectRow(insert_at)

    def _merge_similar(self) -> None:
        stops = self._validated_table()
        if stops is None or len(stops) <= 2:
            return
        stops = self._normalize_stops(stops)
        threshold = float(self._threshold.value())
        merged = [stops[0]]
        for stop in stops[1:-1]:
            if _color_distance(merged[-1][1], stop[1]) > threshold:
                merged.append(stop)
        merged.append(stops[-1])
        self._stops = merged
        self._reload_table()

    def _reverse(self) -> None:
        stops = self._validated_table()
        if stops is None:
            return
        self._stops = [(1.0 - pos, color) for pos, color in reversed(self._normalize_stops(stops))]
        self._reload_table()

    def _edit_color(self, row: int) -> None:
        item = self._table.item(row, 1)
        if not item:
            return
        btn = self._table.cellWidget(row, 2)
        original = item.text().strip().upper()[:6]
        dialog = QColorDialog(bgr_to_qcolor(original), self)
        dialog.setWindowTitle(tr("选择采样颜色"))
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)

        def preview(color: QColor) -> None:
            if isinstance(btn, QPushButton) and color.isValid():
                self._set_button_color(btn, qcolor_to_bgr(color))

        dialog.currentColorChanged.connect(preview)
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted:
            color = dialog.selectedColor()
            if color.isValid():
                bgr = qcolor_to_bgr(color)
                item.setText(bgr)
                if isinstance(btn, QPushButton):
                    self._set_button_color(btn, bgr)
        else:
            if isinstance(btn, QPushButton):
                self._set_button_color(btn, original)

    def _set_button_color(self, btn: QPushButton, bgr: str) -> None:
        color = bgr_to_qcolor(bgr)
        btn.setText("")
        btn.setStyleSheet(
            "QPushButton {"
            f" background: rgb({color.red()}, {color.green()}, {color.blue()});"
            " border: 1px solid #666;"
            "}"
        )

    def _normalize_stops(self, stops: list[tuple[float, str]]) -> list[tuple[float, str]]:
        normalized: list[tuple[float, str]] = []
        for pos, color in stops:
            color = (color or "FFFFFF").ljust(6, "0")[:6].upper()
            normalized.append((max(0.0, min(1.0, float(pos))), color))
        normalized.sort(key=lambda item: item[0])
        return normalized

    def _stops_from_sampled_colors(
        self,
        sampled_colors: list[tuple[int, str]] | None,
    ) -> list[tuple[float, str]]:
        colors: list[str] = []
        for _key, color in sorted(sampled_colors or [], key=lambda item: int(item[0])):
            color_text = (color or "FFFFFF").ljust(6, "0")[:6].upper()
            if len(color_text) == 6:
                colors.append(color_text)
        if not colors:
            return []
        denom = max(len(colors) - 1, 1)
        return [(idx / denom, color) for idx, color in enumerate(colors)]


def _color_distance(a: str, b: str) -> float:
    ca = bgr_to_qcolor(a)
    cb = bgr_to_qcolor(b)
    return math.sqrt(
        (ca.red() - cb.red()) ** 2
        + (ca.green() - cb.green()) ** 2
        + (ca.blue() - cb.blue()) ** 2
    )


def _valid_bgr(text: str) -> Optional[str]:
    value = text.strip().upper().removeprefix("#")
    return value if re.fullmatch(r"[0-9A-F]{6}", value) else None
