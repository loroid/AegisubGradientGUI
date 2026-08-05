"""Line selection widget for multi-line preview/apply."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from engine.tag_parser import strip_tags
from gui.i18n import set_button_text, tr


REFERENCE_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def processing_line_indices(
    selected_indices: list[int],
    reference_indices: list[int],
    active_idx: int,
    total_lines: int,
    fallback: bool = True,
) -> list[int]:
    references = {idx for idx in reference_indices if 0 <= idx < total_lines}
    selected = sorted(
        {
            idx
            for idx in selected_indices
            if 0 <= idx < total_lines and idx not in references
        }
    )
    if (
        not selected
        and fallback
        and 0 <= active_idx < total_lines
        and active_idx not in references
    ):
        return [active_idx]
    return selected


class _ReferenceAwareListWidget(QListWidget):
    reference_clicked = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reference_press_row = -1

    def _reference_hit(self, position) -> int:
        index = self.indexAt(position)
        if not index.isValid():
            return -1
        delegate = self.itemDelegate(index)
        if not isinstance(delegate, _ReferenceLineDelegate):
            return -1
        option = QStyleOptionViewItem()
        option.rect = self.visualRect(index)
        option.widget = self
        if delegate.is_reference_area(option, position):
            return index.row()
        return -1

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            row = self._reference_hit(event.position().toPoint())
            if row >= 0:
                self._reference_press_row = row
                event.accept()
                return
        self._reference_press_row = -1
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        row = self._reference_press_row
        self._reference_press_row = -1
        if row >= 0:
            if (
                event.button() == Qt.MouseButton.LeftButton
                and self._reference_hit(event.position().toPoint()) == row
            ):
                self.reference_clicked.emit(row)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _ReferenceLineDelegate(QStyledItemDelegate):
    reference_toggled = Signal(int, bool)

    def _reference_rect(self, option: QStyleOptionViewItem) -> QRect:
        label = tr("参照")
        style = option.widget.style() if option.widget else QApplication.style()
        indicator = style.pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth)
        width = max(70, option.fontMetrics.horizontalAdvance(label) + indicator + 20)
        return QRect(
            option.rect.right() - width - 4,
            option.rect.top(),
            width,
            option.rect.height(),
        )

    def is_reference_area(self, option: QStyleOptionViewItem, position) -> bool:
        return self._reference_rect(option).contains(position)

    def paint(self, painter, option, index) -> None:
        reference_rect = self._reference_rect(option)
        text_option = QStyleOptionViewItem(option)
        available = max(0, reference_rect.left() - option.rect.left() - 10)
        text_option.text = option.fontMetrics.elidedText(
            str(index.data() or ""),
            Qt.TextElideMode.ElideRight,
            available,
        )
        super().paint(painter, text_option, index)

        button = QStyleOptionButton()
        button.rect = reference_rect
        button.text = tr("参照")
        button.palette = option.palette
        button.state = QStyle.StateFlag.State_Enabled
        if bool(index.data(REFERENCE_ROLE)):
            button.state |= QStyle.StateFlag.State_On
        else:
            button.state |= QStyle.StateFlag.State_Off
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_CheckBox, button, painter)

    def editorEvent(self, event, model, option, index) -> bool:
        in_reference_area = (
            event.button() == Qt.MouseButton.LeftButton
            and self._reference_rect(option).contains(event.position().toPoint())
        )
        if in_reference_area:
            # Consume the press as well as the release.  QListWidget otherwise
            # clears an existing multi-selection before the checkbox release.
            if event.type() == QEvent.Type.MouseButtonRelease:
                enabled = not bool(index.data(REFERENCE_ROLE))
                model.setData(index, enabled, REFERENCE_ROLE)
                self.reference_toggled.emit(index.row(), enabled)
            return True
        return super().editorEvent(event, model, option, index)


class LineSelectionPanel(QFrame):
    active_line_changed = Signal(int)
    selection_changed = Signal()
    merge_changed = Signal(bool)
    reference_lines_changed = Signal()
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

        self._list = _ReferenceAwareListWidget()
        self._reference_delegate = _ReferenceLineDelegate(self._list)
        self._reference_delegate.reference_toggled.connect(
            lambda _row, _enabled: self.reference_lines_changed.emit()
        )
        self._list.reference_clicked.connect(self._toggle_reference)
        self._list.setItemDelegate(self._reference_delegate)
        self._list.setToolTip(
            tr("在传入行右端开启参照后，该行只在预览中原样显示，不参与渐变处理")
        )
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
                item.setData(REFERENCE_ROLE, False)
                item.setSelected(True)
        self._list.setCurrentRow(0 if events else -1)
        self._list.blockSignals(False)

    def selected_indices(self, active_idx: int, fallback: bool = True) -> list[int]:
        return processing_line_indices(
            self.raw_selected_indices(),
            self.reference_indices(),
            active_idx,
            self._list.count(),
            fallback,
        )

    def raw_selected_indices(self) -> list[int]:
        return sorted(
            {
                self._list.row(item)
                for item in self._list.selectedItems()
                if self._list.row(item) >= 0
            }
        )

    def reference_indices(self) -> list[int]:
        return [
            idx
            for idx in range(self._list.count())
            if bool(self._list.item(idx).data(REFERENCE_ROLE))
        ]

    def _toggle_reference(self, row: int) -> None:
        item = self._list.item(row)
        if item is None:
            return
        enabled = not bool(item.data(REFERENCE_ROLE))
        item.setData(REFERENCE_ROLE, enabled)
        self._list.viewport().update()
        self.reference_lines_changed.emit()

    def set_reference_indices(self, indices: list[int], total_lines: int) -> None:
        references = {idx for idx in indices if 0 <= idx < total_lines}
        for idx in range(self._list.count()):
            self._list.item(idx).setData(REFERENCE_ROLE, idx in references)
        self._list.viewport().update()

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
        self._list.setToolTip(
            tr("在传入行右端开启参照后，该行只在预览中原样显示，不参与渐变处理")
        )
        self._list.viewport().update()
