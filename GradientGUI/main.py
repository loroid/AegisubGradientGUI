"""
GradientGUI — Entry point.

Usage:
    python main.py --input <input.ass> --output <output.ass>

Or run without arguments for standalone testing.
"""

import sys
import os
import argparse
from pathlib import Path

# Ensure the app directory is in PATH for Windows portable DLLs.
app_dir = Path(__file__).parent.resolve()
libass_dir = app_dir / "libass"
if os.name == "nt":
    path_parts = [str(app_dir)]
    if libass_dir.exists():
        path_parts.append(str(libass_dir))
    os.environ["PATH"] = os.pathsep.join(path_parts) + os.pathsep + os.environ.get("PATH", "")

from gui import startup_profile
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

from gui.main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description="GradientGUI — ASS Gradient Editor")
    parser.add_argument("--input", "-i", type=str, default=None,
                        help="Path to input ASS file from Aegisub")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="Path to write output ASS file")
    args = parser.parse_args()
    startup_profile.reset("process start")
    startup_profile.mark("arguments parsed")

    app = QApplication(sys.argv)
    startup_profile.mark("QApplication created")
    app.setApplicationName("GradientGUI")
    app.setStyle("Fusion")

    # Set default font
    font = QFont("Segoe UI", 10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)

    window = MainWindow(
        input_path=args.input,
        output_path=args.output,
    )
    startup_profile.mark("MainWindow constructed")
    window.show()
    startup_profile.mark("window shown")

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
