"""Fast directory scanner and playlist navigator for cycling through images & videos."""

from __future__ import annotations

import os
import random
import re
from pathlib import Path
from typing import List, Optional, Tuple

IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif",
    ".tiff", ".tif", ".ico", ".svg", ".avif", ".heic",
    ".pbm", ".pgm", ".ppm", ".xbm", ".xpm", ".jfif",
})

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v",
    ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".ts", ".ogv",
})

SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

_NATURAL_SORT_RE = re.compile(r"(\d+)")


def natural_sort_key(path: Path) -> List[object]:
    """Generate a case-insensitive natural sort key so 'img2.jpg' comes before 'img10.jpg'."""
    parts = _NATURAL_SORT_RE.split(path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def is_image_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def is_video_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_supported_media(path: Path | str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED_EXTENSIONS


class FolderNavigator:
    """Manages the list of media files in a folder and current cursor position."""

    def __init__(self) -> None:
        self.folder: Optional[Path] = None
        self.files: List[Path] = []
        self.index: int = -1

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def current_file(self) -> Optional[Path]:
        if 0 <= self.index < len(self.files):
            return self.files[self.index]
        return None

    def load_target(self, target: Path | str, pick_random: bool = False) -> Optional[Path]:
        """Load a file from the middle of a folder (or a folder directly) and scan siblings.

        Args:
            target: Path to a media file or a directory.
            pick_random: If True and target is a folder (or requested via CLI), select a random file.

        Returns:
            The selected Path, or None if no supported media files exist in the folder.
        """
        target_path = Path(target).expanduser().resolve()

        if target_path.is_file():
            folder = target_path.parent
            selected_file = target_path
        elif target_path.is_dir():
            folder = target_path
            selected_file = None
        else:
            return None

        self.folder = folder
        self.files = self._scan_folder(folder)

        if not self.files:
            # If the user explicitly opened a single file whose extension wasn't in our list,
            # still include it so the viewer can try to open it.
            if selected_file is not None and selected_file.is_file():
                self.files = [selected_file]
                self.index = 0
                return selected_file
            self.index = -1
            return None

        if pick_random:
            self.index = random.randrange(len(self.files))
            return self.files[self.index]

        if selected_file is not None:
            try:
                self.index = self.files.index(selected_file)
            except ValueError:
                # Resolve symlinks or case differences on Windows
                norm_target = os.path.normcase(str(selected_file))
                matched_idx = next(
                    (i for i, p in enumerate(self.files) if os.path.normcase(str(p)) == norm_target),
                    -1,
                )
                if matched_idx >= 0:
                    self.index = matched_idx
                else:
                    self.files.append(selected_file)
                    self.files.sort(key=natural_sort_key)
                    self.index = self.files.index(selected_file)
        else:
            self.index = 0

        return self.current_file

    @staticmethod
    def _scan_folder(folder: Path) -> List[Path]:
        """Fast single-pass directory scan using os.scandir."""
        results: List[Path] = []
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_file(follow_symlinks=True):
                            ext = os.path.splitext(entry.name)[1].lower()
                            if ext in SUPPORTED_EXTENSIONS:
                                results.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            return []

        results.sort(key=natural_sort_key)
        return results

    def next(self, wrap: bool = True) -> Optional[Path]:
        """Advance to the next file in the folder."""
        if not self.files:
            return None
        if self.index + 1 < len(self.files):
            self.index += 1
        elif wrap:
            self.index = 0
        return self.current_file

    def prev(self, wrap: bool = True) -> Optional[Path]:
        """Step back to the previous file in the folder."""
        if not self.files:
            return None
        if self.index - 1 >= 0:
            self.index -= 1
        elif wrap:
            self.index = len(self.files) - 1
        return self.current_file

    def first(self) -> Optional[Path]:
        """Jump to the first file in the folder."""
        if not self.files:
            return None
        self.index = 0
        return self.current_file

    def last(self) -> Optional[Path]:
        """Jump to the last file in the folder."""
        if not self.files:
            return None
        self.index = len(self.files) - 1
        return self.current_file

    def random_file(self) -> Optional[Path]:
        """Pick a random file in the folder (avoiding the current one if count > 1)."""
        if not self.files:
            return None
        if len(self.files) == 1:
            self.index = 0
            return self.current_file
        choices = [i for i in range(len(self.files)) if i != self.index]
        self.index = random.choice(choices)
        return self.current_file

    def neighbors(self, radius: int = 2) -> List[Path]:
        """Return neighboring image files around the current index for preloading."""
        if not self.files or self.index < 0:
            return []
        n = len(self.files)
        result: List[Path] = []
        for offset in range(1, radius + 1):
            for candidate_idx in ((self.index + offset) % n, (self.index - offset) % n):
                if candidate_idx != self.index:
                    candidate = self.files[candidate_idx]
                    if is_image_file(candidate) and candidate not in result:
                        result.append(candidate)
        return result

    def remove_current_from_list(self) -> Optional[Path]:
        """Remove the current file from the in-memory list and return the new current file."""
        if not self.files or self.index < 0:
            return None
        self.files.pop(self.index)
        if not self.files:
            self.index = -1
            return None
        if self.index >= len(self.files):
            self.index = len(self.files) - 1
        return self.current_file
