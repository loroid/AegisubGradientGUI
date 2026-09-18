"""Real Qt restore flows must not write stale editors over saved effect state."""

from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtWidgets import QApplication

from engine.ass_parser import ASSFile, ASSEvent, ASSStyle
from engine.interpolation import ColorSpace, CurveNode, InterpolationMode, make_default_nodes
from engine.path_model import PathSet
from gui.dependency_health import HealthReport
from gui.main_window import MainWindow
from gui.state_codec import preset_from_state, state_from_preset_data


class StateRestoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        source = ASSFile(
            styles=[ASSStyle(name="Default", fontname="Arial")],
            events=[ASSEvent(end="0:00:02.00", text=f"Item {i}") for i in range(3)],
        )
        with (
            patch("gui.main_window.parse_ass_file", return_value=source),
            patch("gui.main_window.run_startup_health_check", return_value=HealthReport([])),
            patch.object(MainWindow, "_start_update_check"),
        ):
            self.window = MainWindow(input_path=__file__)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window._preview_timer.stop)
        self.addCleanup(self.window._undo_timer.stop)
        self.window._tag_panel.get_row("1c")._check.setChecked(True)
        self.window._on_curve_key_selected("1c")

    def set_effect(self, changed=False):
        w = self.window
        w._show_editor_tabs()
        w._curve_editor.set_nodes(make_default_nodes(
            start_color="FF0000" if changed else "0000FF", end_color="00FF00",
        ), is_color=True)
        w._curve_editor.set_mode(InterpolationMode.SMOOTH if changed else InterpolationMode.BEZIER)
        w._on_curve_changed()
        w._animation_curve_editor.set_nodes([
            CurveNode(x=0, y=8 if changed else 2),
            CurveNode(x=10, y=-3 if changed else 4),
        ])
        w._animation_enabled_tags = set() if changed else {"1c"}
        w._animation_frame_steps = {"1c": 5 if changed else 2}
        w._animation_seam_blend_lengths = {"1c": 7 if changed else 3}
        w._sync_animation_panel_for_active_tag()
        w._curve_mirrors = {"1c": (changed, False)}
        w._curve_editor.set_mirror_state(changed, False)
        w._animation_curve_mirrors = {"1c": (False, changed)}
        w._animation_curve_editor.set_mirror_state(False, changed)
        w._group_range_tags = {"bord"} if changed else {"1c"}
        w._line_panel.set_merge_range_enabled(not changed)
        w._line_panel.set_reference_indices([2] if changed else [1], 3)
        w._tag_panel.set_path_smooth_map({"1c": changed})
        w._tag_panel.set_path_smooth_strength_map({"1c": 0.7 if changed else 0.3})
        w._sampling_paths = {0: {"1c": PathSet.from_ass_path(
            "m 0 0 l 2 0", sampling_frame=4 if changed else 2,
            sampled_points=[
                (0, 0, 0, "FFFFFF" if changed else "0000FF"),
                (0, 2, 0, "00FF00"),
            ],
        )}}
        return w._capture_state("Synthetic state")

    def test_restore_from_each_page_keeps_all_effect_fields(self):
        w = self.window
        for page in ("editor", "tag_overview", "animation_overview"):
            with self.subTest(page=page):
                original = self.set_effect()
                saved = copy.deepcopy(original)
                self.set_effect(changed=True)
                if page == "tag_overview":
                    w._show_tag_overview_tab()
                elif page == "animation_overview":
                    w._show_animation_overview_tab()
                w._restore_state(original)
                restored = w._capture_state(original.description)
                self.assertEqual(original, saved, "restoring must not mutate the saved snapshot")
                self.assertEqual(restored, saved)

    def test_color_space_selection_and_restore_reach_active_editor(self):
        w = self.window
        original = w._capture_state("Color space state")
        w._tag_panel._cs_combo.setCurrentText("HSL")
        self.assertEqual(w._curve_editor.view._color_space, ColorSpace.HSL)
        self.assertFalse(w._curve_editor._sample_editor_btn.isEnabled())
        w._restore_state(original)
        self.assertEqual(w._curve_editor.view._color_space, ColorSpace.RGB)

    def test_undo_redo_while_overview_is_open(self):
        w = self.window
        original = self.set_effect()
        w._undo._undo_stack.clear()
        w._undo._redo_stack.clear()
        w._undo.push(original)
        changed = self.set_effect(changed=True)
        w._undo.push(changed)
        w._undo_timer.stop()
        w._show_animation_overview_tab()
        w._do_undo()
        self.assertEqual(w._capture_state(original.description), original)
        w._do_redo()
        self.assertEqual(w._capture_state(changed.description), changed)

    def test_view_refresh_after_undo_does_not_clear_redo_or_queue_generation(self):
        w = self.window
        original = self.set_effect()
        w._push_undo("First effect")
        changed = self.set_effect(changed=True)
        w._push_undo("Second effect")
        w._do_undo()
        self.assertTrue(w._undo.can_redo)
        w._preview_timer.stop()
        w._animation_panel.set_frame_offset(10, emit=False)
        with patch.object(w, "_schedule_preview", wraps=w._schedule_preview) as schedule:
            w._curve_editor.view._width += 50
            w._curve_editor.view._redraw_all()
            w._animation_curve_editor.set_y_range(-20, 20)
            w._flush_pending_undo()
            schedule.assert_not_called()
        self.assertTrue(w._undo.can_redo)
        self.assertFalse(w._undo_timer.isActive())
        self.assertFalse(w._preview_timer.isActive())
        w._do_redo()
        self.assertEqual(w._capture_state(changed.description), changed)

    def test_preset_effect_restore_does_not_depend_on_open_overview(self):
        w = self.window
        original = self.set_effect()
        preset = preset_from_state(original)
        self.set_effect(changed=True)
        w._show_tag_overview_tab()
        state = state_from_preset_data(preset)
        state.reference_lines = original.reference_lines.copy()
        w._restore_state(state)
        self.assertEqual(w._capture_state(original.description), original)

    def test_failed_restore_rolls_back_effect_and_resumes_history(self):
        w = self.window
        original = self.set_effect()
        invalid = self.set_effect(changed=True)
        w._restore_state(original)
        invalid.animation_modes["1c"] = "not-an-interpolation-mode"
        with self.assertRaises(ValueError):
            w._restore_state(invalid)
        self.assertFalse(w._undo_suspended)
        self.assertEqual(w._capture_state(original.description), original)
        w._queue_undo("Synthetic edit")
        self.assertTrue(w._undo_timer.isActive())

    def test_failed_undo_or_redo_preserves_history_and_can_be_retried(self):
        w = self.window
        for redo in (False, True):
            with self.subTest(redo=redo):
                first = self.set_effect()
                w._undo._undo_stack.clear()
                w._undo._redo_stack.clear()
                w._last_undo_signature = None
                w._push_undo("First effect")
                second = self.set_effect(changed=True)
                w._push_undo("Second effect")
                if redo:
                    w._do_undo()
                command = w._do_redo if redo else w._do_undo
                before = w._capture_state()
                history = copy.deepcopy((w._undo._undo_stack, w._undo._redo_stack))
                buttons = (w._undo_btn.isEnabled(), w._redo_btn.isEnabled())
                apply_state = w._apply_restored_state
                calls = []

                def fail_once(state):
                    apply_state(state)
                    calls.append(state)
                    if len(calls) == 1:
                        raise RuntimeError("Synthetic interrupted restore")

                with (
                    patch.object(w, "_apply_restored_state", side_effect=fail_once),
                    patch("gui.main_window.QMessageBox.critical") as error,
                ):
                    command()
                error.assert_called_once()
                self.assertIn("Synthetic interrupted restore", error.call_args.args[-1])
                self.assertEqual(w._capture_state(), before)
                self.assertEqual((w._undo._undo_stack, w._undo._redo_stack), history)
                self.assertEqual((w._undo_btn.isEnabled(), w._redo_btn.isEnabled()), buttons)
                self.assertFalse(w._undo_suspended)
                command()
                expected = second if redo else first
                self.assertEqual(w._capture_state(expected.description), expected)

    def test_invalid_preset_reports_error_without_changing_effect(self):
        w = self.window
        invalid = self.set_effect(changed=True)
        invalid.animation_modes["1c"] = "not-an-interpolation-mode"
        original = self.set_effect()
        payload = preset_from_state(invalid)
        for recent in (False, True):
            with (
                self.subTest(recent=recent),
                patch("gui.main_window.preset_io.select_and_read_preset", return_value=("synthetic.ggpreset", payload)),
                patch("gui.main_window.preset_io.read_preset", return_value=payload),
                patch("gui.main_window.preset_io.remember_recent_preset") as remember,
                patch("gui.main_window.QMessageBox.critical") as error,
                patch.object(w, "_confirm_preset_load", return_value=True),
            ):
                if recent:
                    w._load_recent_preset("synthetic.ggpreset")
                else:
                    w._load_preset()
                error.assert_called_once()
                remember.assert_not_called()
                self.assertFalse(w._undo_suspended)
                self.assertEqual(w._capture_state(original.description), original)

    def test_restore_different_line_refreshes_paths_and_frame_range(self):
        w = self.window
        w._source_events[0].end = "0:00:10.00"
        original = self.set_effect()
        original.animation_state["preview_frame"] = 200
        w._on_active_line_changed(1)
        self.assertLess(w._animation_panel._total_frames, 200)
        w._restore_state(original)
        self.assertEqual(w._active_event_idx, 0)
        self.assertEqual(w._animation_panel._total_frames, w._active_event_total_frames())
        self.assertEqual(w._animation_panel.frame_offset(), 200)
        self.assertTrue(w._tag_panel.get_row("1c")._path_active)

    def test_invalid_preset_is_rejected_before_confirmation_or_history_changes(self):
        w = self.window
        self.set_effect()
        w._push_undo("First effect")
        self.set_effect(changed=True)
        w._push_undo("Second effect")
        w._do_undo()
        original = w._capture_state("Before invalid preset")
        history = copy.deepcopy((w._undo._undo_stack, w._undo._redo_stack))
        self.assertTrue(w._undo.can_redo)
        payload = copy.deepcopy(preset_from_state(original))
        payload["animation"]["curves"]["nodes"]["1c"][0]["y"] = float("nan")
        for recent in (False, True):
            with (
                self.subTest(recent=recent),
                patch("gui.main_window.preset_io.select_and_read_preset", return_value=("synthetic.ggpreset", payload)),
                patch("gui.main_window.preset_io.read_preset", return_value=payload),
                patch("gui.main_window.preset_io.remember_recent_preset") as remember,
                patch("gui.main_window.QMessageBox.critical") as error,
                patch.object(w, "_confirm_preset_load", return_value=True) as confirm,
                patch.object(w, "_restore_state", wraps=w._restore_state) as restore,
            ):
                if recent:
                    w._load_recent_preset("synthetic.ggpreset")
                else:
                    w._load_preset()
                error.assert_called_once()
                self.assertIn("animation.curves.nodes.1c[0].y", error.call_args.args[-1])
                confirm.assert_not_called()
                restore.assert_not_called()
                remember.assert_not_called()
                self.assertEqual(w._capture_state(original.description), original)
                self.assertEqual((w._undo._undo_stack, w._undo._redo_stack), history)

    def test_all_tag_gui_state_is_accepted_by_preset_validation(self):
        w = self.window
        for tag, row in w._tag_panel._rows.items():
            row._check.setChecked(True)
        original = w._capture_state("All tags")
        restored = state_from_preset_data(preset_from_state(original))
        restored.reference_lines = original.reference_lines.copy()
        self.assertEqual(restored, original)

    def test_preset_omitted_tags_do_not_keep_previous_gradients_enabled(self):
        w = self.window
        state = self.set_effect()
        state.tag_configs = {"1c": state.tag_configs["1c"]}
        w._tag_panel.get_row("fscx")._check.setChecked(True)
        self.assertIn("fscx", w._tag_panel.get_enabled_tags())
        w._restore_state(state)
        self.assertEqual(w._tag_panel.get_enabled_tags(), ["1c"])


if __name__ == "__main__":
    unittest.main()
