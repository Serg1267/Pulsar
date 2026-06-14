"""Цвета элементов схемы — переключаются между тёмной и светлой темой."""

YELLOW = "#ffcc00"
GREEN = "#00aa00"
WHITE = "#ffffff"
RED = "#ff0000"

_BODY_LINE_WIDTH = 2.0
_LEAD_LINE_WIDTH = 1.5

_is_light = False


def set_light_theme(light: bool):
    global _is_light
    _is_light = light


def is_light_theme() -> bool:
    return _is_light


def lead_color() -> str:
    return "#000000" if _is_light else "#ffffff"


def body_color() -> str:
    return GREEN  # всегда зелёный


def junction_color() -> str:
    return "#800080" if _is_light else "#ffcc00"


BODY_LINE_WIDTH = _BODY_LINE_WIDTH
LEAD_LINE_WIDTH = _LEAD_LINE_WIDTH
