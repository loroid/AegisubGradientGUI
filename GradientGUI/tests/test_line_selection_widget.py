"""Exercise selection and reference controls using real Qt widgets."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

from engine.ass_parser import ASSEvent
from gui.line_selection_panel import LineSelectionPanel


class LineSelectionWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = LineSelectionPanel()
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.close)
        self.panel.set_events([ASSEvent(text=f"Item {idx}") for idx in range(3)])

    def test_input_lines_are_initially_all_selected(self):
        self.assertEqual(self.panel.raw_selected_indices(), [0, 1, 2])
        self.assertEqual(self.panel._list.currentRow(), 0)

    def test_restore_selection_keeps_multiple_rows_and_the_active_row(self):
        for active, selected in ((2, [0, 2]), (0, [1, 2]), (1, [0, 2])):
            with self.subTest(active=active, selected=selected):
                self.panel.restore_selection(active, selected, 3)
                self.assertEqual(self.panel.raw_selected_indices(), selected)
                self.assertEqual(self.panel._list.currentRow(), active)

    def test_current_only_button_uses_the_active_row(self):
        self.panel._list.setCurrentRow(2)
        self.panel.select_all()
        self.panel._select_current_btn.click()
        self.assertEqual(self.panel.raw_selected_indices(), [2])

    def test_reference_toggle_keeps_selection(self):
        self.panel.select_all()
        self.panel._toggle_reference(1)
        self.assertEqual(self.panel.raw_selected_indices(), [0, 1, 2])
        self.assertEqual(self.panel.selected_indices(0), [0, 2])

    def test_clicking_reference_checkbox_keeps_selection(self):
        self.panel.resize(800, 150)
        self.panel.show()
        self.app.processEvents()
        self.panel.select_all()
        view = self.panel._list
        option = QStyleOptionViewItem()
        option.widget = view
        option.rect = view.visualItemRect(view.item(1))
        position = self.panel._reference_delegate._reference_rect(option).center()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(self.panel.raw_selected_indices(), [0, 1, 2])
        self.assertEqual(self.panel.reference_indices(), [1])

    def test_keyboard_events_do_not_enter_mouse_only_reference_handling(self):
        view = self.panel._list
        option = QStyleOptionViewItem()
        option.widget = view
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier)
        self.panel._reference_delegate.editorEvent(event, view.model(), option, view.model().index(0, 0))
        self.assertEqual(self.panel.reference_indices(), [])


if __name__ == "__main__":
    unittest.main()
