from __future__ import annotations
import math, re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple


@dataclass
class SymPin:
    x1: float; y1: float; x2: float; y2: float
    pinnumber: str = ""; pinlabel: str = ""; pintype: str = ""

@dataclass
class SymLine: x1: float; y1: float; x2: float; y2: float
@dataclass
class SymBox: x: float; y: float; width: float; height: float
@dataclass
class SymCircle: x: float; y: float; radius: float; fill: bool = False
@dataclass
class SymText: x: float; y: float; content: str; visible: bool = True

@dataclass
class SymArc:
    x: float; y: float; radius: float
    start_angle: float = 0.0   # градусы, CCW от 3 часов (gEDA convention)
    sweep_angle: float = 360.0 # градусы, положительный = CCW

@dataclass
class SymData:
    pins: List[SymPin] = field(default_factory=list)
    lines: List[SymLine] = field(default_factory=list)
    boxes: List[SymBox] = field(default_factory=list)
    circles: List[SymCircle] = field(default_factory=list)
    polygons: List[List[Tuple[float, float]]] = field(default_factory=list)
    arcs: List[SymArc] = field(default_factory=list)
    texts: List[SymText] = field(default_factory=list)
    attributes: Dict[str, str] = field(default_factory=dict)
    bounding_box: Optional[Tuple[float, float, float, float]] = None
    version: str = "v"
    device: str = ""
    default_value: str = ""


