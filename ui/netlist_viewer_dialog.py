from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QPushButton, QFileDialog, QMessageBox,
)
from PySide6.QtGui import QFont

from utils.spice_template import wrap_netlist_in_template


class NetlistViewerDialog(QDialog):
    """Диалог просмотра SPICE netlist"""

    def __init__(self, netlist_text: str, parent=None):
        super().__init__(parent)
        self._netlist_text = netlist_text
        self.setWindowTitle("Просмотр SPICE netlist")
        self.resize(700, 500)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QLabel("SPICE Netlist (результат экспорта из .sch)")
        header.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(header)

        self.viewer = QTextEdit()
        self.viewer.setReadOnly(True)
        self.viewer.setFont(QFont("Monospace", 10))
        self.viewer.setPlainText(self._netlist_text)
        self.viewer.setStyleSheet(
            "QTextEdit { "
            "background-color: #1e1e1e; "
            "color: #d4d4d4; "
            "border: 1px solid #333; "
            "}"
        )
        layout.addWidget(self.viewer)

        btn_layout = QHBoxLayout()
        copy_btn = QPushButton("Копировать")
        copy_btn.clicked.connect(lambda: self.viewer.selectAll() or self.viewer.copy())
        btn_layout.addWidget(copy_btn)

        save_btn = QPushButton("Сохранить как .cir…")
        save_btn.clicked.connect(self._save_netlist)
        btn_layout.addWidget(save_btn)

        btn_layout.addStretch()

        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)

        layout.addLayout(btn_layout)

    def _save_netlist(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить SPICE netlist",
            "",
            "SPICE Circuit (*.cir);;SPICE Netlist (*.sp);;All Files (*)",
        )
        if file_path:
            try:
                wrapped = wrap_netlist_in_template(
                    self._netlist_text,
                    circuit_name=Path(file_path).stem,
                )
                Path(file_path).write_text(wrapped)
                QMessageBox.information(self, "Успех", f"Netlist сохранён:\n{file_path}")
            except OSError as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{e}")
