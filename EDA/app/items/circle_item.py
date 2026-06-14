from __future__ import annotations

from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import QRectF, QPointF, Qt
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QPainterPathStroker

_CIRCLE_COLOR = "#00ff88"
_CIRCLE_SEL_COLOR = "#ffcc00"
_HANDLE_SIZE = 40.0
_STROKE_HALF = 15.0


class CircleItem(QGraphicsItem):
    """Окружность на схеме (по двум углам bounding box) с узлами изменения размера."""

    def __init__(self, x1: float, y1: float, x2: float, y2: float,
                 color: str | None = None, parent=None):
        super().__init__(parent)
        self._x1 = min(x1, x2)
        self._y1 = min(y1, y2)
        self._x2 = max(x1, x2)
        self._y2 = max(y1, y2)
        self._color = color
        self._selected = False
        self.setZValue(50)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self._update_pos()

    def _update_pos(self):
        self.setPos(self._x1, self._y1)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            dx = value.x() - self._x1
            dy = value.y() - self._y1
            self._x1 = value.x()
            self._y1 = value.y()
            self._x2 += dx
            self._y2 += dy
        return super().itemChange(change, value)

    def rect(self) -> tuple[float, float, float, float]:
        return (self._x1, self._y1, self._x2, self._y2)

    def set_rect(self, x1: float, y1: float, x2: float, y2: float):
        self.prepareGeometryChange()
        self._x1 = min(x1, x2)
        self._y1 = min(y1, y2)
        self._x2 = max(x1, x2)
        self._y2 = max(y1, y2)
        self._update_pos()
        self.update()

    def color(self) -> str | None:
        return self._color

    def set_color(self, color: str | None):
        self._color = color
        self.update()

    def set_selected(self, val: bool):
        self._selected = val
        self.update()

    def boundingRect(self) -> QRectF:
        hs = _HANDLE_SIZE if self._selected else 0
        m = _STROKE_HALF + hs
        w = abs(self._x2 - self._x1)
        h = abs(self._y2 - self._y1)
        return QRectF(-m, -m, w + 2 * m, h + 2 * m)

    def shape(self) -> QPainterPath:
        if self._selected:
            path = QPainterPath()
            path.addRect(self.boundingRect())
            return path
        w = abs(self._x2 - self._x1)
        h = abs(self._y2 - self._y1)
        path = QPainterPath()
        path.addEllipse(0, 0, w, h)
        stroker = QPainterPathStroker()
        stroker.setWidth(30.0)
        return stroker.createStroke(path)

    def _local_corners(self) -> list[QPointF]:
        w = abs(self._x2 - self._x1)
        h = abs(self._y2 - self._y1)
        return [QPointF(0, 0), QPointF(w, 0), QPointF(0, h), QPointF(w, h)]

    def handle_at(self, scene_pos: QPointF) -> int:
        """Вернуть индекс узла (0-3) или -1 если не попали."""
        if not self._selected:
            return -1
        local = self.mapFromScene(scene_pos)
        hs = _HANDLE_SIZE
        for i, corner in enumerate(self._local_corners()):
            if abs(local.x() - corner.x()) <= hs and abs(local.y() - corner.y()) <= hs:
                return i
        return -1

    def resize_handle(self, handle_idx: int, new_scene_pos: QPointF):
        """Изменить угол по индексу узла (0=TL, 1=TR, 2=BL, 3=BR)."""
        local = self.mapFromScene(new_scene_pos)
        g = 100.0
        snapped_x = round(local.x() / g) * g
        snapped_y = round(local.y() / g) * g
        px, py = self.pos().x(), self.pos().y()
        new_scene_x = snapped_x + px
        new_scene_y = snapped_y + py
        self.prepareGeometryChange()
        if handle_idx == 0:
            self._x1 = min(new_scene_x, self._x2 - g)
            self._y1 = min(new_scene_y, self._y2 - g)
        elif handle_idx == 1:
            self._x2 = max(new_scene_x, self._x1 + g)
            self._y1 = min(new_scene_y, self._y2 - g)
        elif handle_idx == 2:
            self._x1 = min(new_scene_x, self._x2 - g)
            self._y2 = max(new_scene_y, self._y1 + g)
        elif handle_idx == 3:
            self._x2 = max(new_scene_x, self._x1 + g)
            self._y2 = max(new_scene_y, self._y1 + g)
        self._update_pos()
        self.update()

    def paint(self, painter: QPainter, option, widget=None):
        color_hex = self._color or _CIRCLE_COLOR
        color = QColor(_CIRCLE_SEL_COLOR) if self._selected else QColor(color_hex)
        pen = QPen(color, 0.0)
        painter.setPen(pen)
        painter.setBrush(QColor(0, 0, 0, 0))
        w = abs(self._x2 - self._x1)
        h = abs(self._y2 - self._y1)
        painter.drawEllipse(QRectF(0, 0, w, h))

        if self._selected:
            hs = _HANDLE_SIZE
            painter.setPen(QPen(QColor(_CIRCLE_SEL_COLOR), 0.0))
            painter.setBrush(QBrush(QColor(_CIRCLE_SEL_COLOR)))
            for corner in self._local_corners():
                painter.drawRect(QRectF(corner.x() - hs, corner.y() - hs, 2 * hs, 2 * hs))
