"""Render synthetic manual color bands and compare pixels with engine values."""

from __future__ import annotations

import copy
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from engine.interpolation import ColorSpace, CurveNode, InterpolationMode, interpolate, make_default_nodes
from gui.curve_editor import CurveEditorView, MARGIN_LEFT, MARGIN_TOP


class ColorCurvePreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def view(self, nodes, mode=InterpolationMode.LINEAR):
        view = CurveEditorView()
        self.addCleanup(view.deleteLater)
        self.addCleanup(view.close)
        view._width = 601
        view.set_tag_type(True)
        view.set_nodes(copy.deepcopy(nodes))
        view.set_mode(mode)
        return view

    @staticmethod
    def render_band(view):
        image = QImage(int(view._width), 25, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            view.scene().render(painter, QRectF(image.rect()), QRectF(
                MARGIN_LEFT, MARGIN_TOP + view._height - 25, view._width, 25,
            ))
        finally:
            painter.end()
        return image

    def test_rendered_manual_band_matches_interpolation_and_color_space(self):
        nodes = [CurveNode(x=x, value_str=color) for x, color in (
            (0, "0000FF"), (20, "00FF00"), (55, "FF0000"), (100, "00FFFF"),
        )]
        nodes[1].segment_mode = InterpolationMode.STEPPED
        for space in ColorSpace:
            for mode in InterpolationMode:
                with self.subTest(space=space, mode=mode):
                    view = self.view(nodes, mode)
                    if space != ColorSpace.RGB:
                        view.set_color_space(space)
                    image = self.render_band(view)
                    for pixel in (20, 53, 90, 180, 250, 330, 380, 450, 550, 590):
                        expected = interpolate(nodes, (pixel + .5) / image.width() * 100, mode, True, space)
                        color = image.pixelColor(pixel, 12)
                        actual = f"{color.blue():02X}{color.green():02X}{color.red():02X}"
                        self.assertEqual(actual, expected, (pixel, space, mode))

    def test_switching_color_space_repaints_without_editing_nodes(self):
        nodes = make_default_nodes(start_color="0000FF", end_color="00FF00")
        view = self.view(nodes)
        notifications = []
        view.curve_changed.connect(lambda: notifications.append(True))
        view.set_color_space(ColorSpace.HSL)
        image = self.render_band(view)
        self.assertEqual(image.pixelColor(300, 12).name(), "#ffff00")
        self.assertEqual(view.current_nodes(), nodes)
        self.assertEqual(notifications, [])

    def test_inserted_color_nodes_use_selected_color_space(self):
        nodes = make_default_nodes(start_color="0000FF", end_color="00FF00")
        for batch in (False, True):
            with self.subTest(batch=batch):
                view = self.view(nodes)
                view.set_color_space(ColorSpace.HSL)
                if batch:
                    view.add_batch_nodes(1, 50, 1, "constant", 0, 0, "constant")
                else:
                    view.add_node_at(50, 0)
                self.assertEqual(view.current_nodes()[1].value_str, "00FFFF")

    def test_saved_path_stops_override_manual_color_space(self):
        view = self.view(make_default_nodes(start_color="0000FF", end_color="00FF00"))
        view.set_color_preview_stops([(0, "123456"), (1, "ABCDEF")])
        reference = self.render_band(view)
        for space in ColorSpace:
            view.set_color_space(space)
            self.assertEqual(self.render_band(view), reference)


if __name__ == "__main__":
    unittest.main()
