from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QLabel, QPushButton,
    QHBoxLayout, QMessageBox, QSplitter, QTextEdit,
)
from PySide6.QtCore import Qt, Signal, QTimer, QSettings, QFileSystemWatcher
from PySide6.QtGui import QFont, QColor, QPalette, QIcon

from EDA.app.canvas import SchematicCanvas
from EDA.core.library.library import ComponentLibrary
from EDA.app.dialogs.component_browser import ComponentBrowser
from editor.line_highlight_editor import LineHighlightPlainTextEdit
from editor.line_number_area import LineNumberArea
from editor.spice_highlighter import SpiceHighlighter, DEFAULT_SCHEME, COLOR_SCHEMES
from utils.spice_template import wrap_netlist_in_template


class _SchematicTabPage(QWidget):
    """Страница вкладки — холст + терминал ngspice."""

    def __init__(self, canvas: SchematicCanvas, filepath: str | None = None, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.filepath: str | None = filepath
        self._dirty = False
        self.page_type = 'sch'

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(canvas)

        term_container = QWidget()
        term_layout = QVBoxLayout(term_container)
        term_layout.setContentsMargins(0, 0, 0, 0)
        term_layout.setSpacing(0)

        term_header = QWidget()
        term_header.setStyleSheet("background-color: #2a2a2a; border: none;")
        header_layout = QHBoxLayout(term_header)
        header_layout.setContentsMargins(8, 2, 4, 2)
        header_label = QLabel("Терминал NGspice")
        header_label.setStyleSheet("color: #888; font-size: 10px;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; font-size: 12px; }"
            "QPushButton:hover { color: #fff; background: #c42b1c; }"
        )
        close_btn.clicked.connect(term_container.hide)
        header_layout.addWidget(close_btn)
        term_layout.addWidget(term_header)

        self.terminal = QTextEdit()
        self.terminal.setFont(QFont("Monospace", 10))
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; border-top: none; }"
        )
        self.terminal.setPlaceholderText("Вывод NGspice будет отображаться здесь…")
        term_layout.addWidget(self.terminal)

        term_container.setVisible(False)
        self._term_container = term_container
        splitter.addWidget(term_container)

        splitter.setSizes([560, 200])
        layout.addWidget(splitter)

    def is_dirty(self) -> bool:
        return self._dirty

    def set_dirty(self, val: bool):
        self._dirty = val

    def clear_terminal(self):
        self.terminal.clear()

    def tab_label(self) -> str:
        name = Path(self.filepath).name if self.filepath else "Новая схема"
        return f"* {name}" if self._dirty else name


