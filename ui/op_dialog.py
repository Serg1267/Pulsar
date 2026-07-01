from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                               QTableWidget, QTableWidgetItem, QPushButton,
                               QHeaderView, QAbstractScrollArea, QCheckBox,
                               QWidget, QLabel)
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtCore import Qt


def _fmt_voltage(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.4g}"


def _fmt_current(i: float | None) -> str:
    if i is None:
        return "—"
    return f"{i:.4g}"


def _fmt_power(p: float | None) -> str:
    if p is None:
        return "—"
    return f"{p:.4g}"


_HEADERS = ["Компонент", "Напряжение, В", "Ток, А", "Мощность, Вт"]


class OpDialog(QDialog):
    def __init__(self, parent, rows: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("Pulsar — Анализ по постоянному току (.OP)")
        self.resize(640, 420)
        self.setMinimumSize(400, 250)

        self._all_rows = rows

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # top: checkboxes
        cb_row = QHBoxLayout()
        cb_row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Показать столбцы:")
        lbl.setStyleSheet("font-size: 11px;")
        cb_row.addWidget(lbl)

        self._cb_v = QCheckBox("Напряжение")
        self._cb_v.setChecked(True)
        self._cb_v.toggled.connect(self._update_columns)
        cb_row.addWidget(self._cb_v)

        self._cb_i = QCheckBox("Ток")
        self._cb_i.setChecked(True)
        self._cb_i.toggled.connect(self._update_columns)
        cb_row.addWidget(self._cb_i)

        self._cb_p = QCheckBox("Мощность")
        self._cb_p.setChecked(True)
        self._cb_p.toggled.connect(self._update_columns)
        cb_row.addWidget(self._cb_p)

        cb_row.addStretch()
        layout.addLayout(cb_row)

        # table
        self._table = QTableWidget(len(rows), 4)
        self._table.setHorizontalHeaderLabels(_HEADERS)
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(True)
        self._table.setAlternatingRowColors(False)
        self._table.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContentsOnFirstShow
        )
        self._table.verticalHeader().setDefaultSectionSize(22)

        h = self._table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            h.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._style_table()

        font_name = QFont("Segoe UI", 9)
        font_val = QFont("Consolas", 10)

        for i, row in enumerate(rows):
            name = row["name"]
            v = row.get("voltage")
            c = row.get("current")

            # power = V * I (both present)
            p = None
            if v is not None and c is not None:
                p = v * c

            name_item = QTableWidgetItem(f"  {name}")
            name_item.setFont(font_name)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 0, name_item)

            v_item = QTableWidgetItem(f"  {_fmt_voltage(v)}")
            v_item.setFont(font_val)
            v_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 1, v_item)

            c_item = QTableWidgetItem(f"  {_fmt_current(c)}")
            c_item.setFont(font_val)
            c_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 2, c_item)

            p_item = QTableWidgetItem(f"  {_fmt_power(p)}")
            p_item.setFont(font_val)
            p_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 3, p_item)

        layout.addWidget(self._table, 1)

        # bottom buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        info = QLabel(f"Элементов: {len(rows)}")
        info.setStyleSheet("font-size: 11px; color: #666;")
        btn_row.addWidget(info)
        btn_row.addStretch()

        copy_btn = QPushButton("Копировать")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        btn_row.addWidget(copy_btn)

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _style_table(self):
        self._table.setStyleSheet("""
            QTableWidget {
                background-color: #ffffff;
                color: #000000;
                gridline-color: #000000;
                outline: none;
            }
            QTableWidget::item {
                padding: 1px 4px;
                border: none;
            }
            QTableWidget::item:selected {
                background-color: #cce5ff;
                color: #000000;
            }
            QHeaderView::section {
                background-color: #e0e0e0;
                color: #000000;
                border: 1px solid #000000;
                padding: 3px 8px;
                font-weight: bold;
                font-size: 11px;
            }
        """)

    def _update_columns(self):
        show_v = self._cb_v.isChecked()
        show_i = self._cb_i.isChecked()
        show_p = self._cb_p.isChecked()
        self._table.setColumnHidden(1, not show_v)
        self._table.setColumnHidden(2, not show_i)
        self._table.setColumnHidden(3, not show_p)

    def _copy_to_clipboard(self):
        lines = ["Компонент\tНапряжение, В\tТок, А\tМощность, Вт"]
        for row in self._all_rows:
            v = row.get("voltage")
            c = row.get("current")
            p = (v * c) if v is not None and c is not None else None
            v_str = f"{v:.6e}" if v is not None else ""
            c_str = f"{c:.6e}" if c is not None else ""
            p_str = f"{p:.6e}" if p is not None else ""
            lines.append(f"{row['name']}\t{v_str}\t{c_str}\t{p_str}")
        QGuiApplication.clipboard().setText("\n".join(lines))
