"""Lightweight dependency-boundary tests."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
ENGINE_DIR = PROJECT_DIR / "engine"
GUI_DIR = PROJECT_DIR / "gui"


def _imports_for(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append("." * node.level + node.module)
    return imports


class ArchitectureTest(unittest.TestCase):
    def test_engine_does_not_import_gui_or_qt(self) -> None:
        forbidden = ("gui", "PySide6")
        offenders: list[str] = []
        for path in ENGINE_DIR.glob("*.py"):
            for imported in _imports_for(path):
                if imported.startswith(forbidden):
                    offenders.append(f"{path.name}: {imported}")

        self.assertEqual(offenders, [])

    def test_gui_uses_public_engine_api_for_gradient_generation(self) -> None:
        offenders: list[str] = []
        private_generation_modules = {"engine.gradient", "engine.path_tracer"}
        for path in GUI_DIR.glob("*.py"):
            for imported in _imports_for(path):
                if imported in private_generation_modules:
                    offenders.append(f"{path.name}: {imported}")

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
