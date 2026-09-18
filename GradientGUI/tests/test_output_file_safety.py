"""Failed saves must not damage existing files or publish partial new files."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from engine.ass_parser import ASSFile, ASSEvent, parse_ass_file
from engine.file_io import atomic_text_writer
from gui import preset_io


class OutputFileSafetyTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="gradient-save-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def assert_failed_save_preserves_target(self, writer, error_type):
        path = self.root / "saved-file"
        original = b"Original complete file\r\n"
        for existing in (False, True):
            with self.subTest(existing=existing):
                path.unlink(missing_ok=True)
                if existing:
                    path.write_bytes(original)
                with self.assertRaises(error_type):
                    writer(path)
                if existing:
                    self.assertEqual(path.read_bytes(), original)
                else:
                    self.assertFalse(path.exists())
                self.assertEqual(set(self.root.iterdir()), {path} if existing else set())

    def test_preset_serialization_failure_preserves_target(self):
        self.assert_failed_save_preserves_target(
            lambda path: preset_io.write_preset(path, {"version": 1, "invalid": object()}),
            TypeError,
        )

    def test_full_ass_serialization_failure_preserves_target(self):
        document = ASSFile()
        document.to_string = Mock(side_effect=ValueError("Synthetic serialization failure"))
        self.assert_failed_save_preserves_target(document.write, ValueError)

    def test_late_event_failure_preserves_target(self):
        bad_event = Mock()
        bad_event.to_ass_line.side_effect = ValueError("Synthetic event failure")
        document = ASSFile(events=[ASSEvent(text="ABCD"), bad_event])
        self.assert_failed_save_preserves_target(document.write_events_only, ValueError)

    def test_preset_encoding_failure_preserves_target(self):
        self.assert_failed_save_preserves_target(
            lambda path: preset_io.write_preset(path, {"invalid": "\ud800"}),
            UnicodeEncodeError,
        )

    def test_successful_saves_keep_encoding_and_content(self):
        preset = {"version": 1, "text": "Test \u03a9", "tags": {"1c": {"enabled": True}}}
        path = self.root / "test preset.ggpreset"
        path.write_bytes(b"Previous preset")
        preset_io.write_preset(path, preset)
        self.assertEqual(preset_io.read_preset(path), preset)
        expected = json.dumps(preset, ensure_ascii=False, indent=2)
        self.assertEqual(path.read_text(encoding="utf-8"), expected)
        self.assertFalse(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        document = ASSFile(events=[ASSEvent(text=r"AB\NCD{\fsp2} EF")])
        for method in (document.write, document.write_events_only):
            with self.subTest(method=method.__name__):
                method(path)
                self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
                self.assertEqual(parse_ass_file(path).events, document.events)
                self.assertEqual(set(self.root.iterdir()), {path})

    def test_io_failures_preserve_target_for_every_output_writer(self):
        document = ASSFile(events=[ASSEvent(text="ABCD")])
        real_temporary_file = tempfile.NamedTemporaryFile
        writers = (
            document.write, document.write_events_only,
            lambda path: preset_io.write_preset(path, {"version": 1}),
        )
        for writer in writers:
            for failure in ("create", "write", "flush", "fsync", "replace"):
                with self.subTest(writer=writer, failure=failure):
                    error = OSError(f"Synthetic {failure} failure")

                    def failing_stream(*args, **kwargs):
                        stream = real_temporary_file(*args, **kwargs)
                        if failure == "write":
                            real_write = stream.write

                            def partial_write(text):
                                real_write(text[:3])
                                raise error

                            stream.write = partial_write
                        else:
                            stream.flush = Mock(side_effect=error)
                        return stream

                    if failure in {"create", "write", "flush"}:
                        target = "engine.file_io.tempfile.NamedTemporaryFile"
                        effect = error if failure == "create" else failing_stream
                    else:
                        target = f"engine.file_io.os.{failure}"
                        effect = error
                    with patch(target, side_effect=effect):
                        self.assert_failed_save_preserves_target(writer, OSError)

    def test_final_replace_sees_complete_closed_file_next_to_destination(self):
        path = self.root / "file.ass"
        original = b"Original output"
        path.write_bytes(original)
        real_replace = os.replace

        def replace(source, destination):
            self.assertEqual(Path(source).parent, path.parent)
            self.assertEqual(Path(destination), path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(Path(source).read_text(encoding="utf-8-sig"), "AB\nCD")
            self.assertTrue(stream.closed)
            real_replace(source, destination)

        with patch("engine.file_io.os.replace", side_effect=replace) as commit:
            with atomic_text_writer(path, encoding="utf-8-sig") as stream:
                stream.write("AB\n")
                self.assertEqual(path.read_bytes(), original)
                stream.write("CD")
                self.assertEqual(path.read_bytes(), original)
        commit.assert_called_once()
        self.assertEqual(path.read_text(encoding="utf-8-sig"), "AB\nCD")
        self.assertEqual(set(self.root.iterdir()), {path})

    def test_long_existing_filename_can_still_be_saved(self):
        path = self.root / ("a" * 240 + ".ggpreset")
        try:
            path.write_bytes(b"Original output")
        except OSError as error:
            self.skipTest(f"Filesystem does not support this destination path: {error}")
        preset_io.write_preset(path, {"version": 1})
        self.assertEqual(preset_io.read_preset(path), {"version": 1})
        self.assertEqual(set(self.root.iterdir()), {path})

    def test_abort_removes_temporary_and_keeps_existing_output(self):
        def interrupted_save(path):
            with atomic_text_writer(path) as stream:
                stream.write("Partial output")
                raise KeyboardInterrupt()

        self.assert_failed_save_preserves_target(interrupted_save, KeyboardInterrupt)

    def test_recent_list_survives_failed_replace_and_recovers(self):
        recent_path = self.root / "recent.json"
        first = self.root / "first.ggpreset"
        second = self.root / "second.ggpreset"
        first.write_text("{}", encoding="utf-8")
        second.write_text("{}", encoding="utf-8")
        with patch.object(preset_io, "RECENT_PRESETS_PATH", recent_path):
            preset_io.remember_recent_preset(str(first))
            original = recent_path.read_bytes()
            with patch("engine.file_io.os.replace", side_effect=PermissionError("Synthetic lock")):
                preset_io.remember_recent_preset(str(second))
            self.assertEqual(recent_path.read_bytes(), original)
            self.assertEqual(preset_io.read_recent_presets(), [str(first)])
            preset_io.remember_recent_preset(str(second))
            self.assertEqual(preset_io.read_recent_presets(), [str(second), str(first)])
        self.assertEqual(set(self.root.iterdir()), {first, second, recent_path})

    @unittest.skipUnless(os.name == "nt", "Windows read-only file semantics")
    def test_read_only_windows_target_is_not_overwritten_and_temp_is_removed(self):
        path = self.root / "read-only.json"
        path.write_bytes(b"Original output")
        path.chmod(stat.S_IREAD)
        try:
            with self.assertRaises(PermissionError):
                preset_io.write_preset(path, {"version": 1})
            self.assertEqual(path.read_bytes(), b"Original output")
            self.assertEqual(set(self.root.iterdir()), {path})
        finally:
            path.chmod(stat.S_IREAD | stat.S_IWRITE)

    @unittest.skipIf(os.name == "nt", "POSIX permissions and symlinks")
    def test_posix_permissions_and_symlinks_are_preserved(self):
        target = self.root / "target.json"
        target.write_text("{}", encoding="utf-8")
        target.chmod(0o640)
        link = self.root / "link.json"
        link.symlink_to(target.name)
        preset_io.write_preset(link, {"version": 1})
        self.assertTrue(link.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"version": 1})
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertEqual(set(self.root.iterdir()), {target, link})

    def test_apply_failure_leaves_window_open_and_retry_preserves_other_rows(self):
        from gui.main_window import MainWindow

        path = self.root / "output.ass"
        source = [ASSEvent(text="ABCD"), ASSEvent(text="Reference", comment=True)]
        generated = [ASSEvent(text=r"{\1c&HFF0000&}ABCD")]
        window = SimpleNamespace(
            _build_settings=Mock(return_value=object()),
            _output_path=path, _ass_file=ASSFile(events=source), _source_events=source,
            _selected_event_indices=Mock(return_value=[0]),
            _line_group_enabled=Mock(return_value=False),
            _get_bounds_meta=Mock(), _settings_for_event=Mock(),
            _show_generation_error=Mock(), _status=Mock(), _video=Mock(), close=Mock(),
        )
        with (
            patch("gui.main_window.generate_gradient_events", return_value=generated),
            patch("gui.main_window.QMessageBox.critical") as critical,
        ):
            with patch("engine.file_io.os.replace", side_effect=OSError("Synthetic write failure")):
                MainWindow._apply_and_close(window)
            critical.assert_called_once()
            window.close.assert_not_called()
            window._video.cleanup.assert_not_called()
            window._status.showMessage.assert_not_called()
            self.assertEqual(set(self.root.iterdir()), set())
            MainWindow._apply_and_close(window)
        self.assertEqual(parse_ass_file(path).events, [*generated, source[1]])
        window.close.assert_called_once()
        window._video.cleanup.assert_called_once()

    def test_debug_export_failure_preserves_report_and_reports_error(self):
        from gui.main_window import MainWindow

        path = self.root / "report.json"
        path.write_bytes(b"Original report")
        window = SimpleNamespace(
            _input_path=None, _status=Mock(),
            _build_debug_report=Mock(return_value={"synthetic": "data"}),
        )
        with (
            patch("gui.main_window.QFileDialog.getSaveFileName", return_value=(str(path), "")),
            patch("gui.main_window.QMessageBox.critical") as critical,
            patch("engine.file_io.os.replace", side_effect=OSError("Synthetic write failure")),
        ):
            MainWindow._export_debug_report(window)
        critical.assert_called_once()
        window._status.showMessage.assert_not_called()
        self.assertEqual(path.read_bytes(), b"Original report")
        self.assertEqual(set(self.root.iterdir()), {path})


if __name__ == "__main__":
    unittest.main()
