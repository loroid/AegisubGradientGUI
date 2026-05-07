import os
import subprocess
import tempfile
from collections import OrderedDict
from pathlib import Path
from PIL import Image


DEFAULT_FFMPEG = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


class FrameSampler:
    """Extracts frames from video using FFmpeg and provides pixel color sampling."""
    
    def __init__(self, ffmpeg_path: str | None = None, max_cached_frames: int = 6):
        self.ffmpeg_path = ffmpeg_path or DEFAULT_FFMPEG
        self._current_video = None
        self._current_time = None
        self._current_frame = None
        self._image: Image.Image = None
        self._frame_cache: OrderedDict[tuple, Image.Image] = OrderedDict()
        self._max_cached_frames = max(1, int(max_cached_frames))
        self.last_error = ""

    def _temp_frame_path(self) -> Path:
        return (
            Path(tempfile.gettempdir())
            / f"gradient_gui_frame_{os.getpid()}_{id(self):x}.bmp"
        )

    def _prepare_temp_frame_path(self) -> Path:
        temp_img_path = self._temp_frame_path()
        try:
            temp_img_path.unlink(missing_ok=True)
        except OSError:
            pass
        return temp_img_path

    def _remove_temp_frame_path(self, temp_img_path: Path) -> None:
        try:
            temp_img_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _clear_loaded_frame(self) -> None:
        self._current_video = None
        self._current_time = None
        self._current_frame = None
        self._image = None

    def _read_temp_frame(self, temp_img_path: Path) -> Image.Image:
        with Image.open(str(temp_img_path)) as image:
            return image.convert("RGB")

    def _cache_get(self, cache_key: tuple) -> Image.Image | None:
        image = self._frame_cache.get(cache_key)
        if image is None:
            return None
        self._frame_cache.move_to_end(cache_key)
        return image

    def _cache_put(self, cache_key: tuple, image: Image.Image) -> None:
        self._frame_cache[cache_key] = image
        self._frame_cache.move_to_end(cache_key)
        while len(self._frame_cache) > self._max_cached_frames:
            self._frame_cache.popitem(last=False)
        
    def load_frame(self, video_path: str, time_sec: float) -> bool:
        """Extracts a frame at the specified time using FFmpeg and caches it in memory."""
        time_key = round(float(time_sec), 3)
        cache_key = ("time", video_path, time_key)
        if (
            self._current_video == video_path
            and self._current_time is not None
            and abs(self._current_time - time_key) < 0.01
            and self._image is not None
        ):
            self.last_error = ""
            return True # Already loaded

        cached = self._cache_get(cache_key)
        if cached is not None:
            self._image = cached
            self._current_video = video_path
            self._current_time = time_key
            self._current_frame = None
            self.last_error = ""
            return True
            
        temp_img_path = self._prepare_temp_frame_path()
        
        # Build FFmpeg command to extract exactly 1 frame at the given time
        cmd = [
            self.ffmpeg_path,
            "-y",                   # Overwrite output
            "-ss", str(time_key),   # Seek time
            "-i", video_path,       # Input file
            "-vframes", "1",        # Extract 1 frame
            "-q:v", "2",            # High quality
            "-f", "image2",         # Image sequence format
            str(temp_img_path)
        ]
        
        try:
            # Hide console window on Windows
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            
            if temp_img_path.exists():
                # Load image into Pillow
                self._image = self._read_temp_frame(temp_img_path)
                self._cache_put(cache_key, self._image)
                self._current_video = video_path
                self._current_time = time_key
                self._current_frame = None
                self.last_error = ""
                self._remove_temp_frame_path(temp_img_path)
                return True
            self.last_error = "FFmpeg did not create a frame image."
        except subprocess.CalledProcessError as e:
            self.last_error = (e.stderr or e.stdout or str(e)).strip()
            print(f"FrameSampler: Failed to extract frame: {self.last_error}")
        except Exception as e:
            self.last_error = str(e)
            print(f"FrameSampler: Failed to extract frame: {self.last_error}")

        self._remove_temp_frame_path(temp_img_path)
        self._clear_loaded_frame()
        return False

    def load_frame_number(self, video_path: str, frame_number: int) -> bool:
        """Extract an exact 0-based frame number using FFmpeg's select filter."""
        frame_number = max(0, int(frame_number))
        cache_key = ("frame", video_path, frame_number)
        if (
            self._current_video == video_path
            and self._current_frame == frame_number
            and self._image is not None
        ):
            self.last_error = ""
            return True

        cached = self._cache_get(cache_key)
        if cached is not None:
            self._image = cached
            self._current_video = video_path
            self._current_time = None
            self._current_frame = frame_number
            self.last_error = ""
            return True

        temp_img_path = self._prepare_temp_frame_path()
        select_expr = f"select=eq(n\\,{frame_number})"
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", video_path,
            "-vf", select_expr,
            "-frames:v", "1",
            "-vsync", "0",
            "-f", "image2",
            str(temp_img_path),
        ]

        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                startupinfo=startupinfo,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            if temp_img_path.exists():
                self._image = self._read_temp_frame(temp_img_path)
                self._cache_put(cache_key, self._image)
                self._current_video = video_path
                self._current_time = None
                self._current_frame = frame_number
                self.last_error = ""
                self._remove_temp_frame_path(temp_img_path)
                return True
            self.last_error = "FFmpeg did not create a frame image."
        except subprocess.CalledProcessError as e:
            self.last_error = (e.stderr or e.stdout or str(e)).strip()
            print(f"FrameSampler: Failed to extract frame {frame_number}: {self.last_error}")
        except Exception as e:
            self.last_error = str(e)
            print(f"FrameSampler: Failed to extract frame {frame_number}: {self.last_error}")

        self._remove_temp_frame_path(temp_img_path)
        self._clear_loaded_frame()
        return False
        
    def get_pixel_bgr(self, x: int, y: int) -> str:
        """Returns the BGR hex string for the pixel at (x, y)."""
        if not self._image:
            return None
            
        width, height = self._image.size
        # Clamp coordinates to image boundaries
        x = max(0, min(width - 1, x))
        y = max(0, min(height - 1, y))
        
        try:
            r, g, b = self._image.getpixel((x, y))
            return f"{b:02X}{g:02X}{r:02X}"
        except Exception:
            return None

    def get_image_copy(self):
        """Return a copy of the currently loaded RGB frame image."""
        return self._image.copy() if self._image else None

    def frame_cache_key(self):
        """Return a stable key for the currently cached frame."""
        if not self._image or not self._current_video:
            return None
        if self._current_frame is not None:
            return ("frame", self._current_video, int(self._current_frame))
        if self._current_time is not None:
            return ("time", self._current_video, round(float(self._current_time), 3))
        return None
