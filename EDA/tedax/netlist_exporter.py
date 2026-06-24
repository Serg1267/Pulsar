from __future__ import annotations

from EDA.app.items.component_item import ComponentGraphicsItem
from EDA.app.items.node_label_item import NetLabelItem
from EDA.core.router.wire_item import WireItem

# Дефолтные футпринты pcb-rnd по device-типу
_TEDAX_DEFAULT_FP: dict[str, str] = {
    "RESISTOR": "acy(500)",
    "CAPACITOR": "rcy(200)",
    "POLARIZED_CAPACITOR": "rcy(200)",
    "INDUCTOR": "acy(400)",
    "DIODE": "acy(300)",
    "ZENER_DIODE": "acy(300)",
    "LED": "led5",
    "AOP-STANDARD": "dip(8)",
    "NPN_TRANSISTOR": "TO92",
    "PNP_TRANSISTOR": "TO92",
    "NMOS_TRANSISTOR": "TO220",
    "PMOS_TRANSISTOR": "TO220",
    "VDC": "connector(1,2)",
    "VOLTAGE_SOURCE": "connector(1,2)",
    "VSIN": "connector(1,2)",
    "VPULSE": "connector(1,2)",
    "CURRENT_SOURCE": "connector(1,2)",
}

# Алиасы для советских МЛТ резисторов → acy(spacing)
# Расстояние между выводами (мм → mil, округление к стандартному шагу)
_MLT_FP: dict[str, str] = {
    "MLT-0.125": "acy(400)",
    "MLT-0.25": "acy(400)",
    "MLT-0.5": "acy(500)",
    "MLT-1": "acy(600)",
    "MLT-2": "acy(800)",
}

# Конвертация gEDA-имён футпринтов → pcb-rnd параметрические генераторы
_GEDA_TO_TEDAX_FP: dict[str, str] = {
    "ACY500.FP": "acy(500)",
    "ACY300.FP": "acy(300)",
    "ACY400.FP": "acy(400)",

    "RCY100.FP": "rcy(100)",
    "RCY200.FP": "rcy(200)",
    "TO92.FP": "TO92",
    "TO220.FP": "TO220",
    "LED5.FP": "led5",
    "SIP(2)": "connector(1,2)",
    "SIP2.FP": "connector(1,2)",
    "DIP8.FP": "dip(8)",
    "DIP6.FP": "dip(6)",
    "DIP14.FP": "dip(14)",
}


def _tedax_footprint_for(fp_raw: str, dev_name: str) -> str:
    """Выбрать футпринт для pcb-rnd: из атрибута, конвертации gEDA→pcb-rnd, или дефолт по device."""
    if not fp_raw or fp_raw.lower() in ("none", "", "~"):
        return _TEDAX_DEFAULT_FP.get(dev_name, "connector(1,2)")
    key = fp_raw.upper().strip()

    if key in _MLT_FP:
        return _MLT_FP[key]

    if key in _GEDA_TO_TEDAX_FP:
        return _GEDA_TO_TEDAX_FP[key]
    if "(" in fp_raw or not fp_raw.endswith(".fp"):
        return fp_raw
    return fp_raw[:-3]


