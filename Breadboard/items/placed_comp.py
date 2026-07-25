"""QGraphicsItem for a placed breadboard component — paint-only, no child items."""

from __future__ import annotations

from PySide6.QtWidgets import QGraphicsItem
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (
    QPainterPath, QPolygonF, QPen, QBrush, QColor, QTransform, QFont,
)

from Breadboard.pcb_library import Package, DrawCommand
from Breadboard.board import Board

# Colors
PAD_COLOR = QColor("#e87a20")
SILK_COLOR = QColor("#f0f0f0")
COPPER_LAYERS = ("copper0", "copper1")
SELECTED_PEN = QPen(QColor("#ffff00"))
SELECTED_PEN.setWidth(0)
SELECTED_PEN.setStyle(Qt.PenStyle.DashLine)


def _leftmost_copper(pkg: Package) -> tuple[float, float]:
    best = None
    best_x = float("inf")
    for cmd in pkg.commands:
        if cmd.type == "circle" and cmd.layer in COPPER_LAYERS:
            cx = cmd.params.get("cx", 0)
            if cx < best_x:
                best_x = cx
                best = (cx, cmd.params.get("cy", 0))
    return best or (0, 0)


def _all_copper_centers(pkg: Package) -> list[tuple[float, float]]:
    circles = []
    for cmd in pkg.commands:
        if cmd.type == "circle" and cmd.layer in COPPER_LAYERS:
            circles.append((cmd.params.get("cx", 0), cmd.params.get("cy", 0)))
    circles.sort(key=lambda c: c[0])
    return circles


