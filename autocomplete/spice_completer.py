#!/usr/bin/env python3
"""
Автодополнение SPICE-кода для SpiceEDA

Контекстное автодополнение:
- После '.' — директивы (.TRAN, .DC, .AC...)
- После букв компонента (R, C, L, V...) — предложения значений
- В позициях узлов — имена узлов из текущего файла
- Ключевые слова (SIN, PULSE, EXP...)
- Модели (NPN, NMOS, PMOS...)

Активация:
- Ctrl+Space — вручную
- Автоматически при вводе '.' или префикса компонента

Шаблоны директив с навигацией по параметрам (Tab/Shift+Tab)
"""

import re
from PySide6.QtWidgets import QListWidget, QListWidgetItem
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor


# ─── Словари SPICE ─────────────────────────────────────────────────

SPICE_DIRECTIVES = [
    ".TRAN", ".DC", ".AC", ".OP", ".TF", ".NOISE",
    ".DISTO", ".SENS", ".PRINT",
    ".MODEL", ".SUBCKT", ".ENDS", ".LIB", ".INC",
    ".PARAM", ".FUNC", ".STEP", ".OPTIONS", ".TEMP",
    ".MC", ".WAVE", ".FOUR", ".NET", ".SAVE",
    ".MEAS", ".MEASURE", ".END",
]

# Шаблоны: список кортежей (директива, [параметры])
# Каждый параметр: (имя_плейсхолдера, обязательный)
DIRECTIVE_SCHEMAS = {
    ".TRAN": [
        ("Tstep", True), ("Tstop", True), ("Tstart", False), ("Tmax", False)
    ],
    ".DC": [
        ("src", True), ("start", True), ("stop", True), ("step", False)
    ],
    ".AC": [
        ("type", True), ("points", True), ("fstart", True), ("fstop", True)
    ],
    ".OP": [],
    ".TF": [
        ("output", True), ("source", True)
    ],
    ".NOISE": [
        ("output", True), ("src", True), ("points", True)
    ],
    ".PRINT": [
        ("analysis", True), ("var1", True), ("var2...", False)
    ],
    ".MODEL": [
        ("name", True), ("type", True), ("params", False)
    ],
    ".SUBCKT": [
        ("name", True), ("node1", True), ("node2...", False)
    ],
    ".PARAM": [
        ("name=value", True)
    ],
    ".STEP": [
        ("param", True), ("start", True), ("stop", True), ("step", True)
    ],
    ".END": [],
}

COMPONENT_PREFIXES = set("RVCLXDIJMEGQFTBHKOWY")

COMPONENT_TYPES = {
    "R": "Резистор", "C": "Конденсатор", "L": "Индуктивность",
    "V": "Источник напряжения", "I": "Источник тока", "D": "Диод",
    "Q": "BJT транзистор", "M": "MOSFET транзистор",
}

KEYWORDS = [
    "SIN", "PULSE", "EXP", "PWL", "SFFM",
    "LIN", "OCT", "DEC", "IC", "VALUE", "TABLE", "POLY",
]

MODEL_TYPES = ["NPN", "PNP", "NJF", "PJF", "NMOS", "PMOS", "D", "SW"]


# ─── CompletionPopup ───────────────────────────────────────────────

