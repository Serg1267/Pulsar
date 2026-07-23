import os
import re
import math
import xml.etree.ElementTree as ET

from dataclasses import dataclass


@dataclass
class DrawCommand:
    type: str
    params: dict
    layer: str = ''


@dataclass
class Package:
    name: str
    width_mm: float
    height_mm: float
    commands: list[DrawCommand]
    vb: tuple[float, float, float, float] = (0, 0, 1, 1)


def clip_point(x: float, y: float, vb_x: float, vb_y: float, vb_w: float, vb_h: float) -> tuple[float, float]:
    return max(vb_x, min(x, vb_x + vb_w)), max(vb_y, min(y, vb_y + vb_h))


def _parse_inch(s: str) -> float:
    s = s.strip()
    if s.endswith('in'):
        return float(s[:-2])
    if s.endswith('mm'):
        return float(s[:-2]) / 25.4
    return float(s)


def _apply_transform(x: float, y: float, tf: tuple) -> tuple[float, float]:
    return (
        x * tf[0] + y * tf[2] + tf[4],
        x * tf[1] + y * tf[3] + tf[5],
    )


def _bezier_segments(
    x0, y0, x1, y1, x2, y2, x3, y3, n=16
) -> list[tuple[float, float]]:
    pts = []
    for i in range(n + 1):
        t = i / n
        mt = 1 - t
        x = mt * mt * mt * x0 + 3 * mt * mt * t * x1 + 3 * mt * t * t * x2 + t * t * t * x3
        y = mt * mt * mt * y0 + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t * t * t * y3
        pts.append((x, y))
    return pts


def _arc_segments(
    x0, y0, rx, ry, x_rot, large, sweep, x1, y1, n=24
) -> list[tuple[float, float]]:
    if rx == 0 or ry == 0:
        return [(x1, y1)]
    x_rot_rad = math.radians(x_rot)
    cos = math.cos(x_rot_rad)
    sin = math.sin(x_rot_rad)
    x1p = cos * (x0 - x1) / 2 + sin * (y0 - y1) / 2
    y1p = -sin * (x0 - x1) / 2 + cos * (y0 - y1) / 2
    rx, ry = abs(rx), abs(ry)
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        rx *= math.sqrt(lam)
        ry *= math.sqrt(lam)
    s = 1 if large != sweep else -1
    sq = (
        rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    ) / (rx * rx * y1p * y1p + ry * ry * x1p * x1p)
    sq = max(0, sq)
    coef = s * math.sqrt(sq)
    cxp = coef * rx * y1p / ry
    cyp = coef * -ry * x1p / rx
    cx = cos * cxp - sin * cyp + (x0 + x1) / 2
    cy = sin * cxp + cos * cyp + (y0 + y1) / 2

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        cross = ux * vy - uy * vx
        return math.atan2(cross, dot)

    sa = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    da = angle(
        (x1p - cxp) / rx, (y1p - cyp) / ry,
        (-x1p - cxp) / rx, (-y1p - cyp) / ry,
    )
    if sweep == 0 and da > 0:
        da -= 2 * math.pi
    elif sweep == 1 and da < 0:
        da += 2 * math.pi
    pts = []
    for i in range(n + 1):
        t = sa + da * i / n
        pts.append((cx + rx * math.cos(t) * cos - ry * math.sin(t) * sin,
                     cy + rx * math.cos(t) * sin + ry * math.sin(t) * cos))
    return pts


