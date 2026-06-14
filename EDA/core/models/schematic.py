from __future__ import annotations
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any


class PinDirection(Enum):
    PASSIVE = "passive"
    INPUT = "input"
    OUTPUT = "output"
    BIDIR = "bidir"
    TRISTATE = "tristate"


@dataclass
class Point:
    x: float
    y: float


@dataclass
class Pin:
    id: str
    name: str
    component_id: str
    rel_x: float
    rel_y: float
    direction: PinDirection = PinDirection.PASSIVE
    net: Optional[str] = None


@dataclass
class Junction:
    id: str
    x: float
    y: float


@dataclass
class Component:
    id: str
    refdes: str
    part_type: str
    pins: List[Pin]
    x: float
    y: float
    rotation: float = 0.0
    properties: Dict[str, Any] = field(default_factory=dict)

    def get_absolute_pin_pos(self, pin: Pin) -> Point:
        rad = math.radians(self.rotation)
        rx = pin.rel_x * math.cos(rad) - pin.rel_y * math.sin(rad)
        ry = pin.rel_x * math.sin(rad) + pin.rel_y * math.cos(rad)
        return Point(self.x + rx, self.y + ry)


@dataclass
class Wire:
    id: str
    segments: list
    connected_pins: set
    net_id: Optional[str] = None


@dataclass
class Net:
    id: str
    name: str
    pins: set
    wires: set
    is_auto_named: bool = False
