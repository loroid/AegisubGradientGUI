"""Transactional writes for persistent user files and completed ASS output."""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Iterator, TextIO


@contextmanager
def atomic_text_writer(path: str | Path, *, encoding: str = "utf-8") -> Iterator[TextIO]:
    """Replace the destination only after serialization and flushing succeed."""
    destination = Path(path).resolve()
    try:
        mode = stat.S_IMODE(destination.stat().st_mode)
    except FileNotFoundError:
        mode = None
    temporary: Path | None = None
    try:
        # The same directory keeps the final replace on the same filesystem.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding=encoding, dir=destination.parent,
            prefix=".gradientgui-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None and os.name != "nt":
            os.chmod(temporary, mode)
        # Windows requires the temporary file handle to be closed first.
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