class _CirTabPage(QWidget):
    modified = Signal()

    def __init__(self, filepath: str | None = None, parent=None):
        super().__init__(parent)
        self.filepath: str | None = filepath
        self._dirty = False
        self._terminal_visible = True
        self.page_type = 'cir'

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Vertical)

        editor_container = QWidget()
        editor_layout = QHBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)

        self.editor = LineHighlightPlainTextEdit()

        settings = QSettings("Pulsar", "Pulsar")
        saved_family = settings.value("editor/font_family", "")
        saved_size = settings.value("editor/font_size", 14, type=int)
        if saved_family:
            self.editor.setFont(QFont(saved_family, saved_size))
        else:
            self.editor.setFont(QFont("Monospace", 14))
        self.editor.setPlaceholderText("SPICE netlist (.cir). Начните вводить код…")
        self.highlighter = SpiceHighlighter(self.editor.document())

        scheme = COLOR_SCHEMES.get(DEFAULT_SCHEME, {})
        bg = QColor(scheme.get("bg", "#1e1e1e"))
        text = QColor(scheme.get("text", "#d4d4d4"))
        palette = self.editor.palette()
        palette.setColor(QPalette.ColorRole.Base, bg)
        palette.setColor(QPalette.ColorRole.Text, text)
        self.editor.setPalette(palette)
        self.editor.set_line_highlight_color(bg.lighter(165))

        self.line_numbers = LineNumberArea(self.editor)
        self.editor.updateRequest.connect(lambda *_: self.line_numbers.update())
        self.editor.blockCountChanged.connect(lambda _: self.line_numbers.updateGeometry() or self.line_numbers.update())
        QTimer.singleShot(0, self.line_numbers.update)

        line_bg = bg.lighter(115)
        line_text = QColor(text.red(), text.green(), text.blue(), 160)
        self.line_numbers.set_colors(line_bg, line_text)

        self._line_numbers_enabled = True
        self.line_numbers.setVisible(True)

        editor_layout.addWidget(self.line_numbers)
        editor_layout.addWidget(self.editor, 1)

        splitter.addWidget(editor_container)

        term_container = QWidget()
        term_layout = QVBoxLayout(term_container)
        term_layout.setContentsMargins(0, 0, 0, 0)
        term_layout.setSpacing(0)

        term_header = QWidget()
        term_header.setStyleSheet("background-color: #2a2a2a; border: none;")
        header_layout = QHBoxLayout(term_header)
        header_layout.setContentsMargins(8, 2, 4, 2)
        header_label = QLabel("Терминал NGspice")
        header_label.setStyleSheet("color: #888; font-size: 10px;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #888; border: none; font-size: 12px; }"
            "QPushButton:hover { color: #fff; background: #c42b1c; }"
        )
        close_btn.clicked.connect(term_container.hide)
        header_layout.addWidget(close_btn)
        term_layout.addWidget(term_header)

        self.terminal = QTextEdit()
        self.terminal.setFont(QFont("Monospace", 10))
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet(
            f"QTextEdit {{ background-color: {bg.name()}; color: {text.name()}; "
            "border: 1px solid #333; border-top: none; }"
        )
        self.terminal.setPlaceholderText("Вывод NGspice будет отображаться здесь…")
        term_layout.addWidget(self.terminal)

        self._term_container = term_container
        splitter.addWidget(term_container)

        splitter.setSizes([560, 200])
        layout.addWidget(splitter)

        self.editor.textChanged.connect(self._mark_dirty)

    def _mark_dirty(self):
        if not self._dirty:
            self.modified.emit()
            self._dirty = True

    def is_dirty(self) -> bool:
        return self._dirty

    def set_dirty(self, val: bool):
        self._dirty = val

    def tab_label(self) -> str:
        name = Path(self.filepath).name if self.filepath else "Новый .cir"
        return f"* {name}" if self._dirty else name

    def load_file(self, path: str):
        self.filepath = path
        self.editor.setPlainText(Path(path).read_text())
        self.set_dirty(False)

    def save(self):
        if not self.filepath:
            return
        text = self.editor.toPlainText()
        fp = Path(self.filepath)
        if fp.with_suffix('.sch').exists():
            text = wrap_netlist_in_template(text, circuit_name=fp.stem)
            self.editor.setPlainText(text)
        fp.write_text(text)
        self.set_dirty(False)

    def save_as(self, path: str):
        self.filepath = path
        self.save()

    def apply_theme(self, scheme_name: str):
        self.highlighter.set_scheme(scheme_name)
        scheme = COLOR_SCHEMES.get(scheme_name, {})
        bg = QColor(scheme.get("bg", "#1e1e1e"))
        text = QColor(scheme.get("text", "#d4d4d4"))
        palette = self.editor.palette()
        palette.setColor(QPalette.ColorRole.Base, bg)
        palette.setColor(QPalette.ColorRole.Text, text)
        self.editor.setPalette(palette)
        luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        is_dark = luminance < 128
        if is_dark:
            line_bg = bg.lighter(115)
            line_text = QColor(text.red(), text.green(), text.blue(), 160)
            hl_bg = bg.lighter(165)
        else:
            line_bg = bg.darker(105)
            line_text = QColor(100, 100, 100)
            hl_bg = bg.darker(125)
        self.line_numbers.set_colors(line_bg, line_text)
        self.editor.set_line_highlight_color(hl_bg)
        self.terminal.setStyleSheet(
            f"QTextEdit {{ background-color: {bg.name()}; color: {text.name()}; "
            "border: 1px solid #333; }"
        )

    def set_line_numbers_visible(self, visible: bool):
        self._line_numbers_enabled = visible
        self.line_numbers.setVisible(visible)

    def set_terminal_visible(self, visible: bool):
        self._terminal_visible = visible
        self._term_container.setVisible(visible)

    def set_font(self, family: str, size: int):
        self.editor.setFont(QFont(family, size))

    def clear_terminal(self):
        self.terminal.clear()

    def append_terminal(self, text: str):
        self.terminal.append(text)

    def terminal_text(self) -> str:
        return self.terminal.toPlainText()


