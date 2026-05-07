"""
mpv-based video preview widget.

Embeds mpv into a PySide6 QWidget for real-time subtitle preview.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import QWidget, QLabel, QSizePolicy
from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from gui.debug_overlay import DebugOverlayWidget
from gui.i18n import tr


def _render_debug(message: str) -> None:
    if os.environ.get("GRADIENTGUI_MPV_RENDER_DEBUG"):
        print(f"[mpv-render] {message}", flush=True)


def _running_under_wsl() -> bool:
    if sys.platform != "linux":
        return False
    try:
        with open("/proc/sys/kernel/osrelease", "r", encoding="utf-8") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def _wsl_render_api_preview() -> bool:
    if not _running_under_wsl():
        return False
    value = os.environ.get("GRADIENTGUI_MPV_RENDER_API", "").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _mpv_window_options() -> dict[str, str]:
    override = os.environ.get("GRADIENTGUI_MPV_VO", "").strip()
    if override:
        return {"vo": override}
    if _running_under_wsl():
        # WSLg can make mpv's default GPU context choose Vulkan/ZINK and fail.
        # Keep mpv's GPU renderer, but force an OpenGL context. Embedded WID
        # mode is unstable on WSLg, so this branch is only for explicit render
        # API disablement.
        return {
            "vo": "gpu",
            "gpu_api": "opengl",
            "gpu_context": "x11egl",
        }
    return {"vo": "gpu"}


class _MpvRenderWidget(QOpenGLWidget):
    """QOpenGLWidget backed by libmpv's render API."""

    frame_ready = Signal()
    render_ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setAutoFillBackground(False)
        self._mpv_module = None
        self._player = None
        self._render_context = None
        self._gl_get_proc = None
        self._paint_count = 0
        self._update_count = 0
        self.frame_ready.connect(self.update, Qt.ConnectionType.QueuedConnection)

    def set_player(self, mpv_module, player) -> None:
        self._mpv_module = mpv_module
        self._player = player
        _render_debug("set_player")
        self._create_render_context()

    def has_render_context(self) -> bool:
        return self._render_context is not None

    def detach_player(self) -> None:
        _render_debug("detach_player")
        self.makeCurrent()
        try:
            if self._render_context is not None:
                self._render_context.update_cb = None
                self._render_context.free()
        except Exception:
            pass
        finally:
            self._render_context = None
            self._player = None
            self._mpv_module = None
            self._gl_get_proc = None
            self.doneCurrent()

    def _get_proc_address(self, _ctx, name):
        context = QOpenGLContext.currentContext()
        if context is None:
            return 0
        func_name = name.decode("ascii", errors="ignore") if isinstance(name, bytes) else str(name)
        for candidate in (func_name.encode("ascii", errors="ignore"), func_name):
            try:
                address = context.getProcAddress(candidate)
            except TypeError:
                continue
            if not address:
                continue
            try:
                return int(address)
            except TypeError:
                try:
                    return address.__int__()
                except Exception:
                    return 0
        return 0

    def _create_render_context(self) -> None:
        if self._render_context is not None or self._mpv_module is None or self._player is None:
            return
        if self.context() is None:
            _render_debug("create skipped: no Qt GL context yet")
            return
        self.makeCurrent()
        try:
            self._gl_get_proc = self._mpv_module.MpvGlGetProcAddressFn(self._get_proc_address)
            _render_debug("creating render context")
            self._render_context = self._mpv_module.MpvRenderContext(
                self._player,
                "opengl",
                opengl_init_params={"get_proc_address": self._gl_get_proc},
                advanced_control=True,
            )
            self._render_context.update_cb = self._on_mpv_update
            _render_debug("render context created")
            self.render_ready.emit()
        finally:
            self.doneCurrent()

    def _on_mpv_update(self) -> None:
        self._update_count += 1
        if self._update_count <= 5:
            _render_debug(f"update callback #{self._update_count}")
        self.frame_ready.emit()

    def initializeGL(self) -> None:
        _render_debug("initializeGL")
        self._create_render_context()

    def paintGL(self) -> None:
        if self._render_context is None:
            if self._paint_count == 0:
                _render_debug("paintGL skipped: no render context")
            return
        self._paint_count += 1
        ratio = max(float(self.devicePixelRatioF()), 1.0)
        width = max(1, int(round(self.width() * ratio)))
        height = max(1, int(round(self.height() * ratio)))
        if self._paint_count <= 5:
            _render_debug(
                f"paintGL #{self._paint_count}: fbo={int(self.defaultFramebufferObject())} size={width}x{height}"
            )
        self._render_context.render(
            opengl_fbo={
                "fbo": int(self.defaultFramebufferObject()),
                "w": width,
                "h": height,
                "internal_format": 0,
            },
            flip_y=True,
        )
        try:
            self._render_context.report_swap()
        except Exception:
            pass


