"""HakuView: Lightweight, fast IrfanView-style media viewer for Ubuntu and Windows."""

from __future__ import annotations

from pathlib import Path
import subprocess

__app_name__ = "HakuView"

try:  # written by hatch-vcs from `git describe` when the package is built or installed
    from ._version import __version__
except ImportError:
    try:
        from importlib.metadata import version as _dist_version

        __version__ = _dist_version("hakuview")
    except Exception:
        __version__ = "0+unknown"


def display_version() -> str:
    """Version for the About box / --version.

    An editable install records its version when installed, so after new commits it would
    keep showing the old one. When running from a git checkout, ask git for the live state.
    """
    root = Path(__file__).resolve().parents[2]
    if (root / ".git").exists():
        try:
            res = subprocess.run(
                ["git", "-C", str(root), "describe", "--tags", "--always", "--dirty"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            described = res.stdout.strip()
            # Exactly on a release tag (e.g. "v0.3.0"): the version already says it all.
            if res.returncode == 0 and described and described.lstrip("v") != __version__:
                return f"{__version__} ({described})"
        except (OSError, subprocess.SubprocessError):
            pass
    return __version__