class SymParser:
    def parse_file(self, filepath: str) -> SymData:
        data = SymData()
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception as e:
            print(f"Не удалось открыть {filepath}: {e}")
            return data

        current_poly = None
        in_pin_block = False

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            if not line or line.startswith('#'):
                continue
            if line == '{':
                continue
            if line == '}':
                in_pin_block = False
                continue

            parts = line.split()
            if not parts:
                continue
            token = parts[0]

            try:
                if token == 'P' and len(parts) >= 5:
                    in_pin_block = True
                    x1, y1, x2, y2 = map(float, parts[1:5])
                    data.pins.append(SymPin(x1=x1, y1=y1, x2=x2, y2=y2))
                elif token == 'T' and len(parts) >= 10:
                    x = float(parts[1])
                    y = float(parts[2])
                    visibility = int(parts[5])
                    num_lines = max(1, int(parts[9]))
                    content_parts = []
                    for _ in range(num_lines):
                        if i < len(lines):
                            cl = lines[i].strip()
                            i += 1
                            if cl and not cl.startswith('#') and cl != '{' and cl != '}':
                                content_parts.append(cl)
                    content = '\n'.join(content_parts)
                    if in_pin_block and data.pins and content.strip():
                        attr_match = re.search(r'(\w+)\s*=\s*(.*)', content)
                        if attr_match:
                            key = attr_match.group(1).lower()
                            val = attr_match.group(2).strip('" ')
                            p = data.pins[-1]
                            if key == 'pinnumber':
                                p.pinnumber = val
                            elif key == 'pinlabel':
                                p.pinlabel = val
                            elif key == 'pintype':
                                p.pintype = val
                    elif not in_pin_block and content:
                        data.texts.append(SymText(x=x, y=y, content=content, visible=visibility != 0))
                        attr_match = re.search(r'([\w-]+)\s*=\s*(.*)', content)
                        if attr_match:
                            key, val = attr_match.group(1).lower(), attr_match.group(2).strip('" ')
                            data.attributes[key] = val
                            if key == "device":
                                data.device = val
                            elif key == "value":
                                data.default_value = val
                elif token == 'M' and len(parts) >= 2 and ',' in parts[1]:
                    x, y = map(float, parts[1].split(','))
                    current_poly = [(x, y)]
                    data.polygons.append(current_poly)
                elif token == 'L' and len(parts) == 2 and ',' in parts[1] and current_poly is not None:
                    x, y = map(float, parts[1].split(','))
                    current_poly.append((x, y))
                elif token == 'L' and len(parts) >= 5:
                    x1, y1, x2, y2 = map(float, parts[1:5])
                    data.lines.append(SymLine(x1=x1, y1=y1, x2=x2, y2=y2))
                    current_poly = None
                elif token == 'z':
                    current_poly = None
                elif token == 'B' and len(parts) >= 5:
                    x, y, w, h = map(float, parts[1:5])
                    data.boxes.append(SymBox(x=x, y=y, width=w, height=h))
                elif token == 'C' and len(parts) >= 4:
                    x, y, r = map(float, parts[1:4])
                    data.circles.append(SymCircle(x=x, y=y, radius=r, fill=False))
                elif token == 'V' and len(parts) >= 4:
                    x, y, r = float(parts[1]), float(parts[2]), float(parts[3])
                    _fill = len(parts) > 7 and parts[7] in ('1',)
                    data.circles.append(SymCircle(x=x, y=y, radius=r, fill=_fill))
                elif token == 'A' and len(parts) >= 6:
                    x, y, r = float(parts[1]), float(parts[2]), float(parts[3])
                    start, sweep = float(parts[4]), float(parts[5])
                    data.arcs.append(SymArc(x=x, y=y, radius=r,
                                            start_angle=start, sweep_angle=sweep))
                elif token == 'A':
                    self._parse_attribute(data, line)
                elif token == 'D' and len(parts) >= 2:
                    data.device = parts[1]
            except Exception:
                pass

        self._compute_bounding_box(data)
        self._normalize_pins(data)
        return data

    def _parse_attribute(self, data: SymData, line: str):
        match = re.search(r'([\w-]+)\s*=\s*"?([^"]*)"?', line)
        if match:
            key, val = match.group(1).lower(), match.group(2)
            data.attributes[key] = val
            if key == "device": data.device = val
            if key == "value": data.default_value = val
            return
        match = re.search(r'A\s+(-?[\d.]+)\s+(-?[\d.]+)\s+.*?"(.+)"', line)
        if match:
            x, y, content = float(match.group(1)), float(match.group(2)), match.group(3)
            data.texts.append(SymText(x=x, y=y, content=content))
            for p in data.pins:
                dist = math.hypot(p.x2 - x, p.y2 - y)
                if dist < 20:
                    k = content.lower()
                    if "pinnumber" in k: p.pinnumber = content.split("=")[-1].strip()
                    elif "pinlabel" in k: p.pinlabel = content.split("=")[-1].strip()
                    elif p.pinlabel == "" and content not in ["", "unknown", "refdes", "value"]:
                        p.pinlabel = content

    def _normalize_pins(self, data: SymData):
        bb = data.bounding_box
        if bb is None:
            return
        cx = (bb[0] + bb[2]) / 2.0
        cy = (bb[1] + bb[3]) / 2.0
        for p in data.pins:
            d1 = math.hypot(p.x1 - cx, p.y1 - cy)
            d2 = math.hypot(p.x2 - cx, p.y2 - cy)
            if d2 > d1:
                p.x1, p.x2 = p.x2, p.x1
                p.y1, p.y2 = p.y2, p.y1
            if p.pintype == 'pwr' and p.y1 > p.y2:
                ref_y = p.y2
                for l in data.lines:
                    l.y1 = 2*ref_y - l.y1
                    l.y2 = 2*ref_y - l.y2
                for b in data.boxes:
                    b.y = 2*ref_y - b.y
                for c in data.circles:
                    c.y = 2*ref_y - c.y
                for a in data.arcs:
                    a.y = 2*ref_y - a.y
                for poly in data.polygons:
                    poly[:] = [(x, 2*ref_y - y) for x, y in poly]
                p.y1 = 2*ref_y - p.y1

    @staticmethod
    def _arc_bbox(cx: float, cy: float, r: float,
                  start_deg: float, sweep_deg: float) -> tuple[float, float, float, float]:
        """Bounding box сегмента дуги (gEDA: 0°=3ч, CCW+)."""
        if abs(sweep_deg) >= 360:
            return cx - r, cy - r, cx + r, cy + r
        s = math.radians(start_deg)
        e = math.radians(start_deg + sweep_deg)
        xs = [cx + r * math.cos(s), cx + r * math.cos(e)]
        ys = [cy + r * math.sin(s), cy + r * math.sin(e)]
        if sweep_deg >= 0:
            s_norm = s % (2 * math.pi)
            sweep_rad = math.radians(sweep_deg)
            for k in range(4):
                ca = k * math.pi / 2
                if s_norm <= ca <= s_norm + sweep_rad:
                    xs.append(cx + r * math.cos(ca))
                    ys.append(cy + r * math.sin(ca))
                elif s_norm + sweep_rad > 2 * math.pi:
                    ca_w = ca + 2 * math.pi
                    if ca_w <= s_norm + sweep_rad:
                        xs.append(cx + r * math.cos(ca))
                        ys.append(cy + r * math.sin(ca))
        else:
            s_norm = s % (2 * math.pi)
            sweep_abs = math.radians(-sweep_deg)
            for k in range(4):
                ca = k * math.pi / 2
                if s_norm - sweep_abs <= ca <= s_norm:
                    xs.append(cx + r * math.cos(ca))
                    ys.append(cy + r * math.sin(ca))
                elif s_norm - sweep_abs < 0:
                    ca_w = ca - 2 * math.pi
                    if s_norm - sweep_abs <= ca_w <= s_norm:
                        xs.append(cx + r * math.cos(ca))
                        ys.append(cy + r * math.sin(ca))
        return min(xs), min(ys), max(xs), max(ys)

    def _compute_bounding_box(self, data: SymData):
        min_x, min_y = float('inf'), float('inf')
        max_x, max_y = float('-inf'), float('-inf')
        found = False
        for l in data.lines:
            min_x=min(min_x,l.x1,l.x2); min_y=min(min_y,l.y1,l.y2)
            max_x=max(max_x,l.x1,l.x2); max_y=max(max_y,l.y1,l.y2); found=True
        for b in data.boxes:
            min_x=min(min_x,b.x); min_y=min(min_y,b.y)
            max_x=max(max_x,b.x+b.width); max_y=max(max_y,b.y+b.height); found=True
        for c in data.circles:
            min_x=min(min_x,c.x-c.radius); min_y=min(min_y,c.y-c.radius)
            max_x=max(max_x,c.x+c.radius); max_y=max(max_y,c.y+c.radius); found=True
        for a in data.arcs:
            ax, ay, ax2, ay2 = self._arc_bbox(a.x, a.y, a.radius, a.start_angle, a.sweep_angle)
            min_x = min(min_x, ax, ax2); min_y = min(min_y, ay, ay2)
            max_x = max(max_x, ax, ax2); max_y = max(max_y, ay, ay2); found = True
        for p in data.pins:
            min_x=min(min_x,p.x1,p.x2); min_y=min(min_y,p.y1,p.y2)
            max_x=max(max_x,p.x1,p.x2); max_y=max(max_y,p.y1,p.y2); found=True
        for poly in data.polygons:
            for x, y in poly:
                min_x=min(min_x,x); min_y=min(min_y,y)
                max_x=max(max_x,x); max_y=max(max_y,y); found=True
        if found: data.bounding_box = (min_x, min_y, max_x, max_y)
        else: data.bounding_box = (0, 0, 1000, 1000)
