# HakuView Backlog 📌

Planned features and future enhancements for HakuView.

---

## 1. Interactive Spatial Video Cropping (`C`)

- **Status**: Backlog
- **Difficulty**: Medium (~2 hours)
- **Goal**: Enable users to crop the visible rectangular area of any video frame with draggable border handles, exactly like photo border cropping (`C`).

### User Flow
1. While watching or paused on any video, user presses **`C`** (or clicks **Crop**).
2. The interactive border-crop handle overlay (`CropOverlay`) activates directly over the video viewport.
3. User pulls any edge or corner handle inward to frame the cropped region (e.g. 16:9 -> 9:16 vertical crop, removing black letterbox bars, or focusing on a specific subject).
4. User presses **`C`** or **`Enter`** to commit (or **`Esc`** to cancel).
5. HakuView runs an asynchronous background FFmpeg transcode:
   ```bash
   ffmpeg -y -i input.mp4 -vf "crop=w:h:x:y" -c:v libx264 -crf 18 -preset fast -c:a copy input_crop.mp4
   ```
6. A subtle progress bar displays conversion status with a **Cancel** option.
7. Once finished, the cropped video is automatically added to the current folder queue and announced in the status bar (`(Press → to view)`).

### Technical Details & Considerations
- **Aspect Ratio & Coordinate Mapping**: Map viewport crop handles accurately to native video stream pixel coordinates `(w, h, x, y)` accounting for video aspect ratio letterboxing/pillarboxing inside `QVideoWidget`.
- **Even Dimension Rule**: FFmpeg H.264 encoders require width and height to be even numbers (`w = w - (w % 2)`, `h = h - (h % 2)`).
- **Hardware Acceleration**: Auto-detect `h264_nvenc` or `h264_vaapi` where available for near-instant encoding, falling back to CPU `libx264 ultrafast/fast`.