def _path_to_polylines(d: str) -> list[list[tuple[float, float]]]:
    d = d.strip()
    d = re.sub(r'([MLCSAZmlcsaz])\s*', r' \1 ', d)
    d = re.sub(r',', ' ', d)
    tokens = d.split()
    cmds = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in 'MLCSAZmlcsaz':
            cmd = t
            i += 1
            if cmd in 'Zz':
                cmds.append((cmd, []))
            elif cmd in 'Aa':
                n = 7
                if i + n - 1 < len(tokens):
                    try:
                        params = [float(tokens[i + j]) for j in range(n)]
                        cmds.append((cmd, params))
                        i += n
                    except ValueError:
                        i += 1
                else:
                    break
            else:
                params = []
                while i < len(tokens):
                    try:
                        params.append(float(tokens[i]))
                        i += 1
                    except ValueError:
                        break
                if params:
                    cmds.append((cmd, params))
        else:
            i += 1

    subpaths = []
    current = []
    cx = cy = 0.0
    sx = sy = 0.0
    lcx = lcy = 0.0
    prev = ''

    def add(x, y):
        nonlocal cx, cy
        cx, cy = x, y
        current.append((x, y))

    for cmd, params in cmds:
        if cmd == 'M':
            if current:
                subpaths.append(current)
            current = []
            if len(params) >= 2:
                add(params[0], params[1])
                sx, sy = cx, cy
                for j in range(2, len(params), 2):
                    if j + 1 < len(params):
                        add(params[j], params[j + 1])
            prev = 'M'
        elif cmd == 'm':
            if current:
                subpaths.append(current)
            current = []
            if len(params) >= 2:
                add(cx + params[0], cy + params[1])
                sx, sy = cx, cy
                for j in range(2, len(params), 2):
                    if j + 1 < len(params):
                        add(cx + params[j], cy + params[j + 1])
            prev = 'm'
        elif cmd == 'L':
            for j in range(0, len(params), 2):
                if j + 1 < len(params):
                    add(params[j], params[j + 1])
            prev = 'L'
        elif cmd == 'l':
            for j in range(0, len(params), 2):
                if j + 1 < len(params):
                    add(cx + params[j], cy + params[j + 1])
            prev = 'l'
        elif cmd == 'C':
            for j in range(0, len(params), 6):
                if j + 5 < len(params):
                    pts = _bezier_segments(cx, cy, params[j], params[j + 1],
                                           params[j + 2], params[j + 3],
                                           params[j + 4], params[j + 5])
                    for p in pts[1:]:
                        current.append(p)
                    cx, cy = params[j + 4], params[j + 5]
                    lcx, lcy = params[j + 2], params[j + 3]
            prev = 'C'
        elif cmd == 'c':
            for j in range(0, len(params), 6):
                if j + 5 < len(params):
                    x1 = cx + params[j]
                    y1 = cy + params[j + 1]
                    x2 = cx + params[j + 2]
                    y2 = cy + params[j + 3]
                    x3 = cx + params[j + 4]
                    y3 = cy + params[j + 5]
                    pts = _bezier_segments(cx, cy, x1, y1, x2, y2, x3, y3)
                    for p in pts[1:]:
                        current.append(p)
                    cx, cy = x3, y3
                    lcx, lcy = x2, y2
            prev = 'c'
        elif cmd == 'S':
            for j in range(0, len(params), 4):
                if j + 3 < len(params):
                    x2, y2 = params[j], params[j + 1]
                    x3, y3 = params[j + 2], params[j + 3]
                    if prev in ('C', 'c', 'S', 's'):
                        x1, y1 = 2 * cx - lcx, 2 * cy - lcy
                    else:
                        x1, y1 = cx, cy
                    pts = _bezier_segments(cx, cy, x1, y1, x2, y2, x3, y3)
                    for p in pts[1:]:
                        current.append(p)
                    cx, cy = x3, y3
                    lcx, lcy = x2, y2
            prev = 'S'
        elif cmd == 's':
            for j in range(0, len(params), 4):
                if j + 3 < len(params):
                    x2 = cx + params[j]
                    y2 = cy + params[j + 1]
                    x3 = cx + params[j + 2]
                    y3 = cy + params[j + 3]
                    if prev in ('C', 'c', 'S', 's'):
                        x1, y1 = 2 * cx - lcx, 2 * cy - lcy
                    else:
                        x1, y1 = cx, cy
                    pts = _bezier_segments(cx, cy, x1, y1, x2, y2, x3, y3)
                    for p in pts[1:]:
                        current.append(p)
                    cx, cy = x3, y3
                    lcx, lcy = x2, y2
            prev = 's'
        elif cmd == 'A':
            for j in range(0, len(params), 7):
                if j + 6 < len(params):
                    rx, ry, x_rot, large, sweep = params[j:j + 5]
                    x, y = params[j + 5], params[j + 6]
                    pts = _arc_segments(cx, cy, rx, ry, x_rot, large, sweep, x, y)
                    for p in pts[1:]:
                        current.append(p)
                    cx, cy = x, y
            prev = 'A'
        elif cmd == 'Z' or cmd == 'z':
            if current and (current[0][0] != cx or current[0][1] != cy):
                add(sx, sy)
            prev = 'Z'

    if current:
        subpaths.append(current)
    return subpaths


