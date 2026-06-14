from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath

from EDA.app.items.colors import junction_color


class JunctionItem(QGraphicsItem):
    """Точка соединения двух проводов."""

    def __init__(self, pos: QPointF):
        super().__init__()
        self.setPos(pos)
        self.setZValue(0)
        self._selected = False

    def set_selected(self, val: bool):
        self._selected = val
        self.update()

    def rotate(self, angle_delta: float):
        pass

    def boundingRect(self):
        return QRectF(-50, -50, 100, 100)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), 45, 45)
        return path

    def paint(self, painter, option, widget=None):
        col = junction_color()
        if self._selected:
            painter.setBrush(QBrush(QColor(col)))
            pen = QPen(QColor("#ffffff"), 1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
        else:
            painter.setBrush(QBrush(QColor(col)))
            pen = QPen(QColor(col), 1.0)
            pen.setCosmetic(True)
            painter.setPen(pen)
        painter.drawEllipse(QPointF(0, 0), 40, 40)
