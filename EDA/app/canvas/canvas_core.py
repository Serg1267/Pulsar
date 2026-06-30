# -*- coding: utf-8 -*-
"""Холст редактора: QGraphicsView + QGraphicsScene, координаты Y-вверх, сетка 100 mil, snap."""

from __future__ import annotations                    # Аннотации с отложенным вычислением

import json                                           # сериализация в буфер обмена
import math                                           # floor / ceil, тригонометрия для поворота
import re                                             # разбор refdes для повторного размещения

from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsItem,
                               QGraphicsRectItem, QGraphicsPathItem,
                               QGraphicsLineItem, QGraphicsSimpleTextItem,
                               QInputDialog, QDialog, QColorDialog,
                                QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                                QPushButton, QCheckBox, QTextEdit, QComboBox,
                               QApplication)  # View/Scene/Item
from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QPointF, QMimeData, Signal
from PySide6.QtGui import (QPainter, QMouseEvent, QCursor, QColor, QPen,
                           QBrush, QTransform, QPolygonF, QFont, QFontMetrics,
                           QPainterPath, QDragEnterEvent, QDropEvent,
                           QPixmap)  # Графика и курсор

from EDA.core.parser.sym_parser import SymData                                  # Данные .sym файла
from EDA.core.router import ManhattanRouter, WireItem, WireGraph                # Трассировка проводов
from EDA.core.router.wire_item import _WIRE_COLOR                               # Стандартный цвет провода
from EDA.app.items.component_item import ComponentGraphicsItem                  # QGraphicsItem компонента
from EDA.app.items.label_item import LabelItem                                  # Текстовая метка
from EDA.app.items.junction_item import JunctionItem                            # Точка соединения
from EDA.app.items.directive_item import DirectiveItem                          # SPICE-директива
from EDA.app.items.node_label_item import NetLabelItem                         # Метка узла
from EDA.app.items.text_item import TextItem                                   # Текстовый элемент
from EDA.app.items.rectangle_item import RectangleItem                         # Прямоугольник
from EDA.app.items.circle_item import CircleItem                               # Окружность
from EDA.app.items.colors import is_light_theme
from .canvas_wires import WireMixin
from .canvas_placement import PlacementMixin
from .canvas_selection import SelectionMixin
from .canvas_export import ExportMixin
from .canvas_serialization import SerializationMixin


