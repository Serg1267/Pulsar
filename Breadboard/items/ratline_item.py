"""Rubber-band ratline between component pins — selectable, deletable, with endpoint drag."""

from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QPainterPath, QPolygonF, QPainter, QPen, QBrush, QColor,
)

from Breadboard.board import Board

RATLINE_COLOR = QColor("#e87a20")
RATLINE_PEN = QPen(RATLINE_COLOR, 0.5)
SELECTED_PEN = QPen(QColor("#ffff00"), 0.8)
ENDPOINT_RADIUS = 0.5  # mm
HOVER_TOLERANCE = 1.0  # mm


class RatlineItem(QGraphicsItem):
    """Dashed line showing a connection between two pins — interactive."""

    def __init__(self, net_name: str, points: list[QPointF],
                 board: Board | None = None):
        super().__init__()
        self._net_name = net_name
        self._points = list(points)
        self._board = board

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(-1)
        self._hovered = False

    # ── Public ────────────────────────────────────────────────

    def net_name(self) -> str:
        return self._net_name

    def points(self) -> list[QPointF]:
        return list(self._points)

    # ── Geometry ──────────────────────────────────────────────

    def _rebuild(self):
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:
        if not self._points:
            return QRectF()
        xs = [p.x() for p in self._points]
        ys = [p.y() for p in self._points]
        pad = ENDPOINT_RADIUS + HOVER_TOLERANCE
        return QRectF(min(xs) - pad, min(ys) - pad,
                      max(xs) - min(xs) + 2 * pad,
                      max(ys) - min(ys) + 2 * pad)

    def shape(self) -> QPainterPath:
        body = QPainterPath()
        if len(self._points) < 2:
            return body
        # Segment hit-boxes: thin rectangles between each pair of points
        hw = HOVER_TOLERANCE
        for i in range(len(self._points) - 1):
            a = self._points[i]
            b = self._points[i + 1]
            dx = b.x() - a.x()
            dy = b.y() - a.y()
            length = (dx * dx + dy * dy) ** 0.5
            if length < 1e-6:
                continue
            # Unit perpendicular
            nx = -dy / length * hw
            ny = dx / length * hw
            poly = QPolygonF([
                QPointF(a.x() + nx, a.y() + ny),
                QPointF(a.x() - nx, a.y() - ny),
                QPointF(b.x() - nx, b.y() - ny),
                QPointF(b.x() + nx, b.y() + ny),
            ])
            body.addPolygon(poly)
        # Endpoint circles
        for pt in self._points:
            body.addEllipse(pt, hw, hw)
        return body

    def paint(self, painter: QPainter, option, widget=None):
        if len(self._points) < 2:
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.isSelected():
            line_pen = SELECTED_PEN
        elif self._hovered:
            line_pen = QPen(QColor("#ffb347"), 0.6)
        else:
            line_pen = RATLINE_PEN
        painter.setPen(line_pen)
        body = QPainterPath()
        body.moveTo(self._points[0])
        for p in self._points[1:]:
            body.lineTo(p)
        painter.drawPath(body)

        # Endpoint dots
        dot_color = QColor("#ffff00") if self.isSelected() else (QColor("#ffb347") if self._hovered else RATLINE_COLOR)
        dot_pen = QPen(dot_color, 0.2)
        dot_brush = QBrush(dot_color)
        for pt in self._points:
            painter.setPen(dot_pen)
            painter.setBrush(dot_brush)
            painter.drawEllipse(pt, ENDPOINT_RADIUS, ENDPOINT_RADIUS)

    def hoverEnterEvent(self, event):
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)
