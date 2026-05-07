"""Dialog for selecting tags that use merged multi-line bounds."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from engine.tag_parser import TAG_INFO
from gui.i18n import tag_label, tr


class GroupRangeSettingsDialog(QDialog):
    def __init__(
        self,
        enabled_tags: list[str],
        selected_tags: set[str],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("整体范围设置"))
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._tag_list = QListWidget()
        for tag in enabled_tags:
            info = TAG_INFO.get(tag, {})
            item = QListWidgetItem(tag_label(tag, info.get("label", f"\\{tag}")))
            item.setData(Qt.ItemDataRole.UserRole, tag)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if tag in selected_tags
                else Qt.CheckState.Unchecked
            )
            self._tag_list.addItem(item)
        layout.addWidget(self._tag_list)

        select_row = QHBoxLayout()
        select_all_btn = QPushButton(tr("全选"))
        invert_btn = QPushButton(tr("反选"))
        select_all_btn.clicked.connect(self._select_all)
        invert_btn.clicked.connect(self._invert)
        select_row.addWidget(select_all_btn)
        select_row.addWidget(invert_btn)
        select_row.addStretch()
        layout.addLayout(select_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_tags(self) -> set[str]:
        selected: set[str] = set()
        for i in range(self._tag_list.count()):
            item = self._tag_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                selected.add(item.data(Qt.ItemDataRole.UserRole))
        return selected

    def _select_all(self) -> None:
        for i in range(self._tag_list.count()):
            self._tag_list.item(i).setCheckState(Qt.CheckState.Checked)

    def _invert(self) -> None:
        for i in range(self._tag_list.count()):
            item = self._tag_list.item(i)
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
