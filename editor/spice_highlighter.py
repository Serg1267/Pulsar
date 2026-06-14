#!/usr/bin/env python3
"""
Подсветка синтаксиса SPICE-кода для QTextEdit
"""

from PySide6.QtGui import QSyntaxHighlighter, QTextCharFormat, QColor, QFont
from PySide6.QtCore import QRegularExpression


# ─── Цветовые схемы ────────────────────────────────────────────────
COLOR_SCHEMES = {
    "Dark (VS Code)": {
        "bg": "#1e1e1e",
        "text": "#d4d4d4",
        "comment": QColor("#6A9955"),
        "directive": QColor("#569CD6"),
        "keyword": QColor("#C586C0"),
        "component": QColor("#4EC9B0"),
        "number": QColor("#B5CEA8"),
        "node": QColor("#9CDCFE"),
        "string": QColor("#CE9178"),
        "error": QColor("#F44747"),
        "value": QColor("#D7BA7D"),
    },
    "Light": {
        "bg": "#ffffff",
        "text": "#1e1e1e",
        "comment": QColor("#008000"),
        "directive": QColor("#0000FF"),
        "keyword": QColor("#800080"),
        "component": QColor("#006400"),
        "number": QColor("#800000"),
        "node": QColor("#000080"),
        "string": QColor("#A31515"),
        "error": QColor("#FF0000"),
        "value": QColor("#804000"),
    },
    "Monokai": {
        "bg": "#272822",
        "text": "#f8f8f2",
        "comment": QColor("#75715e"),
        "directive": QColor("#66d9ef"),
        "keyword": QColor("#f92672"),
        "component": QColor("#a6e22e"),
        "number": QColor("#ae81ff"),
        "node": QColor("#f8f8f2"),
        "string": QColor("#e6db74"),
        "error": QColor("#f92672"),
        "value": QColor("#fd971f"),
    },
    "Solarized Dark": {
        "bg": "#002b36",
        "text": "#839496",
        "comment": QColor("#586e75"),
        "directive": QColor("#268bd2"),
        "keyword": QColor("#859900"),
        "component": QColor("#2aa198"),
        "number": QColor("#cb4b16"),
        "node": QColor("#b58900"),
        "string": QColor("#dc322f"),
        "error": QColor("#dc322f"),
        "value": QColor("#d33682"),
    },
}

# Схема по умолчанию
DEFAULT_SCHEME = "Dark (VS Code)"


