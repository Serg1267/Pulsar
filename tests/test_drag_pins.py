"""
Тест: пины (красные квадраты) остаются скрытыми
для соединённых элементов во время перетаскивания компонента.

Проверяет:
1. _connected_pins заполняется при соединении провода с пином
2. После setup drag (аналог mousePressEvent) + _update_live_pins
   _connected_pins НЕ очищается — пины остаются скрытыми
3. Пин на конце провода скрывается при соединении
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF

from EDA.core.parser.sym_parser import SymData, SymPin
from EDA.app.canvas import SchematicCanvas
from EDA.core.router.wire_item import WireItem


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app


class TestDragPinHiding:
    """Проверка скрытия пинов при перетаскивании."""

    @staticmethod
    def _make_sym_two_pins() -> SymData:
        """SymData с двумя пинами, центр в (0,0)."""
        data = SymData()
        data.bounding_box = (-300, -100, 300, 100)
        data.pins = [
            SymPin(x1=-300, y1=0, x2=-400, y2=0, pinnumber="1"),
            SymPin(x1=300, y1=0, x2=400, y2=0, pinnumber="2"),
        ]
        return data

    def test_connected_pins_persist_after_drag_setup(self, qapp):
        canvas = SchematicCanvas()
        sym = self._make_sym_two_pins()
        item = canvas.place_component(sym, 0, 0, refdes="R1")

        # Пин 0 в scene-координатах:
        #   _p(-300, 0) = (-300 - 0, -(0 - 0)) = (-300, 0)
        #   mapToScene((-300, 0)) = (-300, 0) при позиции (0,0)
        pin0_scene = item.mapToScene(item._p(-300, 0))

        # Создаём провод, конец которого совпадает с пином 0
        wire = WireItem([QPointF(-300, 0), QPointF(500, 0)], placed=True)
        canvas._scene.addItem(wire)
        canvas._wire_graph.add_wire(wire)
        canvas._update_comp_wire_connections(item)

        # --- проверка 1: провод подключён ---
        assert 0 in item._connected_pins, "Пин 0 должен быть в connected_pins"
        assert len(item._connected_pins) == 1, \
            f"Только пин 0, получено {item._connected_pins}"

        # --- проверка 2: пин на конце провода скрыт ---
        assert not wire._show_start_pin, \
            "Пин на конце провода должен быть скрыт (соединён с компонентом)"
        assert wire._show_end_pin, \
            "Пин на дальнем конце должен быть виден (не соединён)"

        # --- симуляция mousePressEvent для drag ---
        item.set_selected(True)
        canvas._selected_items = [item]
        canvas._drag_items = [item]
        canvas._drag_primary = item
        canvas._drag_comp_wire_links.clear()

        for key, (w, w_idx, px, py) in list(canvas._comp_wire_links.items()):
            comp_id, pin_idx = key
            if comp_id == id(item):
                if w not in canvas._drag_group_wires:
                    canvas._drag_comp_wire_links.append((w, w_idx))
                    canvas._wire_graph.remove_wire(w)
                    w.set_show_start_pin(True)
                    w.set_show_end_pin(True)

        # --- вызов _update_live_pins (ключевая часть фикса) ---
        for drag_item in canvas._drag_items:
            canvas._update_live_pins(drag_item)

        # --- проверка 3: connected_pins сохранился ---
        assert 0 in item._connected_pins, \
            f"После drag setup connected_pins должен содержать пин 0, получено {item._connected_pins}"

    def test_unconnected_pin_not_in_connected_pins(self, qapp):
        """Пин без провода не добавляется в connected_pins."""
        canvas = SchematicCanvas()
        sym = self._make_sym_two_pins()
        item = canvas.place_component(sym, 0, 0, refdes="R2")
        assert len(item._connected_pins) == 0, \
            "Без проводов connected_pins должен быть пуст"