class SchematicCanvas(SerializationMixin, ExportMixin, SelectionMixin, PlacementMixin, WireMixin, QGraphicsView):
    """Холст с системой координат Y-вверх, сеткой 100 mil и привязкой к ней."""

    # Сигнал: испускается при движении мыши с привязанными координатами (x, y в mil)
    position_changed = Signal(float, float)
    # Сигнал: изменение режима (wire_mode / normal)
    mode_changed = Signal(str)
    # Сигнал: компонент размещён (передаётся refdes)
    component_placed = Signal(str)
    # Сигнал: содержимое схемы изменилось
    modified = Signal()
    # Сигнал: изменилось выделение (для обновления тулбара)
    selection_changed = Signal()
    # Сигнал: drag & drop — начать размещение (sym_id)
    drag_placement_started = Signal(str)

    # Параметры сетки
    GRID_SPACING = 100         # Шаг сетки в mil
    SCENE_SIZE = 500000        # Размер сцены в mil (в каждую сторону от 0); большой — чтобы скроллбары не теряли диапазон при сильном зуме

    def __init__(self, parent=None):
        # Вызываем конструктор QGraphicsView
        super().__init__(parent)

        # --- Курсоры ---
        self._load_cursors()
        self._hand_cursor = QCursor(Qt.CursorShape.OpenHandCursor)

        # --- Сцена, центрированная вокруг (0, 0) ---

        # --- Сцена, центрированная вокруг (0, 0) ---
        half = self.SCENE_SIZE / 2.0
        self._scene = QGraphicsScene(QRectF(-half, -half, self.SCENE_SIZE, self.SCENE_SIZE), self)
        self.setScene(self._scene)

        # --- Настройки рендеринга ---
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)

        # --- Отключаем полосы прокрутки — панорамирование вручную (ПКМ) ---
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # --- Режим перетаскивания — NoDrag (своя обработка ЛКМ и ПКМ) ---
        self.setDragMode(self.DragMode.NoDrag)

        # --- Цвета фона ---
        # Фон всегда темный, независимо от темы
        self._bg_color = QColor("#0a0a0a")
        self._grid_color = QColor("#333333")
        self._grid_dots_color = QColor("#555555")
        self._origin_color = QColor("#777777")
        self._grid_dots = False  # False = линии, True = точки
        self._grid_enabled = True
        self.setBackgroundBrush(QBrush(self._bg_color))

        # --- Состояние зума ---
        self._zoom = 0.25

        # Применяем трансформацию: Y-вверх + масштаб
        self._update_transform()

        # Автоматически центрируем вид на начало координат (0, 0)
        self.centerOn(0.0, 0.0)

        # --- Служебные поля для панорамирования ---
        self._panning = False
        self._pan_start = QPoint(0, 0)

        # --- Служебные поля для выделения / перетаскивания (инициализация в SelectionMixin.__init__) ---
        # --- Вставка из буфера обмена (инициализация в SelectionMixin.__init__) ---

        # --- Показ и перетаскивание номеров узлов ---
        self._show_net_numbers = False
        self._net_label_positions: dict[int, QPointF] = {}
        self._drag_net_label_num: int | None = None
        self._drag_net_label_mouse_start: QPoint = QPoint(0, 0)
        self._drag_net_label_start_pos: QPointF = QPointF(0, 0)

        # --- Undo/Redo стек (snapshot-based) ---
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._undo_snapshot_disabled = False  # подавить snapshot при загрузке

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # Трансформация
    # ------------------------------------------------------------------
    def _update_transform(self):
        """Перестраивает матрицу трансформации: scale * Y-flip."""
        t = QTransform()
        t.scale(self._zoom, -self._zoom)
        self.setTransform(t)

    # ------------------------------------------------------------------
    # Отрисовка фона (сетка + маркер начала координат)
    # ------------------------------------------------------------------
    def _load_cursors(self):
        from pathlib import Path
        icons = Path(__file__).resolve().parent.parent.parent.parent / "resources" / "icons"
        light = is_light_theme()
        suffix = "" if not light else "_black"
        pw = icons / f"pencil_cursor{suffix}.png"
        if pw.exists():
            pm = QPixmap(str(pw))
            self._pencil_cursor = QCursor(pm, 2, 20)
        else:
            self._pencil_cursor = QCursor(Qt.CursorShape.CrossCursor)
        # Crosshair — кастомный, видимый на любом фоне
        cs = icons / f"crosshair_{'dark' if light else 'white'}.png"
        if cs.exists():
            pm = QPixmap(str(cs))
            self._cross_cursor = QCursor(pm, 12, 12)
        else:
            self._cross_cursor = QCursor(Qt.CursorShape.CrossCursor)

    def reload_cursors(self):
        """Вызывается при смене темы."""
        self._load_cursors()

    def set_grid_dots(self, dots: bool):
        self._grid_dots = dots
        self.viewport().update()

    def set_grid_enabled(self, enabled: bool):
        self._grid_enabled = enabled
        self.viewport().update()

    def set_background_color(self, color: QColor):
        self._bg_color = color
        self._grid_color = QColor("#333333")
        self._grid_dots_color = QColor("#555555")
        self.setBackgroundBrush(QBrush(self._bg_color))
        self.viewport().update()

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, self._bg_color)

        if self._grid_enabled:
            pixel_per_mil = self._zoom
            spacing = self.GRID_SPACING
            if pixel_per_mil * spacing < 5.0:
                n = int(5.0 / (pixel_per_mil * spacing)) + 1
                spacing *= n

            left   = math.floor(rect.left()   / self.GRID_SPACING) * self.GRID_SPACING
            right  = math.ceil(rect.right()   / self.GRID_SPACING) * self.GRID_SPACING
            top    = math.floor(rect.top()    / self.GRID_SPACING) * self.GRID_SPACING
            bottom = math.ceil(rect.bottom()  / self.GRID_SPACING) * self.GRID_SPACING

            if self._grid_dots:
                painter.setPen(QPen(self._grid_dots_color, 0.0))
                x = left
                while x <= right:
                    y = top
                    while y <= bottom:
                        painter.drawPoint(QPointF(x, y))
                        y += spacing
                    x += spacing
            else:
                painter.setPen(QPen(self._grid_color, 0.0))
                x = left
                while x <= right:
                    painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                    x += spacing

                y = top
                while y <= bottom:
                    painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
                    y += spacing

        marker = 200.0
        painter.setPen(QPen(self._origin_color, 0.0))
        painter.drawLine(QPointF(-marker, 0.0), QPointF(marker, 0.0))
        painter.drawLine(QPointF(0.0, -marker), QPointF(0.0, marker))

    def _get_net_label_rects(self) -> list[tuple[QRect, int, QPointF]]:
        """Вернуть [(viewport_rect, num, scene_pos), ...]."""
        if not self._show_net_numbers:
            return []
        result = []
        vp_w = self.viewport().width()
        vp_h = self.viewport().height()
        font = QFont("Monospace", 16)
        font.setBold(True)
        fm = QFontMetrics(font)
        for num, pt in list(self._net_label_positions.items()):
            vp = self.mapFromScene(QPointF(pt.x(), pt.y()))
            s = str(num)
            tw = fm.horizontalAdvance(s)
            th = fm.height()
            r = QRect(int(vp.x() - tw / 2 - 4), int(vp.y() - th / 2 - 2),
                      int(tw + 8), int(th + 4))
            if r.left() < vp_w and r.right() >= 0 and r.top() < vp_h and r.bottom() >= 0:
                result.append((r, num, pt))
        return result

    def drawForeground(self, painter: QPainter, rect: QRectF):
        labels = self._get_net_label_rects()
        if not labels:
            return
        painter.save()
        painter.setTransform(QTransform())
        font = QFont("Monospace", 16)
        font.setBold(True)
        painter.setFont(font)
        pen = QPen(QColor("#ff4444"))
        painter.setPen(pen)
        brush = QColor(0, 0, 0, 160)
        for r, num, _scene_pt in labels:
            painter.fillRect(r, brush)
            painter.setPen(pen)
            painter.drawText(r, int(Qt.AlignmentFlag.AlignCenter), str(num))
        painter.restore()

    def set_net_numbers_visible(self, visible: bool):
        self._show_net_numbers = visible
        if visible:
            self._update_net_labels()
        self.viewport().update()

    def _update_net_labels(self):
        """Сгруппировать провода BFS, назначить номера 1,2,3..., вычислить центр группы."""
        from EDA.core.router.wire_item import WireItem
        from EDA.app.items.component_item import ComponentGraphicsItem
        from EDA.app.items.node_label_item import NetLabelItem

        processed: set[WireItem] = set()
        net_groups: list[set[WireItem]] = []
        for item in self._scene.items():
            if not isinstance(item, WireItem):
                continue
            if item in processed:
                continue
            connected = self._wire_graph.get_connected(item)
            processed.update(connected)
            net_groups.append(connected)

        all_comps: dict[int, ComponentGraphicsItem] = {}
        power_ids: set[int] = set()
        for item in self._scene.items():
            if isinstance(item, ComponentGraphicsItem):
                cid = id(item)
                all_comps[cid] = item
                if "net" in item._data.attributes:
                    power_ids.add(cid)
                elif item._data.attributes.get("device", "").upper() == "GND":
                    power_ids.add(cid)

        new_positions: dict[int, QPointF] = {}
        net_counter = 0
        for group in net_groups:
            name = None
            is_gnd = False
            skip = False

            for (cid, _pin_idx), (w, _wi, _px, _py) in self._comp_wire_links.items():
                if cid not in power_ids:
                    continue
                if w not in group:
                    continue
                comp = all_comps.get(cid)
                if comp is None:
                    continue
                dev = comp._data.attributes.get("device", "").upper()
                net_attr = comp._data.attributes.get("net", "")
                if net_attr:
                    label = net_attr.split(":")[0] if ":" in net_attr else net_attr
                    if label.upper() == "GND":
                        is_gnd = True
                    else:
                        skip = True  # named power net (VCC, VDD и т.п.) — уже подписана символом
                    name = label
                elif dev == "GND":
                    is_gnd = True
                    name = "0"
                break

            if not skip and name is None:
                for item in self._scene.items():
                    if not isinstance(item, NetLabelItem):
                        continue
                    anchor = item.pos()
                    for w in group:
                        pts = w.points()
                        for i in range(len(pts) - 1):
                            a, b = pts[i], pts[i + 1]
                            if abs(a.x() - b.x()) < 0.1:
                                if abs(anchor.x() - a.x()) <= 30:
                                    if min(a.y(), b.y()) - 30 <= anchor.y() <= max(a.y(), b.y()) + 30:
                                        skip = True  # метка уже стоит — авто-номер не нужен
                                        break
                            else:
                                if abs(anchor.y() - a.y()) <= 30:
                                    if min(a.x(), b.x()) - 30 <= anchor.x() <= max(a.x(), b.x()) + 30:
                                        skip = True
                                        break
                    if skip:
                        break

            if skip:
                continue

            if name is None:
                net_counter += 1
                name = str(net_counter)
            elif name == "0":
                is_gnd = True

            if is_gnd:
                continue

            try:
                num = int(name)
            except ValueError:
                num = net_counter

            if num in self._net_label_positions:
                new_positions[num] = self._net_label_positions[num]
            else:
                pts_sum = QPointF(0, 0)
                count = 0
                for w in group:
                    for p in w.points():
                        pts_sum += p
                        count += 1
                new_positions[num] = pts_sum / count if count else QPointF(0, 0)

        self._net_label_positions = new_positions

    # ------------------------------------------------------------------
    # Zoom колесом мыши
    # ------------------------------------------------------------------
    def wheelEvent(self, event):
        old_scene_pos = self.mapToScene(event.position().toPoint())

        factor = 1.1 ** (event.angleDelta().y() / 120.0)
        self._zoom = max(0.01, min(100.0, self._zoom * factor))
        self._update_transform()

        new_viewport_pos = self.mapFromScene(old_scene_pos)
        delta = new_viewport_pos - event.position().toPoint()
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() + delta.x())
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() + delta.y())

        # Обновить перекрестие после зума/скролла
        if self._crosshair_pos is not None:
            self._show_crosshair(self._crosshair_pos)

        event.accept()

    # ------------------------------------------------------------------
    # Undo / Redo
    # ------------------------------------------------------------------
    def _save_snapshot(self):
        """Сохранить текущее состояние в undo-стек."""
        if self._undo_snapshot_disabled:
            return
        from EDA.app.serializer import serialize_canvas
        data = serialize_canvas(self)
        self._undo_stack.append(data)
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def can_undo(self) -> bool:
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self._redo_stack) > 0

    def _undo(self):
        """Отменить последнее действие."""
        if not self._undo_stack:
            return
        from EDA.app.serializer import serialize_canvas, deserialize_into_canvas
        current = serialize_canvas(self)
        self._redo_stack.append(current)
        data = self._undo_stack.pop()
        self._undo_snapshot_disabled = True
        deserialize_into_canvas(self, data)
        self._undo_snapshot_disabled = False
        self.modified.emit()

    def _redo(self):
        """Повторить отменённое действие."""
        if not self._redo_stack:
            return
        from EDA.app.serializer import serialize_canvas, deserialize_into_canvas
        current = serialize_canvas(self)
        self._undo_stack.append(current)
        data = self._redo_stack.pop()
        self._undo_snapshot_disabled = True
        deserialize_into_canvas(self, data)
        self._undo_snapshot_disabled = False
        self.modified.emit()




    # ------------------------------------------------------------------
    # Обработка мыши: ПКМ — панорамирование, ЛКМ — выделение/перетаскивание/трассировка
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            if self._paste_data is not None:
                self._cancel_paste()
                event.accept()
                return
            if self._place_sym_data is not None:
                self._cancel_placement()
                event.accept()
                return
            if self._wire_draw_mode and self._router.is_active:
                g = self.GRID_SPACING
                sp = self.mapToScene(event.pos())
                self._fix_segment(round(sp.x() / g) * g, round(sp.y() / g) * g)
                event.accept()
                return
            if self._router.is_active:
                self._router.reset()
                self._clear_routing_preview()
                event.accept()
                return
            self._panning = True
            self._pan_start = event.pos()
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.pos())

            # --- Вставка из буфера ---
            if self._paste_data is not None and self._paste_ghosts:
                g = self.GRID_SPACING
                sx = round(scene_pos.x() / g) * g
                sy = round(scene_pos.y() / g) * g
                self._commit_paste(sx, sy)
                event.accept()
                return

            # --- Перетаскивание номера узла ---
            if self._show_net_numbers:
                labels = self._get_net_label_rects()
                ep = event.pos()
                for r, num, _scene_pt in labels:
                    if r.contains(ep):
                        self._drag_net_label_num = num
                        self._drag_net_label_mouse_start = ep
                        self._drag_net_label_start_pos = self._net_label_positions[num]
                        event.accept()
                        return

            # --- Размещение фантома (повторное, пока не нажат Escape) ---
            if self._place_sym_data is not None and self._place_ghost is not None:
                g = self.GRID_SPACING
                x = round(scene_pos.x() / g) * g
                y = round(scene_pos.y() / g) * g
                offset = self._pin_grid_offset(self._place_sym_data, 0.0)
                self.place_component(self._place_sym_data, x, y,
                                     refdes=self._place_refdes, value=self._place_value)
                self.component_placed.emit(self._place_refdes)
                # Следующий refdes
                m = re.match(r'^([A-Za-z]+)(\d+)$', self._place_refdes)
                if m:
                    prefix, num_str = m.group(1), m.group(2)
                    self._place_refdes = f"{prefix}{int(num_str) + 1}"
                self._place_ghost.set_refdes(self._place_refdes)
                event.accept()
                return

            # --- Размещение метки узла ---
            if self._node_label_placement and self._node_label_ghost is not None:
                pos, _on_wire = self._snap_to_nearest_wire_or_pin(scene_pos, 60.0)
                self.place_node_label(self._node_label_text, pos)
                event.accept()
                return

            # --- Размещение текста ---
            if self._text_placement and self._text_ghost is not None:
                g = self.GRID_SPACING
                x = round(scene_pos.x() / g) * g
                y = round(scene_pos.y() / g) * g
                self.place_text(self._text_content, self._text_font_family,
                                self._text_font_size, x, y)
                event.accept()
                return

            # --- Размещение директивы ---
            if self._directive_placement and self._directive_ghost is not None:
                g = self.GRID_SPACING
                x = round(scene_pos.x() / g) * g
                y = round(scene_pos.y() / g) * g
                self.place_directive(self._directive_text, x, y)
                event.accept()
                return

            # --- Рисование прямоугольника ---
            if self._rect_placement:
                g = self.GRID_SPACING
                x = round(scene_pos.x() / g) * g
                y = round(scene_pos.y() / g) * g
                if self._rect_p1 is None:
                    self._rect_p1 = QPointF(x, y)
                    ghost = RectangleItem(x, y, x, y)
                    ghost.setOpacity(0.5)
                    ghost.setZValue(100)
                    self._scene.addItem(ghost)
                    self._rect_ghost = ghost
                else:
                    self.place_rect(self._rect_p1.x(), self._rect_p1.y(), x, y)
                event.accept()
                return

            # --- Рисование окружности ---
            if self._circle_placement:
                g = self.GRID_SPACING
                x = round(scene_pos.x() / g) * g
                y = round(scene_pos.y() / g) * g
                if self._circle_p1 is None:
                    self._circle_p1 = QPointF(x, y)
                    ghost = CircleItem(x, y, x, y)
                    ghost.setOpacity(0.5)
                    ghost.setZValue(100)
                    self._scene.addItem(ghost)
                    self._circle_ghost = ghost
                else:
                    self.place_circle(self._circle_p1.x(), self._circle_p1.y(), x, y)
                event.accept()
                return

            # --- Режим рисования сегментов (N) ---
            if self._wire_draw_mode:
                g = self.GRID_SPACING
                sx = round(scene_pos.x() / g) * g
                sy = round(scene_pos.y() / g) * g
                if self._router.is_active:
                    # Проверка на пин компонента
                    pin_hit = self._find_pin_at(scene_pos, 60.0)
                    if pin_hit is not None:
                        _, pin_pos = pin_hit
                        vertices = self._router.complete(pin_pos.x(), pin_pos.y())
                        if len(vertices) >= 2:
                            qpts = [QPointF(x, y) for x, y in vertices]
                            if self._last_segment_item is not None:
                                self._last_segment_item.set_show_end_pin(False)
                                self._wire_graph.remove_wire(self._last_segment_item)
                                self._last_segment_item.append_points(qpts[1:])
                                self._wire_graph.add_wire(self._last_segment_item)
                                self._update_connections_for_wire(self._last_segment_item)
                            else:
                                wire = WireItem(qpts, placed=True)
                                self._scene.addItem(wire)
                                self._wire_graph.add_wire(wire)
                                self._update_connections_for_wire(wire)
                        self._last_segment_item = None
                        self._clear_routing_preview()
                        self._clear_wire_hover()
                        self._save_snapshot()
                        self.modified.emit()
                        self._wire_draw_mode = False
                        self.unsetCursor()
                        self.mode_changed.emit("")
                        event.accept()
                        return

                    self._fix_segment(sx, sy)
                    # Проверка junction: конец сегмента на теле существующего провода
                    end_pt = QPointF(sx, sy)
                    body = self._find_wire_body_at(end_pt, 5.0)
                    if body is not None:
                        split_pairs = self._split_wire_at(end_pt)
                        junction = JunctionItem(end_pt)
                        self._scene.addItem(junction)
                        if split_pairs:
                            self._junction_split_map[junction] = split_pairs[0]
                    self._clear_wire_hover()
                    self.modified.emit()
                else:
                    self._router.start(sx, sy)
                event.accept()
                return

            # --- Старая трассировка проводов (W) ---
            if self._router.is_active:
                hit = self._find_pin_at(scene_pos, 60.0)
                if hit is not None:
                    _, pin_pos = hit
                    start = self._router.vertices[0] if self._router.vertices else None
                    if start is None or (abs(pin_pos.x() - start[0]) > 1
                                         or abs(pin_pos.y() - start[1]) > 1):
                        vertices = self._router.complete(pin_pos.x(), pin_pos.y())
                        if len(vertices) >= 2:
                            qpts = [QPointF(x, y) for x, y in vertices]
                            wire = WireItem(qpts, placed=True)
                            self._scene.addItem(wire)
                            self._wire_graph.add_wire(wire)
                            self._update_connections_for_wire(wire)
                    self._clear_routing_preview()
                    self._clear_wire_hover()
                    self._save_snapshot()
                    self.modified.emit()
                    event.accept()
                    return

                if self._wire_hover_pos is not None:
                    pos = self._wire_hover_pos
                    g = self.GRID_SPACING
                    snapped = QPointF(round(pos.x() / g) * g,
                                      round(pos.y() / g) * g)
                    split_pairs = self._split_wire_at(snapped)
                    self._router.commit(snapped.x(), snapped.y())
                    junction = JunctionItem(snapped)
                    self._scene.addItem(junction)
                    if split_pairs:
                        self._junction_split_map[junction] = split_pairs[0]
                    self._clear_wire_hover()
                    self._save_snapshot()
                    self.modified.emit()
                    event.accept()
                    return

                self._router.commit(scene_pos.x(), scene_pos.y())
                self._clear_routing_preview()
                self._clear_wire_hover()
                event.accept()
                return

            if self._wire_mode:
                hit = self._find_pin_at(scene_pos, 60.0)
                if hit is not None:
                    _, pin_pos = hit
                    self._router.start(pin_pos.x(), pin_pos.y())
                    self._clear_routing_preview()
                    event.accept()
                    return
                item = self.itemAt(event.pos())
                if isinstance(item, WireItem):
                    g = self.GRID_SPACING
                    sx = round(scene_pos.x() / g) * g
                    sy = round(scene_pos.y() / g) * g
                    # Рядом с концом провода? — начать трассу от него
                    near_end = False
                    for idx in (0, -1):
                        ep = item.points()[idx]
                        if (QPointF(sx, sy) - ep).manhattanLength() <= 30.0:
                            self._router.start(ep.x(), ep.y())
                            near_end = True
                            break
                    if not near_end:
                        # На теле провода — рассечь, junction, начать трассу
                        snapped = QPointF(sx, sy)
                        split_pairs = self._split_wire_at(snapped)
                        j = JunctionItem(snapped)
                        self._scene.addItem(j)
                        if split_pairs:
                            self._junction_split_map[j] = split_pairs[0]
                        self._refresh_wire_endpoint_pins(snapped)
                        self._router.start(sx, sy)
                    self._clear_routing_preview()
                    event.accept()
                    return
                self._deselect_all()
                event.accept()
                return

            item = self.itemAt(event.pos())
            if isinstance(item, DirectiveItem):
                self._save_snapshot()
                if item not in self._selected_items:
                    self._select_item(item)
                self._drag_primary = item
                self._drag_items = self._selected_items[:]
                self._drag_group_wires = []
                self._drag_junctions = []
                self._drag_offset = QPointF(
                    scene_pos.x() - item.pos().x(),
                    scene_pos.y() - item.pos().y(),
                )
                event.accept()
                return
            if isinstance(item, LabelItem):
                if item not in self._selected_items:
                    self._select_item(item)
                self._drag_primary_label = item
                parent = item.parentItem()
                if isinstance(parent, ComponentGraphicsItem):
                    parent_pos = parent.mapFromScene(scene_pos)
                    self._label_drag_offset = QPointF(
                        parent_pos.x() - item.pos().x(),
                        parent_pos.y() - item.pos().y(),
                    )
                event.accept()
                return
            if isinstance(item, ComponentGraphicsItem):
                self._save_snapshot()
                if item not in self._selected_items:
                    self._select_item(item)
                self._drag_items = [i for i in self._selected_items if isinstance(i, (ComponentGraphicsItem, DirectiveItem, NetLabelItem, TextItem, RectangleItem, CircleItem))]
                self._drag_primary = item
                self._drag_offset = QPointF(
                    scene_pos.x() - item.pos().x(),
                    scene_pos.y() - item.pos().y(),
                )
                # Собрать выделенные провода для совместного перемещения
                self._drag_group_wires = [i for i in self._selected_items if isinstance(i, WireItem)]
                self._drag_junctions = [i for i in self._selected_items if isinstance(i, JunctionItem)]
                self._drag_group_wires_removed = False
                # Разорвать соединения с проводами для перетаскивания
                self._drag_comp_wire_links.clear()
                for key, (w, w_idx, px, py) in list(self._comp_wire_links.items()):
                    comp_id, pin_idx = key
                    if comp_id == id(item):
                        if w in self._drag_group_wires:
                            continue  # провод уже перемещается вместе с группой
                        self._drag_comp_wire_links.append((w, w_idx))
                        self._wire_graph.remove_wire(w)
                        if w_idx == 0:
                            w.set_show_start_pin(True)
                        else:
                            w.set_show_end_pin(True)
                for drag_item in self._drag_items:
                    if isinstance(drag_item, ComponentGraphicsItem):
                        self._update_live_pins(drag_item)
                event.accept()
                return
            if isinstance(item, WireItem):
                self._save_snapshot()
                was_selected = item in self._selected_items
                if not was_selected:
                    self._deselect_all()
                    item.set_selected(True)
                    self._selected_items.append(item)
                sel_wires = [i for i in self._selected_items if isinstance(i, WireItem)]
                self._drag_junctions = [i for i in self._selected_items if isinstance(i, JunctionItem)]
                connected = self._wire_graph.get_connected(item)
                pin_hit = self._find_wire_pin_at(connected, scene_pos, 30.0)
                if pin_hit is not None and len(sel_wires) <= 1:
                    self._start_pin_drag(*pin_hit)
                else:
                    pts = item.points()
                    seg_idx = None
                    if len(pts) > 2 and len(sel_wires) == 1:
                        near_vertex = any(
                            (scene_pos - p).manhattanLength() <= 30.0
                            for p in pts
                        )
                        if not near_vertex:
                            for i in range(len(pts) - 1):
                                if self._point_on_manhattan_segment(
                                        scene_pos, pts[i], pts[i + 1], 30.0):
                                    seg_idx = i
                                    break
                    if seg_idx is not None:
                        self._segment_drag_wire = item
                        self._segment_drag_idx = seg_idx
                        item.set_active_segment(seg_idx)
                        g = self.GRID_SPACING
                        self._segment_drag_origin = QPointF(
                            round(scene_pos.x() / g) * g,
                            round(scene_pos.y() / g) * g,
                        )
                    else:
                        self._drag_wires = sel_wires or [item]
                        self._drag_wire_junctioned: set[tuple[int, int]] = set()
                        g = self.GRID_SPACING
                        self._drag_wire_last = QPointF(
                            round(scene_pos.x() / g) * g,
                            round(scene_pos.y() / g) * g,
                        )
                event.accept()
                return
            if isinstance(item, JunctionItem):
                if item not in self._selected_items:
                    self._select_item(item)
                event.accept()
                return
            if isinstance(item, NetLabelItem):
                self._save_snapshot()
                if item not in self._selected_items:
                    self._select_item(item)
                self._drag_primary = item
                self._drag_items = self._selected_items[:]
                self._drag_group_wires = []
                self._drag_junctions = []
                self._drag_offset = QPointF(
                    scene_pos.x() - item.pos().x(),
                    scene_pos.y() - item.pos().y(),
                )
                event.accept()
                return
            if isinstance(item, TextItem):
                self._save_snapshot()
                if item not in self._selected_items:
                    self._select_item(item)
                self._drag_primary = item
                self._drag_items = self._selected_items[:]
                self._drag_group_wires = []
                self._drag_junctions = []
                self._drag_offset = QPointF(
                    scene_pos.x() - item.pos().x(),
                    scene_pos.y() - item.pos().y(),
                )
                event.accept()
                return
            if isinstance(item, RectangleItem):
                if item not in self._selected_items:
                    self._select_item(item)
                handle = item.handle_at(scene_pos)
                if handle >= 0:
                    self._rect_resize_item = item
                    self._rect_resize_handle = handle
                else:
                    self._drag_primary = item
                    self._drag_items = self._selected_items[:]
                    self._drag_group_wires = []
                    self._drag_junctions = []
                    self._drag_offset = QPointF(
                        scene_pos.x() - item.pos().x(),
                        scene_pos.y() - item.pos().y(),
                    )
                event.accept()
                return
            if isinstance(item, CircleItem):
                if item not in self._selected_items:
                    self._select_item(item)
                handle = item.handle_at(scene_pos)
                if handle >= 0:
                    self._circle_resize_item = item
                    self._circle_resize_handle = handle
                else:
                    self._drag_primary = item
                    self._drag_items = self._selected_items[:]
                    self._drag_group_wires = []
                    self._drag_junctions = []
                    self._drag_offset = QPointF(
                        scene_pos.x() - item.pos().x(),
                        scene_pos.y() - item.pos().y(),
                    )
                event.accept()
                return
            # На пустом месте — начинаем rubber band
            self._rubber_start = scene_pos

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        scene_pos = self.mapToScene(event.pos())

        # --- Перетаскивание номера узла ---
        if self._drag_net_label_num is not None:
            dp = event.pos() - self._drag_net_label_mouse_start
            ds = self.mapToScene(event.pos()) - self.mapToScene(self._drag_net_label_mouse_start)
            self._net_label_positions[self._drag_net_label_num] = self._drag_net_label_start_pos + ds
            self.viewport().update()
            event.accept()
            return

        # --- Движение фантомов вставки ---
        if self._paste_ghosts:
            g = self.GRID_SPACING
            anchor_x = round(scene_pos.x() / g) * g
            anchor_y = round(scene_pos.y() / g) * g
            dx = anchor_x - self._paste_origin_x
            dy = anchor_y - self._paste_origin_y
            if self._paste_data is None:
                self._cancel_paste()
                return

            # Перемещаем ВСЕ фантомы в новую позицию
            for ghost_item in self._paste_ghosts:
                self._scene.removeItem(ghost_item)
            self._paste_ghosts = []

            from EDA.app.serializer import sym_data_from_dict
            data = self._paste_data

            for cd in data.get("components", []):
                sym_data = sym_data_from_dict(cd["sym_data"])
                ghost = ComponentGraphicsItem(sym_data, refdes=cd.get("refdes", "?"),
                                              value=cd.get("value", ""))
                ghost.set_model_line(cd.get("model_line", ""))
                ghost.setPos(cd["x"] + dx, cd["y"] + dy)
                ghost.setRotation(cd.get("rotation", 0.0))
                flip_x = cd.get("flip_x", False)
                flip_y = cd.get("flip_y", False)
                if flip_x or flip_y:
                    t = QTransform()
                    if flip_x:
                        t = t.scale(-1, 1)
                    if flip_y:
                        t = t.scale(1, -1)
                    ghost.setTransform(t)
                ghost.setOpacity(0.5)
                ghost.setZValue(100)
                self._scene.addItem(ghost)
                self._paste_ghosts.append(ghost)

            for wd in data.get("wires", []):
                pts = [QPointF(p[0] + dx, p[1] + dy) for p in wd["points"]]
                ghost_wire = WireItem(pts, placed=True,
                                      show_start_pin=wd.get("show_start_pin", True),
                                      show_end_pin=wd.get("show_end_pin", True))
                if "color" in wd:
                    ghost_wire.set_color(wd["color"])
                ghost_wire.setOpacity(0.5)
                ghost_wire.setZValue(100)
                self._scene.addItem(ghost_wire)
                self._paste_ghosts.append(ghost_wire)

            for jd in data.get("junctions", []):
                ghost_j = JunctionItem(QPointF(jd["x"] + dx, jd["y"] + dy))
                ghost_j.setOpacity(0.5)
                ghost_j.setZValue(100)
                self._scene.addItem(ghost_j)
                self._paste_ghosts.append(ghost_j)

            for dd in data.get("directives", []):
                ghost_d = DirectiveItem(dd["text"], dd["x"] + dx, dd["y"] + dy)
                ghost_d.setOpacity(0.5)
                ghost_d.setZValue(100)
                self._scene.addItem(ghost_d)
                self._paste_ghosts.append(ghost_d)

            for nd in data.get("node_labels", []):
                anchor = QPointF(nd["anchor_x"] + dx, nd["anchor_y"] + dy)
                lo = QPointF(nd.get("label_x", 250), nd.get("label_y", 250))
                ghost_nl = NetLabelItem(nd["text"], anchor, lo)
                ghost_nl.setOpacity(0.5)
                ghost_nl.setZValue(100)
                self._scene.addItem(ghost_nl)
                self._paste_ghosts.append(ghost_nl)

            for td in data.get("texts", []):
                ghost_t = TextItem(td["text"], td["x"] + dx, td["y"] + dy,
                                   td.get("font_family", "monospace"),
                                   td.get("font_size", 80))
                ghost_t.setOpacity(0.5)
                ghost_t.setZValue(100)
                self._scene.addItem(ghost_t)
                self._paste_ghosts.append(ghost_t)

            for rd in data.get("rectangles", []):
                r = rd["rect"]
                ghost_r = RectangleItem(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, rd.get("color"))
                ghost_r.setOpacity(0.5)
                ghost_r.setZValue(100)
                self._scene.addItem(ghost_r)
                self._paste_ghosts.append(ghost_r)

            event.accept()
            return

        # --- Движение фантома ---
        if self._place_ghost is not None:
            g = self.GRID_SPACING
            x = round(scene_pos.x() / g) * g
            y = round(scene_pos.y() / g) * g
            offset = self._pin_grid_offset(self._place_sym_data, 0.0)
            self._place_ghost.setPos(x + offset.x(), y + offset.y())
            event.accept()
            return

        # --- Движение фантома метки узла ---
        if self._node_label_placement and self._node_label_ghost is not None:
            pos, _on_wire = self._snap_to_nearest_wire_or_pin(scene_pos, 60.0)
            self._node_label_ghost.setPos(pos)
            event.accept()
            return

        # --- Движение фантома текста ---
        if self._text_placement and self._text_ghost is not None:
            g = self.GRID_SPACING
            x = round(scene_pos.x() / g) * g
            y = round(scene_pos.y() / g) * g
            self._text_ghost.setPos(x, y)
            event.accept()
            return

        # --- Движение фантома директивы ---
        if self._directive_placement and self._directive_ghost is not None:
            g = self.GRID_SPACING
            x = round(scene_pos.x() / g) * g
            y = round(scene_pos.y() / g) * g
            self._directive_ghost.setPos(x, y)
            event.accept()
            return

        # --- Движение фантома прямоугольника ---
        if self._rect_placement and self._rect_ghost is not None and self._rect_p1 is not None:
            g = self.GRID_SPACING
            x = round(scene_pos.x() / g) * g
            y = round(scene_pos.y() / g) * g
            self._rect_ghost.set_rect(self._rect_p1.x(), self._rect_p1.y(), x, y)
            event.accept()
            return

        # --- Движение фантома окружности ---
        if self._circle_placement and self._circle_ghost is not None and self._circle_p1 is not None:
            g = self.GRID_SPACING
            x = round(scene_pos.x() / g) * g
            y = round(scene_pos.y() / g) * g
            self._circle_ghost.set_rect(self._circle_p1.x(), self._circle_p1.y(), x, y)
            event.accept()
            return

        # --- Перекрестие в режиме проводов ---
        if self._wire_draw_mode or self._wire_mode:
            g = self.GRID_SPACING
            sx = round(scene_pos.x() / g) * g
            sy = round(scene_pos.y() / g) * g
            self._show_crosshair(QPointF(sx, sy))

        # --- Предпросмотр сегмента (режим N) ---
        if self._wire_draw_mode and self._router.is_active:
            pts = self._router.preview(scene_pos.x(), scene_pos.y())
            if len(pts) >= 2:
                path = QPainterPath()
                path.moveTo(pts[0][0], pts[0][1])
                for pt in pts[1:]:
                    path.lineTo(pt[0], pt[1])
                self._clear_routing_preview()
                pen = QPen(QColor("#ffcc00"), 2.0)
                pen.setCosmetic(True)
                pen.setStyle(Qt.PenStyle.SolidLine)
                self._routing_preview = self._scene.addPath(path, pen)
                # Проверка наведения конечной точки на тело провода или shared endpoint
                end_pt = QPointF(pts[-1][0], pts[-1][1])
                found = self._find_wire_body_at(end_pt, 5.0)
                if found is not None:
                    if self._wire_hover_pos is None or (self._wire_hover_pos - end_pt).manhattanLength() > 1:
                        self._wire_hover_pos = end_pt
                else:
                    self._wire_hover_pos = None
            else:
                self._clear_routing_preview()
            snap_x = round(scene_pos.x() / self.GRID_SPACING) * self.GRID_SPACING
            snap_y = round(scene_pos.y() / self.GRID_SPACING) * self.GRID_SPACING
            self.position_changed.emit(snap_x, snap_y)
            event.accept()
            return

        # --- Rubber-band трассировки (старый режим W) ---
        if self._router.is_active and not self._wire_draw_mode:
            pts = self._router.preview(scene_pos.x(), scene_pos.y())
            if len(pts) >= 2:
                path = QPainterPath()
                path.moveTo(pts[0][0], pts[0][1])
                for pt in pts[1:]:
                    path.lineTo(pt[0], pt[1])
                self._clear_routing_preview()
                pen = QPen(QColor("#ffcc00"), 2.0)
                pen.setCosmetic(True)
                pen.setStyle(Qt.PenStyle.DashLine)
                self._routing_preview = self._scene.addPath(path, pen)
                # Проверка наведения конечной точки на тело провода или shared endpoint
                end_pt = QPointF(pts[-1][0], pts[-1][1])
                found = self._find_wire_body_at(end_pt, 5.0)
                if found is not None:
                    if self._wire_hover_pos is None or (self._wire_hover_pos - end_pt).manhattanLength() > 1:
                        self._wire_hover_pos = end_pt
                else:
                    self._wire_hover_pos = None
            else:
                self._clear_routing_preview()
                self._wire_hover_pos = None
            snap_x = round(scene_pos.x() / self.GRID_SPACING) * self.GRID_SPACING
            snap_y = round(scene_pos.y() / self.GRID_SPACING) * self.GRID_SPACING
            self.position_changed.emit(snap_x, snap_y)
            event.accept()
            return

        # --- Панорамирование ПКМ ---
        if self._panning:
            delta = event.pos() - self._pan_start
            if not delta.isNull():
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta.y())
            self._pan_start = event.pos()
            event.accept()
            return

        # --- Перетаскивание надписи ---
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_primary_label:
            parent = self._drag_primary_label.parentItem()
            if isinstance(parent, ComponentGraphicsItem):
                scene_pos = self.mapToScene(event.pos())
                parent_pos = parent.mapFromScene(scene_pos)
                self._drag_primary_label.setPos(
                    parent_pos.x() - self._label_drag_offset.x(),
                    parent_pos.y() - self._label_drag_offset.y(),
                )
            event.accept()
            return

        # --- Rubber band (ЛКМ на пустом месте) ---
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._rubber_start is not None:
            cur = self.mapToScene(event.pos())
            rect = QRectF(self._rubber_start, cur).normalized()
            if self._rubber_item:
                self._rubber_item.setRect(rect)
            else:
                self._rubber_item = self._scene.addRect(
                    rect, QPen(QColor("#ffcc00"), 0.0), QBrush(QColor(255, 204, 0, 40)))
            event.accept()
            return

        # --- Resize прямоугольника ---
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._rect_resize_item is not None:
            scene_pos = self.mapToScene(event.pos())
            self._rect_resize_item.resize_handle(self._rect_resize_handle, scene_pos)
            event.accept()
            return

        # --- Resize окружности ---
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._circle_resize_item is not None:
            scene_pos = self.mapToScene(event.pos())
            self._circle_resize_item.resize_handle(self._circle_resize_handle, scene_pos)
            event.accept()
            return

        # --- Перетаскивание группы компонентов ---
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_primary:
            scene_pos = self.mapToScene(event.pos())
            primary = self._drag_primary
            if isinstance(primary, (DirectiveItem, NetLabelItem, TextItem, RectangleItem, CircleItem)):
                offset = QPointF(0, 0)
            else:
                offset = self._pin_grid_offset(primary._data, primary.rotation())
            g = self.GRID_SPACING
            new_x = round((scene_pos.x() - self._drag_offset.x() - offset.x()) / g) * g + offset.x()
            new_y = round((scene_pos.y() - self._drag_offset.y() - offset.y()) / g) * g + offset.y()
            dx = new_x - primary.pos().x()
            dy = new_y - primary.pos().y()
            for item in self._drag_items:
                item.setPos(item.pos().x() + dx, item.pos().y() + dy)
                if hasattr(item, 'set_drag_active'):
                    item.set_drag_active(True)
            # Растянуть соединённые провода вслед за компонентом
            for w, w_idx in self._drag_comp_wire_links:
                pts = list(w.points())
                target = 0 if w_idx == 0 else len(pts) - 1
                pts[target] = QPointF(pts[target].x() + dx, pts[target].y() + dy)
                w.set_points(pts)
                self._refresh_wire_endpoint_pins(pts[0])
                self._refresh_wire_endpoint_pins(pts[-1])
            # Переместить выделенные провода вместе с группой
            if self._drag_group_wires:
                if not self._drag_group_wires_removed:
                    for w in self._drag_group_wires:
                        self._wire_graph.remove_wire(w)
                        w.set_show_start_pin(True)
                        w.set_show_end_pin(True)
                    self._drag_group_wires_removed = True
                # Фаза 1: откатить junction ДО translate (на оригинальных точках)
                if dx != 0 or dy != 0:
                    for w in list(self._drag_group_wires):
                        for idx in (0, -1):
                            old_ep = w.points()[idx]
                            for jitem in list(self._scene.items()):
                                if isinstance(jitem, JunctionItem) and jitem in self._junction_split_map and jitem not in self._drag_junctions:
                                    if (jitem.pos() - old_ep).manhattanLength() <= 1:
                                        half_a, half_b = self._junction_split_map.get(jitem, (None, None))
                                        half_a_in = half_a is not None and half_a in self._drag_group_wires
                                        half_b_in = half_b is not None and half_b in self._drag_group_wires
                                        if half_a is not None and half_b is not None and half_a_in and half_b_in:
                                            # обе половины в группе — junction едет с группой
                                            self._hide_junction(jitem)
                                            if jitem not in self._drag_junctions:
                                                self._drag_junctions.append(jitem)
                                        else:
                                            # половина не в группе — сращиваем, junction удаляем
                                            self._junction_split_map.pop(jitem, None)
                                            self._scene.removeItem(jitem)
                                            if half_a is not None and half_b is not None and half_a.scene() is not None and half_b.scene() is not None:
                                                pts = half_a.points()[:-1] + half_b.points()
                                                rejoined = WireItem(pts, placed=True,
                                                                    show_start_pin=half_a._show_start_pin,
                                                                    show_end_pin=half_b._show_end_pin)
                                                self._wire_graph.remove_wire(half_a)
                                                self._wire_graph.remove_wire(half_b)
                                                self._scene.addItem(rejoined)
                                                self._scene.removeItem(half_a)
                                                self._scene.removeItem(half_b)
                                                if half_a_in:
                                                    self._drag_group_wires.remove(half_a)
                                                    self._drag_group_wires.append(rejoined)
                                                elif half_b_in:
                                                    self._drag_group_wires.remove(half_b)
                                                    self._drag_group_wires.append(rejoined)
                                                self._refresh_wire_endpoint_pins(rejoined.points()[0])
                                                self._refresh_wire_endpoint_pins(rejoined.points()[-1])
                                        break
                # Фаза 2: сначала перемещаем junction, чтобы пины корректно определялись
                for j in self._drag_junctions:
                    j.setPos(j.pos().x() + dx, j.pos().y() + dy)
                # Затем translate всех проводов и обновляем пины
                for w in self._drag_group_wires:
                    w.translate(dx, dy)
                    for idx in (0, -1):
                        self._refresh_wire_endpoint_pins(w.points()[idx])
            # Живое обновление пинов при перетаскивании
            for item in self._drag_items:
                if isinstance(item, ComponentGraphicsItem):
                    self._update_live_pins(item)
            event.accept()
            return

        # --- Pin drag (свободное перемещение пина) ---
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._pin_drag_ends:
            cur = self.mapToScene(event.pos())
            g = self.GRID_SPACING
            dx = round(cur.x() / g) * g - self._pin_drag_origin.x()
            dy = round(cur.y() / g) * g - self._pin_drag_origin.y()
            # Откатить постоянный junction ДО модификации точек,
            # чтобы _undo_junction_split использовала оригинальные (Manhattan) точки half‑wires
            if dx != 0 or dy != 0:
                old_ep = self._pin_drag_origin
                for item in list(self._scene.items()):
                    if isinstance(item, JunctionItem) and item in self._junction_split_map:
                        if (item.pos() - old_ep).manhattanLength() <= 1:
                            half_a, half_b = self._junction_split_map.pop(item, (None, None))
                            if half_a is None and half_b is None:
                                break
                            self._scene.removeItem(item)
                            # сращиваем половины
                            if half_a is not None and half_b is not None and half_a.scene() is not None and half_b.scene() is not None:
                                pts = half_a.points()[:-1] + half_b.points()
                                rejoined = WireItem(pts, placed=True,
                                                    show_start_pin=half_a._show_start_pin,
                                                    show_end_pin=half_b._show_end_pin)
                                self._wire_graph.remove_wire(half_a)
                                self._wire_graph.remove_wire(half_b)
                                self._scene.addItem(rejoined)
                                self._scene.removeItem(half_a)
                                self._scene.removeItem(half_b)
                                self._refresh_wire_endpoint_pins(rejoined.points()[0])
                                self._refresh_wire_endpoint_pins(rejoined.points()[-1])
                            break

            for w, idx in self._pin_drag_ends:
                orig = self._pin_drag_orig_points.get(w)
                if orig is None:
                    continue
                pts = list(orig)
                target = 0 if idx == 0 else len(pts) - 1
                pts[target] = QPointF(orig[target].x() + dx, orig[target].y() + dy)
                w.set_points(pts)
                new_ep = w.points()[target]
                self._refresh_wire_endpoint_pins(new_ep)
                # Временный junction при pin drag (без split)
                pkey = (id(w), target)
                old_j = self._drag_endpoint_junctions.pop(pkey, None)
                if old_j is not None:
                    if (old_j.pos() - new_ep).manhattanLength() > 1:
                        self._scene.removeItem(old_j)
                    else:
                        self._drag_endpoint_junctions[pkey] = old_j
                elif not self._junction_at(new_ep):
                    body = self._find_wire_body_at(new_ep, 5.0)
                    if body is not None:
                        j = JunctionItem(new_ep)
                        self._scene.addItem(j)
                        self._drag_endpoint_junctions[pkey] = j
                for item in self._scene.items():
                    if isinstance(item, ComponentGraphicsItem):
                        for pi, pp in enumerate(item._data.pins):
                            ps = item.mapToScene(item._p(pp.x1, pp.y1))
                            if (ps - new_ep).manhattanLength() <= 60.0:
                                self._update_live_pins(item)
                                break
            event.accept()
            return

        # --- Segment drag ---
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._segment_drag_wire is not None:
            cur = self.mapToScene(event.pos())
            g = self.GRID_SPACING
            cur_snapped = QPointF(round(cur.x() / g) * g, round(cur.y() / g) * g)
            dx = cur_snapped.x() - self._segment_drag_origin.x()
            dy = cur_snapped.y() - self._segment_drag_origin.y()
            if dx != 0 or dy != 0:
                w = self._segment_drag_wire
                idx = self._segment_drag_idx
                pts = w.points()
                p1, p2 = pts[idx], pts[idx + 1]
                is_horizontal = abs(p1.y() - p2.y()) < 0.1
                new_pts = list(pts)
                if is_horizontal:
                    dy = cur_snapped.y() - self._segment_drag_origin.y()
                    if dy != 0:
                        new_pts[idx] = QPointF(p1.x(), p1.y() + dy)
                        new_pts[idx + 1] = QPointF(p2.x(), p2.y() + dy)
                        w.set_points(new_pts)
                        self._segment_drag_origin = QPointF(
                            self._segment_drag_origin.x(), cur_snapped.y())
                else:
                    dx = cur_snapped.x() - self._segment_drag_origin.x()
                    if dx != 0:
                        new_pts[idx] = QPointF(p1.x() + dx, p1.y())
                        new_pts[idx + 1] = QPointF(p2.x() + dx, p2.y())
                        w.set_points(new_pts)
                        self._segment_drag_origin = QPointF(
                            cur_snapped.x(), self._segment_drag_origin.y())
            event.accept()
            return

        # --- Перетаскивание проводов ---
        if (event.buttons() & Qt.MouseButton.LeftButton) and self._drag_wires:
            cur = self.mapToScene(event.pos())
            g = self.GRID_SPACING
            cur_snapped = QPointF(round(cur.x() / g) * g, round(cur.y() / g) * g)
            last = self._drag_wire_last
            if last is not None:
                dx = cur_snapped.x() - last.x()
                dy = cur_snapped.y() - last.y()
                if dx != 0 or dy != 0:
                    if not self._drag_wires_removed:
                        for w in self._drag_wires:
                            self._wire_graph.remove_wire(w)
                            w.set_show_start_pin(True)
                            w.set_show_end_pin(True)
                        self._drag_wires_removed = True
                    # Фаза 1: откатить junction ДО translate (на оригинальных точках)
                    if dx != 0 or dy != 0:
                        for w in list(self._drag_wires):
                            for idx in (0, -1):
                                old_ep = w.points()[idx]
                                for jitem in list(self._scene.items()):
                                    if isinstance(jitem, JunctionItem) and jitem in self._junction_split_map and jitem not in self._drag_junctions:
                                        if (jitem.pos() - old_ep).manhattanLength() <= 1:
                                            half_a, half_b = self._junction_split_map.get(jitem, (None, None))
                                            half_a_in = half_a is not None and half_a in self._drag_wires
                                            half_b_in = half_b is not None and half_b in self._drag_wires
                                            if half_a is not None and half_b is not None and half_a_in and half_b_in:
                                                self._hide_junction(jitem)
                                                if jitem not in self._drag_junctions:
                                                    self._drag_junctions.append(jitem)
                                            else:
                                                self._junction_split_map.pop(jitem, None)
                                                self._scene.removeItem(jitem)
                                                if half_a is not None and half_b is not None and half_a.scene() is not None and half_b.scene() is not None:
                                                    pts = half_a.points()[:-1] + half_b.points()
                                                    rejoined = WireItem(pts, placed=True,
                                                                        show_start_pin=half_a._show_start_pin,
                                                                        show_end_pin=half_b._show_end_pin)
                                                    self._wire_graph.remove_wire(half_a)
                                                    self._wire_graph.remove_wire(half_b)
                                                    self._scene.addItem(rejoined)
                                                    self._scene.removeItem(half_a)
                                                    self._scene.removeItem(half_b)
                                                    if half_a_in:
                                                        self._drag_wires.remove(half_a)
                                                        self._drag_wires.append(rejoined)
                                                    elif half_b_in:
                                                        self._drag_wires.remove(half_b)
                                                        self._drag_wires.append(rejoined)
                                                    self._refresh_wire_endpoint_pins(rejoined.points()[0])
                                                    self._refresh_wire_endpoint_pins(rejoined.points()[-1])
                                            break

                    # Фаза 2: сдвинуть junction вместе с проводами
                    for j in self._drag_junctions:
                        j.setPos(j.pos().x() + dx, j.pos().y() + dy)

                    # Фаза 3: сдвинуть провода
                    for w in list(self._drag_wires):
                        w.translate(dx, dy)
                        self._refresh_wire_endpoint_pins(w.points()[0])
                        self._refresh_wire_endpoint_pins(w.points()[-1])
                    self._drag_wire_last = cur_snapped

        # --- Привязка (snap) к сетке и сигнал ---
        scene_pos = self.mapToScene(event.pos())
        snap_x = round(scene_pos.x() / self.GRID_SPACING) * self.GRID_SPACING
        snap_y = round(scene_pos.y() / self.GRID_SPACING) * self.GRID_SPACING
        self.position_changed.emit(snap_x, snap_y)

        # --- Перекрестие в режиме проводов (если не обновлено выше) ---
        if self._wire_draw_mode or self._wire_mode:
            g = self.GRID_SPACING
            sx = round(scene_pos.x() / g) * g
            sy = round(scene_pos.y() / g) * g
            self._show_crosshair(QPointF(sx, sy))

        # --- Курсор при наведении на узлы прямоугольника / окружности ---
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            if self._wire_draw_mode or self._wire_mode or self._rect_placement or self._circle_placement:
                self.setCursor(self._pencil_cursor)
            elif self._place_sym_data:
                self.setCursor(self._hand_cursor)
            else:
                hovered = False
                for item in self._scene.items():
                    if isinstance(item, (RectangleItem, CircleItem)) and item in self._selected_items:
                        if item.handle_at(scene_pos) >= 0:
                            self.setCursor(Qt.CursorShape.SizeAllCursor)
                            hovered = True
                            break
                if not hovered:
                    self.unsetCursor()

        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton and self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if self._rect_resize_item is not None:
                self._rect_resize_item = None
                self._rect_resize_handle = -1
                self.modified.emit()
                event.accept()
                return
            if self._circle_resize_item is not None:
                self._circle_resize_item = None
                self._circle_resize_handle = -1
                self.modified.emit()
                event.accept()
                return
            if self._router.is_active:
                event.accept()
                return
            if self._rubber_start is not None:
                self._end_rubber_band(self.mapToScene(event.pos()))
                event.accept()
                return
            if self._drag_primary_label:
                self._drag_primary_label = None
                event.accept()
                return
            if self._segment_drag_wire is not None:
                w = self._segment_drag_wire
                w.set_active_segment(-1)
                self._wire_graph.remove_wire(w)
                self._wire_graph.add_wire(w)
                self._update_connections_for_wire(w)
                for pt in (w.points()[0], w.points()[-1]):
                    for jitem in list(self._scene.items()):
                        if (isinstance(jitem, JunctionItem) and not jitem.isVisible()
                                and jitem in self._junction_split_map):
                            if (jitem.pos() - pt).manhattanLength() <= 1:
                                jitem.setVisible(True)
                                break
                self._segment_drag_wire = None
                self._segment_drag_idx = -1
                self.modified.emit()
                event.accept()
                return
            if self._drag_primary:
                if self._drag_comp_wire_links:
                    for w, w_idx in self._drag_comp_wire_links:
                        self._wire_graph.add_wire(w)
                    self._drag_comp_wire_links.clear()
                if self._drag_group_wires:
                    for w in self._drag_group_wires:
                        self._wire_graph.add_wire(w)
                    for w in list(self._drag_group_wires):
                        for pt in (w.points()[0], w.points()[-1]):
                            # Показать скрытый junction
                            for jitem in list(self._scene.items()):
                                if (isinstance(jitem, JunctionItem) and not jitem.isVisible()
                                        and jitem in self._junction_split_map):
                                    if (jitem.pos() - pt).manhattanLength() <= 1:
                                        jitem.setVisible(True)
                                        break
                            # T-junction если конец на теле другого провода (не из группы)
                            if not self._junction_at(pt):
                                body = self._find_wire_body_at(pt, 5.0)
                                if body is not None and body is not w and body not in self._drag_group_wires:
                                    split_pairs = self._split_wire_at(pt)
                                    if split_pairs:
                                        junction = JunctionItem(pt)
                                        self._scene.addItem(junction)
                                        self._junction_split_map[junction] = split_pairs[0]
                    # Восстановить видимость пинов на всех концах
                    for w in self._drag_group_wires:
                        for pt in (w.points()[0], w.points()[-1]):
                            self._refresh_wire_endpoint_pins(pt)
                    self._drag_group_wires.clear()
                    self._drag_group_wires_removed = False
                # Зафиксировать junction'ы component drag
                for (wid, idx), j in list(self._drag_endpoint_junctions.items()):
                    connected_wires = (w for w in self._scene.items()
                                       if isinstance(w, WireItem) and id(w) == wid)
                    target_wire = next(connected_wires, None)
                    if target_wire is not None:
                        ep = target_wire.points()[idx]
                        if (ep - j.pos()).manhattanLength() <= 1:
                            split_pairs = self._split_wire_at(j.pos())
                            if split_pairs:
                                self._junction_split_map[j] = split_pairs[0]
                            continue
                    self._scene.removeItem(j)
                self._drag_endpoint_junctions.clear()
                if isinstance(self._drag_primary, ComponentGraphicsItem):
                    self._break_comp_wire_connections(self._drag_primary)
                    self._update_comp_wire_connections(self._drag_primary)
                for item in self._drag_items:
                    if item is not self._drag_primary:
                        if isinstance(item, ComponentGraphicsItem):
                            self._update_comp_wire_connections(item)
                    if hasattr(item, 'set_drag_active'):
                        item.set_drag_active(False)
                self._drag_items.clear()
                self._drag_junctions.clear()
                self._drag_primary = None
                self.modified.emit()
                event.accept()
                return
            if self._pin_drag_ends:
                # Очистить старые связи для всех перетаскиваемых проводов
                drag_wires = {w for w, _ in self._pin_drag_ends}
                for key in list(self._comp_wire_links):
                    w = self._comp_wire_links[key][0]
                    if w in drag_wires:
                        _, _, px, py = self._comp_wire_links.pop(key)
                        w.set_show_pin_at(QPointF(px, py), True)
                        comp_id, pin_idx = key
                        for scene_item in self._scene.items():
                            if id(scene_item) == comp_id:
                                scene_item._connected_pins.discard(pin_idx)
                                scene_item.update()
                                break
                # Проверить концы всех перетаскиваемых проводов
                processed: set[WireItem] = set()
                for w, _ in list(self._pin_drag_ends):
                    if w in processed:
                        continue
                    processed.add(w)
                    pts = w.points()
                    if len(pts) < 2:
                        continue
                    degenerate = False
                    if len(pts) == 2 and (pts[0] - pts[1]).manhattanLength() < 1:
                        degenerate = True
                    if degenerate:
                        self._wire_graph.remove_wire(w)
                        self._scene.removeItem(w)
                        if w in self._selected_items:
                            self._selected_items.remove(w)
                    else:
                        self._wire_graph.remove_wire(w)
                        self._wire_graph.add_wire(w)
                        self._update_connections_for_wire(w)
                # Зафиксировать junction'ы pin drag
                drag_wire_set = {w for w, _ in self._pin_drag_ends}
                for (wid, idx), j in list(self._drag_endpoint_junctions.items()):
                    target_wire = next((w for w in drag_wire_set if id(w) == wid), None)
                    if target_wire is not None:
                        ep = target_wire.points()[idx]
                        if (ep - j.pos()).manhattanLength() <= 1:
                            split_pairs = self._split_wire_at(j.pos())
                            if split_pairs:
                                self._junction_split_map[j] = split_pairs[0]
                            continue
                    self._scene.removeItem(j)
                self._drag_endpoint_junctions.clear()
                # Показать скрытый junction если конец провода вернулся на место
                for w in drag_wire_set:
                    for ep_idx in (0, -1):
                        ep = w.points()[ep_idx]
                        for jitem in list(self._scene.items()):
                            if (isinstance(jitem, JunctionItem) and not jitem.isVisible()
                                    and jitem in self._junction_split_map):
                                if (jitem.pos() - ep).manhattanLength() <= 1:
                                    jitem.setVisible(True)
                                    break
                self._pin_drag_ends.clear()
                self.modified.emit()
                event.accept()
                return

            if self._drag_wires:
                # Очистить старые связи компонентов с перемещаемыми проводами
                for key in list(self._comp_wire_links):
                    w = self._comp_wire_links[key][0]
                    if w in self._drag_wires:
                        comp_id, pin_idx = key
                        self._comp_wire_links.pop(key)
                        for scene_item in self._scene.items():
                            if id(scene_item) == comp_id:
                                scene_item._connected_pins.discard(pin_idx)
                                scene_item.update()
                                break
                if self._drag_wires_removed:
                    for w in self._drag_wires:
                        self._wire_graph.add_wire(w)
                else:
                    for w in self._drag_wires:
                        self._wire_graph.remove_wire(w)
                        self._wire_graph.add_wire(w)
                for w in self._drag_wires:
                    self._update_connections_for_wire(w)
                # Показать скрытый junction если конец провода вернулся на место
                for w in self._drag_wires:
                    for pt in (w.points()[0], w.points()[-1]):
                        for jitem in list(self._scene.items()):
                            if (isinstance(jitem, JunctionItem) and not jitem.isVisible()
                                    and jitem in self._junction_split_map):
                                if (jitem.pos() - pt).manhattanLength() <= 1:
                                    jitem.setVisible(True)
                                    break
                # Зафиксировать junction'ы — разделить провода в точке соединения
                for (wid, idx), j in list(self._drag_endpoint_junctions.items()):
                    jpos = j.pos()
                    target_wire = next((w for w in self._drag_wires if id(w) == wid), None)
                    if target_wire is not None:
                        ep = target_wire.points()[idx]
                        if (ep - jpos).manhattanLength() <= 1:
                            split_pairs = self._split_wire_at(jpos)
                            if split_pairs:
                                self._junction_split_map[j] = split_pairs[0]
                        else:
                            self._scene.removeItem(j)
                    else:
                        self._scene.removeItem(j)
                self._drag_endpoint_junctions.clear()
                # Создать T-junction если конец провода на теле другого провода (не из группы)
                for w in list(self._drag_wires):
                    for pt in (w.points()[0], w.points()[-1]):
                        if not self._junction_at(pt):
                            body = self._find_wire_body_at(pt, 5.0)
                            if body is not None and body is not w and body not in self._drag_wires:
                                split_pairs = self._split_wire_at(pt)
                                if split_pairs:
                                    junction = JunctionItem(pt)
                                    self._scene.addItem(junction)
                                    self._junction_split_map[junction] = split_pairs[0]
                self._drag_wires.clear()
                self._drag_junctions.clear()
                self._drag_wire_last = None
                self._drag_wires_removed = False
                self.modified.emit()
                event.accept()
                return

        if self._drag_net_label_num is not None:
            self._drag_net_label_num = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Двойной клик — редактирование имени/номинала
    # ------------------------------------------------------------------
    def mouseDoubleClickEvent(self, event):
        self._save_snapshot()
        item = self.itemAt(event.pos())
        if isinstance(item, ComponentGraphicsItem):
            # Найти дочерние метки refdes и value
            refdes_label = None
            value_label = None
            for child in item.childItems():
                if isinstance(child, LabelItem):
                    if child.label_type() == "refdes":
                        refdes_label = child
                    elif child.label_type() == "value":
                        value_label = child

            dialog = QDialog(self)
            dialog.setWindowTitle("Редактирование компонента")
            layout = QVBoxLayout(dialog)

            layout.addWidget(QLabel("Имя (refdes):"))
            ed_refdes = QLineEdit(item.refdes())
            layout.addWidget(ed_refdes)

            _device = item._data.attributes.get("device", "").upper()
            if _device == "TRANSFORMER":
                _parts = item.value().split() if item.value() else ["1m", "1m", "0.99"]
                _l1 = _parts[0] if len(_parts) > 0 else "1m"
                _l2 = _parts[1] if len(_parts) > 1 else "1m"
                _k  = _parts[2] if len(_parts) > 2 else "0.99"
                _rp = item._data.attributes.get("transformer_rp", "")
                _rs = item._data.attributes.get("transformer_rs", "")
                _qle_style = "QLineEdit { color: #ffffff; background-color: #2b2b2b; }"
                layout.addWidget(QLabel("Индуктивность первичной обмотки (L1):"))
                ed_l1 = QLineEdit(_l1)
                ed_l1.setStyleSheet(_qle_style)
                layout.addWidget(ed_l1)
                layout.addWidget(QLabel("Индуктивность вторичной обмотки (L2):"))
                ed_l2 = QLineEdit(_l2)
                ed_l2.setStyleSheet(_qle_style)
                layout.addWidget(ed_l2)
                layout.addWidget(QLabel("Коэффициент связи (K):"))
                ed_k = QLineEdit(_k)
                ed_k.setStyleSheet(_qle_style)
                layout.addWidget(ed_k)
                layout.addWidget(QLabel("Сопротивление первичной обмотки (Rp):"))
                ed_rp = QLineEdit(_rp)
                ed_rp.setPlaceholderText("не задано")
                ed_rp.setStyleSheet(_qle_style)
                layout.addWidget(ed_rp)
                layout.addWidget(QLabel("Сопротивление вторичной обмотки (Rs):"))
                ed_rs = QLineEdit(_rs)
                ed_rs.setPlaceholderText("не задано")
                ed_rs.setStyleSheet(_qle_style)
                layout.addWidget(ed_rs)
                ed_value = None
            else:
                layout.addWidget(QLabel("Номинал (value):"))
                ed_value = QLineEdit(item.value())
                layout.addWidget(ed_value)

            cb_refdes = QCheckBox("Показать имя")
            cb_refdes.setChecked(refdes_label is None or refdes_label.isVisible())
            layout.addWidget(cb_refdes)

            cb_value = QCheckBox("Показать номинал")
            cb_value.setChecked(value_label is None or value_label.isVisible())
            layout.addWidget(cb_value)

            layout.addWidget(QLabel("Модель (.lib):"))
            ed_model = QTextEdit()
            ed_model.setMaximumHeight(200)
            ed_model.setPlaceholderText(".model 1N4148 D (IS=2.682n ...)")
            ed_model.setStyleSheet("QTextEdit { color: #ffffff; background-color: #2b2b2b; }")
            if _device == "TRANSFORMER":
                ed_model.setPlaceholderText("Трансформатор: обмотки L1/L2 + K генерируются автоматически")
                ed_model.setEnabled(False)
            elif item.model_line():
                ed_model.setText(item.model_line())
            else:
                _auto_model = item.value().strip()
                if _auto_model:
                    from pathlib import Path as _Path
                    import re as _re
                    _lib_dirs = [
                        _Path(__file__).resolve().parent.parent.parent.parent / "resources" / "LIB",
                        _Path(__file__).resolve().parent.parent.parent.parent / "Mod",
                    ]
                    for _ld in _lib_dirs:
                        if not _ld.exists():
                            continue
                        for _lf in _ld.rglob("*.lib"):
                            try:
                                _c = _lf.read_text(encoding="utf-8")
                            except Exception:
                                continue
                            # Склеить многострочные модели (+ continuation)
                            _flat = _re.sub(r'\n\s*\+\s*', ' ', _c)
                            for _m in _re.finditer(
                                r'^\s*\.model\s+(\S+)\s+(\S+)\s*(.*)',
                                _flat, _re.MULTILINE | _re.IGNORECASE
                            ):
                                if _m.group(1).upper() == _auto_model.upper():
                                    _full = f".model {_auto_model} {_m.group(2)} {_m.group(3)}"
                                    _full = _full.replace('\n', ' ').replace('\r', '')
                                    ed_model.setText(_full)
                                    break
                            if ed_model.toPlainText().strip():
                                break
                            # fallback: .subckt (полное тело от .SUBCKT до .ENDS)
                            for _m in _re.finditer(
                                r'^\s*\.subckt\s+(\S+)\s*(.*)',
                                _flat, _re.MULTILINE | _re.IGNORECASE
                            ):
                                if _m.group(1).upper() == _auto_model.upper():
                                    _start = _m.start()
                                    _ends = _re.search(r'^\s*\.ends\s+', _c[_start:], _re.MULTILINE | _re.IGNORECASE)
                                    if _ends:
                                        _full = _c[_start:_start + _ends.end() - 1]
                                    else:
                                        _full = f".SUBCKT {_auto_model} {_m.group(2)}"
                                    ed_model.setText(_full)
                                    break
                        if ed_model.toPlainText().strip():
                            break
            layout.addWidget(ed_model)

            layout.addWidget(QLabel("footprint:"))
            ed_fp = QComboBox()
            ed_fp.setEditable(True)
            _common_fp = [
                "acy(200)", "acy(300)", "acy(400)", "acy(500)", "acy(600)",
                "acy(800)", "acy(1000)",
                "rcy(100)", "rcy(200)",
                "dip(6)", "dip(8)", "dip(14)",
                "to92", "to220", "led5",
                "connector(1,2)", "connector(2,2)",
                "MLT-0.125", "MLT-0.25", "MLT-0.5", "MLT-1", "MLT-2",
            ]
            ed_fp.addItems(_common_fp)
            _cur_fp = item._data.attributes.get("footprint", "")
            if _cur_fp:
                ed_fp.setCurrentText(_cur_fp)
            layout.addWidget(ed_fp)

            btn_layout = QHBoxLayout()
            btn_cancel = QPushButton("Отмена")
            btn_cancel.clicked.connect(dialog.reject)
            btn_layout.addWidget(btn_cancel)
            btn_layout.addStretch()
            btn_ok = QPushButton("OK")
            btn_ok.clicked.connect(dialog.accept)
            btn_layout.addWidget(btn_ok)
            layout.addLayout(btn_layout)

            if dialog.exec() == QDialog.DialogCode.Accepted:
                refdes = ed_refdes.text().strip()
                if refdes:
                    item.set_refdes(refdes)
                if _device == "TRANSFORMER":
                    _l1 = ed_l1.text().strip() or "1m"
                    _l2 = ed_l2.text().strip() or "1m"
                    _k  = ed_k.text().strip() or "0.99"
                    item.set_value(f"{_l1} {_l2} {_k}")
                    _rp = ed_rp.text().strip()
                    _rs = ed_rs.text().strip()
                    if _rp:
                        item._data.attributes["transformer_rp"] = _rp
                    elif "transformer_rp" in item._data.attributes:
                        del item._data.attributes["transformer_rp"]
                    if _rs:
                        item._data.attributes["transformer_rs"] = _rs
                    elif "transformer_rs" in item._data.attributes:
                        del item._data.attributes["transformer_rs"]
                else:
                    value = ed_value.text().strip()
                    item.set_value(value)
                item.set_model_line(ed_model.toPlainText().strip())
                fp = ed_fp.currentText().strip()
                if fp:
                    item._data.attributes["footprint"] = fp
                elif "footprint" in item._data.attributes:
                    del item._data.attributes["footprint"]
                if refdes_label:
                    refdes_label.setVisible(cb_refdes.isChecked())
                if value_label:
                    value_label.setVisible(cb_value.isChecked())
                self.modified.emit()
            event.accept()
            return
        if isinstance(item, LabelItem):
            new_text, ok = QInputDialog.getText(
                self, "Редактирование", "Текст:", text=item._text)
            if ok:
                item.set_text(new_text.strip())
                # Если это номинал или refdes — обновить и в родительском компоненте
                parent = item.parentItem()
                if isinstance(parent, ComponentGraphicsItem):
                    if item.label_type() == "value":
                        parent.set_value(new_text.strip())
                    elif item.label_type() == "refdes":
                        parent.set_refdes(new_text.strip())
                self.modified.emit()
            event.accept()
            return
        if isinstance(item, DirectiveItem):
            new_text, ok = QInputDialog.getText(
                self, "Редактировать директиву", "Директива:",
                text=item.text())
            if ok:
                item.set_text(new_text.strip())
                self.modified.emit()
            event.accept()
            return
        if isinstance(item, NetLabelItem):
            new_text, ok = QInputDialog.getText(
                self, "Метка узла", "Имя узла (латиница + цифры):",
                text=item.text())
            if ok:
                t = new_text.strip()
                if t and re.match(r'^[A-Za-z][A-Za-z0-9_]*$', t):
                    item.set_text(t)
                    self.modified.emit()
            event.accept()
            return
        if isinstance(item, WireItem):
            connected = self._wire_graph.get_connected(item)
            current_color = item.color()
            initial = QColor(current_color) if current_color else QColor(_WIRE_COLOR)
            dialog = QColorDialog(initial, self)
            dialog.setWindowTitle("Цвет провода")
            dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
            dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            reset_btn = QPushButton("Сбросить")
            reset_btn.clicked.connect(lambda: dialog.setCurrentColor(QColor(_WIRE_COLOR)))
            dialog.layout().addWidget(reset_btn)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                chosen = dialog.currentColor().name()
                target = None if chosen == _WIRE_COLOR else chosen
                for w in connected:
                    w.set_color(target)
                self.modified.emit()
            event.accept()
            return
        if isinstance(item, TextItem):
            from PySide6.QtWidgets import QFontComboBox, QSpinBox
            dialog = QDialog(self)
            dialog.setWindowTitle("Редактирование текста")
            layout = QVBoxLayout(dialog)
            font_layout = QHBoxLayout()
            font_layout.addWidget(QLabel("Шрифт:"))
            font_combo = QFontComboBox()
            font_combo.setCurrentFont(QFont(item.font_family()))
            font_layout.addWidget(font_combo)
            font_layout.addWidget(QLabel("Размер:"))
            size_spin = QSpinBox()
            size_spin.setRange(8, 200)
            size_spin.setValue(item.font_size())
            font_layout.addWidget(size_spin)
            layout.addLayout(font_layout)
            text_edit = QTextEdit()
            text_edit.setPlainText(item.text())
            text_edit.setMinimumHeight(100)
            layout.addWidget(text_edit)
            btn_layout = QHBoxLayout()
            btn_cancel = QPushButton("Отмена")
            btn_cancel.clicked.connect(dialog.reject)
            btn_layout.addWidget(btn_cancel)
            btn_layout.addStretch()
            btn_ok = QPushButton("OK")
            btn_ok.clicked.connect(dialog.accept)
            btn_layout.addWidget(btn_ok)
            layout.addLayout(btn_layout)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                new_text = text_edit.toPlainText()
                new_family = font_combo.currentFont().family()
                new_size = size_spin.value()
                if new_text.strip():
                    item.set_text(new_text)
                    item.set_font(new_family, new_size)
                    self.modified.emit()
            event.accept()
            return
        if isinstance(item, RectangleItem):
            current_color = item.color()
            initial = QColor(current_color) if current_color else QColor("#00ff88")
            dialog = QColorDialog(initial, self)
            dialog.setWindowTitle("Цвет прямоугольника")
            dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
            dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            reset_btn = QPushButton("Сбросить")
            reset_btn.clicked.connect(lambda: dialog.setCurrentColor(QColor("#00ff88")))
            dialog.layout().addWidget(reset_btn)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                chosen = dialog.currentColor().name()
                target = None if chosen == "#00ff88" else chosen
                item.set_color(target)
                self.modified.emit()
            event.accept()
            return
        if isinstance(item, CircleItem):
            current_color = item.color()
            initial = QColor(current_color) if current_color else QColor("#00ff88")
            dialog = QColorDialog(initial, self)
            dialog.setWindowTitle("Цвет окружности")
            dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
            dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
            reset_btn = QPushButton("Сбросить")
            reset_btn.clicked.connect(lambda: dialog.setCurrentColor(QColor("#00ff88")))
            dialog.layout().addWidget(reset_btn)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                chosen = dialog.currentColor().name()
                target = None if chosen == "#00ff88" else chosen
                item.set_color(target)
                self.modified.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Клавиатура
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self._delete_selected()
            event.accept()
            return

        if event.key() == Qt.Key.Key_Escape:
            if self._paste_data is not None:
                self._cancel_paste()
                event.accept()
                return
            if self._rect_placement:
                self._cancel_rect_placement()
                event.accept()
                return
            if self._circle_placement:
                self._cancel_circle_placement()
                event.accept()
                return
            if self._text_placement:
                self._cancel_text_placement()
                event.accept()
                return
            if self._directive_placement:
                self._cancel_directive_placement()
                event.accept()
                return
            if self._node_label_placement:
                self._cancel_node_label_placement()
                event.accept()
                return
            if self._place_sym_data is not None:
                self._cancel_placement()
                event.accept()
                return
            if self._wire_draw_mode:
                self._wire_draw_mode = False
                self.unsetCursor()
                self._hide_crosshair()
                self._router.reset()
                self._clear_routing_preview()
                self._last_segment_item = None
                self.mode_changed.emit("")
                event.accept()
                return
            if self._router.is_active:
                self._router.reset()
                self._clear_routing_preview()
                event.accept()
                return
            if self._wire_mode:
                self._wire_mode = False
                self._hide_crosshair()
                self.unsetCursor()
                self.mode_changed.emit("")
                event.accept()
                return
            self._edit_mode = False
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_N and not (event.modifiers() & Qt.ControlModifier):
            if not self._wire_draw_mode:
                # Проверить, не наведён ли курсор на тело провода
                vp_pos = self.viewport().mapFromGlobal(QCursor.pos())
                sp = self.mapToScene(vp_pos)
                g = self.GRID_SPACING
                sx = round(sp.x() / g) * g
                sy = round(sp.y() / g) * g
                body = self._find_wire_body_at(QPointF(sx, sy), 5.0)
                if body is not None:
                    # На теле провода → junction + вход в N-mode
                    snapped = QPointF(sx, sy)
                    split_pairs = self._split_wire_at(snapped)
                    j = JunctionItem(snapped)
                    self._scene.addItem(j)
                    if split_pairs:
                        self._junction_split_map[j] = split_pairs[0]
                    self._refresh_wire_endpoint_pins(snapped)
                    self._wire_draw_mode = True
                    self._wire_mode = False
                    self.setCursor(self._pencil_cursor)
                    self._router.reset()
                    self._clear_routing_preview()
                    self._last_segment_item = None
                    self._router.start(sx, sy)
                    self.mode_changed.emit("SEGMENT")
                    event.accept()
                    return
            self._wire_draw_mode = not self._wire_draw_mode
            if self._wire_draw_mode:
                self.setCursor(self._pencil_cursor)
                self._wire_mode = False
                self._router.reset()
                self._clear_routing_preview()
                self._last_segment_item = None
                # Автостарт сегмента от текущей позиции курсора
                g = self.GRID_SPACING
                vp_pos = self.viewport().mapFromGlobal(QCursor.pos())
                sp = self.mapToScene(vp_pos)
                sx = round(sp.x() / g) * g
                sy = round(sp.y() / g) * g
                self._router.start(sx, sy)
                sp_snapped = QPointF(sx, sy)
                self._show_crosshair(sp_snapped)
            else:
                self.unsetCursor()
                self._hide_crosshair()
                self._router.reset()
                self._clear_routing_preview()
                self._clear_wire_hover()
                self._last_segment_item = None
            self.mode_changed.emit("SEGMENT" if self._wire_draw_mode else "")
            event.accept()
            return

        if event.key() == Qt.Key.Key_W and not (event.modifiers() & Qt.ControlModifier):
            self._wire_mode = not self._wire_mode
            if self._wire_mode:
                self._wire_draw_mode = False
                self._router.reset()
                self._clear_routing_preview()
                self.setCursor(self._pencil_cursor)
                self._show_crosshair(QPointF(0, 0))
            else:
                self.unsetCursor()
                self._hide_crosshair()
            self.mode_changed.emit("WIRE" if self._wire_mode else "")
            event.accept()
            return

        if event.key() == Qt.Key.Key_E:
            if self._selected_items:
                self._edit_mode = True
            event.accept()
            return

        if self._edit_mode and event.key() == Qt.Key.Key_R:
            self._edit_mode = False
            self._rotate_selected(-90.0)
            event.accept()
            return

        if (event.modifiers() & Qt.ControlModifier):
            if event.key() == Qt.Key.Key_A:
                self._select_all()
                event.accept()
                return
            if event.key() == Qt.Key.Key_H:
                self._flip_selected_horizontal()
                event.accept()
                return
            if event.key() == Qt.Key.Key_V:
                self._flip_selected_vertical()
                event.accept()
                return

        # ── Горячие клавиши для быстрого размещения компонентов ──
        if not (event.modifiers() & Qt.ControlModifier):
            _hotkey_map = {
                Qt.Key.Key_R: "resistor-2",
                Qt.Key.Key_C: "capacitor-1",
                Qt.Key.Key_D: "diode-1",
                Qt.Key.Key_Q: "npn-1",
                Qt.Key.Key_G: "gnd-1",
                Qt.Key.Key_V: "vsin-1",
            }
            sym_id = _hotkey_map.get(event.key())
            if sym_id is not None:
                self._cancel_placement()
                self._cancel_node_label_placement()
                self._cancel_text_placement()
                self._cancel_rect_placement()
                self._cancel_circle_placement()
                if self._wire_draw_mode:
                    self._wire_draw_mode = False
                    self._router.reset()
                    self._clear_routing_preview()
                    self._last_segment_item = None
                if self._wire_mode:
                    self._wire_mode = False
                self.mode_changed.emit("")
                self.drag_placement_started.emit(sym_id)
                self.setCursor(self._hand_cursor)
                event.accept()
                return

        # ── Стрелки: сдвиг выделенного на шаг сетки ──
        if not (event.modifiers() & Qt.ControlModifier):
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                               Qt.Key.Key_Up, Qt.Key.Key_Down):
                if self._selected_items:
                    self._nudge_selected(event.key())
                    event.accept()
                    return

        self._edit_mode = False
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Drag & Drop из панели компонентов
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasFormat("application/x-spiceeda-component"):
            sym_id = bytes(event.mimeData().data("application/x-spiceeda-component")).decode("utf-8")
            self.drag_placement_started.emit(sym_id)
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat("application/x-spiceeda-component"):
            if self._place_ghost is not None:
                sp = self.mapToScene(event.position().toPoint())
                g = self.GRID_SPACING
                x = round(sp.x() / g) * g
                y = round(sp.y() / g) * g
                offset = self._pin_grid_offset(self._place_sym_data, 0.0)
                self._place_ghost.setPos(x + offset.x(), y + offset.y())
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasFormat("application/x-spiceeda-component"):
            if self._place_ghost is not None and self._place_sym_data is not None:
                sp = self.mapToScene(event.position().toPoint())
                g = self.GRID_SPACING
                x = round(sp.x() / g) * g
                y = round(sp.y() / g) * g
                offset = self._pin_grid_offset(self._place_sym_data, 0.0)
                self.place_component(self._place_sym_data, x, y,
                                     refdes=self._place_refdes, value=self._place_value)
                self.component_placed.emit(self._place_refdes)
                self._cancel_placement()
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def dragLeaveEvent(self, event):
        super().dragLeaveEvent(event)

    # ------------------------------------------------------------------
    # Смещение для выравнивания пинов по сетке
    # ------------------------------------------------------------------
    @staticmethod
    def get_snapped(self, scene_pos: QPointF):
        x = round(scene_pos.x() / self.GRID_SPACING) * self.GRID_SPACING
        y = round(scene_pos.y() / self.GRID_SPACING) * self.GRID_SPACING
        return QPointF(x, y)

# ======================================================================


