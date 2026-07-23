"""Breadboard window — standalone QMainWindow for perfboard layout."""

from __future__ import annotations
import os
import re
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene,
    QWidget, QVBoxLayout, QMessageBox,
)
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QMouseEvent

from Breadboard.board import Board
from Breadboard.pcb_library import parse_pcb_svg, Package
from Breadboard.items.placed_comp import PlacedCompItem
from Breadboard.items.placed_comp import _all_copper_centers
from Breadboard.items.ratline_item import RatlineItem

# ── Paths ────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent.parent  # Pulsar/
PCBS_DIR = SCRIPT_DIR / "Breadboard" / "pcb"

# LABEL_MAP: footprint SVG filename → display name
# Must match the dialog dropdown values in canvas_core.py
_LABEL_MAP: dict[str, str] = {
    "axial_lay_2_200mil_pcb.svg": "Res_5mm",
    "axial_lay_2_300mil_pcb.svg": "Res_8mm",
    "axial_lay_2_400mil_pcb.svg": "Res_10mm",
    "axial_lay_2_500mil_pcb.svg": "Res_13mm",
    "axial_lay_2_600mil_pcb.svg": "Res_15mm",
    "axial_lay_2_800mil_pcb.svg": "Res_20mm",
}

# ── Colors ────────────────────────────────────────────────────
BOARD_FILL = QColor("#2b4a2b")
BOARD_OUTLINE = QColor("#3a6a3a")
HOLE_FILL = QColor("#222222")
HOLE_OUTLINE = QColor("#c9a84c")
BG_COLOR = QColor("#c8bc9a")


