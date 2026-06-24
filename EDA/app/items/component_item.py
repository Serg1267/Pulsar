from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import (QPainter, QColor, QPen, QBrush, QPolygonF, QPainterPath, QPainterPathStroker)

from EDA.core.parser.sym_parser import SymData
from EDA.app.items.colors import YELLOW, GREEN, WHITE, RED, _BODY_LINE_WIDTH, _LEAD_LINE_WIDTH, lead_color, body_color, is_light_theme
from EDA.app.items.label_item import LabelItem


class ComponentGraphicsItem(QGraphicsItem):
    """Рисует символ компонента с поддержкой выделения и перетаскивания."""

    def __init__(self, sym_data: SymData, refdes: str = "", value: str = ""):
        super().__init__()
        self._data = sym_data
        b = sym_data.bounding_box or (0, 0, 1000, 1000)
        self._cx = (b[0] + b[2]) / 2.0
        self._cy = (b[1] + b[3]) / 2.0

        self._selected = False
        self._drag_active = False
        self._connected_pins: set[int] = set()

        self._refdes = refdes
        self._value = value
        self._model_line: str = ""
        self._footprint: str = ""
        self._label_items: list[LabelItem] = []
        self._create_labels()
        self._sync_value_label()

    # ---- состояние ----

    def set_selected(self, val: bool):
        self._selected = val
        if not val:
            self.setZValue(0)
        self.update()

    def set_drag_active(self, val: bool):
        self._drag_active = val
        self.update()

    def rotate(self, angle_delta: float):
        """Поворачивает компонент относительно центра."""
        self.setRotation(self.rotation() + angle_delta)

    # ---- надписи ----

    def refdes(self) -> str:
        return self._refdes

    def value(self) -> str:
        return self._value

    def model_line(self) -> str:
        return self._model_line

    def set_refdes(self, text: str):
        self._refdes = text
        self._update_labels()

    def set_value(self, text: str):
        self._value = text
        self._sync_value_label()

    def set_model_line(self, text: str):
        self._model_line = text

    def footprint(self) -> str:
        return self._footprint

    def set_footprint(self, name: str):
        self._footprint = name

    def _sync_value_label(self):
        """Создаёт или обновляет метку value, если её нет в .sym."""
        for lbl in self._label_items:
            if lbl.label_type() == "value":
                lbl.set_text(self._value)
                lbl.setVisible(True)
                return
        b = self._data.bounding_box or (0, 0, 1000, 1000)
        top = -(b[3] - self._cy)
        y_pos = top - 280 if any(l.label_type() == "refdes" for l in self._label_items) else top - 100
        lbl = LabelItem(self._value, 0, y_pos, self, label_type="value")
        self._label_items.append(lbl)

    def _create_labels(self):
        b = self._data.bounding_box or (0, 0, 1000, 1000)
        top = -(b[3] - self._cy)
        for t in self._data.texts:
            if not t.visible:
                continue
            if t.content.startswith("footprint="):
                continue
            if t.content.startswith("refdes="):
                text = self._refdes or t.content.split("=", 1)[1].strip()
                lbl = LabelItem(text, 0, top - 100, self, label_type="refdes")
                self._label_items.append(lbl)
            elif t.content.startswith("value="):
                text = self._value or t.content.split("=", 1)[1].strip()
                y_pos = top - 100 if not self._label_items else top - 280
                lbl = LabelItem(text, 0, y_pos, self, label_type="value")
                self._label_items.append(lbl)
            else:
                # Остальные видимые тексты — LabelItem в позиции из .sym
                pt = self._p(t.x, t.y)
                lbl = LabelItem(t.content, pt.x(), pt.y(), self,
                                label_type=f"static_{t.content}")
                self._label_items.append(lbl)

    def _update_labels(self):
        for t in self._data.texts:
            if not t.visible:
                continue
            if t.content.startswith("refdes="):
                text = self._refdes or t.content.split("=", 1)[1].strip()
                for lbl in self._label_items:
                    if lbl.label_type() == "refdes":
                        lbl.set_text(text)
                        break
            elif t.content.startswith("value="):
                text = self._value or t.content.split("=", 1)[1].strip()
                for lbl in self._label_items:
                    if lbl.label_type() == "value":
                        lbl.set_text(text)
                        break

    def _remove_label(self, lbl: LabelItem):
        if lbl in self._label_items:
            self._label_items.remove(lbl)

    def boundingRect(self) -> QRectF:
        b = self._data.bounding_box or (0, 0, 1000, 1000)
        return QRectF(
            b[0] - self._cx, -(b[3] - self._cy),
            b[2] - b[0], b[3] - b[1]
        )

    def shape(self) -> QPainterPath:
        stroked = QPainterPath()
        for l in self._data.lines:
            stroked.moveTo(self._p(l.x1, l.y1))
            stroked.lineTo(self._p(l.x2, l.y2))
        for a in self._data.arcs:
            center = self._p(a.x, a.y)
            r = a.radius
            rect = QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r)
            stroked.arcMoveTo(rect, a.start_angle)
            stroked.arcTo(rect, a.start_angle, a.sweep_angle)

        path = QPainterPath()
        for b in self._data.boxes:
            p1 = self._p(b.x, b.y)
            p2 = self._p(b.x + b.width, b.y + b.height)
            path.addRect(QRectF(p1, p2).normalized())
        for c in self._data.circles:
            center = self._p(c.x, c.y)
            path.addEllipse(center, c.radius, c.radius)
        for poly in self._data.polygons:
            pts = [self._p(x, y) for x, y in poly]
            if pts:
                p = QPainterPath()
                p.moveTo(pts[0])
                for pt in pts[1:]:
                    p.lineTo(pt)
                p.closeSubpath()
                path.addPath(p)

        if not stroked.isEmpty():
            stroker = QPainterPathStroker()
            stroker.setWidth(40.0)
            path.addPath(stroker.createStroke(stroked))
        return path

    def _p(self, sx: float, sy: float) -> QPointF:
        return QPointF(sx - self._cx, -(sy - self._cy))

    def hit_test_pin(self, scene_pos: QPointF, tolerance: float = 60.0) -> QPointF | None:
        """Проверяет, находится ли scene_pos рядом с каким-либо пином компонента.
        Возвращает позицию пина на сцене или None."""
        for p in self._data.pins:
            pin_scene = self.mapToScene(self._p(p.x1, p.y1))
            if (scene_pos - pin_scene).manhattanLength() <= tolerance:
                return pin_scene
        return None

    def hit_test_pin_index(self, scene_pos: QPointF, tolerance: float = 30.0) -> int | None:
        """Возвращает индекс пина, рядом с которым находится scene_pos."""
        for i, p in enumerate(self._data.pins):
            pin_scene = self.mapToScene(self._p(p.x1, p.y1))
            if (scene_pos - pin_scene).manhattanLength() <= tolerance:
                return i
        return None

    def _make_cosmetic_pen(self, color: str, width: float) -> QPen:
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)
        return pen

    def paint(self, painter: QPainter, option, widget=None):
        if self._selected:
            pen = self._make_cosmetic_pen(YELLOW, _BODY_LINE_WIDTH)
            painter.setPen(pen)
            for l in self._data.lines:
                painter.drawLine(self._p(l.x1, l.y1), self._p(l.x2, l.y2))
            for b in self._data.boxes:
                p1 = self._p(b.x, b.y)
                p2 = self._p(b.x + b.width, b.y + b.height)
                painter.drawRect(QRectF(p1, p2).normalized())
            for c in self._data.circles:
                center = self._p(c.x, c.y)
                painter.drawEllipse(center, c.radius, c.radius)
            for poly in self._data.polygons:
                pts = [self._p(x, y) for x, y in poly]
                painter.drawPolygon(QPolygonF(pts))
            for a in self._data.arcs:
                center = self._p(a.x, a.y)
                r = a.radius
                rect = QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r)
                painter.drawArc(rect,
                                int(round(a.start_angle * 16)),
                                int(round(a.sweep_angle * 16)))
            pen_lead = self._make_cosmetic_pen(YELLOW, _LEAD_LINE_WIDTH)
            painter.setPen(pen_lead)
            for p in self._data.pins:
                painter.drawLine(self._p(p.x1, p.y1), self._p(p.x2, p.y2))
            painter.setBrush(QBrush(QColor(YELLOW)))
            painter.setPen(pen)
            for i, p in enumerate(self._data.pins):
                if i in self._connected_pins:
                    continue
                pt = self._p(p.x1, p.y1)
                painter.drawRect(QRectF(pt.x() - 20, pt.y() - 20, 40, 40))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            return

        bcol = body_color()
        lcol = lead_color()

        pen_body = self._make_cosmetic_pen(bcol, _BODY_LINE_WIDTH)
        painter.setPen(pen_body)
        for l in self._data.lines:
            painter.drawLine(self._p(l.x1, l.y1), self._p(l.x2, l.y2))
        for b in self._data.boxes:
            p1 = self._p(b.x, b.y)
            p2 = self._p(b.x + b.width, b.y + b.height)
            painter.drawRect(QRectF(p1, p2).normalized())
        for c in self._data.circles:
            center = self._p(c.x, c.y)
            painter.drawEllipse(center, c.radius, c.radius)
        for poly in self._data.polygons:
            pts = [self._p(x, y) for x, y in poly]
            painter.drawPolygon(QPolygonF(pts))
        for a in self._data.arcs:
            center = self._p(a.x, a.y)
            r = a.radius
            rect = QRectF(center.x() - r, center.y() - r, 2 * r, 2 * r)
            painter.drawArc(rect,
                            int(round(a.start_angle * 16)),
                            int(round(a.sweep_angle * 16)))
        pen_lead = self._make_cosmetic_pen(lcol, _LEAD_LINE_WIDTH)
        painter.setPen(pen_lead)
        for p in self._data.pins:
            painter.drawLine(self._p(p.x1, p.y1), self._p(p.x2, p.y2))

        pen_pin = self._make_cosmetic_pen(RED, _BODY_LINE_WIDTH)
        painter.setPen(pen_pin)
        painter.setBrush(QBrush(QColor(RED)))
        for i, p in enumerate(self._data.pins):
            if i in self._connected_pins:
                continue
            pt = self._p(p.x1, p.y1)
            painter.drawRect(QRectF(pt.x() - 20, pt.y() - 20, 40, 40))
        painter.setBrush(Qt.BrushStyle.NoBrush)