class CompletionPopup(QListWidget):
    """Всплывающее окно автодополнения"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAlternatingRowColors(True)
        self.setUniformItemSizes(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.setFixedHeight(200)
        self.setMinimumWidth(250)
        self._callback = None

    def set_completion_callback(self, cb):
        self._callback = cb

    def show_completions(self, items, prefix, position):
        self.clear()
        search = prefix.lstrip(".").upper()
        filtered = [i for i in items if i.lstrip(".").upper().startswith(search)]
        if not filtered:
            self.hide()
            return

        for item in sorted(filtered):
            it = QListWidgetItem(item)
            if item in DIRECTIVE_SCHEMAS:
                params = ", ".join(p[0] for p in DIRECTIVE_SCHEMAS[item])
                it.setToolTip(f"Шаблон: {item} {params}" if params else f"Шаблон: {item}")
            elif item in COMPONENT_TYPES:
                it.setToolTip(COMPONENT_TYPES[item])
            self.addItem(it)

        self.setCurrentRow(0)
        self.move(position)
        w = max(len(i) for i in filtered) * 8 + 20
        self.setMinimumWidth(max(250, min(w, 500)))
        self.show()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            self._select()
            event.accept()
        elif event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
        elif event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            super().keyPressEvent(event)
        else:
            ch = event.text()
            if ch and ch.isprintable():
                self.hide()
                if self._callback:
                    self._callback("KEY", ch)
            else:
                self.hide()
                super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self._select()
        event.accept()

    def _select(self):
        it = self.currentItem()
        if it and self._callback:
            self._callback("SEL", it.text())
        self.hide()


# ─── SpiceCompleter ────────────────────────────────────────────────

class SpiceCompleter:
    """Контекстный автодополнитель SPICE с навигацией по параметрам."""

    def __init__(self, editor):
        self.editor = editor
        self.popup = CompletionPopup(editor)
        self._auto_enabled = True

        # Состояние навигации по шаблону
        self._active = False       # True — пользователь редактирует параметр шаблона
        self._params = []          # [(start_pos, end_pos), ...] абсолютные позиции в документе
        self._idx = 0              # текущий параметр
        self._block_pos = 0        # позиция блока при вставке шаблона

        self.popup.set_completion_callback(self._on_popup)

    # ── Публичные ──

    def enable_auto_completion(self, on: bool):
        self._auto_enabled = on

    def is_auto_enabled(self) -> bool:
        return self._auto_enabled

    def trigger_completion(self):
        cur = self.editor.textCursor()
        blk_pos = cur.block().position()

        # Если были в шаблоне но перешли на другую строку — сбросить
        if self._active and blk_pos != self._block_pos:
            self._deactivate()

        if self._active:
            return

        blk = cur.block()
        txt = blk.text()
        pip = cur.positionInBlock()
        prefix = self._token_before(txt, pip)
        ctx = self._context(txt, pip, prefix)
        if ctx is None:
            self.popup.hide()
            return
        cands = self._candidates(ctx)
        if not cands:
            self.popup.hide()
            return
        r = self.editor.cursorRect()
        self.popup.show_completions(cands, prefix, self.editor.mapToGlobal(r.bottomLeft()))

    def tab_next(self):
        if not self._active:
            return False
        # Пересчитать позиции перед переходом
        cur = self.editor.textCursor()
        self._recalc_from_text(cur.block().position())
        self._idx += 1
        if self._idx >= len(self._params):
            self._deactivate()
            return False
        self._select_param()
        return True

    def tab_prev(self):
        if not self._active:
            return False
        # Пересчитать позиции перед переходом
        cur = self.editor.textCursor()
        self._recalc_from_text(cur.block().position())
        self._idx = max(0, self._idx - 1)
        self._select_param()
        return self._idx > 0

    def is_in_template(self) -> bool:
        return self._active

    def on_text_changed(self):
        if self._active:
            self._update_current_param()
            self._check_cursor_in_template()
            return
        if not self._auto_enabled:
            return
        QTimer.singleShot(300, self.trigger_completion)

    def _check_cursor_in_template(self):
        """Проверить, что курсор всё ещё на строке шаблона. Иначе — сбросить."""
        if not self._active:
            return
        cur = self.editor.textCursor()
        if cur.block().position() != self._block_pos:
            self._deactivate()

    def on_cursor_position_changed(self):
        """Курсор перемещён — проверить, не ушёл ли пользователь из шаблона"""
        if self._active:
            cur = self.editor.textCursor()
            if cur.block().position() != self._block_pos:
                self._deactivate()

    # ── Внутренние ──

    def _token_before(self, text: str, pos: int) -> str:
        if pos > len(text):
            pos = len(text)
        s = pos
        while s > 0 and not text[s - 1].isspace():
            s -= 1
        return text[s:pos]

    def _context(self, text: str, pos: int, prefix: str):
        stripped = text.lstrip()
        if stripped.startswith("*") or stripped.startswith(";"):
            return None
        if prefix == ".":
            before = text[:pos - 1].rstrip()
            if not before or before[-1:].isspace():
                return "directive"
        if prefix.startswith("."):
            return "directive"
        if prefix and prefix[0].upper() in COMPONENT_PREFIXES:
            return "component"
        if self._in_directive(text, pos):
            return "keyword"
        if self._node_pos(text, pos):
            return "node"
        return None

    def _in_directive(self, text, pos):
        for d in SPICE_DIRECTIVES:
            i = text.upper().find(d)
            if i != -1 and i < pos:
                return True
        return False

    def _node_pos(self, text, pos):
        parts = text.split()
        if not parts or parts[0][0:1].upper() not in COMPONENT_PREFIXES:
            return False
        p = 0
        for i, w in enumerate(parts):
            s = text.find(w, p)
            if s == -1:
                continue
            e = s + len(w)
            if s <= pos <= e:
                return i >= 1
            p = e
        return False

    def _candidates(self, ctx):
        if ctx == "directive":
            return SPICE_DIRECTIVES
        if ctx == "component":
            return list(COMPONENT_PREFIXES)
        if ctx == "keyword":
            return KEYWORDS
        if ctx == "node":
            return self._nodes()
        return []

    def _nodes(self):
        nodes = set()
        for ln in self.editor.toPlainText().split("\n"):
            s = ln.strip()
            if not s or s[0] in ".*;":
                continue
            p = s.split()
            if len(p) >= 3:
                nodes.add(p[1])
                nodes.add(p[2])
        return sorted(nodes)

    def _on_popup(self, action, data):
        if action == "SEL":
            self._apply(data)
        elif action == "KEY":
            self.editor.insertPlainText(data)
            QTimer.singleShot(50, lambda: self.trigger_completion())

    def _apply(self, selected: str):
        cur = self.editor.textCursor()
        blk = cur.block()
        txt = blk.text()
        pip = cur.positionInBlock()
        ts = pip
        while ts > 0 and not txt[ts - 1].isspace():
            ts -= 1
        tok = txt[ts:pip]

        if selected in DIRECTIVE_SCHEMAS:
            self._insert_schema(cur, blk.position() + ts, selected)
        else:
            cur.setPosition(cur.position() - len(tok))
            cur.setPosition(cur.position(), QTextCursor.MoveMode.KeepAnchor)
            cur.insertText(selected + " ")
        self.editor.setFocus()

    def _insert_schema(self, cursor: QTextCursor, doc_start: int, directive: str):
        """
        Вставить шаблон директивы с параметрами.
        Для каждого параметра вставить имя и запомнить его позицию.
        """
        schema = DIRECTIVE_SCHEMAS.get(directive, [])

        # Удалить текущий токен
        cur = self.editor.textCursor()
        blk = cur.block()
        txt = blk.text()
        pip = cur.positionInBlock()
        ts = pip
        while ts > 0 and not txt[ts - 1].isspace():
            ts -= 1
        doc_tok_start = blk.position() + ts
        cur.setPosition(doc_tok_start)
        cur.setPosition(doc_tok_start + len(txt[ts:pip]), QTextCursor.MoveMode.KeepAnchor)
        cur.removeSelectedText()

        # Сформировать строку шаблона
        param_names = [p[0] for p in schema]
        template_str = directive
        if param_names:
            template_str += " " + " ".join(param_names)
        template_str += " "

        # Получить новую позицию после вставки
        cur = self.editor.textCursor()
        insert_pos = cur.position()
        cur.insertText(template_str)

        # Вычислить позиции каждого параметра
        self._params = []
        offset = insert_pos + len(directive) + 1  # после директивы + пробел
        for name in param_names:
            self._params.append((offset, offset + len(name)))
            offset += len(name) + 1  # + пробел

        self._block_pos = blk.position()
        self._idx = 0
        self._active = True
        self._select_param()

    def _select_param(self):
        if not self._params or self._idx >= len(self._params):
            self._deactivate()
            return
        s, e = self._params[self._idx]
        cur = self.editor.textCursor()
        cur.setPosition(s)
        cur.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(cur)

    def _update_current_param(self):
        """Обновить границы параметров при вводе текста внутри параметра.
        Не пересчитываем — просто даём пользователю вводить. Пересчёт при Tab."""
        pass

    def _recalc_from_text(self, _blk_pos: int):
        """Пересчитать позиции параметров из текста строки"""
        if not self._active:
            return
        cur = self.editor.textCursor()
        blk = cur.block()
        if not blk.isValid():
            self._deactivate()
            return
        text = blk.text()
        parts = text.split()
        if len(parts) < 2:
            self._deactivate()
            return

        doc_start = blk.position()
        directive = parts[0]
        if directive not in DIRECTIVE_SCHEMAS:
            self._deactivate()
            return

        expected = [p[0] for p in DIRECTIVE_SCHEMAS[directive]]
        actual = parts[1:]

        self._params = []
        offset = len(directive) + 1  # позиция после директивы + пробел
        for i, name in enumerate(expected):
            if i < len(actual):
                word = actual[i]
                # Найти слово в строке начиная с offset
                idx = text.find(word, offset)
                if idx == -1:
                    break
                start = doc_start + idx
                end = start + len(word)
                self._params.append((start, end))
                offset = idx + len(word) + 1  # после слова + пробел
            else:
                break

        if self._idx >= len(self._params):
            self._deactivate()

    def _deactivate(self):
        self._active = False
        self._params = []
        self._idx = 0