class UnifiedTabWidget(QTabWidget):
    """Единый QTabWidget для вкладок .sch и .cir."""

    position_changed = Signal(float, float)
    mode_changed = Signal(str)
    component_placed = Signal(str)
    tabs_count_changed = Signal(int)
    cir_auto_exported = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTabsClosable(True)
        self.tabCloseRequested.connect(self._close_tab)
        self.currentChanged.connect(self._on_tab_changed)
        self.setMovable(False)
        self.setDocumentMode(True)

        self._tab_counter = 0
        self._refdes_counter: dict[str, int] = {}
        self._library = ComponentLibrary()
        self._file_watcher = QFileSystemWatcher(self)
        self._file_watcher.fileChanged.connect(self._on_file_changed_externally)

    # ── тип текущей страницы ──

    def current_page_type(self) -> str | None:
        p = self.currentWidget()
        return p.page_type if p else None

    def current_page_dirty(self) -> bool:
        p = self.currentWidget()
        return p.is_dirty() if p else False

    # ── управление вкладками .sch ──

    def new_sch_tab(self) -> SchematicCanvas:
        self._tab_counter += 1
        canvas = SchematicCanvas(self)
        page = _SchematicTabPage(canvas, parent=self)
        self._connect_canvas_signals(canvas)
        self.addTab(page, page.tab_label())
        self.setCurrentWidget(page)
        self.tabs_count_changed.emit(self.count())
        return canvas

    def open_sch_tab(self, filepath: str) -> SchematicCanvas:
        canvas = SchematicCanvas(self)
        canvas.load_sch(filepath)
        page = _SchematicTabPage(canvas, filepath=filepath, parent=self)
        self._connect_canvas_signals(canvas)
        self.addTab(page, page.tab_label())
        self.setCurrentWidget(page)
        self.tabs_count_changed.emit(self.count())
        return canvas

    def _connect_canvas_signals(self, canvas: SchematicCanvas):
        from PySide6.QtCore import QSettings
        settings = QSettings("Pulsar", "Pulsar")
        grid_dots = settings.value("grid/dots", "false").lower() == "true"
        canvas.set_grid_dots(grid_dots)
        grid_enabled = settings.value("grid/enabled", "true").lower() == "true"
        canvas.set_grid_enabled(grid_enabled)

        canvas.position_changed.connect(self.position_changed.emit)
        canvas.mode_changed.connect(self.mode_changed.emit)
        canvas.component_placed.connect(self._on_component_placed)
        canvas.component_placed.connect(self._mark_tab_dirty)
        canvas.component_placed.connect(self.component_placed.emit)
        canvas.modified.connect(self._mark_tab_dirty)
        canvas.drag_placement_started.connect(self._on_drag_placement_started)

    def _on_component_placed(self, refdes: str):
        import re
        m = re.match(r'^([A-Za-z]+)(\d+)$', refdes)
        if m:
            prefix, num_str = m.group(1), m.group(2)
            n = int(num_str)
            self._refdes_counter[prefix] = n

    def _next_available_refdes_number(self, prefix: str) -> int:
        from EDA.app.items.component_item import ComponentGraphicsItem
        import re
        used: set[int] = set()
        canvas = self.current_canvas()
        if canvas is not None:
            for item in canvas.scene().items():
                if isinstance(item, ComponentGraphicsItem):
                    refdes = item.refdes()
                    m = re.match(r'^([A-Za-z]+)(\d+)$', refdes)
                    if m and m.group(1) == prefix:
                        used.add(int(m.group(2)))
        n = 1
        while n in used:
            n += 1
        return n

    # ── управление вкладками .cir ──

    def new_cir_tab(self, content: str = "") -> LineHighlightPlainTextEdit | None:
        page = _CirTabPage(parent=self)
        page.editor.setPlainText(content)
        self._add_cir_tab(page)
        return page.editor

    def open_cir_tab(self, filepath: str):
        for i in range(self.count()):
            p = self.widget(i)
            if hasattr(p, 'page_type') and p.page_type == 'cir' and p.filepath and Path(p.filepath).resolve() == Path(filepath).resolve():
                p.load_file(filepath)
                self.setTabText(i, p.tab_label())
                self.setCurrentIndex(i)
                return
        page = _CirTabPage(filepath=filepath, parent=self)
        try:
            page.load_file(filepath)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть {filepath}:\n{e}")
            page.deleteLater()
            return
        self._add_cir_tab(page)

    def _add_cir_tab(self, page: _CirTabPage):
        page.modified.connect(self._mark_tab_dirty)
        self.addTab(page, page.tab_label())
        self.setCurrentWidget(page)
        if page.filepath:
            self._file_watcher.addPath(page.filepath)
        self.tabs_count_changed.emit(self.count())

    def _on_file_changed_externally(self, path: str):
        for i in range(self.count()):
            p = self.widget(i)
            if not hasattr(p, 'page_type') or p.page_type != 'cir':
                continue
            if p.filepath and Path(p.filepath).resolve() == Path(path).resolve():
                if p.is_dirty():
                    reply = QMessageBox.question(
                        self, "Файл изменён извне",
                        f"Файл '{Path(path).name}' был изменён другой программой.\n"
                        f"У вас есть несохранённые изменения.\n\n"
                        f"Перезагрузить файл с диска? (изменения будут потеряны)",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
            try:
                cursor = p.editor.textCursor()
                position = cursor.position()
                content = Path(path).read_text()
                if content == p.editor.toPlainText():
                    break
                p.editor.blockSignals(True)
                p.editor.setPlainText(content)
                p.editor.blockSignals(False)
                new_cursor = p.editor.textCursor()
                new_cursor.setPosition(min(position, len(content)))
                p.editor.setTextCursor(new_cursor)
                p.set_dirty(False)
                self.setTabText(i, p.tab_label())
            except Exception:
                pass
                break

    # ── открытие любого файла ──

    def open_tab(self, filepath: str):
        p = Path(filepath)
        suffix = p.suffix.lower()
        if suffix == '.sch':
            self.open_sch_tab(filepath)
        elif suffix in ('.cir', '.sp'):
            self.open_cir_tab(filepath)
        else:
            try:
                data = p.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                data = ""
            if data.strip().startswith('{') and '"version"' in data:
                self.open_sch_tab(filepath)
            else:
                self.open_cir_tab(filepath)

    # ── dirty ──

    def _mark_tab_dirty(self, *args):
        sender = self.sender()
        if hasattr(sender, 'page_type') and hasattr(sender, 'set_dirty'):
            p = sender
        else:
            p = self.currentWidget()
        if p is not None and not p.is_dirty():
            p.set_dirty(True)
            idx = self.indexOf(p)
            if idx >= 0:
                self.setTabText(idx, p.tab_label())

    # ── сохранение ──

    def save_current_tab(self) -> bool:
        p = self.currentWidget()
        if p is None:
            return False
        if p.page_type == 'sch':
            return self._save_sch(p)
        elif p.page_type == 'cir':
            return self._save_cir(p)
        return False

    def _save_sch(self, page: _SchematicTabPage) -> bool:
        if page.filepath is None:
            return self.save_current_tab_as()
        page.canvas.save_sch(page.filepath)
        page.set_dirty(False)
        sch_path = Path(page.filepath)
        cir_path = sch_path.with_suffix('.cir')
        from utils.spice_template import wrap_netlist_in_template
        raw = page.canvas.export_cir()
        if raw.strip():
            wrapped = wrap_netlist_in_template(raw, circuit_name=sch_path.stem)
            cir_path.write_text(wrapped)
        self.cir_auto_exported.emit(str(cir_path))
        self.setTabText(self.currentIndex(), page.tab_label())
        return True

    def _save_cir(self, page: _CirTabPage) -> bool:
        if page.filepath is None:
            return self.save_current_tab_as()
        page.save()
        self.setTabText(self.currentIndex(), page.tab_label())
        return True

    def save_tab_as(self, filepath: str) -> bool:
        p = self.currentWidget()
        if p is None:
            return False
        if p.page_type == 'sch':
            if not filepath.endswith('.sch'):
                filepath += '.sch'
            p.filepath = filepath
            return self._save_sch(p)
        elif p.page_type == 'cir':
            if not (filepath.endswith('.cir') or filepath.endswith('.sp')):
                filepath += '.cir'
            old = p.filepath
            if old:
                self._file_watcher.removePath(old)
            p.save_as(filepath)
            self._file_watcher.addPath(filepath)
            self.setTabText(self.currentIndex(), p.tab_label())
            return True
        return False

    def save_current_tab_as(self) -> bool:
        from PySide6.QtWidgets import QFileDialog
        p = self.currentWidget()
        if p is None:
            return False
        if p.page_type == 'sch':
            fp, _ = QFileDialog.getSaveFileName(self, "Сохранить схему как", p.filepath or "", "Schematic (*.sch)")
            if not fp:
                return False
            return self.save_tab_as(fp)
        elif p.page_type == 'cir':
            fp, _ = QFileDialog.getSaveFileName(self, "Сохранить .cir как", "", "SPICE Circuit (*.cir);;All Files (*)")
            if not fp:
                return False
            return self.save_tab_as(fp)
        return False

    # ── закрытие ──

    def confirm_close_all(self) -> bool:
        for i in range(self.count() - 1, -1, -1):
            p = self.widget(i)
            if not p or not p.is_dirty():
                continue
            ret = QMessageBox.warning(
                self, "Не сохранено",
                f"Сохранить изменения в «{p.tab_label()}»?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                return False
            if ret == QMessageBox.StandardButton.Yes:
                self.setCurrentWidget(p)
                if not self._do_tab_save(p):
                    return False
        return True

    def close_all_tabs(self):
        while self.count():
            p = self.widget(0)
            self.removeTab(0)
            if hasattr(p, 'filepath') and p.filepath and self._file_watcher.files():
                try:
                    self._file_watcher.removePath(p.filepath)
                except Exception:
                    pass
            p.deleteLater()

    def _close_tab(self, index: int):
        p = self.widget(index)
        if p and p.is_dirty():
            ret = QMessageBox.warning(
                self, "Не сохранено",
                f"Сохранить изменения в «{p.tab_label()}» перед закрытием?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            )
            if ret == QMessageBox.StandardButton.Cancel:
                return
            if ret == QMessageBox.StandardButton.Yes:
                self.setCurrentIndex(index)
                if not self._do_tab_save(p):
                    return
        old_path = p.filepath if hasattr(p, 'filepath') and p.filepath else None
        if old_path and self._file_watcher.files():
            try:
                self._file_watcher.removePath(old_path)
            except Exception:
                pass
        self.removeTab(index)
        p.deleteLater()
        self.tabs_count_changed.emit(self.count())

    def _do_tab_save(self, page) -> bool:
        if page.filepath is None:
            return self.save_current_tab_as()
        if page.page_type == 'sch':
            return self._save_sch(page)
        elif page.page_type == 'cir':
            self._save_cir(page)
            return True
        return False

    # ── доступ к текущему ──

    def current_canvas(self) -> SchematicCanvas | None:
        p = self.currentWidget()
        if p is not None and p.page_type == 'sch':
            return p.canvas
        return None

    def current_editor(self) -> LineHighlightPlainTextEdit | None:
        p = self.currentWidget()
        if p is not None and p.page_type == 'cir':
            return p.editor
        return None

    def current_highlighter(self) -> SpiceHighlighter | None:
        p = self.currentWidget()
        if p is not None and p.page_type == 'cir':
            return p.highlighter
        return None

    def current_filepath(self) -> str | None:
        p = self.currentWidget()
        return p.filepath if p else None

    def current_page(self):
        return self.currentWidget()

    def has_sch_tabs(self) -> bool:
        return any(self.widget(i).page_type == 'sch' for i in range(self.count()))

    def has_cir_tabs(self) -> bool:
        return any(self.widget(i).page_type == 'cir' for i in range(self.count()))

    # ── действия со схемой ──

    def add_component(self):
        canvas = self.current_canvas()
        if canvas is None:
            return
        dialog = ComponentBrowser(self._library, self)
        if dialog.exec() != ComponentBrowser.DialogCode.Accepted:
            return
        sym_id = dialog.selected_symbol_id()
        if not sym_id:
            return
        sym = self._library.get(sym_id)
        if not sym or not sym.sym_data:
            return
        refdes_prefix = self._refdes_prefix_from_sym(sym.sym_data) or sym.id[0].upper() or "U"
        n = self._next_available_refdes_number(refdes_prefix)
        refdes = f"{refdes_prefix}{n}"
        value = self._value_from_sym(sym.sym_data)
        canvas.start_placement(sym.sym_data, refdes=refdes, value=value)

    def _on_drag_placement_started(self, sym_id: str):
        canvas = self.current_canvas()
        if canvas is None:
            return
        sym = self._library.get(sym_id)
        if not sym or not sym.sym_data:
            return
        refdes_prefix = self._refdes_prefix_from_sym(sym.sym_data) or sym.id[0].upper() or "U"
        n = self._next_available_refdes_number(refdes_prefix)
        refdes = f"{refdes_prefix}{n}"
        value = self._value_from_sym(sym.sym_data)
        canvas.start_placement(sym.sym_data, refdes=refdes, value=value)

    def export_cir(self) -> str | None:
        canvas = self.current_canvas()
        if canvas is None:
            return None
        return canvas.export_cir()

    def export_tedax(self) -> str | None:
        from EDA.tedax.netlist_exporter import export_tedax_netlist
        canvas = self.current_canvas()
        if canvas is None:
            return None
        return export_tedax_netlist(canvas)

    # ── терминал ngspice (обеих страниц) ──

    def current_terminal(self) -> QTextEdit | None:
        p = self.currentWidget()
        if p is not None and hasattr(p, 'terminal'):
            return p.terminal
        return None

    def terminal_text(self) -> str:
        p = self.currentWidget()
        if p is not None and hasattr(p, 'terminal'):
            return p.terminal.toPlainText()
        return ""

    def clear_terminal(self):
        p = self.currentWidget()
        if p is not None and hasattr(p, 'terminal'):
            p.terminal.clear()

    def show_terminal(self):
        p = self.currentWidget()
        if p is not None and hasattr(p, '_term_container'):
            p._term_container.show()
            p.terminal.setVisible(True)

    def hide_terminal(self):
        p = self.currentWidget()
        if p is not None and hasattr(p, '_term_container'):
            p._term_container.hide()

    def open_error_log_tab(self, text: str):
        """Открыть read-only вкладку с логом ошибок симуляции."""
        page = _CirTabPage(parent=self)
        page.editor.setPlainText(text)
        page.editor.setReadOnly(True)
        page._dirty = False
        page.filepath = None
        page._term_container.setVisible(False)
        self.addTab(page, "error.log")
        self.setCurrentWidget(page)
        self.tabs_count_changed.emit(self.count())

    # ── cir-специфичные ──

    def apply_theme(self, scheme_name: str):
        for i in range(self.count()):
            p = self.widget(i)
            if hasattr(p, 'page_type') and p.page_type == 'cir' and hasattr(p, 'apply_theme'):
                p.apply_theme(scheme_name)

    def toggle_line_numbers(self, visible: bool):
        for i in range(self.count()):
            p = self.widget(i)
            if hasattr(p, 'page_type') and p.page_type == 'cir':
                p.set_line_numbers_visible(visible)

    def toggle_terminal(self, visible: bool):
        for i in range(self.count()):
            p = self.widget(i)
            if hasattr(p, 'page_type') and p.page_type == 'cir':
                p.set_terminal_visible(visible)

    def apply_font(self, family: str, size: int):
        for i in range(self.count()):
            p = self.widget(i)
            if hasattr(p, 'page_type') and p.page_type == 'cir':
                p.set_font(family, size)

    # ── переключение вкладок ──

    def _on_tab_changed(self, index: int):
        if index < 0:
            return

    # ── утилиты ──

    @staticmethod
    def _refdes_prefix_from_sym(sym_data) -> str:
        for t in sym_data.texts:
            if t.content.startswith("refdes="):
                val = t.content.split("=", 1)[1].strip()
                return val.rstrip("?").strip() or ""
        return ""

    @staticmethod
    def _value_from_sym(sym_data) -> str:
        if sym_data.default_value:
            return sym_data.default_value
        for t in sym_data.texts:
            if t.content.startswith("value="):
                return t.content.split("=", 1)[1].strip('" ')
        return ""
