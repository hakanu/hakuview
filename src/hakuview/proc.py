"""Child-process helpers.

On Windows a GUI app (hakuview-gui.exe / pythonw) has no console, so every console
program it starts (ffprobe, ffmpeg, git, jpegtran...) pops up its own cmd window unless
asked not to. Pass **no_window() to every subprocess.run / Popen made from the GUI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_CREATE_NO_WINDOW = 0x08000000


def no_window() -> dict:
    """subprocess kwargs that keep a console program's window hidden on Windows."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)}
    return {}


def gui_python() -> str:
    """Interpreter for relaunching the GUI: pythonw.exe on Windows (python.exe would open a console)."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    return sys.executable