class VideoPreview(QWidget):
    """Video preview widget using embedded mpv."""

    # Emitted when mpv is ready
    ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setStyleSheet("background: #000;")

        self._render_api_preview = _wsl_render_api_preview()
        self._surface = _MpvRenderWidget(self) if self._render_api_preview else QWidget(self)
        if not self._render_api_preview:
            self._surface.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self._surface.setMouseTracking(True)
        self._surface.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self._surface.setStyleSheet("background: #000;")
        self._surface.installEventFilter(self)

        self._player = None
        self._video_loaded = False
        self._pending_video_load = None
        self._sub_path: Optional[str] = None
        self._video_zoom = 0.0
        self._video_pan_x = 0.0
        self._video_pan_y = 0.0
        self._panning = False
        self._pan_start = None
        self._pan_base = (0.0, 0.0)
        self._resize_reload_timer = QTimer(self)
        self._resize_reload_timer.setSingleShot(True)
        self._resize_reload_timer.setInterval(120)
        self._resize_reload_timer.timeout.connect(self._reload_subtitle)

        # Placeholder label
        self._placeholder_error: Optional[str] = None
        self._placeholder = QLabel(tr("等待视频加载..."), self)
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "color: #888; font-size: 16px; background: #1a1a2e;"
        )
        self._placeholder.installEventFilter(self)
        self._debug_overlay = DebugOverlayWidget(self)
        if isinstance(self._surface, _MpvRenderWidget):
            self._surface.render_ready.connect(self._load_pending_render_api_video)
        self._update_surface_geometry()

    def init_mpv(self):
        """Initialize mpv player. Call after widget is shown."""
        try:
            # Ensure bundled libmpv can be found on Windows portable builds.
            app_dir = Path(__file__).parent.parent
            dll_path = app_dir / "libmpv-2.dll" if os.name == "nt" else None
            if dll_path and dll_path.exists():
                os.environ["PATH"] = str(app_dir) + os.pathsep + os.environ.get("PATH", "")

            import mpv

            mpv_args = {
                "hwdec": "auto-safe",
                "keep_open": "yes",
                "pause": True,
                "sid": "no",           # disable embedded subs initially
                "osd_level": 0,        # no OSD
                "input_default_bindings": False,
                "input_vo_keyboard": False,
            }
            if self._render_api_preview:
                mpv_args["vo"] = "libmpv"
            else:
                mpv_args.update(_mpv_window_options())
                mpv_args["wid"] = str(int(self._surface.winId()))

            self._player = mpv.MPV(**mpv_args)
            if self._render_api_preview:
                self._surface.set_player(mpv, self._player)
            self._placeholder.hide()
            self._surface.show()
            self._apply_video_view()
            self.ready.emit()
        except Exception as e:
            self._placeholder_error = str(e)
            self._placeholder.setText(f"{tr('mpv 初始化失败')}:\n{e}")

    def retranslate_ui(self) -> None:
        if self._placeholder_error:
            self._placeholder.setText(
                f"{tr('mpv 初始化失败')}:\n{self._placeholder_error}"
            )
        else:
            self._placeholder.setText(tr("等待视频加载..."))

    def load_video(self, video_path: str, seek_time: float = 0.0, frame_number: int = -1):
        """Load a video file and seek to the specified time or frame."""
        if not self._player:
            return

        if (
            self._render_api_preview
            and isinstance(self._surface, _MpvRenderWidget)
            and not self._surface.has_render_context()
        ):
            self._pending_video_load = (video_path, seek_time, frame_number)
            _render_debug(f"defer load_video until render context: {video_path}")
            return

        self._load_video_now(video_path, seek_time, frame_number)

    def _load_pending_render_api_video(self) -> None:
        if not self._pending_video_load:
            return
        video_path, seek_time, frame_number = self._pending_video_load
        self._pending_video_load = None
        _render_debug(f"load pending video: {video_path}")
        self._load_video_now(video_path, seek_time, frame_number)

    def _load_video_now(self, video_path: str, seek_time: float = 0.0, frame_number: int = -1):
        self._player.loadfile(video_path)
        self._video_loaded = True
        self._target_frame = frame_number
        self._target_time = seek_time
        self._surface.update()

        # Wait a bit for file to load, then seek
        def _seek():
            try:
                if self._target_frame >= 0:
                    # Use fps to calculate exact time for the frame
                    fps = 23.976
                    try:
                        fps = self._player.container_fps or 23.976
                    except Exception:
                        pass
                    t = self._target_frame / fps
                    self._player.command("seek", str(t), "absolute", "exact")
                elif self._target_time > 0:
                    self._player.command("seek", str(self._target_time), "absolute", "exact")
                self._surface.update()
            except Exception:
                pass
        QTimer.singleShot(500, _seek)

    def _apply_video_view(self):
        if self._uses_render_api_surface():
            self._apply_mpv_view_transform()
        self._update_surface_geometry()

    def _apply_video_pan(self):
        if self._uses_render_api_surface():
            self._apply_mpv_view_transform()
        self._update_surface_geometry()

    def _uses_render_api_surface(self) -> bool:
        return self._render_api_preview and isinstance(self._surface, _MpvRenderWidget)

    def _apply_mpv_view_transform(self) -> None:
        if not self._player:
            return
        try:
            self._player.command("set", "video-zoom", str(float(self._video_zoom)))
            self._player.command("set", "video-pan-x", str(float(self._video_pan_x)))
            self._player.command("set", "video-pan-y", str(float(self._video_pan_y)))
        except Exception:
            pass

    def seek_time(self, time_sec: float):
        """Seek the embedded player to an absolute preview time."""
        if not self._player or not self._video_loaded:
            return
        try:
            self._player.command("seek", str(max(0.0, float(time_sec))), "absolute", "exact")
        except Exception:
            pass

    def current_time(self) -> Optional[float]:
        """Return mpv's current playback position in seconds."""
        if not self._player or not self._video_loaded:
            return None
        for attr in ("playback_time", "time_pos"):
            try:
                value = getattr(self._player, attr)
                if value is not None:
                    return max(0.0, float(value))
            except Exception:
                continue
        return None

    def start_loop_playback(self, start_sec: float, end_sec: float, seek_sec: Optional[float] = None) -> bool:
        """Use mpv's native A-B loop for smooth subtitle animation preview."""
        if not self._player or not self._video_loaded:
            return False
        start = max(0.0, float(start_sec))
        end = max(start + 0.001, float(end_sec))
        seek_to = start if seek_sec is None else max(start, min(end, float(seek_sec)))
        try:
            self._player.command("set", "ab-loop-a", str(start))
            self._player.command("set", "ab-loop-b", str(end))
            self._player.command("seek", str(seek_to), "absolute", "exact")
            self._player.pause = False
            return True
        except Exception:
            try:
                self.stop_loop_playback()
            except Exception:
                pass
            return False

    def stop_loop_playback(self, *, pause: bool = True) -> None:
        """Stop native A-B looping and optionally pause on the current frame."""
        if not self._player or not self._video_loaded:
            return
        try:
            self._player.command("set", "ab-loop-a", "no")
            self._player.command("set", "ab-loop-b", "no")
        except Exception:
            pass
        if pause:
            try:
                self._player.pause = True
            except Exception:
                pass

    def seek_frame(self, frame_number: int, fps: Optional[float] = None):
        """Seek to a 0-based frame number using the video's frame rate."""
        if not self._player or not self._video_loaded:
            return
        try:
            video_fps = float(fps) if fps and fps > 0 else float(self._player.container_fps or 23.976)
        except Exception:
            video_fps = 23.976
        self.seek_time(max(0, int(frame_number)) / max(video_fps, 1.0))

    def _update_surface_geometry(self):
        w = max(self.width(), 1)
        h = max(self.height(), 1)
        if self._uses_render_api_surface():
            self._surface.setGeometry(0, 0, w, h)
        else:
            scale = max(0.25, min(16.0, 2.0 ** self._video_zoom))
            sw = max(1, int(round(w * scale)))
            sh = max(1, int(round(h * scale)))
            x = int(round((w - sw) / 2.0 + self._video_pan_x * w / 2.0))
            y = int(round((h - sh) / 2.0 + self._video_pan_y * h / 2.0))
            self._surface.setGeometry(x, y, sw, sh)
        self._placeholder.setGeometry(0, 0, w, h)
        self._debug_overlay.setGeometry(0, 0, w, h)
        self._placeholder.raise_()

    def _event_pos(self, event):
        if hasattr(event, "globalPosition"):
            return event.globalPosition()
        return event.position()

    def eventFilter(self, watched, event):
        if watched in (self._surface, self._placeholder):
            if event.type() == QEvent.Type.Wheel:
                self.wheelEvent(event)
                return event.isAccepted()
            if event.type() == QEvent.Type.MouseButtonPress:
                self.mousePressEvent(event)
                return event.isAccepted()
            if event.type() == QEvent.Type.MouseMove:
                self.mouseMoveEvent(event)
                return event.isAccepted()
            if event.type() == QEvent.Type.MouseButtonRelease:
                self.mouseReleaseEvent(event)
                return event.isAccepted()
        return super().eventFilter(watched, event)

    def reset_view(self):
        self._video_zoom = 0.0
        self._video_pan_x = 0.0
        self._video_pan_y = 0.0
        self._apply_video_view()

    def wheelEvent(self, event):
        if not self._player:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        self._video_zoom = max(-2.0, min(4.0, self._video_zoom + delta / 1200.0))
        self._apply_video_view()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = self._event_pos(event)
            self._pan_base = (self._video_pan_x, self._video_pan_y)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._surface.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.reset_view()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and self._pan_start is not None:
            delta = self._event_pos(event) - self._pan_start
            w = max(float(self.width()), 1.0)
            h = max(float(self.height()), 1.0)
            self._video_pan_x = self._pan_base[0] + delta.x() / w * 2.0
            self._video_pan_y = self._pan_base[1] + delta.y() / h * 2.0
            self._apply_video_pan()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton and self._panning:
            self._panning = False
            self._pan_start = None
            self.unsetCursor()
            self._surface.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def update_subtitle(self, ass_content: str):
        """
        Update the subtitle overlay with new ASS content.
        Writes to a temp file and reloads in mpv.
        """
        if not self._player or not self._video_loaded:
            return

        try:
            # Write to temp file
            if self._sub_path is None:
                fd, self._sub_path = tempfile.mkstemp(suffix=".ass", prefix="gradient_preview_")
                os.close(fd)

            with open(self._sub_path, "w", encoding="utf-8-sig") as f:
                f.write(ass_content)

            self._reload_subtitle()

        except Exception as e:
            print(f"Subtitle update error: {e}")

    def _reload_subtitle(self):
        if not self._player or not self._video_loaded or not self._sub_path:
            return
        try:
            try:
                self._player.command("sub-remove")
            except Exception:
                pass
            self._player.command("sub-add", self._sub_path)
            self._player.sid = "auto"
            # mpv can keep the paused frame visually stale after a subtitle
            # reload; a zero-distance seek forces the current frame to redraw.
            try:
                self._player.command("seek", "0", "relative", "exact")
            except Exception:
                self._surface.update()
        except Exception as e:
            print(f"Subtitle reload error: {e}")

    def refresh_subtitle(self):
        if self._player and self._video_loaded and self._sub_path:
            self._resize_reload_timer.start()

    def set_debug_overlay_data(self, data):
        # Debug geometry is now rendered as ASS events together with subtitles.
        # Keeping the Qt overlay hidden avoids covering native mpv video surfaces.
        self._debug_overlay.set_data(None)

    def cleanup(self):
        """Clean up mpv and temp files."""
        if self._render_api_preview and isinstance(self._surface, _MpvRenderWidget):
            self._surface.detach_player()

        if self._player:
            try:
                self._player.terminate()
            except Exception:
                pass
            self._player = None

        if self._sub_path and os.path.exists(self._sub_path):
            try:
                os.remove(self._sub_path)
            except Exception:
                pass

    def closeEvent(self, event):
        self.cleanup()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_surface_geometry()
        if self._player and self._video_loaded and self._sub_path:
            self._resize_reload_timer.start()
