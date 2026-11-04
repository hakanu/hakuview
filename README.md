# HakuView 🦉

> **Fast, lightweight IrfanView-inspired image and video viewer for Ubuntu & Windows.**  
> Open any image from the middle of a folder, cycle with arrow keys, border-crop, rotate, compress, and run slideshows without lag.

---

## ⚡ 1-Liner Install

Install globally with desktop shortcuts and right-click **"Open with HakuView"** integration:

```bash
uv tool install git+https://github.com/hakanu/hakuview.git && hakuview --install-desktop
```

> **Or run instantly without installing:**
> ```bash
> uvx --from git+https://github.com/hakanu/hakuview.git hakuview ~/Pictures
> ```

To update anytime:
```bash
hakuview --update
```

---

## 📸 What It Does

- 📂 **Instant Folder Cycling (`←` / `→`)**  
  Open any single picture or video from anywhere in a directory. HakuView naturally orders sibling files and preloads neighbors in the background for zero-latency cycling.
- 🎲 **Random File Jump (`Ctrl+M`)**  
  Jump to a random photo or video in the folder, or launch directly with `hakuview --random ~/Pictures`.
- ✂️ **Border Crop (`C`)**  
  Press `C` to reveal border handles on the image edges. Pull any side or corner inward and press `C` or `Enter` to crop. (Or simply left-drag a freehand box).
- 🔄 **Quick Rotate & Flip (`R` / `L` / `H` / `V`)**  
  Rotate 90° clockwise/counter-clockwise or flip instantly.
- 💾 **Non-Destructive & Safe**  
  Edits stay in memory with a 10-step undo (`Ctrl+Z`). Changes are only saved when you press `Ctrl+S` (atomic write) or `Ctrl+Shift+S` (Save As).
- 🗜️ **1-Click Image Compression (`K`)**  
  Click **Compress** or press `K` to save an optimized variant in the same directory (e.g. `image_lossless.png` or `image_lossless.webp`) with zero loss in quality.
- 🎬 **Videos & Slideshows in the Same Window (`W` / `F5`)**  
  Plays MP4, MKV, WebM, MOV, and animated GIFs without switching apps. During slideshows, videos play to completion before auto-advancing.
- ✂️ **Lossless Video Trimming (`[` / `]` or `I` / `O` + `T`)**  
  Scrub to any frame, press `[` (or `I`) to mark start, `]` (or `O`) to mark end, then press `T` or click **✂ Trim**. Uses instant FFmpeg stream copy with zero re-encoding and zero quality loss.

---

## ⌨️ Essential Shortcuts

| Key | Action |
| :--- | :--- |
| `←` / `→` | Previous / Next file in folder |
| `Ctrl + M` | Jump to a random file |
| `C` | **Border Crop** (drag borders inward, then press `C` or `Enter` to commit) |
| `R` / `L` | Rotate 90° Right / Left |
| `H` / `V` | Flip Horizontal / Vertical |
| `K` | **1-Click Compress** & save variant |
| `Ctrl + Z` | Undo last edit |
| `Ctrl + S` | Save changes |
| `[ / I` and `] / O` | **Mark Video In / Out points** for trimming |
| `T` | **Export Trimmed Video** (Instant lossless stream copy) |
| `W` or `F5` | Toggle Slideshow (`[` and `]` change speed during photo view) |
| `F` or `Double-Click` | Fullscreen |
| `+` / `-` or `Wheel` | Zoom (centered at mouse) |
| `0` / `1` | Fit to window / 100% actual size |
| `Click Video` or `Space` | Play / Pause (stop) video |
| `Ctrl + W` / `Esc` | Close window / Exit fullscreen / Clear trim |

---

## 📦 Supported Formats

- **Images**: JPEG, PNG, WebP (static & animated), GIF, BMP, TIFF, SVG, ICO, AVIF, HEIC
- **Videos**: MP4, MKV, WebM, AVI, MOV, M4V, WMV, FLV, MPG, TS

---

## 🛠 Development

```bash
git clone https://github.com/hakanu/hakuview.git
cd hakuview
uv sync --extra dev
uv run pytest -v
```

---

## 👤 Author

- **Hakan Ünal** ([@hakanu_](https://x.com/hakanu_))
- GitHub: [hakanu/hakuview](https://github.com/hakanu/hakuview)

---

## 📄 License

[MIT](LICENSE) © 2026 Hakan Ünal
