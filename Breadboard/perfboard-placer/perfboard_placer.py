import json
import os
import re
import tkinter as tk
import tkinter.filedialog as fd
from board import Board
from pcb_library import parse_pcb_svg, DrawCommand


def _tk_color(s: str) -> str:
    m = re.match(r'rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)', s)
    if m:
        return f'#{int(m.group(1)):02x}{int(m.group(2)):02x}{int(m.group(3)):02x}'
    return s

PAD_COLOR = "#e87a20"
SILK_COLOR = "#f0f0f0"
BOARD_COLOR = "#2b4a2b"


def _layer_color(layer: str) -> str:
    if layer.startswith("copper"):
        return PAD_COLOR
    return SILK_COLOR


class PerfboardCanvas:
    def __init__(self, parent, board: Board):
        self.board = board
        self.scale = 7.0
        self.ox = 0.0
        self.oy = 0.0

        self.canvas = tk.Canvas(parent, bg="#c8bc9a", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-3>", self._pan_start)
        self.canvas.bind("<B3-Motion>", self._pan_move)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Button-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)

        self._pan_data = None
        self._placements = []
        self._drag = None
        self._selected = None
        self._selected_route = None
        self._routing_mode = False
        self._routes = []
        self._route_start = None
        self._route_preview = []
        self._crosshair_items = []
        self._placing_pkg = None
        self._ghost_items = []
        self._dirty = False

    def center_on_board(self):
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 2:
            cw = 900
        if ch < 2:
            ch = 700
        bw = self.board.board_width_mm * self.scale
        bh = self.board.board_height_mm * self.scale
        self.ox = (cw - bw) / 2
        self.oy = (ch - bh) / 2

    def _on_wheel(self, event):
        mx, my = event.x, event.y
        bx = (mx - self.ox) / self.scale
        by = (my - self.oy) / self.scale
        factor = 1.1 if event.delta > 0 else 1 / 1.1
        self.scale *= factor
        self.ox = mx - bx * self.scale
        self.oy = my - by * self.scale
        self.redraw()

    def _pan_start(self, event):
        self._pan_data = (event.x, event.y, self.ox, self.oy)

    def _pan_move(self, event):
        if self._pan_data:
            sx, sy, ox0, oy0 = self._pan_data
            self.ox = ox0 + (event.x - sx)
            self.oy = oy0 + (event.y - sy)
            self.redraw()

    def _on_motion(self, event):
        if self._routing_mode:
            self._update_crosshair_and_preview(event)
            return
        if self._placing_pkg is not None:
            self._update_ghost(event)
            return
        items = self.canvas.find_overlapping(event.x - 1, event.y - 1, event.x + 1, event.y + 1)
        over = any(
            any(tag.startswith("comp_") for tag in self.canvas.gettags(i))
            for i in items
        )
        self.canvas.config(cursor="hand2" if over else "")

    def _drag_start(self, event):
        if self._routing_mode:
            self._routing_click(event)
            return
        if self._placing_pkg is not None:
            self._place_component_at(event)
            return
        self._drag = None
        self._selected = None
        self._selected_route = None
        items = self.canvas.find_overlapping(event.x - 2, event.y - 2, event.x + 2, event.y + 2)
        for i in items:
            for tag in self.canvas.gettags(i):
                if tag.startswith("comp_"):
                    idx = int(tag[5:])
                    self._drag = idx
                    self._selected = idx
                    self.redraw()
                    return
        for i in items:
            for tag in self.canvas.gettags(i):
                if tag.startswith("route_"):
                    idx = int(tag.split("_")[1])
                    self._selected_route = idx
                    self.redraw()
                    return
        self.redraw()

    def _drag_move(self, event):
        if self._drag is None:
            return
        pkg, _, _, rot, fh, fv = self._placements[self._drag]
        mmu = pkg.width_mm / pkg.vb[2] if pkg.vb[2] else 1
        copper = [c for c in pkg.commands if c.type == 'circle' and c.layer.startswith('copper')]
        if not copper:
            return
        leftmost = min(copper, key=lambda c: c.params['cx'])
        sx_mm = leftmost.params['cx'] * mmu
        sy_mm = leftmost.params['cy'] * mmu

        mm_x = (event.x - self.ox) / self.scale
        mm_y = (event.y - self.oy) / self.scale

        col = round((mm_x - sx_mm - self.board.margin_x()) / self.board.pitch_mm)
        row = round((mm_y - sy_mm - self.board.margin_y()) / self.board.pitch_mm)
        col = max(0, min(col, self.board.cols - 1))
        row = max(0, min(row, self.board.rows - 1))
        hx, hy = self.board.hole_pos(col, row)
        self._placements[self._drag] = (pkg, hx - sx_mm, hy - sy_mm, rot, fh, fv)
        self._dirty = True
        self.redraw()

    def _drag_end(self, event):
        self._drag = None

    def redraw(self):
        self.canvas.delete("all")
        self._draw_board()
        self._draw_components()
        self._draw_routes()

    def _draw_board(self):
        b = self.board
        s = self.scale
        x0 = self.ox
        y0 = self.oy
        x1 = x0 + b.board_width_mm * s
        y1 = y0 + b.board_height_mm * s

        self.canvas.create_rectangle(x0, y0, x1, y1, fill=BOARD_COLOR, outline="#3a6a3a", width=2)

        for col in range(b.cols):
            for row in range(b.rows):
                hx, hy = b.hole_pos(col, row)
                px = round(x0 + hx * s)
                py = round(y0 + hy * s)
                pr = max(1, round(0.5 * s))
                self.canvas.create_oval(
                    px - pr, py - pr, px + pr, py + pr,
                    fill="#222222", outline="#c9a84c", width=3,
                )

    def _get_ref_xy(self, pkg):
        copper = [c for c in pkg.commands if c.type == 'circle' and c.layer.startswith('copper')]
        if copper:
            leftmost = min(copper, key=lambda c: c.params['cx'])
            return leftmost.params['cx'], leftmost.params['cy']
        return 0, 0

    def _transform_point(self, px, py, ref_x, ref_y, rotation, flip_h, flip_v):
        dx = px - ref_x
        dy = py - ref_y
        if flip_h:
            dx = -dx
        if flip_v:
            dy = -dy
        if rotation == 90:
            dx, dy = -dy, dx
        elif rotation == 180:
            dx, dy = -dx, -dy
        elif rotation == 270:
            dx, dy = dy, -dx
        return ref_x + dx, ref_y + dy

    def _draw_components(self):
        s = self.scale
        ox = self.ox
        oy = self.oy
        for i, (pkg, bx, by, rotation, flip_h, flip_v) in enumerate(self._placements):
            tag = f"comp_{i}"
            mmu = pkg.width_mm / pkg.vb[2] if pkg.vb[2] else 1
            ref_x, ref_y = self._get_ref_xy(pkg)
            if i == self._selected:
                xmin_mm, ymin_mm, xmax_mm, ymax_mm = self._comp_bbox(
                    pkg, bx, by, mmu, ref_x, ref_y, rotation, flip_h, flip_v
                )
                pad = 2
                self.canvas.create_rectangle(
                    ox + (xmin_mm - pad) * s, oy + (ymin_mm - pad) * s,
                    ox + (xmax_mm + pad) * s, oy + (ymax_mm + pad) * s,
                    outline="#ffff00", width=2, dash=(4, 2),
                )
            for cmd in pkg.commands:
                self._draw_command(cmd, s, ox, oy, bx, by, tag, mmu,
                                   rotation, flip_h, flip_v, ref_x, ref_y)

    def _comp_bbox(self, pkg, bx, by, mmu, ref_x, ref_y, rotation, flip_h, flip_v):
        all_x, all_y = [], []
        for cmd in pkg.commands:
            pts = self._cmd_points(cmd)
            for px, py in pts:
                tpx, tpy = self._transform_point(px, py, ref_x, ref_y, rotation, flip_h, flip_v)
                all_x.append(bx + tpx * mmu)
                all_y.append(by + tpy * mmu)
        if all_x:
            return min(all_x), min(all_y), max(all_x), max(all_y)
        return bx, by, bx, by

    def _cmd_points(self, cmd):
        if cmd.type == "circle":
            return [(cmd.params['cx'], cmd.params['cy'])]
        elif cmd.type == "rect":
            x, y, w, h = cmd.params['x'], cmd.params['y'], cmd.params['w'], cmd.params['h']
            return [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
        elif cmd.type == "line":
            return [(cmd.params['x1'], cmd.params['y1']),
                    (cmd.params['x2'], cmd.params['y2'])]
        elif cmd.type == "path":
            return cmd.params['points']
        return []

    def _draw_command(self, cmd, s, ox, oy, bx, by, comp_tag="", mmu=1.0,
                      rotation=0, flip_h=False, flip_v=False, ref_x=0, ref_y=0):
        layer = cmd.layer
        color = _layer_color(layer)
        fill = _tk_color(cmd.params.get("fill", "none"))
        stroke = _tk_color(cmd.params.get("stroke", ""))
        if layer.startswith("copper") or layer == "silkscreen":
            stroke = ""
        sw = cmd.params.get("sw", 0) * mmu * s
        if sw < 1:
            sw = 1

        def to_canvas(px, py):
            tpx, tpy = self._transform_point(px, py, ref_x, ref_y, rotation, flip_h, flip_v)
            return ox + (bx + tpx * mmu) * s, oy + (by + tpy * mmu) * s

        if cmd.type == "circle":
            cx, cy = to_canvas(cmd.params["cx"], cmd.params["cy"])
            cpx = round(cx)
            cpy = round(cy)
            r = round(cmd.params["r"] * mmu * s)
            c = stroke if stroke else color
            self.canvas.create_oval(
                cpx - r, cpy - r, cpx + r, cpy + r,
                outline=c, width=sw, fill="", tags=comp_tag,
            )

        elif cmd.type == "rect":
            if cmd.layer.startswith("copper"):
                pass
            else:
                x1, y1 = to_canvas(cmd.params["x"], cmd.params["y"])
                w = cmd.params["w"] * mmu * s
                h = cmd.params["h"] * mmu * s
                c = stroke if stroke else color
                self.canvas.create_rectangle(
                    x1, y1, x1 + w, y1 + h,
                    outline=c, width=sw, fill="", tags=comp_tag,
                )

        elif cmd.type == "line":
            x1, y1 = to_canvas(cmd.params["x1"], cmd.params["y1"])
            x2, y2 = to_canvas(cmd.params["x2"], cmd.params["y2"])
            c = stroke if stroke else color
            self.canvas.create_line(
                x1, y1, x2, y2, fill=c, width=sw, tags=comp_tag,
            )

        elif cmd.type == "path":
            pts = cmd.params["points"]
            coords = []
            for px, py in pts:
                cx_, cy_ = to_canvas(px, py)
                coords.append(cx_)
                coords.append(cy_)
            c = stroke if stroke else color
            if fill and fill != "none" and cmd.params.get("closed"):
                self.canvas.create_polygon(
                    coords, outline=c, fill=fill, width=sw, tags=comp_tag,
                )
            else:
                self.canvas.create_line(
                    coords, fill=c, width=sw, tags=comp_tag,
                )

    def place_component(self, pkg, board_x_mm, board_y_mm,
                        rotation=0, flip_h=False, flip_v=False):
        self._placements.append((pkg, board_x_mm, board_y_mm, rotation, flip_h, flip_v))
        self._dirty = True
        self.redraw()

    def rotate_selected(self):
        if self._selected is None:
            return
        pkg, bx, by, rot, fh, fv = self._placements[self._selected]
        self._placements[self._selected] = (pkg, bx, by, (rot + 90) % 360, fh, fv)
        self._dirty = True
        self.redraw()

    def flip_h_selected(self):
        if self._selected is None:
            return
        pkg, bx, by, rot, fh, fv = self._placements[self._selected]
        self._placements[self._selected] = (pkg, bx, by, rot, not fh, fv)
        self._dirty = True
        self.redraw()

    def flip_v_selected(self):
        if self._selected is None:
            return
        pkg, bx, by, rot, fh, fv = self._placements[self._selected]
        self._placements[self._selected] = (pkg, bx, by, rot, fh, not fv)
        self._dirty = True
        self.redraw()

    def delete_selected(self):
        if self._selected is not None:
            self._placements.pop(self._selected)
            self._selected = None
            self._dirty = True
            self.redraw()
        elif self._selected_route is not None:
            self._routes.pop(self._selected_route)
            self._selected_route = None
            self._dirty = True
            self.redraw()

    def _snap_event_to_hole_mm(self, event):
        mm_x = (event.x - self.ox) / self.scale
        mm_y = (event.y - self.oy) / self.scale
        col = round((mm_x - self.board.margin_x()) / self.board.pitch_mm)
        row = round((mm_y - self.board.margin_y()) / self.board.pitch_mm)
        col = max(0, min(col, self.board.cols - 1))
        row = max(0, min(row, self.board.rows - 1))
        return self.board.hole_pos(col, row)

    def toggle_routing(self):
        self._cancel_placement()
        self._routing_mode = not self._routing_mode
        self._route_start = None
        self._route_preview = []
        self._crosshair_items = []
        self._selected_route = None
        if self._routing_mode:
            rx = self.canvas.winfo_rootx()
            ry = self.canvas.winfo_rooty()
            mx = self.canvas.winfo_pointerx() - rx
            my = self.canvas.winfo_pointery() - ry
            self._update_crosshair_and_preview(type('e', (), {'x': mx, 'y': my})())
        else:
            self.redraw()

    def cancel_modes(self):
        self.routing_cancel()
        self._cancel_placement()

    def routing_cancel(self):
        self._routing_mode = False
        self._route_start = None
        if self._routes and len(self._routes[-1]) == 1:
            self._routes.pop()
            self._dirty = True
        self._route_preview = []
        self._crosshair_items = []
        self._selected_route = None
        self.redraw()

    def _clear_temp_items(self):
        for item in self._route_preview + self._crosshair_items:
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        self._route_preview = []
        self._crosshair_items = []

    def _update_crosshair_and_preview(self, event):
        for item in self._crosshair_items:
            self.canvas.delete(item)
        self._crosshair_items = []
        hx_mm, hy_mm = self._snap_event_to_hole_mm(event)
        cx = self.ox + hx_mm * self.scale
        cy = self.oy + hy_mm * self.scale
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        h = self.canvas.create_line(0, cy, cw, cy,
                                    fill="#ffffff", dash=(8, 3, 2, 3), width=1)
        v = self.canvas.create_line(cx, 0, cx, ch,
                                    fill="#ffffff", dash=(8, 3, 2, 3), width=1)
        self._crosshair_items = [h, v]

        for item in self._route_preview:
            self.canvas.delete(item)
        self._route_preview = []

        if self._route_start is None:
            return
        sx, sy = self._route_start
        points = [(sx, sy), (hx_mm, sy), (hx_mm, hy_mm)]
        items = []
        for i in range(len(points) - 1):
            x1 = self.ox + points[i][0] * self.scale
            y1 = self.oy + points[i][1] * self.scale
            x2 = self.ox + points[i+1][0] * self.scale
            y2 = self.oy + points[i+1][1] * self.scale
            item = self.canvas.create_line(
                x1, y1, x2, y2, fill="#ffffff", dash=(4, 4), width=2,
            )
            items.append(item)
        self._route_preview = items

    def _routing_click(self, event):
        hx, hy = self._snap_event_to_hole_mm(event)
        if self._route_start is None:
            self._route_start = (hx, hy)
            self._routes.append([(hx, hy)])
        else:
            sx, sy = self._route_start
            self._routes[-1].extend([(hx, sy), (hx, hy)])
            self._route_start = (hx, hy)
        self._dirty = True
        self._route_preview = []
        self._crosshair_items = []
        self.redraw()

    def _draw_routes(self):
        for ri, route in enumerate(self._routes):
            tag = f"route_{ri}"
            for i in range(len(route) - 1):
                x1 = self.ox + route[i][0] * self.scale
                y1 = self.oy + route[i][1] * self.scale
                x2 = self.ox + route[i+1][0] * self.scale
                y2 = self.oy + route[i+1][1] * self.scale
                if ri == self._selected_route:
                    self.canvas.create_line(
                        x1, y1, x2, y2, fill="#ffff00", width=8,
                        capstyle=tk.ROUND, joinstyle=tk.ROUND, tags=tag,
                    )
                self.canvas.create_line(
                    x1, y1, x2, y2, fill=PAD_COLOR, width=6,
                    capstyle=tk.ROUND, joinstyle=tk.ROUND, tags=tag,
                )

    def _clear_ghost(self):
        for item in self._ghost_items:
            try:
                self.canvas.delete(item)
            except Exception:
                pass
        self._ghost_items = []

    def _cancel_placement(self):
        self._clear_ghost()
        self._placing_pkg = None
        self.redraw()

    def _update_ghost(self, event):
        self._clear_ghost()
        pkg = self._placing_pkg
        if pkg is None:
            return
        s = self.scale
        ox = self.ox
        oy = self.oy
        mmu = pkg.width_mm / pkg.vb[2] if pkg.vb[2] else 1
        ref_x, ref_y = self._get_ref_xy(pkg)

        mm_x = (event.x - ox) / s
        mm_y = (event.y - oy) / s
        col = round((mm_x - self.board.margin_x()) / self.board.pitch_mm)
        row = round((mm_y - self.board.margin_y()) / self.board.pitch_mm)
        col = max(0, min(col, self.board.cols - 1))
        row = max(0, min(row, self.board.rows - 1))
        hx, hy = self.board.hole_pos(col, row)
        bx = hx - ref_x * mmu
        by = hy - ref_y * mmu

        for cmd in pkg.commands:
            self._draw_command(cmd, s, ox, oy, bx, by, "ghost", mmu,
                               0, False, False, ref_x, ref_y)
        self._ghost_items = self.canvas.find_withtag("ghost")

    def _clear_ghost(self):
        self.canvas.delete("ghost")
        self._ghost_items = []

    def _place_component_at(self, event):
        pkg = self._placing_pkg
        if pkg is None:
            return
        mmu = pkg.width_mm / pkg.vb[2] if pkg.vb[2] else 1
        ref_x, ref_y = self._get_ref_xy(pkg)
        mm_x = (event.x - self.ox) / self.scale
        mm_y = (event.y - self.oy) / self.scale
        col = round((mm_x - self.board.margin_x()) / self.board.pitch_mm)
        row = round((mm_y - self.board.margin_y()) / self.board.pitch_mm)
        col = max(0, min(col, self.board.cols - 1))
        row = max(0, min(row, self.board.rows - 1))
        hx, hy = self.board.hole_pos(col, row)
        bx = hx - ref_x * mmu
        by = hy - ref_y * mmu
        self._placements.append((pkg, bx, by, 0, False, False))
        self._dirty = True
        self._clear_ghost()
        self._placing_pkg = None
        self._selected = len(self._placements) - 1
        self.redraw()


PCBS_DIR = os.path.join(os.path.dirname(__file__), "..", "pcb")

LABEL_MAP = {
    "sparkfun-discretesemi_to-92-ammo_pcb.svg": "TO92_5mm",
    "sparkfun-discretesemi_diode-1n4001_pcb.svg": "Diode_1N4001_10mm",
    "DO-41_diode_2_300mil_pcb.svg": "DO41_300mil_8mm",
    "axial_lay_2_800mil_pcb.svg": "Res_20mm",
    "axial_lay_2_600mil_pcb.svg": "Res_15mm",
    "axial_lay_2_500mil_pcb.svg": "Res_13mm",
    "axial_lay_2_400mil_pcb.svg": "Res_10mm",
    "axial_lay_2_300mil_pcb.svg": "Res_8mm",
    "axial_lay_2_200mil_pcb.svg": "Res_5mm",
    "TO-220.svg": "TO220_5mm",
}


class MainWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Perfboard Placer")
        self.root.geometry("900x700")

        self.board = Board()

        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Файл", menu=file_menu)
        file_menu.add_command(label="Открыть", command=self._open_brd)
        file_menu.add_separator()
        file_menu.add_command(label="Сохранить", command=self._save_brd)
        file_menu.add_command(label="Сохранить как...", command=self._save_as_brd)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self._on_exit)

        self._brd_path = None

        self.root.protocol("WM_DELETE_WINDOW", self._on_exit)

        comp_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Компонент", menu=comp_menu)

        self._comp_list = []
        self._file_by_pkg = []
        for fname, label in LABEL_MAP.items():
            pkg = parse_pcb_svg(os.path.join(PCBS_DIR, fname))
            self._comp_list.append((label, pkg))
            self._file_by_pkg.append((pkg, fname))
            comp_menu.add_command(label=label,
                                  command=lambda p=pkg: self._start_placement(p))

        main_frame = tk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True)

        canvas_frame = tk.Frame(main_frame)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.pcanvas = PerfboardCanvas(canvas_frame, self.board)

        self.root.bind('<Key-r>', lambda e: self.pcanvas.rotate_selected())
        self.root.bind('<Key-h>', lambda e: self.pcanvas.flip_h_selected())
        self.root.bind('<Key-v>', lambda e: self.pcanvas.flip_v_selected())
        self.root.bind('<Key-R>', lambda e: self.pcanvas.rotate_selected())
        self.root.bind('<Key-H>', lambda e: self.pcanvas.flip_h_selected())
        self.root.bind('<Key-V>', lambda e: self.pcanvas.flip_v_selected())
        self.root.bind('<Delete>', lambda e: self.pcanvas.delete_selected())
        self.root.bind('<BackSpace>', lambda e: self.pcanvas.delete_selected())
        self.root.bind('<Key-n>', lambda e: self.pcanvas.toggle_routing())
        self.root.bind('<Key-N>', lambda e: self.pcanvas.toggle_routing())
        self.root.bind('<Escape>', lambda e: self.pcanvas.cancel_modes())

        self.root.after(1, self._on_startup)

    def _snap_to_grid(self, pkg, col, row):
        mm_per_unit = pkg.width_mm / pkg.vb[2] if pkg.vb[2] else 1
        copper = [c for c in pkg.commands if c.type == 'circle' and c.layer.startswith('copper')]
        sx_mm = sy_mm = 0.0
        if copper:
            leftmost = min(copper, key=lambda c: c.params['cx'])
            sx_mm = leftmost.params['cx'] * mm_per_unit
            sy_mm = leftmost.params['cy'] * mm_per_unit
        col = max(0, min(col, self.board.cols - 1))
        row = max(0, min(row, self.board.rows - 1))
        hx, hy = self.board.hole_pos(col, row)
        return hx - sx_mm, hy - sy_mm

    def _on_startup(self):
        self.pcanvas.center_on_board()
        self.pcanvas.redraw()

    def _start_placement(self, pkg):
        self.pcanvas._cancel_placement()
        self.pcanvas._placing_pkg = pkg

    def _save_brd(self):
        path = self._brd_path
        if not path:
            self._save_as_brd()
            return
        data = self._serialize()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self.pcanvas._dirty = False

    def _save_as_brd(self):
        path = fd.asksaveasfilename(defaultextension=".brd", filetypes=[("Breadboard", "*.brd")])
        if not path:
            return
        self._brd_path = path
        data = self._serialize()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self.pcanvas._dirty = False

    def _check_dirty(self):
        if not self.pcanvas._dirty:
            return True
        r = tk.messagebox.askyesnocancel("Breadboard", "Сохранить изменения?")
        if r is None:
            return False
        if r:
            self._save_brd()
        return True

    def _open_brd(self):
        if not self._check_dirty():
            return
        path = fd.askopenfilename(filetypes=[("Breadboard", "*.brd")])
        if not path:
            return
        with open(path) as f:
            data = json.load(f)
        self._brd_path = path
        self._deserialize(data)
        self.pcanvas._dirty = False

    def _on_exit(self):
        if self._check_dirty():
            self.root.destroy()

    def _serialize(self):
        pc = self.pcanvas
        placements = []
        for pkg, bx, by, rot, fh, fv in pc._placements:
            fname = None
            for pp, ff in self._file_by_pkg:
                if pp is pkg:
                    fname = ff
                    break
            if fname is None:
                continue
            placements.append({
                "file": fname, "bx": bx, "by": by,
                "rotation": rot, "flip_h": fh, "flip_v": fv,
            })
        return {"placements": placements, "routes": pc._routes}

    def _deserialize(self, data):
        pc = self.pcanvas
        pc._placements = []
        pc._routes = []
        for p in data.get("placements", []):
            fname = p["file"]
            for pp, ff in self._file_by_pkg:
                if ff == fname:
                    pc._placements.append((
                        pp, p["bx"], p["by"],
                        p.get("rotation", 0),
                        p.get("flip_h", False),
                        p.get("flip_v", False),
                    ))
                    break
        pc._routes = data.get("routes", [])
        pc._selected = None
        pc._selected_route = None
        pc.redraw()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    MainWindow().run()