class PlacedCompItem(QGraphicsItem):
    """A component placed on the breadboard; draggable with snap to holes."""

    def __init__(self, pkg: Package, footprint: str, refdes: str,
                 board: Board | None = None,
                 rotation: int = 0, flip_h: bool = False, flip_v: bool = False,
                 on_moved=None):
        super().__init__()
        self._pkg = pkg
        self._footprint = footprint
        self._refdes = refdes
        self._board = board
        self._rotation = rotation
        self._flip_h = flip_h
        self._flip_v = flip_v
        self._on_moved = on_moved

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)

        vbw = pkg.vb[2] if pkg.vb[2] else 1
        self._mmu = pkg.width_mm / vbw

        self._build_geom()
        self._build_transform()
        self._label_item = RefdesLabel(self)
        self._body_opacity = 1.0
        self._on_before_drag = None

    def setBodyOpacity(self, opacity: float):
        self._body_opacity = max(0.0, min(1.0, opacity))
        self.update()

    # ── Properties ──────────────────────────────────────────────

    def refdes(self) -> str:
        return self._refdes

    def footprint(self) -> str:
        return self._footprint

    def all_pin_centers(self) -> list[tuple[float, float]]:
        centers = _all_copper_centers(self._pkg)
        result = []
        for cx, cy in centers:
            sp = self.mapToScene(QPointF(cx, cy))
            result.append((sp.x(), sp.y()))
        return result

    # ── Transform ───────────────────────────────────────────────

    def _build_transform(self):
        ref_x, ref_y = _leftmost_copper(self._pkg)
        t = QTransform()
        t.scale(self._mmu, self._mmu)
        if self._flip_h:
            t.scale(-1, 1)
        if self._flip_v:
            t.scale(1, -1)
        t.rotate(self._rotation)
        t.translate(-ref_x, -ref_y)
        self.setTransform(t)

    def setCompRotation(self, deg: int):
        self._rotation = deg % 360
        self._build_transform()
        self.update()
        if self._on_moved:
            self._on_moved()

    def setFlipH(self, flip: bool):
        self._flip_h = flip
        self._build_transform()
        self.update()
        if self._on_moved:
            self._on_moved()

    def setFlipV(self, flip: bool):
        self._flip_v = flip
        self._build_transform()
        self.update()
        if self._on_moved:
            self._on_moved()

    # ── Geometry builder ────────────────────────────────────────

    def _build_geom(self):
        self._copper: list[tuple[QPainterPath, QPen, QBrush]] = []
        self._silk: list[tuple[QPainterPath, QPen, QBrush]] = []
        self._other: list[tuple[QPainterPath, QPen, QBrush]] = []

        mmu = self._mmu
        for cmd in self._pkg.commands:
            path = self._cmd_to_path(cmd)
            if path is None:
                continue

            is_copper = cmd.layer in COPPER_LAYERS and cmd.type == "circle"
            is_silk = cmd.layer == "silkscreen"

            if is_copper:
                pen = QPen(PAD_COLOR.darker(120), 0.1 / mmu)
                brush = QBrush(PAD_COLOR)
                self._copper.append((path, pen, brush))
            elif is_silk:
                pen = QPen(SILK_COLOR, 0.15 / mmu)
                brush = QBrush(Qt.BrushStyle.NoBrush)
                self._silk.append((path, pen, brush))
            else:
                fill_s = cmd.params.get("fill", "none")
                stroke_s = cmd.params.get("stroke", "")
                pen = QPen(QColor(stroke_s if stroke_s else SILK_COLOR), 0.15 / mmu)
                brush = QBrush(QColor(fill_s)) if fill_s and fill_s != "none" else QBrush(Qt.BrushStyle.NoBrush)
                self._other.append((path, pen, brush))

    def _cmd_to_path(self, cmd: DrawCommand) -> QPainterPath | None:
        if cmd.type == "circle":
            cx = cmd.params["cx"]
            cy = cmd.params["cy"]
            r = cmd.params["r"]
            path = QPainterPath()
            path.addEllipse(QPointF(cx, cy), r, r)
            return path

        if cmd.type == "rect":
            if cmd.layer.startswith("copper0"):
                return None
            x = cmd.params["x"]
            y = cmd.params["y"]
            w = cmd.params["w"]
            h = cmd.params["h"]
            path = QPainterPath()
            path.addRect(x, y, w, h)
            return path

        if cmd.type == "line":
            path = QPainterPath()
            path.moveTo(cmd.params["x1"], cmd.params["y1"])
            path.lineTo(cmd.params["x2"], cmd.params["y2"])
            return path

        if cmd.type == "path":
            pts = cmd.params["points"]
            if not pts:
                return None
            path = QPainterPath()
            path.moveTo(pts[0][0], pts[0][1])
            for p in pts[1:]:
                path.lineTo(p[0], p[1])
            if cmd.params.get("closed"):
                path.closeSubpath()
            return path

        return None

    # ── Geometry helpers (used by RefdesLabel) ───────────────

    def _calc_font_px(self) -> int:
        """Font size in SVG pixels (item-local coords), larger for resistors & transistors."""
        target_mm = 1.8 if ('axial' in self._footprint or 'to-92' in self._footprint) else 0.6
        return max(1, round(target_mm / self._mmu)) if self._mmu else 10

    def _label_gap_svg(self) -> float:
        """Gap below body (SVG units) before refdes text baseline."""
        return 0.4 / self._mmu if ('axial' in self._footprint or 'to-92' in self._footprint) else 2.0 / self._mmu

    @property
    def _body_bbox(self) -> QRectF:
        rect = QRectF()
        for lst in (self._copper, self._silk, self._other):
            for path, _, _ in lst:
                rect |= path.boundingRect()
        return rect

    # ── Geometry ────────────────────────────────────────────────

    def boundingRect(self):
        rect = self._body_bbox
        if self._label_item is not None:
            lb = self._label_item.boundingRect()
            lbl_pos = self._label_item.pos()
            t = self._label_item.transform()
            if t.isIdentity():
                rect |= lb.translated(lbl_pos)
            else:
                rect |= t.mapRect(lb).translated(lbl_pos)
        if rect.isEmpty():
            return QRectF(-5, -5, 10, 10)
        pad = 0.3 / self._mmu if self._mmu else 4
        return rect.adjusted(-pad, -pad, pad, pad)

    def shape(self):
        rect = self._body_bbox
        if rect.isEmpty():
            return QPainterPath()
        tol = 2 / self._mmu if self._mmu else 4
        rect = rect.adjusted(-tol, -tol, tol, tol)
        path = QPainterPath()
        path.addRect(rect)
        return path

    def paint(self, painter, option, widget=None):
        for lst, is_copper in ((self._silk, False), (self._copper, True), (self._other, False)):
            if self._body_opacity < 1.0 and not is_copper:
                painter.save()
                painter.setOpacity(self._body_opacity)
            for path, pen, brush in lst:
                if self.isSelected() and not is_copper:
                    p = QPen(QColor("#ffcc00"), pen.widthF())
                    painter.setPen(p)
                else:
                    painter.setPen(pen)
                painter.setBrush(brush)
                painter.drawPath(path)
            if self._body_opacity < 1.0 and not is_copper:
                painter.restore()

    # ── Snap ────────────────────────────────────────────────────

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self._board is not None and self.scene():
            new_pos = value
            col, row = self._board.nearest_hole(new_pos.x(), new_pos.y())
            hx, hy = self._board.hole_pos(col, row)
            return QPointF(hx, hy)
        if change in (QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged,
                      QGraphicsItem.GraphicsItemChange.ItemTransformHasChanged):
            if self._on_moved:
                self._on_moved()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)

    # ── Hover ───────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if self._on_before_drag:
            self._on_before_drag()
        super().mousePressEvent(event)

    def hoverEnterEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().hoverLeaveEvent(event)


