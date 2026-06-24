# -*- coding: utf-8 -*-
"""SelectionMixin — выделение, копирование, вставка, удаление, поворот, отражение."""

from __future__ import annotations

import json
import re

from PySide6.QtCore import Qt, QRectF, QPointF, QMimeData
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsRectItem,
                               QApplication)
from PySide6.QtGui import QTransform, QCursor

from EDA.app.items.component_item import ComponentGraphicsItem
from EDA.app.items.label_item import LabelItem
from EDA.app.items.junction_item import JunctionItem
from EDA.app.items.directive_item import DirectiveItem
from EDA.app.items.node_label_item import NetLabelItem
from EDA.app.items.text_item import TextItem
from EDA.app.items.rectangle_item import RectangleItem
from EDA.app.items.circle_item import CircleItem
from EDA.core.router.wire_item import WireItem


class SelectionMixin:
    """Mixin для операций выделения, копирования/вставки, удаления, поворота, отражения."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

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

        # --- Вставка из буфера обмена (фантом) ---
        self._paste_data: dict | None = None
        self._paste_ghosts: list = []
        self._paste_origin_x: float = 0.0
        self._paste_origin_y: float = 0.0

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
            elif isinstance(item, (TextItem, DirectiveItem, RectangleItem, CircleItem)):
                item.setRotation(item.rotation() + angle_delta)
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
            elif isinstance(item, (LabelItem, TextItem, DirectiveItem,
                                   RectangleItem, CircleItem, NetLabelItem)):
                if isinstance(item, LabelItem) and item.parentItem() in selected_parents:
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
            elif isinstance(item, (LabelItem, TextItem, DirectiveItem,
                                   RectangleItem, CircleItem, NetLabelItem)):
                if isinstance(item, LabelItem) and item.parentItem() in selected_parents:
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
            if item.scene() is not self._scene:
                continue
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

    # ------------------------------------------------------------------
    # Выравнивание пинов по сетке
    # ------------------------------------------------------------------
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
