# -*- coding: utf-8 -*-
"""Холст редактора: QGraphicsView + QGraphicsScene, координаты Y-вверх, сетка 100 mil, snap."""

from __future__ import annotations                    # Аннотации с отложенным вычислением

import json                                           # сериализация в буфер обмена
import math                                           # floor / ceil, тригонометрия для поворота
import re                                             # разбор refdes для повторного размещения

from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsItem,
                               QGraphicsRectItem, QGraphicsPathItem,
                               QGraphicsLineItem,
                               QInputDialog, QDialog, QColorDialog,
                                QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                                QPushButton, QCheckBox, QTextEdit, QComboBox,
                               QApplication)  # View/Scene/Item
from PySide6.QtCore import Qt, QRectF, QPoint, QPointF, QMimeData, Signal
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


class SchematicCanvas(QGraphicsView):
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
        if is_light_theme():
            self._bg_color = QColor("#ffffff")
            self._grid_color = QColor("#cccccc")
            self._grid_dots_color = QColor("#aaaaaa")
        else:
            self._bg_color = QColor("#1e1e1e")
            self._grid_color = QColor("#2a2a2a")
            self._grid_dots_color = QColor("#555555")
        self._origin_color = QColor("#555555")
        self._grid_dots = False  # False = линии, True = точки
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

        # --- Служебные поля для выделения / перетаскивания ---
        self._selected_items: list[QGraphicsItem] = []
        self._drag_items: list[ComponentGraphicsItem] = []
        self._drag_primary: ComponentGraphicsItem | None = None
        self._drag_offset = QPointF(0, 0)
        self._drag_primary_label: LabelItem | None = None
        self._label_drag_offset = QPointF(0, 0)
        self._edit_mode = False
        self._rubber_start: QPointF | None = None
        self._rubber_item: QGraphicsRectItem | None = None

        # --- Трассировка проводов ---
        self._wire_mode = False
        self._wire_draw_mode = False
        self._router = ManhattanRouter(self.GRID_SPACING)
        self._routing_preview: QGraphicsItem | None = None
        self._last_segment_item: WireItem | None = None

        # --- Перекрестие (crosshair) для режима проводов ---
        self._crosshair_pos: QPointF | None = None
        self._crosshair_v: QGraphicsLineItem | None = None
        self._crosshair_h: QGraphicsLineItem | None = None
        self._wire_graph = WireGraph()
        self._drag_wires: list[WireItem] = []
        self._drag_wire_last: QPointF | None = None
        self._drag_wires_removed: bool = False
        self._drag_endpoint_junctions: dict[tuple[int, int], JunctionItem] = {}
        self._pin_drag_ends: list[tuple[WireItem, int]] = []
        self._pin_drag_origin: QPointF = QPointF(0, 0)
        self._pin_drag_orig_points: dict[WireItem, list[QPointF]] = {}
        self._comp_wire_links: dict[tuple[int, int], tuple[WireItem, int, float, float]] = {}
        self._drag_comp_wire_links: list[tuple[WireItem, int]] = []
        self._drag_group_wires: list[WireItem] = []
        self._drag_group_wires_removed: bool = False
        self._drag_junctions: list[JunctionItem] = []

        self._segment_drag_wire: WireItem | None = None
        self._segment_drag_idx: int = -1
        self._segment_drag_origin: QPointF = QPointF(0, 0)

        self._wire_hover_pos: QPointF | None = None
        self._junction_split_map: dict[JunctionItem, tuple[WireItem, WireItem]] = {}

        # --- Размещение компонента (фантом) ---
        self._place_sym_data: SymData | None = None
        self._place_refdes: str = ""
        self._place_value: str = ""
        self._place_ghost: ComponentGraphicsItem | None = None

        # --- Размещение метки узла (фантом) ---
        self._node_label_placement: bool = False
        self._node_label_text: str = ""
        self._node_label_ghost: NetLabelItem | None = None

        # --- Размещение текста (фантом) ---
        self._text_placement: bool = False
        self._text_content: str = ""
        self._text_font_family: str = "monospace"
        self._text_font_size: int = 80
        self._text_ghost: TextItem | None = None

        # --- Размещение директивы (фантом) ---
        self._directive_placement: bool = False

        # --- Вставка из буфера обмена (фантом) ---
        self._paste_data: dict | None = None
        self._paste_ghosts: list = []
        self._paste_origin_x: float = 0.0
        self._paste_origin_y: float = 0.0
        self._directive_text: str = ""
        self._directive_ghost: DirectiveItem | None = None

        # --- Рисование прямоугольника ---
        self._rect_placement: bool = False
        self._rect_p1: QPointF | None = None
        self._rect_ghost: RectangleItem | None = None
        self._rect_resize_item: RectangleItem | None = None
        self._rect_resize_handle: int = -1

        # --- Рисование окружности ---
        self._circle_placement: bool = False
        self._circle_p1: QPointF | None = None
        self._circle_ghost: CircleItem | None = None
        self._circle_resize_item: CircleItem | None = None
        self._circle_resize_handle: int = -1

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
        icons = Path(__file__).parent.parent.parent / "resources" / "icons"
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

    def set_background_color(self, color: QColor):
        self._bg_color = color
        if color.lightness() > 128:
            self._grid_color = QColor("#cccccc")
            self._grid_dots_color = QColor("#aaaaaa")
        else:
            self._grid_color = QColor("#2a2a2a")
            self._grid_dots_color = QColor("#555555")
        self.setBackgroundBrush(QBrush(self._bg_color))
        self.viewport().update()

    def drawBackground(self, painter: QPainter, rect: QRectF):
        painter.fillRect(rect, self._bg_color)

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
    # Буфер обмена (копировать/вырезать/вставить)
    # ------------------------------------------------------------------
    def _clipboard_data(self) -> dict | None:
        """Собрать JSON-данные выделенных элементов для буфера обмена."""
        from EDA.app.serializer import sym_data_to_dict
        data: dict = {"version": 2, "format": "spiceeda-schematic-clipboard"}

        comps = [i for i in self._selected_items if isinstance(i, ComponentGraphicsItem)]
        if comps:
            data["components"] = []
            for item in comps:
                t = item.transform()
                cd = {
                    "sym_data": sym_data_to_dict(item._data),
                    "x": item.pos().x(),
                    "y": item.pos().y(),
                    "rotation": item.rotation(),
                    "flip_x": t.m11() < 0,
                    "flip_y": t.m22() < 0,
                    "refdes": item.refdes(),
                    "value": item.value(),
                    "model_line": item.model_line(),
                    "labels": [],
                }
                for child in item.childItems():
                    if not isinstance(child, LabelItem):
                        continue
                    ct = child.transform()
                    cd["labels"].append({
                        "type": child.label_type(),
                        "text": child.text(),
                        "rel_x": child.pos().x(),
                        "rel_y": child.pos().y(),
                        "rotation": child.rotation(),
                        "counter_flip_x": ct.m11() < 0,
                        "counter_flip_y": ct.m22() < 0,
                    })
                data["components"].append(cd)

        wires = [i for i in self._selected_items if isinstance(i, WireItem)]
        if wires:
            data["wires"] = []
            for item in wires:
                pts = item.points()
                wd = {
                    "points": [[p.x(), p.y()] for p in pts],
                    "show_start_pin": item._show_start_pin,
                    "show_end_pin": item._show_end_pin,
                }
                if item.color() is not None:
                    wd["color"] = item.color()
                data["wires"].append(wd)

        juncs = [i for i in self._selected_items if isinstance(i, JunctionItem)]
        if juncs:
            data["junctions"] = [{"x": i.pos().x(), "y": i.pos().y()} for i in juncs]

        dirs = [i for i in self._selected_items if isinstance(i, DirectiveItem)]
        if dirs:
            data["directives"] = [{"text": i.text(), "x": i.pos().x(), "y": i.pos().y()} for i in dirs]

        nls = [i for i in self._selected_items if isinstance(i, NetLabelItem)]
        if nls:
            data["node_labels"] = []
            for item in nls:
                lo = item.label_offset()
                data["node_labels"].append({
                    "text": item.text(),
                    "anchor_x": item.pos().x(),
                    "anchor_y": item.pos().y(),
                    "label_x": lo.x(),
                    "label_y": lo.y(),
                })

        txts = [i for i in self._selected_items if isinstance(i, TextItem)]
        if txts:
            data["texts"] = []
            for item in txts:
                data["texts"].append({
                    "text": item.text(),
                    "x": item.pos().x(),
                    "y": item.pos().y(),
                    "font_family": item.font_family(),
                    "font_size": item.font_size(),
                })

        rects = [i for i in self._selected_items if isinstance(i, RectangleItem)]
        if rects:
            data["rectangles"] = []
            for item in rects:
                rd = {"rect": list(item.rect())}
                if item.color() is not None:
                    rd["color"] = item.color()
                data["rectangles"].append(rd)

        circles = [i for i in self._selected_items if isinstance(i, CircleItem)]
        if circles:
            data["circles"] = []
            for item in circles:
                cd = {"rect": list(item.rect())}
                if item.color() is not None:
                    cd["color"] = item.color()
                data["circles"].append(cd)

        return data if any(k in data for k in ("components", "wires", "junctions",
                                                  "directives", "node_labels", "texts", "rectangles", "circles")) else None

    def copy_selected(self):
        data = self._clipboard_data()
        if data is None:
            return
        QApplication.clipboard().setText(json.dumps(data))

    def cut_selected(self):
        self.copy_selected()
        self._save_snapshot()
        self._delete_selected()
        self.modified.emit()

    def paste_from_clipboard(self):
        text = QApplication.clipboard().text()
        if not text:
            return
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return
        if data.get("format") != "spiceeda-schematic-clipboard":
            return
        self._start_paste(data)

    def _start_paste(self, data: dict):
        """Войти в режим вставки: фантомы следуют за курсором, ЛКМ — вставить."""
        self._cancel_paste()
        self._cancel_placement()
        self._cancel_node_label_placement()
        self._cancel_text_placement()
        self._cancel_directive_placement()
        self._cancel_rect_placement()
        self._cancel_circle_placement()

        from EDA.app.serializer import sym_data_from_dict

        # Вычислить bounding box
        def _update_bb(x, y):
            nonlocal min_x, min_y, max_x, max_y
            min_x = min(min_x, x); min_y = min(min_y, y)
            max_x = max(max_x, x); max_y = max(max_y, y)

        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        for cd in data.get("components", []):
            _update_bb(cd["x"], cd["y"])
        for wd in data.get("wires", []):
            for px, py in wd["points"]:
                _update_bb(px, py)
        for jd in data.get("junctions", []):
            _update_bb(jd["x"], jd["y"])
        for dd in data.get("directives", []):
            _update_bb(dd["x"], dd["y"])
        for nd in data.get("node_labels", []):
            _update_bb(nd["anchor_x"], nd["anchor_y"])
        for td in data.get("texts", []):
            _update_bb(td["x"], td["y"])
        for rd in data.get("rectangles", []):
            r = rd["rect"]
            _update_bb(r[0], r[1]); _update_bb(r[2], r[3])
        for cd in data.get("circles", []):
            r = cd["rect"]
            _update_bb(r[0], r[1]); _update_bb(r[2], r[3])

        if min_x == float('inf'):
            return

        self._paste_data = data
        self._paste_origin_x = (min_x + max_x) / 2
        self._paste_origin_y = (min_y + max_y) / 2
        self._paste_ghosts = []
        g = self.GRID_SPACING

        # Создать фантомы со смещением под курсор
        vp_pos = self.viewport().mapFromGlobal(QCursor.pos())
        sp = self.mapToScene(vp_pos)
        anchor_x = round(sp.x() / g) * g
        anchor_y = round(sp.y() / g) * g
        dx = anchor_x - self._paste_origin_x
        dy = anchor_y - self._paste_origin_y

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

        for cd in data.get("circles", []):
            r = cd["rect"]
            ghost_c = CircleItem(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, cd.get("color"))
            ghost_c.setOpacity(0.5)
            ghost_c.setZValue(100)
            self._scene.addItem(ghost_c)
            self._paste_ghosts.append(ghost_c)

        self.setCursor(self._cross_cursor)
        self.mode_changed.emit("PASTE")

    def _cancel_paste(self):
        if self._paste_ghosts:
            for g_item in self._paste_ghosts:
                self._scene.removeItem(g_item)
            self._paste_ghosts = []
        self._paste_data = None
        self._paste_origin_x = 0.0
        self._paste_origin_y = 0.0
        self.unsetCursor()
        self.mode_changed.emit("")

    def _commit_paste(self, snap_x: float, snap_y: float):
        """Разместить элементы из буфера обмена в точке (snap_x, snap_y)."""
        if self._paste_data is None:
            return
        self._save_snapshot()
        self._cancel_placement()
        self._cancel_node_label_placement()
        self._cancel_text_placement()
        self._cancel_directive_placement()
        self._cancel_rect_placement()
        self._cancel_circle_placement()

        data = self._paste_data
        dx = snap_x - self._paste_origin_x
        dy = snap_y - self._paste_origin_y

        from EDA.app.serializer import sym_data_from_dict

        refdes_used: set[str] = set()
        for item in self._scene.items():
            if isinstance(item, ComponentGraphicsItem):
                refdes_used.add(item.refdes())

        def next_refdes(refdes: str) -> str:
            m = re.match(r'^([A-Za-z]+)(\d+)$', refdes)
            if not m:
                return refdes
            prefix = m.group(1)
            base_n = int(m.group(2))
            n = base_n
            candidate = f"{prefix}{n}"
            while candidate in refdes_used:
                n += 1
                candidate = f"{prefix}{n}"
            refdes_used.add(candidate)
            return candidate

        for cd in data.get("components", []):
            sym_data = sym_data_from_dict(cd["sym_data"])
            refdes = next_refdes(cd.get("refdes", "?"))
            comp = ComponentGraphicsItem(sym_data, refdes=refdes,
                                         value=cd.get("value", ""))
            comp.set_model_line(cd.get("model_line", ""))
            comp.setPos(cd["x"] + dx, cd["y"] + dy)
            comp.setRotation(cd.get("rotation", 0.0))
            flip_x = cd.get("flip_x", False)
            flip_y = cd.get("flip_y", False)
            if flip_x or flip_y:
                t = QTransform()
                if flip_x:
                    t = t.scale(-1, 1)
                if flip_y:
                    t = t.scale(1, -1)
                comp.setTransform(t)
            self._scene.addItem(comp)
            self.component_placed.emit(refdes)

            saved_labels = cd.get("labels", [])
            for sl in saved_labels:
                for child in comp.childItems():
                    if not isinstance(child, LabelItem):
                        continue
                    if child.label_type() == sl["type"]:
                        child.set_text(sl["text"])
                        child.setPos(sl["rel_x"], sl["rel_y"])
                        child.setRotation(sl.get("rotation", 0.0))
                        cfx = sl.get("counter_flip_x", False)
                        cfy = sl.get("counter_flip_y", False)
                        if cfx or cfy:
                            t = QTransform()
                            if cfx:
                                t = t.scale(-1, 1)
                            if cfy:
                                t = t.scale(1, -1)
                            child.setTransform(t)
                        break

        for wd in data.get("wires", []):
            pts = [QPointF(p[0] + dx, p[1] + dy) for p in wd["points"]]
            wire = WireItem(pts, placed=True,
                            show_start_pin=wd.get("show_start_pin", True),
                            show_end_pin=wd.get("show_end_pin", True))
            if "color" in wd:
                wire.set_color(wd["color"])
            self._scene.addItem(wire)
            self._wire_graph.add_wire(wire)

        for jd in data.get("junctions", []):
            j = JunctionItem(QPointF(jd["x"] + dx, jd["y"] + dy))
            self._scene.addItem(j)

        for dd in data.get("directives", []):
            directive = DirectiveItem(dd["text"], dd["x"] + dx, dd["y"] + dy)
            self._scene.addItem(directive)

        for nd in data.get("node_labels", []):
            anchor = QPointF(nd["anchor_x"] + dx, nd["anchor_y"] + dy)
            lo = QPointF(nd.get("label_x", 250), nd.get("label_y", 250))
            label = NetLabelItem(nd["text"], anchor, lo)
            self._scene.addItem(label)

        for td in data.get("texts", []):
            text_item = TextItem(td["text"], td["x"] + dx, td["y"] + dy,
                                 td.get("font_family", "monospace"),
                                 td.get("font_size", 80))
            self._scene.addItem(text_item)

        for rd in data.get("rectangles", []):
            r = rd["rect"]
            rect_item = RectangleItem(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, rd.get("color"))
            self._scene.addItem(rect_item)

        for cd in data.get("circles", []):
            r = cd["rect"]
            circle_item = CircleItem(r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, cd.get("color"))
            self._scene.addItem(circle_item)

        # Восстановление соединений
        for item in self._scene.items():
            if isinstance(item, ComponentGraphicsItem):
                self._update_comp_wire_connections(item)

        self._cancel_paste()
        self._deselect_all()
        self.modified.emit()

    # ------------------------------------------------------------------
    # Выделение / снятие выделения
    # ------------------------------------------------------------------
    def _deselect_all(self):
        """Снимает выделение со всех компонентов и надписей."""
        for item in self._selected_items:
            item.set_selected(False)
        self._selected_items.clear()
        self._edit_mode = False
        self.selection_changed.emit()

    def _select_all(self):
        """Выделяет все элементы на сцене."""
        self._deselect_all()
        for item in self._scene.items():
            if isinstance(item, (ComponentGraphicsItem, LabelItem, WireItem,
                                 JunctionItem, DirectiveItem, NetLabelItem,
                                 TextItem, RectangleItem, CircleItem)):
                self._selected_items.append(item)
                item.set_selected(True)
                if isinstance(item, ComponentGraphicsItem):
                    item.setZValue(1)
        self.selection_changed.emit()

    def _select_item(self, item: QGraphicsItem):
        """Выделяет один элемент (компонент или надпись), снимая предыдущее."""
        self._deselect_all()
        self._selected_items = [item]
        item.set_selected(True)
        if isinstance(item, ComponentGraphicsItem):
            item.setZValue(1)
        self.selection_changed.emit()

    def _rotate_selected(self, angle_delta: float = 90.0):
        """Поворачивает все выделенные элементы на angle_delta градусов."""
        self._save_snapshot()
        selected_comp_parents = {id(item) for item in self._selected_items
                                 if isinstance(item, ComponentGraphicsItem)}
        for item in list(self._selected_items):
            if isinstance(item, LabelItem) and id(item.parentItem()) in selected_comp_parents:
                continue
            if hasattr(item, 'rotate'):
                item.rotate(angle_delta)
            if isinstance(item, ComponentGraphicsItem):
                self._align_pins_to_grid(item)
                for label in item._label_items:
                    label.rotate(-angle_delta)
        self.modified.emit()

    def _flip_selected_horizontal(self):
        """Отражает все выделенные элементы по горизонтали (X)."""
        self._save_snapshot()
        selected_parents = {item for item in self._selected_items
                           if isinstance(item, ComponentGraphicsItem)}
        for item in self._selected_items:
            if isinstance(item, ComponentGraphicsItem):
                t = item.transform()
                t.scale(-1, 1)
                item.setTransform(t)
                self._align_pins_to_grid(item)
                # контр-отражение надписей, чтобы текст оставался читаемым
                for label in item._label_items:
                    lt = label.transform()
                    lt.scale(-1, 1)
                    label.setTransform(lt)
            elif isinstance(item, LabelItem):
                if item.parentItem() in selected_parents:
                    continue  # родитель уже отразил и контр-отразил
                t = item.transform()
                t.scale(-1, 1)
                item.setTransform(t)
        self.modified.emit()

    def _flip_selected_vertical(self):
        """Отражает все выделенные элементы по вертикали (Y)."""
        self._save_snapshot()
        selected_parents = {item for item in self._selected_items
                           if isinstance(item, ComponentGraphicsItem)}
        for item in self._selected_items:
            if isinstance(item, ComponentGraphicsItem):
                t = item.transform()
                t.scale(1, -1)
                item.setTransform(t)
                self._align_pins_to_grid(item)
                for label in item._label_items:
                    lt = label.transform()
                    lt.scale(1, -1)
                    label.setTransform(lt)
            elif isinstance(item, LabelItem):
                if item.parentItem() in selected_parents:
                    continue
                t = item.transform()
                t.scale(1, -1)
                item.setTransform(t)
        self.modified.emit()

    def _nudge_selected(self, key):
        """Сдвинуть выделенные элементы на шаг сетки по стрелке."""
        g = self.GRID_SPACING
        dx = dy = 0
        if key == Qt.Key.Key_Left:
            dx = -g
        elif key == Qt.Key.Key_Right:
            dx = g
        elif key == Qt.Key.Key_Up:
            dy = g
        elif key == Qt.Key.Key_Down:
            dy = -g

        self._save_snapshot()

        moved_comps: list[ComponentGraphicsItem] = []
        selected_wire_ids = {id(w) for w in self._selected_items if isinstance(w, WireItem)}

        for item in list(self._selected_items):
            if isinstance(item, ComponentGraphicsItem):
                moved_comps.append(item)
                # Подвинуть концы невыделенных проводов, привязанных к компоненту
                for key, (w, w_idx, _px, _py) in list(self._comp_wire_links.items()):
                    if key[0] == id(item):
                        if id(w) not in selected_wire_ids:
                            pts = list(w.points())
                            target = 0 if w_idx == 0 else len(pts) - 1
                            pts[target] = QPointF(pts[target].x() + dx,
                                                  pts[target].y() + dy)
                            w.set_points(pts)
                        self._comp_wire_links.pop(key)
                item.setPos(item.pos().x() + dx, item.pos().y() + dy)
            elif isinstance(item, (DirectiveItem, NetLabelItem, TextItem, RectangleItem, CircleItem)):
                item.setPos(item.pos().x() + dx, item.pos().y() + dy)
            elif isinstance(item, WireItem):
                pts = [QPointF(p.x() + dx, p.y() + dy) for p in item.points()]
                self._wire_graph.remove_wire(item)
                item.set_points(pts)
                self._wire_graph.add_wire(item)

        for comp in moved_comps:
            self._update_comp_wire_connections(comp)

        self.modified.emit()

    def _select_items_in_rect(self, rect: QRectF):
        """Выделяет все элементы, пересекающие прямоугольную область."""
        self._deselect_all()
        for i in self._scene.items(rect, Qt.ItemSelectionMode.IntersectsItemShape):
            if isinstance(i, (ComponentGraphicsItem, LabelItem, WireItem,
                              JunctionItem, NetLabelItem, DirectiveItem, TextItem)):
                i.set_selected(True)
                if isinstance(i, ComponentGraphicsItem):
                    i.setZValue(1)
                self._selected_items.append(i)
            elif isinstance(i, RectangleItem):
                if rect.intersects(i.boundingRect().translated(i.pos())):
                    i.set_selected(True)
                    self._selected_items.append(i)
            elif isinstance(i, CircleItem):
                if rect.intersects(i.boundingRect().translated(i.pos())):
                    i.set_selected(True)
                    self._selected_items.append(i)
        self.selection_changed.emit()

    def _end_rubber_band(self, scene_pos: QPointF):
        """Завершает rubber band: выбирает компоненты в рамке или снимает выделение."""
        if self._rubber_item:
            self._scene.removeItem(self._rubber_item)
            self._rubber_item = None
        if self._rubber_start is not None:
            rect = QRectF(self._rubber_start, scene_pos).normalized()
            self._rubber_start = None
            if rect.width() > 5 and rect.height() > 5:
                self._select_items_in_rect(rect)
            else:
                self._deselect_all()

    def _delete_selected(self):
        """Удаляет выделенные компоненты/надписи/провода/junction со сцены."""
        self._save_snapshot()
        deleted_endpoints: list[QPointF] = []
        for item in list(self._selected_items):
            if isinstance(item, JunctionItem):
                # Удаление junction напрямую — срастить половины проводов
                rejoined = self._undo_junction_split(item)
                if rejoined is None:
                    self._record_split_halves(item, item.pos())
                    rejoined = self._undo_junction_split(item)
                if rejoined is None:
                    self._scene.removeItem(item)
                continue
            if isinstance(item, ComponentGraphicsItem):
                # Собрать провода, подключённые к компоненту
                comp_wires: set[WireItem] = set()
                for key in list(self._comp_wire_links):
                    if key[0] == id(item):
                        comp_wires.add(self._comp_wire_links[key][0])
                self._break_comp_wire_connections(item)
                # Удалить эти провода
                for wire in comp_wires:
                    if wire not in self._selected_items:
                        deleted_endpoints.append(wire.points()[0])
                        deleted_endpoints.append(wire.points()[-1])
                        self._wire_graph.remove_wire(wire)
                        self._scene.removeItem(wire)
            if isinstance(item, WireItem):
                deleted_endpoints.append(item.points()[0])
                deleted_endpoints.append(item.points()[-1])
                self._wire_graph.remove_wire(item)
                # Очистить связи компонентов с этим проводом
                for key in list(self._comp_wire_links):
                    w = self._comp_wire_links[key][0]
                    if w is item:
                        comp_id, pin_idx = key
                        self._comp_wire_links.pop(key)
                        # Найти компонент по id и обновить _connected_pins
                        for scene_item in self._scene.items():
                            if id(scene_item) == comp_id:
                                scene_item._connected_pins.discard(pin_idx)
                                scene_item.update()
                                break
            if isinstance(item, LabelItem):
                item.setVisible(False)
            elif not isinstance(item, JunctionItem):
                self._scene.removeItem(item)
        # Удалить junction на концах удалённых проводов.
        # Если junction известен _junction_split_map — срастить половины.
        # Если сохранённая пара удалена — пересканировать сцену.
        for ep in deleted_endpoints:
            for scene_item in list(self._scene.items()):
                if isinstance(scene_item, JunctionItem) and (scene_item.pos() - ep).manhattanLength() < 1:
                    rejoined = self._undo_junction_split(scene_item)
                    if rejoined is None:
                        self._record_split_halves(scene_item, scene_item.pos())
                        rejoined = self._undo_junction_split(scene_item)
                    if rejoined is None:
                        self._scene.removeItem(scene_item)
        self._selected_items.clear()
        self._edit_mode = False
        self.modified.emit()

    def _align_pins_to_grid(self, item: ComponentGraphicsItem):
        """Корректирует позицию компонента так, чтобы пины попали в узлы сетки."""
        if not item._data.pins:
            return
        p = item._data.pins[0]
        local_pt = QPointF(p.x1 - item._cx, -(p.y1 - item._cy))
        scene_pt = item.mapToScene(local_pt)
        g = self.GRID_SPACING
        snapped = QPointF(
            round(scene_pt.x() / g) * g,
            round(scene_pt.y() / g) * g,
        )
        delta = snapped - scene_pt
        if not delta.isNull():
            item.setPos(item.pos() + delta)

    # ------------------------------------------------------------------
    # Обработка мыши: ПКМ — панорамирование, ЛКМ — выделение/перетаскивание/трассировка
    # ------------------------------------------------------------------
    def _fix_segment(self, sx: float, sy: float):
        """Зафиксировать текущий сегмент.
        
        Если есть _last_segment_item — дополняем его новыми точками,
        иначе создаём новый WireItem. Так multi-segment провод остаётся
        одним объектом и перетаскивается как единое целое.
        """
        self._save_snapshot()
        segment = self._router.finish(sx, sy)
        if len(segment) >= 2:
            qpts = [QPointF(x, y) for x, y in segment]
            if self._last_segment_item is not None:
                self._last_segment_item.set_show_end_pin(False)
                self._wire_graph.remove_wire(self._last_segment_item)
                self._last_segment_item.append_points(qpts[1:])
                self._wire_graph.add_wire(self._last_segment_item)
                self._update_connections_for_wire(self._last_segment_item)
            else:
                wire = WireItem(qpts, placed=True,
                                show_start_pin=True, show_end_pin=True)
                self._scene.addItem(wire)
                self._wire_graph.add_wire(wire)
                self._update_connections_for_wire(wire)
                self._last_segment_item = wire
            self._router.start(segment[-1][0], segment[-1][1])
            self.modified.emit()
        else:
            self._router.start(sx, sy)
        self._clear_routing_preview()

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

                    # Фаза 2: сдвинуть провода
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
            if item.model_line():
                ed_model.setText(item.model_line())
            else:
                _auto_model = item.value().strip()
                if _auto_model:
                    from pathlib import Path as _Path
                    import re as _re
                    _lib_dirs = [
                        _Path(__file__).resolve().parent.parent.parent / "resources" / "LIB",
                        _Path(__file__).resolve().parent.parent.parent / "Mod",
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
                value = ed_value.text().strip()
                if refdes:
                    item.set_refdes(refdes)
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
    def _pin_grid_offset(sym_data: SymData, rotation: float = 0.0) -> QPointF:
        """Смещение, которое нужно добавить к привязанной к сетке позиции, чтобы пины
        оказались в узлах сетки (кратны 100 mil). Учитывает текущий поворот компонента."""
        if not sym_data.pins:
            return QPointF(0, 0)
        p = sym_data.pins[0]
        b = sym_data.bounding_box or (0, 0, 1000, 1000)
        cx = (b[0] + b[2]) / 2.0
        cy = (b[1] + b[3]) / 2.0
        dx = p.x1 - cx
        dy = -(p.y1 - cy)
        if rotation != 0.0:
            theta = math.radians(rotation)
            c, s = math.cos(theta), math.sin(theta)
            rx = dx * c - dy * s
            ry = dx * s + dy * c
            dx, dy = rx, ry
        g = 100.0
        return QPointF(round((-dx) % g, 6), round((-dy) % g, 6))

    # ------------------------------------------------------------------
    # Размещение компонента на холсте
    # ------------------------------------------------------------------
    def place_component(self, sym_data: SymData, x: float, y: float,
                        refdes: str = "", value: str = ""):
        self._save_snapshot()
        item = ComponentGraphicsItem(sym_data, refdes=refdes, value=value)
        self._auto_fill_model(item)
        offset = self._pin_grid_offset(sym_data, 0.0)
        item.setPos(x + offset.x(), y + offset.y())
        self._scene.addItem(item)
        self._update_comp_wire_connections(item)
        self.modified.emit()
        return item

    # ------------------------------------------------------------------
    # Размещение через фантом
    # ------------------------------------------------------------------
    def start_placement(self, sym_data: SymData, refdes: str = "", value: str = ""):
        """Войти в режим размещения: фантом следует за курсором, ЛКМ ставит компонент."""
        self._cancel_paste()
        self._cancel_placement()
        self._cancel_directive_placement()
        self._place_sym_data = sym_data
        self._place_refdes = refdes
        self._place_value = value
        ghost = ComponentGraphicsItem(sym_data, refdes=refdes, value=value)
        ghost.setOpacity(0.5)
        ghost.setZValue(100)
        self._scene.addItem(ghost)
        self._place_ghost = ghost
        self.setCursor(self._hand_cursor)
        self.mode_changed.emit("PLACE")
        # Сразу переместить фантом под курсор, не дожидаясь mouseMoveEvent
        vp_pos = self.viewport().mapFromGlobal(QCursor.pos())
        sp = self.mapToScene(vp_pos)
        g = self.GRID_SPACING
        x = round(sp.x() / g) * g
        y = round(sp.y() / g) * g
        offset = self._pin_grid_offset(sym_data, 0.0)
        ghost.setPos(x + offset.x(), y + offset.y())

    def _cancel_placement(self):
        """Выйти из режима размещения, убрать фантом."""
        if self._place_ghost is not None:
            self._scene.removeItem(self._place_ghost)
            self._place_ghost = None
        self._place_sym_data = None
        self._place_refdes = ""
        self._place_value = ""
        self.unsetCursor()
        self.mode_changed.emit("")

    def start_node_label_placement(self, text: str):
        """Войти в режим размещения метки узла."""
        self._cancel_placement()
        self._cancel_directive_placement()
        self._cancel_node_label_placement()
        self._node_label_text = text
        self._node_label_placement = True
        ghost = NetLabelItem(text, QPointF(0, 0))
        ghost.setOpacity(0.5)
        ghost.setZValue(100)
        self._scene.addItem(ghost)
        self._node_label_ghost = ghost
        self.setCursor(self._cross_cursor)
        self.mode_changed.emit("PLACE_NODE_LABEL")

    def _cancel_node_label_placement(self):
        if self._node_label_ghost is not None:
            self._scene.removeItem(self._node_label_ghost)
            self._node_label_ghost = None
        self._node_label_placement = False
        self._node_label_text = ""
        self.unsetCursor()
        self.mode_changed.emit("")

    def place_node_label(self, text: str, anchor_pos: QPointF):
        """Разместить метку узла на сцене."""
        self._save_snapshot()
        item = NetLabelItem(text, anchor_pos)
        self._scene.addItem(item)
        self._deselect_all()
        self._selected_items = [item]
        item.set_selected(True)
        self.modified.emit()

    # ------------------------------------------------------------------
    # Размещение текста
    # ------------------------------------------------------------------
    def start_text_placement(self, text: str, font_family: str = "monospace",
                             font_size: int = 80):
        """Войти в режим размещения текста."""
        self._cancel_paste()
        self._cancel_placement()
        self._cancel_directive_placement()
        self._cancel_node_label_placement()
        self._cancel_text_placement()
        self._text_content = text
        self._text_font_family = font_family
        self._text_font_size = font_size
        self._text_placement = True
        g = self.GRID_SPACING
        sr = self.sceneRect()
        cx = round(sr.center().x() / g) * g
        cy = round(sr.center().y() / g) * g
        ghost = TextItem(text, cx, cy, font_family, font_size)
        ghost.setOpacity(0.5)
        ghost.setZValue(100)
        self._scene.addItem(ghost)
        self._text_ghost = ghost
        self.setCursor(self._cross_cursor)
        self.mode_changed.emit("PLACE_TEXT")

    def _cancel_text_placement(self):
        if self._text_ghost is not None:
            self._scene.removeItem(self._text_ghost)
            self._text_ghost = None
        self._text_placement = False
        self._text_content = ""
        self._text_font_family = "monospace"
        self._text_font_size = 80
        self.unsetCursor()
        self.mode_changed.emit("")

    def place_text(self, text: str, font_family: str, font_size: int,
                   x: float, y: float):
        """Разместить текст на сцене."""
        self._save_snapshot()
        item = TextItem(text, x, y, font_family, font_size)
        self._scene.addItem(item)
        self._deselect_all()
        self._selected_items = [item]
        item.set_selected(True)
        self.modified.emit()

    # ------------------------------------------------------------------
    # Размещение директивы (фантом)
    # ------------------------------------------------------------------

    def start_directive_placement(self, text: str):
        """Войти в режим размещения директивы: фантом следует за курсором."""
        self._cancel_paste()
        self._cancel_directive_placement()
        self._cancel_placement()
        self._cancel_node_label_placement()
        self._cancel_text_placement()
        self._directive_text = text
        self._directive_placement = True
        g = self.GRID_SPACING
        sr = self.sceneRect()
        cx = round(sr.center().x() / g) * g
        cy = round(sr.center().y() / g) * g
        ghost = DirectiveItem(text, cx, cy)
        ghost.setOpacity(0.5)
        ghost.setZValue(100)
        self._scene.addItem(ghost)
        self._directive_ghost = ghost
        self.setCursor(self._cross_cursor)
        self.mode_changed.emit("PLACE_DIRECTIVE")

    def _cancel_directive_placement(self):
        if self._directive_ghost is not None:
            self._scene.removeItem(self._directive_ghost)
            self._directive_ghost = None
        self._directive_placement = False
        self._directive_text = ""
        self.unsetCursor()
        self.mode_changed.emit("")

    def place_directive(self, text: str, x: float, y: float):
        """Разместить директиву на сцене."""
        self._save_snapshot()
        item = DirectiveItem(text, x, y)
        self._scene.addItem(item)
        self._deselect_all()
        self._selected_items = [item]
        item.set_selected(True)
        self.modified.emit()

    # ─── Прямоугольник ───

    def start_rect_placement(self):
        """Войти в режим рисования прямоугольника."""
        self._cancel_paste()
        self._cancel_placement()
        self._cancel_node_label_placement()
        self._cancel_text_placement()
        self._cancel_rect_placement()
        self._cancel_circle_placement()
        self._rect_placement = True
        self._rect_p1 = None
        self.setCursor(self._pencil_cursor)
        self.mode_changed.emit("PLACE_RECT")

    def _cancel_rect_placement(self):
        if self._rect_ghost is not None:
            self._scene.removeItem(self._rect_ghost)
            self._rect_ghost = None
        self._rect_placement = False
        self._rect_p1 = None
        self.unsetCursor()
        self.mode_changed.emit("")

    def place_rect(self, x1: float, y1: float, x2: float, y2: float):
        """Разместить прямоугольник на сцене."""
        self._save_snapshot()
        if self._rect_ghost is not None:
            self._scene.removeItem(self._rect_ghost)
            self._rect_ghost = None
        item = RectangleItem(x1, y1, x2, y2)
        self._scene.addItem(item)
        self._deselect_all()
        self._selected_items = [item]
        item.set_selected(True)
        self._rect_placement = False
        self._rect_p1 = None
        self.unsetCursor()
        self.mode_changed.emit("")
        self.modified.emit()

    # ─── Окружность ───

    def start_circle_placement(self):
        """Войти в режим рисования окружности."""
        self._cancel_paste()
        self._cancel_placement()
        self._cancel_node_label_placement()
        self._cancel_text_placement()
        self._cancel_rect_placement()
        self._cancel_circle_placement()
        self._circle_placement = True
        self._circle_p1 = None
        self.setCursor(self._pencil_cursor)
        self.mode_changed.emit("PLACE_CIRCLE")

    def _cancel_circle_placement(self):
        if self._circle_ghost is not None:
            self._scene.removeItem(self._circle_ghost)
            self._circle_ghost = None
        self._circle_placement = False
        self._circle_p1 = None
        self.unsetCursor()
        self.mode_changed.emit("")

    def place_circle(self, x1: float, y1: float, x2: float, y2: float):
        """Разместить окружность на сцене."""
        self._save_snapshot()
        if self._circle_ghost is not None:
            self._scene.removeItem(self._circle_ghost)
            self._circle_ghost = None
        item = CircleItem(x1, y1, x2, y2)
        self._scene.addItem(item)
        self._deselect_all()
        self._selected_items = [item]
        item.set_selected(True)
        self._circle_placement = False
        self._circle_p1 = None
        self.unsetCursor()
        self.mode_changed.emit("")
        self.modified.emit()

    def _snap_to_nearest_wire_or_pin(self, scene_pos: QPointF,
                                     tolerance: float = 60.0) -> tuple[QPointF, bool]:
        """Привязать позицию к ближайшему проводу или пину.
        Возвращает (привязанная_позиция, is_on_wire_or_pin)."""
        g = self.GRID_SPACING
        # Проверить провода
        best: QPointF | None = None
        best_dist = tolerance
        for item in self._scene.items():
            if not isinstance(item, WireItem):
                continue
            pts = item.points()
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                if abs(a.x() - b.x()) < 0.1:  # Вертикальный сегмент
                    if abs(scene_pos.x() - a.x()) <= best_dist:
                        y = scene_pos.y()
                        if min(a.y(), b.y()) - tolerance <= y <= max(a.y(), b.y()) + tolerance:
                            d = abs(scene_pos.x() - a.x())
                            if d < best_dist:
                                best_dist = d
                                best = QPointF(a.x(), y)
                else:  # Горизонтальный сегмент
                    if abs(scene_pos.y() - a.y()) <= best_dist:
                        x = scene_pos.x()
                        if min(a.x(), b.x()) - tolerance <= x <= max(a.x(), b.x()) + tolerance:
                            d = abs(scene_pos.y() - a.y())
                            if d < best_dist:
                                best_dist = d
                                best = QPointF(x, a.y())
        if best is not None:
            return best, True

        # Проверить пины компонентов
        pin_hit = self._find_pin_at(scene_pos, tolerance)
        if pin_hit is not None:
            _, pin_pos = pin_hit
            return pin_pos, True

        # Сетка по умолчанию
        x = round(scene_pos.x() / g) * g
        y = round(scene_pos.y() / g) * g
        return QPointF(x, y), False

    # ------------------------------------------------------------------
    # Директивы
    # ------------------------------------------------------------------

    def add_directive(self, text: str, x: float, y: float):
        """Добавить директиву в указанную позицию сцены."""
        self._save_snapshot()
        item = DirectiveItem(text, x, y)
        self._scene.addItem(item)
        self._deselect_all()
        self._selected_items = [item]
        item.set_selected(True)
        self.modified.emit()

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------
    def get_snapped(self, scene_pos: QPointF):
        x = round(scene_pos.x() / self.GRID_SPACING) * self.GRID_SPACING
        y = round(scene_pos.y() / self.GRID_SPACING) * self.GRID_SPACING
        return QPointF(x, y)

    # ------------------------------------------------------------------
    # Трассировка проводов
    # ------------------------------------------------------------------

    def _find_pin_at(self, scene_pos: QPointF,
                     tolerance: float = 60.0) -> tuple[ComponentGraphicsItem, QPointF] | None:
        """Ищет пин компонента рядом с scene_pos. Возвращает (компонент, позиция пина на сцене)."""
        rect = QRectF(scene_pos.x() - tolerance, scene_pos.y() - tolerance,
                       tolerance * 2, tolerance * 2)
        for item in self._scene.items(rect,
                                       Qt.ItemSelectionMode.IntersectsItemShape,
                                       Qt.SortOrder.DescendingOrder):
            if isinstance(item, ComponentGraphicsItem):
                pin_pos = item.hit_test_pin(scene_pos, tolerance)
                if pin_pos is not None:
                    return item, pin_pos
        return None

    def _find_wire_endpoint_at(self, scene_pos: QPointF,
                                tolerance: float = 30.0) -> tuple[WireItem, int] | None:
        """Ищет конец провода рядом с scene_pos. Возвращает (WireItem, индекс конца)."""
        for w in self._scene.items():
            if not isinstance(w, WireItem):
                continue
            pts = w.points()
            for idx in (0, -1):
                if (scene_pos - pts[idx]).manhattanLength() <= tolerance:
                    return w, idx
        return None

    def _update_comp_wire_connections(self, component: ComponentGraphicsItem):
        """Проверить пины компонента на совпадение с концами проводов."""
        connected: set[int] = set()
        for i, p in enumerate(component._data.pins):
            pin_scene = component.mapToScene(component._p(p.x1, p.y1))
            hit = self._find_wire_endpoint_at(pin_scene, 30.0)
            if hit is not None:
                w, w_idx = hit
                key = (id(component), i)
                pts = w.points()
                target = 0 if w_idx == 0 else len(pts) - 1
                self._comp_wire_links[key] = (w, w_idx, pts[target].x(), pts[target].y())
                w.set_show_pin_at(pin_scene, False)
                connected.add(i)
        component._connected_pins = connected
        component.update()
        component.set_selected(component._selected)

    def _update_live_pins(self, component: ComponentGraphicsItem):
        """Обновить _connected_pins в реальном времени при перетаскивании
        (без изменения _comp_wire_links и видимости пинов на проводе)."""
        connected: set[int] = set()
        for i, p in enumerate(component._data.pins):
            pin_scene = component.mapToScene(component._p(p.x1, p.y1))
            hit = self._find_wire_endpoint_at(pin_scene, 30.0)
            if hit is not None:
                connected.add(i)
        component._connected_pins = connected
        component.update()

    def _refresh_wire_endpoint_pins(self, scene_pt: QPointF):
        """Показать/скрыть пин провода в точке scene_pt в зависимости от наличия пина компонента или junction."""
        has_comp = False
        for item in self._scene.items():
            if isinstance(item, ComponentGraphicsItem) and item.hit_test_pin_index(scene_pt, 30.0) is not None:
                has_comp = True
                break
        has_junction = self._junction_at(scene_pt)
        for w in self._scene.items():
            if isinstance(w, WireItem):
                w.set_show_pin_at(scene_pt, not has_comp and not has_junction)

    @staticmethod
    def _point_on_manhattan_segment(pt: QPointF, a: QPointF, b: QPointF,
                                    tolerance: float = 5.0) -> bool:
        """Лежит ли pt на ортогональном отрезке [a, b] (H или V)."""
        if abs(a.x() - b.x()) < 0.1:
            if abs(pt.x() - a.x()) > tolerance:
                return False
            return (min(a.y(), b.y()) - tolerance <= pt.y() <=
                    max(a.y(), b.y()) + tolerance)
        if abs(a.y() - b.y()) < 0.1:
            if abs(pt.y() - a.y()) > tolerance:
                return False
            return (min(a.x(), b.x()) - tolerance <= pt.x() <=
                    max(a.x(), b.x()) + tolerance)
        return False

    def _find_wire_body_at(self, scene_pos: QPointF,
                           tolerance: float = 5.0) -> WireItem | None:
        """Найти провод, на теле которого (не на конце) лежит scene_pos."""
        for item in self._scene.items():
            if not isinstance(item, WireItem):
                continue
            pts = item.points()
            if len(pts) < 2:
                continue
            if ((scene_pos - pts[0]).manhattanLength() <= tolerance or
                    (scene_pos - pts[-1]).manhattanLength() <= tolerance):
                continue
            for i in range(len(pts) - 1):
                if self._point_on_manhattan_segment(scene_pos, pts[i], pts[i+1], tolerance):
                    return item
        return None

    def _split_wire_at(self, snapped: QPointF) -> list[tuple[WireItem, WireItem]]:
        """Рассечь ВСЕ провода, проходящие через snapped (не на конце).
        Возвращает список пар (half_a, half_b)."""
        split_pairs: list[tuple[WireItem, WireItem]] = []
        for item in list(self._scene.items()):
            if not isinstance(item, WireItem):
                continue
            pts = item.points()
            # Попытка split в середине сегмента
            mid_split = False
            for i in range(len(pts) - 1):
                if self._point_on_manhattan_segment(snapped, pts[i], pts[i+1], 5.0):
                    if ((snapped - pts[i]).manhattanLength() > 1 and
                            (snapped - pts[i+1]).manhattanLength() > 1):
                        for key in list(self._comp_wire_links):
                            if self._comp_wire_links[key][0] is item:
                                self._comp_wire_links.pop(key)
                        self._wire_graph.remove_wire(item)
                        half_a = WireItem(pts[:i+1] + [snapped], placed=True,
                                          show_start_pin=item._show_start_pin,
                                          show_end_pin=False)
                        half_b = WireItem([snapped] + pts[i+1:], placed=True,
                                          show_start_pin=False,
                                          show_end_pin=item._show_end_pin)
                        self._scene.addItem(half_a)
                        self._scene.addItem(half_b)
                        self._wire_graph.add_wire(half_a)
                        self._wire_graph.add_wire(half_b)
                        self._update_connections_for_wire(half_a)
                        self._update_connections_for_wire(half_b)
                        self._scene.removeItem(item)
                        split_pairs.append((half_a, half_b))
                        mid_split = True
                        break
            if mid_split:
                continue
            # Split в существующей вершине (точка стыковки двух сегментов)
            for i in range(1, len(pts) - 1):
                if (snapped - pts[i]).manhattanLength() <= 1:
                    for key in list(self._comp_wire_links):
                        if self._comp_wire_links[key][0] is item:
                            self._comp_wire_links.pop(key)
                    self._wire_graph.remove_wire(item)
                    half_a = WireItem(pts[:i+1], placed=True,
                                      show_start_pin=item._show_start_pin,
                                      show_end_pin=False)
                    half_b = WireItem(pts[i:], placed=True,
                                      show_start_pin=False,
                                      show_end_pin=item._show_end_pin)
                    self._scene.addItem(half_a)
                    self._scene.addItem(half_b)
                    self._wire_graph.add_wire(half_a)
                    self._wire_graph.add_wire(half_b)
                    self._update_connections_for_wire(half_a)
                    self._update_connections_for_wire(half_b)
                    self._scene.removeItem(item)
                    split_pairs.append((half_a, half_b))
                    break
        return split_pairs

    def _record_split_halves(self, junction: JunctionItem, jpos: QPointF):
        """Найти две половины рассечённого провода вокруг junction и сохранить."""
        half_a = half_b = None
        for item in self._scene.items():
            if not isinstance(item, WireItem):
                continue
            if ((hasattr(self, '_drag_wires') and item in self._drag_wires) or
                    (hasattr(self, '_drag_group_wires') and item in self._drag_group_wires)):
                continue
            pts = item.points()
            if (pts[-1] - jpos).manhattanLength() <= 1:
                half_a = item
            if (pts[0] - jpos).manhattanLength() <= 1:
                half_b = item
        if half_a is not None and half_b is not None:
            self._junction_split_map[junction] = (half_a, half_b)

    def _undo_junction_split(self, junction: JunctionItem) -> WireItem | None:
        """Отменить split провода: срастить две половины и удалить junction.
        Возвращает сращенный провод или None."""
        halves = self._junction_split_map.pop(junction, None)
        if halves is None:
            return None
        half_a, half_b = halves
        if half_a not in self._scene.items() or half_b not in self._scene.items():
            return None
        jpos = junction.pos()
        self._wire_graph.remove_wire(half_a)
        self._wire_graph.remove_wire(half_b)
        pts = half_a.points()[:-1] + half_b.points()
        rejoined = WireItem(pts, placed=True)
        self._scene.addItem(rejoined)
        self._wire_graph.add_wire(rejoined)
        self._update_connections_for_wire(rejoined)
        self._scene.removeItem(half_a)
        self._scene.removeItem(half_b)
        self._scene.removeItem(junction)
        return rejoined

    def _hide_junction(self, jitem: JunctionItem):
        """Скрыть junction, не разрушая половины и запись в _junction_split_map."""
        if jitem not in self._junction_split_map:
            return
        if jitem.scene() is None:
            return
        jitem.setVisible(False)

    def _place_junction(self, pos: QPointF):
        """Создать junction в точке pos: рассечение провода, завершение маршрута, визуальный элемент."""
        self._save_snapshot()
        g = self.GRID_SPACING
        snapped = QPointF(round(pos.x() / g) * g, round(pos.y() / g) * g)
        split_pairs = self._split_wire_at(snapped)
        vertices = self._router.complete(snapped.x(), snapped.y())
        if len(vertices) >= 2:
            qpts = [QPointF(x, y) for x, y in vertices]
            wire = WireItem(qpts, placed=True,
                            show_start_pin=True, show_end_pin=False)
            self._scene.addItem(wire)
            self._wire_graph.add_wire(wire)
            self._update_connections_for_wire(wire)
        self._clear_routing_preview()
        junction = JunctionItem(snapped)
        self._scene.addItem(junction)
        if split_pairs:
            self._junction_split_map[junction] = split_pairs[0]
        self.modified.emit()

    def _update_connections_for_wire(self, wire: WireItem):
        """После размещения провода соединить его концы с пинами компонентов."""
        for pt in (wire.points()[0], wire.points()[-1]):
            for item in self._scene.items():
                if not isinstance(item, ComponentGraphicsItem):
                    continue
                if item.hit_test_pin_index(pt, 30.0) is not None:
                    self._update_comp_wire_connections(item)

    def _break_comp_wire_connections(self, component: ComponentGraphicsItem):
        """Разорвать все соединения компонента с проводами."""
        for key in list(self._comp_wire_links):
            comp_id, pin_idx = key
            if comp_id == id(component):
                w, w_idx, px, py = self._comp_wire_links.pop(key)
                w.set_show_pin_at(QPointF(px, py), True)

    def _junction_at(self, pos: QPointF, tolerance: float = 5.0) -> bool:
        """Проверить, есть ли уже JunctionItem в точке pos."""
        for item in self._scene.items():
            if isinstance(item, JunctionItem):
                if (item.pos() - pos).manhattanLength() <= tolerance:
                    return True
        return False

    def _shared_endpoint_at(self, scene_pos: QPointF) -> bool:
        """Проверить, что в точке scene_pos в WireGraph ≥2 проводов (уже есть узел)."""
        k = (round(scene_pos.x(), 1), round(scene_pos.y(), 1))
        return len(self._wire_graph._graph.get(k, set())) >= 2

    def _clear_wire_hover(self):
        self._wire_hover_pos = None

    def _clear_routing_preview(self):
        if self._routing_preview is not None:
            self._scene.removeItem(self._routing_preview)
            self._routing_preview = None
        self._clear_wire_hover()

    # ------------------------------------------------------------------
    # Crosshair (перекрестие) для режима проводов
    # ------------------------------------------------------------------

    def _show_crosshair(self, pos: QPointF):
        """Создаёт или обновляет перекрестие от края до края экрана."""
        # Получить видимую область сцены
        vp_rect = self.viewport().rect()
        top_left = self.mapToScene(vp_rect.topLeft())
        bottom_right = self.mapToScene(vp_rect.bottomRight())

        sx, sy = pos.x(), pos.y()

        if is_light_theme():
            pen = QPen(QColor(80, 80, 80, 150), 0.0)
        else:
            pen = QPen(QColor(100, 180, 255, 120), 0.0)
        pen.setStyle(Qt.PenStyle.DashLine)

        if self._crosshair_v is None:
            self._crosshair_v = QGraphicsLineItem(sx, top_left.y(), sx, bottom_right.y())
            self._crosshair_v.setPen(pen)
            self._crosshair_v.setZValue(1000)
            self._scene.addItem(self._crosshair_v)
        else:
            self._crosshair_v.setLine(sx, top_left.y(), sx, bottom_right.y())
            self._crosshair_v.setPen(pen)

        if self._crosshair_h is None:
            self._crosshair_h = QGraphicsLineItem(top_left.x(), sy, bottom_right.x(), sy)
            self._crosshair_h.setPen(pen)
            self._crosshair_h.setZValue(1000)
            self._scene.addItem(self._crosshair_h)
        else:
            self._crosshair_h.setLine(top_left.x(), sy, bottom_right.x(), sy)
            self._crosshair_h.setPen(pen)

        self._crosshair_pos = pos

    def _hide_crosshair(self):
        """Удаляет перекрестие со сцены."""
        if self._crosshair_v is not None:
            self._scene.removeItem(self._crosshair_v)
            self._crosshair_v = None
        if self._crosshair_h is not None:
            self._scene.removeItem(self._crosshair_h)
            self._crosshair_h = None
        self._crosshair_pos = None

    # ------------------------------------------------------------------
    # .sch file save / load
    # ------------------------------------------------------------------

    @staticmethod
    def _auto_fill_model(comp: 'ComponentGraphicsItem'):
        """Заполнить model_line из .lib/.mod файлов по value компонента."""
        if comp.model_line():
            return
        _value = comp.value().strip()
        if not _value:
            return
        import re as _re
        from pathlib import Path as _Path
        _lib_dirs = [
            _Path(__file__).resolve().parent.parent.parent / "resources" / "LIB",
            _Path(__file__).resolve().parent.parent.parent / "Mod",
        ]
        for _ld in _lib_dirs:
            if not _ld.exists():
                continue
            for _lf in _ld.rglob("*.lib") if _ld.is_dir() else [_ld]:
                try:
                    _c = _lf.read_text(encoding="utf-8")
                except Exception:
                    continue
                _flat = _re.sub(r'\n\s*\+\s*', ' ', _c)
                for _m in _re.finditer(
                    r'^\s*\.model\s+(\S+)\s+(\S+)\s*(.*)',
                    _flat, _re.MULTILINE | _re.IGNORECASE
                ):
                    if _m.group(1).upper() == _value.upper():
                        comp.set_model_line(f".model {_value} {_m.group(2)} {_m.group(3)}".replace('\n',' ').replace('\r',''))
                        return
                for _m in _re.finditer(
                    r'^\s*\.subckt\s+(\S+)\s*(.*)',
                    _flat, _re.MULTILINE | _re.IGNORECASE
                ):
                    if _m.group(1).upper() == _value.upper():
                        _start = _m.start()
                        _ends = _re.search(r'^\s*\.ends\s+', _c[_start:], _re.MULTILINE | _re.IGNORECASE)
                        comp.set_model_line(_c[_start:_start + _ends.end() - 1] if _ends else f".SUBCKT {_value} {_m.group(2)}")
                        return

    def save_sch(self, filepath: str):
        """Сохранить схему в .sch (JSON)."""
        from EDA.app.serializer import save_sch as _save
        _save(self, filepath)

    def load_sch(self, filepath: str):
        """Загрузить схему из .sch (JSON), очистив текущую."""
        from EDA.app.serializer import load_sch as _load
        _load(self, filepath)

    # ------------------------------------------------------------------
    # Net list export
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # SPICE netlist export (.cir для ngspice)
    # ------------------------------------------------------------------
    def export_cir(self) -> str:
        """Экспорт схемы в SPICE netlist (.cir)."""
        lines = ["* SPICE Netlist generated by EDA Schematic Editor"]
        lines.append(".title Untitled")

        # Шаг 1: сгруппировать провода по связности
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

        # Шаг 2: проиндексировать все компоненты на сцене
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

        # Шаг 3: назначить имена сетям
        net_names: list[str] = []
        net_counter = 0
        for group in net_groups:
            name = None
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
                        name = "0"
                    else:
                        name = label
                elif dev == "GND":
                    name = "0"
                break
            # Шаг 3b: проверить метки узлов (NetLabelItem)
            if name is None:
                for item in self._scene.items():
                    if not isinstance(item, NetLabelItem):
                        continue
                    anchor = item.pos()
                    for w in group:
                        pts = w.points()
                        for i in range(len(pts) - 1):
                            a, b = pts[i], pts[i + 1]
                            if abs(a.x() - b.x()) < 0.1:  # Вертикаль
                                if abs(anchor.x() - a.x()) <= 30:
                                    if min(a.y(), b.y()) - 30 <= anchor.y() <= max(a.y(), b.y()) + 30:
                                        name = item.text()
                                        break
                            else:  # Горизонталь
                                if abs(anchor.y() - a.y()) <= 30:
                                    if min(a.x(), b.x()) - 30 <= anchor.x() <= max(a.x(), b.x()) + 30:
                                        name = item.text()
                                        break
                        if name is not None:
                            break
            if name is None:
                net_counter += 1
                name = str(net_counter)
            net_names.append(name)

        # Шаг 4: pin_index → net_name для каждого компонента
        pin_to_net: dict[int, dict[int, str]] = {}
        for (cid, pin_idx), (w, _wi, _px, _py) in self._comp_wire_links.items():
            if cid in power_ids:
                continue
            if cid not in pin_to_net:
                pin_to_net[cid] = {}
            for i, group in enumerate(net_groups):
                if w in group:
                    pin_to_net[cid][pin_idx] = net_names[i]
                    break

        # Шаг 5: сформировать SPICE-строки (компоненты)
        comp_lines: list[str] = []
        dir_lines: list[str] = []
        for cid, pin_nets in pin_to_net.items():
            comp = all_comps.get(cid)
            if comp is None:
                continue
            refdes = comp.refdes() or "U?"
            value = comp.value() or ""
            device = comp._data.attributes.get("device", "").upper()
            pins = comp._data.pins

            line = self._spice_device_line(refdes, device, value, pins, pin_nets)
            if line:
                comp_lines.append(line)
            else:
                comp_lines.append(f"* {refdes}: device={device} не поддерживается")

        # Шаг 6: MODEL / DIRECTIVE — не-электрические аннотации, вставляются без привязки к проводам
        for cid, comp in all_comps.items():
            if cid in pin_to_net or cid in power_ids:
                continue
            device = comp._data.attributes.get("device", "").upper()
            if device not in ("MODEL", "DIRECTIVE"):
                continue
            refdes = comp.refdes() or "A?"
            value = comp.value() or ""
            line = self._spice_device_line(refdes, device, value, comp._data.pins, {})
            if line:
                comp_lines.append(line)

        # Шаг 7: DirectiveItem — standalone-тексты директив
        for item in self._scene.items():
            if not isinstance(item, DirectiveItem):
                continue
            text = item.text().strip()
            if text.startswith(".") or text.upper().startswith("IC="):
                dir_lines.append(text)
            else:
                dir_lines.append(f".{text}")

        # Шаг 8: MODEL-директивы из компонентов (уникальные)
        model_lines: list[str] = []
        seen: set[str] = set()
        for item in self._scene.items():
            if not isinstance(item, ComponentGraphicsItem):
                continue
            ml = item.model_line()
            if not ml:
                self._auto_fill_model(item)
                ml = item.model_line()
            if ml and ml not in seen:
                seen.add(ml)
                model_lines.append(ml)
        model_lines.sort()

        comp_lines.sort()
        dir_lines.sort(key=lambda t: (1 if t.lstrip().lower().startswith(('.print', '.plot', '.probe')) else 0, t))
        lines.extend(comp_lines)
        lines.extend(model_lines)
        lines.extend(dir_lines)
        lines.append(".end")
        return "\n".join(lines)

    @staticmethod
    def _spice_device_line(refdes: str, device: str, value: str,
                           pins, pin_nets: dict[int, str]) -> str | None:
        """Сформировать одну строку SPICE для компонента."""

        def pinnumber_index(target: str) -> int | None:
            for i, p in enumerate(pins):
                if p.pinnumber == target:
                    return i
            return None

        def net(idx: int) -> str | None:
            return pin_nets.get(idx)

        # Пассивные 2-выводные (refdes уже содержит префикс R/C/L)
        if device in ("RESISTOR",):
            n0, n1 = net(0), net(1)
            if n0 is None or n1 is None:
                return None
            return f"{refdes} {n0} {n1} {value}" if value else f"{refdes} {n0} {n1}"

        if device in ("CAPACITOR", "POLARIZED_CAPACITOR"):
            n0, n1 = net(0), net(1)
            if n0 is None or n1 is None:
                return None
            return f"{refdes} {n0} {n1} {value}" if value else f"{refdes} {n0} {n1}"

        if device == "INDUCTOR":
            n0, n1 = net(0), net(1)
            if n0 is None or n1 is None:
                return None
            return f"{refdes} {n0} {n1} {value}" if value else f"{refdes} {n0} {n1}"

        # Диоды: pinnumber=1 = anode, pinnumber=2 = cathode
        if device in ("DIODE", "ZENER_DIODE", "LED"):
            na = net(0)
            nc = net(1)
            if na is None or nc is None:
                return None
            model = value if value else device.lower()
            return f"{refdes} {na} {nc} {model}"

        # Источники напряжения / тока
        if device in ("VOLTAGE_SOURCE", "VAC", "VDC"):
            np_pos, np_neg = net(0), net(1)
            if np_pos is None or np_neg is None:
                return None
            val = value if value else "0"
            return f"{refdes} {np_pos} {np_neg} {val}"

        if device == "VSIN":
            np_pos, np_neg = net(0), net(1)
            if np_pos is None or np_neg is None:
                return None
            val = value if value else "SIN(0 1 1k)"
            return f"{refdes} {np_pos} {np_neg} {val}"

        if device == "VPULSE":
            np_pos, np_neg = net(0), net(1)
            if np_pos is None or np_neg is None:
                return None
            val = value if value else "PULSE(0 1 0 1n 1n 50u 100u)"
            return f"{refdes} {np_pos} {np_neg} {val}"

        if device == "VPWL":
            np_pos, np_neg = net(0), net(1)
            if np_pos is None or np_neg is None:
                return None
            val = value if value else "PWL(0 0 1u 1)"
            return f"{refdes} {np_pos} {np_neg} {val}"

        if device == "VEXP":
            np_pos, np_neg = net(0), net(1)
            if np_pos is None or np_neg is None:
                return None
            val = value if value else "EXP(0 1 0 1u 0 1u)"
            return f"{refdes} {np_pos} {np_neg} {val}"

        if device == "CURRENT_SOURCE":
            np_pos, np_neg = net(0), net(1)
            if np_pos is None or np_neg is None:
                return None
            val = value if value else "0"
            return f"{refdes} {np_pos} {np_neg} {val}"

        # Биполярные транзисторы: pinnumber=C, B, E
        if device in ("NPN_TRANSISTOR", "PNP_TRANSISTOR"):
            i_c = pinnumber_index("C")
            i_b = pinnumber_index("B")
            i_e = pinnumber_index("E")
            if i_c is None or i_b is None or i_e is None:
                # fallback: index order
                n0, n1, n2 = net(0), net(1), net(2)
                if n0 is None or n1 is None or n2 is None:
                    return None
                model = value if value else device.lower()
                return f"{refdes} {n0} {n1} {n2} {model}"
            nc = net(i_c)
            nb = net(i_b)
            ne = net(i_e)
            if nc is None or nb is None or ne is None:
                return None
            model = value if value else device.lower()
            return f"{refdes} {nc} {nb} {ne} {model}"

        # Полевые транзисторы: pinnumber=D, G, S
        if device in ("NMOS_TRANSISTOR", "PMOS_TRANSISTOR"):
            i_d = pinnumber_index("D")
            i_g = pinnumber_index("G")
            i_s = pinnumber_index("S")
            if i_d is not None and i_g is not None and i_s is not None:
                nd = net(i_d)
                ng = net(i_g)
                ns = net(i_s)
                if nd is None or ng is None or ns is None:
                    return None
                model = value if value else device.lower()
                return f"{refdes} {nd} {ng} {ns} 0 {model}"
            n0, n1, n2 = net(0), net(1), net(2)
            if n0 is None or n1 is None or n2 is None:
                return None
            model = value if value else device.lower()
            return f"{refdes} {n0} {n1} {n2} 0 {model}"

        # Директивы и модели SPICE
        if device == "DIRECTIVE":
            return value if value else None

        if device == "MODEL":
            return value if value else None

        # Операционные усилители AOP-Standard: pinnumber → SPICE порядок
        if device == "AOP-STANDARD":
            pin_order = ["1", "2", "3", "4", "5"]  # N+ N- V+ V- OUT
            nets = []
            for pn in pin_order:
                idx = pinnumber_index(pn)
                if idx is not None:
                    n = net(idx)
                    if n is None:
                        return None
                    nets.append(n)
                else:
                    return None
            model = value if value else "LM741"
            return f"X{refdes} {' '.join(nets)} {model}"

        # Сложные IC → X (subcircuit call)
        if device in ("OPAMP", "DUAL_OPAMP", "QUAD_OPAMP", "LM555",
                      "LM741", "LM358", "LM324", "LM311", "LM393",
                      "LM317", "LM337", "LM339", "LM319", "LM2902",
                      "LT1782", "LTC1799", "LTC2400", "LT1761SD",
                      "LT1374CS8", "LT1376", "LP2954IT", "LM2941T",
                      "LM2576T", "LM2822M", "L200"):
            net_list = [pin_nets.get(i, "NC") for i in range(len(pins))]
            if not any(n != "NC" for n in net_list):
                return None
            return f"X{refdes} {' '.join(net_list)} {device.lower()}"

        return None

    # ------------------------------------------------------------------
    # Pin drag (удлинение/укорачивание сегмента за пин)
    # ------------------------------------------------------------------

    def _find_wire_pin_at(self, wires: list[WireItem], scene_pos: QPointF,
                          tolerance: float = 30.0) -> tuple[WireItem, int] | None:
        """Ищет пин провода рядом с scene_pos. Возвращает (WireItem, индекс конца)."""
        closest: tuple[WireItem, int] | None = None
        closest_dist = tolerance
        for w in wires:
            pts = w.points()
            for idx in (0, -1):
                d = (scene_pos - pts[idx]).manhattanLength()
                if d < closest_dist:
                    closest_dist = d
                    closest = (w, idx)
        return closest

    def _start_pin_drag(self, wire: WireItem, idx: int):
        """Захватить пин для перетаскивания. Находит все провода на этой точке."""
        pts = wire.points()
        ep = pts[idx]
        connected = self._wire_graph.get_connected(wire)
        self._pin_drag_ends = []
        self._pin_drag_orig_points.clear()
        seen: set[WireItem] = set()
        for w in connected:
            if w in seen:
                continue
            seen.add(w)
            wpts = w.points()
            if (wpts[0] - ep).manhattanLength() < 1:
                self._pin_drag_ends.append((w, 0))
            elif (wpts[-1] - ep).manhattanLength() < 1:
                self._pin_drag_ends.append((w, -1))
            self._pin_drag_orig_points[w] = list(w.points())
        self._pin_drag_origin = ep


# ======================================================================