def export_tedax_netlist(canvas) -> str:
    """Экспорт схемы в tEDAx формат для pcb-rnd."""
    scene = canvas._scene
    wire_graph = canvas._wire_graph
    comp_wire_links = canvas._comp_wire_links

    # 1. Group wires by connectivity
    processed: set[WireItem] = set()
    net_groups: list[set[WireItem]] = []
    for item in scene.items():
        if not isinstance(item, WireItem):
            continue
        if item in processed:
            continue
        connected = wire_graph.get_connected(item)
        processed.update(connected)
        net_groups.append(connected)

    # 2. Index components, separate power symbols
    power_ids: set[int] = set()
    all_comps: dict[int, ComponentGraphicsItem] = {}
    for item in scene.items():
        if isinstance(item, ComponentGraphicsItem):
            cid = id(item)
            all_comps[cid] = item
            if "net" in item._data.attributes:
                power_ids.add(cid)
            elif item._data.attributes.get("device", "").upper() == "GND":
                power_ids.add(cid)

    # 3. Assign net names
    group_to_net: dict[int, str] = {}
    net_num = 0
    for gidx, group in enumerate(net_groups):
        name = None
        for (cid, _pin_idx), (w, _wi, _px, _py) in comp_wire_links.items():
            if cid not in power_ids:
                continue
            if w not in group:
                continue
            comp = all_comps.get(cid)
            if comp is None:
                continue
            net_attr = comp._data.attributes.get("net", "")
            if net_attr:
                label = net_attr.split(":")[0] if ":" in net_attr else net_attr
                name = "GND" if label.upper() == "GND" else label
            elif comp._data.attributes.get("device", "").upper() == "GND":
                name = "GND"
            break

        if name is None:
            for item in scene.items():
                if not isinstance(item, NetLabelItem):
                    continue
                anchor = item.pos()
                for w in group:
                    pts = w.points()
                    for i in range(len(pts) - 1):
                        a, b = pts[i], pts[i + 1]
                        if abs(a.x() - b.x()) < 0.1:
                            if abs(anchor.x() - a.x()) <= 30:
                                if min(a.y(), b.y()) - 30 <= anchor.y() <= max(a.y(), b.y()) + 30:
                                    name = item.text()
                                    break
                        else:
                            if abs(anchor.y() - a.y()) <= 30:
                                if min(a.x(), b.x()) - 30 <= anchor.x() <= max(a.x(), b.x()) + 30:
                                    name = item.text()
                                    break
                    if name is not None:
                        break

        if name is None:
            net_num += 1
            name = f"N${net_num:04d}"
        group_to_net[gidx] = name

    # 4. Build connections
    net_conns: dict[str, list[tuple[str, str]]] = {}
    assigned_pins: set[tuple[int, int]] = set()

    for gidx, group in enumerate(net_groups):
        name = group_to_net[gidx]
        if name not in net_conns:
            net_conns[name] = []
        for (cid, pin_idx), (w, _wi, _px, _py) in comp_wire_links.items():
            if w not in group:
                continue
            comp = all_comps.get(cid)
            if comp is None:
                continue
            if cid in power_ids:
                continue
            refdes = comp.refdes() or ""
            if not refdes:
                continue
            # tEDAx: всегда 1-based индекс пина (pcb-rnd футпринты
            # используют числовые имена падов, а не буквенные C/B/E)
            pin_num = str(pin_idx + 1)
            net_conns[name].append((refdes, pin_num))
            assigned_pins.add((cid, pin_idx))

    # 5. Collect component metadata
    comp_meta: list[tuple[str, str, str, str]] = []
    for cid, comp in all_comps.items():
        if cid in power_ids:
            continue
        refdes = comp.refdes() or ""
        if not refdes:
            continue
        dev = comp._data.attributes.get("device", "") or comp.value() or "unknown"
        val = comp.value() or ""
        fp = comp.footprint()
        if not fp:
            fp_raw = comp._data.attributes.get("footprint", "")
            fp = _tedax_footprint_for(fp_raw, dev.upper())
        comp_meta.append((refdes, fp, dev, val))

    # 6. Write tEDAx
    out: list[str] = []
    out.append("tEDAx v1")
    out.append("begin netlist v1 netlist\n")

    for refdes, fp, dev, val in sorted(comp_meta, key=lambda x: x[0]):
        out.append(f"\tfootprint {refdes} {fp}")
        out.append(f"\tdevice {refdes} {dev}")
        out.append(f"\tvalue {refdes} {val}\n")

    for name in sorted(net_conns):
        for refdes, pin_num in sorted(set(net_conns[name])):
            out.append(f"\tconn {name} {refdes} {pin_num}")

    out.append("\nend netlist")
    return "\n".join(out)
