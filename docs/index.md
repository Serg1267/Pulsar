# Pulsar

**Редактор принципиальных схем и SPICE-симулятор**

Pulsar — бесплатный инструмент для создания электрических схем и запуска SPICE-симуляций через ngspice.

## Особенности

- 🖥️ Визуальный редактор схем с drag-drop компонентами
- ⚡ Batch-симуляция (TRAN, DC, AC, OP) через ngspice
- 📊 Осциллографический плоттер (matplotlib)
- 📄 Экспорт в .cir, PDF, PNG
- 🎨 Светлая и тёмная темы
- 🔧 Библиотека компонентов: R, C, L, D, Q, ОУ, источники

## Быстрый старт

1. [Установка](quick-start/01-install.md)
2. [Первая схема](quick-start/02-first-schematic.md)
3. [Первая симуляция](quick-start/03-first-simulation.md)
4. [Просмотр графика](quick-start/04-view-graph.md)
5. [Экспорт](quick-start/05-export.md)

## Системные требования

- Python 3.10+
- PySide6 (Qt6)
- ngspice (`apt install ngspice`)
- matplotlib, numpy
