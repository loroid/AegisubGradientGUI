"""Background GitHub release update check."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import threading
import urllib.error
import urllib.request
from typing import Callable, Optional

from gui.app_version import APP_VERSION, GITHUB_REPO


LATEST_RELEASE_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    url: str
    title: str = ""


def _parse_version(value: object) -> Optional[tuple[int, ...]]:
    text = str(value or "")
    match = re.search(r"(\d+(?:\.\d+){1,3})", text)
    if not match:
        return None
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts + (0,) * (4 - len(parts))


def _is_newer_version(latest: object, current: object = APP_VERSION) -> bool:
    latest_parts = _parse_version(latest)
    current_parts = _parse_version(current)
    if latest_parts is None or current_parts is None:
        return False
    return latest_parts > current_parts


def fetch_latest_release(timeout: float = 5.0) -> Optional[UpdateInfo]:
    request = urllib.request.Request(
        LATEST_RELEASE_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"GradientGUI/{APP_VERSION}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))

    tag = str(payload.get("tag_name") or "")
    if not _is_newer_version(tag):
        return None
    return UpdateInfo(
        version=tag.lstrip("vV") or tag,
        url=str(payload.get("html_url") or f"https://github.com/{GITHUB_REPO}/releases"),
        title=str(payload.get("name") or tag),
    )


def check_for_updates_async(
    callback: Callable[[UpdateInfo], None],
    *,
    timeout: float = 5.0,
) -> threading.Thread:
    """Run a silent update check in a daemon thread."""

    def _worker() -> None:
        try:
            update = fetch_latest_release(timeout=timeout)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError):
            return
        except Exception:
            return
        if update is not None:
            callback(update)

    thread = threading.Thread(
        target=_worker,
        name="GradientGUIUpdateCheck",
        daemon=True,
    )
    thread.start()
    return thread
