"""Parse Fritzing PCB SVG files into Package/DrawCommand."""

from __future__ import annotations
import math
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from PySide6.QtGui import QColor


@dataclass
class DrawCommand:
    type: str   # "circle", "rect", "line", "path"
    params: dict = field(default_factory=dict)
    layer: str = ""


@dataclass
class Package:
    name: str
    width_mm: float = 0.0
    height_mm: float = 0.0
    commands: list[DrawCommand] = field(default_factory=list)
    vb: tuple[float, float, float, float] = (0, 0, 1, 1)


def _parse_inch(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("in"):
        return float(s[:-2])
    if s.endswith("mm"):
        return float(s[:-2]) / 25.4
    if s.endswith("mil"):
        return float(s[:-3]) / 1000.0
    if s.endswith("px"):
        return float(s[:-2]) / 96.0
    if s.endswith("pt"):
        return float(s[:-2]) / 72.0
    return float(s) / 96.0  # no suffix → px


def _parse_px(s: str) -> float:
    """Return numeric value in CSS pixels (1px = 1/96 in)."""
    s = s.strip().lower()
    if s.endswith("in"):
        return float(s[:-2]) * 96.0
    if s.endswith("mm"):
        return float(s[:-2]) / 25.4 * 96.0
    if s.endswith("mil"):
        return float(s[:-3]) / 1000.0 * 96.0
    if s.endswith("px"):
        return float(s[:-2])
    if s.endswith("pt"):
        return float(s[:-2]) * 96.0 / 72.0
    return float(s)  # no suffix → px


def _bezier_segments(x0, y0, x1, y1, x2, y2, x3, y3, n=16):
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = mt**3 * x0 + 3 * mt**2 * t * x1 + 3 * mt * t**2 * x2 + t**3 * x3
        y = mt**3 * y0 + 3 * mt**2 * t * y1 + 3 * mt * t**2 * y2 + t**3 * y3
        pts.append((x, y))
    return pts


def _arc_segments(x0, y0, rx, ry, x_rot, large, sweep, x1, y1, n=24):
    # SVG arc to cubic beziers via SVG 1.1 spec
    rx = abs(rx)
    ry = abs(ry)
    if rx == 0 or ry == 0:
        return [(x0, y0), (x1, y1)]

    x_rot_rad = math.radians(x_rot)
    cos_r = math.cos(x_rot_rad)
    sin_r = math.sin(x_rot_rad)

    dx = (x0 - x1) / 2
    dy = (y0 - y1) / 2
    x1p = cos_r * dx + sin_r * dy
    y1p = -sin_r * dx + cos_r * dy

    # Ensure radii are large enough
    lam = (x1p**2) / (rx**2) + (y1p**2) / (ry**2)
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)

    # Center point calculation
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    if den == 0:
        return [(x0, y0), (x1, y1)]
    s = 1 if large != sweep else -1
    factor = s * math.sqrt(max(0, num / den))
    cxp = factor * rx * y1p / ry
    cyp = factor * -ry * x1p / rx

    # Center in original coordinates
    cx = cos_r * cxp - sin_r * cyp + (x0 + x1) / 2
    cy = sin_r * cxp + cos_r * cyp + (y0 + y1) / 2

    # Start/end angles
    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        det = ux * vy - uy * vx
        return math.atan2(det, dot)

    start_angle = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    end_angle = angle((x1p - cxp) / rx, (y1p - cyp) / ry,
                      (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if sweep == 0 and end_angle > 0:
        end_angle -= 2 * math.pi
    elif sweep == 1 and end_angle < 0:
        end_angle += 2 * math.pi

    n_steps = n
    pts = []
    for i in range(n_steps + 1):
        t = start_angle + end_angle * i / n_steps
        x = cx + rx * math.cos(x_rot_rad) * math.cos(t) - ry * math.sin(x_rot_rad) * math.sin(t)
        y = cy + rx * math.sin(x_rot_rad) * math.cos(t) + ry * math.cos(x_rot_rad) * math.sin(t)
        pts.append((x, y))
    return pts


def _path_to_polylines(d: str) -> list[list[tuple[float, float]]]:
    # Tokenize
    tokens = re.findall(r'[MmLlCcSsAaZz]|[-\d.eE+]+', d)
    polylines = []
    current = [0.0, 0.0]
    sub_start = [0.0, 0.0]
    i = 0
    cp = None  # control point for S/s
    while i < len(tokens):
        cmd = tokens[i]
        i += 1
        if cmd == 'M':
            x, y = float(tokens[i]), float(tokens[i+1]); i += 2
            current = [x, y]
            sub_start = [x, y]
            cp = None
        elif cmd == 'm':
            x = current[0] + float(tokens[i]); y = current[1] + float(tokens[i+1]); i += 2
            current = [x, y]
            sub_start = [x, y]
            cp = None
        elif cmd == 'L':
            x, y = float(tokens[i]), float(tokens[i+1]); i += 2
            if not polylines or polylines[-1][-1] != tuple(current):
                polylines.append([tuple(current)])
            polylines[-1].append((x, y))
            current = [x, y]
            cp = None
        elif cmd == 'l':
            x = current[0] + float(tokens[i]); y = current[1] + float(tokens[i+1]); i += 2
            if not polylines or polylines[-1][-1] != tuple(current):
                polylines.append([tuple(current)])
            polylines[-1].append((x, y))
            current = [x, y]
            cp = None
        elif cmd == 'C':
            x1, y1 = float(tokens[i]), float(tokens[i+1]); i += 2
            x2, y2 = float(tokens[i]), float(tokens[i+1]); i += 2
            x, y = float(tokens[i]), float(tokens[i+1]); i += 2
            pts = _bezier_segments(current[0], current[1], x1, y1, x2, y2, x, y)
            if not polylines or polylines[-1][-1] != tuple(current):
                polylines.append([tuple(current)])
            polylines[-1].extend((p[0], p[1]) for p in pts[1:])
            current = [x, y]
            cp = (x2, y2)
        elif cmd == 'c':
            x1 = current[0] + float(tokens[i]); y1 = current[1] + float(tokens[i+1]); i += 2
            x2 = current[0] + float(tokens[i]); y2 = current[1] + float(tokens[i+1]); i += 2
            x = current[0] + float(tokens[i]); y = current[1] + float(tokens[i+1]); i += 2
            pts = _bezier_segments(current[0], current[1], x1, y1, x2, y2, x, y)
            if not polylines or polylines[-1][-1] != tuple(current):
                polylines.append([tuple(current)])
            polylines[-1].extend((p[0], p[1]) for p in pts[1:])
            current = [x, y]
            cp = (x2, y2)
        elif cmd == 'S':
            if cp:
                x1 = 2 * current[0] - cp[0]; y1 = 2 * current[1] - cp[1]
            else:
                x1 = current[0]; y1 = current[1]
            x2, y2 = float(tokens[i]), float(tokens[i+1]); i += 2
            x, y = float(tokens[i]), float(tokens[i+1]); i += 2
            pts = _bezier_segments(current[0], current[1], x1, y1, x2, y2, x, y)
            if not polylines or polylines[-1][-1] != tuple(current):
                polylines.append([tuple(current)])
            polylines[-1].extend((p[0], p[1]) for p in pts[1:])
            current = [x, y]
            cp = (x2, y2)
        elif cmd == 's':
            if cp:
                x1 = 2 * current[0] - cp[0]; y1 = 2 * current[1] - cp[1]
            else:
                x1 = current[0]; y1 = current[1]
            x2 = current[0] + float(tokens[i]); y2 = current[1] + float(tokens[i+1]); i += 2
            x = current[0] + float(tokens[i]); y = current[1] + float(tokens[i+1]); i += 2
            pts = _bezier_segments(current[0], current[1], x1, y1, x2, y2, x, y)
            if not polylines or polylines[-1][-1] != tuple(current):
                polylines.append([tuple(current)])
            polylines[-1].extend((p[0], p[1]) for p in pts[1:])
            current = [x, y]
            cp = (x2, y2)
        elif cmd == 'A':
            rx, ry = float(tokens[i]), float(tokens[i+1]); i += 2
            x_rot = float(tokens[i]); i += 1
            large = int(tokens[i]); i += 1
            sweep = int(tokens[i]); i += 1
            x, y = float(tokens[i]), float(tokens[i+1]); i += 2
            pts = _arc_segments(current[0], current[1], rx, ry, x_rot, large, sweep, x, y)
            if not polylines or polylines[-1][-1] != tuple(current):
                polylines.append([tuple(current)])
            polylines[-1].extend(pts[1:])
            current = [x, y]
            cp = None
        elif cmd == 'a':
            rx, ry = float(tokens[i]), float(tokens[i+1]); i += 2
            x_rot = float(tokens[i]); i += 1
            large = int(tokens[i]); i += 1
            sweep = int(tokens[i]); i += 1
            x = current[0] + float(tokens[i]); y = current[1] + float(tokens[i+1]); i += 2
            pts = _arc_segments(current[0], current[1], rx, ry, x_rot, large, sweep, x, y)
            if not polylines or polylines[-1][-1] != tuple(current):
                polylines.append([tuple(current)])
            polylines[-1].extend(pts[1:])
            current = [x, y]
            cp = None
        elif cmd == 'Z' or cmd == 'z':
            if polylines and polylines[-1]:
                polylines[-1].append(tuple(sub_start))
            current = list(sub_start)
            cp = None
    return polylines


def _apply_transform(x: float, y: float, tf: tuple[float, ...]) -> tuple[float, float]:
    return (x * tf[0] + y * tf[2] + tf[4],
            x * tf[1] + y * tf[3] + tf[5])


def _style_value(el, attr: str) -> str:
    v = el.get(attr, "")
    if not v:
        style = el.get("style", "")
        if style:
            m = re.search(rf'(?:^|;)\s*{attr}\s*:\s*([^;]+)', style)
            if m:
                v = m.group(1).strip()
    return v


def _parse_element(el, parent_layer: str = "", tf: tuple = (1, 0, 0, 1, 0, 0)) -> list[DrawCommand]:
    tag = el.tag.split('}')[-1] if '}' in el.tag else el.tag
    commands = []

    # Parse transform
    tf_str = el.get('transform', '')
    if tf_str:
        m = re.search(r'translate\(([^)]+)\)', tf_str)
        if m:
            parts = m.group(1).split(',')
            tx = float(parts[0].strip())
            ty = float(parts[1].strip()) if len(parts) > 1 else 0
            ntf = (1, 0, 0, 1, tx, ty)
            # Compose transforms
            tf = (ntf[0]*tf[0] + ntf[2]*tf[1],
                  ntf[1]*tf[0] + ntf[3]*tf[1],
                  ntf[0]*tf[2] + ntf[2]*tf[3],
                  ntf[1]*tf[2] + ntf[3]*tf[3],
                  ntf[0]*tf[4] + ntf[2]*tf[5] + ntf[4],
                  ntf[1]*tf[4] + ntf[3]*tf[5] + ntf[5])

    if tag == 'g':
        glayer = el.get('id', parent_layer) or parent_layer
        for child in el:
            commands.extend(_parse_element(child, glayer, tf))
        return commands

    fill = _style_value(el, 'fill')
    stroke = _style_value(el, 'stroke')
    sw_str = _style_value(el, 'stroke-width')
    sw = float(sw_str) if sw_str else 0

    if tag == 'circle':
        cx = float(el.get('cx', 0))
        cy = float(el.get('cy', 0))
        r = float(el.get('r', 0))
        cx, cy = _apply_transform(cx, cy, tf)
        commands.append(DrawCommand('circle', {
            'cx': cx, 'cy': cy, 'r': r,
            'stroke': stroke, 'fill': fill, 'sw': sw,
        }, parent_layer))

    elif tag == 'rect':
        x = float(el.get('x', 0))
        y = float(el.get('y', 0))
        w = float(el.get('width', 0))
        h = float(el.get('height', 0))
        x, y = _apply_transform(x, y, tf)
        commands.append(DrawCommand('rect', {
            'x': x, 'y': y, 'w': w, 'h': h,
            'stroke': stroke, 'fill': fill, 'sw': sw,
        }, parent_layer))

    elif tag == 'line':
        x1 = float(el.get('x1', 0)); y1 = float(el.get('y1', 0))
        x2 = float(el.get('x2', 0)); y2 = float(el.get('y2', 0))
        x1, y1 = _apply_transform(x1, y1, tf)
        x2, y2 = _apply_transform(x2, y2, tf)
        commands.append(DrawCommand('line', {
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'stroke': stroke, 'sw': sw,
        }, parent_layer))

    elif tag == 'polyline':
        pts_str = el.get('points', '')
        if pts_str:
            pts = []
            for pair in pts_str.split():
                if ',' in pair:
                    x, y = pair.split(',')
                    pts.append((float(x), float(y)))
            if tf != (1, 0, 0, 1, 0, 0):
                pts = [_apply_transform(px, py, tf) for px, py in pts]
            if pts:
                commands.append(DrawCommand('path', {
                    'points': pts, 'closed': False,
                    'stroke': stroke, 'fill': fill, 'sw': sw,
                }, parent_layer))

    elif tag == 'path':
        d = el.get('d', '')
        if not d:
            return commands
        polylines = _path_to_polylines(d)
        for pts in polylines:
            if len(pts) < 2:
                continue
            # Check if there's a non-identity transform
            if tf != (1, 0, 0, 1, 0, 0):
                pts = [_apply_transform(px, py, tf) for px, py in pts]
            closed = len(pts) > 1 and pts[0] == pts[-1]
            commands.append(DrawCommand('path', {
                'points': pts, 'closed': closed,
                'stroke': stroke, 'fill': fill, 'sw': sw,
            }, parent_layer))

    return commands


def parse_pcb_svg(filepath: str) -> Package:
    tree = ET.parse(filepath)
    root = tree.getroot()

    width_str = root.get('width', '0in')
    height_str = root.get('height', '0in')
    viewbox_str = root.get('viewBox', '')

    viewbox_str = root.get('viewBox', '')
    has_viewbox = bool(viewbox_str)

    if viewbox_str:
        vb = list(map(float, viewbox_str.split()))
    else:
        # No viewBox → infer from width/height (in px)
        w_px = _parse_px(width_str)
        h_px = _parse_px(height_str)
        vb = [0.0, 0.0, w_px, h_px]

    # Adobe Illustrator SVGs (enable-background attr) label px but mean pt
    eb = root.get('enable-background', '')
    if eb and width_str.strip().lower().endswith('px') and has_viewbox:
        # Treat px as pt: pt → in = /72, but _parse_inch would /96 → override
        width_inch = float(width_str.lower().replace('px', '').strip()) / 72.0
    else:
        width_inch = _parse_inch(width_str)
    vb_x, vb_y, vb_w, vb_h = vb

    mm_per_unit = (width_inch * 25.4) / vb_w if vb_w else 1

    name = os.path.splitext(os.path.basename(filepath))[0]

    commands = []
    for child in root:
        commands.extend(_parse_element(child))

    w_mm = vb_w * mm_per_unit
    h_mm = vb_h * mm_per_unit

    return Package(name=name, width_mm=w_mm, height_mm=h_mm,
                   commands=commands, vb=(vb_x, vb_y, vb_w, vb_h))
