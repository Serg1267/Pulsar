"""Breadboard window — standalone QMainWindow for perfboard layout."""

from __future__ import annotations
import os
import re
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QGraphicsView, QGraphicsScene,
    QWidget, QVBoxLayout, QMessageBox, QMenuBar, QMenu, QFileDialog,
)
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QBrush, QColor, QMouseEvent, QShortcut, QKeySequence, QActionGroup
import json

from Breadboard.board import Board
from Breadboard.pcb_library import parse_pcb_svg, Package
from Breadboard.items.placed_comp import PlacedCompItem, RefdesLabel
from Breadboard.items.placed_comp import _all_copper_centers
from Breadboard.items.ratline_item import RatlineItem
from math import sqrt

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

# PIN_MAP: footprint SVG filename → [copper_idx_for_pin_0, pin_1, …]
# TO-92 ammo: left=emitter(0), middle=base(1), right=collector(2)
# NPN/PNP .sym: pin 0=C, pin 1=E, pin 2=B
_PIN_MAP: dict[str, list[int]] = {
    # SVG has 6 coppers (copper0+copper1 for each of 3 pins)
    # Unique pins: even index 0/2/4 → left/middle/right
    # .sym: pin 0=C(collector→right), pin 1=E(emitter→left), pin 2=B(base→middle)
    "sparkfun-discretesemi_to-92-ammo_pcb.svg": [4, 0, 2],
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

        # Routing mode state
        self._routing_mode = False
        self._routing_start: QPointF | None = None
        self._routing_pin: tuple[str, int] | None = None  # (refdes, pin_num) from first click
        self._routing_hover_pin: tuple[PlacedCompItem, int] | None = None
        self._routing_hover_wire: tuple[RatlineItem, float, QPointF] | None = None
        self._cursor_scene = QPointF()
        self._on_add_connection = None  # callable(ep_list: list[tuple])
        self._on_add_junction = None  # callable(jrd1, jpin1, jrd2, jpin2, ratio, rd3, pin3)
        self._on_manual_segment = None  # callable(seg: RatlineItem)
        self._on_segment_deleted = None  # callable(endpoints: list[tuple])
        self._on_before_change = None  # callable() — save snapshot before state change

        # Shortcuts — use QShortcut to avoid platform key-code quirks
        QShortcut(QKeySequence("Ctrl+H"), self, self._flip_selected_h)
        QShortcut(QKeySequence("Ctrl+V"), self, self._flip_selected_v)

    def _flip_selected_h(self):
        if self._on_before_change:
            self._on_before_change()
        for item in self.scene().selectedItems():
            if isinstance(item, PlacedCompItem):
                item.setFlipH(not item._flip_h)
            elif isinstance(item, RefdesLabel):
                item.setLabelFlipH(not item._flip_h)

    def _flip_selected_v(self):
        if self._on_before_change:
            self._on_before_change()
        for item in self.scene().selectedItems():
            if isinstance(item, PlacedCompItem):
                item.setFlipV(not item._flip_v)
            elif isinstance(item, RefdesLabel):
                item.setLabelFlipV(not item._flip_v)

    def _rotate_selected(self):
        if self._on_before_change:
            self._on_before_change()
        for item in self.scene().selectedItems():
            if isinstance(item, PlacedCompItem):
                item.setCompRotation(item._rotation + 90)
            elif isinstance(item, RefdesLabel):
                item.setLabelRotation(item._rotation + 90)

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

    def drawForeground(self, painter: QPainter, rect: QRectF):
        if not self._routing_mode:
            return
        b = self._board
        col, row = b.nearest_hole(self._cursor_scene.x(), self._cursor_scene.y())
        hx, hy = b.hole_pos(col, row)
        painter.setPen(QPen(QColor(255, 255, 255, 140), 0.08, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(hx, 0),
                         QPointF(hx, b.board_height_mm))
        painter.drawLine(QPointF(0, hy),
                         QPointF(b.board_width_mm, hy))

        # Pin highlight
        if self._routing_hover_pin is not None:
            comp, idx = self._routing_hover_pin
            centers = comp.all_pin_centers()
            if idx < len(centers):
                cx, cy = centers[idx]
                painter.setPen(QPen(QColor("#ffffff"), 0.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), 0.8, 0.8)

        # Preview line during routing (snapped to nearest hole)
        if self._routing_start is not None:
            painter.setPen(QPen(QColor("#ffffff"), 0.15, Qt.PenStyle.DashLine))
            painter.drawLine(self._routing_start, QPointF(hx, hy))
            # Hole indicator ring
            painter.setPen(QPen(QColor("#ffffff"), 0.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(hx, hy), 0.5, 0.5)

    def wheelEvent(self, event):
        factor = 1.15 ** (event.angleDelta().y() / 120)
        new_zoom = abs(self.transform().m11()) * factor
        if new_zoom < 0.05 or new_zoom > 50.0:
            return
        self.scale(factor, factor)
        win = self.window()
        if hasattr(win, '_zoom_factor'):
            win._zoom_factor *= factor

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_R and not self._routing_mode:
            self._rotate_selected()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not self._routing_mode:
            for item in list(self.scene().selectedItems()):
                if isinstance(item, RatlineItem):
                    pts = item.points()
                    endpoints = []
                    for pt in pts:
                        comp, cidx = self._find_pin_at(pt)
                        if comp is not None and cidx >= 0:
                            fp = comp.footprint()
                            pm = _PIN_MAP.get(fp)
                            if pm:
                                try:
                                    pin_num = pm.index(cidx)
                                except ValueError:
                                    pin_num = cidx
                            else:
                                pin_num = cidx
                            endpoints.append((comp.refdes(), pin_num, None, None))
                        else:
                            endpoints.append((None, None, pt.x(), pt.y()))
                    self.scene().removeItem(item)
                    if self._on_segment_deleted:
                        self._on_segment_deleted(endpoints)
            return
        if key == Qt.Key.Key_N and not event.modifiers():
            self._toggle_routing_mode()
            return
        if key == Qt.Key.Key_Escape and self._routing_mode:
            self._cancel_routing()
            self._routing_mode = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.scene().update()
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

        if self._routing_mode and event.button() == Qt.MouseButton.LeftButton:
            sp = self.mapToScene(event.pos())
            snap = self._snap_pos(sp)
            if self._routing_start is None:
                self._routing_start = snap
                rd, pn = self._pin_info_at(snap)
                self._routing_pin = (rd, pn) if rd is not None else None
                self.scene().update()
            else:
                rd1, p1 = self._routing_pin if self._routing_pin else (None, -1)
                rd2, p2 = self._pin_info_at(snap)
                ep1 = (rd1, p1, None, None) if rd1 is not None else (None, None, self._routing_start.x(), self._routing_start.y())

                # Check if second endpoint snaps to wire body (T-junction)
                wire, w_ratio, w_pt = self._find_wire_body_at(sp)
                if wire is not None and rd1 is not None:
                    # Try to find parent segment endpoints as component pins
                    wpts = wire.points()
                    ew_rd1, ew_p1 = self._pin_info_at(wpts[0])
                    ew_rd2, ew_p2 = self._pin_info_at(wpts[1])
                    if ew_rd1 is not None and ew_rd2 is not None:
                        # Junction on (ew_rd1, ew_p1)↔(ew_rd2, ew_p2) at w_ratio
                        if self._on_add_junction:
                            self._on_add_junction(ew_rd1, ew_p1, ew_rd2, ew_p2, w_ratio, rd1, p1)
                        self._routing_start = snap
                        rd, pn = self._pin_info_at(snap)
                        self._routing_pin = (rd, pn) if rd is not None else None
                        self.scene().update()
                        event.accept()
                        return
                    # Fall through: wire endpoints aren't both pins → treat as hole
                    ep2 = (None, None, snap.x(), snap.y())
                    if self._on_add_connection:
                        self._on_add_connection([ep1, ep2])
                elif rd1 is not None or rd2 is not None:
                    ep2 = (rd2, p2, None, None) if rd2 is not None else (None, None, snap.x(), snap.y())
                    if self._on_add_connection:
                        self._on_add_connection([ep1, ep2])
                else:
                    seg = RatlineItem(
                        f"seg_{len(self.scene().items())}",
                        [self._routing_start, snap],
                        board=self._board)
                    self.scene().addItem(seg)
                    if self._on_manual_segment:
                        self._on_manual_segment(seg)
                self._routing_start = snap  # chain from new point
                rd, pn = self._pin_info_at(snap)
                self._routing_pin = (rd, pn) if rd is not None else None
                self.scene().update()
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton and not (
            event.modifiers() & (Qt.KeyboardModifier.ControlModifier |
                                 Qt.KeyboardModifier.ShiftModifier)):
            for item in self.scene().items():
                if isinstance(item, RatlineItem) and item.isSelected():
                    item.setSelected(False)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        sp = self.mapToScene(event.pos())

        if self._routing_mode:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self._cursor_scene = sp
            # Pin hover detection
            comp, idx = self._find_pin_at(sp)
            if (comp, idx) != self._routing_hover_pin:
                self._routing_hover_pin = (comp, idx) if comp is not None else None
                self.scene().update()
            # Wire body hover detection (if not over a pin)
            if comp is None:
                wire, wratio, wpt = self._find_wire_body_at(sp)
                if wire is not None and self._routing_start is not None:
                    self._routing_hover_wire = (wire, wratio, wpt)
                    self.scene().update()
                else:
                    if self._routing_hover_wire is not None:
                        self._routing_hover_wire = None
                        self.scene().update()
            else:
                if self._routing_hover_wire is not None:
                    self._routing_hover_wire = None
                    self.scene().update()
            self.viewport().update()
            event.accept()
            return

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

    # ── Routing helpers ────────────────────────────────────────

    def _toggle_routing_mode(self):
        self._routing_mode = not self._routing_mode
        if self._routing_mode:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self._cursor_scene = self.mapToScene(self.mapFromGlobal(self.cursor().pos()))
            self.scene().update()
        else:
            self._cancel_routing()
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.scene().update()

    def _cancel_routing(self):
        self._routing_start = None
        self._routing_pin = None
        self._routing_hover_pin = None
        self._routing_hover_wire = None

    def _find_pin_at(self, scene_pt: QPointF,
                     tol: float = 0.6) -> tuple[PlacedCompItem | None, int]:
        for item in self.scene().items():
            if isinstance(item, PlacedCompItem):
                centers = item.all_pin_centers()
                for i, (cx, cy) in enumerate(centers):
                    if (QPointF(cx, cy) - scene_pt).manhattanLength() < tol:
                        return item, i
        return None, -1

    def _pin_info_at(self, scene_pt: QPointF,
                     tol: float = 0.6) -> tuple[str | None, int]:
        """Return (refdes, schematic_pin_number) or (None, -1)."""
        comp, cidx = self._find_pin_at(scene_pt, tol)
        if comp is None:
            return None, -1
        fp = comp.footprint()
        pm = _PIN_MAP.get(fp)
        if pm:
            try:
                pin_num = pm.index(cidx)
            except ValueError:
                pin_num = cidx
        else:
            pin_num = cidx
        return comp.refdes(), pin_num

    def _find_wire_body_at(self, scene_pt: QPointF, tol: float = 1.0):
        """Find closest point on any ratline segment near scene_pt.
        Returns (RatlineItem, param_ratio, closest_point) or (None, None, None)."""
        best = (None, None, None)
        best_dist = tol
        for item in self.scene().items():
            if not isinstance(item, RatlineItem):
                continue
            pts = item.points()
            for i in range(len(pts) - 1):
                a = pts[i]
                b = pts[i + 1]
                dx = b.x() - a.x()
                dy = b.y() - a.y()
                seg_len_sq = dx * dx + dy * dy
                if seg_len_sq < 1e-6:
                    continue
                t = ((scene_pt.x() - a.x()) * dx + (scene_pt.y() - a.y()) * dy) / seg_len_sq
                t = max(0.0, min(1.0, t))
                cx = a.x() + t * dx
                cy = a.y() + t * dy
                dist = sqrt((scene_pt.x() - cx) ** 2 + (scene_pt.y() - cy) ** 2)
                if dist < best_dist:
                    best_dist = dist
                    best = (item, t, QPointF(cx, cy))
        return best

    def _snap_pos(self, scene_pt: QPointF) -> QPointF:
        # 1. Pin
        comp, idx = self._find_pin_at(scene_pt)
        if comp is not None and idx >= 0:
            centers = comp.all_pin_centers()
            if idx < len(centers):
                return QPointF(centers[idx][0], centers[idx][1])
        # 2. Wire body (for T-junction)
        _wire, _ratio, wpt = self._find_wire_body_at(scene_pt)
        if _wire is not None:
            return wpt
        # 3. Hole
        col, row = self._board.nearest_hole(scene_pt.x(), scene_pt.y())
        hx, hy = self._board.hole_pos(col, row)
        return QPointF(hx, hy)


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
        self._manual_segments: list[RatlineItem] = []
        self._connection_data: list[list[tuple[str | None, int | None, float | None, float | None]]] = []
        self._junctions: list[tuple[str, int, str, int, float, str, int]] = []
        self._hidden_connections: set[frozenset] = set()
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._restoring = False
        self._components_side = True
        self._zoom_factor = 1.0
        self._view._on_add_connection = self._add_connection_entry
        self._view._on_add_junction = self._add_junction
        self._view._on_manual_segment = self._manual_segments.append
        self._view._on_segment_deleted = self._on_segment_deleted
        self._view._on_before_change = self._save_snapshot

        self.setWindowTitle("Макетная плата — Pulsar")
        self.setMinimumSize(600, 450)
        self.resize(900, 680)
        self._create_menu_bar()
        self.setCentralWidget(self._view)

        app.destroyed.connect(self.close)

        # Scene rect
        self._scene.setSceneRect(0, 0,
                                  self._board.board_width_mm,
                                  self._board.board_height_mm)

        # Load components from current schematic
        self._load_from_schematic()

        # Начальный зум: 4 пикселя на мм (плата 70×90 = 280×360 px)
        self._apply_view_scale()

    # ── Public ────────────────────────────────────────────────

    def _create_menu_bar(self):
        mb = self.menuBar()
        mb.setStyleSheet("""
            QMenuBar { background-color: #ddd; color: #000; }
            QMenuBar::item:selected { background-color: #eee; }
            QMenu { background-color: #ddd; color: #000; }
            QMenu::item:selected { background-color: #eee; color: #000; }
        """)

        # Файл
        fm = mb.addMenu("Файл")
        open_act = fm.addAction("Открыть\tCtrl+O")
        open_act.triggered.connect(self._open_brd)
        fm.addSeparator()
        save_act = fm.addAction("Сохранить\tCtrl+S")
        save_act.triggered.connect(self._save_brd)
        save_as_act = fm.addAction("Сохранить как…\tCtrl+Shift+S")
        save_as_act.triggered.connect(self._save_brd)
        fm.addSeparator()
        export_act = fm.addAction("Экспорт в .JPG")
        export_act.triggered.connect(self._export_jpg)
        export_pdf_act = fm.addAction("Экспорт в .PDF")
        export_pdf_act.triggered.connect(self._export_pdf)
        fm.addSeparator()
        quit_act = fm.addAction("Выход\tCtrl+Q")
        quit_act.triggered.connect(self.close)

        # Правка
        em = mb.addMenu("Правка")
        undo_act = em.addAction("Отменить\tCtrl+Z")
        undo_act.triggered.connect(self._undo)
        redo_act = em.addAction("Повторить\tCtrl+Shift+Z")
        redo_act.triggered.connect(self._redo)
        em.addSeparator()
        rot_act = em.addAction("Повернуть\tR")
        rot_act.triggered.connect(lambda: self._view._rotate_selected())
        em.addSeparator()
        flip_h_act = em.addAction("Отразить по горизонтали\tCtrl+H")
        flip_h_act.triggered.connect(lambda: self._view._flip_selected_h())
        flip_v_act = em.addAction("Отразить по вертикали\tCtrl+V")
        flip_v_act.triggered.connect(lambda: self._view._flip_selected_v())

        # Вид
        vm = mb.addMenu("Вид")
        self._side_group = QActionGroup(self)
        comp_side = self._side_group.addAction("Сторона компонентов")
        comp_side.setCheckable(True)
        comp_side.setChecked(True)
        trace_side = self._side_group.addAction("Сторона дорожек")
        trace_side.setCheckable(True)
        vm.addActions([comp_side, trace_side])
        self._side_group.triggered.connect(self._on_side_changed)
        vm.addSeparator()
        fit_act = vm.addAction("Вписать в экран")
        fit_act.triggered.connect(self._fit_in_view)

    def _save_brd(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить макет", "", "Макетная плата (*.mb)")
        if path:
            if not path.endswith(".mb"):
                path += ".mb"
            snap = self._take_snapshot()
            snap['version'] = 1
            snap['format'] = 'pulsar-breadboard'
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(snap, f, ensure_ascii=False, indent=2)
            except Exception as e:
                QMessageBox.warning(self, "Ошибка", f"Не удалось сохранить:\n{e}")

    def _export_jpg(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт в JPG", "untitled.jpg", "JPEG (*.jpg)")
        if not path:
            return
        base, _ = os.path.splitext(path)
        path = base + '.jpg'
        pixmap = self._view.grab()
        pixmap.save(path, "JPG")

    def _export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт в PDF", "untitled.pdf", "PDF (*.pdf)")
        if not path:
            return
        base, ext = os.path.splitext(path)
        if ext.lower() != '.pdf':
            path = base + '.pdf'
        from PySide6.QtGui import QPdfWriter, QPageSize, QPainter
        from PySide6.QtCore import QRectF, QPointF
        b = self._board
        board_w = b.board_width_mm
        board_h = b.board_height_mm
        writer = QPdfWriter(path)
        writer.setPageSize(QPageSize(QPageSize.A4))
        dpi = 300
        writer.setResolution(dpi)
        margin_px = 50
        page_w_px = int(210 * dpi / 25.4)
        page_h_px = int(297 * dpi / 25.4)
        avail_w = page_w_px - 2 * margin_px
        avail_h = page_h_px - 2 * margin_px
        ar = board_w / board_h
        if ar > avail_w / avail_h:
            target_w = avail_w
            target_h = avail_w / ar
        else:
            target_h = avail_h
            target_w = avail_h * ar
        tx = (page_w_px - target_w) / 2
        ty = (page_h_px - target_h) / 2
        p = QPainter(writer)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale_x = target_w / board_w
        scale_y = target_h / board_h
        p.save()
        p.translate(tx, ty)
        p.scale(scale_x, scale_y)
        p.fillRect(QRectF(0, 0, board_w, board_h), BOARD_FILL)
        p.setPen(QPen(BOARD_OUTLINE, 0.3))
        p.drawRect(QRectF(0, 0, board_w, board_h))
        hole_pen = QPen(HOLE_OUTLINE, 0.15)
        p.setPen(hole_pen)
        for col in range(b.cols):
            for row in range(b.rows):
                hx, hy = b.hole_pos(col, row)
                p.setBrush(HOLE_FILL)
                p.drawEllipse(QPointF(hx, hy), 0.5, 0.5)
        p.restore()
        self._scene.render(p, QRectF(tx, ty, target_w, target_h),
                           QRectF(0, 0, board_w, board_h))
        p.end()

    def _open_brd(self):
        path, _ = QFileDialog.getOpenFileName(self, "Открыть макет", "", "Макетная плата (*.mb)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                snap = json.load(f)
        except Exception as e:
            QMessageBox.warning(self, "Ошибка", f"Не удалось открыть:\n{e}")
            return

        self._restoring = True
        self._clear()

        # Re-create components from saved data
        for cd in snap.get('comps', []):
            fp = cd.get('footprint', '')
            if not fp:
                continue
            svg_path = PCBS_DIR / fp
            if not svg_path.exists():
                continue
            try:
                pkg = parse_pcb_svg(str(svg_path))
            except Exception:
                continue

            comp = PlacedCompItem(
                pkg, fp, cd['refdes'],
                board=self._board,
                on_moved=self._update_ratlines,
            )
            comp._on_before_drag = self._save_snapshot
            comp.setPos(cd['x'], cd['y'])
            comp._rotation = cd['rot']
            comp._flip_h = cd['flip_h']
            comp._flip_v = cd['flip_v']
            comp._build_transform()
            comp.setBodyOpacity(cd.get('opacity', 1.0))
            self._scene.addItem(comp)
            self._placements.append(comp)

            # Restore label
            lbl = comp._label_item
            lbl._offset = QPointF(cd['loffset_x'], cd['loffset_y'])
            lbl._rotation = cd.get('lrot', 0)
            lbl._flip_h = cd.get('lflip_h', False)
            lbl._flip_v = cd.get('lflip_v', False)
            lbl._build_label_transform()
            lbl.setPos(cd['lx'], cd['ly'])

        # Restore connections
        self._connection_data = [
            [tuple(ep) for ep in entry] for entry in snap.get('conn_data', [])]
        self._junctions = list(snap.get('junctions', []))
        self._hidden_connections = {frozenset(tuple(ep) for ep in p) for p in snap.get('hidden', [])}
        for pts, name in snap.get('manual_pts', []):
            qpts = [QPointF(x, y) for x, y in pts]
            seg = RatlineItem(name, qpts, board=self._board)
            self._scene.addItem(seg)
            self._manual_segments.append(seg)

        # Rebuild view
        self._apply_view_scale()
        self._restoring = False
        self._update_ratlines()
        self._undo_stack.clear()
        self._redo_stack.clear()

    def _fit_in_view(self):
        self._view.fitInView(QRectF(0, 0, self._board.board_width_mm, self._board.board_height_mm),
                             Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_factor = self._view.transform().m22() / 4.0
        self._apply_view_scale()

    def _apply_view_scale(self):
        sx = 4.0 if self._components_side else -4.0
        sy = 4.0
        self._view.resetTransform()
        self._view.scale(sx * self._zoom_factor, sy * self._zoom_factor)
        cx = self._board.margin_x() + (self._board.cols - 1) * self._board.pitch_mm / 2
        cy = self._board.margin_y() + (self._board.rows - 1) * self._board.pitch_mm / 2
        self._view.centerOn(cx, cy)

    def _on_side_changed(self, action):
        self._components_side = (action.text() == "Сторона компонентов")
        opacity = 1.0 if self._components_side else 0.25
        for comp in self._placements:
            comp.setBodyOpacity(opacity)
        self._apply_view_scale()

    # ── Snapshot / Undo / Redo ───────────────────────────────

    def _take_snapshot(self) -> dict:
        comps = []
        for c in self._placements:
            lbl = c._label_item
            comps.append({
                'refdes': c.refdes(),
                'footprint': c.footprint(),
                'x': c.pos().x(), 'y': c.pos().y(),
                'rot': c._rotation,
                'flip_h': c._flip_h, 'flip_v': c._flip_v,
                'opacity': c._body_opacity,
                'lx': lbl.pos().x(), 'ly': lbl.pos().y(),
                'lrot': lbl._rotation,
                'lflip_h': lbl._flip_h, 'lflip_v': lbl._flip_v,
                'loffset_x': lbl._offset.x(), 'loffset_y': lbl._offset.y(),
            })
        return {
            'comps': comps,
            'conn_data': [[list(ep) for ep in entry] for entry in self._connection_data],
            'junctions': list(self._junctions),
            'hidden': [(a, b) for a, b in self._hidden_connections],
            'manual_pts': [([(p.x(), p.y()) for p in seg.points()], seg.net_name())
                           for seg in self._manual_segments],
        }

    def _restore_snapshot(self, snap: dict):
        self._restoring = True
        for cd in snap['comps']:
            for c in self._placements:
                if c.refdes() == cd['refdes']:
                    c.setPos(cd['x'], cd['y'])
                    c._rotation = cd['rot']
                    c._flip_h = cd['flip_h']
                    c._flip_v = cd['flip_v']
                    c._build_transform()
                    c.setBodyOpacity(cd['opacity'])
                    lbl = c._label_item
                    lbl._offset = QPointF(cd['loffset_x'], cd['loffset_y'])
                    lbl.setPos(cd['lx'], cd['ly'])
                    lbl._rotation = cd['lrot']
                    lbl._flip_h = cd['lflip_h']
                    lbl._flip_v = cd['lflip_v']
                    lbl._build_label_transform()
                    break

        self._clear_ratlines()
        self._clear_manual_segments()
        self._connection_data = [
            [tuple(ep) for ep in entry] for entry in snap['conn_data']]
        self._junctions = list(snap['junctions'])
        self._hidden_connections = {frozenset(tuple(ep) for ep in p) for p in snap['hidden']}
        for pts, name in snap['manual_pts']:
            qpts = [QPointF(x, y) for x, y in pts]
            seg = RatlineItem(name, qpts, board=self._board)
            self._scene.addItem(seg)
            self._manual_segments.append(seg)
        self._restoring = False
        self._update_ratlines()

    def _save_snapshot(self):
        self._undo_stack.append(self._take_snapshot())
        self._redo_stack.clear()

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(self._take_snapshot())
        self._restore_snapshot(self._undo_stack.pop())

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(self._take_snapshot())
        self._restore_snapshot(self._redo_stack.pop())

    def reload(self):
        """Re-scan the schematic and rebuild the board."""
        self._clear()
        self._load_from_schematic()
        self._apply_view_scale()

    # ── Internal ──────────────────────────────────────────────

    def _on_segment_deleted(self, endpoints):
        """Handle segment deletion from the view."""
        self._save_snapshot()
        # Detect junction segment: exactly one pin endpoint → match _junctions
        ep_pins = [(ep[0], ep[1]) for ep in endpoints if ep[0] is not None]
        if len(ep_pins) == 1:
            rd3, pin3 = ep_pins[0]
            for j in list(self._junctions):
                if j[5] == rd3 and j[6] == pin3:
                    self._junctions.remove(j)
                    return
        # Pin→pin → hidden_connections
        if (endpoints[0][0] is not None and endpoints[1][0] is not None):
            self._hidden_connections.add(
                frozenset([(endpoints[0][0], endpoints[0][1]),
                           (endpoints[1][0], endpoints[1][1])]))
        else:
            # Any hole endpoint → remove matching entry from _connection_data
            for entry in list(self._connection_data):
                if len(entry) != len(endpoints):
                    continue
                match = True
                for a, b in zip(entry, endpoints):
                    if a[0] != b[0] or a[1] != b[1]:
                        match = False
                        break
                    # For hole points, also compare coordinates
                    if a[0] is None and (a[2] != b[2] or a[3] != b[3]):
                        match = False
                        break
                if match:
                    self._connection_data.remove(entry)
                    break

    def _clear(self):
        for item in self._placements:
            self._scene.removeItem(item)
        self._placements.clear()
        self._clear_ratlines()
        self._clear_manual_segments()
        self._connection_data.clear()
        self._junctions.clear()
        self._hidden_connections.clear()

    def _clear_ratlines(self):
        for item in list(self._ratlines):
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        self._ratlines.clear()

    def _clear_manual_segments(self):
        for item in list(self._manual_segments):
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        self._manual_segments.clear()

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
            comp_item._on_before_drag = self._save_snapshot
            comp_item.setPos(hole_x, hole_y)
            self._scene.addItem(comp_item)
            self._placements.append(comp_item)

            col += dx_cols + 1  # +1 for gap

        # 3. Build connection map from canvas wire graph and draw ratlines
        self._draw_ratlines(canvas)

    def _draw_ratlines(self, canvas):
        """Build net groups from the canvas wire graph and store pin‑specific
        connection data. Each net entry is (refdes, pin_number) so that
        _update_ratlines can draw ratlines to the correct pin."""
        from EDA.app.items.component_item import ComponentGraphicsItem

        cid_to_refdes: dict[int, str] = {}
        for item in canvas.items():
            if isinstance(item, ComponentGraphicsItem):
                cid_to_refdes[id(item)] = item.refdes()

        wire_graph = canvas._wire_graph
        comp_wire_links = canvas._comp_wire_links

        processed_wires: set = set()
        self._connection_data = []
        for (_cid, _pin_idx), (wire, _ep, _px, _py) in comp_wire_links.items():
            if wire in processed_wires:
                continue
            connected = wire_graph.get_connected(wire)
            processed_wires.update(connected)

            net_entries: list[tuple[str | None, int | None, float | None, float | None]] = []
            for (cid, pin_idx), (w2, _ep2, _px2, _py2) in comp_wire_links.items():
                if w2 not in connected:
                    continue
                rd = cid_to_refdes.get(cid)
                if rd:
                    net_entries.append((rd, pin_idx, None, None))

            if len(set(rd for rd, *_ in net_entries)) >= 2:
                self._connection_data.append(net_entries)

        self._update_ratlines()

    def _update_ratlines(self):
        """Rebuild ratline paths from current component positions."""
        if self._restoring:
            return
        self._clear_ratlines()
        board_comps = {c.refdes(): c for c in self._placements}

        def _pin_pos(rd, pin_number):
            bc = board_comps.get(rd)
            if bc is None:
                return None
            centers = bc.all_pin_centers()
            fp_name = bc.footprint()
            pin_map = _PIN_MAP.get(fp_name)
            if pin_map and pin_number < len(pin_map):
                idx = pin_map[pin_number]
            else:
                idx = pin_number
            if idx >= len(centers):
                idx = 0
            return QPointF(centers[idx][0], centers[idx][1])

        # 1. Standard connection data
        for net_entries in self._connection_data:
            points: list[QPointF] = []
            for rd, pin_number, hx, hy in net_entries:
                if rd is not None:
                    pt = _pin_pos(rd, pin_number)
                    if pt is not None:
                        points.append(pt)
                elif hx is not None and hy is not None:
                    points.append(QPointF(hx, hy))
            if len(points) >= 2:
                for i in range(len(points) - 1):
                    ep_a = net_entries[i]
                    ep_b = net_entries[i + 1]
                    if (ep_a[0] is not None and ep_b[0] is not None and
                        frozenset([(ep_a[0], ep_a[1]), (ep_b[0], ep_b[1])]) in self._hidden_connections):
                        continue
                    seg = RatlineItem(
                        f"seg_{len(self._ratlines)}",
                        [points[i], points[i + 1]],
                        board=self._board)
                    self._scene.addItem(seg)
                    self._ratlines.append(seg)

        # 2. Junction segments (T-connections on wire bodies)
        for jrd1, jpin1, jrd2, jpin2, ratio, rd3, pin3 in self._junctions:
            pt1 = _pin_pos(jrd1, jpin1)
            pt2 = _pin_pos(jrd2, jpin2)
            pt3 = _pin_pos(rd3, pin3)
            if pt1 is None or pt2 is None or pt3 is None:
                continue
            # Junction point = ratio along parent segment
            jx = pt1.x() + ratio * (pt2.x() - pt1.x())
            jy = pt1.y() + ratio * (pt2.y() - pt1.y())
            jpt = QPointF(jx, jy)
            seg = RatlineItem(
                f"junc_{len(self._ratlines)}",
                [jpt, pt3],
                board=self._board)
            seg._is_junction = True
            self._scene.addItem(seg)
            self._ratlines.append(seg)

    def _add_connection_entry(self, endpoints):
        """Add a connection entry (pin→pin, pin→hole, or hole→pin) to _connection_data."""
        self._save_snapshot()
        if (len(endpoints) >= 2 and endpoints[0][0] is not None and endpoints[1][0] is not None):
            pair = frozenset([(endpoints[0][0], endpoints[0][1]),
                             (endpoints[1][0], endpoints[1][1])])
            self._hidden_connections.discard(pair)
        self._connection_data.append(endpoints)
        self._update_ratlines()

    def _add_junction(self, jrd1, jpin1, jrd2, jpin2, ratio, rd3, pin3):
        """Add a T-junction on the body of segment (jrd1,jpin1)↔(jrd2,jpin2)."""
        self._save_snapshot()
        self._junctions.append((jrd1, jpin1, jrd2, jpin2, ratio, rd3, pin3))
        self._update_ratlines()
