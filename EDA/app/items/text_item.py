from __future__ import annotations

from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QFontMetrics


_TEXT_COLOR = "#00ff88"
_TEXT_SEL_COLOR = "#ffcc00"


class TextItem(QGraphicsItem):
    """Текстовый элемент на схеме (многострочный)."""

    def __init__(self, text: str, x: float, y: float,
                 font_family: str = "monospace", font_size: int = 80,
                 parent=None):
        super().__init__(parent)
        self._text = text
        self._font_family = font_family
        self._font_size = font_size
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

    def font_family(self) -> str:
        return self._font_family

    def font_size(self) -> int:
        return self._font_size

    def set_font(self, family: str, size: int):
        self._font_family = family
        self._font_size = size
        self._cached_rect = None
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:
        if self._cached_rect is None:
            font = QFont(self._font_family, self._font_size)
            fm = QFontMetrics(font)
            lines = self._text.split('\n')
            max_width = max(fm.horizontalAdvance(line) for line in lines) if lines else 0
            total_height = fm.height() * len(lines)
            self._cached_rect = QRectF(0, 0, max_width, total_height)
        return self._cached_rect

    def paint(self, painter: QPainter, option, widget=None):
        color = QColor(_TEXT_SEL_COLOR) if self._selected else QColor(_TEXT_COLOR)
        font = QFont(self._font_family, self._font_size)
        painter.setPen(QPen(color, 0.0))
        painter.setFont(font)
        fm = QFontMetrics(font)
        lines = self._text.split('\n')
        total_height = fm.height() * len(lines)
        painter.save()
        painter.scale(1, -1)
        y_offset = -fm.ascent()
        for line in lines:
            painter.drawText(QPointF(0, y_offset), line)
            y_offset -= fm.height()
        painter.restore()
