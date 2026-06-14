from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLineEdit, QTreeWidget,
    QTreeWidgetItem, QLabel,
)
from PySide6.QtCore import Qt, QMimeData, QByteArray, QPoint
from PySide6.QtGui import QDrag, QPixmap, QPainter, QPen, QColor

from EDA.core.library.library import ComponentLibrary


class _ComponentDragTree(QTreeWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_start = None

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if item and item.childCount() == 0:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is not None and (
            (event.pos() - self._drag_start).manhattanLength() > 10
        ):
            self._drag_start = None
            item = self.currentItem()
            if not item:
                return
            sym_id = item.data(0, Qt.ItemDataRole.UserRole)
            if not sym_id:
                return

            mime = QMimeData()
            mime.setData("application/x-spiceeda-component",
                         QByteArray(sym_id.encode("utf-8")))
            drag = QDrag(self)
            drag.setMimeData(mime)

            px = QPixmap(24, 24)
            px.fill(Qt.GlobalColor.transparent)
            p = QPainter(px)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor("#ffffff"), 3)
            p.setPen(pen)
            p.drawLine(4, 12, 20, 12)
            p.drawLine(12, 4, 12, 20)
            p.end()
            drag.setPixmap(px)
            drag.setHotSpot(QPoint(12, 12))

            drag.exec(Qt.DropAction.CopyAction)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)


class ComponentPanel(QWidget):
    """Панель компонентов с фильтром и drag & drop."""

    def __init__(self, library: ComponentLibrary | None = None, parent=None):
        super().__init__(parent)
        self._library = library or ComponentLibrary()
        self._build_ui()
        self._populate()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Фильтр компонентов…")
        self._filter.textChanged.connect(self._on_filter)
        layout.addWidget(self._filter)

        self._tree = _ComponentDragTree()
        self._tree.setHeaderHidden(True)
        self._tree.setSelectionMode(self._tree.SelectionMode.SingleSelection)
        self._tree.setAnimated(True)
        self._tree.setIndentation(16)
        layout.addWidget(self._tree, stretch=1)

        self._placeholder = QLabel("Компоненты не найдены")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #666;")
        self._placeholder.hide()
        layout.addWidget(self._placeholder)

    def _populate(self, filter_text: str = ""):
        self._tree.clear()
        ft = filter_text.strip().lower()

        for cat in self._library.list_categories():
            sym_ids = self._library.list_by_category(cat)
            if ft:
                sym_ids = [s for s in sym_ids if ft in s.lower() or ft in cat.lower()]
                if not sym_ids:
                    continue

            cat_item = QTreeWidgetItem([cat])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)
            self._tree.addTopLevelItem(cat_item)

            for sym_id in sym_ids:
                sym = self._library.get(sym_id)
                label = sym_id
                leaf = QTreeWidgetItem([label])
                leaf.setData(0, Qt.ItemDataRole.UserRole, sym_id)
                leaf.setToolTip(0, f"{label} — {sym_id}")
                leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsDragEnabled)
                cat_item.addChild(leaf)

            if ft:
                cat_item.setExpanded(True)

        if self._tree.topLevelItemCount() == 0:
            text = "Нет компонентов" if ft else "Библиотека пуста"
            self._placeholder.setText(text)
            self._placeholder.show()
            self._tree.hide()
        else:
            self._placeholder.hide()
            self._tree.show()

    def _on_filter(self, text: str):
        self._populate(text)
