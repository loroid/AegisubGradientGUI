"""Mirroring must reverse evaluated curves, not only node positions."""

from __future__ import annotations

import copy
import os
import re
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtWidgets import QApplication

from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.ass_parser import ASSEvent
from engine.interpolation import ColorSpace, CurveNode, InterpolationMode, interpolate, mirror_nodes_horizontal
from gui.curve_editor import CurveEditorView
from gui.curve_node_table import CurveNodeTableDialog
from gui.main_window import MainWindow
from gui.state_codec import serialize_nodes, deserialize_nodes


class CurveMirroringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self, nodes, mode=InterpolationMode.STEPPED, *, color=False, text=False):
        view = CurveEditorView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view.set_tag_type(color, text)
        view.set_nodes(copy.deepcopy(nodes))
        view.set_mode(mode)
        return view

    def test_numeric_mixed_segments_reverse_their_evaluated_values(self):
        nodes = [CurveNode(x=x, y=y) for x, y in ((0, 1), (20, 7), (40, -4), (70, 9), (100, 3))]
        for node, mode in zip(nodes, InterpolationMode):
            node.segment_mode = mode
        view = self.view(nodes)
        view.mirror_horizontal()
        for x in [i * 0.25 for i in range(401)]:
            with self.subTest(x=x):
                self.assertAlmostEqual(interpolate(view.current_nodes(), x), interpolate(nodes, 100 - x), places=7)
        view.mirror_horizontal()
        self.assertEqual(view.current_nodes(), nodes)

    def test_stepped_color_mirror_reverses_intervals_and_keeps_node_colors(self):
        nodes = [CurveNode(x=x, value_str=color) for x, color in (
            (0, "0000FF"), (30, "00FF00"), (70, "FF0000"), (100, "FFFFFF"),
        )]
        for space in ColorSpace:
            view = self.view(nodes, color=True)
            view.mirror_horizontal()
            for x in (0, 0.1, 10, 29.999, 30, 30.001, 50, 70, 70.001, 99.9, 100):
                with self.subTest(space=space, x=x):
                    self.assertEqual(
                        interpolate(view.current_nodes(), x, InterpolationMode.STEPPED, True, space),
                        interpolate(nodes, 100 - x, InterpolationMode.STEPPED, True, space),
                    )

    def test_font_curve_mirror_reverses_held_text_values(self):
        nodes = [CurveNode(x=x, value_str=text) for x, text in ((0, "Alpha"), (40, "Beta"), (100, "Gamma"))]
        view = self.view(nodes, text=True)
        positions = (0, 1, 30, 40, 41, 59, 60, 61, 99, 100)
        expected = [view._text_value_at(100 - x) for x in positions]
        view.mirror_horizontal()
        self.assertEqual([view._text_value_at(x) for x in positions], expected)

    def test_mirrored_node_data_survives_cloning_table_and_serialization(self):
        nodes = mirror_nodes_horizontal([
            CurveNode(x=x, y=y, value_str=color, segment_mode=InterpolationMode.STEPPED)
            for x, y, color in ((0, 1, "0000FF"), (40, 5, "00FF00"), (100, 9, "FF0000"))
        ])
        self.assertTrue(nodes[0].step_from_right)
        for color, text in ((False, False), (True, False), (False, True)):
            with self.subTest(color=color, text=text):
                dialog = CurveNodeTableDialog(nodes, is_color=color, is_text=text)
                self.addCleanup(dialog.deleteLater)
                self.addCleanup(dialog.close)
                dialog._sort_table()
                dialog.accept()
                for result in (dialog.nodes(), MainWindow._clone_nodes(None, nodes), deserialize_nodes(serialize_nodes(nodes))):
                    self.assertEqual(result, nodes)
                    self.assertIsNot(result[0], nodes[0])
        raw = serialize_nodes(nodes)
        self.assertIs(raw[0]["step_from_right"], True)
        for node in raw:
            node.pop("step_from_right", None)
        self.assertFalse(any(node.step_from_right for node in deserialize_nodes(raw)))

    def test_insertions_keep_the_mirrored_step_intervals(self):
        for text in (False, True):
            for batch in (False, True):
                with self.subTest(text=text, batch=batch):
                    nodes = [CurveNode(x=x, value_str=value, segment_mode=InterpolationMode.STEPPED)
                             for x, value in ((0, "0000FF"), (20, "00FF00"), (60, "FF0000"), (100, "FFFFFF"))]
                    view = self.view(nodes, mode=InterpolationMode.LINEAR, color=not text, text=text)
                    view.mirror_horizontal()
                    def values():
                        return [interpolate(view.current_nodes(), x * .5, is_color=not text, is_text=text)
                                for x in range(201)]
                    expected = values()
                    if batch:
                        view.add_batch_nodes(5, 5, 15, "constant", 0, 0, "constant")
                    else:
                        view.add_node_at(25, 0)
                    self.assertGreater(len(view.current_nodes()), len(nodes))
                    self.assertEqual(values(), expected)

    def test_gbc_output_uses_mirrored_numeric_color_and_font_values(self):
        nodes = [CurveNode(x=x, y=y, value_str=color, segment_mode=InterpolationMode.STEPPED)
                 for x, y, color in ((0, 1, "0000FF"), (50, 5, "00FF00"), (100, 9, "FF0000"))]
        configs = {
            tag: TagGradientConfig(tag=tag, enabled=True, nodes=mirror_nodes_horizontal(nodes))
            for tag in ("fscx", "1c")
        }
        fonts = [CurveNode(x=x, value_str=value) for x, value in ((0, "Alpha"), (50, "Beta"), (100, "Gamma"))]
        configs["fn"] = TagGradientConfig(tag="fn", enabled=True, nodes=mirror_nodes_horizontal(fonts))
        settings = GradientSettings(mode=GradientMode.GBC, tags=configs)
        output = generate_gradient(ASSEvent(text="ABCDE"), None, settings)[0].text
        self.assertEqual([float(v) for v in re.findall(r"\\fscx([\d.]+)", output)], [9, 5, 5, 1, 1])
        self.assertEqual(re.findall(r"\\1c&H([A-F\d]+)&", output), ["FF0000", "00FF00", "00FF00", "0000FF", "0000FF"])
        self.assertEqual(re.findall(r"\\fn([^}\\]+)", output), ["Gamma", "Beta", "Beta", "Alpha", "Alpha"])


if __name__ == "__main__":
    unittest.main()
