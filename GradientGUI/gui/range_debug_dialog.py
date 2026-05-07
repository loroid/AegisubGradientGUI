"""Range debug table for inspecting generated gradient bounds."""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from gui.i18n import tr


class RangeDebugDialog(QDialog):
    """Read-only diagnostics for per-line range and strip generation."""

    COLUMNS = [
        "行",
        "Tag",
        "范围来源",
        "libass",
        "原 clip",
        "生成范围",
        "整体范围",
        "投影范围",
        "clip 矩形",
        "输出行",
    ]

    def __init__(self, rows: list[dict[str, Any]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("范围调试"))
        self.resize(1180, 620)
        self._rows = rows

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        layout.addWidget(QLabel(tr("当前选中字幕行的范围、投影、clip 和输出行数。选中一行可查看完整细节。")))

        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels([tr(col) for col in self.COLUMNS])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.itemSelectionChanged.connect(self._update_details)
        layout.addWidget(self._table, stretch=3)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setMaximumBlockCount(2000)
        layout.addWidget(self._details, stretch=2)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._reload_table()

    def _reload_table(self) -> None:
        self._table.setRowCount(0)
        for item in self._rows:
            row = self._table.rowCount()
            self._table.insertRow(row)
            values = [
                str(item.get("line", "")),
                ", ".join(item.get("enabled_tags", [])) or "-",
                str(item.get("range_source", "-")),
                _fmt_rect(item.get("libass_bounds")),
                _fmt_rect(item.get("source_clip")),
                _fmt_rect(item.get("base_bounds")),
                _fmt_rect(item.get("group_bounds")),
                _fmt_range(item.get("event_projected_range")),
                _fmt_rect(item.get("clip_rect")),
                str(item.get("strip_count", "-")),
            ]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self._table.setItem(row, col, cell)
        if self._rows:
            self._table.selectRow(0)
        self._table.resizeColumnsToContents()

    def _update_details(self) -> None:
        selected = self._table.selectedIndexes()
        if not selected:
            self._details.clear()
            return
        row = selected[0].row()
        if not (0 <= row < len(self._rows)):
            self._details.clear()
            return
        self._details.setPlainText(
            json.dumps(self._rows[row], ensure_ascii=False, indent=2, default=str)
        )


def _fmt_rect(rect) -> str:
    if not rect:
        return "-"
    try:
        x1, y1, x2, y2 = rect
        return f"{x1:.1f},{y1:.1f},{x2:.1f},{y2:.1f}"
    except Exception:
        return str(rect)


def _fmt_range(values) -> str:
    if not values:
        return "-"
    try:
        g1, g2, p1, p2 = values
        return f"g {g1:.1f}-{g2:.1f} / p {p1:.1f}-{p2:.1f}"
    except Exception:
        return str(values)
