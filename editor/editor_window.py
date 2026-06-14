"""
SpiceEDA Editor Window — отдельное окно с редактором, терминалом и симуляцией.
Открывается из главного окна проекта.
"""

import re
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QTextEdit,
    QSplitter,
    QFileDialog,
    QMessageBox,
    QFontDialog,
    QDialog,
    QLabel,
)
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QAction, QFont, QPalette, QColor, QTextCharFormat, QCloseEvent

from simulator.ngspice_simulator import NGspiceSimulator
from simulator.netlist_validator import validate_netlist
from plotter.spice_plotter import SpicePlotterWindow
from editor.spice_highlighter import SpiceHighlighter, DEFAULT_SCHEME, COLOR_SCHEMES
from editor.line_number_area import LineNumberArea
from editor.line_highlight_editor import LineHighlightPlainTextEdit
from autocomplete.spice_completer import SpiceCompleter
from ui.settings_dialog import SettingsDialog


class SpiceEDAEditorWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        # ...конец __init__ ...

    def closeEvent(self, event):
        if self._is_modified:
            reply = QMessageBox.question(
                self,
                "Сохранение изменений",
                "Есть несохраненные изменения. Сохранить перед выходом?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if reply == QMessageBox.Yes:
                self.save_file()
            elif reply == QMessageBox.Cancel:
                event.abort()
        event.accept()
    """Отдельное окно с SPICE-редактором, терминалом и симуляцией"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SpiceEDA — Редактор")
        self.resize(1000, 700)

        self.settings = QSettings("SpiceEDA", "SpiceEDA")
        self._current_file: Path | None = None
        self._is_modified = False
        self.simulator = NGspiceSimulator()

        self._output_queue = []
        self._output_timer = QTimer()
        self._output_timer.timeout.connect(self._process_output_queue)

        # Счётчик прогресса (имитация, NGspice не даёт реальных %)
        self._sim_progress_value = 0

        self._setup_ui()

        # Автодополнение (после _setup_ui, где создаётся self.editor)
        self.completer = SpiceCompleter(self.editor)
        self.editor.set_completer(self.completer)  # Связь для навигации по Tab

        self._create_menu_bar()
        self._apply_theme(DEFAULT_SCHEME)
        self._output_timer.start(50)

        auto_complete_enabled = self.settings.value("editor/auto_complete", "true").lower() == "true"
        self.completer.enable_auto_completion(auto_complete_enabled)
        self.editor.textChanged.connect(self.completer.on_text_changed)
        self.editor.cursorPositionChanged.connect(self.completer.on_cursor_position_changed)

        # Загрузить состояние терминала
        terminal_visible = self.settings.value("editor/terminal_visible", "true").lower() == "true"
        if not terminal_visible:
            self._terminal_action.setChecked(False)
            self._toggle_terminal()

    def _update_title(self):
        name = self._current_file.name if self._current_file else "Без имени"
        star = " *" if self._is_modified else ""
        self.setWindowTitle(f"SpiceEDA — {name}{star}")

    def _mark_modified(self):
        self._is_modified = True
        self._update_title()

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        splitter = QSplitter(Qt.Orientation.Vertical)

        # Сохраняем ссылку для доступа из _toggle_terminal
        self._main_splitter = splitter

        # Редактор
        editor_container = QWidget()
        editor_layout = QHBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)

        self.editor = LineHighlightPlainTextEdit()
        saved_family = self.settings.value("editor/font_family", "")
        saved_size = self.settings.value("editor/font_size", 14, type=int)
        if saved_family:
            self.editor.setFont(QFont(saved_family, saved_size))
        else:
            self.editor.setFont(QFont("Monospace", 14))
        self.editor.setPlaceholderText(
            "Откройте файл схемы (.cir, .sp) или начните вводить SPICE-код здесь..."
        )
        self.highlighter = SpiceHighlighter(self.editor.document())
        self.editor.textChanged.connect(self._mark_modified)

        self.line_numbers = LineNumberArea(self.editor)
        self._line_numbers_enabled = self.settings.value("editor/line_numbers", "true").lower() == "true"
        self.line_numbers.setVisible(self._line_numbers_enabled)
        self.editor.updateRequest.connect(self._update_line_numbers_on_scroll)
        self.editor.blockCountChanged.connect(self._on_block_count_changed)
        self.editor.textChanged.connect(self._on_text_changed)
        QTimer.singleShot(0, self.line_numbers.update)

        editor_layout.addWidget(self.line_numbers)
        editor_layout.addWidget(self.editor, 1)
        self._main_splitter.addWidget(editor_container)

        # Терминал
        self.terminal = QTextEdit()
        self.terminal.setFont(QFont("Monospace", 10))
        self.terminal.setReadOnly(True)
        self.terminal.setStyleSheet(
            "QTextEdit { "
            "background-color: #1e1e1e; "
            "color: #d4d4d4; "
            "border: 1px solid #333; "
            "}"
        )
        self.terminal.setPlaceholderText("Вывод NGspice будет отображаться здесь...")
        self._main_splitter.addWidget(self.terminal)

        # Прогресс-бар симуляции (проценты)
        self._sim_progress = QLabel("")
        self._sim_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sim_progress.setStyleSheet(
            "QLabel { background: #1a1a2e; color: #00ff41; font-weight: bold; "
            "font-size: 13px; border: 1px solid #333; border-radius: 3px; }"
        )
        self._sim_progress.setFixedHeight(24)
        self._sim_progress.hide()
        main_layout.addWidget(self._sim_progress)

        self._main_splitter.setSizes([560, 240])
        main_layout.addWidget(self._main_splitter)

    def _create_menu_bar(self):
        menubar = self.menuBar()

        # Файл
        file_menu = menubar.addMenu("Файл")

        open_action = QAction("Открыть…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        open_netlist_action = QAction("Открыть Netlist…", self)
        open_netlist_action.setShortcut("Ctrl+Shift+O")
        open_netlist_action.triggered.connect(self.open_netlist)
        file_menu.addAction(open_netlist_action)

        save_action = QAction("Сохранить", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_file)
        file_menu.addAction(save_action)

        save_as_action = QAction("Сохранить как…", self)
        save_as_action.setShortcut("Ctrl+Shift+S")
        save_as_action.triggered.connect(self.save_file_as)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        save_plot_action = QAction("Сохранить график…", self)
        save_plot_action.setShortcut("Ctrl+Alt+S")
        save_plot_action.triggered.connect(self._save_plot)
        file_menu.addAction(save_plot_action)

        file_menu.addSeparator()

        close_action = QAction("Закрыть окно", self)
        close_action.setShortcut("Ctrl+W")
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        # Правка
        edit_menu = menubar.addMenu("Правка")
        select_all_action = QAction("Выделить всё", self)
        select_all_action.setShortcut("Ctrl+A")
        select_all_action.triggered.connect(self.editor.selectAll)
        edit_menu.addAction(select_all_action)

        undo_action = QAction("Отменить", self)
        undo_action.setShortcut("Ctrl+Z")
        undo_action.triggered.connect(self.editor.undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("Повторить", self)
        redo_action.setShortcut("Ctrl+Shift+Z")
        redo_action.triggered.connect(self.editor.redo)
        edit_menu.addAction(redo_action)

        edit_menu.addSeparator()

        cut_action = QAction("Вырезать", self)
        cut_action.setShortcut("Ctrl+X")
        cut_action.triggered.connect(self.editor.cut)
        edit_menu.addAction(cut_action)

        copy_action = QAction("Копировать", self)
        copy_action.setShortcut("Ctrl+C")
        copy_action.triggered.connect(self.editor.copy)
        edit_menu.addAction(copy_action)

        paste_action = QAction("Вставить", self)
        paste_action.setShortcut("Ctrl+V")
        paste_action.triggered.connect(self.editor.paste)
        edit_menu.addAction(paste_action)

        edit_menu.addSeparator()

        complete_action = QAction("Автодополнение", self)
        complete_action.setShortcut("Ctrl+Space")
        complete_action.triggered.connect(self.completer.trigger_completion)
        edit_menu.addAction(complete_action)

        # Вид
        view_menu = menubar.addMenu("Вид")

        theme_menu = view_menu.addMenu("Цветовая схема")
        self._theme_actions = {}
        for scheme_name in SpiceHighlighter.available_schemes():
            action = QAction(scheme_name, self, checkable=True)
            action.setChecked(scheme_name == DEFAULT_SCHEME)
            action.triggered.connect(lambda checked, name=scheme_name: self._apply_theme(name))
            theme_menu.addAction(action)
            self._theme_actions[scheme_name] = action

        view_menu.addSeparator()

        self._line_numbers_action = QAction("Номера строк", self, checkable=True)
        self._line_numbers_action.setChecked(self._line_numbers_enabled)
        self._line_numbers_action.triggered.connect(self._toggle_line_numbers)
        view_menu.addAction(self._line_numbers_action)

        self._auto_complete_action = QAction("Автодополнение", self, checkable=True)
        self._auto_complete_action.setChecked(self.completer.is_auto_enabled())
        self._auto_complete_action.triggered.connect(self._toggle_auto_complete)
        view_menu.addAction(self._auto_complete_action)

        font_action = QAction("Шрифт редактора…", self)
        font_action.triggered.connect(self._choose_font)
        view_menu.addAction(font_action)

        view_menu.addSeparator()

        self._terminal_action = QAction("Терминал NGspice", self, checkable=True)
        self._terminal_action.setChecked(True)
        self._terminal_action.setShortcut("Ctrl+T")
        self._terminal_action.triggered.connect(self._toggle_terminal)
        view_menu.addAction(self._terminal_action)

        view_menu.addSeparator()

        settings_action = QAction("Настройки программы…", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings)
        view_menu.addAction(settings_action)

        view_menu.addSeparator()

        # Симуляция
        sim_menu = menubar.addMenu("Симуляция")

        run_action = QAction("Запуск", self)
        run_action.setShortcut("F5")
        run_action.triggered.connect(self.run_simulation)
        sim_menu.addAction(run_action)

        stop_action = QAction("Остановить", self)
        stop_action.setShortcut("Ctrl+Shift+S")
        stop_action.triggered.connect(self.stop_simulation)
        sim_menu.addAction(stop_action)

    # ─── Файловые операции ───

    def open_file(self, file_path: str | None = None):
        """Открыть файл"""
        if not file_path:
            file_path, _ = QFileDialog.getOpenFileName(
                self, "Открыть файл схемы", str(Path.home()),
                "SPICE Files (*.cir *.sp);;All Files (*)",
            )
        if not file_path:
            return

        try:
            with open(file_path, "r") as f:
                content = f.read()
            self.editor.setPlainText(content)
            self._current_file = Path(file_path)
            self._is_modified = False
            self._update_title()
            self._log_to_terminal_safe(f"Открыт файл: {self._current_file.name}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть файл:\n{e}")

    def open_netlist(self):
        """Открыть netlist файл (.cir, .sp)"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Открыть Netlist", str(Path.home()),
            "SPICE Netlist (*.cir *.sp);;All Files (*)",
        )
        if not file_path:
            return

        try:
            with open(file_path, "r") as f:
                content = f.read()
            self.editor.setPlainText(content)
            self._current_file = Path(file_path)
            self._is_modified = False
            self._update_title()
            self._log_to_terminal_safe(f"Открыт netlist: {self._current_file.name}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть netlist:\n{e}")

    def save_file(self):
        if self._current_file:
            self._save_to_file(self._current_file)
        else:
            self.save_file_as()

    def save_file_as(self):
        dialog = QFileDialog(self, "Сохранить файл схемы как…", str(self._current_file.parent if self._current_file else Path.home()))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setNameFilters(["SPICE Circuit (*.cir)", "SPICE Netlist (*.sp)", "All Files (*)"])
        if dialog.exec() != QFileDialog.DialogCode.Accepted:
            return

        file_path = dialog.selectedFiles()[0]
        if not file_path:
            return

        name_filter = dialog.selectedNameFilter()
        if ".cir" in name_filter and not file_path.endswith((".cir", ".sp")):
            file_path += ".cir"
        elif ".sp" in name_filter and not file_path.endswith((".cir", ".sp")):
            file_path += ".sp"

        self._current_file = Path(file_path)
        self._save_to_file(self._current_file)
        self._update_title()

    def get_current_file(self) -> Path | None:
        """Вернуть путь к текущему файлу"""
        return self._current_file

    def reload_file(self):
        """Перечитать текущий файл с диска"""
        if not self._current_file or not self._current_file.exists():
            return
        try:
            content = self._current_file.read_text()
            # Сохранить позицию курсора если возможно
            cursor = self.editor.textCursor()
            position = cursor.position()
            self.editor.setPlainText(content)
            # Восстановить позицию курсора
            new_cursor = self.editor.textCursor()
            new_cursor.setPosition(min(position, len(content)))
            self.editor.setTextCursor(new_cursor)
            self._is_modified = False
            self._update_title()
            self._log_to_terminal_safe(f"Файл обновлён: {self._current_file.name}")
        except Exception as e:
            self._log_to_terminal_safe(f"Ошибка перезаписи файла: {e}")

    def _save_to_file(self, file_path: Path):
        try:
            with open(file_path, "w") as f:
                f.write(self.editor.toPlainText())
            self._is_modified = False
            self._update_title()
            self._log_to_terminal_safe(f"Сохранён файл: {file_path.name}")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл:\n{e}")

    def _save_plot(self):
        """Сохранить график в файл"""
        if not hasattr(self, '_plot_window') or self._plot_window is None:
            QMessageBox.information(self, "Нет графика", "Сначала запустите симуляцию.")
            return
        self._plot_window.save_plot()

    # ─── Симуляция ───

    def run_simulation(self):
        if self.simulator.is_running:
            self._log_to_terminal_safe("[WARN] Симуляция уже запущена")
            return

        if not self._current_file:
            self._log_to_terminal_safe("[ERROR] Сначала сохраните файл схемы")
            QMessageBox.warning(self, "Предупреждение", "Сначала сохраните файл схемы (.cir или .sp)")
            return

        # Валидация netlist
        result = validate_netlist(self._current_file)
        report = result.formatted_report()
        self._log_to_terminal_safe(report)

        if not result.is_valid:
            self._log_to_terminal_safe(
                f"❌ Симуляция отменена: {result.error_count} ошибок"
            )
            # Показать диалог
            QMessageBox.warning(
                self,
                "Ошибки в netlist",
                f"Найдено {result.error_count} ошибок:\n\n"
                + "\n".join(
                    f"• {e.message}" for e in result.errors if e.severity == "error"
                ),
            )
            return

        # Предупреждения — не блокируют, но показываем
        if result.warning_count > 0:
            self._log_to_terminal_safe(
                f"⚠️ Предупреждений: {result.warning_count}"
            )

        # Исправить пробелы в .PRINT: v (2) → v(2)
        self._fix_print_directive()

        self._save_to_file(self._current_file)
        self.terminal.clear()

        # Показать прогресс-бар
        self._sim_progress_value = 0
        self._sim_progress.setText("Симуляция... 0%")
        self._sim_progress.show()
        QTimer.singleShot(50, self._update_sim_progress)

        self.simulator.run_simulation(
            self._current_file,
            output_callback=self._log_to_terminal_safe,
            finished_callback=lambda success: self._output_queue.append(("FINISHED", success)),
        )

    def _fix_print_directive(self):
        """Исправить пробелы в .PRINT: v (2) → v(2)"""
        text = self.editor.toPlainText()
        fixed_lines = []
        changed = False
        for line in text.split('\n'):
            if re.match(r'^\s*\.PRINT\b', line, re.IGNORECASE):
                new_line = re.sub(r'(\w)\s*\(', r'\1(', line)
                if new_line != line:
                    changed = True
                fixed_lines.append(new_line)
            else:
                fixed_lines.append(line)
        if changed:
            fixed_text = '\n'.join(fixed_lines)
            cursor = self.editor.textCursor()
            pos = cursor.position()
            self.editor.setPlainText(fixed_text)
            new_cursor = self.editor.textCursor()
            new_cursor.setPosition(min(pos, len(fixed_text)))
            self.editor.setTextCursor(new_cursor)
            self._log_to_terminal_safe("[INFO] Исправлены пробелы в .PRINT директивах")

    def stop_simulation(self):
        self.simulator.stop_simulation()
        self._sim_progress_value = 0
        self._sim_progress.hide()
        self._log_to_terminal_safe("[INFO] Запрос на остановку отправлен")

    # ─── Тема / Шрифт ───

    def _apply_theme(self, scheme_name: str):
        for name, action in self._theme_actions.items():
            action.setChecked(name == scheme_name)

        self.highlighter.set_scheme(scheme_name)
        colors = COLOR_SCHEMES[scheme_name]
        bg = QColor(colors.get("bg", "#1e1e1e"))
        text = QColor(colors.get("text", "#d4d4d4"))

        palette = self.editor.palette()
        palette.setColor(QPalette.ColorRole.Base, bg)
        palette.setColor(QPalette.ColorRole.Text, text)
        self.editor.setPalette(palette)

        self.terminal.setStyleSheet(
            f"QTextEdit {{ "
            f"background-color: {bg.name()}; "
            f"color: {text.name()}; "
            f"border: 1px solid #333; "
            f"}}"
        )

        self._update_line_number_colors()
        self._update_line_highlight_color()

    def _update_line_number_colors(self):
        scheme_name = self.highlighter.scheme_name
        colors = COLOR_SCHEMES[scheme_name]
        bg = QColor(colors.get("bg", "#1e1e1e"))
        text = QColor(colors.get("text", "#d4d4d4"))
        luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        is_dark = luminance < 128
        if is_dark:
            line_bg = bg.lighter(115)
            line_text = QColor(text.red(), text.green(), text.blue(), 160)
        else:
            line_bg = bg.darker(105)
            line_text = QColor(100, 100, 100)
        self.line_numbers.set_colors(line_bg, line_text)

    def _update_line_highlight_color(self):
        scheme_name = self.highlighter.scheme_name
        colors = COLOR_SCHEMES[scheme_name]
        bg = QColor(colors.get("bg", "#1e1e1e"))
        luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        if luminance < 128:
            line_bg = bg.lighter(165)
        else:
            line_bg = bg.darker(125)
        self.editor.set_line_highlight_color(line_bg)

    def _choose_font(self):
        current_font = self.editor.font()
        result = QFontDialog.getFont(current_font, self, "Выбор шрифта редактора")
        if isinstance(result[0], QFont):
            font, ok = result
        else:
            ok, font = result
        if ok and isinstance(font, QFont):
            self.editor.setFont(font)
            self.settings.setValue("editor/font_family", font.family())
            self.settings.setValue("editor/font_size", font.pointSize())
            QTimer.singleShot(0, lambda: (self.line_numbers.update(),
                                           self.line_numbers.parentWidget().layout().update()))

    def _check_duplicate_refdes(self, code: str) -> list:
        """Проверить код на дубликаты имён компонентов (refdes)"""
        refdes_map = {}  # refdes -> line_number
        duplicates = []
        
        for i, line in enumerate(code.split('\n'), 1):
            line = line.strip()
            if not line or line.startswith('*'):
                continue
            # Имя компонента: первая буква + цифры, до первого пробела
            match = re.match(r'^([A-Za-z][A-Za-z0-9]*)\s', line)
            if match:
                refdes = match.group(1).upper()
                if refdes in refdes_map:
                    duplicates.append((refdes, refdes_map[refdes], i))
                else:
                    refdes_map[refdes] = i
        
        return duplicates

    def _toggle_line_numbers(self):
        self._line_numbers_enabled = self._line_numbers_action.isChecked()
        self.line_numbers.setVisible(self._line_numbers_enabled)
        self.settings.setValue("editor/line_numbers", str(self._line_numbers_enabled))

    def _toggle_auto_complete(self):
        enabled = self._auto_complete_action.isChecked()
        self.completer.enable_auto_completion(enabled)
        self.settings.setValue("editor/auto_complete", str(enabled))

    def _toggle_terminal(self):
        """Скрыть/показать терминал NGspice"""
        visible = self._terminal_action.isChecked()
        self.terminal.setVisible(visible)
        self._sim_progress.setVisible(visible)

        if visible:
            # Восстановить размеры сплиттера
            self._main_splitter.setSizes([560, 240])
        else:
            # Скрыть терминал — отдать всё пространство редактору
            self._main_splitter.setSizes([800, 0])

        self.settings.setValue("editor/terminal_visible", str(visible))

    def _update_line_numbers_on_scroll(self, rect, dy):
        if self._line_numbers_enabled:
            self.line_numbers.update()

    def _on_block_count_changed(self, count):
        self.line_numbers.updateGeometry()
        self.line_numbers.update()

    def _on_text_changed(self):
        QTimer.singleShot(0, self.line_numbers.update)

    # ─── Терминал / Вывод ───

    def _update_sim_progress(self):
        """Обновить процент прогресса (имитация через singleShot)"""
        if not self._sim_progress.isVisible():
            return
        if self._sim_progress_value < 95:
            self._sim_progress_value += 1
            self._sim_progress.setText(f"Симуляция... {self._sim_progress_value}%")
            QTimer.singleShot(50, self._update_sim_progress)

    def _log_to_terminal_safe(self, text: str):
        self._output_queue.append(text)

    def _process_output_queue(self):
        if not hasattr(self, 'terminal'):
            return
        while self._output_queue:
            item = self._output_queue.pop(0)
            if isinstance(item, tuple) and item[0] == "FINISHED":
                success = item[1]
                # Скрыть прогресс-бар
                self._sim_progress_value = 100
                self._sim_progress.setText("Готово — 100%")
                QTimer.singleShot(1500, lambda: self._sim_progress.hide())
                if success and self.simulator.simulation_data:
                    self._output_queue.append("[INFO] Попытка отображения графиков...")
                    QTimer.singleShot(500, lambda: self._plot_simulation_results())
            else:
                self._append_colored_text(item)

        scrollbar = self.terminal.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 10
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _append_colored_text(self, text: str):
        """Добавить текст в терминал с раскраской по типу сообщения"""
        cursor = self.terminal.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)

        # Определить формат по префиксу
        fmt = QTextCharFormat()
        if text.startswith("[ERROR]"):
            fmt.setForeground(QColor("#ff6b6b"))  # Красный для ошибок
        elif text.startswith("[WARN]") or text.startswith("[WARNING]"):
            fmt.setForeground(QColor("#ffbe0b"))  # Жёлтый для предупреждений
        elif text.startswith("[SUCCESS]"):
            fmt.setForeground(QColor("#00ff41"))  # Зелёный для успеха
        elif text.startswith("[STOP]"):
            fmt.setForeground(QColor("#ff9e00"))  # Оранжевый для остановки
        elif text.startswith("[INFO]"):
            fmt.setForeground(QColor("#888888"))  # Серый для информации
        else:
            fmt.setForeground(self.terminal.palette().text().color())  # Обычный цвет

        cursor.insertText(text + "\n", fmt)

    def _open_settings(self):
        """Открыть диалог настроек программы"""
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._log_to_terminal_safe("[INFO] Настройки применены")
            settings = QSettings("SpiceEDA", "SpiceEDA")
            family = settings.value("editor/font_family", "Monospace")
            size = settings.value("editor/font_size", 14, type=int)
            self.editor.setFont(QFont(family, size))

    def _plot_simulation_results(self):
        try:
            terminal_text = self.terminal.toPlainText()
            sim_data = self.simulator.simulation_data
            analysis_type = sim_data.get('type', 'unknown')

            # Проверяем тип анализа
            if analysis_type == 'unknown':
                if re.search(r'\.TRAN\b', terminal_text, re.IGNORECASE):
                    analysis_type = 'tran'
                elif re.search(r'\.DC\b', terminal_text, re.IGNORECASE):
                    analysis_type = 'dc'
                elif re.search(r'\.AC\b', terminal_text, re.IGNORECASE):
                    analysis_type = 'ac'
                elif re.search(r'\.OP\b', terminal_text, re.IGNORECASE):
                    analysis_type = 'op'

            # Проверяем есть ли табличные данные для графика
            has_plot_data = re.search(r'Index\s+(?:time|frequency|v-sweep)', terminal_text)

            if has_plot_data and analysis_type != 'unknown':
                plot_window = SpicePlotterWindow(terminal_text, analysis_type)
                plot_window.show()
                self._plot_window = plot_window
                self._log_to_terminal_safe("[SUCCESS] Окно графиков открыто")
        except Exception as e:
            self._log_to_terminal_safe(f"[WARN] Не удалось отобразить графики: {e}")

    def closeEvent(self, event: QCloseEvent):
        """Обработка закрытия окна редактора"""
        if self._is_modified:
            # Файл изменён — спросить пользователя
            reply = QMessageBox.question(
                self,
                "Сохранить изменения?",
                f"Файл '{self._current_file.name if self._current_file else 'Без имени'}' был изменён.\n\nСохранить перед закрытием?",
                QMessageBox.StandardButton.Save |
                QMessageBox.StandardButton.Discard |
                QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save
            )

            if reply == QMessageBox.StandardButton.Save:
                # Сохранить и закрыть
                if self._current_file:
                    self._save_to_file(self._current_file)
                else:
                    # Новый файл — спросить путь
                    path, _ = QFileDialog.getSaveFileName(
                        self,
                        "Сохранить netlist как...",
                        "",
                        "SPICE Netlist (*.cir *.sp);;Все файлы (*.*)"
                    )
                    if path:
                        self._current_file = Path(path)
                        self._save_to_file(self._current_file)
                    else:
                        event.ignore()  # Отмена — не закрывать
                        return
            elif reply == QMessageBox.StandardButton.Discard:
                pass  # Не сохранять, просто закрыть
            else:
                event.ignore()  # Cancel — не закрывать
                return

        event.accept()

