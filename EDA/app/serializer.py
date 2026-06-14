"""Сериализация/десериализация состояния SchematicCanvas в .sch (JSON)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

from EDA.core.parser.sym_parser import SymData, SymPin, SymLine, SymBox, SymCircle, SymText, SymArc
from EDA.app.items.component_item import ComponentGraphicsItem
from EDA.app.items.label_item import LabelItem
from EDA.core.router.wire_item import WireItem
from EDA.app.items.junction_item import JunctionItem
from EDA.app.items.directive_item import DirectiveItem
from EDA.app.items.node_label_item import NetLabelItem
from EDA.app.items.text_item import TextItem
from EDA.app.items.rectangle_item import RectangleItem
from EDA.app.items.circle_item import CircleItem


# ── helpers: SymData ↔ dict ──────────────────────────────────────────

def _sym_pin_from_dict(d: dict) -> SymPin:
    return SymPin(x1=d["x1"], y1=d["y1"], x2=d["x2"], y2=d["y2"],
                  pinnumber=d.get("pinnumber", ""),
                  pinlabel=d.get("pinlabel", ""))

def _sym_line_from_dict(d: dict) -> SymLine:
    return SymLine(x1=d["x1"], y1=d["y1"], x2=d["x2"], y2=d["y2"])

def _sym_box_from_dict(d: dict) -> SymBox:
    return SymBox(x=d["x"], y=d["y"], width=d["width"], height=d["height"])

def _sym_circle_from_dict(d: dict) -> SymCircle:
    return SymCircle(x=d["x"], y=d["y"], radius=d["radius"])

def _sym_text_from_dict(d: dict) -> SymText:
    return SymText(x=d["x"], y=d["y"], content=d["content"],
                   visible=d.get("visible", True))

def _sym_arc_from_dict(d: dict) -> SymArc:
    return SymArc(x=d["x"], y=d["y"], radius=d["radius"],
                  start_angle=d.get("start_angle", 0.0),
                  sweep_angle=d.get("sweep_angle", 360.0))


def sym_data_to_dict(sd: SymData) -> dict:
    return {
        "pins": [{"x1": p.x1, "y1": p.y1, "x2": p.x2, "y2": p.y2,
                   "pinnumber": p.pinnumber, "pinlabel": p.pinlabel}
                  for p in sd.pins],
        "lines": [{"x1": l.x1, "y1": l.y1, "x2": l.x2, "y2": l.y2}
                  for l in sd.lines],
        "boxes": [{"x": b.x, "y": b.y, "width": b.width, "height": b.height}
                  for b in sd.boxes],
        "circles": [{"x": c.x, "y": c.y, "radius": c.radius}
                    for c in sd.circles],
        "polygons": [[list(pt) for pt in poly] for poly in sd.polygons],
        "arcs": [{"x": a.x, "y": a.y, "radius": a.radius,
                  "start_angle": a.start_angle, "sweep_angle": a.sweep_angle}
                 for a in sd.arcs],
        "texts": [{"x": t.x, "y": t.y, "content": t.content,
                    "visible": t.visible}
                  for t in sd.texts],
        "attributes": dict(sd.attributes),
        "bounding_box": list(sd.bounding_box) if sd.bounding_box else None,
        "version": sd.version,
        "device": sd.device,
        "default_value": sd.default_value,
    }


def sym_data_from_dict(d: dict) -> SymData:
    return SymData(
        pins=[_sym_pin_from_dict(p) for p in d.get("pins", [])],
        lines=[_sym_line_from_dict(l) for l in d.get("lines", [])],
        boxes=[_sym_box_from_dict(b) for b in d.get("boxes", [])],
        circles=[_sym_circle_from_dict(c) for c in d.get("circles", [])],
        polygons=[[(pt[0], pt[1]) for pt in poly] for poly in d.get("polygons", [])],
        arcs=[_sym_arc_from_dict(a) for a in d.get("arcs", [])],
        texts=[_sym_text_from_dict(t) for t in d.get("texts", [])],
        attributes=dict(d.get("attributes", {})),
        bounding_box=tuple(d["bounding_box"]) if d.get("bounding_box") else None,
        version=d.get("version", "v"),
        device=d.get("device", ""),
        default_value=d.get("default_value", ""),
    )


# ── serialise ────────────────────────────────────────────────────────

def serialize_canvas(canvas) -> dict:
    from EDA.app.canvas import SchematicCanvas
    scene = canvas._scene
    data: dict[str, Any] = {
        "version": 2,
        "format": "spiceeda-schematic",
        "components": [],
        "wires": [],
        "junctions": [],
        "directives": [],
        "node_labels": [],
        "rectangles": [],
    }

    # Компоненты
    for item in scene.items():
        if not isinstance(item, ComponentGraphicsItem):
            continue
        t = item.transform()
        comp = {
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
        # Дочерние метки
        for child in item.childItems():
            if not isinstance(child, LabelItem):
                continue
            ct = child.transform()
            comp["labels"].append({
                "type": child.label_type(),
                "text": child.text(),
                "rel_x": child.pos().x(),
                "rel_y": child.pos().y(),
                "rotation": child.rotation(),
                "counter_flip_x": ct.m11() < 0,
                "counter_flip_y": ct.m22() < 0,
            })
        data["components"].append(comp)

    # Провода
    for item in scene.items():
        if not isinstance(item, WireItem):
            continue
        pts = item.points()
        wire_data = {
            "points": [[p.x(), p.y()] for p in pts],
            "show_start_pin": item._show_start_pin,
            "show_end_pin": item._show_end_pin,
        }
        if item.color() is not None:
            wire_data["color"] = item.color()
        data["wires"].append(wire_data)

    # Точки соединения
    for item in scene.items():
        if not isinstance(item, JunctionItem):
            continue
        data["junctions"].append({
            "x": item.pos().x(),
            "y": item.pos().y(),
        })

    # Директивы
    for item in scene.items():
        if not isinstance(item, DirectiveItem):
            continue
        data["directives"].append({
            "text": item.text(),
            "x": item.pos().x(),
            "y": item.pos().y(),
        })

    # Метки узлов
    data["node_labels"] = []
    for item in scene.items():
        if not isinstance(item, NetLabelItem):
            continue
        lo = item.label_offset()
        data["node_labels"].append({
            "text": item.text(),
            "anchor_x": item.pos().x(),
            "anchor_y": item.pos().y(),
            "label_x": lo.x(),
            "label_y": lo.y(),
        })

    # Текстовые элементы
    data["texts"] = []
    for item in scene.items():
        if not isinstance(item, TextItem):
            continue
        data["texts"].append({
            "text": item.text(),
            "x": item.pos().x(),
            "y": item.pos().y(),
            "font_family": item.font_family(),
            "font_size": item.font_size(),
        })

    # Прямоугольники
    data["rectangles"] = []
    for item in scene.items():
        if not isinstance(item, RectangleItem):
            continue
        rd = {"rect": list(item.rect())}
        if item.color() is not None:
            rd["color"] = item.color()
        data["rectangles"].append(rd)

    # Окружности
    data["circles"] = []
    for item in scene.items():
        if not isinstance(item, CircleItem):
            continue
        cd = {"rect": list(item.rect())}
        if item.color() is not None:
            cd["color"] = item.color()
        data["circles"].append(cd)

    return data


# ── deserialise ──────────────────────────────────────────────────────

def deserialize_into_canvas(canvas, data: dict):
    """Очищает сцену и загружает схему из data."""
    from EDA.app.canvas import SchematicCanvas
    scene = canvas._scene
    scene.clear()

    # Сброс ВСЕГО внутреннего состояния canvas (scene.clear() уничтожил все items,
    # так что все Python-ссылки на них — висячие)
    canvas._wire_graph._graph.clear()
    canvas._comp_wire_links.clear()
    canvas._drag_comp_wire_links.clear()
    canvas._junction_split_map.clear()
    canvas._selected_items.clear()
    canvas._drag_items.clear()
    canvas._drag_primary = None
    canvas._drag_primary_label = None
    canvas._drag_wires.clear()
    canvas._drag_group_wires.clear()
    canvas._drag_junctions.clear()
    canvas._drag_endpoint_junctions.clear()
    canvas._pin_drag_ends.clear()
    canvas._pin_drag_orig_points.clear()
    canvas._paste_ghosts.clear()
    canvas._rect_resize_item = None
    canvas._segment_drag_wire = None
    canvas._rubber_item = None
    canvas._rubber_start = None
    canvas._place_ghost = None
    canvas._node_label_ghost = None
    canvas._text_ghost = None
    canvas._directive_ghost = None
    canvas._rect_ghost = None
    canvas._circle_ghost = None
    canvas._routing_preview = None
    canvas._last_segment_item = None
    canvas._wire_hover_pos = None

    def _auto_fill_model(comp: ComponentGraphicsItem):
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
                _flat = _re.sub(r'\+\s*\n\s*', ' ', _c)
                # .model
                for _m in _re.finditer(
                    r'^\s*\.model\s+(\S+)\s+(\S+)\s*(.*)',
                    _flat, _re.MULTILINE | _re.IGNORECASE
                ):
                    if _m.group(1).upper() == _value.upper():
                        comp.set_model_line(f".model {_value} {_m.group(2)} {_m.group(3)}".replace('\n',' ').replace('\r',''))
                        return
                # .subckt
                for _m in _re.finditer(
                    r'^\s*\.subckt\s+(\S+)\s*(.*)',
                    _flat, _re.MULTILINE | _re.IGNORECASE
                ):
                    if _m.group(1).upper() == _value.upper():
                        _start = _m.start()
                        _ends = _re.search(r'^\s*\.ends\s+', _c[_start:], _re.MULTILINE | _re.IGNORECASE)
                        comp.set_model_line(_c[_start:_start + _ends.end() - 1] if _ends else f".SUBCKT {_value} {_m.group(2)}")
                        return

    # Загрузка компонентов
    for cd in data.get("components", []):
        sym_data = sym_data_from_dict(cd["sym_data"])
        comp = ComponentGraphicsItem(
            sym_data,
            refdes=cd.get("refdes", ""),
            value=cd.get("value", ""),
        )
        comp.set_model_line(cd.get("model_line", ""))
        _auto_fill_model(comp)
        comp.setPos(cd["x"], cd["y"])
        comp.setRotation(cd.get("rotation", 0.0))
        # Flip
        flip_x = cd.get("flip_x", False)
        flip_y = cd.get("flip_y", False)
        if flip_x or flip_y:
            t = QTransform()
            if flip_x:
                t = t.scale(-1, 1)
            if flip_y:
                t = t.scale(1, -1)
            comp.setTransform(t)
        scene.addItem(comp)

        # Восстановление меток
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

    # Загрузка проводов
    for wd in data.get("wires", []):
        pts = [QPointF(p[0], p[1]) for p in wd["points"]]
        wire = WireItem(
            pts,
            placed=True,
            show_start_pin=wd.get("show_start_pin", True),
            show_end_pin=wd.get("show_end_pin", True),
        )
        if "color" in wd:
            wire.set_color(wd["color"])
        scene.addItem(wire)
        canvas._wire_graph.add_wire(wire)

    # Загрузка точек соединения
    for jd in data.get("junctions", []):
        j = JunctionItem(QPointF(jd["x"], jd["y"]))
        scene.addItem(j)

    # Загрузка директив
    for dd in data.get("directives", []):
        directive = DirectiveItem(dd["text"], dd["x"], dd["y"])
        scene.addItem(directive)

    # Загрузка меток узлов
    for nd in data.get("node_labels", []):
        anchor = QPointF(nd["anchor_x"], nd["anchor_y"])
        lo = QPointF(nd.get("label_x", 250), nd.get("label_y", 250))
        label = NetLabelItem(nd["text"], anchor, lo)
        scene.addItem(label)

    # Загрузка текстовых элементов
    for td in data.get("texts", []):
        text_item = TextItem(
            td["text"], td["x"], td["y"],
            td.get("font_family", "monospace"),
            td.get("font_size", 80),
        )
        scene.addItem(text_item)

    # Загрузка прямоугольников
    for rd in data.get("rectangles", []):
        r = rd["rect"]
        rect_item = RectangleItem(r[0], r[1], r[2], r[3], rd.get("color"))
        scene.addItem(rect_item)

    # Загрузка окружностей
    for cd in data.get("circles", []):
        r = cd["rect"]
        circle_item = CircleItem(r[0], r[1], r[2], r[3], cd.get("color"))
        scene.addItem(circle_item)

    # Восстановление _comp_wire_links — сканируем все компоненты
    for item in scene.items():
        if isinstance(item, ComponentGraphicsItem):
            canvas._update_comp_wire_connections(item)


def save_sch(canvas, filepath: str):
    data = serialize_canvas(canvas)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_sch(canvas, filepath: str):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    deserialize_into_canvas(canvas, data)
