from PySide6.QtWidgets import QGraphicsItem, QInputDialog
from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics, QPainterPath


class NetLabelItem(QGraphicsItem):
    """Метка узла на схеме — зелёная точка с выноской и текстом."""

    ANCHOR_RADIUS = 30
    FONT_SIZE = 80
    FONT_FAMILY = "monospace"

    def __init__(self, text: str, anchor_pos: QPointF, label_offset: QPointF | None = None):
        super().__init__()
        self._text = text
        self._selected = False
        self._label_offset = label_offset if label_offset is not None else QPointF(250, 250)
        self._drag_label = False
        self._drag_offset = QPointF(0, 0)
        self.setPos(anchor_pos)
        self.setZValue(60)

    def set_selected(self, val: bool):
        self._selected = val
        self.update()

    def set_text(self, text: str):
        self._text = text
        self.prepareGeometryChange()
        self.update()

    def text(self) -> str:
        return self._text

    def anchor_pos(self) -> QPointF:
        return self.pos()

    def set_label_offset(self, offset: QPointF):
        self._label_offset = offset
        self.prepareGeometryChange()
        self.update()

    def label_offset(self) -> QPointF:
        return self._label_offset

    def rotate(self, angle_delta: float):
        pass

    def _text_rect(self) -> QRectF:
        fm = QFontMetrics(QFont(self.FONT_FAMILY, self.FONT_SIZE))
        r = fm.boundingRect(self._text)
        return QRectF(r.x(), 0, r.width(), r.height())

    def boundingRect(self) -> QRectF:
        tr = self._text_rect().translated(self._label_offset)
        ax, ay = 0, 0
        min_x = min(ax - self.ANCHOR_RADIUS, tr.x())
        min_y = min(ay - self.ANCHOR_RADIUS, tr.y())
        max_x = max(ax + self.ANCHOR_RADIUS, tr.x() + tr.width())
        max_y = max(ay + self.ANCHOR_RADIUS, tr.y() + tr.height())
        margin = 20
        return QRectF(min_x - margin, min_y - margin,
                      max_x - min_x + 2 * margin,
                      max_y - min_y + 2 * margin)

    def shape(self) -> QPainterPath:
        path = QPainterPath()
        path.addEllipse(QPointF(0, 0), self.ANCHOR_RADIUS + 10, self.ANCHOR_RADIUS + 10)
        tr = self._text_rect().translated(self._label_offset)
        path.addRect(tr.adjusted(-10, -10, 10, 10))
        return path

    def paint(self, painter: QPainter, option, widget=None):
        # Выноска
        pen = QPen(QColor("#00ff88"), 0.0)
        painter.setPen(pen)
        painter.drawLine(QPointF(0, 0), self._label_offset)

        # Текст
        color = QColor("#ffcc00") if self._selected else QColor("#00ff88")
        painter.setPen(QPen(color, 0.0))
        font = painter.font()
        font.setFamily(self.FONT_FAMILY)
        font.setPointSize(self.FONT_SIZE)
        painter.setFont(font)
        painter.save()
        painter.scale(1, -1)
        painter.drawText(QPointF(self._label_offset.x(), -self._label_offset.y()), self._text)
        painter.restore()

        # Точка привязки
        painter.setBrush(QColor("#00ff88") if self._selected else QColor("#00cc66"))
        painter.setPen(QPen(QColor("#00ff88"), 0.0))
        painter.drawEllipse(QPointF(0, 0), self.ANCHOR_RADIUS, self.ANCHOR_RADIUS)