class SpiceHighlighter(QSyntaxHighlighter):
    """Подсветка синтаксиса SPICE-нетлиста"""

    # SPICE-директивы (начинаются с точки)
    DIRECTIVES = [
        r"\.TRAN", r"\.DC", r"\.AC", r"\.OP", r"\.TF", r"\.NOISE",
        r"\.DISTO", r"\.SENS", r"\.PRINT", r"\.PLOT", r"\.PROBE",
        r"\.MODEL", r"\.SUBCKT", r"\.ENDS", r"\.LIB", r"\.INC",
        r"\.PARAM", r"\.FUNC", r"\.STEP", r"\.OPTIONS", r"\.TEMP",
        r"\.MC", r"\.WAVE", r"\.FOUR", r"\.NET", r"\.SAVE",
        r"\.MEAS", r"\.MEASURE",
    ]

    # Ключевые слова в директивах
    KEYWORDS = [
        r"\bLIN\b", r"\bOCT\b", r"\bDEC\b", r"\bSIN\b", r"\bPULSE\b",
        r"\bEXP\b", r"\bPWL\b", r"\bSFFM\b", r"\bAM\b", r"\bFM\b",
        r"\bIC\b", r"\bRSH\b", r"\bRSW\b", r"\bOFF\b",
    ]

    # Компоненты (первая буква — тип)
    COMPONENT_PREFIXES = "RVCLXDIJMEGQFTBHKOWY"

    def __init__(self, parent=None, scheme_name=None):
        super().__init__(parent)
        self._scheme_name = scheme_name or DEFAULT_SCHEME
        self._apply_scheme(self._scheme_name)

    # ── Управление схемами ──────────────────────────────────────────

    @property
    def scheme_name(self):
        return self._scheme_name

    @classmethod
    def available_schemes(cls):
        return list(COLOR_SCHEMES.keys())

    def set_scheme(self, name: str):
        """Применить цветовую схему и перерисовать документ"""
        if name not in COLOR_SCHEMES:
            return
        self._scheme_name = name
        self._apply_scheme(name)
        self.rehighlight()

    def _apply_scheme(self, name: str):
        """Создать QTextCharFormat на основе выбранной схемы"""
        c = COLOR_SCHEMES[name]

        self.comment_format = QTextCharFormat()
        self.comment_format.setForeground(c["comment"])
        self.comment_format.setFontItalic(True)

        self.directive_format = QTextCharFormat()
        self.directive_format.setForeground(c["directive"])
        self.directive_format.setFontWeight(QFont.Bold)

        self.keyword_format = QTextCharFormat()
        self.keyword_format.setForeground(c["keyword"])
        self.keyword_format.setFontWeight(QFont.Bold)

        self.component_format = QTextCharFormat()
        self.component_format.setForeground(c["component"])
        self.component_format.setFontWeight(QFont.Bold)

        self.number_format = QTextCharFormat()
        self.number_format.setForeground(c["number"])

        self.node_format = QTextCharFormat()
        self.node_format.setForeground(c["node"])

        self.value_format = QTextCharFormat()
        self.value_format.setForeground(c["value"])

        self.bg_color = c.get("bg", "#1e1e1e")
        self.text_color = c.get("text", "#d4d4d4")

        # Правила подсветки (regexp, format)
        self.rules = [
            # Директивы
            (QRegularExpression("|".join(self.DIRECTIVES), QRegularExpression.PatternOption.CaseInsensitiveOption), self.directive_format),
            # Ключевые слова
            (QRegularExpression("|".join(self.KEYWORDS), QRegularExpression.PatternOption.CaseInsensitiveOption), self.keyword_format),
            # Числа (включая научную нотацию: 1.5k, 100u, 4.7Meg, 1e-6)
            (QRegularExpression(r"\b\d+\.?\d*\s*[a-zA-Z]*\b"), self.number_format),
        ]

    # ── Подсветка ───────────────────────────────────────────────────

    def highlightBlock(self, text):
        """Подсветка одной строки текста"""
        stripped = text.strip()

        # Комментарии: строки начинающиеся с *
        if stripped.startswith("*"):
            self.setFormat(0, len(text), self.comment_format)
            return

        # Комментарии: строки начинающиеся с ;
        if stripped.startswith(";"):
            self.setFormat(0, len(text), self.comment_format)
            return

        # Подстрочные комментарии (начинающиеся с $ до конца строки)
        dollar_pos = text.find("$")
        if dollar_pos != -1:
            # Сначала подсветим основную часть, потом комментарий
            comment_len = len(text) - dollar_pos
            self.setFormat(dollar_pos, comment_len, self.comment_format)
            # Дальше работаем только с частью до $
            text = text[:dollar_pos]

        # Применение правил подсветки
        for pattern, fmt in self.rules:
            match_iterator = pattern.globalMatch(text)
            while match_iterator.hasNext():
                match = match_iterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

        # Подсветка компонентов: R1, C10, Vcc, L2, Q3...
        self._highlight_components(text)

        # Подсветка узлов: числа после компонентов или между пробелами
        self._highlight_nodes(text)

    def _highlight_components(self, text):
        """Подсветка именований компонентов (R1, C1, V1, L2, Q3...)"""
        import re
        for match in re.finditer(r'\b([A-Za-z])(\d+)\b', text):
            prefix = match.group(1).upper()
            if prefix in self.COMPONENT_PREFIXES:
                start = match.start()
                length = match.end() - start
                self.setFormat(start, length, self.component_format)

    def _highlight_nodes(self, text):
        """Подсветка номеров узлов в SPICE-нетлисте"""
        import re
        # Номера узлов обычно идут после названия компонента
        # Формат: Name node1 node2 ...
        # Пропускаем строки-директивы
        stripped = text.strip()
        if stripped.startswith("."):
            return

        parts = text.split()
        if len(parts) >= 3:
            # Первое — имя компонента, остальные — узлы и значения
            # Ищем числа/имена узлов после имени компонента
            idx = 0
            # Пропускаем имя компонента
            first_word = parts[0]
            idx = text.find(first_word) + len(first_word)

            # Подсвечиваем узлы (первые 1-2 токена после имени — это узлы)
            node_count = 0
            for part in parts[1:]:
                # Узел — это число или имя, не начинающееся с цифры (для именованных узлов)
                if re.match(r'^[A-Za-z_]\w*$', part) or re.match(r'^\d+$', part):
                    start = text.find(part, idx)
                    if start != -1:
                        self.setFormat(start, len(part), self.node_format)
                        idx = start + len(part)
                        node_count += 1
                        if node_count >= 2:
                            break
                else:
                    break
