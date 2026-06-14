from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                               QTableWidget, QTableWidgetItem, QPushButton,
                               QHeaderView, QAbstractScrollArea)
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt


def _fmt_voltage(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if v == 0:
        return "0 V"
    if a >= 1.0:
        return f"{v:.4g} V"
    if a >= 1e-3:
        return f"{v*1e3:.4g} mV"
    return f"{v*1e6:.4g} µV"


def _fmt_current(i: float | None) -> str:
    if i is None:
        return "—"
    a = abs(i)
    if i == 0:
        return "0 A"
    if a >= 1.0:
        return f"{i:.4g} A"
    if a >= 1e-3:
        return f"{i*1e3:.4g} mA"
    if a >= 1e-6:
        return f"{i*1e6:.4g} µA"
    return f"{i*1e9:.4g} nA"


class OpDialog(QDialog):
    def __init__(self, parent, rows: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("SpiceEDA — .OP рабочая точка")
        self.resize(500, 450)

        self._rows = rows

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        self._table = QTableWidget(len(rows), 3)
        self._table.setHorizontalHeaderLabels(["Имя", "Напряжение", "Ток"])
        self._table.verticalHeader().hide()
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents
        )
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._set_dark_style()

        for i, row in enumerate(rows):
            name_item = QTableWidgetItem(row["name"])
            name_item.setFont(QFont("Monospace", 10))
            self._table.setItem(i, 0, name_item)

            v_text = _fmt_voltage(row.get("voltage"))
            v_item = QTableWidgetItem(v_text)
            v_item.setFont(QFont("Monospace", 10))
            v_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 1, v_item)

            c_text = _fmt_current(row.get("current"))
            c_item = QTableWidgetItem(c_text)
            c_item.setFont(QFont("Monospace", 10))
            c_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._table.setItem(i, 2, c_item)

        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        copy_btn = QPushButton("Копировать")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        btn_row.addWidget(copy_btn)
        btn_row.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _set_dark_style(self):
        self._table.setStyleSheet("""
            QTableWidget {
                background-color: #1e1e1e;
                color: #d4d4d4;
                gridline-color: #333;
                alternate-background-color: #222;
            }
            QTableWidget::item {
                padding: 4px 8px;
            }
            QHeaderView::section {
                background-color: #2a2a2a;
                color: #aaa;
                border: 1px solid #333;
                padding: 4px 8px;
                font-weight: bold;
            }
        """)

    def _copy_to_clipboard(self):
        from PySide6.QtGui import QGuiApplication
        lines = ["Имя\tНапряжение\tТок"]
        for row in self._rows:
            v = row.get("voltage")
            c = row.get("current")
            v_str = f"{v:.6e}" if v is not None else ""
            c_str = f"{c:.6e}" if c is not None else ""
            lines.append(f"{row['name']}\t{v_str}\t{c_str}")
        QGuiApplication.clipboard().setText("\n".join(lines))
