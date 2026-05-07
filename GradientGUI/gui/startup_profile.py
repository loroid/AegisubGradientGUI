"""Startup timing output for debug launches."""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import time


_enabled = True
_start = time.perf_counter()
_last = _start
_console_stream = None
_console_checked = False


def enabled() -> bool:
    return _enabled


def reset(label: str = "start") -> None:
    global _start, _last
    _start = time.perf_counter()
    _last = _start
    if _enabled:
        _write(label, 0.0, 0.0)


def mark(label: str) -> None:
    global _last
    if not _enabled:
        return
    now = time.perf_counter()
    delta = now - _last
    total = now - _start
    _last = now
    _write(label, delta, total)


@contextlib.contextmanager
def block(label: str):
    if not _enabled:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        now = time.perf_counter()
        _write(label, now - start, now - _start)


def _write(label: str, elapsed: float, total: float) -> None:
    message = f"[startup] {label}: {elapsed * 1000:.1f} ms (total {total * 1000:.1f} ms)"
    _write_stderr(message)
    _write_parent_console(message)


def _write_stderr(message: str) -> None:
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except Exception:
        pass


def _write_parent_console(message: str) -> None:
    stream = _ensure_parent_console_stream()
    if stream is None:
        return
    try:
        print(message, file=stream, flush=True)
    except Exception:
        pass


def _ensure_parent_console_stream():
    global _console_stream, _console_checked
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    if _console_checked:
        return _console_stream

    _console_checked = True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        attached = bool(kernel32.AttachConsole(-1))
        error = ctypes.get_last_error()
        if attached or error == 5:  # ERROR_ACCESS_DENIED means already attached.
            _console_stream = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
    except Exception:
        _console_stream = None
    return _console_stream
