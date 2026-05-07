"""Startup dependency health checks for the GUI."""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from engine.frame_sampler import FrameSampler
from engine.libass_bounds import LibassBoundsError, check_libass_available
from gui.i18n import tr
from gui import startup_profile


_DLL_DIR_HANDLES = []


@dataclass(frozen=True)
class HealthItem:
    name: str
    ok: bool
    message: str
    detail: str = ""


@dataclass(frozen=True)
class HealthReport:
    items: list[HealthItem]

    @property
    def ok(self) -> bool:
        return all(item.ok for item in self.items)

    def to_status_text(self) -> str:
        return tr("依赖检查通过") if self.ok else tr("依赖检查发现问题")

    def to_message(self) -> str:
        lines = [tr("启动依赖健康检查发现问题："), ""]
        for item in self.items:
            state = "OK" if item.ok else "FAIL"
            lines.append(f"[{state}] {item.name}: {item.message}")
            if item.detail:
                lines.append(f"    {item.detail}")
        lines.append("")
        lines.append(tr("字体不会在此检查中验证。修复失败项后重新打开 GUI 即可重新检查。"))
        return "\n".join(lines)


def run_startup_health_check(
    *,
    video_path: Optional[str],
    video_time: Optional[float],
    video_frame: int,
    frame_sampler: FrameSampler,
    check_video_frame: bool = True,
) -> HealthReport:
    """Check runtime dependencies that affect preview and gradient generation."""

    with startup_profile.block("health.libass"):
        libass_item = _check_libass()
    with startup_profile.block("health.mpv"):
        mpv_item = _check_mpv()
    items = [libass_item, mpv_item]

    with startup_profile.block("health.find_ffmpeg"):
        ffmpeg_path = _find_ffmpeg(frame_sampler.ffmpeg_path)
    with startup_profile.block("health.ffmpeg_version"):
        items.append(_check_ffmpeg(ffmpeg_path))
    if ffmpeg_path:
        frame_sampler.ffmpeg_path = ffmpeg_path
    if check_video_frame:
        items.append(
            run_video_frame_health_check(
                video_path=video_path,
                video_time=video_time,
                video_frame=video_frame,
                frame_sampler=frame_sampler,
            )
        )

    return HealthReport(items)


def run_video_frame_health_check(
    *,
    video_path: Optional[str],
    video_time: Optional[float],
    video_frame: int,
    frame_sampler: FrameSampler,
) -> HealthItem:
    """Check whether FFmpeg can extract the current video frame."""

    with startup_profile.block("health.video_frame"):
        try:
            return _check_video_frame(video_path, video_time, video_frame, frame_sampler)
        except Exception as exc:
            return HealthItem(tr("视频帧读取"), False, tr("检查失败"), str(exc))


def _check_libass() -> HealthItem:
    try:
        dll_path = check_libass_available()
        return HealthItem("libass", True, tr("可用"), dll_path)
    except LibassBoundsError as exc:
        return HealthItem("libass", False, tr("不可用"), str(exc))
    except Exception as exc:
        return HealthItem("libass", False, tr("检查失败"), str(exc))


def _check_mpv() -> HealthItem:
    try:
        importlib.import_module("mpv")
    except Exception as exc:
        return HealthItem("mpv", False, tr("python-mpv 模块不可用"), str(exc))

    try:
        dll_path = _load_mpv_dll()
        return HealthItem("mpv", True, tr("可用"), dll_path)
    except Exception as exc:
        return HealthItem("mpv", False, tr("libmpv 不可用"), str(exc))


def _load_mpv_dll() -> str:
    candidates: list[Path] = []
    library_names = _mpv_library_names()
    for base in _runtime_dirs():
        for name in library_names:
            candidates.extend([base / name, base / "mpv" / name])

    errors: list[str] = []
    for candidate in candidates:
        if candidate.exists():
            _add_dll_dir(candidate.parent)
            try:
                ctypes.CDLL(str(candidate))
                return str(candidate)
            except OSError as exc:
                errors.append(f"{candidate}: {exc}")

    found = ctypes.util.find_library("mpv-2") or ctypes.util.find_library("mpv")
    if found:
        try:
            ctypes.CDLL(found)
            return found
        except OSError as exc:
            errors.append(f"{found}: {exc}")

    fallback_name = library_names[0]
    try:
        ctypes.CDLL(fallback_name)
        return fallback_name
    except OSError as exc:
        errors.append(f"{fallback_name}: {exc}")
        raise RuntimeError("; ".join(errors)) from exc


