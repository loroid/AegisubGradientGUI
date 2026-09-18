"""ASS records, styles, and render headers must preserve source semantics."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.ass_parser import (
    ASSFile, ASSEvent, ASSStyle, EVENT_FIELDS, STYLE_FIELDS,
    parse_ass_file, parse_ass_string,
)
from engine.libass_bounds import _build_single_event_ass, measure_event_bounds
from engine.api import GradientMode, GradientSettings, TagGradientConfig, generate_gradient
from engine.interpolation import make_default_nodes
from gui.gradient_generation import build_preview_ass


STYLE = "Style: Demo,Arial,24,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
HEADER = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 240\n"
    "[V4+ Styles]\nFormat: " + ", ".join(STYLE_FIELDS) + "\n" + STYLE + "\n"
    "[Events]\nFormat: " + ", ".join(EVENT_FIELDS) + "\n"
)


class AssSerializationTest(unittest.TestCase):
    def test_text_whitespace_is_preserved(self):
        for text in ("AB  ", " AB, CD \t", "   ", "AB\u00a0", "A\u2028B", "A\u0085B"):
            with self.subTest(text=text):
                line = ASSEvent(end="0:00:02.00", style="Demo", text=text).to_ass_line()
                self.assertEqual(ASSEvent.from_line(line).text, text)
                parsed = parse_ass_string(HEADER + line + "\r\n")
                self.assertEqual(parsed.events[0].text, text)
                self.assertEqual(parse_ass_string(parsed.to_string()).events[0].text, text)

    def test_string_bom_does_not_hide_script_info(self):
        parsed = parse_ass_string("\ufeff" + HEADER.replace("\n", "\r\n"))
        self.assertEqual((parsed.play_res_x, parsed.play_res_y), (320, 240))

    def test_programmatic_and_modified_styles_are_serialized(self):
        for style in (ASSStyle(name="Demo", fontname="Arial", fontsize=36), ASSStyle.from_line(STYLE)):
            with self.subTest(raw=bool(style.raw)):
                style.fontsize = 42.25
                style.scale_x = 110.5
                source = ASSFile(styles=[style])
                restored = parse_ass_string(source.to_string())
                self.assertEqual(len(restored.styles), 1)
                self.assertEqual(restored.styles[0].fontsize, 42.25)
                self.assertEqual(restored.styles[0].scale_x, 110.5)

    def test_declared_formats_control_parsing_and_writing(self):
        style_values = dict(zip(STYLE_FIELDS, STYLE.removeprefix("Style: ").split(",")))
        style_fields = ["Name", "Fontsize", "Fontname", *STYLE_FIELDS[3:]]
        event_fields = ["Start", "End", "Layer", "Style", "Actor", "MarginV", "MarginL", "MarginR", "Effect", "Extra", "Text"]
        content = (
            "[Script Info]\nPlayResX: 320\nPlayResY: 240\n[V4+ Styles]\n"
            "Format: " + ", ".join(style_fields) + "\nStyle: "
            + ",".join(style_values[name] for name in style_fields)
            + "\n[Events]\nFormat: " + ", ".join(event_fields)
            + "\nDialogue: 0:00:01.00,0:00:02.00,3,Demo,Speaker,4,5,6,,Keep, A,B  \n"
        )
        source = parse_ass_string(content)
        self.assertEqual(source.styles[0].fontname, "Arial")
        self.assertEqual(source.styles[0].fontsize, 24)
        event = source.events[0]
        self.assertEqual((event.start, event.end, event.layer), ("0:00:01.00", "0:00:02.00", 3))
        self.assertEqual((event.name, event.margin_v, event.margin_l, event.margin_r), ("Speaker", 4, 5, 6))
        self.assertEqual(event.text, " A,B  ")
        restored = parse_ass_string(source.to_string())
        self.assertEqual(restored.events, source.events)
        self.assertIn(",Keep, A,B  ", source.to_string())
        with tempfile.TemporaryDirectory(prefix="gradient-ass-format-") as directory:
            path = Path(directory) / "output.ass"
            source.write_events_only(path)
            self.assertEqual(parse_ass_file(path).events, source.events)

    def test_attachments_and_unknown_sections_survive_round_trip(self):
        attachments = "[Fonts]\nfontname: synthetic.ttf\n!!!!\n[Graphics]\nfilename: synthetic.bmp\n####\n[Custom Data]\n; note\nvalue: keep  \n"
        source = parse_ass_string(HEADER + attachments)
        serialized = source.to_string()
        for content in ("[Fonts]\nfontname: synthetic.ttf\n!!!!", "[Graphics]\nfilename: synthetic.bmp\n####", "[Custom Data]\n; note\nvalue: keep  "):
            self.assertIn(content, serialized)
        self.assertEqual(parse_ass_string(serialized).to_string(), serialized)

    def test_preview_and_measurement_share_styles_and_font_attachments(self):
        event = ASSEvent(style="Demo", end="0:00:02.00", text=r"{\an7\pos(20,20)}AB")
        source = parse_ass_string(HEADER + event.to_ass_line() + "\n[Fonts]\nfontname: synthetic.ttf\n!!!!\n")
        source.styles[0].fontsize = 35
        source.project_garbage["Video File"] = "synthetic-video.mp4"
        preview = build_preview_ass(source, [event])
        measurement = _build_single_event_ass(source, event)
        for document in (preview, measurement):
            self.assertIn("[Fonts]\nfontname: synthetic.ttf\n!!!!", document)
            self.assertEqual(parse_ass_string(document).styles[0].fontsize, 35)
            self.assertNotIn("synthetic-video.mp4", document)
        self.assertEqual(preview, measurement)

    def test_libass_uses_programmatic_style_and_later_changes(self):
        event = ASSEvent(style="Demo", end="0:00:02.00", text=r"{\an7\pos(20,20)}AB")
        reference = parse_ass_string(HEADER + event.to_ass_line())
        source = ASSFile(script_info=reference.script_info.copy(), styles=[ASSStyle(name="Demo", fontname="Arial", fontsize=24, alignment=7)], events=[event])
        original = measure_event_bounds(source, event)
        self.assertIsNotNone(original)
        self.assertEqual(original, measure_event_bounds(reference, event))
        source.styles[0].fontsize = 48
        expanded = measure_event_bounds(source, event)
        self.assertIsNotNone(expanded)
        self.assertGreater(expanded.x2 - expanded.x1, original.x2 - original.x1)

    def test_generated_events_keep_extension_fields(self):
        source = parse_ass_string(
            HEADER.replace("Effect, Text", "Effect, Extra, Text")
            + "Dialogue: 0,0:00:00.00,0:00:02.00,Demo,,0,0,0,,Keep,ABCD"
        )
        for mode in GradientMode:
            with self.subTest(mode=mode):
                settings = GradientSettings(
                    mode=mode, step=5, text_x1=0, text_y1=0, text_x2=20, text_y2=20,
                    tags={"1c": TagGradientConfig(tag="1c", enabled=True, nodes=make_default_nodes(start_color="000000", end_color="FFFFFF"))},
                )
                events = generate_gradient(source.events[0], source.styles[0], settings)
                self.assertTrue(events)
                for event in events:
                    self.assertIn(",Keep,", event.to_ass_line(source.events_format))

    def test_font_data_that_looks_like_a_header_is_not_split(self):
        content = HEADER + "[Fonts]\nfontname: synthetic.ttf\n[ABCD]\n!!!!\n"
        source = parse_ass_string(content)
        expected = "[Fonts]\nfontname: synthetic.ttf\n[ABCD]\n!!!!"
        self.assertIn(expected, source.to_string())
        self.assertIn(expected, build_preview_ass(source, []))

    def test_render_defaults_do_not_override_case_variant_resolution(self):
        source = parse_ass_string(HEADER.replace("PlayResX", "playresx").replace("PlayResY", "PLAYRESY"))
        self.assertEqual((source.play_res_x, source.play_res_y), (320, 240))
        rendered = build_preview_ass(source, [])
        self.assertNotIn("1920", rendered)
        self.assertEqual(rendered.lower().count("playresx:"), 1)


if __name__ == "__main__":
    unittest.main()
