"""
Модуль для отображения графиков симуляции
Автоматическое создание и показ графиков в окне PySide6
"""

import math
import re
import time
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget, QMenuBar, QFileDialog, QStatusBar
)
from PySide6.QtCore import Signal, QTimer
from PySide6.QtGui import QAction
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


# ─── Стиль осциллографа ────────────────────────────────────────────
SCOPE_BG = "#1a1a2e"
SCOPE_GRID = "#444444"
SCOPE_LINE = "#00ff41"
SCOPE_AXIS = "#aaaaaa"
SCOPE_TICK = "#cccccc"
SCOPE_TEXT = "#cccccc"
SCOPE_COLORS = ["#00ff41", "#00d4ff", "#ff006e", "#ffbe0b", "#8338ec", "#3a86ff"]


def _get_ylabel_for_vars(var_names: list) -> str:
    """Определить подпись оси Y по именам переменных"""
    # v(...) — напряжение, i(...) или *#branch — ток
    has_voltage = any('v(' in v.lower() for v in var_names)
    has_current = any('i(' in v.lower() or '#branch' in v.lower() for v in var_names)

    if has_current and not has_voltage:
        return 'Ток (А)'
    elif has_voltage and not has_current:
        return 'Напряжение (В)'
    elif has_current and has_voltage:
        return 'Напряжение / Ток'
    else:
        return 'Значение'


class MultiCursor:
    """Один курсор на все subplot — vline на каждой оси, hline только на текущей.
    Использует blit для производительности."""
    def __init__(self, fig, axes, x_data, y_data_lists):
        self.fig = fig
        self.axes = list(axes)
        self.x_data = np.array(x_data)
        self.y_data_lists = y_data_lists
        self.enabled = False
        self.vlines = []
        self.hlines = []
        self.info_texts = []
        self._bg_regions = [None] * len(axes)
        self._cursor_interval = 1.0 / 30
        self._last_time = 0

        for ax in self.axes:
            xlim = ax.get_xlim()
            ylim = ax.get_ylim()
            vl = ax.axvline(x=0, color='#ffff00', linestyle='--', linewidth=0.8, alpha=0.7, visible=False)
            hl = ax.axhline(y=0, color='#ffff00', linestyle='--', linewidth=0.5, alpha=0.4, visible=False)
            txt = ax.text(0, 0, '', ha='left', va='bottom', fontsize=10,
                          color=SCOPE_TICK, zorder=10, visible=False)
            ax.set_xlim(xlim)
            ax.set_ylim(ylim)
            self.vlines.append(vl)
            self.hlines.append(hl)
            self.info_texts.append(txt)

        self.fig.canvas.mpl_connect('draw_event', self._on_draw)
        self.fig.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
        self.fig.canvas.mpl_connect('figure_leave_event', self._hide_all)
        self.fig.canvas.draw_idle()

    def _on_draw(self, event):
        for i, ax in enumerate(self.axes):
            self._bg_regions[i] = self.fig.canvas.copy_from_bbox(ax.bbox)

    def toggle(self, enabled: bool):
        self.enabled = enabled
        if not enabled:
            self._hide_all()

    def _hide_all(self, event=None):
        for vl, hl, txt in zip(self.vlines, self.hlines, self.info_texts):
            vl.set_visible(False)
            hl.set_visible(False)
            txt.set_visible(False)
        self.fig.canvas.draw_idle()

    def _on_mouse_move(self, event):
        if event.button is not None or not self.enabled or event.xdata is None:
            return
        now = time.monotonic()
        if now - self._last_time < self._cursor_interval:
            return
        self._last_time = now

        x = event.xdata
        idx = np.argmin(np.abs(self.x_data - x))
        x_nearest = self.x_data[idx]

        for ax_i, (ax, vl, hl, txt) in enumerate(zip(self.axes, self.vlines, self.hlines, self.info_texts)):
            is_hovered = (event.inaxes == ax)
            vl.set_xdata([x_nearest])
            vl.set_visible(True)

            if is_hovered and ax_i < len(self.y_data_lists) and self.y_data_lists[ax_i]:
                y_val = self.y_data_lists[ax_i][idx]
                hl.set_ydata([y_val])
                hl.set_visible(True)
                txt.set_text(f'x={x_nearest:.4f}\ny={y_val:.6f}')

                xlim = ax.get_xlim()
                ylim = ax.get_ylim()
                x_range = xlim[1] - xlim[0]
                y_range = ylim[1] - ylim[0]
                tx = x_nearest + x_range * 0.03
                ty = y_val + y_range * 0.03

                if tx > xlim[1] - x_range * 0.02:
                    tx = x_nearest - x_range * 0.18
                    txt.set_ha('right')
                else:
                    txt.set_ha('left')

                if ty > ylim[1] - y_range * 0.15:
                    ty = y_val - y_range * 0.12
                    txt.set_va('top')
                else:
                    txt.set_va('bottom')

                txt.set_position((tx, ty))
                txt.set_visible(True)
            else:
                hl.set_visible(False)
                txt.set_visible(False)

            if self._bg_regions[ax_i] is not None:
                self.fig.canvas.restore_region(self._bg_regions[ax_i])
                if vl.get_visible():
                    ax.draw_artist(vl)
                if hl.get_visible():
                    ax.draw_artist(hl)
                if txt.get_visible():
                    ax.draw_artist(txt)
                self.fig.canvas.blit(ax.bbox)



