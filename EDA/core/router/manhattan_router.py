from __future__ import annotations


class ManhattanRouter:
    """Минимальный помощник для рисования одиночных сегментов проводов.

    Хранит только начальную точку текущего сегмента.
    `preview(x, y)` — возвращает [start, locked_end] (блокировка оси).
    `finish(x, y)` — завершает сегмент, возвращает [start, locked_end], сбрасывает.
    `finish_at(ex, ey)` — завершает сегмент точной точкой (пин).
    `commit(x, y)` — добавить промежуточную вершину (начать новый сегмент).
    `complete(x, y)` — завершить маршрут точной точкой, вернуть все вершины.
    """

    def __init__(self, grid_spacing: float = 100.0):
        self.grid = grid_spacing
        self.reset()

    def snap(self, val: float) -> float:
        return round(val / self.grid) * self.grid

    def reset(self):
        self._start: tuple[float, float] | None = None
        self._vertices: list[tuple[float, float]] = []

    @property
    def is_active(self) -> bool:
        return self._start is not None

    @property
    def vertices(self) -> list[tuple[float, float]]:
        return list(self._vertices)

    def start(self, x: float, y: float):
        """Начать маршрут от точки (x, y)."""
        self._start = (x, y)
        self._vertices = [(x, y)]

    def preview(self, x: float, y: float) -> list[tuple[float, float]]:
        """[start, (corner), end] для предпросмотра текущего сегмента."""
        if self._start is None:
            return []
        sx, sy = self._start
        ex, ey = self.snap(x), self.snap(y)
        if ex == sx:
            return [(sx, sy), (sx, ey)]     # I: вертикаль
        if ey == sy:
            return [(sx, sy), (ex, sy)]     # I: горизонталь
        return [(sx, sy), (ex, sy), (ex, ey)]  # L: горизонталь → вертикаль

    def finish(self, x: float, y: float) -> list[tuple[float, float]]:
        """Завершить текущий сегмент snapped точкой. Вернуть [start, (corner), end]."""
        if self._start is None:
            return []
        sx, sy = self._start
        ex, ey = self.snap(x), self.snap(y)
        self.reset()
        if ex == sx and ey == sy:
            return []
        if ex == sx:
            return [(sx, sy), (sx, ey)]     # I: вертикаль
        if ey == sy:
            return [(sx, sy), (ex, sy)]     # I: горизонталь
        return [(sx, sy), (ex, sy), (ex, ey)]  # L: горизонталь → вертикаль

    def finish_at(self, end_x: float, end_y: float) -> list[tuple[float, float]]:
        """Завершить текущий сегмент точной точкой (пин). Вернуть [start, locked_end]."""
        if self._start is None:
            return []
        sx, sy = self._start
        ex, ey = end_x, end_y
        self.reset()
        if abs(ex - sx) < 0.1 and abs(ey - sy) < 0.1:
            return []
        if abs(ex - sx) >= abs(ey - sy):
            return [(sx, sy), (ex, sy)]
        else:
            return [(sx, sy), (sx, ey)]

    def commit(self, x: float, y: float):
        """Зафиксировать промежуточную вершину, начать новый сегмент от неё."""
        if self._start is None:
            return
        sx, sy = self._start
        ex, ey = self.snap(x), self.snap(y)
        if ex == sx and ey == sy:
            return
        # Добавить вершину(ы) текущего сегмента
        if ex == sx:
            self._vertices.append((sx, ey))    # I: вертикаль
            self._start = (sx, ey)
        elif ey == sy:
            self._vertices.append((ex, sy))    # I: горизонталь
            self._start = (ex, sy)
        else:
            self._vertices.append((ex, sy))    # L: угол
            self._vertices.append((ex, ey))    # L: конец
            self._start = (ex, ey)

    def complete(self, x: float, y: float) -> list[tuple[float, float]]:
        """Завершить маршрут точной точкой. Вернуть все вершины, сбросить."""
        if self._start is None:
            return []
        sx, sy = self._start
        ex, ey = x, y
        if abs(ex - sx) < 0.1 and abs(ey - sy) < 0.1:
            return []
        if ex == sx:
            # I: вертикаль
            self._vertices.append((sx, ey))
        elif ey == sy:
            # I: горизонталь
            self._vertices.append((ex, sy))
        else:
            # L: угол + конец
            if abs(ex - sx) >= abs(ey - sy):
                self._vertices.append((ex, sy))
            else:
                self._vertices.append((sx, ey))
            self._vertices.append((ex, ey))
        result = list(self._vertices)
        self.reset()
        return result