def _mpv_library_names() -> tuple[str, ...]:
    if os.name == "nt":
        return ("libmpv-2.dll", "mpv-2.dll")
    if sys.platform == "darwin":
        return ("libmpv.dylib", "libmpv.2.dylib")
    return ("libmpv.so.2", "libmpv.so")


def _check_ffmpeg(ffmpeg_path: Optional[str]) -> HealthItem:
    if not ffmpeg_path:
        return HealthItem(
            "ffmpeg",
            False,
            tr("未找到 ffmpeg.exe"),
            tr("请将 ffmpeg.exe 放入程序目录或加入 PATH。"),
        )

    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            startupinfo=_hidden_startupinfo(),
        )
    except Exception as exc:
        return HealthItem("ffmpeg", False, tr("无法运行"), str(exc))

    first_line = (result.stdout or result.stderr).splitlines()
    detail = first_line[0] if first_line else ffmpeg_path
    return HealthItem("ffmpeg", True, tr("可用"), detail)


def _check_video_frame(
    video_path: Optional[str],
    video_time: Optional[float],
    video_frame: int,
    frame_sampler: FrameSampler,
) -> HealthItem:
    if not video_path:
        return HealthItem(
            tr("视频帧读取"),
            False,
            tr("ASS 未关联视频文件"),
            tr("需要视频预览或路径采色时请加载带视频路径的字幕。"),
        )

    video = Path(video_path)
    if not video.exists():
        return HealthItem(tr("视频帧读取"), False, tr("视频文件不存在"), str(video))

    try:
        frame_number = int(video_frame)
    except (TypeError, ValueError):
        frame_number = -1
    ok = False
    if frame_number >= 0:
        ok = frame_sampler.load_frame_number(str(video), frame_number)
    if not ok and video_time is not None:
        ok = frame_sampler.load_frame(str(video), float(video_time))

    if ok:
        detail = f"{video}"
        cache_key = frame_sampler.frame_cache_key()
        if cache_key:
            detail = f"{detail} ({cache_key[0]}={cache_key[-1]})"
        return HealthItem(tr("视频帧读取"), True, tr("可用"), detail)

    return HealthItem(
        tr("视频帧读取"),
        False,
        tr("无法读取当前帧"),
        frame_sampler.last_error or str(video),
    )


def _find_ffmpeg(configured_path: str) -> Optional[str]:
    configured = Path(configured_path)
    if configured.is_absolute() and configured.exists():
        return str(configured)

    found = shutil.which(configured_path) or shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if found:
        return found

    names = [configured_path, "ffmpeg.exe", "ffmpeg"]
    for base in _runtime_dirs():
        for name in names:
            for candidate in (base / name, base / "ffmpeg" / name, base / "ffmpeg" / "bin" / name):
                if candidate.exists():
                    return str(candidate)
    return None


def _runtime_dirs() -> list[Path]:
    dirs: list[Path] = []
    module_dir = Path(__file__).resolve().parents[1]
    dirs.append(module_dir)
    dirs.append(module_dir.parent)
    dirs.append(Path.cwd())
    executable_dir = Path(getattr(sys, "executable", "")).resolve().parent
    dirs.append(executable_dir)
    if hasattr(sys, "_MEIPASS"):
        dirs.append(Path(getattr(sys, "_MEIPASS")))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in dirs:
        key = str(path).lower()
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def _add_dll_dir(path: Path) -> None:
    if hasattr(os, "add_dll_directory"):
        _DLL_DIR_HANDLES.append(os.add_dll_directory(str(path)))
    os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")


def _hidden_startupinfo():
    if os.name != "nt":
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return startupinfo
