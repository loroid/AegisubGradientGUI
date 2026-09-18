"""History commits only after a successful restore."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from gui.undo_manager import UndoManager, UndoState


def snapshot(angle):
    return UndoState(tag_configs={}, tag_curves={}, tag_modes={}, angle=angle)


class UndoManagerTest(unittest.TestCase):
    def test_failed_moves_keep_both_stacks_and_do_not_notify(self):
        for redo in (False, True):
            with self.subTest(redo=redo):
                manager = UndoManager()
                states = [snapshot(angle) for angle in (0, 10, 20)]
                for state in states:
                    manager.push(state)
                if redo:
                    manager.undo()
                before = copy.deepcopy((manager._undo_stack, manager._redo_stack))
                notifications = []
                manager.set_change_callback(lambda: notifications.append(True))

                def fail(state):
                    self.assertEqual(state, states[-1] if redo else states[-2])
                    self.assertEqual((manager._undo_stack, manager._redo_stack), before)
                    raise RuntimeError("Synthetic restore failure")

                move = manager.redo if redo else manager.undo
                with self.assertRaisesRegex(RuntimeError, "Synthetic restore failure"):
                    move(restore=fail)
                self.assertEqual((manager._undo_stack, manager._redo_stack), before)
                self.assertEqual(notifications, [])
                restored = []
                self.assertEqual(move(restore=restored.append), states[-1] if redo else states[-2])
                self.assertEqual(restored, [states[-1] if redo else states[-2]])
                self.assertEqual(notifications, [True])

    def test_success_notification_sees_restored_effect_and_committed_history(self):
        manager = UndoManager()
        first, second = snapshot(0), snapshot(10)
        manager.push(first)
        manager.push(second)
        effect = [second]
        observed = []
        manager.set_change_callback(lambda: observed.append((effect[0], manager.undo_count, manager.redo_count)))

        def restore(state):
            effect[0] = state

        manager.undo(restore=restore)
        manager.redo(restore=restore)
        self.assertEqual(observed, [(first, 0, 1), (second, 1, 0)])

    def test_empty_history_does_not_call_restore_or_notify(self):
        manager = UndoManager()
        restored, notifications = [], []
        for states in ([], [snapshot(0)]):
            for state in states:
                manager.push(state)
            manager.set_change_callback(lambda: notifications.append(True))
            notifications.clear()
            self.assertIsNone(manager.undo(restore=restored.append))
            self.assertIsNone(manager.redo(restore=restored.append))
            self.assertEqual(restored, [])
            self.assertEqual(notifications, [])

    def test_history_limit_and_new_edit_branch_preserve_expected_states(self):
        manager = UndoManager()
        for value in range(manager.MAX_HISTORY + 5):
            manager.push(snapshot(value))
        self.assertEqual(manager.undo_count, manager.MAX_HISTORY - 1)
        for expected in range(manager.MAX_HISTORY + 3, 4, -1):
            self.assertEqual(manager.undo().angle, expected)
        self.assertFalse(manager.can_undo)
        self.assertEqual(manager.redo().angle, 6)
        manager.push(snapshot(1000))
        self.assertFalse(manager.can_redo)
        self.assertEqual(manager.undo().angle, 6)


if __name__ == "__main__":
    unittest.main()