class RefdesLabel(QGraphicsItem):
    """Separate, movable/rotatable/flippable refdes label child of PlacedCompItem."""

    _SELECTED_PEN = QPen(QColor("#ffff00"))
    _SELECTED_PEN.setWidth(0)
    _SELECTED_PEN.setStyle(Qt.PenStyle.DashLine)

    def __init__(self, comp: PlacedCompItem):
        super().__init__(comp)
        self._comp = comp
        self._offset = QPointF(0, 0)
        self._rotation = 0
        self._flip_h = False
        self._flip_v = False

        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable |
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
            QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setZValue(100)

        self._update_pos()
        self._build_label_transform()

    # ── Public API ───────────────────────────────────────────

    def setLabelRotation(self, deg: int):
        self._rotation = deg % 360
        self._build_label_transform()
        self.update()

    def setLabelFlipH(self, flip: bool):
        self._flip_h = flip
        self._build_label_transform()
        self.update()

    def setLabelFlipV(self, flip: bool):
        self._flip_v = flip
        self._build_label_transform()
        self.update()

    # ── Position ─────────────────────────────────────────────

    def _default_pos(self) -> QPointF:
        br = self._comp._body_bbox
        if br.isEmpty():
            return QPointF(0, 10)
        fs = self._comp._calc_font_px()
        gap = self._comp._label_gap_svg()
        baseline_y = br.bottom() + gap + fs * 0.7
        return QPointF(br.center().x(), baseline_y)

    def _update_pos(self):
        self.setPos(self._default_pos() + self._offset)

    def _build_label_transform(self):
        if self.parentItem():
            self.parentItem().prepareGeometryChange()
        t = QTransform()
        if self._flip_h:
            t.scale(-1, 1)
        if self._flip_v:
            t.scale(1, -1)
        t.rotate(self._rotation)
        self.setTransform(t)

    # ── Qt interface ─────────────────────────────────────────

    def boundingRect(self):
        fs = self._comp._calc_font_px()
        text = self._comp.refdes()
        tw = len(text) * fs * 0.65
        h = fs
        return QRectF(-1, -h - 1, tw + 2, h + 2)

    def shape(self):
        fs = self._comp._calc_font_px()
        text = self._comp.refdes()
        tw = len(text) * fs * 0.65
        h = fs
        path = QPainterPath()
        path.addRect(-1, -h - 1, tw + 2, h + 2)
        return path

    def paint(self, painter, option, widget=None):
        fs = self._comp._calc_font_px()
        font = QFont("sans-serif")
        font.setPixelSize(fs)
        painter.setFont(font)
        color = QColor("#ffcc00") if self.isSelected() else QColor("#f0f0f0")
        painter.setPen(QPen(color, 0))
        painter.drawText(QPointF(0, 0), self._comp.refdes())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            dp = self._default_pos()
            new_offset = QPointF(value.x() - dp.x(), value.y() - dp.y())
            self._offset = new_offset
            self.parentItem().prepareGeometryChange()
        if change in (QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged,
                      QGraphicsItem.GraphicsItemChange.ItemTransformHasChanged):
            if self._comp._on_moved:
                self._comp._on_moved()
            if self.scene():
                r = self.sceneBoundingRect().adjusted(-5, -5, 5, 5)
                self.scene().update(r)
        return super().itemChange(change, value)
