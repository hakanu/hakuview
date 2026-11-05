"""Cross-platform desktop shortcut, file association, and self-updater utilities for HakuView."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

from .folder_scanner import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS

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


def ensure_hakuview_ico() -> Path:
    """Ensure the multi-resolution HakuView ICO file exists on disk and return its path."""
    if sys.platform.startswith("win"):
        base_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "HakuView"
    else:
        base_dir = Path.home() / ".local" / "share" / "icons" / "hakuview"

    ico_path = base_dir / "hakuview.ico"
    if ico_path.exists():
        return ico_path

    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image, ImageDraw

        img = Image.new("RGBA", (256, 256), (15, 23, 42, 255))
        draw = ImageDraw.Draw(img)
        # Rounded container border
        draw.rounded_rectangle([16, 26, 240, 230], radius=28, fill=(30, 41, 59), outline=(56, 189, 248), width=10)
        # Golden eye
        draw.ellipse([64, 74, 116, 126], fill=(250, 204, 21))
        # Stylized mountain / wings
        draw.polygon([(36, 204), (104, 124), (148, 168), (188, 128), (220, 204)], fill=(56, 189, 248))
        img.save(
            str(ico_path),
            format="ICO",
            sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        )
    except Exception:
        pass
    return ico_path


# Thumbnail providers kept on HakuView's ProgIDs so Explorer still shows previews.
_PHOTO_THUMBNAILER = "{C7657C4A-9F68-40fa-A4DF-96BC08EB3551}"
_VIDEO_THUMBNAILER = "{9DBD2C50-62AD-11D0-B88D-00A024ABF13F}"
_SHELLEX_THUMBNAIL_KEYS = ("{e357fccd-a995-4576-b01f-234630154e96}", "{BB2E617C-0920-11d1-9A0B-00C04FC2D6C1}")
_WIN_PROGIDS = {
    "HakuView.Image": ("HakuView Image", "image", _PHOTO_THUMBNAILER, IMAGE_EXTENSIONS),
    "HakuView.Video": ("HakuView Video", "video", _VIDEO_THUMBNAILER, VIDEO_EXTENSIONS),
}
_WIN_APP_EXES = ("hakuview-gui.exe", "hakuview.exe")
_WIN_CONTEXT_MENU_KEYS = (r"Software\Classes\*\shell\HakuView", r"Software\Classes\Directory\shell\HakuView")


def _resolve_windows_command() -> str:
    """Quoted executable for registry commands. Prefers hakuview-gui.exe: the console
    hakuview.exe would flash a terminal window every time a file is opened from Explorer."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    candidates = [Path(sys.executable).parent / "hakuview-gui.exe", Path(sys.executable).parent / "Scripts" / "hakuview-gui.exe"]
    which_gui = shutil.which("hakuview-gui")
    if which_gui:
        candidates.insert(0, Path(which_gui))
    for candidate in candidates:
        if candidate.exists():
            return f'"{candidate.resolve()}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return f'"{pythonw if pythonw.exists() else sys.executable}" -m hakuview.cli'


def _reg_set(path: str, values: dict) -> None:
    """Create HKCU\\<path> and write string values ('' = the key's default value)."""
    import winreg  # type: ignore[import-not-found]

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        for name, value in values.items():
            if isinstance(value, int):
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, value)
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _reg_delete_tree(path: str) -> bool:
    """Delete HKCU\\<path> with all its subkeys; False if it didn't exist."""
    import winreg  # type: ignore[import-not-found]

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as k:
            children = []
            i = 0
            while True:
                try:
                    children.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        return False
    for child in children:
        _reg_delete_tree(rf"{path}\{child}")
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return False
    return True


