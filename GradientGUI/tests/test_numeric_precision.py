"""Regression tests for tag-specific numeric precision."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.tag_parser import TAG_INFO, build_tag_string


class NumericPrecisionTest(unittest.TestCase):
    def test_shear_tags_keep_sub_cent_precision(self) -> None:
        self.assertEqual(build_tag_string("fax", 0.0001234), r"\fax0.000123")
        self.assertEqual(build_tag_string("fay", -0.0009876), r"\fay-0.000988")
        self.assertEqual(TAG_INFO["fax"]["precision"], 6)
        self.assertEqual(TAG_INFO["fay"]["precision"], 6)

    def test_adjacent_shear_values_are_not_quantized_to_one_cent(self) -> None:
        first = build_tag_string("fay", 0.001)
        second = build_tag_string("fay", 0.002)
        self.assertNotEqual(first, second)
        self.assertEqual(first, r"\fay0.001000")
        self.assertEqual(second, r"\fay0.002000")

    def test_other_numeric_tags_keep_two_decimal_output(self) -> None:
        self.assertEqual(build_tag_string("frz", 0.1234), r"\frz0.12")
        self.assertEqual(build_tag_string("blur", 0.1234), r"\blur0.12")


if __name__ == "__main__":
    unittest.main()
