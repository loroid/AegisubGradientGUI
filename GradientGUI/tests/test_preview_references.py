from __future__ import annotations

import unittest

from engine.ass_parser import ASSEvent
from gui.gradient_generation import compose_preview_events
from gui.line_selection_panel import processing_line_indices


class PreviewReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sources = [
            ASSEvent(text="source 1"),
            ASSEvent(text="source 2"),
            ASSEvent(text="source 3"),
        ]
        self.generated = [
            ASSEvent(text="generated 1a"),
            ASSEvent(text="generated 1b"),
        ]

    def test_no_reference_lines_keeps_only_generated_events(self) -> None:
        events = compose_preview_events(self.sources, [], self.generated)

        self.assertEqual([event.text for event in events], ["generated 1a", "generated 1b"])

    def test_reference_preview_adds_only_designated_input_lines(self) -> None:
        events = compose_preview_events(self.sources, [0, 2], self.generated)

        self.assertEqual(
            [event.text for event in events],
            ["source 1", "source 3", "generated 1a", "generated 1b"],
        )

    def test_reference_preview_does_not_mutate_input_lists(self) -> None:
        events = compose_preview_events(self.sources, [1], self.generated)

        events.clear()
        self.assertEqual(len(self.sources), 3)
        self.assertEqual(len(self.generated), 2)

    def test_reference_lines_are_excluded_from_processing_selection(self) -> None:
        self.assertEqual(
            processing_line_indices([0, 1, 2], [1], 0, 3),
            [0, 2],
        )

    def test_reference_active_line_is_not_used_as_fallback(self) -> None:
        self.assertEqual(processing_line_indices([], [1], 1, 3), [])


if __name__ == "__main__":
    unittest.main()
