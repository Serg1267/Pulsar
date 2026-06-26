"""Pulsar — Главное окно приложения.
"""

__version__ = "0.9.0"

import sys
import json
import os
import tempfile
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFileDialog,
    QMenuBar,
    QStatusBar,
    QMessageBox,
    QSplashScreen,
    QDialog,
    QInputDialog,
    QLabel,
    QPushButton,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QTimer, QSettings, QPointF
from PySide6.QtGui import QAction, QIcon, QPixmap, QKeySequence, QCursor, QPalette, QColor, QPainter
from EDA.app.items.directive_item import DirectiveItem

from ui.settings_dialog import SettingsDialog
from ui.unified_tabs import UnifiedTabWidget
from ui.netlist_viewer_dialog import NetlistViewerDialog
from ui.side_panel import ComponentPanel
from utils.spice_template import create_cir_template
from EDA.app.canvas import SchematicCanvas
from editor.spice_highlighter import SpiceHighlighter, DEFAULT_SCHEME
from simulator.ngspice_simulator import NGspiceSimulator
from simulator.netlist_validator import validate_netlist
from plotter.spice_plotter import SpicePlotterWindow




class PulsarMainWindow(QMainWindow):
    """Главное окно приложения — редактор .sch и .cir файлов."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Pulsar")
        self.resize(1200, 800)

        self._sim_output_buffer: list[str] = []

        # Simulation engine
        self._simulator = NGspiceSimulator()
        self._sim_output_queue: list = []
        self._sim_output_timer = QTimer(self)
        self._sim_output_timer.timeout.connect(self._process_output_queue)
        self._sim_output_timer.start(50)
        self._sim_progress_value = 0
        self._plot_window = None

        # .OP analysis (должно быть до _on_tab_changed)
        self._op_dialog = None
        self._op_connected_canvas = None
        self._dirty_connected_editor = None
        self._op_temp_file = None

        self._setup_ui()
        self._create_menu_bar()
        self._create_toolbar()
        self._create_status_bar()
        self._on_tab_changed(-1)

    def closeEvent(self, event):
        try:
            if not self._tabs.confirm_close_all():
                event.ignore()
                return
        except Exception as e:
            print(f"[WARN] confirm_close_all error: {e}")
        self._tabs.close_all_tabs()
        event.accept()

    def _setup_ui(self):
        self._tabs = UnifiedTabWidget(self)
        self._tabs.position_changed.connect(self._sch_on_position_changed)
        self._tabs.mode_changed.connect(self._sch_on_mode_changed)
        self._tabs.component_placed.connect(self._sch_on_component_placed)
        self._tabs.tabs_count_changed.connect(self._on_tabs_count_changed)
        self._tabs.cir_auto_exported.connect(self._reload_cir_tab_if_open)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self.setCentralWidget(self._tabs)

        self._comp_dock: QDockWidget | None = None

    def _create_menu_bar(self):
        menubar = self.menuBar()

        menu_font = menubar.font()
        menu_font.setPointSizeF(menu_font.pointSizeF() + 0.2)
        menubar.setFont(menu_font)

        # ─── Файл (always visible) ───
        file_menu = menubar.addMenu("Файл")

        self._new_sch_action = QAction("Создать файл .sch…", self)
        self._new_sch_action.setShortcut("Ctrl+N")
        self._new_sch_action.triggered.connect(self._new_file_dialog)
        file_menu.addAction(self._new_sch_action)

        self._new_cir_action = QAction("Создать файл .cir…", self)
        self._new_cir_action.setShortcut("Ctrl+Shift+N")
        self._new_cir_action.triggered.connect(self._new_cir_file)
        file_menu.addAction(self._new_cir_action)

        file_menu.addSeparator()

        open_action = QAction("Открыть…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_file)
        file_menu.addAction(open_action)

        file_menu.addSeparator()

        self._save_action = QAction("Сохранить", self)
        self._save_action.setShortcut("Ctrl+S")
        self._save_action.triggered.connect(self._save_current)
        self._save_action.setEnabled(False)
        file_menu.addAction(self._save_action)

        self._save_as_action = QAction("Сохранить как…", self)
        self._save_as_action.setShortcut("Ctrl+Shift+S")
        self._save_as_action.triggered.connect(self._save_current_as)
        self._save_as_action.setEnabled(False)
        file_menu.addAction(self._save_as_action)

        self._export_pdf_action = QAction("Сохранить схему в .pdf…", self)
        self._export_pdf_action.triggered.connect(self._export_pdf)
        self._export_pdf_action.setEnabled(False)
        file_menu.addAction(self._export_pdf_action)

        self._export_png_action = QAction("Сохранить схему в .png…", self)
        self._export_png_action.triggered.connect(self._export_png)
        self._export_png_action.setEnabled(False)
        file_menu.addAction(self._export_png_action)

        self._sch_export_cir_action = QAction("Экспорт SPICE netlist…", self)
        self._sch_export_cir_action.triggered.connect(self._sch_export_cir)
        self._sch_export_cir_action.setEnabled(False)
        file_menu.addAction(self._sch_export_cir_action)

        self._sch_export_tedax_action = QAction("Экспорт tEDAx (pcb-rnd)…", self)
        self._sch_export_tedax_action.triggered.connect(self._sch_export_tedax)
        self._sch_export_tedax_action.setEnabled(False)
        file_menu.addAction(self._sch_export_tedax_action)

        file_menu.addSeparator()

        exit_action = QAction("Выход", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # ─── Правка ───

        self._edit_menu = menubar.addMenu("Правка")

        self._cir_undo_action = QAction(QIcon(str(Path(__file__).parent / "resources" / "icons" / "gschem-undo.png")), "Отменить", self)
        self._cir_undo_action.setShortcut("Ctrl+Z")
        self._cir_undo_action.triggered.connect(self._edit_undo)
        self._edit_menu.addAction(self._cir_undo_action)

        self._cir_redo_action = QAction(QIcon(str(Path(__file__).parent / "resources" / "icons" / "gschem-redo.png")), "Повторить", self)
        self._cir_redo_action.setShortcut("Ctrl+Shift+Z")
        self._cir_redo_action.triggered.connect(self._edit_redo)
        self._edit_menu.addAction(self._cir_redo_action)

        self._edit_menu.addSeparator()

        self._cir_cut_action = QAction("Вырезать", self)
        self._cir_cut_action.triggered.connect(self._edit_cut)
        self._edit_menu.addAction(self._cir_cut_action)

        self._cir_copy_action = QAction("Копировать", self)
        self._cir_copy_action.triggered.connect(self._edit_copy)
        self._edit_menu.addAction(self._cir_copy_action)

        self._cir_paste_action = QAction("Вставить", self)
        self._cir_paste_action.triggered.connect(self._edit_paste)
        self._edit_menu.addAction(self._cir_paste_action)

        self._edit_menu.addSeparator()

        self._cir_select_all_action = QAction("Выделить всё", self)
        self._cir_select_all_action.setShortcut("Ctrl+A")
        self._cir_select_all_action.triggered.connect(self._edit_select_all)
        self._edit_menu.addAction(self._cir_select_all_action)

        # ─── Вид (always visible, content varies) ───
        self._view_menu = menubar.addMenu("Вид")

        # Cir-specific view items (hidden by default)
        self._cir_theme_menu = self._view_menu.addMenu("Цветовая схема")
        self._cir_theme_menu.menuAction().setVisible(False)
        self._cir_theme_actions: dict[str, QAction] = {}
        for scheme_name in SpiceHighlighter.available_schemes():
            action = QAction(scheme_name, self, checkable=True)
            action.setChecked(scheme_name == DEFAULT_SCHEME)
            action.triggered.connect(lambda checked, name=scheme_name: self._tabs.apply_theme(name))
            self._cir_theme_menu.addAction(action)
            self._cir_theme_actions[scheme_name] = action

        self._cir_line_numbers_action = QAction("Номера строк", self, checkable=True)
        self._cir_line_numbers_action.setChecked(True)
        self._cir_line_numbers_action.triggered.connect(self._cir_toggle_line_numbers)
        self._cir_line_numbers_action.setVisible(False)
        self._view_menu.addAction(self._cir_line_numbers_action)

        self._cir_terminal_action = QAction("Терминал NGspice", self, checkable=True)
        self._cir_terminal_action.setChecked(True)
        self._cir_terminal_action.setShortcut("Ctrl+T")
        self._cir_terminal_action.triggered.connect(self._cir_toggle_terminal)
        self._cir_terminal_action.setVisible(False)
        self._view_menu.addAction(self._cir_terminal_action)

        self._comp_panel_action = QAction("Панель компонентов", self, checkable=True)
        self._comp_panel_action.setChecked(False)
        self._comp_panel_action.triggered.connect(self._toggle_comp_panel)
        self._comp_panel_action.setVisible(False)
        self._view_menu.addAction(self._comp_panel_action)

        self._view_menu.addSeparator()

        settings_action = QAction("Настройки программы…", self)
        settings_action.setShortcut("Ctrl+,")
        settings_action.triggered.connect(self._open_settings)
        self._view_menu.addAction(settings_action)

        # ─── Симуляция (hidden by default) ───
        self._sim_menu = menubar.addMenu("Симуляция")
        self._sim_menu.menuAction().setVisible(False)

        self._sim_run_action = QAction("Запуск", self)
        self._sim_run_action.setShortcut("F5")
        self._sim_run_action.triggered.connect(self._run_simulation)
        self._sim_menu.addAction(self._sim_run_action)

        self._sim_stop_action = QAction("Остановить", self)
        self._sim_stop_action.setShortcut("Ctrl+Shift+S")
        self._sim_stop_action.setEnabled(False)
        self._sim_stop_action.triggered.connect(self._stop_simulation)
        self._sim_menu.addAction(self._sim_stop_action)

        # ─── Анализ ───
        self._analysis_menu = menubar.addMenu("Анализ")

        self._sch_run_action = QAction("Пуск", self)
        self._sch_run_action.setShortcut("F5")
        self._sch_run_action.triggered.connect(self._sch_run_simulation)
        self._analysis_menu.addAction(self._sch_run_action)

        self._analysis_op_action = QAction("Анализ по постоянному току", self)
        self._analysis_op_action.triggered.connect(self._toggle_op)
        self._analysis_menu.addAction(self._analysis_op_action)

        # ─── Схема (hidden by default) ───
        self._sch_menu = menubar.addMenu("Схема")
        self._sch_menu.menuAction().setVisible(False)

        self._sch_new_action = QAction("Новая схема", self)
        self._sch_new_action.setShortcut("Ctrl+Shift+E")
        self._sch_new_action.triggered.connect(self._sch_new_tab)
        self._sch_menu.addAction(self._sch_new_action)

        self._sch_add_component_action = QAction("Добавить компонент…", self)
        self._sch_add_component_action.setShortcut("Ctrl+K")
        self._sch_add_component_action.triggered.connect(self._sch_add_component)
        self._sch_add_component_action.setEnabled(False)
        self._sch_menu.addAction(self._sch_add_component_action)

        self._sch_add_directive_action = QAction("Добавить директиву…", self)
        self._sch_add_directive_action.setShortcut(".")
        self._sch_add_directive_action.triggered.connect(self._sch_add_directive)
        self._sch_add_directive_action.setEnabled(False)
        self._sch_menu.addAction(self._sch_add_directive_action)

        self._sch_add_node_label_action = QAction("Добавить метку узла…", self)
        self._sch_add_node_label_action.setShortcut("L")
        self._sch_add_node_label_action.triggered.connect(self._sch_add_node_label)
        self._sch_add_node_label_action.setEnabled(False)
        self._sch_menu.addAction(self._sch_add_node_label_action)

        self._sch_add_text_action = QAction("Добавить текст…", self)
        self._sch_add_text_action.setShortcut("T")
        self._sch_add_text_action.triggered.connect(self._sch_add_text)
        self._sch_add_text_action.setEnabled(False)
        self._sch_menu.addAction(self._sch_add_text_action)

        self._sch_add_rect_action = QAction("Добавить прямоугольник…", self)
        self._sch_add_rect_action.triggered.connect(self._sch_add_rect)
        self._sch_add_rect_action.setEnabled(False)
        self._sch_menu.addAction(self._sch_add_rect_action)

        self._sch_add_circle_action = QAction("Добавить окружность…", self)
        self._sch_add_circle_action.triggered.connect(self._sch_add_circle)
        self._sch_add_circle_action.setEnabled(False)
        self._sch_menu.addAction(self._sch_add_circle_action)

        self._sch_menu.addSeparator()

        self._view_netlist_action = QAction("Просмотр SPICE netlist…", self)
        self._view_netlist_action.setShortcut("Ctrl+Shift+V")
        self._view_netlist_action.triggered.connect(self._view_netlist_dialog)
        self._view_netlist_action.setEnabled(True)
        self._sch_menu.addAction(self._view_netlist_action)

        # ─── Справка (самый правый пункт) ───
        help_menu = menubar.addMenu("Справка")
        about_action = QAction("О программе", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self):
        QMessageBox.about(self, "О программе Pulsar",
            "Pulsar — редактор принципиальных схем и SPICE-симулятор.\n\n"
            "Основан на PySide6, matplotlib и ngspice.\n"
            "Версия 0.9.0")

    def _create_toolbar(self):
        from PySide6.QtWidgets import QToolBar
        from PySide6.QtGui import QIcon
        from PySide6.QtCore import QSize

        icons = Path(__file__).parent / "resources" / "icons"

        tb = QToolBar("Инструменты")
        tb.setIconSize(QSize(24, 24))
        tb.setMovable(False)
        self.addToolBar(tb)

        class DotSep(QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.setFixedWidth(10)
                self.setFixedHeight(24)
            def paintEvent(self, ev):
                p = QPainter(self)
                p.setPen(QColor("#4488ff"))
                for y in range(3, 24, 6):
                    p.drawPoint(5, y)
                p.end()

        def _dot_sep():
            tb.addWidget(DotSep())

        new_action = QAction(QIcon(str(icons / "gschem-new.png")), "Новый файл", self)
        new_action.setShortcut("Ctrl+N")
        new_action.triggered.connect(self._new_file_dialog)
        tb.addAction(new_action)

        open_action = QAction(QIcon(str(icons / "gschem-open.png")), "Открыть", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_file)
        tb.addAction(open_action)

        self._save_action.setIcon(QIcon(str(icons / "gschem-save.png")))
        tb.addAction(self._save_action)

        self._wire_mode_action = QAction(QIcon(str(icons / "icons8-ball-point-pen-50.ico")), "Нарисовать провод", self)
        self._wire_mode_action.setCheckable(True)
        self._wire_mode_action.setEnabled(False)
        self._wire_mode_action.triggered.connect(self._toggle_wire_mode)

        _dot_sep()

        self._rect_action = QAction(QIcon(str(icons / "insert-box.png")), "Прямоугольник", self)
        self._rect_action.setCheckable(True)
        self._rect_action.setEnabled(False)
        self._rect_action.triggered.connect(self._toggle_rect_mode)
        tb.addAction(self._rect_action)

        self._circle_action = QAction(QIcon(str(icons / "insert-circle.png")), "Окружность", self)
        self._circle_action.setCheckable(True)
        self._circle_action.setEnabled(False)
        self._circle_action.triggered.connect(self._toggle_circle_mode)
        tb.addAction(self._circle_action)

        _dot_sep()

        # ── Быстрое размещение компонентов ──
        self._comp_actions = []

        def _make_comp_action(sym_id, icon_name, tooltip):
            ico = QIcon()
            ico.addPixmap(QPixmap(str(icons / f"{icon_name}.png")), QIcon.Mode.Normal)
            ico.addPixmap(QPixmap(str(icons / f"{icon_name}_dim.png")), QIcon.Mode.Disabled)
            act = QAction(ico, tooltip, self)
            act.setEnabled(False)
            act.triggered.connect(lambda checked, sid=sym_id: self._start_comp_placement(sid))
            tb.addAction(act)
            self._comp_actions.append(act)

        ico_r = QIcon(str(icons / "icon-resistor.ico"))
        act_r = QAction(ico_r, "Резистор (R)", self)
        act_r.setEnabled(False)
        act_r.triggered.connect(lambda: self._start_comp_placement("resistor-2"))
        tb.addAction(act_r)
        self._comp_actions.append(act_r)
        ico_c = QIcon(str(icons / "icon-capacitor.ico"))
        act_c = QAction(ico_c, "Конденсатор (C)", self)
        act_c.setEnabled(False)
        act_c.triggered.connect(lambda: self._start_comp_placement("capacitor-1"))
        tb.addAction(act_c)
        self._comp_actions.append(act_c)
        # Транзистор из .ico
        ico_t = QIcon(str(icons / "icon-transistor.ico"))
        act_t = QAction(ico_t, "Транзистор (Q)", self)
        act_t.setEnabled(False)
        act_t.triggered.connect(lambda: self._start_comp_placement("npn-1"))
        tb.addAction(act_t)
        self._comp_actions.append(act_t)
        ico_d = QIcon(str(icons / "icon-diode.ico"))
        act_d = QAction(ico_d, "Диод (D)", self)
        act_d.setEnabled(False)
        act_d.triggered.connect(lambda: self._start_comp_placement("diode-1"))
        tb.addAction(act_d)
        self._comp_actions.append(act_d)
        ico_g = QIcon(str(icons / "icon-ground.ico"))
        act_g = QAction(ico_g, "Земля (G)", self)
        act_g.setEnabled(False)
        act_g.triggered.connect(lambda: self._start_comp_placement("gnd-1"))
        tb.addAction(act_g)
        self._comp_actions.append(act_g)

        ico_el = QIcon(str(icons / "icons8-electronics-50.ico"))
        act_el = QAction(ico_el, "Компоненты…", self)
        act_el.setEnabled(False)
        act_el.triggered.connect(self._sch_add_component)
        tb.addAction(act_el)
        self._comp_actions.append(act_el)

        _dot_sep()

        def _icon_with_dim(name):
            """QIcon: Normal=red, Disabled=dim"""
            ico = QIcon()
            ico.addPixmap(QPixmap(str(icons / f"{name}.png")), QIcon.Mode.Normal)
            ico.addPixmap(QPixmap(str(icons / f"{name}_dim.png")), QIcon.Mode.Disabled)
            return ico

        self._jump_action = QAction(QIcon(str(icons / "icon-rotate-l.ico")), "Повернуть влево…", self)
        self._jump_action.setToolTip("Повернуть выделенный компонент влево на 90°")
        self._jump_action.setEnabled(False)
        self._jump_action.triggered.connect(self._rotate_selected_left)
        tb.addAction(self._jump_action)

        self._jump_mirror_action = QAction(QIcon(str(icons / "icon-rotate-r.ico")), "Повернуть вправо…", self)
        self._jump_mirror_action.setToolTip("Повернуть выделенный компонент вправо на 90°")
        self._jump_mirror_action.setEnabled(False)
        self._jump_mirror_action.triggered.connect(self._rotate_selected_right)
        tb.addAction(self._jump_mirror_action)

        self._flip_h_action = QAction(QIcon(str(icons / "icon-flip-h.ico")), "Отразить по горизонтали…", self)
        self._flip_h_action.setToolTip("Отразить выделенный компонент по горизонтали (X)")
        self._flip_h_action.setEnabled(False)
        self._flip_h_action.triggered.connect(self._flip_selected_h)
        tb.addAction(self._flip_h_action)

        self._flip_v_action = QAction(QIcon(str(icons / "icon-flip-v.ico")), "Отразить по вертикали…", self)
        self._flip_v_action.setToolTip("Отразить выделенный компонент по вертикали (Y)")
        self._flip_v_action.setEnabled(False)
        self._flip_v_action.triggered.connect(self._flip_selected_v)
        tb.addAction(self._flip_v_action)

        _dot_sep()
        tb.addAction(self._wire_mode_action)

        self._toolbar_text_action = QAction(QIcon(str(icons / "icons8-type-50.ico")), "Добавить текст", self)
        self._toolbar_text_action.setEnabled(False)
        self._toolbar_text_action.triggered.connect(self._sch_add_text)
        tb.addAction(self._toolbar_text_action)

        self._toolbar_node_label_action = QAction(QIcon(str(icons / "icons8-добавить-метку-50.ico")), "Добавить метку узла", self)
        self._toolbar_node_label_action.setEnabled(False)
        self._toolbar_node_label_action.triggered.connect(self._sch_add_node_label)
        tb.addAction(self._toolbar_node_label_action)

        self._toolbar_code_file_action = QAction(QIcon(str(icons / "icons8-code-file-50.ico")), ".SPICE директива", self)
        self._toolbar_code_file_action.setEnabled(False)
        self._toolbar_code_file_action.triggered.connect(self._sch_add_directive)
        tb.addAction(self._toolbar_code_file_action)

        self._toolbar_netlist_action = QAction(QIcon(str(icons / "icons8-режим-одной-страницы-50.ico")), "Просмотр SPICE netlist", self)
        self._toolbar_netlist_action.setEnabled(False)
        self._toolbar_netlist_action.triggered.connect(self._view_netlist_dialog)
        tb.addAction(self._toolbar_netlist_action)

    def _update_jump_actions(self):
        canvas = self._tabs.current_canvas()
        has_sel = bool(canvas and canvas._selected_items)
        self._jump_action.setEnabled(has_sel)
        self._jump_mirror_action.setEnabled(has_sel)
        self._flip_h_action.setEnabled(has_sel)
        self._flip_v_action.setEnabled(has_sel)

    def _rotate_selected_left(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas._rotate_selected(-90.0)

    def _rotate_selected_right(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas._rotate_selected(90.0)

    def _flip_selected_h(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas._flip_selected_horizontal()
            canvas.modified.emit()

    def _flip_selected_v(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas._flip_selected_vertical()
            canvas.modified.emit()

    def _toggle_wire_mode(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        canvas._wire_draw_mode = self._wire_mode_action.isChecked()
        if canvas._wire_draw_mode:
            canvas._wire_mode = False
            canvas.setCursor(canvas._pencil_cursor)
            canvas._router.reset()
            canvas._clear_routing_preview()
            canvas._last_segment_item = None
            vp = canvas.viewport()
            pos = canvas.mapToScene(vp.mapFromGlobal(QCursor.pos()))
            g = canvas.GRID_SPACING
            canvas._show_crosshair(QPointF(round(pos.x() / g) * g, round(pos.y() / g) * g))
        else:
            canvas.unsetCursor()
            canvas._hide_crosshair()
            canvas._router.reset()
            canvas._clear_routing_preview()
            canvas._clear_wire_hover()
            canvas._last_segment_item = None
        canvas.setFocus()
        canvas.mode_changed.emit("SEGMENT" if canvas._wire_draw_mode else "")

    def _toggle_rect_mode(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        if self._rect_action.isChecked():
            canvas.start_rect_placement()
            canvas.setFocus()
        else:
            canvas._cancel_rect_placement()

    def _toggle_circle_mode(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        if self._circle_action.isChecked():
            canvas.start_circle_placement()
            canvas.setFocus()
        else:
            canvas._cancel_circle_placement()

    def _start_comp_placement(self, sym_id: str):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        canvas._cancel_placement()
        canvas._cancel_node_label_placement()
        canvas._cancel_text_placement()
        canvas._cancel_rect_placement()
        canvas._cancel_circle_placement()
        if canvas._wire_draw_mode:
            canvas._wire_draw_mode = False
            canvas._router.reset()
            canvas._clear_routing_preview()
            canvas._last_segment_item = None
        if canvas._wire_mode:
            canvas._wire_mode = False
        canvas.mode_changed.emit("")
        canvas.drag_placement_started.emit(sym_id)
        canvas.setCursor(canvas._hand_cursor)
        canvas.setFocus()

    def _create_status_bar(self):
        self.statusBar().showMessage("Готово")

    def _show_notification(self, message: str, timeout: int = 5000):
        self.statusBar().showMessage(message, timeout)

    # ─── Переключение активного редактора ───

    def _current_tab_widget(self):
        return self._tabs

    # ─── Открытие файлов ───

    def _open_file(self):
        fp, _ = QFileDialog.getOpenFileName(
            self, "Открыть файл", "",
            "Поддерживаемые (*.sch *.cir *.sp);;"
            "Schematic (*.sch);;SPICE Circuit (*.cir *.sp);;All Files (*)",
        )
        if not fp:
            return
        path = Path(fp)
        if not path.exists():
            QMessageBox.warning(self, "Ошибка", f"Файл не найден:\n{path}")
            return
        self._tabs.open_tab(str(path))

    # ─── Сохранение ───

    def _save_current(self):
        self._tabs.save_current_tab()
        self._on_dirty_changed()

    def _save_current_as(self):
        self._tabs.save_current_tab_as()
        self._on_dirty_changed()

    # ─── События вкладок ───

    def _ensure_comp_dock(self):
        if self._comp_dock is None:
            from PySide6.QtWidgets import QDockWidget
            panel = ComponentPanel()
            self._comp_dock = QDockWidget(self)
            self._comp_dock.setFeatures(QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
            self._comp_dock.setTitleBarWidget(QWidget())
            self._comp_dock.setWidget(panel)
            self._comp_dock.setMinimumWidth(200)
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._comp_dock)

    def _toggle_comp_panel(self):
        if self._comp_panel_action.isChecked():
            self._ensure_comp_dock()
            self._comp_dock.show()
        else:
            if self._comp_dock:
                self._comp_dock.hide()

    def _update_menus_for_mode(self):
        has_sch = self._tabs.has_sch_tabs()
        has_cir = self._tabs.has_cir_tabs()

        self._sch_menu.menuAction().setVisible(has_sch)

        self._sim_menu.menuAction().setVisible(has_cir)

        self._cir_theme_menu.menuAction().setVisible(has_cir)
        self._cir_line_numbers_action.setVisible(has_cir)
        self._cir_terminal_action.setVisible(has_cir)

        if has_cir:
            hl = self._tabs.current_highlighter()
            if hl is not None:
                name = hl.scheme_name
                for sn, act in self._cir_theme_actions.items():
                    act.setChecked(sn == name)

    def _cir_toggle_line_numbers(self, visible: bool):
        self._tabs.toggle_line_numbers(visible)

    def _cir_toggle_terminal(self, visible: bool):
        self._tabs.toggle_terminal(visible)

    def _on_dirty_changed(self):
        self._update_save_actions(self._tabs.count() > 0)

    def _update_save_actions(self, any_open: bool):
        dirty = self._tabs.current_page_dirty() if any_open else False
        self._save_action.setEnabled(dirty)
        self._save_as_action.setEnabled(any_open)
        self._export_pdf_action.setEnabled(self._tabs.has_sch_tabs())
        self._export_png_action.setEnabled(self._tabs.has_sch_tabs())

    # ── делегаты для меню Редактировать ──

    def _edit_undo(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas._undo()
            return
        editor = self._tabs.current_editor()
        if editor:
            editor.undo()

    def _edit_redo(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas._redo()
            return
        editor = self._tabs.current_editor()
        if editor:
            editor.redo()

    def _edit_cut(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas.cut_selected()
            return
        editor = self._tabs.current_editor()
        if editor:
            editor.cut()

    def _edit_copy(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas.copy_selected()
            return
        editor = self._tabs.current_editor()
        if editor:
            editor.copy()

    def _edit_paste(self):
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas.paste_from_clipboard()
            return
        editor = self._tabs.current_editor()
        if editor:
            editor.paste()

    def _edit_select_all(self):
        editor = self._tabs.current_editor()
        if editor:
            editor.selectAll()
            return
        canvas = self._tabs.current_canvas()
        if canvas:
            canvas._select_all()

    def _on_tab_changed(self, index: int):
        # Отключить старые сигналы
        if self._op_connected_canvas is not None:
            try:
                self._op_connected_canvas.modified.disconnect(self._reset_op)
                self._op_connected_canvas.modified.disconnect(self._on_dirty_changed)
                self._op_connected_canvas.selection_changed.disconnect(self._update_jump_actions)
            except Exception:
                pass
            self._op_connected_canvas = None
        if self._dirty_connected_editor is not None:
            try:
                self._dirty_connected_editor.textChanged.disconnect(self._on_dirty_changed)
            except Exception:
                pass
            self._dirty_connected_editor = None

        page_type = self._tabs.current_page_type()
        is_cir = page_type == 'cir'
        is_sch = page_type == 'sch'
        self._wire_mode_action.setEnabled(is_sch)
        self._toolbar_text_action.setEnabled(is_sch)
        self._toolbar_node_label_action.setEnabled(is_sch)
        self._toolbar_code_file_action.setEnabled(is_sch)
        self._toolbar_netlist_action.setEnabled(is_sch)
        self._cir_cut_action.setEnabled(is_cir or is_sch)
        self._cir_copy_action.setEnabled(is_cir or is_sch)
        self._cir_paste_action.setEnabled(is_cir or is_sch)

        # Подключить сигналы dirty для новой вкладки
        if is_sch:
            canvas = self._tabs.current_canvas()
            if canvas is not None:
                canvas.modified.connect(self._reset_op)
                self._op_connected_canvas = canvas
                canvas.modified.connect(self._on_dirty_changed)
                canvas.selection_changed.connect(self._update_jump_actions)
        elif is_cir:
            editor = self._tabs.current_editor()
            if editor is not None:
                editor.textChanged.connect(self._on_dirty_changed)
                self._dirty_connected_editor = editor

        self._update_save_actions(self._tabs.count() > 0)
        self._update_jump_actions()

        is_sch_now = self._tabs.current_page_type() == 'sch'
        self._sch_add_component_action.setEnabled(is_sch_now)
        self._sch_add_directive_action.setEnabled(is_sch_now)
        self._sch_add_node_label_action.setEnabled(is_sch_now)
        self._sch_add_text_action.setEnabled(is_sch_now)
        self._rect_action.setEnabled(is_sch_now)
        self._circle_action.setEnabled(is_sch_now)
        self._sch_add_rect_action.setEnabled(is_sch_now)
        self._sch_add_circle_action.setEnabled(is_sch_now)
        self._sch_export_cir_action.setEnabled(is_sch_now)
        self._sch_export_tedax_action.setEnabled(is_sch_now)
        for act in self._comp_actions:
            act.setEnabled(is_sch_now)

        self._comp_panel_action.setVisible(self._tabs.has_sch_tabs())
        if is_sch_now and self._comp_panel_action.isChecked():
            self._ensure_comp_dock()
            self._comp_dock.show()
        elif self._comp_dock:
            self._comp_dock.hide()

    def _on_tabs_count_changed(self, count: int):
        has_sch = self._tabs.has_sch_tabs()
        is_sch_now = self._tabs.current_page_type() == 'sch'
        self._comp_panel_action.setVisible(has_sch)
        if has_sch and is_sch_now and self._comp_panel_action.isChecked():
            self._ensure_comp_dock()
            self._comp_dock.show()
        else:
            if not has_sch:
                self._comp_panel_action.setChecked(False)
            if self._comp_dock:
                self._comp_dock.hide()
        has_sch = self._tabs.current_page_type() == 'sch'
        self._sch_add_component_action.setEnabled(has_sch)
        self._sch_add_directive_action.setEnabled(has_sch)
        self._sch_add_node_label_action.setEnabled(has_sch)
        self._sch_add_text_action.setEnabled(has_sch)
        self._toolbar_text_action.setEnabled(has_sch)
        self._toolbar_node_label_action.setEnabled(has_sch)
        self._toolbar_code_file_action.setEnabled(has_sch)
        self._toolbar_netlist_action.setEnabled(has_sch)
        self._rect_action.setEnabled(has_sch)
        self._circle_action.setEnabled(has_sch)
        self._sch_add_rect_action.setEnabled(has_sch)
        self._sch_add_circle_action.setEnabled(has_sch)
        self._sch_export_cir_action.setEnabled(has_sch)
        self._sch_export_tedax_action.setEnabled(has_sch)
        self._wire_mode_action.setEnabled(has_sch)
        for act in self._comp_actions:
            act.setEnabled(has_sch)
        self._update_menus_for_mode()
        self._update_save_actions(count > 0)
        self._update_jump_actions()

    def _reload_cir_tab_if_open(self, cir_path: str):
        """Перезагрузить вкладку .cir если она уже открыта. Не открывает новую."""
        rp = Path(cir_path).resolve()
        for i in range(self._tabs.count()):
            p = self._tabs.widget(i)
            if hasattr(p, 'page_type') and p.page_type == 'cir' and p.filepath:
                if Path(p.filepath).resolve() == rp:
                    p.load_file(cir_path)
                    self._tabs.setTabText(i, p.tab_label())
                    return

    # ─── Схема ───

    def _new_file_dialog(self):
        """Диалог «Новый файл» — выбор .sch или .cir, пути и имени."""
        fp, selected_filter = QFileDialog.getSaveFileName(
            self, "Новый файл", "",
            "Schematic (*.sch);;SPICE Circuit (*.cir *.sp)",
            "Schematic (*.sch)",
        )
        if not fp:
            return
        if '.sch' in selected_filter:
            if not fp.endswith('.sch'):
                fp += '.sch'
            import json as _json
            Path(fp).write_text(_json.dumps({
                "version": 2, "format": "spiceeda-schematic",
                "components": [], "wires": [], "junctions": [],
                "directives": [], "node_labels": [], "texts": [], "rectangles": [],
            }), encoding='utf-8')
            self._tabs.open_sch_tab(str(Path(fp)))
        else:
            if not (fp.endswith('.cir') or fp.endswith('.sp')):
                fp += '.cir'
            Path(fp).write_text('', encoding='utf-8')
            self._tabs.open_cir_tab(str(Path(fp)))

    def _new_cir_file(self):
        fp, selected_filter = QFileDialog.getSaveFileName(
            self, "Новый SPICE-файл", "",
            "SPICE Circuit (*.cir *.sp)",
            "SPICE Circuit (*.cir *.sp)",
        )
        if not fp:
            return
        if not (fp.endswith('.cir') or fp.endswith('.sp')):
            fp += '.cir'
        name = Path(fp).stem
        text = create_cir_template(circuit_name=name, analysis_type="TRAN")
        Path(fp).write_text(text, encoding='utf-8')
        self._tabs.open_cir_tab(str(Path(fp)))

    def _sch_new_tab(self):
        self._tabs.new_sch_tab()

    def _sch_add_component(self):
        self._tabs.add_component()

    def _sch_add_directive(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        text, ok = QInputDialog.getText(
            self, "Добавить директиву",
            "SPICE-директива (например .model, .tran, .ic):")
        if ok and text.strip():
            canvas.start_directive_placement(text.strip())

    def _sch_add_node_label(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        text, ok = QInputDialog.getText(
            self, "Метка узла",
            "Имя узла (латиница + цифры, напр. CLK, VOUT, N$001):")
        if ok and text.strip():
            t = text.strip()
            import re
            if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', t):
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Ошибка",
                    "Имя узла должно начинаться с буквы и содержать только "
                    "латинские буквы, цифры и знак подчёркивания.")
                return
            canvas.start_node_label_placement(t)

    def _sch_add_text(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        from PySide6.QtWidgets import QFontComboBox, QSpinBox, QTextEdit
        dialog = QDialog(self)
        dialog.setWindowTitle("Добавить текст")
        layout = QVBoxLayout(dialog)
        font_layout = QHBoxLayout()
        font_layout.addWidget(QLabel("Шрифт:"))
        font_combo = QFontComboBox()
        font_layout.addWidget(font_combo)
        font_layout.addWidget(QLabel("Размер:"))
        size_spin = QSpinBox()
        size_spin.setRange(8, 200)
        size_spin.setValue(80)
        font_layout.addWidget(size_spin)
        layout.addLayout(font_layout)
        text_edit = QTextEdit()
        text_edit.setPlaceholderText("Введите текст…")
        text_edit.setMinimumHeight(100)
        layout.addWidget(text_edit)
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton("Отмена")
        btn_cancel.clicked.connect(dialog.reject)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addStretch()
        btn_insert = QPushButton("Вставить")
        btn_insert.clicked.connect(dialog.accept)
        btn_layout.addWidget(btn_insert)
        layout.addLayout(btn_layout)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            text = text_edit.toPlainText()
            if text.strip():
                family = font_combo.currentFont().family()
                size = size_spin.value()
                canvas.start_text_placement(text.strip(), family, size)

    def _sch_add_rect(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        canvas.start_rect_placement()

    def _sch_add_circle(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        canvas.start_circle_placement()

    def _sch_export_cir(self):
        from PySide6.QtWidgets import QFileDialog
        content = self._tabs.export_cir()
        if content is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить SPICE netlist", "", "SPICE (*.cir);;Все файлы (*)")
        if path:
            if not path.lower().endswith(".cir"):
                path += ".cir"
            Path(path).write_text(content)
            self._show_notification(f"SPICE netlist сохранён: {path}")

    def _sch_export_tedax(self):
        from PySide6.QtWidgets import QFileDialog
        content = self._tabs.export_tedax()
        if content is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить tEDAx netlist", "", "tEDAx (*.tdx);;Все файлы (*)")
        if path:
            if not path.lower().endswith(".tdx"):
                path += ".tdx"
            Path(path).write_text(content)
            self._show_notification(f"tEDAx netlist сохранён: {path}")

    def _export_pdf(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить схему в .pdf", "", "PDF (*.pdf);;Все файлы (*)")
        if not path:
            return
        from PySide6.QtGui import QPainter, QPdfWriter, QImage, QPageSize, QColor, QPen
        from PySide6.QtCore import QSizeF, QRectF
        import math

        scene = canvas._scene
        rect = scene.itemsBoundingRect()
        if rect.isEmpty():
            self._show_notification("Схема пуста")
            return
        screen_dpi = 96
        margin = 400.0
        rect.adjust(-margin, -margin, margin, margin)
        zoom = canvas._zoom
        w = max(1, int(rect.width() * zoom))
        h = max(1, int(rect.height() * zoom))

        dpm = int(screen_dpi / 0.0254 + 0.5)
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.setDotsPerMeterX(dpm)
        img.setDotsPerMeterY(dpm)
        img.fill(QColor("#1E1E1E"))

        ip = QPainter(img)
        ip.setRenderHint(QPainter.Antialiasing)

        grid_step = 100.0
        ip.setPen(QPen(QColor("#2a2a2a"), 0.0))
        gx = math.floor(rect.left() / grid_step) * grid_step
        while gx <= rect.right():
            ix = (gx - rect.left()) / rect.width() * w
            ip.drawLine(int(ix), 0, int(ix), h)
            gx += grid_step
        gy = math.floor(rect.top() / grid_step) * grid_step
        while gy <= rect.bottom():
            iy = (gy - rect.top()) / rect.height() * h
            ip.drawLine(0, int(iy), w, int(iy))
            gy += grid_step

        scene.render(ip, QRectF(0, 0, w, h), rect)
        ip.end()

        flipped = img.mirrored(False, True)

        writer = QPdfWriter(path)
        writer.setResolution(300)
        writer.setPageSize(QPageSize(QSizeF(297, 210), QPageSize.Millimeter))
        painter = QPainter()
        if painter.begin(writer):
            pw = painter.device().width()
            ph = painter.device().height()
            pdf_w = w * 300.0 / screen_dpi
            pdf_h = h * 300.0 / screen_dpi
            if pdf_w > pw or pdf_h > ph:
                fit = min(pw / pdf_w, ph / pdf_h)
                pdf_w *= fit
                pdf_h *= fit
            cx = (pw - pdf_w) / 2
            cy = (ph - pdf_h) / 2
            painter.drawImage(QRectF(cx, cy, pdf_w, pdf_h), flipped)
            painter.end()
            self._show_notification(f"PDF сохранён (тёмный, сетка): {path}")
        else:
            self._show_notification("Ошибка сохранения PDF")

    def _export_png(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            return
        from PySide6.QtWidgets import QFileDialog, QMessageBox
        from PySide6.QtGui import QPainter, QImage, QColor, QPen
        from PySide6.QtCore import QRectF
        from EDA.app.items.colors import set_light_theme, is_light_theme
        import math

        msg = QMessageBox(self)
        msg.setWindowTitle("Сохранение PNG")
        msg.setText("Выберите режим изображения:")
        btn_color = msg.addButton("Цветной", QMessageBox.ButtonRole.AcceptRole)
        btn_bw = msg.addButton("Чёрно-белый", QMessageBox.ButtonRole.AcceptRole)
        msg.exec()
        color_mode = msg.clickedButton() == btn_color

        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить схему в .png", "", "PNG (*.png);;Все файлы (*)")
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"

        scene = canvas._scene
        rect = scene.itemsBoundingRect()
        if rect.isEmpty():
            self._show_notification("Схема пуста")
            return
        margin = 400.0
        rect.adjust(-margin, -margin, margin, margin)

        SCREEN_DPI = 96
        min_scale = 300.0 / 1000.0
        scale = max(canvas._zoom, min_scale)
        w = max(1, int(rect.width() * scale))
        h = max(1, int(rect.height() * scale))

        was_light = is_light_theme()
        try:
            if not color_mode:
                set_light_theme(True)

            bg = QColor("#1E1E1E") if color_mode else QColor("#FFFFFF")
            img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            img.setDotsPerMeterX(int(SCREEN_DPI / 0.0254 + 0.5))
            img.setDotsPerMeterY(int(SCREEN_DPI / 0.0254 + 0.5))
            img.fill(bg.rgb())

            ip = QPainter(img)
            ip.setRenderHint(QPainter.Antialiasing)

            if color_mode:
                from PySide6.QtCore import QSettings
                _gs = QSettings("Pulsar", "Pulsar")
                _grid_ok = _gs.value("grid/enabled", "true").lower() == "true"
                if _grid_ok:
                    grid_step = 100.0
                    ip.setPen(QPen(QColor("#2a2a2a"), 0.0))
                    gx = math.floor(rect.left() / grid_step) * grid_step
                    while gx <= rect.right():
                        ix = (gx - rect.left()) / rect.width() * w
                        ip.drawLine(int(ix), 0, int(ix), h)
                        gx += grid_step
                    gy = math.floor(rect.top() / grid_step) * grid_step
                    while gy <= rect.bottom():
                        iy = (gy - rect.top()) / rect.height() * h
                        ip.drawLine(0, int(iy), w, int(iy))
                        gy += grid_step

            scene.render(ip, QRectF(0, 0, w, h), rect)
            ip.end()

            flipped = img.mirrored(False, True)
            if not color_mode:
                gray = flipped.convertToFormat(QImage.Format_Grayscale8)
                ptr = gray.bits()
                n = gray.sizeInBytes()
                for i in range(n):
                    ptr[i] = 0 if ptr[i] < 196 else 255
                flipped = gray
            ok = flipped.save(path, "PNG")
        finally:
            if not color_mode:
                set_light_theme(was_light)

        # Полное обновление сцены и вьюпорта
        canvas._scene.update()
        canvas.viewport().update()

        if ok:
            self._show_notification(f"PNG сохранён: {path}")
        else:
            self._show_notification("Ошибка сохранения PNG")

    # ─── Просмотр netlist ───

    def _view_netlist_dialog(self):
        canvas = self._tabs.current_canvas()
        if canvas is None:
            QMessageBox.information(self, "Netlist", "Откройте схему в редакторе.")
            return
        netlist = canvas.export_cir()
        if not netlist.strip():
            QMessageBox.information(self, "Netlist", "Схема пуста.")
            return
        dialog = NetlistViewerDialog(netlist, self)
        dialog.exec()

    # ─── Статус-бар ───

    def _sch_on_position_changed(self, x: float, y: float):
        self.statusBar().showMessage(f"X: {x:.0f}  Y: {y:.0f}  mil")

    def _sch_on_mode_changed(self, mode: str):
        if mode == "WIRE":
            self.statusBar().showMessage("Режим проводов (W — выход, Esc — отмена)")
        elif mode == "SEGMENT":
            self.statusBar().showMessage("Режим сегментов [N]  ЛКМ — начать, ПКМ — зафиксировать, Esc — выход")
        elif mode == "PLACE":
            self.statusBar().showMessage("Размещение: ЛКМ — поставить, ПКМ/Esc — отмена")
        elif mode == "PLACE_RECT":
            self.statusBar().showMessage("Прямоугольник: 1-й клик — угол, 2-й клик — противоположный угол, Esc — отмена")
        elif mode == "PLACE_CIRCLE":
            self.statusBar().showMessage("Окружность: 1-й клик — центр, 2-й клик — радиус, Esc — отмена")
        elif not mode:
            self.statusBar().showMessage("Готово")
        if hasattr(self, '_wire_mode_action'):
            self._wire_mode_action.setChecked(mode == "SEGMENT")
        if hasattr(self, '_rect_action'):
            self._rect_action.setChecked(mode == "PLACE_RECT")
        if hasattr(self, '_circle_action'):
            self._circle_action.setChecked(mode == "PLACE_CIRCLE")

    def _sch_on_component_placed(self, refdes: str):
        import re
        m = re.match(r'^([A-Za-z]+)(\d+)$', refdes)
        if m:
            prefix, num_str = m.group(1), m.group(2)
            n = int(num_str)
        self._show_notification(f"Компонент {refdes} размещён")

    # ─── Симуляция ───

    def _log_to_terminal_safe(self, text: str):
        self._sim_output_queue.append(text)

    def _process_output_queue(self):
        while self._sim_output_queue:
            item = self._sim_output_queue.pop(0)
            if isinstance(item, tuple) and item[0] == "FINISHED":
                success = item[1]
                self._sim_progress_value = 100
                self._sim_progress.setValue(100)
                QTimer.singleShot(800, self._sim_progress.hide)
                self._sim_run_action.setEnabled(True)
                self._sim_stop_action.setEnabled(False)
                # Очистить temp-файл симуляции
                sim_temp = getattr(self, '_sim_current_temp', None)
                if sim_temp:
                    self._cleanup_temp_file(sim_temp)
                    self._sim_current_temp = None
                if success and self._simulator.simulation_data:
                    QTimer.singleShot(500, self._show_simulation_results)
            else:
                self._sim_output_buffer.append(item)
                term = self._tabs.current_terminal()
                if term is not None:
                    term.append(item)

    def _terminal_text(self) -> str:
        text = self._tabs.terminal_text()
        if text:
            return text
        return "\n".join(self._sim_output_buffer)

    def _detect_output_directives(self, text: str) -> dict:
        """Определить, какие директивы вывода есть в тексте ДО авто-фикса."""
        import re
        result = {'has_print': False, 'has_plot': False, 'has_op': False}
        for line in text.split('\n'):
            s = line.strip().upper()
            if not s or s.startswith('*') or s.startswith(';'):
                continue
            if re.match(r'\.PRINT\b', s):
                result['has_print'] = True
            elif re.match(r'\.PLOT\b', s):
                result['has_plot'] = True
            elif s.startswith('.OP') and not s.startswith('.OPTION'):
                result['has_op'] = True
        return result

    def _fix_print_directive(self):
        import re
        page = self._tabs.current_page()
        if page is None or page.page_type != 'cir':
            return
        editor = page.editor
        text = editor.toPlainText()

        # Сохранить оригинальные директивы ДО любых изменений
        self._output_directives = self._detect_output_directives(text)

        # Определить тип анализа из файла (пропуская комментарии)
        analysis_type = ""
        for line in text.split('\n'):
            s = line.strip()
            if not s or s.startswith('*') or s.startswith(';'):
                continue
            su = s.upper()
            for atype in ('.TRAN', '.AC', '.DC', '.OP'):
                if su.startswith(atype):
                    analysis_type = atype[1:].lower()
                    break
            if analysis_type:
                break

        # Если есть .PRINT/.PLOT но нет анализа — добавить .OP
        has_any_output = any(
            re.match(r'^\s*\.(?:PRINT|PLOT)\b', line, re.IGNORECASE)
            for line in text.split('\n')
        )
        add_op = not analysis_type and has_any_output

        fixed_lines = []
        changed = False
        op_added = False
        for line in text.split('\n'):
            # Вставить .OP перед первой директивой вывода если нужен
            if add_op and not op_added and re.match(r'^\s*\.(?:PRINT|PLOT)\b', line, re.IGNORECASE):
                fixed_lines.append('.OP')
                op_added = True
                changed = True
                self._log_to_terminal_safe("[INFO] Добавлен .OP — нет директивы анализа")

            # .PLOT → .PRINT (ngspice отдаёт табличные данные, нужные плоттеру)
            if re.match(r'^\s*\.PLOT\b', line, re.IGNORECASE):
                line = re.sub(r'\.PLOT\b', '.PRINT', line, count=1, flags=re.IGNORECASE)

            if re.match(r'^\s*\.PRINT\b', line, re.IGNORECASE):
                # Исправить пробелы: v (out) -> v(out)
                new_line = re.sub(r'(\w)\s*\(', r'\1(', line)

                # Добавить тип анализа если отсутствует
                parts = new_line.upper().split()
                if len(parts) >= 2:
                    after_print = parts[1].replace('(', '').replace(')', '')
                    if after_print not in ('TRAN', 'AC', 'DC', 'OP') and analysis_type:
                        new_line = re.sub(
                            r'(\.PRINT\b)',
                            rf'\1 {analysis_type}',
                            new_line,
                            count=1,
                            flags=re.IGNORECASE,
                        )

                if new_line != line:
                    changed = True
                fixed_lines.append(new_line)
            else:
                fixed_lines.append(line)
        if changed:
            cursor = editor.textCursor()
            pos = cursor.position()
            editor.setPlainText('\n'.join(fixed_lines))
            new_cursor = editor.textCursor()
            new_cursor.setPosition(min(pos, len('\n'.join(fixed_lines))))
            editor.setTextCursor(new_cursor)
            self._log_to_terminal_safe("[INFO] Исправлены .PRINT/.PLOT (пробелы, тип анализа)")

    def _sch_run_simulation(self):
        """Пуск: прогресс-диалог + симуляция (F5)."""
        try:
            self._sch_run_simulation_impl()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._log_to_terminal_safe(f"[ОШИБКА] {e}")
            self._log_to_terminal_safe(tb)
            QMessageBox.critical(self, "Ошибка", f"{e}\n\nПодробности в терминале.")

    def _sch_run_simulation_impl(self):
        if self._simulator.is_running:
            return

        cir_path = self._tabs.current_filepath()
        page_type = self._tabs.current_page_type()

        # Если открыта схема (.sch) — сохранить и взять авто-экспортированный .cir
        if page_type == 'sch':
            page = self._tabs.currentWidget()
            if page is None or page.filepath is None:
                QMessageBox.warning(self, "Предупреждение",
                                    "Сначала сохраните файл схемы (.sch)")
                return
            self._tabs.save_current_tab()
            cir_path = str(Path(page.filepath).with_suffix('.cir'))
            if not Path(cir_path).exists():
                QMessageBox.warning(self, "Ошибка",
                                    "Не удалось создать .cir файл из схемы.")
                return
            self._tabs.show_terminal()
            self._tabs.clear_terminal()
            self._run_simulation_from_file(cir_path)
            return

        if cir_path is None:
            self._run_simulation()
            return

        self._run_simulation()

    def _ensure_print_directive_in_file(self, cir_path: Path):
        """Конвертировать .PLOT → .PRINT, исправить тип анализа.
           НЕ добавляет .PRINT если его нет — пользователь сам решает."""
        import re
        text = cir_path.read_text()
        lines = text.split('\n')

        # Сохранить оригинальные директивы (уже сделано в _run_simulation_from_file)
        # здесь только модифицируем файл

        # Определить тип анализа
        analysis_type = ""
        for line in lines:
            s = line.strip()
            if not s or s.startswith('*') or s.startswith(';'):
                continue
            su = s.upper()
            for atype in ('.TRAN', '.AC', '.DC', '.OP'):
                if su.startswith(atype):
                    analysis_type = atype[1:].lower()
                    break
            if analysis_type:
                break

        # Если есть .PRINT/.PLOT но нет анализа — добавить .OP
        has_any_output = any(
            re.match(r'\.(?:PRINT|PLOT)\b', line.strip(), re.IGNORECASE)
            for line in lines
            if not line.strip().startswith('*') and not line.strip().startswith(';')
        )
        add_op = not analysis_type and has_any_output

        fixed_lines = []
        changed = False
        op_added = False
        for line in lines:
            s = line.strip()
            if s.startswith('*') or s.startswith(';') or not s:
                fixed_lines.append(line)
                continue

            # Вставить .OP перед первой директивой вывода
            if add_op and not op_added and re.match(r'\.(?:PRINT|PLOT)\b', s, re.IGNORECASE):
                fixed_lines.append('.OP')
                op_added = True
                changed = True
                self._log_to_terminal_safe("[INFO] Добавлен .OP — нет директивы анализа")

            # .PLOT → .PRINT (ngspice отдаёт табличные данные для плоттера)
            if re.match(r'\.PLOT\b', s, re.IGNORECASE):
                line = re.sub(r'\.PLOT\b', '.PRINT', line, count=1, flags=re.IGNORECASE)
                changed = True
                s = line.strip()

            m = re.match(r'\.PRINT\b', s, re.IGNORECASE)
            if not m:
                fixed_lines.append(line)
                continue

            # Проверить, есть ли тип анализа после .PRINT
            parts = s.upper().split()
            if len(parts) >= 2:
                after_print = parts[1].replace('(', '').replace(')', '')
                if after_print in ('TRAN', 'AC', 'DC', 'OP'):
                    fixed_lines.append(line)
                    continue
            # Добавить тип анализа
            if analysis_type:
                new_line = re.sub(
                    r'(\.PRINT\b)',
                    rf'\1 {analysis_type}',
                    s,
                    count=1,
                    flags=re.IGNORECASE,
                )
                fixed_lines.append(new_line)
                self._log_to_terminal_safe(f"[INFO] Исправлен .PRINT: добавлен тип {analysis_type}")
                if new_line != line:
                    changed = True
            else:
                fixed_lines.append(line)

        new_text = '\n'.join(fixed_lines)
        if new_text != text and changed:
            cir_path.write_text(new_text)

    def _apply_sim_fixes(self, text: str) -> str:
        """Применить .PLOT → .PRINT, .OP, тип анализа.
           Возвращает фиксированный текст. Ничего не модифицирует на диске."""
        import re
        lines = text.split('\n')

        self._output_directives = self._detect_output_directives(text)

        # ── .ic ... + uic → .ic ... + UIC в .tran ──
        uic_needed = False
        for i, line in enumerate(lines):
            s = line.strip()
            m_ic = re.match(r'(?i)\.IC\b\s+(.*?)\+\s*UIC\s*$', s)
            if m_ic:
                lines[i] = '.ic ' + m_ic.group(1).strip()
                uic_needed = True
            elif re.match(r'(?i)\.IC\b.*\bUIC\b', s):
                lines[i] = re.sub(r'\bUIC\b', '', s, flags=re.IGNORECASE).strip()
                uic_needed = True
        if uic_needed:
            for i, line in enumerate(lines):
                s = line.strip()
                if re.match(r'(?i)\.TRAN\b', s) and 'UIC' not in s.upper():
                    lines[i] = s + ' UIC'

        # Определить тип анализа
        analysis_type = ""
        for line in lines:
            s = line.strip()
            if not s or s.startswith('*') or s.startswith(';'):
                continue
            su = s.upper()
            for atype in ('.TRAN', '.AC', '.DC', '.OP'):
                if su.startswith(atype):
                    analysis_type = atype[1:].lower()
                    break
            if analysis_type:
                break

        # Если есть .PRINT/.PLOT но нет анализа — добавить .OP
        has_any_output = any(
            re.match(r'\.(?:PRINT|PLOT)\b', line.strip(), re.IGNORECASE)
            for line in lines
            if not line.strip().startswith('*') and not line.strip().startswith(';')
        )
        add_op = not analysis_type and has_any_output

        fixed_lines = []
        op_added = False
        for line in lines:
            s = line.strip()
            if s.startswith('*') or s.startswith(';') or not s:
                fixed_lines.append(line)
                continue

            # Вставить .OP перед первой директивой вывода
            if add_op and not op_added and re.match(r'\.(?:PRINT|PLOT)\b', s, re.IGNORECASE):
                fixed_lines.append('.OP')
                op_added = True

            # .PLOT → .PRINT
            if re.match(r'\.PLOT\b', s, re.IGNORECASE):
                line = re.sub(r'\.PLOT\b', '.PRINT', line, count=1, flags=re.IGNORECASE)
                s = line.strip()

            m = re.match(r'\.PRINT\b', s, re.IGNORECASE)
            if not m:
                fixed_lines.append(line)
                continue

            # Проверить, есть ли тип анализа после .PRINT
            parts = s.upper().split()
            if len(parts) >= 2:
                after_print = parts[1].replace('(', '').replace(')', '')
                if after_print in ('TRAN', 'AC', 'DC', 'OP'):
                    fixed_lines.append(line)
                    continue
            # Добавить тип анализа
            if analysis_type:
                new_line = re.sub(
                    r'(\.PRINT\b)',
                    rf'\1 {analysis_type}',
                    s,
                    count=1,
                    flags=re.IGNORECASE,
                )
                fixed_lines.append(new_line)
            else:
                fixed_lines.append(line)

        return '\n'.join(fixed_lines)

    def _write_temp_sim_file(self, text: str, suffix: str = '.cir') -> str:
        """Записать текст во временный файл для симуляции. Вернуть путь."""
        fd, temp_path = tempfile.mkstemp(suffix=suffix)
        os.write(fd, text.encode('utf-8'))
        os.close(fd)
        return temp_path

    def _cleanup_temp_file(self, path: Path):
        """Удалить временный файл, если он существует."""
        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

    def _run_simulation_from_file(self, cir_path: str):
        """Запустить симуляцию напрямую из .cir файла (без редактора)."""
        if self._simulator.is_running:
            self._log_to_terminal_safe("[WARN] Симуляция уже запущена")
            return

        self._sim_output_queue.clear()
        self._sim_output_buffer.clear()

        path = Path(cir_path)

        # Читаем оригинал, применяем фиксы в памяти, пишем в temp
        text = path.read_text()
        self._output_directives = self._detect_output_directives(text)
        fixed_text = self._apply_sim_fixes(text)
        sim_path = Path(self._write_temp_sim_file(fixed_text))

        # Валидация (по temp-файлу, без модификации оригинала)
        result = validate_netlist(sim_path)
        report_lines = result.formatted_report()
        for line in report_lines.split('\n'):
            if line.strip():
                self._log_to_terminal_safe(line)

        if not result.is_valid:
            self._log_to_terminal_safe(f"❌ Симуляция отменена: {result.error_count} ошибок")
            QMessageBox.warning(
                self, "Ошибки в netlist",
                f"Найдено {result.error_count} ошибок:\n\n" + "\n".join(
                    f"• {e.message}" for e in result.errors if e.severity == "error"
                ),
            )
            self._cleanup_temp_file(sim_path)
            return

        if result.warning_count > 0:
            self._log_to_terminal_safe(f"⚠️ Предупреждений: {result.warning_count}")

        # Очистить терминал, если он открыт
        page = self._tabs.current_page()
        if page:
            page.clear_terminal()

        from PySide6.QtWidgets import QProgressBar
        if not hasattr(self, '_sim_progress'):
            self._sim_progress = QProgressBar(self.statusBar())
            self._sim_progress.setMaximumWidth(200)
            self._sim_progress.setMaximumHeight(16)
            self._sim_progress.hide()
            self.statusBar().addPermanentWidget(self._sim_progress)

        self._sim_progress_value = 0
        self._sim_progress.setValue(0)
        self._sim_progress.show()
        self._sim_run_action.setEnabled(False)
        self._sim_stop_action.setEnabled(True)
        QTimer.singleShot(50, self._update_sim_progress)

        self._sim_current_temp = sim_path  # для очистки после симуляции

        self._simulator.run_simulation(
            sim_path,
            output_callback=self._log_to_terminal_safe,
            finished_callback=lambda success: self._sim_output_queue.append(("FINISHED", success)),
        )

    def _run_simulation(self):
        if self._simulator.is_running:
            self._log_to_terminal_safe("[WARN] Симуляция уже запущена")
            return

        # Очистить очередь предыдущего запуска (чтобы старые данные не попали в терминал)
        self._sim_output_queue.clear()
        self._sim_output_buffer.clear()

        filepath = self._tabs.current_filepath()
        if filepath is None:
            QMessageBox.warning(self, "Предупреждение", "Сначала сохраните файл схемы (.cir)")
            self._tabs.save_current_tab_as()
            filepath = self._tabs.current_filepath()
            if filepath is None:
                return

        page = self._tabs.current_page()

        # Сохранить оригинал на диск (без .PLOT→.PRINT фикса)
        if page:
            page.save()

        # Фиксы в памяти → temp-файл (оригинал не трогаем)
        text = page.editor.toPlainText()
        fixed_text = self._apply_sim_fixes(text)
        sim_path = Path(self._write_temp_sim_file(fixed_text))

        from PySide6.QtWidgets import QProgressBar
        if not hasattr(self, '_sim_progress'):
            self._sim_progress = QProgressBar(self.statusBar())
            self._sim_progress.setMaximumWidth(200)
            self._sim_progress.setMaximumHeight(16)
            self._sim_progress.hide()
            self.statusBar().addPermanentWidget(self._sim_progress)

        result = validate_netlist(sim_path)
        report_lines = result.formatted_report()
        for line in report_lines.split('\n'):
            if line.strip():
                self._log_to_terminal_safe(line)

        if not result.is_valid:
            self._log_to_terminal_safe(f"❌ Симуляция отменена: {result.error_count} ошибок")
            QMessageBox.warning(
                self, "Ошибки в netlist",
                f"Найдено {result.error_count} ошибок:\n\n" + "\n".join(
                    f"• {e.message}" for e in result.errors if e.severity == "error"
                ),
            )
            self._cleanup_temp_file(sim_path)
            return

        if result.warning_count > 0:
            self._log_to_terminal_safe(f"⚠️ Предупреждений: {result.warning_count}")

        if page:
            page.clear_terminal()

        self._sim_progress_value = 0
        self._sim_progress.setValue(0)
        self._sim_progress.show()
        self._sim_run_action.setEnabled(False)
        self._sim_stop_action.setEnabled(True)
        QTimer.singleShot(50, self._update_sim_progress)

        self._sim_current_temp = sim_path

        self._simulator.run_simulation(
            sim_path,
            output_callback=self._log_to_terminal_safe,
            finished_callback=lambda success: self._sim_output_queue.append(("FINISHED", success)),
        )

    def _stop_simulation(self):
        self._simulator.stop_simulation()
        self._sim_progress_value = 0
        self._sim_progress.hide()
        self._sim_run_action.setEnabled(True)
        self._sim_stop_action.setEnabled(False)
        sim_temp = getattr(self, '_sim_current_temp', None)
        if sim_temp:
            self._cleanup_temp_file(sim_temp)
            self._sim_current_temp = None
        self._log_to_terminal_safe("[INFO] Запрос на остановку отправлен")

    def _update_sim_progress(self):
        if not self._sim_progress.isVisible():
            return
        if self._sim_progress_value < 95:
            self._sim_progress_value += 1
            self._sim_progress.setValue(self._sim_progress_value)
            QTimer.singleShot(50, self._update_sim_progress)

    def _show_simulation_results(self):
        directives = getattr(self, '_output_directives', {})
        terminal_text = self._terminal_text()

        if directives.get('has_print'):
            self._show_print_table(terminal_text)
        elif directives.get('has_plot'):
            self._plot_simulation_results(terminal_text)
        elif directives.get('has_op'):
            self._show_op_table(terminal_text)
        else:
            self._log_to_terminal_safe("[INFO] Нет директив вывода (.PRINT / .PLOT) — результаты не показаны")

    def _plot_simulation_results(self, terminal_text: str = None):
        import re
        if terminal_text is None:
            terminal_text = self._terminal_text()
        sim_data = self._simulator.simulation_data
        analysis_type = sim_data.get('type', 'unknown')

        if analysis_type == 'unknown':
            if re.search(r'\.TRAN\b', terminal_text, re.IGNORECASE):
                analysis_type = 'tran'
            elif re.search(r'\.DC\b', terminal_text, re.IGNORECASE):
                analysis_type = 'dc'
            elif re.search(r'\.AC\b', terminal_text, re.IGNORECASE):
                analysis_type = 'ac'
            elif re.search(r'\.OP\b', terminal_text, re.IGNORECASE):
                analysis_type = 'op'

        has_plot_data = re.search(r'Index\s+(?:time|frequency|v-sweep|i-sweep)', terminal_text)

        if has_plot_data and analysis_type != 'unknown':
            try:
                editor = self._tabs.current_editor()
                netlist = editor.toPlainText() if editor else self._sim_output_to_netlist()
                self._plot_window = SpicePlotterWindow(terminal_text, analysis_type, netlist_text=netlist)
                self._plot_window.show()
                self._log_to_terminal_safe("[SUCCESS] Окно графиков открыто")
            except Exception as e:
                self._log_to_terminal_safe(f"[WARN] Не удалось отобразить графики: {e}")
        else:
            self._log_to_terminal_safe("[INFO] Нет данных для построения графиков")

    def _show_print_table(self, terminal_text: str):
        """Показать .PRINT результаты в отдельном окне."""
        self._show_result_dialog("Результаты .PRINT", terminal_text)

    def _show_op_table(self, terminal_text: str):
        """Показать .OP результаты в отдельном окне."""
        self._show_result_dialog("Результаты .OP", terminal_text)

    def _show_result_dialog(self, title: str, text: str):
        """Создать диалог с результатами симуляции."""
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout
        from PySide6.QtGui import QFont, QTextCursor

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(800, 600)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(10, 10, 10, 10)

        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setFont(QFont("Monospace", 10))
        viewer.setPlainText(text)
        viewer.setStyleSheet(
            "QTextEdit { background-color: #1e1e1e; color: #d4d4d4; border: 1px solid #333; }"
        )
        layout.addWidget(viewer)

        btn_layout = QHBoxLayout()
        copy_btn = QPushButton("Копировать всё")
        copy_btn.clicked.connect(lambda: (viewer.selectAll(), viewer.copy(),
            viewer.moveCursor(QTextCursor.MoveOperation.Start)))
        btn_layout.addWidget(copy_btn)
        btn_layout.addStretch()
        close_btn = QPushButton("Закрыть")
        close_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        dialog.show()
        self._log_to_terminal_safe(f"[SUCCESS] {title} — окно открыто")

    def _sim_output_to_netlist(self) -> str:
        """Собрать netlist из последнего запуска (для DC-парсера)."""
        lines = [l for l in self._sim_output_buffer if not l.startswith("[")]
        return "\n".join(lines)

    # ─── .OP анализ ───

    def _toggle_op(self):
        self._run_op_analysis()

    def _reset_op(self):
        if self._op_dialog is not None:
            try:
                self._op_dialog.close()
            except Exception:
                pass
            self._op_dialog = None
        if self._op_temp_file:
            self._cleanup_temp_file(self._op_temp_file)
            self._op_temp_file = None

    def _run_op_analysis(self):
        page_type = self._tabs.current_page_type()
        if page_type not in ('sch', 'cir'):
            return

        if page_type == 'sch':
            canvas = self._tabs.current_canvas()
            if canvas is None:
                return
            cir_text = canvas.export_cir()
            if not cir_text:
                return
        else:
            editor = self._tabs.current_editor()
            if editor is None:
                return
            cir_text = editor.toPlainText()

        # Проверка наличия источников тока или напряжения
        import re
        lines = cir_text.split('\n')
        has_source = any(
            re.match(r'^[VI]\S+\s+\S+', s) and not s.strip().startswith('*')
            for s in lines
        )
        if not has_source:
            QMessageBox.warning(
                self, "Нет источников",
                "В схеме нет источников тока и напряжения. Расчёт не возможен.\n"
                "Для расчёта схемы по постоянному току, добавьте в схему источники тока или напряжения."
            )
            return

        # Убедиться, что .OP есть
        has_op = any(
            s.strip().upper().startswith('.OP') and not s.strip().upper().startswith('.OPTION')
            for s in lines
        )
        if not has_op:
            cir_text = cir_text.rstrip() + '\n.OP\n'

        # Записать temp-файл
        import subprocess
        temp_path = Path(self._write_temp_sim_file(cir_text))
        self._op_temp_file = temp_path

        try:
            result = subprocess.run(
                ["ngspice", "-b", str(temp_path)],
                capture_output=True, text=True, timeout=30,
            )
            output = result.stdout + result.stderr

            if result.returncode != 0 and not result.stdout:
                self._log_to_terminal_safe(f"[ERROR] ngspice вернул код {result.returncode}")
                QMessageBox.warning(self, "Ошибка .OP",
                                    f"ngspice завершился с ошибкой (код {result.returncode})")
                return

            rows = self._parse_op_output(output, cir_text)
            if not rows:
                self._log_to_terminal_safe("[WARN] Не удалось распарсить .OP вывод")
                return

            from ui.op_dialog import OpDialog
            self._op_dialog = OpDialog(self, rows)
            self._op_dialog.finished.connect(self._on_op_dialog_finished)
            self._op_dialog.show()
            self._log_to_terminal_safe(f"[SUCCESS] .OP анализ — {len(rows)} строк")

        except subprocess.TimeoutExpired:
            self._log_to_terminal_safe("[ERROR] .OP анализ превысил 30 с")
            QMessageBox.warning(self, "Таймаут", "ngspice не завершился за 30 секунд")
        except FileNotFoundError:
            self._log_to_terminal_safe("[ERROR] ngspice не найден. Установите: apt install ngspice")
            QMessageBox.critical(self, "Ошибка", "ngspice не найден")
        except Exception as e:
            self._log_to_terminal_safe(f"[ERROR] {e}")
            import traceback
            self._log_to_terminal_safe(traceback.format_exc())

    def _on_op_dialog_finished(self, _result: int):
        self._op_dialog = None

    def _parse_op_output(self, ngspice_output: str, cir_text: str) -> list[dict]:
        import re
        node_voltages: dict[str, float] = {}
        dev_currents: dict[str, float] = {}
        bjt_currents: dict[str, float] = {}

        # ── 1. Node voltages — таблица "Node Voltage" ──
        in_table = False
        for line in ngspice_output.split('\n'):
            s = line.strip()
            if 'Node' in s and 'Voltage' in s:
                in_table = True
                continue
            if in_table:
                if not s or re.match(r'^[-= \t]+$', s):
                    continue
                pairs = list(re.finditer(r'(\S+)\s+(-?[0-9.eE+\-]+)', s))
                if pairs:
                    for m in pairs:
                        raw = m.group(1)
                        vm = re.match(r'V\(([^)]+)\)', raw)
                        node = vm.group(1) if vm else raw
                        try:
                            node_voltages[node] = float(m.group(2))
                        except ValueError:
                            pass
                elif s[0:1] not in '0123456789-':
                    in_table = False

        # ── 2. V(name): value V формат ──
        for line in ngspice_output.split('\n'):
            m = re.match(r'V\(([^)]+)\)\s*:?\s*(-?[0-9.eE+\-]+)', line)
            if m:
                try:
                    node_voltages[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass

        # GND (узел 0) не выводится ngspice, добавляем явно
        node_voltages.setdefault('0', 0.0)
        node_voltages.setdefault('GND', 0.0)
        node_voltages.setdefault('gnd', 0.0)

        # ── 3. Device currents I(V1) = value + v#branch table ──
        for line in ngspice_output.split('\n'):
            s = line.strip()
            # I(V1) = value
            m = re.match(r'I\((\S+)\)\s*=\s*(-?[0-9.eE+\-]+)\s*A?', s)
            if m:
                try:
                    dev_currents[m.group(1)] = float(m.group(2))
                except ValueError:
                    pass
            # vcc#branch  -1.19805e-02 (Source Current table)
            m2 = re.match(r'(v\w+#branch)\s+(-?[0-9.eE+\-]+)', s, re.IGNORECASE)
            if m2:
                try:
                    dev_currents[m2.group(1)] = float(m2.group(2))
                except ValueError:
                    pass

        # ── 4. BJT currents (two formats) ──
        lines = ngspice_output.split('\n')

        # Format A: Q1: Ic=value Ib=value Ie=value
        i = 0
        while i < len(lines):
            s = lines[i].strip()
            bjt_m = re.match(r'^([Qq]\w+):', s)
            if bjt_m:
                dev = bjt_m.group(1).upper()
                i += 1
                while i < len(lines):
                    sub = lines[i].strip()
                    if not sub:
                        i += 1
                        continue
                    sub_m = re.match(r'(I[ceb])\s*=\s*(-?[0-9.eE+\-]+)', sub, re.IGNORECASE)
                    if sub_m:
                        pin = sub_m.group(1)[1].upper()
                        try:
                            bjt_currents[f"{dev}({pin})"] = float(sub_m.group(2))
                        except ValueError:
                            pass
                        i += 1
                    else:
                        break
                continue
            i += 1

        # Format B: BJT: Bipolar Junction Transistor table section
        in_bjt_section = False
        cur_bjt_dev = None
        for line in lines:
            s = line.strip()
            m1 = re.match(r'^device\s+(\S+)', s)
            if in_bjt_section and m1:
                cur_bjt_dev = m1.group(1).upper()
                continue
            if in_bjt_section and cur_bjt_dev:
                m2 = re.match(r'(i[ceb])\s+(-?[0-9.eE+\-]+)', s, re.IGNORECASE)
                if m2:
                    pin = m2.group(1)[1].upper()
                    try:
                        bjt_currents[f"{cur_bjt_dev}({pin})"] = float(m2.group(2))
                    except ValueError:
                        pass
                    continue
                # End of parameters for this device
                if re.match(r'^\w+:', s) or s.startswith('---') or s.startswith('=='):
                    cur_bjt_dev = None
                    in_bjt_section = False
                    continue
            # Detect section start
            if s.startswith('BJT:'):
                in_bjt_section = True
                cur_bjt_dev = None

        # ── 5. Собрать строки по компонентам ──
        return self._build_op_rows(
            cir_text, node_voltages, dev_currents, bjt_currents
        )

    def _extract_all_components(self, cir_text: str) -> list[dict]:
        """Разобрать .cir — список {refdes, type, pins, value}."""
        import re
        comps: list[dict] = []
        for line in cir_text.split('\n'):
            s = line.strip()
            if not s or s.startswith('*') or s.startswith(';') or s.startswith('.'):
                continue
            tokens = s.split()
            if not tokens:
                continue
            refdes = tokens[0]
            prefix = refdes[0].upper()

            m = re.match(r'^([RrCcLlDd])(\w+)$', refdes)
            if m and len(tokens) >= 4:
                comps.append({
                    'refdes': refdes, 'type': m.group(1).upper(),
                    'pins': [tokens[1], tokens[2]], 'value': tokens[3],
                })
                continue

            m = re.match(r'^([Qq])(\w+)$', refdes)
            if m and len(tokens) >= 5:
                comps.append({
                    'refdes': refdes, 'type': 'Q',
                    'pins': [tokens[1], tokens[2], tokens[3]],
                    'value': tokens[4], 'pin_names': ['C', 'B', 'E'],
                })
                continue

            m = re.match(r'^([Mm])(\w+)$', refdes)
            if m and len(tokens) >= 6:
                comps.append({
                    'refdes': refdes, 'type': 'M',
                    'pins': [tokens[1], tokens[2], tokens[3]],
                    'value': tokens[5], 'pin_names': ['D', 'G', 'S'],
                })
                continue

            m = re.match(r'^([VvIiWw])(\w+)$', refdes)
            if m and len(tokens) >= 3:
                comps.append({
                    'refdes': refdes, 'type': prefix,
                    'pins': [tokens[1], tokens[2]],
                    'value': tokens[3] if len(tokens) > 3 else '',
                })
                continue

            m = re.match(r'^([UuXx])(\w+)$', refdes)
            if m:
                comps.append({
                    'refdes': refdes, 'type': 'X',
                    'pins': tokens[1:-1],
                    'value': tokens[-1] if len(tokens) > 1 else '',
                })
                continue
        return comps

    def _build_op_rows(self, cir_text: str,
                        node_voltages: dict[str, float],
                        dev_currents: dict[str, float],
                        bjt_currents: dict[str, float]) -> list[dict]:
        rows: list[dict] = []
        comps = self._extract_all_components(cir_text)

        for comp in comps:
            refdes = comp['refdes']
            ctype = comp['type']
            pins = comp['pins']
            row = None

            if ctype == 'R':
                vp = node_voltages.get(pins[0])
                vm = node_voltages.get(pins[1])
                dv = (vp - vm) if vp is not None and vm is not None else None
                cur = None
                val = self._parse_spice_value(comp.get('value', ''))
                if val and dv is not None:
                    cur = dv / val
                row = {'name': refdes, 'voltage': dv, 'current': cur}

            elif ctype == 'C':
                vp = node_voltages.get(pins[0])
                vm = node_voltages.get(pins[1])
                dv = (vp - vm) if vp is not None and vm is not None else None
                row = {'name': refdes, 'voltage': dv, 'current': 0.0}

            elif ctype == 'L':
                vp = node_voltages.get(pins[0])
                vm = node_voltages.get(pins[1])
                dv = (vp - vm) if vp is not None and vm is not None else None
                row = {'name': refdes, 'voltage': dv, 'current': None}

            elif ctype == 'D':
                vp = node_voltages.get(pins[0])
                vm = node_voltages.get(pins[1])
                dv = (vp - vm) if vp is not None and vm is not None else None
                row = {'name': refdes, 'voltage': dv, 'current': None}

            elif ctype == 'Q':
                pin_names = comp.get('pin_names', ['C', 'B', 'E'])
                for i, pn in enumerate(pin_names):
                    net = pins[i] if i < len(pins) else ''
                    v_pin = node_voltages.get(net)
                    cur = bjt_currents.get(f'{refdes}({pn})')
                    rows.append({'name': f'{refdes}({pn})', 'voltage': v_pin, 'current': cur})
                continue

            elif ctype == 'M':
                pin_names = comp.get('pin_names', ['D', 'G', 'S'])
                for i, pn in enumerate(pin_names):
                    net = pins[i] if i < len(pins) else ''
                    v_pin = node_voltages.get(net)
                    rows.append({'name': f'{refdes}({pn})', 'voltage': v_pin, 'current': None})
                continue

            elif ctype in ('V', 'I'):
                vp = node_voltages.get(pins[0])
                vm = node_voltages.get(pins[1])
                dv = (vp - vm) if vp is not None and vm is not None else None
                cur = dev_currents.get(refdes)
                if cur is None:
                    cur = dev_currents.get(refdes.lower() + '#branch')
                if cur is None:
                    cur = dev_currents.get(refdes.upper())
                row = {'name': refdes, 'voltage': dv, 'current': cur}

            elif ctype == 'X':
                vp = node_voltages.get(pins[0]) if pins else None
                vm = node_voltages.get(pins[1]) if len(pins) > 1 else None
                dv = (vp - vm) if vp is not None and vm is not None else None
                row = {'name': refdes, 'voltage': dv, 'current': None}

            if row is not None:
                rows.append(row)

        return rows

    @staticmethod
    def _parse_spice_value(s: str) -> float | None:
        """Распарсить SPICE-число: 1k, 0.1u, 10Meg, 4.7e3."""
        import re
        s = s.strip().upper()
        match = re.match(r'(-?[0-9.]+(?:[eE][+-]?\d+)?)\s*([a-zA-Z]*)', s)
        if not match:
            return None
        try:
            num = float(match.group(1))
        except ValueError:
            return None
        suffix = match.group(2).upper()
        if suffix == 'MEG':
            num *= 1e6
        elif suffix == 'M':
            num *= 1e-3
        elif suffix == 'K':
            num *= 1e3
        elif suffix == 'G':
            num *= 1e9
        elif suffix == 'T':
            num *= 1e12
        elif suffix in ('U', 'μ'):
            num *= 1e-6
        elif suffix == 'N':
            num *= 1e-9
        elif suffix == 'P':
            num *= 1e-12
        elif suffix == 'F':
            num *= 1e-15
        return num

    # ─── Настройки ───

    def _open_settings(self):
        from EDA.app.items.colors import is_light_theme as _current_theme
        old_light = _current_theme()
        dialog = SettingsDialog(self)
        dialog.exec()
        settings = QSettings("Pulsar", "Pulsar")
        family = settings.value("editor/font_family", "Monospace")
        size = settings.value("editor/font_size", 14, type=int)
        self._tabs.apply_font(family, size)
        # Применить стиль сетки ко всем холстам
        grid_dots = settings.value("grid/dots", "false").lower() == "true"
        grid_enabled = settings.value("grid/enabled", "true").lower() == "true"
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if hasattr(page, 'canvas'):
                page.canvas.set_grid_dots(grid_dots)
                page.canvas.set_grid_enabled(grid_enabled)
        # Переприменить тему только если она изменилась
        new_light = settings.value("app/theme", "dark") == "light"
        if new_light != old_light:
            self._apply_app_theme()

    def _apply_app_theme(self):
        from EDA.app.items.colors import set_light_theme
        settings = QSettings("Pulsar", "Pulsar")
        theme = settings.value("app/theme", "dark")
        is_light = theme == "light"
        set_light_theme(is_light)
        app = QApplication.instance()
        if theme == "light":
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#e0e0e0"))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#e0e0e0"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
            palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff0000"))
            palette.setColor(QPalette.ColorRole.Link, QColor("#0000ff"))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#0078d4"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            app.setPalette(palette)
        else:
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Window, QColor("#2d2d2d"))
            palette.setColor(QPalette.ColorRole.WindowText, QColor("#d4d4d4"))
            palette.setColor(QPalette.ColorRole.Base, QColor("#1e1e1e"))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#3c3c3c"))
            palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2d2d2d"))
            palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#d4d4d4"))
            palette.setColor(QPalette.ColorRole.Text, QColor("#d4d4d4"))
            palette.setColor(QPalette.ColorRole.Button, QColor("#3c3c3c"))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor("#d4d4d4"))
            palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff0000"))
            palette.setColor(QPalette.ColorRole.Link, QColor("#00a8ff"))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#264f78"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            app.setPalette(palette)

        # Обновить фон и перерисовать все холсты
        bg = QColor("#ffffff") if is_light else QColor("#1e1e1e")
        for i in range(self._tabs.count()):
            page = self._tabs.widget(i)
            if hasattr(page, 'canvas'):
                page.canvas.set_background_color(bg)
                page.canvas.reload_cursors()
            if hasattr(page, 'canvas'):
                page.canvas.viewport().update()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Pulsar")
    app.setApplicationVersion("0.3.0")
    app.setOrganizationName("Pulsar")
    app.setStyle("Fusion")

    # Применить тему из сохранённых настроек
    settings = QSettings("Pulsar", "Pulsar")
    theme = settings.value("app/theme", "dark")
    is_light = theme == "light"
    from EDA.app.items.colors import set_light_theme
    set_light_theme(is_light)
    if is_light:
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#f0f0f0"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#000000"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#e0e0e0"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#000000"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#000000"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#e0e0e0"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#000000"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff0000"))
        palette.setColor(QPalette.ColorRole.Link, QColor("#0000ff"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#0078d4"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)
    else:
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#2d2d2d"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#d4d4d4"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#1e1e1e"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#3c3c3c"))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2d2d2d"))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#d4d4d4"))
        palette.setColor(QPalette.ColorRole.Text, QColor("#d4d4d4"))
        palette.setColor(QPalette.ColorRole.Button, QColor("#3c3c3c"))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor("#d4d4d4"))
        palette.setColor(QPalette.ColorRole.BrightText, QColor("#ff0000"))
        palette.setColor(QPalette.ColorRole.Link, QColor("#00a8ff"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#264f78"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        app.setPalette(palette)

    show_splash = settings.value("splash/show", "true").lower() == "true"

    if show_splash:
        splash_path = Path(__file__).parent / "resources" / "images" / "splash.png"
        splash_pixmap = QPixmap(str(splash_path))
        splash_pixmap = splash_pixmap.scaled(688, 384, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        splash = QSplashScreen(splash_pixmap)
        splash.show()
        app.processEvents()
    else:
        splash = None

    _main_window = None

    def _show_main():
        nonlocal _main_window
        _main_window = PulsarMainWindow()
        _main_window._apply_app_theme()
        _main_window.show()
        if splash is not None:
            splash.close()

    if show_splash:
        QTimer.singleShot(2000, _show_main)
    else:
        _show_main()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