def _refresh_windows_shell() -> None:
    """Tell Explorer associations changed and rebuild its icon cache, so the new icon
    appears in "Open with" without logging out."""
    try:
        import ctypes

        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass
    try:
        subprocess.run(["ie4uinit.exe", "-show"], check=False, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def _install_windows_integration() -> str:
    import winreg  # type: ignore[import-not-found]

    ico_path = ensure_hakuview_ico()
    # Icon locations are "path,index" without quotes; the shell splits on the last comma.
    icon = f"{ico_path},0"
    exec_cmd = _resolve_windows_command()
    open_cmd = f'{exec_cmd} "%1"'

    # 1. Explorer context menu for all files and folders.
    for target in _WIN_CONTEXT_MENU_KEYS:
        title = "Open with HakuView" if "*" in target else "Open Folder in HakuView"
        _reg_set(target, {"": title, "Icon": icon})
        _reg_set(target + r"\command", {"": open_cmd})

    # 2. ProgIDs. The "How do you want to open this file?" / "Open with" dialog takes the
    # name and icon of a ProgID entry from its Application subkey; without one it falls back
    # to the icon embedded in the .exe, and pip/uv launcher stubs have none. This is what makes
    # HakuView's own icon show up there.
    for progid, (type_name, perceived, thumbnailer, _exts) in _WIN_PROGIDS.items():
        base = rf"Software\Classes\{progid}"
        _reg_set(base, {"": f"{type_name} File", "FriendlyTypeName": type_name, "PerceivedType": perceived})
        _reg_set(base + r"\DefaultIcon", {"": icon})
        _reg_set(
            base + r"\Application",
            {
                "ApplicationName": "HakuView",
                "ApplicationIcon": icon,
                "ApplicationCompany": "HakuView",
                "ApplicationDescription": "Fast, keyboard-driven image and video viewer",
            },
        )
        _reg_set(base + r"\shell\open", {"Icon": icon})
        _reg_set(base + r"\shell\open\command", {"": open_cmd})
        for shellex in _SHELLEX_THUMBNAIL_KEYS:
            _reg_set(rf"{base}\ShellEx\{shellex}", {"": thumbnailer})

    # 3. Applications\<exe>: used when the user browses to the .exe in "Choose another app".
    for exe_name in _WIN_APP_EXES:
        app_path = rf"Software\Classes\Applications\{exe_name}"
        _reg_set(app_path, {"FriendlyAppName": "HakuView"})
        _reg_set(app_path + r"\DefaultIcon", {"": icon})
        _reg_set(app_path + r"\shell\open", {"Icon": icon})
        _reg_set(app_path + r"\shell\open\command", {"": open_cmd})
        _reg_set(app_path + r"\SupportedTypes", {ext: "" for ext in sorted(SUPPORTED_EXTENSIONS)})

    # 4. List HakuView under every media extension's "Open with", keeping thumbnails working.
    # If the user once chose "Always use hakuview-gui.exe", Windows pointed .ext straight at the
    # exe (losing PerceivedType and the thumbnail handlers); point it back at our ProgID.
    for progid, (_type_name, perceived, _thumbnailer, exts) in _WIN_PROGIDS.items():
        for ext in exts:
            ext_key = rf"Software\Classes\{ext}"
            try:
                _reg_set(ext_key, {"PerceivedType": perceived})
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ext_key, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
                    try:
                        val, _ = winreg.QueryValueEx(k, "")
                        if "hakuview" in val.lower() or val.endswith(".exe"):
                            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, progid)
                    except OSError:
                        pass
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{ext_key}\OpenWithProgids") as k:
                    winreg.SetValueEx(k, progid, 0, winreg.REG_NONE, b"")
            except OSError:
                pass

    _refresh_windows_shell()
    return f"Registered HakuView in Explorer's 'Open with' list with its icon ({ico_path}), command: {exec_cmd}"


def _uninstall_windows_integration() -> str:
    import winreg  # type: ignore[import-not-found]

    removed = [key for key in _WIN_CONTEXT_MENU_KEYS if _reg_delete_tree(key)]
    for progid, (_type_name, _perceived, _thumbnailer, exts) in _WIN_PROGIDS.items():
        if _reg_delete_tree(rf"Software\Classes\{progid}"):
            removed.append(progid)
        for ext in exts:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}\OpenWithProgids", 0, winreg.KEY_SET_VALUE
                ) as k:
                    winreg.DeleteValue(k, progid)
            except OSError:
                pass
    for exe_name in _WIN_APP_EXES:
        if _reg_delete_tree(rf"Software\Classes\Applications\{exe_name}"):
            removed.append(exe_name)
    _refresh_windows_shell()
    if not removed:
        return "No HakuView Explorer integration found."
    return "Removed HakuView from Explorer (context menu, 'Open with' entries and file types)."


def run_self_update() -> int:
    """Update HakuView using uv tool, pipx, or pip."""
    pkg_root = Path(__file__).resolve().parents[2]
    is_local_checkout = (pkg_root / "pyproject.toml").exists()

    if is_local_checkout and (pkg_root / ".git").exists() and shutil.which("git"):
        # Check if an upstream tracking branch is configured before attempting git pull
        has_upstream = (
            subprocess.run(
                ["git", "-C", str(pkg_root), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )

        if has_upstream:
            print(f"Pulling latest git changes at {pkg_root}...")
            res = subprocess.run(["git", "-C", str(pkg_root), "pull", "--ff-only"], check=False)
            if res.returncode != 0:
                print("Warning: git pull failed; proceeding with package upgrade...")
        else:
            print(f"Local checkout at {pkg_root} has no upstream tracking branch; skipping git pull.")

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
