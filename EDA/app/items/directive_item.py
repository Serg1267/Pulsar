from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics


class DirectiveItem(QGraphicsItem):
    """Текст SPICE-директивы на схеме (аналог LTspice .op .tran .model)."""

    def __init__(self, text: str, x: float, y: float, parent=None):
        super().__init__(parent)
        self._text = text
        self._selected = False
        self._cached_rect: QRectF | None = None
        self.setPos(x, y)
        self.setZValue(50)

    def set_selected(self, val: bool):
        self._selected = val
        self.update()

    def set_text(self, text: str):
        self._text = text
        self._cached_rect = None
        self.prepareGeometryChange()
        self.update()

    def text(self) -> str:
        return self._text

    def boundingRect(self) -> QRectF:
        if self._cached_rect is None:
            fm = QFontMetrics(QFont("monospace", 80))
            r = fm.boundingRect(self._text)
            self._cached_rect = QRectF(r.x(), 0, r.width(), r.height())
        return self._cached_rect

    def paint(self, painter: QPainter, option, widget=None):
        color = QColor("#ffcc00") if self._selected else QColor("#00ff88")
        painter.setPen(QPen(color, 0.0))
        font = painter.font()
        font.setFamily("monospace")
        font.setPointSize(80)
        painter.setFont(font)
        painter.save()
        painter.scale(1, -1)
        painter.drawText(QPointF(0, 0), self._text)
        painter.restore()
