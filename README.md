# HakuView 🦉

> **Ultra-fast, lightweight IrfanView-inspired image & video viewer with built-in editing superpowers for Linux, macOS & Windows.**  
> Instant middle-of-folder cycling (`←`/`→`), tactile border-drag crop (`C`), floating paste & stamp image mashups (`Ctrl+V`), CapCut/Reels-style pill captions (`A`), face/object blur & pixelation (`X`/`Del`), 9:16 → 16:9 blurred-backdrop video reframe, lossless video trim, and 1-click WhatsApp Web export.

🌐 **Website & Live Video Showcase:** **[https://hakanu.github.io/hakuview/](https://hakanu.github.io/hakuview/)**  
🏷️ **Tags:** `image-viewer` · `video-player` · `photo-editor` · `video-editor` · `irfanview` · `capcut` · `pyside6` · `qt6` · `ffmpeg` · `crop` · `blur-faces` · `pixelate` · `image-collage` · `whatsapp-video`

<p align="center">
  <a href="https://hakanu.github.io/hakuview/">
    <img src="docs/assets/screen-paste.png" alt="HakuView Floating Paste & Stamp" width="840" />
  </a>
</p>

<p align="center">
  <a href="docs/assets/demo.mp4">🎥 <b>Watch the Full Feature Demo Video (<code>docs/assets/demo.mp4</code>)</b></a>
</p>

<p align="center">
  <img src="docs/assets/screen-caption.png" alt="Caption & Pill Text Overlay" width="415" />
  <img src="docs/assets/screen-erase.png" alt="Delete / Pixelate Face Region" width="415" />
</p>
<p align="center">
  <img src="docs/assets/screen-resize.png" alt="CapCut-Style Video Reframe" width="415" />
  <img src="docs/assets/screen-crop.png" alt="Border-Drag Crop" width="415" />
</p>

---

## ⚡ 1-Liner Install

Install globally with desktop shortcuts and right-click **"Open with HakuView"** integration:

```bash
uv tool install git+https://github.com/hakanu/hakuview.git && hakuview --install-desktop
```

On Windows this adds HakuView, with its icon, to Explorer's right-click menu and to the **"Open with" / "How do you want to open this file?"** list. If you set it up with an older version and the icon is missing, run `hakuview --install-desktop` again.

> **Or run instantly without installing:**
> ```bash
> uvx --from git+https://github.com/hakanu/hakuview.git hakuview ~/Pictures
> ```

To update anytime:
```bash
hakuview --update
```

HakuView also checks GitHub for a newer release when it starts (at most every 12 hours) and offers a one-click update. Turn it off from the **⋯** menu → *Check for Updates on Startup*, or run *Check for Updates...* by hand.

---

## 📸 What It Does

- 📂 **Instant Folder Cycling (`←` / `→`)**  
  Open any single picture or video from anywhere in a directory. HakuView naturally orders sibling files and preloads neighbors in the background for zero-latency cycling.
- 🎲 **Random File Jump (`Ctrl+M`)**  
  Jump to a random photo or video in the folder, or launch directly with `hakuview --random ~/Pictures`.
- 🔎 **Go to File (`Ctrl+G`)**  
  Type a position number or part of a file name, pick with `↑` / `↓`, press `Enter`. Handy in folders with hundreds of files.
- 🕘 **Recent Folders**  
  The ⋯ menu lists the last 8 folders you opened (turn off with "Remember window size, position and recent folders" in Settings).
- ✂️ **Border Crop (`C`)**  
  Press `C` to reveal border handles on the image edges. Pull any side or corner inward and press `C` or `Enter` to crop. (Or simply left-drag a freehand box).
- 🧹 **Delete / Erase / Blur Region (`Del` or `X`)**  
  Drag a box over any unwanted face, license plate, watermark, or object and press `Delete` (or press `X` to open the interactive **Erase / Redact Region** dialog for both **images** and **videos**). Choose between **🌫️ Heavy Blur**, **🟦 Pixelate / Mosaic**, **🪄 Smart Heal / Delogo** (interpolates surrounding pixels), **🎨 Surrounding Color Fill**, or **⬛ Blackout** — with optional In/Out timestamp scoping on videos.
- 💬 **Caption / Text Overlay (`💬 Text` or `A` / `Shift+T`)**  
  Add single- or multi-line captions to both **images** and **videos** with a live preview, quick style presets (Reels Dark Pill, Light Pill, Yellow Highlight, Snapchat Full-Width Banner, Classic Impact Meme Outline, Cinema Subtitle), custom fonts & sizes, custom text/box colors & opacity, and flexible placement. Apply non-destructively to images (`Ctrl+Z` undo) or export captioned videos (`<name>_caption.mp4`, optionally scoped to marked In/Out trim points).
- 📌 **Floating Paste & Stamp / Image Mashup (`Ctrl+V` / `Ctrl+Shift+V`)**  
  Copy any image (`Ctrl+C`), cycle to another photo (`→`), and press `Ctrl+V` (or pick a file with `Ctrl+Shift+V` / `Shift+Drop`) to float it on top. Drag to move, drag the 8 handles to resize, rotate/flip the floating piece with `R`/`L`/`H`/`V`, tweak **Opacity**, **Rounded Corners**, or **Frame & Shadow**, or click **◫ Side-by-Side** / **⊟ Stack Below** to stitch images together. Press `Enter` (or `Ctrl+V` again to chain another image!) to stamp it into the `Ctrl+Z` undo stack.
- 🔄 **Quick Rotate & Flip (`R` / `L` / `H` / `V`)**  
  Rotate 90° clockwise/counter-clockwise or flip horizontally/vertically on both images and videos (`<name>_rot90.mp4`, etc.).
- 🔇 **1-Click Lossless Audio Strip (`Edit ▾ → Strip Audio`)**  
  Instantly strip audio from any video (`<name>_muted.mp4`) using FFmpeg stream copy (`-c:v copy -an`) with zero video quality loss.
- 💾 **Non-Destructive & Safe**  
  Edits stay in memory with a 10-step undo (`Ctrl+Z`). Changes are only saved when you press `Ctrl+S` (atomic write) or `Ctrl+Shift+S` (Save As).
- 🗜️ **1-Click Image Compression (`K`)**  
  Click **Compress** or press `K` to save an optimized variant in the same directory (e.g. `image_lossless.png` or `image_lossless.webp`) with zero loss in quality.
- 🎬 **Videos & Slideshows in the Same Window (`W` / `F5`)**  
  Plays MP4, MKV, WebM, MOV, and animated GIFs without switching apps. During slideshows, videos play to completion before auto-advancing.
- ✂️ **Lossless Video Trimming (`[` / `]` or `I` / `O` + `T`)**  
  Scrub to any frame, press `[` (or `I`) to mark start, `]` (or `O`) to mark end, then press `T` or click **✂ Trim**. Uses instant FFmpeg stream copy with zero re-encoding and zero quality loss.
- 📱 **1-Click WhatsApp Web Video Export (`K` or `📱 WhatsApp`)**  
  Click **📱 WhatsApp** in the video bar (or press `K`) to export `<name>_whatsapp.mp4` with lowered H.264 + AAC bitrate (`yuv420p`, `+faststart`) guaranteed to stay under WhatsApp Web's limit.
- 📐 **CapCut-Style Video Reframe & Resize (`📐 Resize`)**  
  Convert vertical videos to landscape (16:9 1080p/720p, 9:16, 1:1, 4:5, 21:9, or custom W×H) with a live preview and automatic dominant-color Gaussian-blurred backdrop, living 4-corner ambient glow, solid dominant fill, or center crop.
- 🎨 **Color Scaling: Brightness & Contrast (`B`)**  
  Real-time brightness and contrast for images and videos. Save the result as a new image, a single video frame, or a whole new video (`<name>_color.mp4`) — originals are never overwritten.
- ℹ️ **Geeky Media Inspector (`I` / `Ctrl+I`)**  
  Opens a detailed technical card showing true compression ratios, video/audio bitrates, bits-per-pixel (BPP) efficiency, container profiles, and camera EXIF data with 1-click clipboard export.
- ⚙️ **Settings & Themes (`Ctrl+,`)**  
  Dark, Light or Follow-system theme, mouse wheel = zoom or next/previous file, sort by name / date / size, default compression preset and slideshow interval. Window size, position, recent folders and volume are remembered.

---

## ⌨️ Essential Shortcuts

| Key | Action |
| :--- | :--- |
| `←` / `→` | Previous / Next file in folder |
| `Ctrl + M` | Jump to a random file (also under `⋮` More Options) |
| `C` | **Border Crop** (drag borders inward, then press `C` or `Enter` to commit) |
| `Del` / `Backspace` or `X` | **Delete / Erase / Blur Region** (Blur, Pixelate, Smart Heal/Delogo, or Blackout a face/object on images & videos) |
| `B` | **Color Scaling** (Live brightness & contrast sliders for images and videos) |
| `A` or `Shift + T` | **Add Caption / Text** (Fonts, sizes, pill/banner/outline backgrounds for images & videos) |
| `R` / `L` | Rotate 90° Right / Left |
| `H` / `V` | Flip Horizontal / Vertical |
| `K` | **1-Click Compress** & save variant |
| `I` or `Ctrl + I` | **Media Inspector / Info Card** (Bitrate, Compression ratio, Codecs, EXIF) |
| `Ctrl + Z` | Undo last edit |
| `Ctrl + S` | Save changes (saves as new image if color scaling is active). Rotated/flipped JPEGs are saved losslessly when `jpegtran` is installed, otherwise at the original quality |
| `[ / I` and `] / O` | **Mark Video In / Out points** for trimming |
| `T` | **Export Trimmed Video** (Instant lossless stream copy) |
| `W` or `F5` | Toggle Slideshow (`[` and `]` change speed during photo view) |
| `F` or `Double-Click` | Fullscreen |
| `+` / `-` or `Wheel` | Zoom (centered at mouse); on video: playback speed. The wheel can step through files instead (Settings) |
| `,` / `.` | Video: step one frame back / forward |
| `Ctrl + C` / `Ctrl + Shift + C` | Copy image (or video frame) / copy file path |
| `Ctrl + V` / `Ctrl + Shift + V` | **Floating Paste Overlay** from clipboard / from file (`Enter` to Stamp, `Esc` to cancel) |
| `F2` | Rename the current file |
| `Ctrl + G` | Go to file by number or name |
| `S` | Video: save the current frame as PNG next to the video (with brightness/contrast) |
| `Ctrl + ,` | Settings (theme, wheel, sorting, defaults) |
| `0` / `1` | Fit to window / 100% actual size |
| `Click Video` or `Space` | Play / Pause (stop) video |
| `Ctrl + W` / `Esc` | Close window / Exit fullscreen / Clear trim |

---

## 🆚 How HakuView Compares

Why use **HakuView** instead of traditional viewers (**IrfanView**, **XnView MP**), paint tools (**KolourPaint**, **Windows Paint**), or heavy video editors (**CapCut**)?

| Feature / Workflow | 🦉 **HakuView** | **IrfanView** | **XnView MP** | **KolourPaint** | **Windows Paint** | **CapCut Desktop** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **OS Support** | **Linux, Windows, macOS** | Windows only | Linux, Win, macOS | Linux (KDE) | Windows only | Win, macOS |
| **License & Price** | **Open Source (MIT)** | Freeware (Closed) | Freeware (Closed) | Open Source (BSD) | Proprietary | Freemium (Paid Pro) |
| **Instant `< 100ms` Folder Cycling (`←`/`→`)** | ✅ **Yes (Preloaded)** | ✅ Yes | ✅ Yes | ❌ No (Editor only) | ❌ No (Editor only) | ❌ No (Project import) |
| **Video Playback in Same Window** | ✅ **Yes (Qt6/FFmpeg)** | ⚠️ Needs plugins | ✅ Yes | ❌ No | ❌ No | ✅ Yes (Heavy timeline) |
| **Tactile Border-Drag Crop (`C`)** | ✅ **Yes** | ⚠️ Box select + menu | ⚠️ Box select + menu | ⚠️ Box select | ⚠️ Box select | ✅ Yes |
| **Floating Paste & Stamp Mashup (`Ctrl+V`)** | ✅ **Yes (Opacity, Corners, Frame, Stitch)** | ❌ Overwrites or fixed paste | ❌ Basic clipboard | ⚠️ Basic raster paste | ⚠️ Basic raster paste | ✅ Full layers (Heavy) |
| **CapCut/Reels Pill Captions (Images & Videos)** | ✅ **Yes (`A`)** | ⚠️ Basic text dialog (Images only) | ⚠️ Images only | ⚠️ Basic text box | ⚠️ Basic text box | ✅ Yes |
| **Face / Object Blur, Pixelate & Smart Heal** | ✅ **Yes (Images & Videos w/ Delogo)** | ⚠️ Blur selection (Images only) | ⚠️ Images only | ❌ Manual smudge | ❌ Manual erase | ⚠️ Paid / keyframes |
| **9:16 → 16:9 Blurred-Backdrop Video Reframe** | ✅ **Yes (Free, 5 Styles)** | ❌ No | ❌ No | ❌ No | ❌ No | 💰 **Paid Pro feature** |
| **Lossless Video Trim (`[`/`]` + `T`) & Mute** | ✅ **Yes (Instant stream copy)** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ Always re-encodes |
| **1-Click WhatsApp Web Video Fix (`< 16 MB`)** | ✅ **Yes (`K`)** | ❌ No | ❌ No | ❌ No | ❌ No | ❌ Manual export settings |
| **Geeky Codec / BPP / EXIF Inspector (`I`)** | ✅ **Yes** | ✅ Yes (`I`) | ✅ Yes | ❌ No | ❌ No | ❌ Basic info |

---

## 📦 Supported Formats

- **Images**: JPEG, PNG, WebP (static & animated), GIF, BMP, TIFF, SVG, ICO, AVIF, HEIC (HEIC needs the optional `pillow-heif`: `uv tool install --force --with pillow-heif git+https://github.com/hakanu/hakuview.git`)
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

- **Hakan Uysal** ([@hakanu_](https://x.com/hakanu_))
- GitHub: [hakanu/hakuview](https://github.com/hakanu/hakuview)

---

## 📄 License

[MIT](LICENSE) © 2026 Hakan Uysal
