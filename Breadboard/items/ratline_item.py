"""Rubber-band ratline between component pins."""

from PySide6.QtWidgets import QGraphicsPathItem
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainterPath, QPen, QColor


RATLINE_COLOR = QColor("#e87a20")
RATLINE_PEN = QPen(RATLINE_COLOR, 0.5)


class RatlineItem(QGraphicsPathItem):
    """Dashed line showing a connection between two pins."""

    def __init__(self, net_name: str, points: list[QPointF]):
        super().__init__()
        self._net_name = net_name

        path = QPainterPath()
        if points:
            path.moveTo(points[0])
            for p in points[1:]:
                path.lineTo(p)

        self.setPath(path)
        self.setPen(RATLINE_PEN)
        self.setZValue(-1)  # behind components
