# -*- coding: utf-8 -*-
"""WireMixin — трассировка проводов, рассечение, junction, crosshair."""

from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainterPath, QPen, QColor
from PySide6.QtWidgets import QGraphicsLineItem

from EDA.core.router import ManhattanRouter, WireItem, WireGraph
from EDA.app.items.component_item import ComponentGraphicsItem
from EDA.app.items.junction_item import JunctionItem
from EDA.app.items.colors import is_light_theme


class WireMixin:
    """Mixin для трассировки проводов, управления junction'ами и crosshair."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # --- Трассировка проводов ---
        self._wire_mode = False
        self._wire_draw_mode = False
        self._router = ManhattanRouter(self.GRID_SPACING)
        self._routing_preview: QGraphicsLineItem | None = None
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