class CursorTracker:
    """Отслеживает позицию мыши и рисует вертикальный/горизонтальный курсор на графике"""

    def __init__(self, ax, fig, canvas):
        self.ax = ax
        self.fig = fig
        self.canvas = canvas
        self.vline = None
        self.hline = None
        self.info_text = None
        self.x_data = None
        self.y_data = None
        self.line_objects = None
        self.enabled = False
        self.dragging_h = False
        self._initialized = False
        self._bg_renderer = None
        self._bg_bbox = None
        self._bg_buffer = None

        # Троттлинг курсора — ограничение частоты обновления (30 FPS для производительности)
        self._last_cursor_time = 0
        self._cursor_interval = 1.0 / 30  # 30 FPS
        self._pending_event = None  # отложенное событие для обновления после задержки

        self._setup_cursor()

    def _setup_cursor(self):
        """Настроить обработку событий мыши"""
        self.fig.canvas.mpl_connect('motion_notify_event', self._on_mouse_move)
        self.fig.canvas.mpl_connect('figure_leave_event', self._on_mouse_leave)
        self.fig.canvas.mpl_connect('button_press_event', self._on_button_press)
        self.fig.canvas.mpl_connect('button_release_event', self._on_button_release)
        
        # Инвалидация blit-кэша при pan/zoom — перерисовка фона
        self.fig.canvas.mpl_connect('draw_event', self._on_draw)
        self.fig.canvas.mpl_connect('resize_event', self._on_resize)

    def set_data(self, x_data, y_data_list, line_objects=None):
        """Установить данные для отображения координат"""
        self.x_data = np.array(x_data)
        self.y_data = [np.array(y) for y in y_data_list]
        self.line_objects = line_objects  # list of Line2D из ax.lines

    def toggle(self, enabled: bool):
        """Включить/выключить курсор"""
        self.enabled = enabled
        if not enabled:
            self._hide_cursor()

    def _ensure_lines(self):
        """Создать линии один раз при первом вызове"""
        if self._initialized:
            return

        # Фиксируем пределы осей
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()

        self.vline = self.ax.axvline(x=0, color='#ffff00', linestyle='--', linewidth=0.8, alpha=0.7, visible=False)
        self.hline = self.ax.axhline(y=0, color='#ffff00', linestyle='--', linewidth=0.8, alpha=0.5, visible=False)

        self.info_text = self.ax.text(0, 0, '',
                                       ha='left', va='bottom',
                                       fontsize=10,
                                       color=SCOPE_TICK,
                                       zorder=10,
                                       visible=False)

        self.ax.set_xlim(xlim)
        self.ax.set_ylim(ylim)

        # Рисуем начальный фон и сохраняем его
        self.canvas.draw()
        self._save_background()

        self._initialized = True

    def _save_background(self):
        """Сохранить фон для последующего blit"""
        self.canvas.draw()
        self._bg_renderer = self.canvas.get_renderer()
        self._bg_bbox = self.ax.bbox.frozen()
        self._bg_buffer = self.canvas.copy_from_bbox(self._bg_bbox)

    def _blit_cursor(self):
        """Быстрая отрисовка курсора через blit"""
        if self._bg_buffer is None:
            self._save_background()

        # Восстанавливаем фон
        self.canvas.restore_region(self._bg_buffer)

        # Рисуем курсор поверх
        if self.vline.get_visible():
            self.ax.draw_artist(self.vline)
        if self.hline.get_visible():
            self.ax.draw_artist(self.hline)
        if self.info_text.get_visible():
            self.ax.draw_artist(self.info_text)

        # Выводим на экран (без flush_events — он слишком дорогой)
        self.canvas.blit(self._bg_bbox)

    def _update_cursor(self, x, y):
        """Обновить курсор — линии и текст следуют за мышью"""
        self._ensure_lines()

        self.vline.set_xdata([x])
        self.vline.set_visible(True)

        self.hline.set_ydata([y])
        self.hline.set_visible(True)

        # Позиция текста — с отступом от курсора, в пределах осей
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        x_range = xlim[1] - xlim[0]
        y_range = ylim[1] - ylim[0]

        tx = x + x_range * 0.03
        ty = y + y_range * 0.03

        # Если текст вылезает за правый край — показать слева от курсора
        if tx > xlim[1] - x_range * 0.02:
            tx = x - x_range * 0.18
            self.info_text.set_ha('right')
        else:
            self.info_text.set_ha('left')

        # Если текст вылезает за верхний край — показать ниже курсора
        if ty > ylim[1] - y_range * 0.15:
            ty = y - y_range * 0.12
            self.info_text.set_va('top')
        else:
            self.info_text.set_va('bottom')

        self.info_text.set_position((tx, ty))
        self.info_text.set_text(f'X={x:.6g}   Y={y:.6g}')
        self.info_text.set_visible(True)

        self._blit_cursor()

    def _update_hline(self, y):
        """Обновить только горизонтальную линию"""
        if self.hline is not None:
            self.hline.set_ydata([y])
            self._blit_cursor()

    def _on_draw(self, event):
        """Инвалидация blit-кэша при любой перерисовке (pan, zoom, и т.д.)"""
        self._bg_buffer = None
        self._bg_bbox = None

    def _on_resize(self, event):
        """Сброс кэша при изменении размера окна"""
        self._bg_buffer = None
        self._bg_bbox = None

    def _hide_cursor(self):
        """Скрыть курсор"""
        if self.vline is not None:
            self.vline.set_visible(False)
        if self.hline is not None:
            self.hline.set_visible(False)
        if self.info_text is not None:
            self.info_text.set_visible(False)
        # Полная перерисовка без курсора
        self.canvas.draw()
        self._save_background()

    def _on_button_press(self, event):
        """Обработка нажатия кнопки мыши"""
        if not self.enabled or event.inaxes != self.ax:
            return

        if self.hline is not None and event.ydata is not None:
            hline_y = self.hline.get_ydata()
            if len(hline_y) > 0 and abs(event.ydata - hline_y[0]) < (self.ax.get_ylim()[1] - self.ax.get_ylim()[0]) * 0.02:
                self.dragging_h = True

    def _on_button_release(self, event):
        """Обработка отпускания кнопки мыши"""
        self.dragging_h = False

    def _on_mouse_move(self, event):
        """Обработка движения мыши — с троттлингом для производительности"""
        # Не обновлять курсор если зажата кнопка (pan/перемещение графика)
        if event.button is not None:
            return
            
        if not self.enabled:
            # Если курсор отключён — скрыть один раз
            if self.vline is not None and self.vline.get_visible():
                self._hide_cursor()
            return

        if event.inaxes != self.ax:
            # Пропустить, если курсор уже скрыт (избегаем дорогого canvas.draw())
            if self.vline is None or not self.vline.get_visible():
                return
            self._hide_cursor()
            return

        x = event.xdata
        y = event.ydata
        if x is None:
            return

        if self.dragging_h and y is not None:
            self._update_hline(y)
            return

        # Троттлинг — не чаще 60 FPS
        now = time.monotonic()
        elapsed = now - self._last_cursor_time
        
        if elapsed < self._cursor_interval:
            # Сохраняем последнее событие и откладываем обновление
            self._pending_event = (x, y)
            return

        self._last_cursor_time = now
        self._update_cursor(x, y)
        
        # Если есть отложенное событие — запланировать его обработку
        if self._pending_event is not None:
            QTimer.singleShot(0, self._process_pending_cursor_event)

    def _process_pending_cursor_event(self):
        """Обработать отложенное событие курсора"""
        if self._pending_event is None:
            return
        
        x, y = self._pending_event
        self._pending_event = None
        
        now = time.monotonic()
        elapsed = now - self._last_cursor_time
        
        if elapsed >= self._cursor_interval:
            self._last_cursor_time = now
            self._update_cursor(x, y)
        else:
            # Всё ещё слишком рано — снова отложить
            QTimer.singleShot(0, self._process_pending_cursor_event)

    def _on_mouse_leave(self, event):
        """Обработка выхода мыши за пределы графика"""
        if self.dragging_h:
            return
        self._hide_cursor()