class BoardView(QGraphicsView):
    """QGraphicsView with ПКМ pan, wheel zoom, and board background."""

    def __init__(self, board: Board, scene: QGraphicsScene):
        super().__init__(scene)
        self._board = board
        self._panning = False
        self._last_pan_pos = QPointF()
        self._saved_anchor = self.transformationAnchor()

        self.setRenderHints(
            QPainter.RenderHint.Antialiasing |
            QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def fitBoard(self, margin_mm: float = 10):
        s = self.scene()
        if s is not None and s.sceneRect().isValid():
            r = s.sceneRect().adjusted(-margin_mm, -margin_mm, margin_mm, margin_mm)
            self.fitInView(r, Qt.AspectRatioMode.KeepAspectRatio)

    def drawBackground(self, painter: QPainter, rect: QRectF):
        # Заливаем весь видимый участок фоновым цветом
        painter.fillRect(rect, BG_COLOR)

        b = self._board

        # Board rect
        br = QRectF(0, 0, b.board_width_mm, b.board_height_mm)
        painter.fillRect(br, BOARD_FILL)
        painter.setPen(QPen(BOARD_OUTLINE, 0.3))
        painter.drawRect(br)

        # Holes
        hole_pen = QPen(HOLE_OUTLINE, 0.15)
        painter.setPen(hole_pen)
        r_out = 0.5  # outer radius in mm
        r_in = 0.25  # inner radius
        for col in range(b.cols):
            for row in range(b.rows):
                hx, hy = b.hole_pos(col, row)
                painter.setBrush(HOLE_FILL)
                painter.drawEllipse(QPointF(hx, hy), r_out, r_out)

    def wheelEvent(self, event):
        factor = 1.15 ** (event.angleDelta().y() / 120)
        new_zoom = self.transform().m11() * factor
        if new_zoom < 0.05 or new_zoom > 50.0:
            return
        self.scale(factor, factor)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_R:
            for item in self.scene().selectedItems():
                if isinstance(item, PlacedCompItem):
                    item.setCompRotation(item._rotation + 90)
            return
        if key in (Qt.Key.Key_H, Qt.Key.Key_V) and (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            flip_h = key == Qt.Key.Key_H
            for item in self.scene().selectedItems():
                if isinstance(item, PlacedCompItem):
                    if flip_h:
                        item.setFlipH(not item._flip_h)
                    else:
                        item.setFlipV(not item._flip_v)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton:
            self._panning = True
            self._last_pan_pos = QPointF(event.pos())
            self._saved_anchor = self.transformationAnchor()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning:
            delta = QPointF(event.pos()) - self._last_pan_pos
            if delta.manhattanLength() > 2:
                self._last_pan_pos = QPointF(event.pos())
                self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
                self.translate(delta.x(), delta.y())
                self.setTransformationAnchor(self._saved_anchor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.RightButton and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class BreadboardWindow(QMainWindow):
    """Standalone breadboard layout window."""

    def __init__(self, app):
        super().__init__()
        self._app = app
        self._board = Board()
        self._scene = QGraphicsScene(self)
        self._view = BoardView(self._board, self._scene)
        self._placements: list[PlacedCompItem] = []
        self._ratlines: list[RatlineItem] = []
        self._connection_data: list[list[str]] = []

        self.setWindowTitle("Макетная плата — Pulsar")
        self.setMinimumSize(600, 450)
        self.resize(900, 680)
        self.setCentralWidget(self._view)

        app.destroyed.connect(self.close)

        # Scene rect
        self._scene.setSceneRect(0, 0,
                                  self._board.board_width_mm,
                                  self._board.board_height_mm)

        # Load components from current schematic
        self._load_from_schematic()

        # Начальный зум: 4 пикселя на мм (плата 70×90 = 280×360 px)
        self._view.resetTransform()
        self._view.scale(4.0, 4.0)

    # ── Public ────────────────────────────────────────────────

    def reload(self):
        """Re-scan the schematic and rebuild the board."""
        self._clear()
        self._load_from_schematic()
        self._view.resetTransform()
        self._view.scale(4.0, 4.0)

    # ── Internal ──────────────────────────────────────────────

    def _clear(self):
        for item in self._placements:
            self._scene.removeItem(item)
        self._placements.clear()
        self._clear_ratlines()
        self._connection_data.clear()

    def _clear_ratlines(self):
        for item in self._ratlines:
            self._scene.removeItem(item)
        self._ratlines.clear()

    def _load_from_schematic(self):
        canvas = self._app._tabs.current_canvas()
        if canvas is None:
            return

        # 1. Collect components with footprint from the schematic
        comp_data: list[tuple[str, str, str]] = []  # (refdes, footprint, value)
        for item in canvas.items():
            from EDA.app.items.component_item import ComponentGraphicsItem
            if not isinstance(item, ComponentGraphicsItem):
                continue
            fp = item.footprint()
            if fp:
                comp_data.append((item.refdes(), fp, item.value()))

        if not comp_data:
            QMessageBox.information(
                self, "Макетная плата",
                "На схеме нет компонентов с указанным footprint (МП).\n"
                "Откройте схему, задайте footprint в свойствах компонента."
            )
            return

        # 2. Load PCB SVGs and place components
        col = 0
        row = 2  # start a bit from the top
        max_col = self._board.cols - 2

        for refdes, fp, value in comp_data:
            svg_path = PCBS_DIR / fp
            if not svg_path.exists():
                print(f"[breadboard] SVG not found: {svg_path}")
                continue

            try:
                pkg = parse_pcb_svg(str(svg_path))
            except Exception as e:
                print(f"[breadboard] Failed to parse {svg_path}: {e}")
                continue

            coppers = _all_copper_centers(pkg)
            dx_cols = 1
            if len(coppers) >= 2:
                mmu = pkg.width_mm / pkg.vb[2] if pkg.vb[2] else 1
                pin_spacing_mm = (coppers[-1][0] - coppers[0][0]) * mmu
                dx_cols = max(1, round(pin_spacing_mm / self._board.pitch_mm))

            if col + dx_cols >= max_col:
                col = 0
                row += 2

            hole_x, hole_y = self._board.hole_pos(col, row)

            comp_item = PlacedCompItem(
                pkg, fp, refdes,
                board=self._board,
                on_moved=self._update_ratlines,
            )
            comp_item.setPos(hole_x, hole_y)
            self._scene.addItem(comp_item)
            self._placements.append(comp_item)

            col += dx_cols + 1  # +1 for gap

        # 3. Build connection map from canvas wire graph and draw ratlines
        self._draw_ratlines(canvas)

    def _draw_ratlines(self, canvas):
        """Build net groups from the canvas wire graph and store connection data."""
        from EDA.app.items.component_item import ComponentGraphicsItem

        cid_to_refdes: dict[int, str] = {}
        for item in canvas.items():
            if isinstance(item, ComponentGraphicsItem):
                cid_to_refdes[id(item)] = item.refdes()

        wire_graph = canvas._wire_graph
        comp_wire_links = canvas._comp_wire_links

        processed_wires: set = set()
        for (_cid, _pin_idx), (wire, _ep, _px, _py) in comp_wire_links.items():
            if wire in processed_wires:
                continue
            connected = wire_graph.get_connected(wire)
            processed_wires.update(connected)

            refdes_set: set[str] = set()
            for (cid, pin_idx), (w2, _ep2, _px2, _py2) in comp_wire_links.items():
                if w2 not in connected:
                    continue
                rd = cid_to_refdes.get(cid)
                if rd:
                    refdes_set.add(rd)

            if len(refdes_set) >= 2:
                self._connection_data.append(sorted(refdes_set))

        self._update_ratlines()

    def _update_ratlines(self):
        """Rebuild ratline paths from current component positions."""
        self._clear_ratlines()
        board_comps = {c.refdes(): c for c in self._placements}
        for refdes_list in self._connection_data:
            points: list[QPointF] = []
            for rd in refdes_list:
                bc = board_comps.get(rd)
                if bc is not None:
                    centers = bc.all_pin_centers()
                    if centers:
                        points.append(QPointF(centers[0][0], centers[0][1]))
            if len(points) >= 2:
                item = RatlineItem(f"net_{len(self._ratlines)}", points)
                self._scene.addItem(item)
                self._ratlines.append(item)