def _style_value(el, attr: str) -> str:
    v = el.get(attr, '').strip()
    if not v:
        style = el.get('style', '')
        m = re.search(rf'{attr}:([^;]+)', style)
        if m:
            v = m.group(1).strip()
    return v


def _parse_element(el, parent_layer='', tf=(1, 0, 0, 1, 0, 0)) -> list[DrawCommand]:
    tag = el.tag.split('}')[-1] if '}' in el.tag else el.tag
    commands = []

    et = el.get('transform', '')
    if et:
        m = re.match(r'translate\(([-\d.]+)(?:[,\s]+([-\d.]+))?\)', et)
        if m:
            tx = float(m.group(1))
            ty = float(m.group(2)) if m.group(2) else 0
            ntf = (tf[0], tf[1], tf[2], tf[3],
                   tf[0] * tx + tf[2] * ty + tf[4],
                   tf[1] * tx + tf[3] * ty + tf[5])
        else:
            ntf = tf
    else:
        ntf = tf

    if tag == 'g':
        glayer = el.get('id', parent_layer) or parent_layer
        for child in el:
            commands.extend(_parse_element(child, glayer, ntf))
        return commands

    layer = parent_layer

    fill = _style_value(el, 'fill') or 'none'
    stroke = _style_value(el, 'stroke') or ''
    sw_str = el.get('stroke-width', '') or ''
    sw = float(sw_str) if sw_str else 0
    if not sw_str:
        style = el.get('style', '')
        m = re.search(r'stroke-width:\s*([\d.]+)', style)
        if m:
            sw = float(m.group(1))

    if tag == 'circle':
        cx, cy = _apply_transform(float(el.get('cx', 0)), float(el.get('cy', 0)), ntf)
        r = float(el.get('r', 0))
        commands.append(DrawCommand('circle', {
            'cx': cx, 'cy': cy, 'r': r,
            'stroke': stroke, 'fill': fill, 'sw': sw,
        }, layer))

    elif tag == 'rect':
        x, y = _apply_transform(float(el.get('x', 0)), float(el.get('y', 0)), ntf)
        w = float(el.get('width', 0))
        h = float(el.get('height', 0))
        commands.append(DrawCommand('rect', {
            'x': x, 'y': y, 'w': w, 'h': h,
            'stroke': stroke, 'fill': fill, 'sw': sw,
        }, layer))

    elif tag == 'line':
        x1, y1 = _apply_transform(float(el.get('x1', 0)), float(el.get('y1', 0)), ntf)
        x2, y2 = _apply_transform(float(el.get('x2', 0)), float(el.get('y2', 0)), ntf)
        commands.append(DrawCommand('line', {
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
            'stroke': stroke, 'sw': sw,
        }, layer))

    elif tag == 'path':
        d = el.get('d', '')
        if d:
            polylines = _path_to_polylines(d)
            for pts in polylines:
                if not pts:
                    continue
                if ntf != (1, 0, 0, 1, 0, 0):
                    pts = [_apply_transform(px, py, ntf) for px, py in pts]
                closed = len(pts) > 1 and pts[0] == pts[-1]
                commands.append(DrawCommand('path', {
                    'points': pts, 'closed': closed,
                    'stroke': stroke, 'fill': fill, 'sw': sw,
                }, layer))

    return commands


def parse_pcb_svg(filepath: str) -> Package:
    tree = ET.parse(filepath)
    root = tree.getroot()

    width_str = root.get('width', '0in')
    viewbox_str = root.get('viewBox', '0 0 1 1')

    width_inch = _parse_inch(width_str)
    vb = list(map(float, viewbox_str.split()))
    vb_x, vb_y, vb_w, vb_h = vb

    mm_per_unit = (width_inch * 25.4) / vb_w

    name = os.path.splitext(os.path.basename(filepath))[0]

    commands = []
    for child in root:
        commands.extend(_parse_element(child))

    w_mm = vb_w * mm_per_unit
    h_mm = vb_h * mm_per_unit

    return Package(name=name, width_mm=w_mm, height_mm=h_mm, commands=commands, vb=(vb_x, vb_y, vb_w, vb_h))