def _apply_scope_style(fig, ax):
    """Применить стиль осциллографа к фигуре и осям"""
    fig.patch.set_facecolor(SCOPE_BG)
    ax.set_facecolor(SCOPE_BG)

    ax.grid(True, color=SCOPE_GRID, linewidth=0.8, alpha=0.6)

    ax.spines["bottom"].set_color(SCOPE_AXIS)
    ax.spines["left"].set_color(SCOPE_AXIS)
    ax.spines["top"].set_color(SCOPE_AXIS)
    ax.spines["right"].set_color(SCOPE_AXIS)

    ax.tick_params(colors=SCOPE_TICK)

    ax.xaxis.label.set_color(SCOPE_TEXT)
    ax.yaxis.label.set_color(SCOPE_TEXT)
    ax.title.set_color(SCOPE_TEXT)


class SpicePlotterWindow(QMainWindow):
    """Окно для отображения графиков SPICE симуляции"""

    cursorStateChanged = Signal(bool)  # сигнал при изменении состояния курсора

    def __init__(self, terminal_text: str, analysis_type: str, netlist_text: str = ""):
        super().__init__()
        self.terminal_text = terminal_text
        self.analysis_type = analysis_type
        self.netlist_text = netlist_text
        self.tab_cursors = {}  # курсоры по индексам вкладок
        self.active_cursor_idx = None  # индекс активной вкладки с курсором
        self._cursor_enabled = False  # состояние курсора

        # Настройки отображения кривых
        self._tab_line_objects = {}   # храним ссылки на Line2D по вкладкам для перерисовки

        self.setWindowTitle(f"Pulsar - Графики ({analysis_type.upper()})")
        self.resize(1100, 750)

        self._setup_menu()
        self._setup_ui()
        self._setup_status_bar()

    def _setup_status_bar(self):
        """Создать статус-бар для отображения координат"""
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self.statusBar.setStyleSheet(
            "QStatusBar { background: #1a1a2e; color: #cccccc; }"
        )
        self.statusBar.showMessage("Готово | Курсор: Ctrl+M для включения")

    def _setup_menu(self):
        """Создать меню для окна графиков"""
        menubar = self.menuBar()

        file_menu = menubar.addMenu("Файл")

        save_plot_action = QAction("Сохранить график…", self)
        save_plot_action.setShortcut("Ctrl+S")
        save_plot_action.triggered.connect(self._save_plot)
        file_menu.addAction(save_plot_action)

        view_menu = menubar.addMenu("Вид")

        self._cursor_action = QAction("Курсор", self, checkable=True)
        self._cursor_action.setChecked(False)
        self._cursor_action.setShortcut("Ctrl+M")
        self._cursor_action.triggered.connect(self._toggle_cursor)
        view_menu.addAction(self._cursor_action)

        self._grid_action = QAction("Сетка", self, checkable=True)
        self._grid_action.setChecked(True)
        self._grid_action.setShortcut("Ctrl+G")
        self._grid_action.triggered.connect(self._toggle_grid)
        view_menu.addAction(self._grid_action)

    def _save_plot(self):
        """Сохранить текущий график в файл"""
        current_idx = self.tabs.currentIndex()
        tab = self.tabs.widget(current_idx)
        if tab is None:
            return

        # Найти canvas на вкладке
        for child in tab.children():
            if isinstance(child, FigureCanvas):
                break
        else:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Сохранить график",
            str(Path.home() / "plot.jpg"),
            "JPEG Image (*.jpg)"
        )
        if not file_path:
            return

        child.figure.savefig(file_path, dpi=150, facecolor=SCOPE_BG, edgecolor='none')

    def _toggle_cursor(self):
        """Включить/отключить курсор на активной вкладке"""
        self._cursor_enabled = self._cursor_action.isChecked()
        self.set_cursor_enabled(self._cursor_enabled)
        if self._cursor_enabled:
            self.statusBar.showMessage("Курсор включён — двигайте мышь по графику")
        else:
            self.statusBar.showMessage("Курсор отключён")

    def _toggle_grid(self):
        """Включить/отключить сетку на активной вкладке"""
        show_grid = self._grid_action.isChecked()
        current_idx = self.tabs.currentIndex()
        tab = self.tabs.widget(current_idx)
        if tab is None:
            return

        for child in tab.children():
            if isinstance(child, FigureCanvas):
                ax = child.figure.axes[0]
                ax.grid(show_grid)
                child.draw()
                break

        if show_grid:
            self.statusBar.showMessage("Сетка включена")
        else:
            self.statusBar.showMessage("Сетка отключена")

    def set_cursor_enabled(self, enabled: bool):
        """Включить/отключить курсор на активной вкладке"""
        self._cursor_enabled = enabled
        self._cursor_action.setChecked(enabled)
        current_idx = self.tabs.currentIndex()
        if current_idx in self.tab_cursors:
            cursor = self.tab_cursors[current_idx]
            cursor.toggle(enabled)
            self.cursorStateChanged.emit(enabled)
        else:
            self.cursorStateChanged.emit(False)

    def _setup_ui(self):
        """Создать интерфейс окна графиков"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Вкладки для графиков
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)

        # Построить графики
        if self.analysis_type == 'tran':
            self._plot_transient()
        elif self.analysis_type == 'dc':
            self._plot_dc()
        elif self.analysis_type == 'ac':
            self._plot_ac()
        elif self.analysis_type == 'op':
            self._plot_operating_point()

    def _create_tab(self, fig, title: str):
        """Создать вкладку с графиком (без кастомного canvas и курсора)"""
        canvas = FigureCanvas(fig)
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(canvas)
        self.tabs.addTab(tab, title)

    def _create_tab_with_canvas(self, fig, title: str, canvas, cursor=None):
        """Создать вкладку с canvas и курсором"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(canvas)

        # Добавить вкладку и сохранить индекс
        self.tabs.addTab(tab, title)
        tab_idx = self.tabs.count() - 1

        if cursor is not None:
            self.tab_cursors[tab_idx] = cursor

    def get_cursor_state(self) -> bool:
        """Получить состояние курсора на активной вкладке"""
        current_idx = self.tabs.currentIndex()
        if current_idx in self.tab_cursors:
            return self.tab_cursors[current_idx].enabled
        return False

    def _on_tab_changed(self, index: int):
        """Обработка переключения вкладки"""
        # Отправить сигнал с текущим состоянием курсора на новой вкладке
        has_cursor = index in self.tab_cursors
        state = False
        if has_cursor:
            state = self.tab_cursors[index].enabled
        self.cursorStateChanged.emit(state)

    def _parse_tran_data_from_text(self):
        """Парсинг данных переходного анализа из текста"""
        time_data = []
        voltage_data = {}
        current_vars = []
        in_data_section = False
        seen_indices = set()
        skip_time = False  # время добавляем только из первой таблицы с данным набором переменных

        lines = self.terminal_text.split('\n')

        for i, line in enumerate(lines):
            # Найти заголовок с переменными (регистронезависимый)
            header_match = re.match(r'index\s+time\s+(.*)', line, re.IGNORECASE)
            if header_match:
                var_part = header_match.group(1)
                # Извлечь имена переменных В ПОРЯДКЕ следования в заголовке
                # Ловим: v(3), i(V1), vsense#branch, v1#branch и т.д.
                new_vars = re.findall(r'[a-zA-Z][a-zA-Z0-9_]*(?:\([^)]*\)|#[a-zA-Z]+)', var_part)

                # Если это первый заголовок — инициализировать
                if not current_vars:
                    current_vars = new_vars
                    for var in current_vars:
                        voltage_data[var] = []
                    in_data_section = True
                    skip_time = False
                elif new_vars:
                    # Второй и последующие заголовки
                    seen_indices.clear()
                    # Сравнить переменные: если набор изменился — это новый .PRINT (время уже есть)
                    if set(v.upper() for v in new_vars) != set(v.upper() for v in current_vars):
                        skip_time = True
                    else:
                        skip_time = False  # тот же .PRINT, разбитый NGspice на части — время продолжаем
                    current_vars[:] = new_vars
                    for var in current_vars:
                        if var not in voltage_data:
                            voltage_data[var] = []
                    in_data_section = True
                continue

            if not in_data_section:
                continue

            # Пропускать разделительные линии
            if re.match(r'^\s*-+\s*$', line):
                continue

            # Парсить строки данных — захватываем все колонки значений
            data_match = re.match(r'^\s*(\d+)\s+([0-9eE+\-.]+)\s+(.*)', line)
            if data_match:
                idx = int(data_match.group(1))

                # Пропустить дубликаты (одинаковый индекс в рамках одной таблицы)
                if idx in seen_indices:
                    continue

                seen_indices.add(idx)

                try:
                    time_val = float(data_match.group(2))
                except ValueError:
                    continue

                # Разобрать все оставшиеся значения
                rest = data_match.group(3).strip()
                values = re.findall(r'[0-9eE+\-.]+', rest)

                if not skip_time:
                    time_data.append(time_val)

                if current_vars:
                    for vi, val_str in enumerate(values):
                        if vi < len(current_vars):
                            var_name = current_vars[vi]
                            if var_name in voltage_data:
                                try:
                                    voltage_data[var_name].append(float(val_str))
                                except ValueError:
                                    pass

        return time_data, voltage_data

    def _plot_transient(self):
        """Построить графики переходного процесса"""
        time_data, voltage_data = self._parse_tran_data_from_text()

        if not time_data:
            fig = Figure(figsize=(8, 4))
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5, 'Нет данных для отображения\nДобавьте .PRINT TRAN в netlist',
                   ha='center', va='center', fontsize=14, transform=ax.transAxes)
            ax.axis('off')
            self._create_tab(fig, 'Нет данных')
            return

        # Защита: обрезать все переменные до длины time_data
        for var_name in list(voltage_data.keys()):
            if len(voltage_data[var_name]) > len(time_data):
                voltage_data[var_name] = voltage_data[var_name][:len(time_data)]
            elif len(voltage_data[var_name]) < len(time_data):
                # Дополнить None если короче (должно быть 0 в таких случаях)
                voltage_data[var_name] = voltage_data[var_name] + [0.0] * (len(time_data) - len(voltage_data[var_name]))

        # Создать фигуру с отдельными subplot для каждой переменной
        var_items = [(n, v) for n, v in voltage_data.items() if v]
        n_vars = len(var_items)
        fig = Figure(figsize=(10, 2.5 * max(n_vars, 1)))

        y_data_list = []
        for idx, (var_name, values) in enumerate(var_items):
            ax = fig.add_subplot(n_vars, 1, idx + 1)
            color = SCOPE_COLORS[idx % len(SCOPE_COLORS)]
            ax.plot(
                np.array(time_data) * 1e3,
                values,
                linewidth=0.5,
                label=var_name,
                color=color,
            )
            y_data_list.append(values)

            # Подпись оси Y по типу переменной
            if var_name.lower().startswith('i(') or '#branch' in var_name.lower():
                ylabel = 'Ток (А)'
            elif var_name.lower().startswith('v('):
                ylabel = 'Напряжение (В)'
            else:
                ylabel = 'Значение'
            ax.set_ylabel(ylabel, fontsize=10)

            # Только для первого subplot — заголовок
            if idx == 0:
                ax.set_title('Переходный процесс (Transient Analysis)',
                             fontsize=14, fontweight='bold')

            # Только для последнего subplot — подпись X
            if idx == n_vars - 1:
                ax.set_xlabel('Время (мс)', fontsize=12)

            _apply_scope_style(fig, ax)
            if idx < n_vars - 1:
                ax.set_xlabel('')
            ax.legend(fontsize=9, loc='upper right')

        fig.subplots_adjust(left=0.12, right=0.98, top=0.95, bottom=0.12, hspace=0.05)

        # Создать canvas и MultiCursor на всех subplot
        canvas = FigureCanvas(fig)
        cursor = MultiCursor(fig, fig.axes, np.array(time_data) * 1e3, y_data_list)

        self._create_tab_with_canvas(fig, 'Переходный процесс', canvas, cursor)

    def _parse_spice_value(self, s: str) -> float:
        s = s.strip().lower()
        factor = {'f': 1e-15, 'p': 1e-12, 'n': 1e-9, 'u': 1e-6,
                  'm': 1e-3, 'k': 1e3, 'meg': 1e6}
        if s.endswith('meg'):
            return float(s[:-3]) * 1e6
        if s and s[-1] in factor:
            return float(s[:-1]) * factor[s[-1]]
        return float(s)

    def _get_outer_sweep_params(self):
        """Извлечь параметры внешнего DC sweep (Ib) из netlist или terminal_text"""
        text = self.netlist_text or self.terminal_text
        for line in text.split('\n'):
            m = re.match(
                r'\.dc\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)',
                line, re.IGNORECASE
            )
            if m:
                try:
                    start = self._parse_spice_value(m.group(2))
                    step = self._parse_spice_value(m.group(4))
                    return start, step
                except ValueError:
                    pass
        return None

    def _parse_dc_data_from_text(self):
        """Парсинг данных DC-анализа из текста вывода NGspice

        Формат вывода (простой sweep):
        Index   v-sweep         v(1)            v1#branch
        0       0.000000e+00    0.000000e+00    0.000000e+00

        Формат вывода (вложенный sweep):
        Index   v-sweep         v(3)            vce#branch
        0       0.000000e+00    0.000000e+00    ...
        5       5.000000e-01    5.000000e-01    ...
        6       0.000000e+00    0.000000e+00    ...  <-- v-sweep сбросился, новый блок
        ...
        """
        all_records = []      # (sweep_val, {var: val, ...}) — все строки подряд
        current_vars = []
        in_data_section = False

        lines = self.terminal_text.split('\n')

        for line in lines:
            header_match = re.match(r'Index\s+(?:v|i)-sweep\s+(.*)', line)
            if header_match:
                var_part = header_match.group(1).strip()
                new_vars = re.findall(r'(\S+)', var_part)
                if not current_vars:
                    current_vars = new_vars
                in_data_section = True
                continue

            if not in_data_section:
                continue

            if re.match(r'^\s*-+\s*$', line):
                continue
            if re.match(r'^\s*\.', line):
                continue

            data_match = re.match(
                r'^\s*(\d+)\s+([0-9eE+\-.]+)\s+((?:[0-9eE+\-.]+\s*)+)', line
            )
            if data_match:
                try:
                    sweep_val = float(data_match.group(2))
                except ValueError:
                    continue

                values_str = data_match.group(3).strip()
                values = re.findall(r'[0-9eE+\-.]+', values_str)
                row = {}
                for i in range(min(len(values), len(current_vars))):
                    try:
                        row[current_vars[i]] = float(values[i])
                    except ValueError:
                        pass
                if len(row) < len(current_vars):
                    continue
                all_records.append((sweep_val, row))

            if re.match(r'\s*Total', line):
                break

        if not all_records:
            return [], {}

        # Разделить на блоки по сбросу sweep-значения
        blocks = []  # [(sweep_list, {var: [vals], ...}), ...]
        cur_sweep = []
        cur_data = {var: [] for var in current_vars}
        start_val = all_records[0][0]
        prev = start_val

        for sweep_val, row in all_records:
            # Сброс: sweep вернулся к начальному значению (или около него),
            # а блок уже набрал больше 5 точек (чтобы не реагировать на шум)
            is_reset = (
                sweep_val < prev
                and abs(sweep_val - start_val) < abs(start_val - prev) * 0.5
                and len(cur_sweep) > 5
            )
            if is_reset:
                blocks.append((cur_sweep, cur_data))
                cur_sweep = []
                cur_data = {var: [] for var in current_vars}
            cur_sweep.append(sweep_val)
            for var in current_vars:
                cur_data[var].append(row[var])
            prev = sweep_val

        if cur_sweep:
            blocks.append((cur_sweep, cur_data))

        # Если один блок — возвращаем как раньше
        if len(blocks) == 1:
            return blocks[0]

        # Несколько блоков → склеиваем с суффиксами
        outer_params = self._get_outer_sweep_params()
        voltage_data = {}
        sweep_data = []
        for bi, (sweep_list, data_dict) in enumerate(blocks):
            if outer_params:
                start, step = outer_params
                ib_val = start + bi * step
                suffix = f' [Ib={ib_val*1e3:.1f}mA]'
            else:
                suffix = f' [{bi}]'
            for var in current_vars:
                voltage_data[var + suffix] = data_dict[var]

        # Для sweep используем последний блок (все одинаковые)
        sweep_data = blocks[-1][0]

        return sweep_data, voltage_data

    def _parse_ac_data_from_text(self):
        """Парсинг данных AC-анализа из текста вывода NGspice

        Формат вывода (комплексные значения: real, imaginary в одной строке):
        Index   frequency       v(2)
        0       1.000000e+01    9.960677e-01,   -6.25848e-02

        Затем отдельная секция для фазы:
        Index   frequency       vp(2)
        0       1.000000e+01    -6.27494e-02

        Первая секция содержит REAL и IMAGINARY части.
        Magnitude = sqrt(real² + imag²), Phase = atan2(imag, real).
        """
        frequency_data = []
        magnitude_data = {}
        phase_data = {}
        all_vars = []
        current_table_vars = []
        in_data_section = False
        seen_indices = set()

        lines = self.terminal_text.split('\n')

        for line in lines:
            # Найти заголовок с переменными (Index + frequency + var_name)
            header_match = re.match(r'Index\s+frequency\s+(.*)', line)
            if header_match:
                var_part = header_match.group(1).strip()
                new_vars = re.findall(r'(\S+)', var_part)
                current_table_vars = new_vars

                if not all_vars:
                    all_vars = current_table_vars[:]
                    for var in all_vars:
                        magnitude_data[var] = []
                        phase_data[var] = []
                else:
                    seen_indices.clear()
                    for var in current_table_vars:
                        if var not in all_vars:
                            all_vars.append(var)
                            magnitude_data[var] = []
                            phase_data[var] = []

                in_data_section = True
                continue

            if not in_data_section:
                continue

            # Пропускать разделительные линии
            if re.match(r'^\s*-+\s*$', line):
                continue

            # Пропускать заголовки секций
            if re.match(r'^\s*\.', line):
                continue

            # Парсить строки данных
            # Формат: Index  frequency  real_part,  imag_part
            data_match = re.match(
                r'^\s*(\d+)\s+([0-9eE+\-.]+)\s+([0-9eE+\-.]+),\s*([0-9eE+\-.]+)',
                line
            )
            if data_match:
                idx = int(data_match.group(1))
                if idx in seen_indices:
                    continue
                seen_indices.add(idx)

                freq_val = float(data_match.group(2))

                # Первая пара: real, imag для первой переменной
                pairs = [(float(data_match.group(3)), float(data_match.group(4)))]

                # Дополнительные пары (для остальных переменных)
                rest = line[data_match.end():]
                more = re.findall(
                    r'\s+([0-9eE+\-.]+),\s*([0-9eE+\-.]+)',
                    rest
                )
                for r, i in more:
                    pairs.append((float(r), float(i)))

                if not frequency_data or frequency_data[-1] != freq_val:
                    frequency_data.append(freq_val)

                for vi, (real_part, imag_part) in enumerate(pairs):
                    if vi < len(current_table_vars):
                        var_name = current_table_vars[vi]
                        mag_val = (real_part ** 2 + imag_part ** 2) ** 0.5
                        phase_val = math.atan2(imag_part, real_part)
                        magnitude_data[var_name].append(mag_val)
                        phase_data[var_name].append(phase_val)

            # Отдельная секция только для фазы: Index  frequency  phase
            phase_match = re.match(
                r'^\s*(\d+)\s+([0-9eE+\-.]+)\s+([0-9eE+\-.]+)\s*$',
                line
            )
            if phase_match and ',' not in line:
                freq_val = float(phase_match.group(2))
                phase_val = float(phase_match.group(3))

                if current_table_vars and current_table_vars[0] in phase_data:
                    try:
                        fi = frequency_data.index(freq_val)
                        phase_data[current_table_vars[0]][fi] = phase_val
                    except ValueError:
                        pass

            # Остановиться при конце секции данных
            if re.match(r'\s*Total', line):
                break

        # Защита: обрезать до минимальной длины
        min_len = len(frequency_data)
        for name in list(magnitude_data.keys()):
            if len(magnitude_data[name]) < min_len:
                min_len = len(magnitude_data[name])
        if min_len < len(frequency_data):
            frequency_data = frequency_data[:min_len]
            for name in magnitude_data:
                magnitude_data[name] = magnitude_data[name][:min_len]
            for name in phase_data:
                phase_data[name] = phase_data[name][:min_len]

        return frequency_data, magnitude_data, phase_data

    def _plot_dc(self):
        """Построить графики DC-анализа"""
        sweep_data, voltage_data = self._parse_dc_data_from_text()

        if not sweep_data:
            fig = Figure(figsize=(8, 4))
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5,
                    'Нет данных DC-анализа\nДобавьте .PRINT DC и .DC в netlist',
                    ha='center', va='center', fontsize=14, transform=ax.transAxes)
            ax.axis('off')
            self._create_tab(fig, 'Нет данных')
            return

        # Создать фигуру
        fig = Figure(figsize=(10, 6))
        ax = fig.add_subplot(111)

        # Построить графики для всех переменных
        y_data_list = []
        for idx, (var_name, values) in enumerate(voltage_data.items()):
            if values:
                color = SCOPE_COLORS[idx % len(SCOPE_COLORS)]
                ax.plot(
                    sweep_data,
                    values,
                    linewidth=0.5,
                    label=var_name,
                    color=color,
                )
                y_data_list.append(values)

        # Определить тип переменных для подписи оси Y
        var_names = list(voltage_data.keys())
        ylabel = _get_ylabel_for_vars(var_names)

        ax.set_xlabel('Напряжение/ток свипирования (В/А)', fontsize=12)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.set_title('DC-анализ (DC Sweep)', fontsize=14, fontweight='bold')

        # Легенда если несколько переменных
        if len(voltage_data) > 1:
            ax.legend(loc='best', fontsize=10, facecolor=SCOPE_BG,
                      edgecolor=SCOPE_AXIS, labelcolor=SCOPE_TEXT)

        _apply_scope_style(fig, ax)
        fig.subplots_adjust(left=0.12, right=0.98, top=0.95, bottom=0.12)

        # Курсор
        canvas = FigureCanvas(fig)
        cursor = CursorTracker(ax, fig, canvas)
        cursor.set_data(np.array(sweep_data), y_data_list, line_objects=list(ax.lines))
        self._create_tab_with_canvas(fig, 'DC-анализ', canvas, cursor)

    def _plot_ac(self):
        """Построить графики AC-анализа (частотная характеристика / Bode plot)"""
        frequency_data, magnitude_data, phase_data = self._parse_ac_data_from_text()

        if not frequency_data:
            fig = Figure(figsize=(8, 4))
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5,
                    'Нет данных AC-анализа\nДобавьте .PRINT AC и .AC в netlist',
                    ha='center', va='center', fontsize=14, transform=ax.transAxes)
            ax.axis('off')
            self._create_tab(fig, 'Нет данных')
            return

        # Принудительно обрезать до минимальной длины
        min_len = len(frequency_data)
        for vals in magnitude_data.values():
            if len(vals) < min_len:
                min_len = len(vals)
        if min_len < len(frequency_data):
            frequency_data = frequency_data[:min_len]
            magnitude_data = {k: v[:min_len] for k, v in magnitude_data.items()}
            phase_data = {k: v[:min_len] for k, v in phase_data.items()}

        # --- Вкладка 1: Амплитудно-частотная характеристика (Bode magnitude) ---
        fig_mag = Figure(figsize=(10, 5))
        ax_mag = fig_mag.add_subplot(111)

        y_mag_list = []
        for idx, (var_name, values) in enumerate(magnitude_data.items()):
            if values:
                color = SCOPE_COLORS[idx % len(SCOPE_COLORS)]
                # Децибелы: 20 * log10(|V|)
                db_values = [20 * np.log10(max(abs(v), 1e-15)) for v in values]
                ax_mag.semilogx(
                    frequency_data,
                    db_values,
                    linewidth=0.5,
                    label=f'{var_name} (dB)',
                    color=color
                )
                y_mag_list.append(db_values)

        ax_mag.set_xlabel('Частота (Гц)', fontsize=12)
        ax_mag.set_ylabel('Амплитуда (дБ)', fontsize=12)
        ax_mag.set_title('АЧХ (Bode Magnitude Plot)', fontsize=14, fontweight='bold')
        if len(magnitude_data) > 1:
            ax_mag.legend(loc='best', fontsize=10, facecolor=SCOPE_BG,
                          edgecolor=SCOPE_AXIS, labelcolor=SCOPE_TEXT)
        _apply_scope_style(fig_mag, ax_mag)
        fig_mag.subplots_adjust(left=0.12, right=0.98, top=0.95, bottom=0.12)

        canvas_mag = FigureCanvas(fig_mag)
        cursor_mag = CursorTracker(ax_mag, fig_mag, canvas_mag)
        cursor_mag.set_data(np.array(frequency_data), y_mag_list,
                            line_objects=list(ax_mag.lines))
        self._create_tab_with_canvas(fig_mag, 'АЧХ (дБ)', canvas_mag, cursor_mag)

        # --- Вкладка 2: Фазо-частотная характеристика (Bode phase) ---
        fig_phase = Figure(figsize=(10, 5))
        ax_phase = fig_phase.add_subplot(111)

        y_phase_list = []
        for idx, (var_name, values) in enumerate(phase_data.items()):
            if values:
                color = SCOPE_COLORS[(idx + 2) % len(SCOPE_COLORS)]
                ax_phase.semilogx(
                    frequency_data,
                    values,
                    linewidth=0.5,
                    label=f'{var_name} (phase)',
                    color=color
                )
                y_phase_list.append(values)

        ax_phase.set_xlabel('Частота (Гц)', fontsize=12)
        ax_phase.set_ylabel('Фаза (рад)', fontsize=12)
        ax_phase.set_title('ФЧХ (Bode Phase Plot)', fontsize=14, fontweight='bold')
        if len(phase_data) > 1:
            ax_phase.legend(loc='best', fontsize=10, facecolor=SCOPE_BG,
                            edgecolor=SCOPE_AXIS, labelcolor=SCOPE_TEXT)
        _apply_scope_style(fig_phase, ax_phase)
        fig_phase.subplots_adjust(left=0.12, right=0.98, top=0.95, bottom=0.12)

        canvas_phase = FigureCanvas(fig_phase)
        cursor_phase = CursorTracker(ax_phase, fig_phase, canvas_phase)
        cursor_phase.set_data(np.array(frequency_data), y_phase_list,
                              line_objects=list(ax_phase.lines))
        self._create_tab_with_canvas(fig_phase, 'ФЧХ (фаза)', canvas_phase, cursor_phase)

        # --- Вкладка 3: Линейная амплитуда ---
        fig_linear = Figure(figsize=(10, 5))
        ax_linear = fig_linear.add_subplot(111)

        y_lin_list = []
        for idx, (var_name, values) in enumerate(magnitude_data.items()):
            if values:
                color = SCOPE_COLORS[idx % len(SCOPE_COLORS)]
                ax_linear.semilogx(
                    frequency_data,
                    values,
                    linewidth=0.5,
                    label=var_name,
                    color=color
                )
                y_lin_list.append(values)

        ax_linear.set_xlabel('Частота (Гц)', fontsize=12)
        ax_linear.set_ylabel('Амплитуда (В)', fontsize=12)
        ax_linear.set_title('АЧХ (линейная шкала)', fontsize=14, fontweight='bold')
        if len(magnitude_data) > 1:
            ax_linear.legend(loc='best', fontsize=10, facecolor=SCOPE_BG,
                             edgecolor=SCOPE_AXIS, labelcolor=SCOPE_TEXT)
        _apply_scope_style(fig_linear, ax_linear)
        fig_linear.subplots_adjust(left=0.12, right=0.98, top=0.95, bottom=0.12)

        canvas_linear = FigureCanvas(fig_linear)
        cursor_linear = CursorTracker(ax_linear, fig_linear, canvas_linear)
        cursor_linear.set_data(np.array(frequency_data), y_lin_list,
                               line_objects=list(ax_linear.lines))
        self._create_tab_with_canvas(fig_linear, 'АЧХ (линейн.)', canvas_linear, cursor_linear)

    def _plot_operating_point(self):
        """Отобразить рабочую точку"""
        voltages = {}
        lines = self.terminal_text.split('\n')
        in_node_section = False

        for line in lines:
            if 'Node' in line and 'Voltage' in line:
                in_node_section = True
                continue

            if in_node_section:
                match = re.match(r'\s*(\d+)\s+([0-9eE+\-.]+)', line)
                if match:
                    node = match.group(1)
                    voltage = float(match.group(2))
                    voltages[node] = voltage
                elif line.strip() and not re.match(r'\s*-+\s*', line):
                    if voltages:
                        break

        if not voltages:
            fig = Figure(figsize=(8, 4))
            ax = fig.add_subplot(111)
            ax.text(0.5, 0.5, 'Нет данных о рабочей точке',
                   ha='center', va='center', fontsize=14, transform=ax.transAxes)
            ax.axis('off')
            self._create_tab(fig, 'Рабочая точка')
            return

        fig = Figure(figsize=(8, 5))
        ax = fig.add_subplot(111)

        nodes = list(voltages.keys())
        values = list(voltages.values())

        bars = ax.bar(nodes, values, color=SCOPE_LINE, alpha=0.8, edgecolor=SCOPE_AXIS)
        ax.set_xlabel('Узел', fontsize=12)
        ax.set_ylabel('Напряжение (В)', fontsize=12)
        ax.set_title('Рабочая точка (Operating Point)', fontsize=14, fontweight='bold')
        _apply_scope_style(fig, ax)

        # Добавить значения на столбцы
        for bar, voltage in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 0.01,
                f'{voltage:.3f}В',
                ha='center',
                va='bottom',
                fontsize=9,
                color=SCOPE_TEXT,
            )

        self._create_tab(fig, 'Рабочая точка')
