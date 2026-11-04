"""Check GitHub for a newer tagged release and run the self-updater from the GUI."""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Iterable, Optional, Tuple
import urllib.request

from PySide6.QtCore import QObject, QProcess, QSettings, QThreadPool, Signal

from . import __version__

REPO = "hakanu/hakuview"
TAGS_API_URL = f"https://api.github.com/repos/{REPO}/tags?per_page=100"
RELEASES_URL = f"https://github.com/{REPO}/releases"

# Startup checks are throttled so frequent launches (e.g. opening files from the file
# manager) don't hit GitHub's 60 requests/hour unauthenticated limit.
STARTUP_CHECK_INTERVAL_SEC = 12 * 3600

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?(?:\.dev(\d+))?")


def parse_version(text: str) -> Optional[Tuple[int, int, int, int, int]]:
    """Sortable key for 'v1.2.3', '1.2.3' or '1.2.4.dev5+gabc'; a .devN sorts before its release."""
    m = _VERSION_RE.match(text.strip())
    if not m:
        return None
    major, minor, patch, dev = m.groups()
    is_release = 1 if dev is None else 0
    return (int(major), int(minor), int(patch or 0), is_release, int(dev or 0))


def latest_version(tag_names: Iterable[str]) -> Optional[str]:
    """Highest version-like tag, without the leading 'v'."""
    best: Optional[Tuple[Tuple[int, int, int, int, int], str]] = None
    for name in tag_names:
        key = parse_version(name)
        if key is not None and key[3] == 1 and (best is None or key > best[0]):
            best = (key, name.lstrip("v"))
    return best[1] if best else None


def is_newer(candidate: str, current: str = __version__) -> bool:
    cand, cur = parse_version(candidate), parse_version(current)
    if cand is None:
        return False
    if cur is None or cur[:3] == (0, 0, 0):
        # Unknown local version (e.g. built without git metadata): don't nag on every launch.
        return False
    return cand > cur


def fetch_latest_version(timeout: float = 6.0) -> Optional[str]:
    req = urllib.request.Request(
        TAGS_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"HakuView/{__version__}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        tags = json.loads(resp.read().decode("utf-8"))
    return latest_version(tag.get("name", "") for tag in tags)


def can_self_update() -> bool:
    """A PyInstaller binary can't pip/uv-upgrade itself; it has to be re-downloaded."""
    return not getattr(sys, "frozen", False)


class UpdateSettings:
    """Persisted update preferences (QSettings: HakuView/HakuView, group 'updates')."""

    def __init__(self, settings: Optional[QSettings] = None) -> None:
        self._s = settings or QSettings("HakuView", "HakuView")

    @property
    def check_on_startup(self) -> bool:
        return self._s.value("updates/check_on_startup", True, type=bool)

    @check_on_startup.setter
    def check_on_startup(self, value: bool) -> None:
        self._s.setValue("updates/check_on_startup", bool(value))

    @property
    def skipped_version(self) -> str:
        return self._s.value("updates/skipped_version", "", type=str)

    @skipped_version.setter
    def skipped_version(self, value: str) -> None:
        self._s.setValue("updates/skipped_version", value)

    def startup_check_due(self, now: Optional[float] = None) -> bool:
        if not self.check_on_startup:
            return False
        last = self._s.value("updates/last_check", 0.0, type=float)
        return (now or time.time()) - last >= STARTUP_CHECK_INTERVAL_SEC

    def mark_checked(self, now: Optional[float] = None) -> None:
        self._s.setValue("updates/last_check", now or time.time())


class UpdateChecker(QObject):
    """Fetches the latest tag off the UI thread. Emits finished(latest_version_or_empty, error_or_empty)."""

    finished = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None, fetch=fetch_latest_version) -> None:
        super().__init__(parent)
        self._fetch = fetch
        self.running = False
        self.finished.connect(self._on_finished)  # first slot, so `running` is cleared before others run

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        fetch, emit = self._fetch, self.finished.emit

        def work() -> None:
            try:
                emit(fetch() or "", "")
            except Exception as exc:  # offline, DNS, rate limit, bad JSON...
                emit("", str(exc) or type(exc).__name__)

        QThreadPool.globalInstance().start(work)

    def _on_finished(self, _latest: str, _error: str) -> None:
        self.running = False


class UpdateRunner(QObject):
    """Runs `hakuview --update` (uv tool / pipx / pip / git pull) as a child process."""

    finished = Signal(bool, str)  # success, combined output

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.finished.connect(self._on_finished)
        self.process.errorOccurred.connect(self._on_error)

    def start(self) -> None:
        self.process.start(sys.executable, ["-m", "hakuview.cli", "--update"])

    def _output(self) -> str:
        return bytes(self.process.readAll()).decode("utf-8", errors="replace")

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        ok = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        self.finished.emit(ok, self._output())

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.finished.emit(False, f"Could not start the updater: {self.process.errorString()}")
