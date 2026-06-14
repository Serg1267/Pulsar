"""
Диалог настроек программы SpiceEDA
Открывается через Вид → Настройки программы…
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget,
    QWidget, QGroupBox, QComboBox, QLabel,
    QDialogButtonBox, QCheckBox,
    QSpinBox, QFormLayout, QHBoxLayout, QButtonGroup, QRadioButton
)
from PySide6.QtCore import QSettings


class SettingsDialog(QDialog):
    """Диалог настроек программы"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки программы")
        self.resize(500, 450)
        self.settings = QSettings("SpiceEDA", "SpiceEDA")

        self._setup_ui()
        self._load_settings()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Вкладки настроек
        self.tabs = QTabWidget()

        # ─── Вкладка: Редактор ───
        editor_tab = self._create_editor_tab()
        self.tabs.addTab(editor_tab, "Редактор")

        # ─── Вкладка: Схема ───
        sch_tab = self._create_schematic_tab()
        self.tabs.addTab(sch_tab, "Схема")

        layout.addWidget(self.tabs)

        # Кнопки
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel |
            QDialogButtonBox.StandardButton.Apply
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply_settings)
        layout.addWidget(button_box)

    def _create_editor_tab(self):
        """Вкладка настроек редактора"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Шрифт
        font_group = QGroupBox("Шрифт редактора")
        font_layout = QFormLayout(font_group)

        self.font_family_combo = QComboBox()
        # Популярные моноширинные шрифты
        monospace_fonts = [
            "Monospace", "Consolas", "DejaVu Sans Mono", "Liberation Mono",
            "Ubuntu Mono", "Source Code Pro", "Fira Code", "JetBrains Mono",
            "Noto Sans Mono", "Courier New"
        ]
        self.font_family_combo.addItems(monospace_fonts)
        self.font_family_combo.setEditable(True)
        font_layout.addRow("Семейство:", self.font_family_combo)

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 32)
        self.font_size_spin.setValue(14)
        font_layout.addRow("Размер:", self.font_size_spin)

        layout.addWidget(font_group)

        # Опции
        options_group = QGroupBox("Опции редактора")
        options_layout = QVBoxLayout(options_group)

        self.line_numbers_check = QCheckBox("Показывать номера строк")
        options_layout.addWidget(self.line_numbers_check)

        self.auto_complete_check = QCheckBox("Автодополнение")
        options_layout.addWidget(self.auto_complete_check)

        layout.addWidget(options_group)

        # Заставка
        splash_group = QGroupBox("Запуск")
        splash_layout = QVBoxLayout(splash_group)
        self.splash_check = QCheckBox("Показывать заставку")
        splash_layout.addWidget(self.splash_check)
        layout.addWidget(splash_group)

        # Тема оформления
        theme_group = QGroupBox("Тема оформления")
        theme_layout = QFormLayout(theme_group)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Тёмная", "Светлая"])
        theme_layout.addRow("Тема:", self.theme_combo)
        layout.addWidget(theme_group)

        layout.addStretch()

        return widget

    def _create_schematic_tab(self):
        """Вкладка настроек схемы"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Сетка
        grid_group = QGroupBox("Сетка")
        grid_layout = QVBoxLayout(grid_group)

        self.grid_lines_radio = QRadioButton("Линии")
        self.grid_dots_radio = QRadioButton("Точки")
        self.grid_dots_radio.setChecked(False)

        grid_layout.addWidget(self.grid_lines_radio)
        grid_layout.addWidget(self.grid_dots_radio)

        layout.addWidget(grid_group)
        layout.addStretch()
        return widget

    def _load_settings(self):
        """Загрузить настройки из QSettings"""
        # Редактор
        saved_font = self.settings.value("editor/font_family", "")
        if saved_font:
            idx = self.font_family_combo.findText(saved_font)
            if idx >= 0:
                self.font_family_combo.setCurrentIndex(idx)
            else:
                self.font_family_combo.setEditText(saved_font)

        saved_font_size = self.settings.value("editor/font_size", 14, type=int)
        self.font_size_spin.setValue(saved_font_size)

        saved_line_numbers = self.settings.value("editor/line_numbers", "true").lower() == "true"
        self.line_numbers_check.setChecked(saved_line_numbers)

        saved_auto_complete = self.settings.value("editor/auto_complete", "true").lower() == "true"
        self.auto_complete_check.setChecked(saved_auto_complete)

        saved_splash = self.settings.value("splash/show", "true").lower() == "true"
        self.splash_check.setChecked(saved_splash)

        # Схема
        grid_dots = self.settings.value("grid/dots", "false").lower() == "true"
        self.grid_dots_radio.setChecked(grid_dots)
        self.grid_lines_radio.setChecked(not grid_dots)

        # Тема
        theme = self.settings.value("app/theme", "dark")
        self.theme_combo.setCurrentIndex(0 if theme == "dark" else 1)

    def _apply_settings(self):
        """Применить настройки (вызывается по кнопке Apply / Ok)"""
        # Редактор
        font_family = self.font_family_combo.currentText()
        self.settings.setValue("editor/font_family", font_family)
        self.settings.setValue("editor/font_size", self.font_size_spin.value())
        self.settings.setValue("editor/line_numbers", str(self.line_numbers_check.isChecked()))
        self.settings.setValue("editor/auto_complete", str(self.auto_complete_check.isChecked()))
        self.settings.setValue("splash/show", str(self.splash_check.isChecked()))

        # Схема
        self.settings.setValue("grid/dots", str(self.grid_dots_radio.isChecked()))

        # Тема
        self.settings.setValue("app/theme", "dark" if self.theme_combo.currentIndex() == 0 else "light")

    def accept(self):
        """Нажата Ok — применить и закрыть"""
        self._apply_settings()
        super().accept()
