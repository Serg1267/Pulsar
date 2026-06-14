from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics

from EDA.app.items.colors import is_light_theme


class LabelItem(QGraphicsItem):
    """Текстовая метка возле компонента (refdes, value или другой текст из .sym)."""

    def __init__(self, text: str, rel_x: float, rel_y: float, parent=None,
                 label_type: str = "text"):
        super().__init__(parent)
        self._text = text
        self._label_type = label_type
        self._selected = False
        self._cached_rect: QRectF | None = None
        self.setPos(rel_x, rel_y)

    # ---- публичный интерфейс (как у ComponentGraphicsItem) ----

    def set_selected(self, val: bool):
        self._selected = val
        self.update()

    def rotate(self, angle_delta: float):
        self.setRotation(self.rotation() + angle_delta)

    def set_text(self, text: str):
        self._text = text
        self._cached_rect = None
        self.prepareGeometryChange()
        self.update()

    def text(self) -> str:
        return self._text

    def label_type(self) -> str:
        return self._label_type

    # ---- QGraphicsItem ----

    def boundingRect(self) -> QRectF:
        if self._cached_rect is None:
            fm = QFontMetrics(QFont("monospace", 80))
            r = fm.boundingRect(self._text)
            self._cached_rect = QRectF(r.x(), 0, r.width(), r.height())
        return self._cached_rect

    def paint(self, painter: QPainter, option, widget=None):
        if self._selected:
            color = QColor("#ffcc00")
        else:
            color = QColor("#000000") if is_light_theme() else QColor("#ffffff")
        painter.setPen(QPen(color, 0.0))
        font = painter.font()
        font.setFamily("monospace")
        font.setPointSize(80)
        painter.setFont(font)
        painter.save()
        painter.scale(1, -1)
        painter.drawText(QPointF(0, 0), self._text)
        painter.restore()
