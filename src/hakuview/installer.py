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


def _install_windows_integration() -> str:
    import winreg  # type: ignore[import-not-found]
    import ctypes

    ico_path = ensure_hakuview_ico()
    exec_cmd = _resolve_executable_command()
    if not exec_cmd.startswith('"'):
        exec_cmd = f'"{exec_cmd}"'

    # 1. Register Context Menu for all files ('*' and 'Directory') with the official icon
    for target in (r"Software\Classes\*\shell\HakuView", r"Software\Classes\Directory\shell\HakuView"):
        title = "Open with HakuView" if "*" in target else "Open Folder in HakuView"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, target) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, title)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, f'"{ico_path}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, target + r"\command") as cmd_key:
            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, f'{exec_cmd} "%1"')

    # 2. Register Application in Windows Explorer with proper Icon & SupportedTypes
    # This ensures that "Open with -> hakuview-gui.exe" displays the real HakuView icon
    for exe_name in ("hakuview-gui.exe", "hakuview.exe"):
        app_path = rf"Software\Classes\Applications\{exe_name}"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, app_path) as k:
            winreg.SetValueEx(k, "FriendlyAppName", 0, winreg.REG_SZ, "HakuView")
            winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, f'"{ico_path}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_path}\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{ico_path}",0')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_path}\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'{exec_cmd} "%1"')
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{app_path}\SupportedTypes") as k:
            for ext in sorted(SUPPORTED_EXTENSIONS):
                winreg.SetValueEx(k, ext, 0, winreg.REG_SZ, "")

    # 3. Create ProgIDs HakuView.Image and HakuView.Video that preserve Windows Shell Thumbnail generation
    # Photo Thumbnail Provider: {C7657C4A-9F68-40fa-A4DF-96BC08EB3551}
    # Video Thumbnail Provider: {9DBD2C50-62AD-11D0-B88D-00A024ABF13F}
    img_progid = r"Software\Classes\HakuView.Image"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, img_progid) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "HakuView Image File")
        winreg.SetValueEx(k, "FriendlyTypeName", 0, winreg.REG_SZ, "HakuView Image")
        winreg.SetValueEx(k, "PerceivedType", 0, winreg.REG_SZ, "image")
        winreg.SetValueEx(k, "Treatment", 0, winreg.REG_DWORD, 0)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{img_progid}\DefaultIcon") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{ico_path}",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{img_progid}\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'{exec_cmd} "%1"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{img_progid}\ShellEx\{{e357fccd-a995-4576-b01f-234630154e96}}") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "{C7657C4A-9F68-40fa-A4DF-96BC08EB3551}")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{img_progid}\ShellEx\{{BB2E617C-0920-11d1-9A0B-00C04FC2D6C1}}") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "{C7657C4A-9F68-40fa-A4DF-96BC08EB3551}")

    vid_progid = r"Software\Classes\HakuView.Video"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, vid_progid) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "HakuView Video File")
        winreg.SetValueEx(k, "FriendlyTypeName", 0, winreg.REG_SZ, "HakuView Video")
        winreg.SetValueEx(k, "PerceivedType", 0, winreg.REG_SZ, "video")
        winreg.SetValueEx(k, "Treatment", 0, winreg.REG_DWORD, 0)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{vid_progid}\DefaultIcon") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'"{ico_path}",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{vid_progid}\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f'{exec_cmd} "%1"')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{vid_progid}\ShellEx\{{e357fccd-a995-4576-b01f-234630154e96}}") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "{9DBD2C50-62AD-11D0-B88D-00A024ABF13F}")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{vid_progid}\ShellEx\{{BB2E617C-0920-11d1-9A0B-00C04FC2D6C1}}") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "{9DBD2C50-62AD-11D0-B88D-00A024ABF13F}")

    # 4. Repair thumbnail generation for all media extensions in HKCU\Software\Classes
    # If the user selected "Always open with hakuview-gui.exe", Windows created keys in HKCU\Software\Classes\.ext
    # that lack PerceivedType or thumbnail handlers. We ensure PerceivedType="image" / "video" and link OpenWithProgids!
    for ext in IMAGE_EXTENSIONS:
        ext_key = rf"Software\Classes\{ext}"
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ext_key) as k:
                winreg.SetValueEx(k, "PerceivedType", 0, winreg.REG_SZ, "image")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ext_key, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
                try:
                    val, _ = winreg.QueryValueEx(k, "")
                    if "hakuview" in val.lower() or val.endswith(".exe"):
                        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "HakuView.Image")
                except OSError:
                    pass
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{ext_key}\OpenWithProgids") as k:
                winreg.SetValueEx(k, "HakuView.Image", 0, winreg.REG_NONE, b"")
        except OSError:
            pass

    for ext in VIDEO_EXTENSIONS:
        ext_key = rf"Software\Classes\{ext}"
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, ext_key) as k:
                winreg.SetValueEx(k, "PerceivedType", 0, winreg.REG_SZ, "video")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ext_key, 0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
                try:
                    val, _ = winreg.QueryValueEx(k, "")
                    if "hakuview" in val.lower() or val.endswith(".exe"):
                        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "HakuView.Video")
                except OSError:
                    pass
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{ext_key}\OpenWithProgids") as k:
                winreg.SetValueEx(k, "HakuView.Video", 0, winreg.REG_NONE, b"")
        except OSError:
            pass

    # 5. Flush and refresh Windows Explorer shell icon & thumbnail cache
    try:
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass

    return f"Successfully registered HakuView icon and restored Windows thumbnail previews: {ico_path}"


def _uninstall_windows_integration() -> str:
    import winreg  # type: ignore[import-not-found]
    import ctypes

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\HakuView\command")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\*\shell\HakuView")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\shell\HakuView\command")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\Directory\shell\HakuView")
    except OSError:
        pass

    try:
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass

    return "Removed 'Open with HakuView' from Windows Explorer context menu."


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
