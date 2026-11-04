# HakuView (`hakuview`)

**HakuView** is an ultra-fast, lightweight, keyboard-first desktop **image and video viewer** for **Ubuntu** and **Windows**, inspired by classic **IrfanView**.

Open any image or video from the middle of a folder—or pick one at random—and cycle effortlessly through the entire directory with your arrow keys while rotating, cropping, or running slideshows on the fly.

---

## ✨ Highlights

- **Instant Middle-of-Folder Cycling (`←` / `→`)**
  - Open any file in a folder and immediately step backward/forward through all sibling images and videos.
  - Uses single-pass `os.scandir` with **natural sorting** (`photo2.jpg` comes before `photo10.jpg`).
  - **Background LRU Preloader**: Neighboring images (`±2` around your current position) are decoded in background threads so arrow-key switching feels instantaneous.
- **Random File Jump (`Ctrl+M` or `--random`)**
  - Jump to a random image or video in the current folder at any time with `Ctrl+M`, or launch directly into a random file from the command line (`hakuview --random ~/Pictures`).
- **Border Crop (`C`) & Freehand Drag-to-Crop**
  - **Rotate & Flip**: Press `R` (Rotate 90° CW), `L` (Rotate 90° CCW), `H` (Flip Horizontal), or `V` (Flip Vertical).
  - **Border Crop (`C`)**: Press `C` to immediately place crop handles along the 4 outer borders of the image—simply grab any border or corner and pull it inward, then press `C` or `Enter` again to apply the crop! (You can also left-click and drag anywhere inside the image to draw a custom crop box.)
  - **Non-Destructive & Atomic Save**: Edits stay in memory with a 10-step undo stack (`Ctrl+Z`) until you explicitly save with `Ctrl+S` (in-place atomic write that never corrupts files) or `Ctrl+Shift+S` (Save As).
- **1-Click Image Compression as Variant (`K` or `🗜 Compress`)**
  - Click **`🗜 Compress (K)`** (or press `K` / `Ctrl+K`) to immediately save an optimized variant in the same folder (e.g., `photo_lossless.png`, `photo_lossless.webp`, `photo_q85.jpg`) without overwriting your original file, showing exact before/after file sizes and percentage saved in the status bar.
  - Includes 4 selectable compression methods right in the toolbar:
    1. **Lossless (Auto)** *(default)*: 100% lossless optimization (`jpegtran`/progressive Huffman for JPEG, `compress_level=9` max deflate for PNG, lossless WebP, Adobe Deflate for TIFF, or lossless PNG conversion for uncompressed BMPs).
    2. **Lossless WebP**: 100% pixel-exact lossless WebP (`lossless=True, method=6`), typically 25–45% smaller than PNG.
    3. **Smart Balanced (Q85)**: Visually lossless high-efficiency compression (`quality=85` / 256-color quantized PNG).
    4. **Compact WebP (Q80)**: Maximum space savings in modern WebP format.
- **Flexible Slideshow Mode (`W` or `F5`)**
  - Toggle automatic slideshow playback at any time.
  - Adjust speed live with `[` and `]` (or the toolbar spinner, `0.5s` – `120s`), and toggle **Shuffle** for random order.
  - Automatically plays videos to completion before advancing to the next slide.
- **Seamless Image + Video Playback in One Window**
  - Supports **Images**: `JPG`, `JPEG`, `PNG`, `WEBP` (static & animated), `GIF` (animated), `BMP`, `TIFF`, `ICO`, `SVG`, `AVIF`, `HEIC`, `PBM`, `PGM`, `PPM`.
  - Supports **Videos**: `MP4`, `MKV`, `WEBM`, `AVI`, `MOV`, `M4V`, `WMV`, `FLV`, `MPG`, `MPEG`, `3GP`, `TS`.
  - Arrow keys (`←` / `→`) always cycle folder files so you never get stuck inside a video; use `Shift+←` / `Shift+→` to seek `-5s` / `+5s` within a video.

---

## 🚀 Installation & Updates

### Ubuntu / Linux

