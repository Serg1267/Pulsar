# Pulsar

SPICE-редактор схем и симулятор на Python + PySide6 + ngspice.

## Возможности

- **Редактор принципиальных схем** — .sym компоненты, Manhattan-трассировка, сетка 100 mil
- **SPICE текстовый редактор** — подсветка синтаксиса, 4+ цветовых схемы, номера строк
- **Симуляция ngspice** — TRAN, DC, AC, OP анализ. Batch-режим, фоновый поток
- **Плоттер** — matplotlib-графики в стиле осциллографа, MultiCursor на всех subplot
- **Библиотека компонентов** — Passive, Diodes, BJT, MOSFET, JFET, Sources, Opamp, Connector и др.
- **Экспорт** — SPICE netlist (.cir), tEDAx (pcb-rnd), PDF, PNG

## Требования

- Python 3.10+
- PySide6, matplotlib, numpy (`pip install -r requirements.txt`)
- ngspice (`apt install ngspice`)

## Быстрый старт

```bash
git clone https://github.com/Serg1267/Pulsar.git
cd Pulsar
pip install -r requirements.txt
python3 main.py
```

## Использование

| Действие | Горячая клавиша |
|----------|----------------|
| Новый .sch | Ctrl+N |
| Новый .cir | Ctrl+Shift+N |
| Сохранить | Ctrl+S |
| Запуск симуляции | F5 |
| Добавить компонент | Ctrl+K |
| Режим проводов | N |
| Добавить текст | T |
| Добавить метку узла | L |
| Добавить директиву | . |
| Поворот влево | E+R |

## Структура проекта

| Директория | Назначение |
|------------|-----------|
| `EDA/` | Редактор схем (ядро, компоненты, сериализация, роутер) |
| `editor/` | SPICE текстовый редактор, подсветка |
| `simulator/` | ngspice симулятор, валидатор netlist |
| `plotter/` | matplotlib-графики |
| `ui/` | Вкладки, боковая панель, диалоги |
| `utils/` | SPICE-шаблоны |
| `Mod/` | SPICE-модели (.lib) |
| `resources/` | Иконки, темы |
| `examples/` | Примеры .cir файлов |

## Лицензия

MIT
