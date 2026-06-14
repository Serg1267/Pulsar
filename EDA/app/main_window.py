# -*- coding: utf-8 -*-
"""Главное окно приложения (QMainWindow)."""

from PySide6.QtWidgets import QMainWindow, QStatusBar, QFileDialog
from PySide6.QtCore import QSize
from PySide6.QtGui import QAction

from EDA.app.canvas import SchematicCanvas                  # Импортируем холст
from EDA.core.library.library import ComponentLibrary       # Библиотека компонентов
from EDA.core.parser.sym_parser import SymData              # SymData для поиска refdes/value в .sym
from EDA.app.dialogs.component_browser import ComponentBrowser  # Диалог выбора компонентов


class MainWindow(QMainWindow):
    """Главное окно редактора схем."""

    def __init__(self):
        """Инициализирует окно: заголовок, размер, холст, библиотека, строка состояния."""
        # Вызываем конструктор родительского класса QMainWindow
        super().__init__()

        # Устанавливаем заголовок окна
        self.setWindowTitle("EDA Schematic Editor")
        # Фиксируем начальный размер окна 800×600 пикселей
        self.resize(QSize(800, 600))

        # --- Холст (центральный виджет) ---
        self.canvas = SchematicCanvas(self)
        # Назначаем холст центральным виджетом окна (он заполняет всю клиентскую область)
        self.setCentralWidget(self.canvas)

        # --- Строка состояния (создаётся ДО первого использования) ---
        self.status_bar = QStatusBar(self)
        self.setStatusBar(self.status_bar)

        # --- Библиотека компонентов ---
        self.library = ComponentLibrary()
        self.status_bar.showMessage(
            f"Библиотека загружена: {len(self.library.list_all())} компонентов"
        )

        # --- Счётчик для рефдесов ---
        self._refdes_counter: dict[str, int] = {}

        # --- Главное меню ---
        self._create_menus()

        # --- Соединяем сигналы холста ---
        self.canvas.position_changed.connect(self._on_position_changed)
        self.canvas.mode_changed.connect(self._on_mode_changed)
        self.canvas.component_placed.connect(self._on_component_placed)

    def _on_position_changed(self, x: float, y: float):
        """Обновляет строку состояния привязанными координатами курсора."""
        self.status_bar.showMessage(f" X: {x:.0f}  Y: {y:.0f}  mil")

    def _on_mode_changed(self, mode: str):
        if mode == "WIRE":
            self.status_bar.showMessage("Режим проводов (W — выход, Esc — отмена)")
        elif mode == "SEGMENT":
            self.status_bar.showMessage("Режим сегментов [N]  ЛКМ — начать, ПКМ — зафиксировать, Esc — выход")
        elif mode == "PLACE":
            self.status_bar.showMessage("Размещение: ЛКМ — поставить, ПКМ/Esc — отмена")
        else:
            self.status_bar.showMessage("")

    def _create_menus(self):
        """Создаёт строку главного меню: Файл, Правка, Вид, Добавить, Справка."""
        menubar = self.menuBar()

        # Пункт "Файл" — операции с проектами и файлами
        menu_file = menubar.addMenu("Файл")
        act_cir = QAction("Экспорт SPICE netlist...", self)
        act_cir.triggered.connect(self._on_export_cir)
        menu_file.addAction(act_cir)
        # Пункт "Правка" — отмена/повтор, буфер обмена
        menubar.addMenu("Правка")
        # Пункт "Вид" — настройки отображения, зум
        menubar.addMenu("Вид")
        # Пункт "Добавить" — добавление компонентов, проводов, текста
        menu_add = menubar.addMenu("Добавить")
        # Дочерний пункт "Компонент" — открывает диалог выбора
        act_component = QAction("Компонент", self)
        act_component.triggered.connect(self._on_add_component)
        menu_add.addAction(act_component)
        # Пункт "Справка" — о программе, документация
        menubar.addMenu("Справка")

    def _on_export_cir(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить SPICE netlist", "", "SPICE (*.cir);;Все файлы (*)")
        if path:
            content = self.canvas.export_cir()
            with open(path, "w") as f:
                f.write(content)
            self.status_bar.showMessage(f"SPICE netlist сохранён: {path}")

    def _on_component_placed(self, refdes: str):
        """Обновить счётчик refdes после размещения компонента."""
        import re
        m = re.match(r'^([A-Za-z]+)(\d+)$', refdes)
        if m:
            prefix, num_str = m.group(1), m.group(2)
            n = int(num_str)
            self._refdes_counter[prefix] = max(self._refdes_counter.get(prefix, 0), n)

    def _on_add_component(self):
        """Открывает диалог выбора компонента и размещает его на холсте."""
        dialog = ComponentBrowser(self.library, self)
        if dialog.exec() == ComponentBrowser.DialogCode.Accepted:
            sym_id = dialog.selected_symbol_id()
            if not sym_id:
                return
            sym = self.library.get(sym_id)
            if not sym or not sym.sym_data:
                return

            # Извлекаем префикс refdes из .sym (e.g. "R?" → "R")
            refdes_prefix = self._refdes_prefix_from_sym(sym.sym_data) or sym.id[0].upper() or "U"

            # Счётчик refdes
            n = self._refdes_counter.get(refdes_prefix, 0) + 1
            self._refdes_counter[refdes_prefix] = n
            refdes = f"{refdes_prefix}{n}"

            # Номинал из .sym
            value = self._value_from_sym(sym.sym_data)

            # Войти в режим размещения (фантом под курсором)
            self.canvas.start_placement(sym.sym_data, refdes=refdes, value=value)

    @staticmethod
    def _refdes_prefix_from_sym(sym_data: SymData) -> str:
        """Извлекает префикс refdes из .sym (e.g. 'R?' → 'R')."""
        for t in sym_data.texts:
            if t.content.startswith("refdes="):
                val = t.content.split("=", 1)[1].strip()
                return val.rstrip("?").strip() or ""
        return ""

    @staticmethod
    def _value_from_sym(sym_data: SymData) -> str:
        """Извлекает номинал из .sym."""
        if sym_data.default_value:
            return sym_data.default_value
        for t in sym_data.texts:
            if t.content.startswith("value="):
                return t.content.split("=", 1)[1].strip('" ')
        return ""