```bash
# 1. Clone the repository and install globally as an isolated CLI & GUI app via uv (or pipx)
git clone <repo-url> hakuview
cd hakuview
uv tool install --python 3.13 --editable .
# (Alternatively with pipx: pipx install .)

# 2. Register Desktop Launcher & "Open with HakuView" right-click menu in Files (Nautilus)
hakuview --install-desktop
```

### Windows (PowerShell / CMD)

```powershell
# 1. Install via uv or pipx
uv tool install --python 3.13 .\hakuview
# (or: pipx install .\hakuview)

# 2. Add "Open with HakuView" to Windows Explorer right-click menu (no Admin required)
hakuview --install-desktop
```

### One-Command Self-Update

```bash
hakuview --update
```

### Optional: Build a Standalone Single-File Binary (`.exe` / Linux Binary)

If you want a zero-dependency executable to copy onto another machine:

```bash
uv run python scripts/build_binary.py
# Output binary is placed in: dist/hakuview (Ubuntu) or dist\hakuview.exe (Windows)
```

---

## 💻 Command-Line Usage

```bash
# Launch empty viewer (or drag & drop files in)
hakuview

# Open a specific image in the middle of a folder and cycle from there
hakuview ~/Pictures/vacation/IMG_0420.JPG

# Open a random image/video from a folder
hakuview --random ~/Pictures

# Start a fullscreen slideshow with a 2.5-second interval
hakuview --slideshow --interval 2.5 --fullscreen ~/Pictures

# Remove OS desktop launcher / context-menu association
hakuview --uninstall-desktop
```

---

## ⌨️ Keyboard & Mouse Shortcuts

### Navigation & Window
| Shortcut | Action |
| :--- | :--- |
| `←` / `→` or `PageUp` / `PageDown` | Previous / Next image or video in folder |
| `Home` / `End` | Jump to First / Last file in folder |
| `Ctrl+M` | Jump to a **Random** file in the folder |
| `O` or `Ctrl+O` | Open file dialog |
| `F` or `F11` or `Double-Click` | Toggle distraction-free Fullscreen |
| `I` | Toggle File Info HUD overlay (resolution, size, timestamp) |
| `Delete` | Delete current file from disk (with confirmation prompt) |
| `Ctrl+W` / `Ctrl+Q` | Close HakuView |
| `Esc` | Clear crop selection → Stop slideshow → Exit fullscreen |

### Rotate, Flip, Crop & Save
| Shortcut | Action |
| :--- | :--- |
| `R` | Rotate 90° Clockwise (Right) |
| `L` | Rotate 90° Counter-Clockwise (Left) |
| `H` / `V` | Flip Horizontal / Flip Vertical |
| `C` (1st press) | **Border Crop Mode**: Activate crop frame along outer image borders (drag any edge/corner inward) |
| `C` (2nd press) or `Enter` or `Ctrl+Y` | **Apply Crop** to the adjusted border frame or freehand selection |
| `Left-Click + Drag` | Optional: Draw a freehand crop box anywhere inside the image |
| `Right-Click` or `Esc` | Clear crop selection |
| `Ctrl+A` | Select entire image |
| `Ctrl+Z` | Undo last edit (up to 10 steps) |
| `Ctrl+S` | Save changes in-place (atomic write) |
| `Ctrl+Shift+S` | Save As new file |
| `K` or `Ctrl+K` | **1-Click Compress & Save as Variant** (using selected toolbar compression preset) |

### Zoom & Pan
| Shortcut | Action |
| :--- | :--- |
| `Mouse Wheel` or `+` / `-` | Smooth Zoom In / Out anchored at cursor |
| `0` | Fit image to window |
| `1` | Actual pixel size (`100%` / `1:1`) |
| `Space + Left-Drag` or `Middle-Drag` | Pan across zoomed image |

### Slideshow & Video Playback
| Shortcut | Action |
| :--- | :--- |
| `W` or `F5` | Start / Stop Slideshow |
| `[` / `]` | Decrease / Increase slideshow interval (`-0.5s` / `+0.5s`) |
| `Space` (during video) | Play / Pause video |
| `Shift + ←` / `Shift + →` (during video) | Seek `-5s` / `+5s` within current video |
| `M` (during video) | Mute / Unmute video audio |

---

## 🧪 Development & Testing

```bash
uv sync --python 3.13 --extra dev
uv run pytest -v
```
