"""IrfanView-inspired interactive image canvas with fast zoom, pan, and direct drag-to-crop."""

from __future__ import annotations

from math import gcd
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QMovie,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

from .image_ops import DrawStroke, flip_qimage, paint_overlay_on_painter, render_draw_stroke, rotate_qimage
from .theme import PALETTE


class ImageCanvas(QWidget):
    """Hardware-accelerated QPainter canvas supporting IrfanView-style selection crop, zoom & pan."""

    zoomChanged = Signal(float)
    selectionChanged = Signal(QRect)  # Emitted in image pixel coordinates
    overlayChanged = Signal(bool)  # True when a floating paste overlay is active
    drawModeChanged = Signal(bool)  # True when quick draw & annotate mode is active
    colorPicked = Signal(str, QColor, QPoint)  # (#RRGGBB, QColor, image_pt) when eyedropper samples a pixel
    doubleClicked = Signal()
    wheelNavigate = Signal(int)  # +1 = next file, -1 = previous (when the wheel is set to navigate)

    HANDLE_RADIUS = 6

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._image: Optional[QImage] = None
        self._pixmap: Optional[QPixmap] = None
        self._movie: Optional[QMovie] = None

        self._fit_to_window: bool = True
        self._scale: float = 1.0
        self._pan_offset: QPointF = QPointF(0.0, 0.0)

        # Selection rectangle in image pixel coordinates
        self._selection: QRect = QRect()
        # Floating paste overlay in image pixel coordinates
        self._overlay_image: Optional[QImage] = None
        self._overlay_rect: QRect = QRect()
        self._overlay_opacity: int = 255
        self._overlay_rounded_pct: int = 0
        self._overlay_border: bool = False

        # Quick draw & annotate mode in image pixel coordinates
        self._draw_mode: bool = False
        self._draw_tool: str = "arrow"
        self._draw_color: str = "#ef4444"
        self._draw_size: int = 6
        self._draw_strokes: list[DrawStroke] = []
        self._active_stroke: Optional[DrawStroke] = None

        # Eyedropper / pixel color picker mode (P or Alt+Click)
        self._eyedropper_mode: bool = False
        self._hover_widget_pos: Optional[QPointF] = None

        self._drag_mode: Optional[str] = None  # 'new', 'move', 'n','s','e','w','ne','nw','se','sw', 'pan', 'draw'
        self._drag_start_widget: QPointF = QPointF()
        self._drag_start_image: QPoint = QPoint()
        self._drag_initial_sel: QRect = QRect()
        self._space_pressed: bool = False
        self._show_info_overlay: bool = False
        self._info_text: str = ""
        self._checker_brush: Optional[QBrush] = None
        self._checker_colors: tuple = ()
        self._message: Optional[str] = None  # shown instead of the hint when a file failed to load
        self.wheel_navigates: bool = False
        self._wheel_accum: int = 0

    # ------------------------------------------------------------------
    # Image & Movie management
    # ------------------------------------------------------------------

    def set_image(
        self,
        image: Optional[QImage],
        reset_view: bool = True,
        movie_path: Optional[Path] = None,
    ) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None

        self._image = image
        self._pixmap = QPixmap.fromImage(image) if (image is not None and not image.isNull()) else None
        self.clear_selection()
        if reset_view:
            self.clear_overlay()
            self.stop_draw_mode()
            self.set_eyedropper_mode(False)

        if movie_path is not None and movie_path.suffix.lower() in (".gif", ".webp"):
            movie = QMovie(str(movie_path))
            if movie.frameCount() > 1:
                self._movie = movie
                self._movie.frameChanged.connect(self._on_movie_frame)
                self._movie.start()

        if reset_view:
            self._fit_to_window = True
            self._pan_offset = QPointF(0.0, 0.0)
            self._recompute_fit_scale()
        else:
            if self._fit_to_window:
                self._recompute_fit_scale()
        self.update()

    def set_display_image(self, image: Optional[QImage]) -> None:
        """Show other pixels for the current image (a colour-adjusted copy, or a smaller live
        preview that gets scaled up) without touching zoom, pan or the crop selection.
        None shows the original again."""
        if self._image is None or self._image.isNull():
            return
        source = image if image is not None and not image.isNull() else self._image
        self._pixmap = QPixmap.fromImage(source)
        self.update()

    def _on_movie_frame(self) -> None:
        if self._movie is not None:
            frame_img = self._movie.currentImage()
            if not frame_img.isNull():
                self._image = frame_img
                self._pixmap = QPixmap.fromImage(frame_img)
                self.update()

    @property
    def image(self) -> Optional[QImage]:
        return self._image

    @property
    def selection(self) -> QRect:
        return self._selection

    @property
    def zoom_scale(self) -> float:
        return self._scale

    def set_info_overlay(self, visible: bool, text: str = "") -> None:
        self._show_info_overlay = visible
        self._info_text = text
        self.update()

    def clear_selection(self) -> None:
        if not self._selection.isNull():
            self._selection = QRect()
            self.selectionChanged.emit(QRect())
            self.update()

    def select_all(self) -> None:
        if self._image is not None and not self._image.isNull():
            self._selection = self._image.rect()
            self.selectionChanged.emit(self._selection)
            self.update()

    # ------------------------------------------------------------------
    # Floating Paste Overlay
    # ------------------------------------------------------------------

    @property
    def has_overlay(self) -> bool:
        return (
            self._overlay_image is not None
            and not self._overlay_image.isNull()
            and not self._overlay_rect.isEmpty()
        )

    @property
    def overlay_image(self) -> Optional[QImage]:
        return self._overlay_image

    @property
    def overlay_rect(self) -> QRect:
        return self._overlay_rect

    @property
    def overlay_opacity(self) -> int:
        return self._overlay_opacity

    @property
    def overlay_rounded_pct(self) -> int:
        return self._overlay_rounded_pct

    @property
    def overlay_border(self) -> bool:
        return self._overlay_border

    def start_overlay(self, image: QImage, rect: Optional[QRect] = None) -> bool:
        """Place `image` as a movable/resizable floating layer over the current canvas image."""
        if self._image is None or self._image.isNull() or image is None or image.isNull():
            return False
        self.clear_selection()
        self._overlay_image = image.copy()

        bw, bh = self._image.width(), self._image.height()
        ow, oh = max(1, image.width()), max(1, image.height())
        if rect is not None and not rect.isEmpty():
            self._overlay_rect = rect.intersected(self._image.rect())
        else:
            # Default: fit within 55% of the base image while preserving aspect ratio (or 1:1 if smaller)
            max_w = max(16.0, bw * 0.55)
            max_h = max(16.0, bh * 0.55)
            scale = min(1.0, max_w / ow, max_h / oh)
            if scale < 0.15:
                scale = min(max_w / ow, max_h / oh)
            tw = max(8, min(bw, round(ow * scale)))
            th = max(8, min(bh, round(oh * scale)))
            tx = max(0, (bw - tw) // 2)
            ty = max(0, (bh - th) // 2)
            self._overlay_rect = QRect(tx, ty, tw, th)

        self.overlayChanged.emit(True)
        self.update()
        return True

    def set_overlay_style(
        self,
        *,
        opacity: Optional[int] = None,
        rounded_pct: Optional[int] = None,
        border: Optional[bool] = None,
    ) -> None:
        if opacity is not None:
            self._overlay_opacity = max(10, min(255, int(opacity)))
        if rounded_pct is not None:
            self._overlay_rounded_pct = max(0, min(50, int(rounded_pct)))
        if border is not None:
            self._overlay_border = bool(border)
        self.update()

    def transform_overlay(self, op: str) -> bool:
        """Rotate or flip the floating overlay image in place."""
        if not self.has_overlay or self._overlay_image is None or self._image is None:
            return False
        if op == "rotate_left":
            self._overlay_image = rotate_qimage(self._overlay_image, -90)
        elif op == "rotate_right":
            self._overlay_image = rotate_qimage(self._overlay_image, 90)
        elif op == "flip_h":
            self._overlay_image = flip_qimage(self._overlay_image, horizontal=True)
        elif op == "flip_v":
            self._overlay_image = flip_qimage(self._overlay_image, vertical=True)
        else:
            return False

        if op in ("rotate_left", "rotate_right"):
            c = self._overlay_rect.center()
            nw = min(self._image.width(), self._overlay_rect.height())
            nh = min(self._image.height(), self._overlay_rect.width())
            nx = max(0, min(self._image.width() - nw, c.x() - nw // 2))
            ny = max(0, min(self._image.height() - nh, c.y() - nh // 2))
            self._overlay_rect = QRect(nx, ny, nw, nh)
        self.update()
        return True

    def clear_overlay(self) -> None:
        was_active = self.has_overlay
        self._overlay_image = None
        self._overlay_rect = QRect()
        if was_active:
            self.overlayChanged.emit(False)
            self.update()

    # ------------------------------------------------------------------
    # Quick Draw & Annotate Mode
    # ------------------------------------------------------------------

    @property
    def draw_mode(self) -> bool:
        return self._draw_mode

    @property
    def draw_strokes(self) -> list[DrawStroke]:
        return list(self._draw_strokes)

    def start_draw_mode(self, tool: str = "arrow", color: str = "#ef4444", size: int = 6) -> bool:
        if self._image is None or self._image.isNull():
            return False
        self.clear_selection()
        self.clear_overlay()
        self._draw_mode = True
        self._draw_tool = tool
        self._draw_color = color
        self._draw_size = max(1, int(size))
        self._draw_strokes.clear()
        self._active_stroke = None
        self.drawModeChanged.emit(True)
        self.update()
        return True

    def stop_draw_mode(self) -> None:
        was_active = self._draw_mode or bool(self._draw_strokes)
        self._draw_mode = False
        self._draw_strokes.clear()
        self._active_stroke = None
        if was_active:
            self.drawModeChanged.emit(False)
            self.update()

    def set_draw_style(self, tool: str, color: str, size: int) -> None:
        self._draw_tool = tool
        self._draw_color = color
        self._draw_size = max(1, int(size))

    def add_draw_stroke(self, stroke: DrawStroke) -> None:
        if stroke.points:
            self._draw_strokes.append(stroke)
            self.update()

    def undo_draw_stroke(self) -> bool:
        if not self._draw_strokes:
            return False
        self._draw_strokes.pop()
        self.update()
        return True

    def clear_draw_strokes(self) -> None:
        if self._draw_strokes or self._active_stroke is not None:
            self._draw_strokes.clear()
            self._active_stroke = None
            self.update()

    # ------------------------------------------------------------------
    # Zoom & Pan controls
    # ------------------------------------------------------------------

    def zoom_fit(self) -> None:
        self._fit_to_window = True
        self._pan_offset = QPointF(0.0, 0.0)
        self._recompute_fit_scale()
        self.update()

    def zoom_actual(self) -> None:
        self._fit_to_window = False
        self._pan_offset = QPointF(0.0, 0.0)
        self._set_scale(1.0)
        self.update()

    def zoom_in(self, factor: float = 1.25, anchor: Optional[QPointF] = None) -> None:
        self._zoom_by(factor, anchor)

    def zoom_out(self, factor: float = 1.25, anchor: Optional[QPointF] = None) -> None:
        self._zoom_by(1.0 / factor, anchor)

    def _zoom_by(self, factor: float, anchor: Optional[QPointF] = None) -> None:
        if self._image is None or self._image.isNull():
            return
        old_scale = self._scale
        new_scale = max(0.02, min(32.0, old_scale * factor))
        if abs(new_scale - old_scale) < 1e-6:
            return

        if anchor is None:
            anchor = QPointF(self.width() / 2.0, self.height() / 2.0)

        # Keep the image point under `anchor` stationary
        center = QPointF(self.width() / 2.0, self.height() / 2.0) + self._pan_offset
        rel = anchor - center
        ratio = new_scale / old_scale
        new_center = anchor - rel * ratio
        self._pan_offset = new_center - QPointF(self.width() / 2.0, self.height() / 2.0)

        self._fit_to_window = False
        self._set_scale(new_scale)
        self.update()

    def _set_scale(self, scale: float) -> None:
        self._scale = scale
        self.zoomChanged.emit(self._scale)

    def _recompute_fit_scale(self) -> None:
        if self._image is None or self._image.isNull() or self.width() <= 4 or self.height() <= 4:
            self._set_scale(1.0)
            return
        iw = self._image.width()
        ih = self._image.height()
        vw = max(1, self.width() - 28)
        vh = max(1, self.height() - 28)
        fit = min(vw / iw, vh / ih)
        # Do not upscale small images above 100% in default fit mode (just like IrfanView default)
        self._set_scale(min(1.0, fit))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_to_window:
            self._recompute_fit_scale()

    # ------------------------------------------------------------------
    # Coordinate mapping between Widget and Image pixels
    # ------------------------------------------------------------------

    def _image_target_rect(self) -> QRectF:
        if self._image is None or self._image.isNull():
            return QRectF()
        iw = self._image.width() * self._scale
        ih = self._image.height() * self._scale
        cx = self.width() / 2.0 + self._pan_offset.x()
        cy = self.height() / 2.0 + self._pan_offset.y()
        return QRectF(cx - iw / 2.0, cy - ih / 2.0, iw, ih)

    def widget_to_image(self, pos: QPointF, clamp: bool = True) -> QPoint:
        if self._image is None or self._image.isNull():
            return QPoint(0, 0)
        rect = self._image_target_rect()
        ix = int((pos.x() - rect.left()) / self._scale)
        iy = int((pos.y() - rect.top()) / self._scale)
        if clamp:
            ix = max(0, min(self._image.width(), ix))
            iy = max(0, min(self._image.height(), iy))
        return QPoint(ix, iy)

    def image_rect_to_widget(self, rect: QRect) -> QRectF:
        target = self._image_target_rect()
        x = target.left() + rect.left() * self._scale
        y = target.top() + rect.top() * self._scale
        w = rect.width() * self._scale
        h = rect.height() * self._scale
        return QRectF(x, y, w, h)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform,
            self._scale < 2.0,  # Smooth downscaling; crisp pixel grid when zoomed > 200%
        )

        painter.fillRect(self.rect(), QColor(PALETTE["canvas"]))

        if self._image is None or self._image.isNull() or self._pixmap is None:
            self._draw_empty_placeholder(painter)
            return

        target_rect = self._image_target_rect()

        # Subtle checkerboard behind transparent images
        if self._image.hasAlphaChannel():
            self._draw_checkerboard(painter, target_rect)

        painter.drawPixmap(target_rect, self._pixmap, QRectF(self._pixmap.rect()))

        # Draw floating paste overlay if active
        if self.has_overlay:
            self._draw_floating_overlay(painter)

        # Draw uncommitted quick-draw annotations if active
        if self._draw_strokes or self._active_stroke is not None:
            offset = target_rect.topLeft()
            for stroke in self._draw_strokes:
                render_draw_stroke(painter, stroke, scale=self._scale, offset=offset)
            if self._active_stroke is not None:
                render_draw_stroke(painter, self._active_stroke, scale=self._scale, offset=offset)

        # Draw crop selection rectangle & HUD if active
        if not self._selection.isNull() and self._selection.width() > 0 and self._selection.height() > 0:
            self._draw_selection_overlay(painter, target_rect)

        # Optional info overlay ('I' key)
        if self._show_info_overlay and self._info_text:
            self._draw_info_hud(painter)

        # Live eyedropper color swatch & hex loupe when in eyedropper mode ('P' key)
        if self._eyedropper_mode and self._hover_widget_pos is not None:
            self._draw_eyedropper_loupe(painter, self._hover_widget_pos)

    def set_message(self, message: Optional[str]) -> None:
        """Text to show when there is no image (e.g. why a file failed to load); None = default hint."""
        self._message = message
        self.update()

    def _draw_empty_placeholder(self, painter: QPainter) -> None:
        painter.setPen(QColor(PALETTE["canvas_hint"]))
        font = QFont(self.font())
        font.setPointSize(12)
        painter.setFont(font)
        msg = self._message or (
            "Drop an image or video here, or press O to Open\n\n"
            "← / → : Cycle folder   |   R / L : Rotate   |   Drag + C : Crop\n"
            "W / F5 : Slideshow   |   Ctrl+M : Random File   |   F / F11 : Fullscreen"
        )
        flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
        painter.drawText(self.rect().adjusted(40, 0, -40, 0), flags, msg)

    def _draw_checkerboard(self, painter: QPainter, rect: QRectF) -> None:
        # Only paint the visible part of the image; at high zoom `rect` can be
        # hundreds of thousands of pixels wide. A texture brush tiles natively.
        visible = rect.intersected(QRectF(self.rect()))
        if visible.isEmpty():
            return
        colors = (PALETTE["checker_a"], PALETTE["checker_b"])
        if self._checker_brush is None or self._checker_colors != colors:  # rebuilt on theme change
            tile = 12
            pattern = QPixmap(tile * 2, tile * 2)
            pattern.fill(QColor(colors[0]))
            p = QPainter(pattern)
            p.fillRect(tile, 0, tile, tile, QColor(colors[1]))
            p.fillRect(0, tile, tile, tile, QColor(colors[1]))
            p.end()
            self._checker_brush = QBrush(pattern)
            self._checker_colors = colors
        painter.save()
        painter.setBrushOrigin(rect.topLeft())
        painter.fillRect(visible, self._checker_brush)
        painter.restore()

    def _draw_selection_overlay(self, painter: QPainter, image_rect: QRectF) -> None:
        sel_widget = self.image_rect_to_widget(self._selection)

        # Dim outside region
        outer_path = QPainterPath()
        outer_path.addRect(image_rect)
        inner_path = QPainterPath()
        inner_path.addRect(sel_widget)
        dim_path = outer_path.subtracted(inner_path)
        painter.fillPath(dim_path, QColor(0, 0, 0, 145))

        # Rule-of-thirds grid lines
        if sel_widget.width() > 45 and sel_widget.height() > 45:
            grid_pen = QPen(QColor(255, 255, 255, 70), 1, Qt.PenStyle.DashLine)
            painter.setPen(grid_pen)
            for i in (1, 2):
                gx = sel_widget.left() + sel_widget.width() * i / 3.0
                gy = sel_widget.top() + sel_widget.height() * i / 3.0
                painter.drawLine(QPointF(gx, sel_widget.top()), QPointF(gx, sel_widget.bottom()))
                painter.drawLine(QPointF(sel_widget.left(), gy), QPointF(sel_widget.right(), gy))

        # High-contrast dual border
        painter.setPen(QPen(QColor(0, 0, 0, 200), 2))
        painter.drawRect(sel_widget)
        painter.setPen(QPen(QColor(56, 189, 248), 1.5, Qt.PenStyle.SolidLine))
        painter.drawRect(sel_widget)

        # Corner & edge handles (plus L-brackets for easy border cropping)
        painter.setBrush(QColor(56, 189, 248))
        painter.setPen(QPen(QColor(15, 23, 42), 1.5))
        for pt in self._handle_points(sel_widget).values():
            painter.drawEllipse(pt, 5.5, 5.5)

        # Dimension & Aspect Ratio HUD badge
        w, h = self._selection.width(), self._selection.height()
        g = gcd(w, h) if (w > 0 and h > 0) else 1
        ar_w, ar_h = w // g, h // g
        ar_str = f" ({ar_w}:{ar_h})" if ar_w <= 32 and ar_h <= 32 else ""
        is_full_border = (
            self._image is not None
            and not self._image.isNull()
            and self._selection == self._image.rect()
        )
        action_hint = (
            "Drag any border/corner inward, then press C or Enter"
            if is_full_border
            else "Press C/Enter to Crop  •  Del/X to Erase/Blur (Esc to cancel)"
        )
        badge_text = f"{w} × {h} px{ar_str}  •  {action_hint}"

        font = QFont(self.font())
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(badge_text) + 18
        th = fm.height() + 10

        bx = max(8.0, min(self.width() - tw - 8.0, sel_widget.left()))
        by = sel_widget.top() - th - 6.0
        if by < 8.0:
            by = min(self.height() - th - 8.0, sel_widget.bottom() + 6.0)

        badge_rect = QRectF(bx, by, tw, th)
        painter.setBrush(QColor(15, 23, 42, 225))
        painter.setPen(QPen(QColor(56, 189, 248, 180), 1))
        painter.drawRoundedRect(badge_rect, 5, 5)
        painter.setPen(QColor(241, 245, 249))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

    def _draw_floating_overlay(self, painter: QPainter) -> None:
        if not self.has_overlay or self._overlay_image is None:
            return
        ovl_widget = self.image_rect_to_widget(self._overlay_rect)
        paint_overlay_on_painter(
            painter,
            self._overlay_image,
            ovl_widget,
            opacity=self._overlay_opacity,
            rounded_pct=self._overlay_rounded_pct,
            border=self._overlay_border,
        )

        # High-contrast dashed bounding box + 8 handles
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 190), 2))
        painter.drawRect(ovl_widget)
        painter.setPen(QPen(QColor(52, 211, 153), 1.8, Qt.PenStyle.DashLine))
        painter.drawRect(ovl_widget)

        painter.setBrush(QColor(52, 211, 153))
        painter.setPen(QPen(QColor(15, 23, 42), 1.5))
        for pt in self._handle_points(ovl_widget).values():
            painter.drawEllipse(pt, 5.5, 5.5)

        w, h = self._overlay_rect.width(), self._overlay_rect.height()
        pct = round(self._overlay_opacity * 100 / 255)
        badge_text = f"📌 Paste: {w} × {h} px ({pct}%)  •  Drag to move/resize  •  Enter to Stamp (Esc to cancel)"

        font = QFont(self.font())
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(badge_text) + 18
        th = fm.height() + 10

        bx = max(8.0, min(self.width() - tw - 8.0, ovl_widget.left()))
        by = ovl_widget.top() - th - 6.0
        if by < 8.0:
            by = min(self.height() - th - 8.0, ovl_widget.bottom() + 6.0)

        badge_rect = QRectF(bx, by, tw, th)
        painter.setBrush(QColor(15, 23, 42, 225))
        painter.setPen(QPen(QColor(52, 211, 153, 195), 1))
        painter.drawRoundedRect(badge_rect, 5, 5)
        painter.setPen(QColor(241, 245, 249))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

    def _draw_info_hud(self, painter: QPainter) -> None:
        font = QFont("Monospace", 10)
        painter.setFont(font)
        fm = painter.fontMetrics()
        lines = self._info_text.splitlines()
        max_w = max((fm.horizontalAdvance(line) for line in lines), default=120)
        box_w = max_w + 28
        box_h = len(lines) * (fm.lineSpacing() + 2) + 20
        rect = QRectF(16, 16, box_w, box_h)
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.setPen(QPen(QColor(100, 116, 139, 160), 1))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor(226, 232, 240))
        painter.drawText(
            rect.adjusted(14, 10, -14, -10),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self._info_text,
        )

    @property
    def eyedropper_mode(self) -> bool:
        return self._eyedropper_mode

    def set_eyedropper_mode(self, enabled: bool) -> None:
        if self._eyedropper_mode == enabled:
            return
        self._eyedropper_mode = enabled
        if enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self._hover_widget_pos = None
        self.update()

    def pick_color_at_image_point(self, img_pt: QPoint) -> Optional[tuple[str, QColor, QPoint]]:
        """Sample pixel at `img_pt`, emit `colorPicked`, and return `(hex_upper, QColor, clamped_pt)`."""
        if self._image is None or self._image.isNull():
            return None
        ix = max(0, min(self._image.width() - 1, img_pt.x()))
        iy = max(0, min(self._image.height() - 1, img_pt.y()))
        clamped = QPoint(ix, iy)
        color = self._image.pixelColor(ix, iy)
        hex_code = color.name().upper()
        self.set_eyedropper_mode(False)
        self.colorPicked.emit(hex_code, color, clamped)
        return hex_code, color, clamped

    def _draw_eyedropper_loupe(self, painter: QPainter, pos: QPointF) -> None:
        if self._image is None or self._image.isNull():
            return
        pt = self.widget_to_image(pos, clamp=True)
        ix = max(0, min(self._image.width() - 1, pt.x()))
        iy = max(0, min(self._image.height() - 1, pt.y()))
        c = self._image.pixelColor(ix, iy)
        hex_code = c.name().upper()
        label = f"{hex_code}  •  rgb({c.red()}, {c.green()}, {c.blue()})  [{ix}, {iy}]"

        font = QFont("Monospace", 9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(label) + 46
        th = max(28, fm.height() + 12)
        bx = min(self.width() - tw - 10.0, max(10.0, pos.x() + 16.0))
        by = max(10.0, pos.y() - th - 12.0)

        pill = QRectF(bx, by, tw, th)
        painter.setBrush(QColor(15, 23, 42, 238))
        painter.setPen(QPen(QColor(56, 189, 248, 210), 1.5))
        painter.drawRoundedRect(pill, 7, 7)

        swatch = QRectF(bx + 8, by + (th - 16) / 2.0, 16, 16)
        painter.setBrush(c)
        painter.setPen(QPen(QColor(248, 250, 252, 220), 1.2))
        painter.drawRoundedRect(swatch, 4, 4)

        painter.setPen(QColor(241, 245, 249))
        painter.drawText(
            QRectF(bx + 30, by, tw - 36, th),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )

    # ------------------------------------------------------------------
    # Mouse & Wheel interaction (Crop selection + Pan + Zoom)
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_points(r: QRectF) -> dict[str, QPointF]:
        cx = r.center().x()
        cy = r.center().y()
        return {
            "nw": r.topLeft(),
            "ne": r.topRight(),
            "sw": r.bottomLeft(),
            "se": r.bottomRight(),
            "n": QPointF(cx, r.top()),
            "s": QPointF(cx, r.bottom()),
            "w": QPointF(r.left(), cy),
            "e": QPointF(r.right(), cy),
        }

    def _hit_test_rect(self, rect: QRect, pos: QPointF, *, is_overlay: bool = False) -> Optional[str]:
        if rect.isNull() or rect.isEmpty():
            return None
        sel_w = self.image_rect_to_widget(rect)
        pts = self._handle_points(sel_w)

        # 1. Check 4 corners first, then 4 midpoint handles
        for name in ("nw", "ne", "sw", "se", "n", "s", "w", "e"):
            if (pos - pts[name]).manhattanLength() <= self.HANDLE_RADIUS * 2.4:
                return name

        # 2. Check anywhere along the 4 border edges (within 10px tolerance)
        edge_tol = 10.0
        in_x = (sel_w.left() - edge_tol) <= pos.x() <= (sel_w.right() + edge_tol)
        in_y = (sel_w.top() - edge_tol) <= pos.y() <= (sel_w.bottom() + edge_tol)

        near_left = in_y and abs(pos.x() - sel_w.left()) <= edge_tol
        near_right = in_y and abs(pos.x() - sel_w.right()) <= edge_tol
        near_top = in_x and abs(pos.y() - sel_w.top()) <= edge_tol
        near_bottom = in_x and abs(pos.y() - sel_w.bottom()) <= edge_tol

        if near_top and near_left:
            return "nw"
        if near_top and near_right:
            return "ne"
        if near_bottom and near_left:
            return "sw"
        if near_bottom and near_right:
            return "se"
        if near_top:
            return "n"
        if near_bottom:
            return "s"
        if near_left:
            return "w"
        if near_right:
            return "e"

        if sel_w.contains(pos):
            # If the crop frame is still at the full image border, dragging well inside
            # the interior starts a freehand selection box; otherwise it moves the box.
            if not is_overlay and self._image is not None and rect == self._image.rect():
                return None
            return "move"
        return None

    def _hit_test_selection(self, pos: QPointF) -> Optional[str]:
        if self.has_overlay:
            return self._hit_test_rect(self._overlay_rect, pos, is_overlay=True)
        return self._hit_test_rect(self._selection, pos, is_overlay=False)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._image is None or self._image.isNull():
            return

        pos = event.position()

        # Middle-click or Space+Left-click or Right-click drag pans the canvas
        if (
            event.button() == Qt.MouseButton.MiddleButton
            or (event.button() == Qt.MouseButton.LeftButton and self._space_pressed)
        ):
            self._drag_mode = "pan"
            self._drag_start_widget = pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.RightButton:
            if not self.has_overlay and not self._selection.isNull():
                self.clear_selection()
            else:
                self._drag_mode = "pan"
                self._drag_start_widget = pos
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self._eyedropper_mode or bool(event.modifiers() & Qt.KeyboardModifier.AltModifier):
                img_pt = self.widget_to_image(pos, clamp=True)
                self.pick_color_at_image_point(img_pt)
                return

            if self._draw_mode:
                self._drag_mode = "draw"
                img_pt = self.widget_to_image(pos, clamp=True)
                self._active_stroke = DrawStroke(
                    tool=self._draw_tool,
                    color=self._draw_color,
                    size=self._draw_size,
                    points=[QPointF(img_pt)],
                )
                self.update()
                return

            if self.has_overlay:
                hit = self._hit_test_rect(self._overlay_rect, pos, is_overlay=True)
                if hit is not None:
                    self._drag_mode = hit
                    self._drag_start_image = self.widget_to_image(pos, clamp=False)
                    self._drag_initial_sel = QRect(self._overlay_rect)
                return

            hit = self._hit_test_selection(pos)
            if hit is not None:
                self._drag_mode = hit
                self._drag_start_image = self.widget_to_image(pos, clamp=True)
                self._drag_initial_sel = QRect(self._selection)
            else:
                # Start a new IrfanView-style crop selection rectangle
                self._drag_mode = "new"
                self._drag_start_image = self.widget_to_image(pos, clamp=True)
                self._selection = QRect(self._drag_start_image, QSize(0, 0))
                self.selectionChanged.emit(self._selection)
                self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._eyedropper_mode:
            self._hover_widget_pos = pos
            self.update()

        if self._drag_mode is None:
            self._update_hover_cursor(pos)
            return

        if self._drag_mode == "pan":
            delta = pos - self._drag_start_widget
            self._drag_start_widget = pos
            self._pan_offset += delta
            self.update()
            return

        if self._image is None or self._image.isNull():
            return

        cur_img_pt = self.widget_to_image(pos, clamp=True)
        img_w = self._image.width()
        img_h = self._image.height()

        if self._drag_mode == "draw" and self._active_stroke is not None:
            pt_f = QPointF(cur_img_pt)
            if self._active_stroke.tool in ("pen", "highlighter"):
                self._active_stroke.points.append(pt_f)
            else:
                if len(self._active_stroke.points) == 1:
                    self._active_stroke.points.append(pt_f)
                else:
                    self._active_stroke.points[-1] = pt_f
            self.update()
            return

        if self.has_overlay:
            r = QRect(self._drag_initial_sel)
            if self._drag_mode == "move":
                raw_pt = self.widget_to_image(pos, clamp=False)
                dx = raw_pt.x() - self._drag_start_image.x()
                dy = raw_pt.y() - self._drag_start_image.y()
                nx = max(0, min(max(0, img_w - r.width()), r.left() + dx))
                ny = max(0, min(max(0, img_h - r.height()), r.top() + dy))
                self._overlay_rect = QRect(nx, ny, r.width(), r.height())
            else:
                left, top, right, bottom = r.left(), r.top(), r.left() + r.width(), r.top() + r.height()
                if "w" in self._drag_mode:
                    left = min(cur_img_pt.x(), right - 8)
                if "e" in self._drag_mode:
                    right = max(cur_img_pt.x(), left + 8)
                if "n" in self._drag_mode:
                    top = min(cur_img_pt.y(), bottom - 8)
                if "s" in self._drag_mode:
                    bottom = max(cur_img_pt.y(), top + 8)
                self._overlay_rect = QRect(
                    max(0, left),
                    max(0, top),
                    max(8, min(img_w, right) - max(0, left)),
                    max(8, min(img_h, bottom) - max(0, top)),
                )
            self.update()
            return

        if self._drag_mode == "new":
            x1 = min(self._drag_start_image.x(), cur_img_pt.x())
            y1 = min(self._drag_start_image.y(), cur_img_pt.y())
            x2 = max(self._drag_start_image.x(), cur_img_pt.x())
            y2 = max(self._drag_start_image.y(), cur_img_pt.y())
            self._selection = QRect(x1, y1, x2 - x1, y2 - y1)
        elif self._drag_mode == "move":
            dx = cur_img_pt.x() - self._drag_start_image.x()
            dy = cur_img_pt.y() - self._drag_start_image.y()
            r = QRect(self._drag_initial_sel)
            nx = max(0, min(img_w - r.width(), r.left() + dx))
            ny = max(0, min(img_h - r.height(), r.top() + dy))
            self._selection = QRect(nx, ny, r.width(), r.height())
        else:
            r = QRect(self._drag_initial_sel)
            left, top, right, bottom = r.left(), r.top(), r.left() + r.width(), r.top() + r.height()
            if "w" in self._drag_mode:
                left = min(cur_img_pt.x(), right - 2)
            if "e" in self._drag_mode:
                right = max(cur_img_pt.x(), left + 2)
            if "n" in self._drag_mode:
                top = min(cur_img_pt.y(), bottom - 2)
            if "s" in self._drag_mode:
                bottom = max(cur_img_pt.y(), top + 2)
            self._selection = QRect(
                max(0, left),
                max(0, top),
                min(img_w, right) - max(0, left),
                min(img_h, bottom) - max(0, top),
            )

        self.selectionChanged.emit(self._selection)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode == "draw" and self._active_stroke is not None:
            if self._active_stroke.points:
                self._draw_strokes.append(self._active_stroke)
            self._active_stroke = None
            self._drag_mode = None
            self.update()
            return
        if self._drag_mode == "new":
            if self._selection.width() < 4 or self._selection.height() < 4:
                self.clear_selection()
        self._drag_mode = None
        self._update_hover_cursor(event.position())

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clear_selection()
            self.doubleClicked.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self.wheel_navigates and not ctrl:
            # Ctrl+wheel still zooms; touchpads send many small deltas, so only act on a full notch.
            self._wheel_accum += delta
            if abs(self._wheel_accum) >= 120:
                self.wheelNavigate.emit(-1 if self._wheel_accum > 0 else 1)
                self._wheel_accum = 0
            event.accept()
            return
        if delta > 0:
            self.zoom_in(1.18, event.position())
        else:
            self.zoom_out(1.18, event.position())
        event.accept()

    def set_space_pressed(self, pressed: bool) -> None:
        self._space_pressed = pressed
        if pressed:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def _update_hover_cursor(self, pos: QPointF) -> None:
        if self._space_pressed:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return
        hit = self._hit_test_selection(pos)
        cursor_map = {
            "nw": Qt.CursorShape.SizeFDiagCursor,
            "se": Qt.CursorShape.SizeFDiagCursor,
            "ne": Qt.CursorShape.SizeBDiagCursor,
            "sw": Qt.CursorShape.SizeBDiagCursor,
            "n": Qt.CursorShape.SizeVerCursor,
            "s": Qt.CursorShape.SizeVerCursor,
            "e": Qt.CursorShape.SizeHorCursor,
            "w": Qt.CursorShape.SizeHorCursor,
            "move": Qt.CursorShape.SizeAllCursor,
        }
        if hit in cursor_map:
            self.setCursor(cursor_map[hit])
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
