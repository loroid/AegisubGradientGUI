import unittest
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = ROOT / "GradientGUI"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from gui.update_checker import _is_newer_version, _parse_version


class UpdateCheckerTest(unittest.TestCase):
    def test_parse_version_accepts_tag_prefixes(self) -> None:
        self.assertEqual(_parse_version("v1.2.3"), (1, 2, 3, 0))
        self.assertEqual(_parse_version("release-2.0"), (2, 0, 0, 0))

    def test_newer_version_comparison(self) -> None:
        self.assertTrue(_is_newer_version("v1.0.1", "1.0.0"))
        self.assertTrue(_is_newer_version("2.0.0", "1.9.9"))
        self.assertFalse(_is_newer_version("1.0.0", "1.0.0"))
        self.assertFalse(_is_newer_version("0.9.9", "1.0.0"))

    def test_unparseable_versions_are_not_newer(self) -> None:
        self.assertFalse(_is_newer_version("latest", "1.0.0"))
        self.assertFalse(_is_newer_version("1.0.1", "current"))


if __name__ == "__main__":
    unittest.main()
