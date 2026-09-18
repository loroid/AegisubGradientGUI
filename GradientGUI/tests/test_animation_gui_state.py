"""Animation edits must belong to their tag, not to the current display state."""

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

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from engine.ass_parser import ASSFile, ASSEvent, ASSStyle
from engine.interpolation import CurveNode, InterpolationMode, make_default_nodes, mirror_nodes_horizontal
from engine.path_model import PathSet
from gui.curve_keys import curve_keys_for_tag
from gui.curve_node_table import CurveNodeTableDialog
from gui.dependency_health import HealthReport
from gui.gradient_generation import _animation_move_for_tag, generate_gradient_events
from gui.main_window import MainWindow
from gui.state_codec import preset_from_state, state_from_preset_data


class AnimationGuiStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.source = ASSFile(
            styles=[ASSStyle(name="Default", fontname="Arial")],
            events=[ASSEvent(start=start, end=end, text=text) for start, end, text in (
                ("0:00:00.00", "0:00:01.00", "ABCD"),
                ("0:00:05.00", "0:00:06.00", "EFGH"),
                ("0:00:00.00", "0:00:00.50", "IJKL"),
                ("0:00:00.00", "0:00:02.00", "MNOP"),
                ("0:00:00.00", "0:00:00.05", "QRST"),
            )],
        )
        for index, (first_frame, last_frame) in enumerate(((0, 19), (100, 119), (0, 9), (0, 39), (0, 0)), 1):
            self.source.gradient_metadata.update({
                f"Event Frame Start {index}": str(first_frame),
                f"Event Frame End {index}": str(last_frame),
                f"Event Start MS {index}": str(first_frame * 50),
                f"Event End MS {index}": str((last_frame + 1) * 50),
                f"Event Frame Times {index}": ",".join(
                    f"{frame}:{frame * 50}" for frame in range(first_frame, last_frame + 2)
                ),
            })
        with (
            patch("gui.main_window.parse_ass_file", return_value=self.source),
            patch("gui.main_window.run_startup_health_check", return_value=HealthReport([])),
            patch.object(MainWindow, "_start_update_check"),
        ):
            self.window = MainWindow(input_path=__file__)
        self.addCleanup(self.window.deleteLater)
        self.addCleanup(self.window.close)
        self.addCleanup(self.window._loop_preview_timer.stop)
        self.addCleanup(self.window._preview_timer.stop)
        self.addCleanup(self.window._undo_timer.stop)

    def seed_animation(self, tag, *, frame_step=2, seam=3):
        w = self.window
        w._tag_panel.get_row(tag)._check.setChecked(True)
        w._on_tag_selected(tag)
        nodes = [CurveNode(
            x=x, y=y, handle_in_x=hix, handle_in_y=y - 1,
            handle_out_x=hox, handle_out_y=y + 2, segment_mode=mode,
        ) for x, y, hix, hox, mode in (
            (0, 1.0, 0, 2, InterpolationMode.BEZIER),
            (5, 5.125, 4, 7, InterpolationMode.STEPPED),
            (11, -2.375, 9, 13, InterpolationMode.SMOOTH),
            (19, 3.5, 16, 19, None),
        )]
        w._animation_curve_editor.set_nodes(nodes)
        w._animation_curve_editor.set_mode(InterpolationMode.BEZIER)
        w._animation_panel._enabled.setChecked(True)
        w._animation_panel._frame_step.setValue(frame_step)
        w._animation_panel._seam_blend_length.setValue(seam)
        return self.animation_snapshot()

    def animation_snapshot(self):
        state = self.window._capture_state("Animation snapshot")
        panel = state.animation_state
        return copy.deepcopy({
            "curves": state.animation_curves,
            "modes": state.animation_modes,
            "mirrors": state.animation_curve_mirrors,
            "enabled_tags": panel["enabled_tags"],
            "frame_steps": panel["frame_steps"],
            "seams": panel["seam_blend_lengths"],
            "use_transform": panel["use_transform"],
        })

    def click_tag(self, tag):
        QTest.mouseClick(self.window._tag_panel.get_row(tag), Qt.MouseButton.LeftButton, pos=QPoint(2, 2))

    def test_clicking_a_disabled_tag_does_not_erase_its_animation(self):
        w = self.window
        for tag in ("1c", "fscx", "pos:x", "pos:y", "org:x", "org:y"):
            with self.subTest(tag=tag):
                original = self.seed_animation(tag)
                w._tag_panel.get_row(tag)._check.setChecked(False)
                self.click_tag(tag)
                w._build_settings()
                self.assertEqual(self.animation_snapshot(), original)
                w._tag_panel.get_row(tag)._check.setChecked(True)
                self.assertTrue(w._animation_panel._enabled.isChecked())
                self.assertEqual(w._animation_panel.frame_step(), 2)
                self.assertEqual(w._animation_panel.seam_blend_length(), 3)

    def test_no_tag_animation_controls_cannot_edit_a_hidden_disabled_tag(self):
        w = self.window
        self.seed_animation("1c")
        w._tag_panel.get_row("1c")._check.setChecked(False)
        self.click_tag("1c")
        self.assertFalse(w._animation_panel._enabled.isEnabled())
        self.assertFalse(w._animation_panel._frame_step.isEnabled())
        self.assertFalse(w._animation_panel._seam_blend_length.isEnabled())
        self.assertTrue(w._animation_panel._use_transform.isEnabled())

    def test_tag_and_axis_switches_preserve_each_tag_animation(self):
        w = self.window
        for index, tag in enumerate(("1c", "fscx", "pos:x", "pos:y", "org:x", "org:y")):
            self.seed_animation(tag, frame_step=index + 2, seam=index + 3)
        original = self.animation_snapshot()
        for tag in ("1c", "fscx", "pos:x", "org:y", "pos:y", "org:x", "1c"):
            for key in curve_keys_for_tag(tag):
                w._on_curve_key_selected(key)
                w._show_animation_overview_tab()
                w._show_tag_overview_tab()
                w._show_editor_tabs()
                w._build_settings()
                self.assertEqual(self.animation_snapshot(), original)

    def test_switching_between_equal_duration_lines_preserves_animation(self):
        w = self.window
        original = self.seed_animation("1c")
        for index in (1, 0, 1, 0):
            w._on_active_line_changed(index)
            w._build_settings()
            self.assertEqual(self.animation_snapshot(), original)

    def test_viewing_a_shorter_line_does_not_rewrite_the_shared_curve(self):
        w = self.window
        original = self.seed_animation("1c")
        for index in (2, 3, 0, 4, 2, 0):
            w._on_active_line_changed(index)
            w._build_settings()
            self.assertEqual(self.animation_snapshot(), original)

    def test_editor_view_range_and_table_do_not_pin_nodes_to_the_current_line(self):
        w = self.window
        self.seed_animation("1c")
        expected = copy.deepcopy(w._animation_curves["1c"])
        expected[0].handle_out_x = 2.125
        w._animation_curves["1c"] = copy.deepcopy(expected)
        for index, last in ((2, 9), (3, 39), (4, 0), (0, 19)):
            w._on_active_line_changed(index)
            view = w._animation_curve_editor.view
            self.assertEqual(view._active_x_max, last)
            self.assertEqual(view.x_max(), max(last, 19))
            self.assertEqual(view.current_nodes(), expected)
            self.assertEqual(w._animation_overview._rows[0].nodes, expected)
            dialog = CurveNodeTableDialog(
                view.current_nodes(), integer_x=True, x_max=view.x_max(),
                pin_endpoints_to_range=False,
            )
            self.addCleanup(dialog.deleteLater)
            dialog._sort_table()
            dialog.accept()
            self.assertEqual(dialog.nodes(), expected)

    def test_dragging_endpoint_y_after_duration_change_keeps_its_frame(self):
        w = self.window
        self.seed_animation("1c")
        w._on_active_line_changed(3)
        view = w._animation_curve_editor.view
        endpoint = view._node_items[-1]
        endpoint.setPos(view.value_x_to_scene(39), view.value_y_to_scene(4))
        self.assertEqual(endpoint.curve_node.x, 19)
        self.assertAlmostEqual(endpoint.curve_node.y, 4)
        self.assertEqual(w._animation_curves["1c"][-1].x, 19)

    def test_mouse_group_drag_publishes_complete_animation_settings_during_refresh(self):
        w = self.window
        for tag in ("1c", "fscx", "pos:x", "org:y"):
            with self.subTest(tag=tag):
                self.seed_animation(tag)
                w._editor_tabs.setCurrentIndex(1)
                w.show()
                self.app.processEvents()
                w._preview_timer.stop()
                w._undo_timer.stop()
                view = w._animation_curve_editor.view
                for item in view._node_items[1:3]:
                    item.setSelected(True)
                item = view._node_items[1]
                start = view.mapFromScene(item.pos())
                snapshots = []

                def record():
                    snapshots.append(copy.deepcopy(w._build_settings().animation.shift_curves[tag]))

                def mouse(event_type, delta):
                    position = start + QPoint(delta, 0)
                    self.app.sendEvent(view.viewport(), QMouseEvent(
                        event_type, QPointF(position), QPointF(view.viewport().mapToGlobal(position)),
                        Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton,
                        Qt.MouseButton.NoButton if event_type == QEvent.Type.MouseButtonRelease else Qt.MouseButton.LeftButton,
                        Qt.KeyboardModifier.ControlModifier,
                    ))

                view.curve_changed.connect(record)
                try:
                    mouse(QEvent.Type.MouseButtonPress, 0)
                    mouse(QEvent.Type.MouseMove, round(view.plot_rect().width() * 2 / 19))
                    w._sync_animation_curve_y_range()
                    self.assertIs(view.scene().mouseGrabberItem(), item)
                    delta = round(view.plot_rect().width() * 3 / 19)
                    mouse(QEvent.Type.MouseMove, delta)
                    mouse(QEvent.Type.MouseButtonRelease, delta)
                    self.app.processEvents()
                finally:
                    view.curve_changed.disconnect(record)
                self.assertEqual(len(snapshots), 2)
                for nodes, x in zip(snapshots, (7, 8)):
                    self.assertEqual([n.x for n in nodes], [0, x, x + 6, 19])
                    self.assertEqual([n.y for n in nodes], [1.0, 5.125, -2.375, 3.5])
                self.assertEqual(w._animation_curves[tag], snapshots[-1])
                self.assertEqual(view.current_nodes(), snapshots[-1])
                self.assertEqual(sum(item.isSelected() for item in view._node_items), 2)

    def test_late_frame_edit_survives_visiting_a_shorter_line(self):
        w = self.window
        self.seed_animation("1c")
        w._on_active_line_changed(3)
        view = w._animation_curve_editor.view
        view.add_batch_nodes(
            count=1, start_x=25, interval=1, interval_rule="constant",
            start_y=8, value_step=0, value_rule="constant",
        )
        expected = self.animation_snapshot()
        self.assertIn(25, [node["x"] for node in expected["curves"]["1c"]])
        w._on_active_line_changed(2)
        self.assertEqual(w._animation_curve_editor.view.x_max(), 25)
        self.assertEqual(self.animation_snapshot(), expected)

    def test_mirror_toggle_across_different_durations_restores_original_frames(self):
        w = self.window
        original = self.seed_animation("1c")
        original["mirrors"]["1c"] = (False, False)
        w._animation_curve_editor._mirror_h_btn.setChecked(True)
        mirrored = self.animation_snapshot()
        w._on_active_line_changed(3)
        self.assertEqual(self.animation_snapshot(), mirrored)
        w._animation_curve_editor._mirror_h_btn.setChecked(False)
        self.assertEqual(self.animation_snapshot(), original)

    def test_mirror_result_does_not_depend_on_the_current_line_duration(self):
        w = self.window
        self.seed_animation("1c")
        w._animation_curve_editor._mirror_h_btn.setChecked(True)
        expected = self.animation_snapshot()
        w._animation_curve_editor._mirror_h_btn.setChecked(False)
        w._on_active_line_changed(3)
        w._animation_curve_editor._mirror_h_btn.setChecked(True)
        self.assertEqual(self.animation_snapshot(), expected)

    def test_mirrored_movement_direction_survives_preset_and_line_switches(self):
        w = self.window
        self.seed_animation("1c")
        expected = mirror_nodes_horizontal(w._animation_curves["1c"])
        w._animation_curve_editor._mirror_h_btn.setChecked(True)
        self.assertEqual(w._animation_curves["1c"], expected)
        preset = preset_from_state(w._capture_state("Mirrored animation"))
        w._animation_curve_editor._mirror_h_btn.setChecked(False)
        w._restore_state(state_from_preset_data(preset))
        for index in (2, 3, 4, 0):
            w._on_active_line_changed(index)
            self.assertEqual(w._animation_curves["1c"], expected)
            self.assertEqual(w._animation_curve_editor.get_nodes(), expected)
            self.assertEqual(w._build_settings().animation.shift_curves["1c"], expected)

    def test_preset_load_on_shorter_lines_preserves_authored_frames(self):
        w = self.window
        expected = self.seed_animation("1c")
        preset = preset_from_state(w._capture_state("Authored animation"))
        for index in (2, 3, 4):
            with self.subTest(index=index):
                w._on_active_line_changed(index)
                with (
                    patch("gui.main_window.preset_io.select_and_read_preset", return_value=("animation.ggpreset", preset)),
                    patch("gui.main_window.preset_io.remember_recent_preset"),
                    patch.object(w, "_confirm_preset_load", return_value=True),
                ):
                    w._load_preset()
                self.assertEqual(w._active_event_idx, index)
                self.assertEqual(self.animation_snapshot(), expected)
                round_trip = state_from_preset_data(preset_from_state(w._capture_state()))
                self.assertEqual(round_trip.animation_curves, expected["curves"])

    def test_undo_redo_restores_animation_after_changing_line_duration(self):
        w = self.window
        self.seed_animation("1c")
        original = w._capture_state("Before animation edit")
        w._undo._undo_stack.clear()
        w._undo._redo_stack.clear()
        w._undo.push(original)
        w._on_active_line_changed(3)
        view = w._animation_curve_editor.view
        nodes = copy.deepcopy(view.current_nodes())
        nodes[-1].y = 7
        view.set_nodes(nodes)
        w._animation_panel._frame_step.setValue(4)
        changed = w._capture_state("After animation edit")
        w._undo.push(changed)
        w._on_active_line_changed(4)
        w._undo_timer.stop()
        w._show_animation_overview_tab()
        w._do_undo()
        self.assertEqual(w._capture_state(original.description), original)
        w._do_redo()
        self.assertEqual(w._capture_state(changed.description), changed)

    def test_generated_output_is_independent_of_the_line_last_viewed(self):
        w = self.window
        self.seed_animation("1c")
        w._curve_editor.set_nodes(make_default_nodes(start_color="0000FF", end_color="FF0000"), is_color=True)
        w._step_spin.setValue(10)

        def output():
            settings = w._build_settings()
            self.assertEqual(_animation_move_for_tag(settings.animation, "1c", 5, 0, 20), 5)
            return [event.to_ass_line() for event in generate_gradient_events(
                w._source_events, list(range(len(w._source_events))), self.source, settings,
                w._settings_for_event, w._get_bounds_meta,
            )]

        for use_path in (False, True):
            if use_path:
                w._sampling_paths = {index: {"1c": PathSet.from_ass_path(
                    "m 0 0 l 3 0", sampling_frame=0,
                    sampled_points=[(0, i, 0, color) for i, color in enumerate(("0000FF", "00FF00", "FF0000", "FFFFFF"))],
                )} for index in range(len(w._source_events))}
            for transform in (True, False):
                with self.subTest(use_path=use_path, transform=transform):
                    w._animation_panel._use_transform.setChecked(transform)
                    baseline = output()
                    self.assertGreater(len(baseline), len(w._source_events))
                    self.assertEqual(any(r"\t(" in event for event in baseline), transform)
                    for index in (2, 3, 4, 1, 0):
                        w._on_active_line_changed(index)
                        self.assertEqual(output(), baseline)


if __name__ == "__main__":
    unittest.main()
