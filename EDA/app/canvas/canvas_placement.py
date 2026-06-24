# -*- coding: utf-8 -*-
"""PlacementMixin — размещение компонентов, меток, текста, директив, прямоугольников, окружностей."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtGui import QTransform

from EDA.core.parser.sym_parser import SymData
from EDA.app.items.component_item import ComponentGraphicsItem
from EDA.app.items.label_item import LabelItem
from EDA.app.items.directive_item import DirectiveItem
from EDA.app.items.node_label_item import NetLabelItem
from EDA.app.items.text_item import TextItem
from EDA.app.items.rectangle_item import RectangleItem
from EDA.app.items.circle_item import CircleItem


class PlacementMixin:
    """Mixin для размещения элементов на холсте."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

    # ------------------------------------------------------------------
    # Размещение метки узла
    # ------------------------------------------------------------------
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
