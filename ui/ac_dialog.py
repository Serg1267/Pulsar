from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                               QTableWidget, QTableWidgetItem, QPushButton,
                               QHeaderView, QAbstractScrollArea, QCheckBox,
                               QWidget, QLabel)
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtCore import Qt


def _fmt_mag(v: float | None) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a < 1e-15:
        return "0"
    if a >= 1.0:
        return f"{v:.4g}"
    if a >= 1e-3:
        return f"{v * 1e3:.4g}"
    if a >= 1e-6:
        return f"{v * 1e6:.4g}"
    return f"{v * 1e9:.4g}"


def _mag_unit(v: float | None) -> str:
    if v is None:
        return ""
    a = abs(v)
    if a < 1e-15:
        return ""
    if a >= 1.0:
        return ""
    if a >= 1e-3:
        return "m"
    if a >= 1e-6:
        return "µ"
    return "n"


_HEADERS = ["Компонент", "|V|", "|I|", "|S|"]


class AcDialog(QDialog):
    def __init__(self, parent, rows: list[dict], frequency: float):
        super().__init__(parent)
        freq_str = f"{frequency:.4g}" if frequency >= 1 else f"{frequency * 1e3:.4g}m"
        self.setWindowTitle(f"Pulsar — Анализ по переменному току ({freq_str} Гц)")
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
        ncols = 4
        self._table = QTableWidget(len(rows), ncols)
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

            p = None
            if v is not None and c is not None:
                p = abs(v * c)

            name_item = QTableWidgetItem(f"  {name}")
            name_item.setFont(font_name)
            name_item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(i, 0, name_item)

            v_str = f"{_fmt_mag(v)}{_mag_unit(v)}V" if v is not None else "—"
            v_item = QTableWidgetItem(f"  {v_str}")
            v_item.setFont(font_val)
            v_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(i, 1, v_item)

            c_str = f"{_fmt_mag(c)}{_mag_unit(c)}A" if c is not None else "—"
            c_item = QTableWidgetItem(f"  {c_str}")
            c_item.setFont(font_val)
            c_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(i, 2, c_item)

            p_val = _fmt_mag(p)
            p_unit = _mag_unit(p)
            p_str = f"{p_val}{p_unit}VA" if p is not None else "—"
            p_item = QTableWidgetItem(f"  {p_str}")
            p_item.setFont(font_val)
            p_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(i, 3, p_item)

        layout.addWidget(self._table, 1)

        # bottom buttons
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)

        info = QLabel(f"Элементов: {len(rows)}  |  Частота: {freq_str} Гц")
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
        self._table.setColumnHidden(1, not self._cb_v.isChecked())
        self._table.setColumnHidden(2, not self._cb_i.isChecked())
        self._table.setColumnHidden(3, not self._cb_p.isChecked())

    def _copy_to_clipboard(self):
        lines = ["Компонент\t|V|\t|I|\t|S|"]
        for row in self._all_rows:
            v = row.get("voltage")
            c = row.get("current")
            p = abs(v * c) if v is not None and c is not None else None
            v_str = f"{v:.6e}" if v is not None else ""
            c_str = f"{c:.6e}" if c is not None else ""
            p_str = f"{p:.6e}" if p is not None else ""
            lines.append(f"{row['name']}\t{v_str}\t{c_str}\t{p_str}")
        QGuiApplication.clipboard().setText("\n".join(lines))
