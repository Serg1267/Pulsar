"""
QPlainTextEdit с подсветкой фона текущей строки
"""

from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QTextCharFormat, QKeyEvent


class LineHighlightPlainTextEdit(QPlainTextEdit):
    """QPlainTextEdit с подсветкой текущей строки по всей ширине viewport.

    Подсветка рисуется ДО текста, чтобы текст оставался читаемым.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._line_bg_color = QColor("#2d2d2d")
        self._line_bg_enabled = True
        self._completer = None  # Ссылка на SpiceCompleter
        self.cursorPositionChanged.connect(self.viewport().update)

    def set_line_highlight_color(self, color: QColor):
        """Установить цвет подсветки текущей строки"""
        self._line_bg_color = color
        self.viewport().update()

    def set_line_highlight_enabled(self, enabled: bool):
        """Включить/отключить подсветку текущей строки"""
        self._line_bg_enabled = enabled
        self.viewport().update()

    def set_completer(self, completer):
        """Установить ссылку на SpiceCompleter для навигации по параметрам"""
        self._completer = completer

    def keyPressEvent(self, event: QKeyEvent):
        """Перехват Tab/Shift+Tab для навигации по параметрам шаблона"""
        if self._completer and self._completer.is_in_template():
            if event.key() == Qt.Key.Key_Tab:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self._completer.tab_prev()
                else:
                    self._completer.tab_next()
                event.accept()
                return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        """Отрисовка: сначала подсветка, потом текст поверх"""
        # Рисуем подсветку текущей строки
        if self._line_bg_enabled:
            cursor = self.textCursor()
            cursor.movePosition(cursor.MoveOperation.StartOfBlock)
            cursor.movePosition(cursor.MoveOperation.EndOfBlock,
                                cursor.MoveMode.KeepAnchor)
            rect = self.cursorRect(cursor)
            if rect.isValid():
                painter = QPainter(self.viewport())
                painter.fillRect(0, rect.y(),
                                 self.viewport().width(), rect.height(),
                                 self._line_bg_color)
                painter.end()

        # Рисуем текст поверх подсветки
        super().paintEvent(event)
