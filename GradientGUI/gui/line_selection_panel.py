"""Line selection widget for multi-line preview/apply."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from engine.tag_parser import strip_tags
from gui.i18n import set_button_text, tr


class LineSelectionPanel(QFrame):
    active_line_changed = Signal(int)
    selection_changed = Signal()
    merge_changed = Signal(bool)
    settings_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("linePanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        header = QHBoxLayout()
        self._header = header
        self._header_trailing_widget: QWidget | None = None
        self._line_label = QLabel(tr("行:"))
        header.addWidget(self._line_label)

        self._select_all_btn = QPushButton(tr("全选"))
        set_button_text(self._select_all_btn, "全选", minimum=50, padding=24)
        self._select_all_btn.clicked.connect(self.select_all)
        header.addWidget(self._select_all_btn)

        self._select_current_btn = QPushButton(tr("仅当前"))
        set_button_text(self._select_current_btn, "仅当前", minimum=70, padding=24)
        self._select_current_btn.clicked.connect(self.select_current)
        header.addWidget(self._select_current_btn)

        self._merge_check = QCheckBox(tr("整体范围"))
        self._merge_check.setToolTip(tr("将多选行视为一个整体，共用合并后的渐变范围"))
        self._merge_check.toggled.connect(self.merge_changed)
        header.addWidget(self._merge_check)

        self._settings_btn = QPushButton(tr("设置"))
        set_button_text(self._settings_btn, "设置", minimum=58, padding=24)
        self._settings_btn.setToolTip(tr("设置哪些 tag 使用整体范围"))
        self._settings_btn.clicked.connect(self.settings_requested)
        header.addWidget(self._settings_btn)
        header.addStretch()
        layout.addLayout(header)

        self._list = QListWidget()
        self._list.setMinimumHeight(76)
        self._list.setMaximumHeight(92)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.currentRowChanged.connect(self.active_line_changed)
        self._list.itemSelectionChanged.connect(self.selection_changed)
        layout.addWidget(self._list)

    def set_events(self, events) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for i, event in enumerate(events):
            plain = strip_tags(event.text)[:30]
            self._list.addItem(f"[{i + 1}] {event.style}: {plain}")
            item = self._list.item(i)
            if item:
                item.setSelected(True)
        self._list.setCurrentRow(0 if events else -1)
        self._list.blockSignals(False)

    def selected_indices(self, active_idx: int, fallback: bool = True) -> list[int]:
        selected: list[int] = []
        for item in self._list.selectedItems():
            row = self._list.row(item)
            if row >= 0:
                selected.append(row)
        selected = sorted(set(selected))
        if not selected and fallback and 0 <= active_idx < self._list.count():
            selected = [active_idx]
        return selected

    def has_selection(self) -> bool:
        return bool(self._list.selectedItems())

    def select_all(self) -> None:
        self._list.blockSignals(True)
        self._list.selectAll()
        self._list.blockSignals(False)
        self.selection_changed.emit()

    def select_current(self, index: int | None = None, emit: bool = True) -> None:
        if index is None:
            index = self._list.currentRow()
        self._list.blockSignals(True)
        self._list.clearSelection()
        item = self._list.item(index)
        if item:
            item.setSelected(True)
        self._list.blockSignals(False)
        if emit:
            self.selection_changed.emit()

    def restore_selection(
        self,
        active_idx: int,
        selected_lines: list[int],
        total_lines: int,
    ) -> None:
        selected = [idx for idx in selected_lines if 0 <= idx < total_lines]
        if not selected and 0 <= active_idx < total_lines:
            selected = [active_idx]

        self._list.blockSignals(True)
        self._list.clearSelection()
        for idx in selected:
            item = self._list.item(idx)
            if item:
                item.setSelected(True)
        self._list.setCurrentRow(active_idx)
        self._list.blockSignals(False)

    def merge_range_enabled(self) -> bool:
        return self._merge_check.isChecked()

    def set_merge_range_enabled(self, enabled: bool) -> None:
        prev = self._merge_check.blockSignals(True)
        try:
            self._merge_check.setChecked(enabled)
        finally:
            self._merge_check.blockSignals(prev)

    def set_header_trailing_widget(self, widget: QWidget | None) -> None:
        if self._header_trailing_widget is widget:
            return
        if self._header_trailing_widget is not None:
            self._header.removeWidget(self._header_trailing_widget)
            self._header_trailing_widget.setParent(None)
        self._header_trailing_widget = widget
        if widget is not None:
            self._header.addWidget(widget)

    def retranslate_ui(self) -> None:
        self._line_label.setText(tr("行:"))
        set_button_text(self._select_all_btn, "全选", minimum=50, padding=24)
        set_button_text(self._select_current_btn, "仅当前", minimum=70, padding=24)
        self._merge_check.setText(tr("整体范围"))
        self._merge_check.setToolTip(tr("将多选行视为一个整体，共用合并后的渐变范围"))
        set_button_text(self._settings_btn, "设置", minimum=58, padding=24)
        self._settings_btn.setToolTip(tr("设置哪些 tag 使用整体范围"))
