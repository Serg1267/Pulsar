from __future__ import annotations
from typing import Optional

from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
                               QTreeWidget, QTreeWidgetItem, QPushButton,
                               QLabel, QSplitter, QTextEdit)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from EDA.core.library.library import ComponentLibrary, LIB_SYM_DIR


class ComponentBrowser(QDialog):
    """Диалог выбора компонента из библиотеки .sym файлов."""

    # Излучается при выборе: имя символа (id)
    symbol_selected = Signal(str)

    def __init__(self, library: ComponentLibrary, parent=None):
        super().__init__(parent)
        self._library = library

        self.setWindowTitle("Выбор компонента")
        self.resize(520, 480)

        self._build_ui()
        self._populate_tree()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Поле поиска
        self._search = QLineEdit()
        self._search.setPlaceholderText("Поиск компонента...")
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)

        # Дерево: категории -> символы
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Компонент", "Описание"])
        self._tree.header().setStretchLastSection(True)
        self._tree.setColumnWidth(0, 200)
        self._tree.setAlternatingRowColors(True)
        self._tree.itemClicked.connect(self._on_item_clicked)
        self._tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._tree, stretch=1)

        # Кнопки
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_layout.addStretch()

        self._btn_place = QPushButton("Разместить")
        self._btn_place.setEnabled(False)
        self._btn_place.clicked.connect(self._on_place)
        btn_layout.addWidget(self._btn_place)

        layout.addLayout(btn_layout)

        # Информация о выбранном
        self._info = QLabel()
        self._info.setWordWrap(True)
        self._info.setVisible(False)
        layout.addWidget(self._info)

    def _populate_tree(self, filter_text: str = ""):
        self._tree.clear()
        self._selected_id = None
        self._btn_place.setEnabled(False)
        self._info.setVisible(False)

        ft = filter_text.strip().lower()

        for cat in self._library.list_categories():
            symbols = self._library.list_by_category(cat)
            if ft:
                symbols = [s for s in symbols if ft in s.lower()]

            if not symbols:
                continue

            cat_item = QTreeWidgetItem([cat.capitalize(), ""])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)

            for sym_id in symbols:
                sym = self._library.get(sym_id)
                desc = sym.description if sym else ""
                item = QTreeWidgetItem([sym_id, desc])
                item.setData(0, Qt.ItemDataRole.UserRole, sym_id)
                cat_item.addChild(item)

            self._tree.addTopLevelItem(cat_item)
            cat_item.setExpanded(bool(ft))

    def _on_search(self, text: str):
        self._populate_tree(text)

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int):
        sym_id = item.data(0, Qt.ItemDataRole.UserRole)
        if not sym_id:
            self._selected_id = None
            self._btn_place.setEnabled(False)
            self._info.setVisible(False)
            return

        self._selected_id = sym_id
        self._btn_place.setEnabled(True)

        sym = self._library.get(sym_id)
        if sym:
            info = (
                f"Файл: {sym.source_path}\n"
                f"Выводов: {len(sym.pins)}\n"
                f"Устройство: {sym.attributes.get('device', '—')}\n"
                f"Value: {sym.attributes.get('value', '—')}"
            )
            self._info.setText(info)
            self._info.setVisible(True)

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        self._on_item_clicked(item, column)
        if self._selected_id:
            self.accept()

    def _on_place(self):
        if self._selected_id:
            self.accept()

    def selected_symbol_id(self) -> Optional[str]:
        return self._selected_id if self.result() == QDialog.DialogCode.Accepted else None
