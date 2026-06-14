from __future__ import annotations

from PySide6.QtCore import QPointF

from EDA.core.router.wire_item import WireItem


class WireGraph:
    """Граф связности проводов: точка ↔ список WireItems, соединённых в этой точке."""

    def __init__(self):
        self._graph: dict[tuple[float, float], set[WireItem]] = {}

    @staticmethod
    def _key(pt: QPointF) -> tuple[float, float]:
        return (round(pt.x(), 1), round(pt.y(), 1))

    def add_wire(self, wire: WireItem):
        """Зарегистрировать провод и скрыть пины в точках соединения."""
        pts = wire.points()
        for pt in (pts[0], pts[-1]):
            k = self._key(pt)
            if k not in self._graph:
                self._graph[k] = set()
            self._graph[k].add(wire)
            if len(self._graph[k]) > 1:
                for w in self._graph[k]:
                    w.set_show_pin_at(pt, False)

    def remove_wire(self, wire: WireItem):
        """Удалить провод из графа. Если на точке остался один провод — показать пин."""
        pts = wire.points()
        for pt in (pts[0], pts[-1]):
            k = self._key(pt)
            if k not in self._graph:
                continue
            self._graph[k].discard(wire)
            if not self._graph[k]:
                del self._graph[k]
            elif len(self._graph[k]) == 1:
                remaining = next(iter(self._graph[k]))
                remaining.set_show_pin_at(pt, True)

    def get_connected(self, wire: WireItem) -> set[WireItem]:
        """BFS: все провода, соединённые через общие концы."""
        visited: set[WireItem] = set()
        queue: list[WireItem] = [wire]
        while queue:
            w = queue.pop(0)
            if w in visited:
                continue
            visited.add(w)
            for pt in (w.points()[0], w.points()[-1]):
                for n in self._graph.get(self._key(pt), set()):
                    if n not in visited:
                        queue.append(n)
        return visited

    def clear(self):
        self._graph.clear()
