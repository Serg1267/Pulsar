"""
Номера строк для текстового редактора
"""

from PySide6.QtWidgets import QWidget, QPlainTextEdit
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPainter, QColor


class LineNumberArea(QWidget):
    """Панель с номерами строк, привязанная к QPlainTextEdit"""

    def __init__(self, editor: QPlainTextEdit):
        super().__init__(parent=editor)
        self._editor = editor
        # Цвета по умолчанию (Dark)
        self._bg_color = QColor("#2d2d2d")
        self._text_color = QColor("#858585")

    def set_colors(self, bg: QColor, text: QColor):
        """Установить цвета фона и текста номеров строк"""
        self._bg_color = bg
        self._text_color = text
        self.update()

    def sizeHint(self):
        return QSize(self._line_number_area_width(), 0)

    def minimumSizeHint(self):
        return QSize(self._line_number_area_width(), 0)

    def _line_number_area_width(self):
        """Вычислить ширину панели номеров строк"""
        digits = 1
        block_count = self._editor.blockCount()
        while block_count >= 10:
            block_count //= 10
            digits += 1
        # Ширина = отступ + ширина цифры × кол-во цифр + отступ справа
        space = 16 + self._editor.fontMetrics().horizontalAdvance("9") * digits
        return space

    def paintEvent(self, event):
        """Отрисовка номеров строк"""
        painter = QPainter(self)
        try:
            painter.fillRect(event.rect(), self._bg_color)

            font = self._editor.font()
            painter.setFont(font)
            painter.setPen(self._text_color)

            area_top = event.rect().top()
            area_bottom = event.rect().bottom()

            # Находим первый видимый блок
            block = self._editor.firstVisibleBlock()

            # Получаем offset документа (учитывает прокрутку и document margin)
            offset_y = self._editor.contentOffset().y()

            block_number = block.blockNumber()

            # Рисуем пока блок видимый
            while block.isValid():
                block_top = self._editor.blockBoundingGeometry(block).translated(0, offset_y).top()
                block_bottom = block_top + self._editor.blockBoundingRect(block).height()

                if block_top > area_bottom:
                    break

                if block.isVisible() and block_bottom >= area_top:
                    number = str(block_number + 1)
                    painter.drawText(
                        4,
                        int(block_top),
                        self._line_number_area_width() - 8,
                        int(self._editor.blockBoundingRect(block).height()),
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                        number,
                    )

                block = block.next()
                block_number += 1

        finally:
            painter.end()
