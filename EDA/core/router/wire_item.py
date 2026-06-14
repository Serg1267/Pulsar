from __future__ import annotations

from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import QRectF, QPointF, Qt
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPainterPath,
                           QPainterPathStroker)

_WIRE_COLOR = "#3377dd"
_WIRE_SEL_COLOR = "#ffcc00"
_WIRE_WIDTH = 1.5
_HIT_MARGIN = 30.0


class WireItem(QGraphicsItem):
    """Одиночный сегмент провода (две точки, H или V)."""

    def __init__(self, points: list[QPointF], placed: bool = True,
                 show_start_pin: bool = True, show_end_pin: bool = True):
        super().__init__()
        self._points = list(points)
        self._placed = placed
        self._show_start_pin = show_start_pin
        self._show_end_pin = show_end_pin
        self._selected = False
        self._active_segment: int = -1
        self._cached_rect: QRectF | None = None
        self._color: str | None = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setZValue(-1)

    def set_selected(self, val: bool):
        self._selected = val
        if not val:
            self._active_segment = -1
        self.update()

    def set_color(self, color: str | None):
        """Установить кастомный цвет провода (None = стандартный)."""
        self._color = color
        self.update()

    def color(self) -> str | None:
        """Вернуть кастомный цвет или None если стандартный."""
        return self._color

    def set_active_segment(self, idx: int):
        self._active_segment = idx
        self.update()

    def set_placed(self, val: bool):
        self._placed = val
        self._cached_rect = None
        self.prepareGeometryChange()
        self.update()

    def set_show_end_pin(self, val: bool):
        self._show_end_pin = val
        self.update()

    def set_show_start_pin(self, val: bool):
        self._show_start_pin = val
        self.update()

    def set_show_pin_at(self, pt: QPointF, visible: bool):
        """Показать/скрыть пин на конкретном конце провода (по координате)."""
        if not self._points:
            return
        if (pt - self._points[0]).manhattanLength() < 1:
            self._show_start_pin = visible
        if (pt - self._points[-1]).manhattanLength() < 1:
            self._show_end_pin = visible
        self.update()

    def translate(self, dx: float, dy: float):
        """Сдвинуть все точки провода на (dx, dy)."""
        self.prepareGeometryChange()
        self._points = [QPointF(p.x() + dx, p.y() + dy) for p in self._points]
        self._cached_rect = None
        self.update()

    def append_points(self, pts: list[QPointF]):
        """Добавить точки в конец провода (для N-mode multi-segment)."""
        self.prepareGeometryChange()
        self._points.extend(pts)
        self._cached_rect = None
        self.update()

    def set_points(self, pts: list[QPointF]):
        """Заменить все точки провода."""
        self.prepareGeometryChange()
        self._points = list(pts)
        self._cached_rect = None
        self.update()

    def points(self) -> list[QPointF]:
        return list(self._points)

    def boundingRect(self) -> QRectF:
        if self._cached_rect is None:
            if not self._points:
                return QRectF()
            xs = [p.x() for p in self._points]
            ys = [p.y() for p in self._points]
            margin = 30.0
            self._cached_rect = QRectF(
                min(xs) - margin, min(ys) - margin,
                max(xs) - min(xs) + 2 * margin,
                max(ys) - min(ys) + 2 * margin,
            )
        return self._cached_rect

    def paint(self, painter: QPainter, option, widget=None):
        if self._selected:
            color = _WIRE_SEL_COLOR
        else:
            color = self._color or _WIRE_COLOR

        pen = QPen(QColor(color), _WIRE_WIDTH)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for i in range(len(self._points) - 1):
            if i == self._active_segment:
                hi_pen = QPen(QColor(_WIRE_SEL_COLOR), _WIRE_WIDTH * 2)
                hi_pen.setCosmetic(True)
                hi_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(hi_pen)
                painter.drawLine(self._points[i], self._points[i + 1])
                painter.setPen(pen)
            else:
                painter.drawLine(self._points[i], self._points[i + 1])

        # Красные пины на концах (только у размещённого провода)
        if self._placed and len(self._points) >= 2:
            painter.setBrush(QBrush(QColor("#ff0000")))
            pen_pin = QPen(QColor("#ff0000"), 0.0)
            painter.setPen(pen_pin)
            if self._show_start_pin:
                painter.drawRect(QRectF(self._points[0].x() - 20, self._points[0].y() - 20, 40, 40))
            if self._show_end_pin:
                painter.drawRect(QRectF(self._points[-1].x() - 20, self._points[-1].y() - 20, 40, 40))
            painter.setBrush(Qt.BrushStyle.NoBrush)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        if len(self._points) < 2:
            return path
        path.moveTo(self._points[0])
        for p in self._points[1:]:
            path.lineTo(p)
        stroker = QPainterPathStroker()
        stroker.setWidth(_HIT_MARGIN * 2)
        stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return stroker.createStroke(path)
