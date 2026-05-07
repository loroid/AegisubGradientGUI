"""Spreadsheet-style curve node editor."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from engine.interpolation import CurveNode, InterpolationMode
from gui.i18n import is_english, tr


class CurveNodeTableDialog(QDialog):
    """Edit curve nodes in a table and paste tabular data from Excel."""

    MODE_NAMES = {
        InterpolationMode.LINEAR.value,
        InterpolationMode.SMOOTH.value,
        InterpolationMode.STEPPED.value,
        InterpolationMode.BEZIER.value,
        "",
    }

    def __init__(
        self,
        nodes: list[CurveNode],
        *,
        is_color: bool = False,
        is_text: bool = False,
        x_max: float = 100.0,
        integer_x: bool = False,
        integer_y: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("曲线节点表格"))
        self.setMinimumSize(760, 420)
        self.resize(980, 560)
        self._is_color = is_color
        self._is_text = is_text
        self._x_max = max(1.0, float(x_max or 100.0))
        self._integer_x = bool(integer_x)
        self._integer_y = bool(integer_y)
        self._nodes = [_clone_node(node) for node in nodes]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        axis_label = tr("整数帧" if self._integer_x else "位置")
        if is_english():
            hint = (
                f"Paste cells copied from Excel directly; the first column is "
                f"{axis_label} 0-{_fmt_float(self._x_max)}."
            )
        else:
            hint = f"可直接粘贴从 Excel 复制的单元格；第一列为{axis_label} 0-{_fmt_float(self._x_max)}。"
        layout.addWidget(QLabel(hint))

        self._table = QTableWidget(0, 0)
        self._table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self._table.setWordWrap(False)
        self._table.setStyleSheet(
            "QTableWidget::item { padding: 4px 8px; }"
            "QHeaderView::section { padding: 4px 8px; }"
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setMinimumSectionSize(68)
        self._table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.verticalHeader().setDefaultSectionSize(34)
        self._table.verticalHeader().setMinimumWidth(36)
        layout.addWidget(self._table, stretch=1)

        controls = QHBoxLayout()
        add_btn = QPushButton(tr("添加行"))
        add_btn.clicked.connect(self._add_row)
        controls.addWidget(add_btn)

        delete_btn = QPushButton(tr("删除选中"))
        delete_btn.clicked.connect(self._delete_selected)
        controls.addWidget(delete_btn)

        paste_btn = QPushButton(tr("粘贴"))
        paste_btn.clicked.connect(self._paste_from_clipboard)
        controls.addWidget(paste_btn)

        sort_btn = QPushButton(tr("排序"))
        sort_btn.clicked.connect(self._sort_table)
        controls.addWidget(sort_btn)
        controls.addStretch()
        layout.addLayout(controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("应用"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        QShortcut(QKeySequence("Ctrl+V"), self._table, self._paste_from_clipboard)
        self._reload_table()

    def nodes(self) -> list[CurveNode]:
        return [_clone_node(node) for node in self._nodes]

    def accept(self) -> None:
        try:
            nodes = self._read_table()
        except ValueError as exc:
            QMessageBox.warning(self, tr("节点数据错误"), str(exc))
            return
        if len(nodes) < 2:
            QMessageBox.warning(self, tr("节点不足"), tr("至少需要两个控制点。"))
            return
        nodes.sort(key=lambda node: node.x)
        nodes[0].x = 0.0
        nodes[-1].x = self._x_max
        self._nodes = nodes
        super().accept()

    def _headers(self) -> list[str]:
        x_header = tr("帧" if self._integer_x else "位置")
        if self._is_color:
            return [x_header, "BGR", tr("段插值")]
        if self._is_text:
            return [x_header, tr("文本"), tr("段插值")]
        if self._integer_y:
            return [
                x_header,
                tr("整数移动"),
                tr("入柄X"),
                tr("入柄Y"),
                tr("出柄X"),
                tr("出柄Y"),
                tr("段插值"),
            ]
        return [
            x_header,
            tr("数值"),
            tr("入柄X"),
            tr("入柄Y"),
            tr("出柄X"),
            tr("出柄Y"),
            tr("段插值"),
        ]

    def _reload_table(self) -> None:
        headers = self._headers()
        self._table.setColumnCount(len(headers))
        self._table.setHorizontalHeaderLabels(headers)
        self._table.setRowCount(0)
        for node in sorted(self._nodes, key=lambda n: n.x):
            self._append_node(node)
        self._table.resizeColumnsToContents()
        self._apply_column_spacing()

    def _apply_column_spacing(self) -> None:
        if self._is_color:
            widths = [84, 116, 112]
        elif self._is_text:
            widths = [84, 220, 112]
        else:
            widths = [84, 84, 84, 84, 84, 84, 112]
        for col, minimum in enumerate(widths[: self._table.columnCount()]):
            self._table.setColumnWidth(col, max(self._table.columnWidth(col), minimum))

    def _append_node(self, node: CurveNode) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        if self._is_color or self._is_text:
            values = [
                _fmt_float(node.x),
                node.value_str,
                node.segment_mode.value if node.segment_mode else "",
            ]
        else:
            values = [
                _fmt_float(node.x),
                _fmt_float(node.y),
                _fmt_float(node.handle_in_x),
                _fmt_float(node.handle_in_y),
                _fmt_float(node.handle_out_x),
                _fmt_float(node.handle_out_y),
                node.segment_mode.value if node.segment_mode else "",
            ]
        for col, value in enumerate(values):
            cell = QTableWidgetItem(str(value))
            cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, col, cell)

    def _add_row(self) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        mid = self._x_max / 2.0
        if self._integer_x:
            mid = round(mid)
        mid_x = _fmt_float(mid)
        defaults = [mid_x, "FFFFFF" if self._is_color else "", ""]
        if self._is_text:
            defaults = [mid_x, "", ""]
        elif not self._is_color:
            defaults = [
                mid_x,
                "0",
                _fmt_float(self._x_max / 2.0 - 5.0),
                "0",
                _fmt_float(self._x_max / 2.0 + 5.0),
                "0",
                "",
            ]
        for col, value in enumerate(defaults[: self._table.columnCount()]):
            self._table.setItem(row, col, QTableWidgetItem(value))

    def _delete_selected(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        if self._table.rowCount() - len(rows) < 2:
            QMessageBox.warning(self, tr("节点不足"), tr("至少保留两个控制点。"))
            return
        for row in rows:
            self._table.removeRow(row)

    def _sort_table(self) -> None:
        try:
            self._nodes = self._read_table()
        except ValueError as exc:
            QMessageBox.warning(self, tr("节点数据错误"), str(exc))
            return
        self._nodes.sort(key=lambda node: node.x)
        self._reload_table()

    def _paste_from_clipboard(self) -> None:
        text = QApplication.clipboard().text()
        if not text:
            return
        start = self._table.currentIndex()
        start_row = start.row() if start.isValid() else self._table.rowCount()
        start_col = start.column() if start.isValid() else 0
        if start_row < 0:
            start_row = self._table.rowCount()
        lines = [line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line]
        for r, line in enumerate(lines):
            cells = line.split("\t")
            row = start_row + r
            while row >= self._table.rowCount():
                self._table.insertRow(self._table.rowCount())
            for c, value in enumerate(cells):
                col = start_col + c
                if col >= self._table.columnCount():
                    break
                item = self._table.item(row, col)
                if item is None:
                    item = QTableWidgetItem()
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self._table.setItem(row, col, item)
                item.setText(value.strip())

    def _read_table(self) -> list[CurveNode]:
        nodes: list[CurveNode] = []
        for row in range(self._table.rowCount()):
            x_text = _cell_text(self._table, row, 0)
            if not x_text:
                continue
            try:
                x = max(0.0, min(self._x_max, float(x_text)))
            except ValueError as exc:
                if is_english():
                    raise ValueError(f"Row {row + 1} position is not a number.") from exc
                raise ValueError(f"第 {row + 1} 行位置不是数字。") from exc
            if self._integer_x:
                x = float(round(x))
            mode = _parse_mode(_cell_text(self._table, row, self._table.columnCount() - 1))
            if self._is_color:
                value = _cell_text(self._table, row, 1).upper().replace("#", "")
                if len(value) != 6:
                    if is_english():
                        raise ValueError(f"Row {row + 1} BGR color must be 6 hex digits.")
                    raise ValueError(f"第 {row + 1} 行 BGR 颜色应为 6 位十六进制。")
                node = CurveNode(x=x, y=0.0, value_str=value)
            elif self._is_text:
                node = CurveNode(x=x, y=0.0, value_str=_cell_text(self._table, row, 1))
            else:
                try:
                    y = float(_cell_text(self._table, row, 1) or "0")
                    hix = float(_cell_text(self._table, row, 2) or str(x - 10))
                    hiy = float(_cell_text(self._table, row, 3) or str(y))
                    hox = float(_cell_text(self._table, row, 4) or str(x + 10))
                    hoy = float(_cell_text(self._table, row, 5) or str(y))
                except ValueError as exc:
                    if is_english():
                        raise ValueError(f"Row {row + 1} contains an invalid number.") from exc
                    raise ValueError(f"第 {row + 1} 行包含非法数值。") from exc
                if self._integer_y:
                    y = float(round(y))
                    hiy = float(round(hiy))
                    hoy = float(round(hoy))
                if self._integer_x:
                    hix = float(round(hix))
                    hox = float(round(hox))
                node = CurveNode(
                    x=x,
                    y=y,
                    handle_in_x=hix,
                    handle_in_y=hiy,
                    handle_out_x=hox,
                    handle_out_y=hoy,
                )
            node.segment_mode = mode
            nodes.append(node)
        return nodes


def _clone_node(node: CurveNode) -> CurveNode:
    return CurveNode(
        x=node.x,
        y=node.y,
        value_str=node.value_str,
        handle_in_x=node.handle_in_x,
        handle_in_y=node.handle_in_y,
        handle_out_x=node.handle_out_x,
        handle_out_y=node.handle_out_y,
        segment_mode=node.segment_mode,
    )


def _parse_mode(text: str) -> Optional[InterpolationMode]:
    text = text.strip().lower()
    if not text:
        return None
    aliases = {
        "linear": InterpolationMode.LINEAR,
        "smooth": InterpolationMode.SMOOTH,
        "stepped": InterpolationMode.STEPPED,
        "step": InterpolationMode.STEPPED,
        "bezier": InterpolationMode.BEZIER,
    }
    if text not in aliases:
        if is_english():
            raise ValueError(f"Unknown interpolation mode: {text}")
        raise ValueError(f"未知插值类型: {text}")
    return aliases[text]


def _cell_text(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    return item.text().strip() if item else ""


def _fmt_float(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):.4f}".rstrip("0").rstrip(".")
