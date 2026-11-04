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


class ImageCanvas(QWidget):
    """Hardware-accelerated QPainter canvas supporting IrfanView-style selection crop, zoom & pan."""

    zoomChanged = Signal(float)
    selectionChanged = Signal(QRect)  # Emitted in image pixel coordinates
    doubleClicked = Signal()

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
        self._drag_mode: Optional[str] = None  # 'new', 'move', 'n','s','e','w','ne','nw','se','sw', 'pan'
        self._drag_start_widget: QPointF = QPointF()
        self._drag_start_image: QPoint = QPoint()
        self._drag_initial_sel: QRect = QRect()
        self._space_pressed: bool = False
        self._show_info_overlay: bool = False
        self._info_text: str = ""
        self._checker_brush: Optional[QBrush] = None

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

        # Dark neutral backdrop (IrfanView fullscreen/dark style)
        painter.fillRect(self.rect(), QColor(20, 22, 26))

        if self._image is None or self._image.isNull() or self._pixmap is None:
            self._draw_empty_placeholder(painter)
            return

        target_rect = self._image_target_rect()

        # Subtle checkerboard behind transparent images
        if self._image.hasAlphaChannel():
            self._draw_checkerboard(painter, target_rect)

        painter.drawPixmap(target_rect, self._pixmap, QRectF(self._pixmap.rect()))

        # Draw crop selection rectangle & HUD if active
        if not self._selection.isNull() and self._selection.width() > 0 and self._selection.height() > 0:
            self._draw_selection_overlay(painter, target_rect)

        # Optional info overlay ('I' key)
        if self._show_info_overlay and self._info_text:
            self._draw_info_hud(painter)

    def _draw_empty_placeholder(self, painter: QPainter) -> None:
        painter.setPen(QColor(145, 152, 165))
        font = QFont(self.font())
        font.setPointSize(12)
        painter.setFont(font)
        msg = (
            "Drop an image or video here, or press O to Open\n\n"
            "← / → : Cycle folder   |   R / L : Rotate   |   Drag + C : Crop\n"
            "W / F5 : Slideshow   |   Ctrl+M : Random File   |   F / F11 : Fullscreen"
        )
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, msg)

    def _draw_checkerboard(self, painter: QPainter, rect: QRectF) -> None:
        # Only paint the visible part of the image; at high zoom `rect` can be
        # hundreds of thousands of pixels wide. A texture brush tiles natively.
        visible = rect.intersected(QRectF(self.rect()))
        if visible.isEmpty():
            return
        if self._checker_brush is None:
            tile = 12
            pattern = QPixmap(tile * 2, tile * 2)
            pattern.fill(QColor(42, 44, 48))
            p = QPainter(pattern)
            p.fillRect(tile, 0, tile, tile, QColor(58, 60, 66))
            p.fillRect(0, tile, tile, tile, QColor(58, 60, 66))
            p.end()
            self._checker_brush = QBrush(pattern)
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
            else "Press C or Enter to Crop (Esc to cancel)"
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

    def _hit_test_selection(self, pos: QPointF) -> Optional[str]:
        if self._selection.isNull():
            return None
        sel_w = self.image_rect_to_widget(self._selection)
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
            # the interior starts a freehand selection box; otherwise it moves the crop box.
            if self._image is not None and self._selection == self._image.rect():
                return None
            return "move"
        return None

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
            if not self._selection.isNull():
                self.clear_selection()
            else:
                self._drag_mode = "pan"
                self._drag_start_widget = pos
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton:
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
