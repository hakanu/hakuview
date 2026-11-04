"""Cross-platform desktop shortcut, file association, and self-updater utilities for HakuView."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys

HAKUVIEW_SVG_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect width="128" height="128" rx="26" fill="#0f172a"/>
  <rect x="14" y="22" width="100" height="84" rx="12" fill="#1e293b" stroke="#38bdf8" stroke-width="5"/>
  <circle cx="42" cy="48" r="11" fill="#facc15"/>
  <path d="M22 96 L52 64 L74 84 L92 66 L106 96 Z" fill="#38bdf8"/>
</svg>
"""

MIME_TYPES = [
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
    "image/avif",
    "image/heic",
    "video/mp4",
    "video/x-matroska",
    "video/webm",
    "video/x-msvideo",
    "video/quicktime",
]


def _resolve_executable_command() -> str:
    """Find the installed `hakuview` executable path or fallback to python -m hakuview.cli."""
    which_hv = shutil.which("hakuview")
    if which_hv:
        return str(Path(which_hv).resolve())
    exe_dir = Path(sys.executable).parent
    for candidate_name in ("hakuview", "hakuview.exe", "hakuview-gui.exe"):
        candidate = exe_dir / candidate_name
        if candidate.exists():
            return str(candidate.resolve())
    return f'"{sys.executable}" -m hakuview.cli'


def install_desktop_integration() -> str:
    """Install OS desktop launcher & 'Open With HakuView' association on Ubuntu/Linux or Windows."""
    if sys.platform.startswith("win"):
        return _install_windows_integration()
    return _install_linux_integration()


def uninstall_desktop_integration() -> str:
    """Remove OS desktop launcher & context menu associations."""
    if sys.platform.startswith("win"):
        return _uninstall_windows_integration()
    return _uninstall_linux_integration()


def _install_linux_integration() -> str:
    home = Path.home()
    apps_dir = home / ".local" / "share" / "applications"
    icons_dir = home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
    apps_dir.mkdir(parents=True, exist_ok=True)
    icons_dir.mkdir(parents=True, exist_ok=True)

    icon_path = icons_dir / "hakuview.svg"
    icon_path.write_text(HAKUVIEW_SVG_ICON, encoding="utf-8")

    exec_cmd = _resolve_executable_command()
    mime_str = ";".join(MIME_TYPES) + ";"

    desktop_content = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=HakuView\n"
        "GenericName=Image & Video Viewer\n"
        "Comment=Fast IrfanView-style image and video viewer\n"
        f"Exec={exec_cmd} %f\n"
        f"Icon={icon_path}\n"
        "Terminal=false\n"
        "Categories=Graphics;Viewer;Photography;AudioVideo;Player;\n"
        f"MimeType={mime_str}\n"
        "StartupNotify=false\n"
    )
    desktop_file = apps_dir / "hakuview.desktop"
    desktop_file.write_text(desktop_content, encoding="utf-8")
    desktop_file.chmod(0o755)

    update_db = shutil.which("update-desktop-database")
    if update_db:
        subprocess.run([update_db, str(apps_dir)], check=False, capture_output=True)

    return f"Installed Linux desktop launcher: {desktop_file}"


def _uninstall_linux_integration() -> str:
    home = Path.home()
    desktop_file = home / ".local" / "share" / "applications" / "hakuview.desktop"
    icon_path = home / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps" / "hakuview.svg"
    removed = []
    for p in (desktop_file, icon_path):
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return f"Removed: {', '.join(removed)}" if removed else "No Linux desktop entry found."


def _install_windows_integration() -> str:
    import winreg  # type: ignore[import-not-found]

    exec_cmd = _resolve_executable_command()
    if not exec_cmd.startswith('"'):
        exec_cmd = f'"{exec_cmd}"'

    key_path = r"Software\Classes\*\shell\HakuView"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "Open with HakuView")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path + r"\command") as cmd_key:
        winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, f'{exec_cmd} "%1"')

    return "Registered 'Open with HakuView' in Windows Explorer context menu (HKCU)."


def _uninstall_windows_integration() -> str:
    import winreg  # type: ignore[import-not-found]

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\HakuView\command")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\HakuView")
        return "Removed 'Open with HakuView' from Windows Explorer context menu."
    except OSError:
        return "No Windows registry entry found."


def run_self_update() -> int:
    """Update HakuView using uv tool, pipx, or pip."""
    pkg_root = Path(__file__).resolve().parents[2]
    is_local_checkout = (pkg_root / "pyproject.toml").exists()

    if is_local_checkout and (pkg_root / ".git").exists() and shutil.which("git"):
        print(f"Updating git repository at {pkg_root}...")
        res = subprocess.run(["git", "-C", str(pkg_root), "pull", "--ff-only"], check=False)
        if res.returncode != 0:
            return res.returncode

    if shutil.which("uv"):
        print("Upgrading hakuview via `uv tool`...")
        cmd = (
            ["uv", "tool", "install", "--python", "3.13", "--editable", "--force", str(pkg_root)]
            if is_local_checkout
            else ["uv", "tool", "upgrade", "hakuview"]
        )
        res = subprocess.run(cmd, check=False)
        if res.returncode == 0:
            return 0

    if shutil.which("pipx"):
        print("Upgrading hakuview via `pipx`...")
        res = subprocess.run(["pipx", "upgrade", "hakuview"], check=False)
        if res.returncode == 0:
            return 0

    target = str(pkg_root) if is_local_checkout else "hakuview"
    print("Upgrading hakuview via `pip`...")
    return subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", target], check=False).returncode
