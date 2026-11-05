#!/usr/bin/env python3
"""Build a standalone single-file executable of HakuView for Linux or Windows using PyInstaller."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    entry = root / "src" / "hakuview" / "cli.py"
    sys.path.insert(0, str(root / "src"))
    from hakuview.installer import ensure_hakuview_ico

    # Embedded in the .exe: Explorer, the taskbar and "Open with" read the icon from here.
    icon = ensure_hakuview_ico()
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        "hakuview",
        "--icon",
        str(icon),
        "--paths",
        str(root / "src"),
        str(entry),
    ]
    print("Running:", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(root), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
