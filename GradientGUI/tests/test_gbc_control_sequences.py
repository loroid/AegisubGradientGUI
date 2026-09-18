from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.ass_parser import ASSEvent, ASSStyle
from engine.interpolation import make_default_nodes


GENERATED_COLOR_RE = re.compile(r"\{\\1c&H[0-9A-F]{6}&\}")


def _event(text: str) -> ASSEvent:
    return ASSEvent(
        layer=0,
        start="0:00:00.00",
        end="0:00:05.00",
        style="Default",
        name="",
        margin_l=0,
        margin_r=0,
        margin_v=0,
        effect="",
        text=text,
        comment=False,
    )


def _settings() -> GradientSettings:
    return GradientSettings(
        mode=GradientMode.GBC,
        tags={
            "1c": TagGradientConfig(
                tag="1c",
                enabled=True,
                nodes=make_default_nodes(
                    start_color="548B55",
                    end_color="ACE9BA",
                ),
            )
        },
    )


class GbcControlSequenceTest(unittest.TestCase):
    def test_ass_line_breaks_are_preserved_in_long_tagged_text(self) -> None:
        source = (
            r"{\an7\fnTest Sans\fs26\fsp0.5\c&HACE9BA&\1a&H42&\blur1"
            r"\pos(124.93,833.6)}Alpha text with symbols ×10^-4 and punctuation."
            r"\NSecond line changes spacing{\fsp-4}, then restores it{\fsp0.5}."
            r"\NThird line repeats{\fsp-4}, and finishes{\fsp0.5}."
        )

        generated = generate_gradient(_event(source), ASSStyle(name="Default"), _settings())

        self.assertEqual(len(generated), 1)
        output = generated[0].text
        self.assertEqual(output.count(r"\N"), 2)
        self.assertNotIn(r"\{", output)
        self.assertEqual(GENERATED_COLOR_RE.sub("", output), source)

    def test_hard_space_advances_once_but_line_breaks_do_not(self) -> None:
        source = r"A\hB\NC\nD"

        generated = generate_gradient(_event(source), ASSStyle(name="Default"), _settings())

        output = generated[0].text
        self.assertEqual(GENERATED_COLOR_RE.sub("", output), source)
        self.assertEqual(len(GENERATED_COLOR_RE.findall(output)), 5)

    def test_cyclic_shift_visits_every_character_including_the_last(self) -> None:
        settings = _settings()
        source = r"A\NB\hC"
        initial = generate_gradient(_event(source), ASSStyle(name="Default"), settings)[0]
        colors = GENERATED_COLOR_RE.findall(initial.text)
        self.assertEqual(len(colors), 4)
        for shift in (-1, 1, 2, 3, 4):
            with self.subTest(shift=shift):
                settings.color_shift_steps_by_tag = {"1c": shift}
                output = generate_gradient(_event(source), ASSStyle(name="Default"), settings)[0].text
                self.assertEqual(GENERATED_COLOR_RE.sub("", output), source)
                self.assertEqual(
                    GENERATED_COLOR_RE.findall(output),
                    [colors[(i - shift) % len(colors)] for i in range(len(colors))],
                )

    def test_cyclic_shift_does_not_change_a_single_character(self) -> None:
        settings = _settings()
        original = generate_gradient(_event("A"), ASSStyle(name="Default"), settings)
        settings.color_shift_steps = 1
        self.assertEqual(generate_gradient(_event("A"), ASSStyle(name="Default"), settings), original)

    def test_drawing_commands_do_not_receive_character_tags(self) -> None:
        drawing = r"{\p1}m 0 0 l 20 0 20 20 0 20{\p0}"
        settings = _settings()
        expected_colors = GENERATED_COLOR_RE.findall(
            generate_gradient(_event("AB"), None, settings)[0].text
        )
        for source in (drawing, drawing + "AB", "A" + drawing + "B"):
            with self.subTest(source=source):
                output = generate_gradient(_event(source), None, settings)[0].text
                self.assertIn(drawing, output)
                self.assertEqual(GENERATED_COLOR_RE.sub("", output), source)
                self.assertEqual(
                    GENERATED_COLOR_RE.findall(output),
                    [] if source == drawing else expected_colors,
                )

    def test_drawing_state_survives_other_tags_and_style_reset(self) -> None:
        source = (
            r"{\p2}m 0 0 l 40 0{\r\pbo5\pos(20,20)} l 40 40 0 40"
            r"{\p0\p1}m 0 0 l 10 0 10 10{\p-1}AB"
        )
        output = generate_gradient(_event(source), None, _settings())[0].text
        self.assertEqual(GENERATED_COLOR_RE.sub("", output), source)
        self.assertEqual(len(GENERATED_COLOR_RE.findall(output)), 2)
        self.assertTrue(output.startswith(source[:-2]))

    def test_position_and_baseline_tags_do_not_enable_drawing(self) -> None:
        source = r"{\pos(20,20)\pbo5}AB"
        output = generate_gradient(_event(source), None, _settings())[0].text
        self.assertEqual(GENERATED_COLOR_RE.sub("", output), source)
        self.assertEqual(len(GENERATED_COLOR_RE.findall(output)), 2)

    def test_sampling_metadata_is_not_copied_to_generated_text(self) -> None:
        for text in (r"{\1xlip(m 0 0 l 3 0)\fs24}AB", r"{\1xlip(m 0 0 l 3 0)}\N"):
            with self.subTest(text=text):
                output = generate_gradient(_event(text), None, _settings())[0].text
                self.assertNotIn("xlip(", output)
                self.assertEqual(
                    GENERATED_COLOR_RE.sub("", output),
                    text.replace(r"\1xlip(m 0 0 l 3 0)", ""),
                )


if __name__ == "__main__":
    unittest.main()
