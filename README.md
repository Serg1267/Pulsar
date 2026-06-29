# Pulsar

![Pulsar v0.9.0](screenshots/Pulsar%20v0.9.0.png)

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

MIT License

Copyright (c) 2026 Pulsar

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.


