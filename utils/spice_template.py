"""
Генератор шаблонов SPICE netlist с секционированными комментариями.

Стандартная структура SPICE-файла:
1. Заголовок и описание схемы
2. Определения компонентов
3. Модели компонентов (.MODEL)
4. Директивы анализа (.TRAN, .AC, .DC, .OP)
5. Директивы вывода (.PRINT, .PLOT, .PROBE)
6. Конец файла (.END)
"""

import re
from datetime import datetime
from pathlib import Path


def _parse_raw_netlist(raw_netlist: str) -> dict:
    """Разобрать "сырой" netlist от lepton-netlist на компоненты и модели.

    Args:
        raw_netlist: Текст netlist от lepton-netlist

    Returns:
        dict с ключами:
            - components: list строк компонентов (R, C, L, V, I, D, Q...)
            - models: list строк .MODEL
            - directives: list строк директив анализа (.TRAN, .AC, .DC, .OP)
            - output: list строк вывода (.PRINT, .PROBE)
            - title: строка заголовка (первая строка если есть)
            - other: list остальных строк
    """
    components = []
    models = []
    directives = []
    output = []
    subcircuits = []
    other = []
    title = ""
    in_subckt = False

    lines = raw_netlist.strip().split("\n")
    
    for line in lines:
        stripped = line.strip()
        
        # Пропустить пустые строки и чистые комментарии
        if not stripped or stripped.startswith("*"):
            if in_subckt:
                subcircuits.append(line)
            else:
                if stripped and not stripped.startswith("* lepton") and not stripped.startswith("* Created"):
                    other.append(line)
            continue
        
        # Пропустить .END
        if stripped.upper() == ".end":
            if in_subckt:
                subcircuits.append(line)
                in_subckt = False
            continue
        
        # .SUBCKT / .ENDS
        if stripped.upper().startswith(".SUBCKT"):
            subcircuits.append(line)
            in_subckt = True
            continue
        if stripped.upper().startswith(".ENDS"):
            subcircuits.append(line)
            in_subckt = False
            continue
        if in_subckt:
            subcircuits.append(line)
            continue
        
        # Первая строка может быть заголовком
        if not title and not stripped.startswith(".") and not stripped[0].isalpha():
            title = stripped
            continue
        
        # Модели
        if stripped.upper().startswith(".MODEL"):
            models.append(line)
            continue
        
        # Директивы анализа
        if stripped.upper().startswith((".TRAN ", ".AC ", ".DC ", ".OP",
                                       ".IC ", ".NODESET ", ".STEP ")):
            directives.append(line)
            continue
        
        # Директивы вывода
        if stripped.upper().startswith((".PRINT ", ".PROBE ", ".PLOT ")):
            output.append(line)
            continue
        
        # Компоненты (начинаются с буквы refdes)
        if stripped and stripped[0].isalpha():
            components.append(line)
            continue
        
        # Остальное
        other.append(line)

    return {
        "components": components,
        "models": models,
        "directives": directives,
        "output": output,
        "subcircuits": subcircuits,
        "title": title,
        "other": other,
    }


def wrap_netlist_in_template(
    raw_netlist: str,
    circuit_name: str = "",
    description: str = "",
    author: str = "",
    extra_directives: list[str] | None = None,
) -> str:
    """Обернуть "сырой" netlist от lepton-netlist в шаблон с секциями.

    Использует тот же формат что и create_cir_template(), но вставляет
    реальные компоненты/модели из netlist вместо примеров-заглушек.

    Args:
        raw_netlist: Текст netlist от lepton-netlist
        circuit_name: Название схемы (по умолчанию: из netlist или имя файла)
        description: Описание схемы
        author: Автор
        extra_directives: Дополнительные SPICE-директивы из .sch файла
                          (lepton-netlist их игнорирует)

    Returns:
        Строка с netlist в шаблоне с секциями
    """
    # Разобрать netlist
    parsed = _parse_raw_netlist(raw_netlist)

    # Добавить директивы из .sch файла (lepton-netlist их игнорирует)
    if extra_directives:
        for directive in extra_directives:
            d_stripped = directive.strip()
            d_upper = d_stripped.upper()
            # Проверка на дубликаты — сравниваем stripped+upper версии
            existing_directives = [d.strip().upper() for d in parsed["directives"]]
            existing_output = [d.strip().upper() for d in parsed["output"]]
            existing_other = [d.strip().upper() for d in parsed["other"]]

            if d_upper.startswith((".TRAN ", ".AC ", ".DC ", ".OP")):
                if d_upper not in existing_directives:
                    parsed["directives"].append(d_stripped)
            elif d_upper.startswith((".PRINT ", ".PROBE ", ".PLOT ")):
                if d_upper not in existing_output:
                    parsed["output"].append(d_stripped)
            else:
                # Неизвестная директива — в "другие"
                if d_upper not in existing_other:
                    parsed["other"].append(d_stripped)
    
    # Если название не указано — использовать из netlist или default
    if not circuit_name:
        circuit_name = parsed["title"] or "Untitled Circuit"
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    separator = "=" * 66
    separator_dash = "-" * 66
    
    lines = []
    
    # =========================================================================
    # СЕКЦИЯ 1: ЗАГОЛОВОК И ОПИСАНИЕ
    # =========================================================================
    lines.append(f"* {separator}")
    lines.append(f"* {circuit_name}")
    lines.append(f"* {separator}")
    lines.append(f"*")
    lines.append(f"* Дата создания: {timestamp}")
    
    if author:
        lines.append(f"* Автор: {author}")
    
    if description:
        lines.append(f"*")
        lines.append(f"* Описание:")
        words = description.split()
        current_line = "* "
        for word in words:
            if len(current_line) + len(word) + 1 > 70:
                lines.append(current_line)
                current_line = f"* {word}"
            else:
                current_line += f" {word}"
        if current_line.strip() != "*":
            lines.append(current_line)
    
    lines.append(f"*")
    lines.append(f"* {separator}")
    lines.append("")
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 2: ПОДСХЕМЫ (.SUBCKT)
    # =========================================================================
    if parsed["subcircuits"]:
        lines.append(f"* {separator_dash}")
        lines.append("* ПОДСХЕМЫ")
        lines.append(f"* {separator_dash}")
        lines.append("*")
        lines.append("")
        for sub in parsed["subcircuits"]:
            lines.append(sub)
        lines.append("")
        lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 3: КОМПОНЕНТЫ СХЕМЫ
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* КОМПОНЕНТЫ СХЕМЫ")
    lines.append(f"* {separator_dash}")
    lines.append("*")
    lines.append("* Формат: <имя> <узел+> <узел-> <значение>")
    lines.append("*")
    lines.append("* Источники напряжения: Vxxx n+ n- <тип> <параметры>")
    lines.append("* Резисторы:          Rxxx n+ n- <сопротивление>")
    lines.append("* Конденсаторы:       Cxxx n+ n- <ёмкость>")
    lines.append("* Индуктивности:      Lxxx n+ n- <индуктивность>")
    lines.append("* Диоды:              Dxxx anode cathode <модель>")
    lines.append("* Транзисторы:        Qxxx collector base emitter <модель>")
    lines.append("*")
    lines.append("")
    
    # Вставить реальные компоненты из netlist
    if parsed["components"]:
        # Разделить по типам
        voltage_sources = [c for c in parsed["components"] if c.strip() and c.strip()[0] == 'V']
        passive = [c for c in parsed["components"] if c.strip() and c.strip()[0] in ('R', 'C', 'L')]
        semiconductors = [c for c in parsed["components"] if c.strip() and c.strip()[0] in ('D', 'Q', 'M', 'J')]
        other_components = [c for c in parsed["components"] if c.strip() and c.strip()[0] not in ('V', 'R', 'C', 'L', 'D', 'Q', 'M', 'J')]
        
        if voltage_sources:
            lines.append("* --- Источники ---")
            for comp in voltage_sources:
                lines.append(comp)
            lines.append("")
        
        if passive:
            lines.append("* --- Пассивные компоненты ---")
            for comp in passive:
                lines.append(comp)
            lines.append("")
        
        if semiconductors:
            lines.append("* --- Полупроводники ---")
            for comp in semiconductors:
                lines.append(comp)
            lines.append("")
        
        if other_components:
            lines.append("* --- Другие компоненты ---")
            for comp in other_components:
                lines.append(comp)
            lines.append("")
    else:
        lines.append("* --- Источники ---")
        lines.append("* V1 1 0 DC 5")
        lines.append("")
        lines.append("* --- Пассивные компоненты ---")
        lines.append("* R1 1 2 1k")
        lines.append("* C1 2 0 1uF")
        lines.append("* L1 2 3 10mH")
        lines.append("")
        lines.append("* --- Полупроводники ---")
        lines.append("* D1 3 0 1N4148")
        lines.append("* Q1 3 2 0 2N2222")
        lines.append("")
    
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 4: МОДЕЛИ КОМПОНЕНТОВ
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* МОДЕЛИ КОМПОНЕНТОВ")
    lines.append(f"* {separator_dash}")
    lines.append("*")
    lines.append("* Формат: .MODEL <имя> <тип> (<параметры>)")
    lines.append("*")
    lines.append("* Типы моделей:")
    lines.append("*   D   — диод")
    lines.append("*   NPN — биполярный NPN-транзистор")
    lines.append("*   PNP — биполярный PNP-транзистор")
    lines.append("*   NMOS/PMOS — полевые транзисторы")
    lines.append("*")
    lines.append("")
    
    if parsed["models"]:
        for model in parsed["models"]:
            lines.append(model)
        lines.append("")
    else:
        lines.append("* Примеры (закомментируйте или замените своими):")
        lines.append("* .MODEL MyDiode D (IS=2.52n RS=.568 N=1.752)")
        lines.append("* .MODEL MyTransistor NPN (BF=255.9 VAF=74.03)")
        lines.append("")
    
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 5: ДИРЕКТИВЫ АНАЛИЗА
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* ДИРЕКТИВЫ АНАЛИЗА")
    lines.append(f"* {separator_dash}")
    lines.append("*")
    lines.append("* Типы анализов:")
    lines.append("*   .TRAN <шаг> <время_останова> — переходный процесс")
    lines.append("*   .AC <тип> <точек> <f_start> <f_stop> — частотный анализ")
    lines.append("*   .DC <источник> <начало> <конец> <шаг> — развёртка по DC")
    lines.append("*   .OP — рабочая точка")
    lines.append("*")
    lines.append("")
    
    if parsed["directives"]:
        for directive in parsed["directives"]:
            lines.append(directive)
        lines.append("")
    else:
        lines.append("* --- Директива анализа не указана (добавьте вручную) ---")
        lines.append("* .TRAN <шаг_времени> <время_моделирования>")
        lines.append("")
    
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 6: ДИРЕКТИВЫ ВЫВОДА
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* ДИРЕКТИВЫ ВЫВОДА ДАННЫХ")
    lines.append(f"* {separator_dash}")
    lines.append("*")
    lines.append("* Формат: .PRINT <тип_анализа> <переменные>")
    lines.append("*")
    lines.append("* Переменные:")
    lines.append("*   V(<узел>)     — напряжение на узле")
    lines.append("*   V(<узел1> <узел2>) — дифференциальное напряжение")
    lines.append("*   I(<источник>) — ток через источник")
    lines.append("*")
    lines.append("")
    
    if parsed["output"]:
        for out in parsed["output"]:
            lines.append(out)
        lines.append("")
    else:
        lines.append("* --- Вывод напряжений ---")
        lines.append("* .PRINT TRAN V(1) V(2) V(3)")
        lines.append("")
        lines.append("* --- Вывод токов ---")
        lines.append("* .PRINT TRAN I(V1) I(V2)")
        lines.append("")
    
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 7: КОНЕЦ ФАЙЛА
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* КОНЕЦ СХЕМЫ")
    lines.append(f"* {separator_dash}")
    lines.append(".END")
    lines.append("")
    
    return "\n".join(lines)


def create_cir_template(
    circuit_name: str = "Untitled Circuit",
    description: str = "",
    analysis_type: str = "TRAN",
    include_models: bool = False,
    author: str = "",
) -> str:
    """Создать шаблон SPICE netlist с секционированными комментариями.

    Args:
        circuit_name: Название схемы (отображается в заголовке)
        description: Описание схемы (произвольный текст)
        analysis_type: Тип анализа по умолчанию: TRAN, AC, DC, OP
        include_models: Включить секцию моделей с примерами
        author: Автор схемы

    Returns:
        Строка с содержимым шаблона .cir
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    lines = []
    
    # =========================================================================
    # СЕКЦИЯ 1: ЗАГОЛОВОК И ОПИСАНИЕ
    # =========================================================================
    separator = "=" * 66
    lines.append(f"* {separator}")
    lines.append(f"* {circuit_name}")
    lines.append(f"* {separator}")
    lines.append(f"*")
    lines.append(f"* Дата создания: {timestamp}")
    
    if author:
        lines.append(f"* Автор: {author}")
    
    if description:
        lines.append(f"*")
        lines.append(f"* Описание:")
        # Разбить описание на строки по 68 символов
        words = description.split()
        current_line = "* "
        for word in words:
            if len(current_line) + len(word) + 1 > 70:
                lines.append(current_line)
                current_line = f"* {word}"
            else:
                current_line += f" {word}"
        if current_line.strip() != "*":
            lines.append(current_line)
    
    lines.append(f"*")
    lines.append(f"* {separator}")
    lines.append("")
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 2: ОПРЕДЕЛЕНИЯ КОМПОНЕНТОВ
    # =========================================================================
    separator_dash = "-" * 66
    lines.append(f"* {separator_dash}")
    lines.append("* КОМПОНЕНТЫ СХЕМЫ")
    lines.append(f"* {separator_dash}")
    lines.append("*")
    lines.append("* Формат: <имя> <узел+> <узел-> <значение>")
    lines.append("*")
    lines.append("* Источники напряжения: Vxxx n+ n- <тип> <параметры>")
    lines.append("* Резисторы:          Rxxx n+ n- <сопротивление>")
    lines.append("* Конденсаторы:       Cxxx n+ n- <ёмкость>")
    lines.append("* Индуктивности:      Lxxx n+ n- <индуктивность>")
    lines.append("* Диоды:              Dxxx anode cathode <модель>")
    lines.append("* Транзисторы:        Qxxx collector base emitter <модель>")
    lines.append("*")
    lines.append("")
    lines.append("* --- Источники ---")
    lines.append("* V1 1 0 DC 5")
    lines.append("")
    lines.append("* --- Пассивные компоненты ---")
    lines.append("* R1 1 2 1k")
    lines.append("* C1 2 0 1uF")
    lines.append("* L1 2 3 10mH")
    lines.append("")
    lines.append("* --- Полупроводники ---")
    lines.append("* D1 3 0 1N4148")
    lines.append("* Q1 3 2 0 2N2222")
    lines.append("")
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 3: МОДЕЛИ КОМПОНЕНТОВ
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* МОДЕЛИ КОМПОНЕНТОВ")
    lines.append(f"* {separator_dash}")
    lines.append("*")
    lines.append("* Формат: .MODEL <имя> <тип> (<параметры>)")
    lines.append("*")
    lines.append("* Типы моделей:")
    lines.append("*   D   — диод")
    lines.append("*   NPN — биполярный NPN-транзистор")
    lines.append("*   PNP — биполярный PNP-транзистор")
    lines.append("*   NMOS/PMOS — полевые транзисторы")
    lines.append("*")
    lines.append("")
    
    if include_models:
        lines.append("* --- Пример: диод 1N4148 ---")
        lines.append(".MODEL 1N4148 D (IS=2.52n RS=.568 N=1.752 CJO=4p M=.4 TT=20n)")
        lines.append("")
        lines.append("* --- Пример: транзистор 2N2222 ---")
        lines.append(".MODEL 2N2222 NPN (IS=14.34F XTI=3 EG=1.11 VAF=74.03")
        lines.append("+ BF=255.9 IKF=.2847 ISE=1.506F NE=2 BR=6.092 IKR=0 ISC=0")
        lines.append("+ VAR=100 NR=1 RC=1 CJC=7.306P MJC=.3416 VJC=.75 TF=.4157N")
        lines.append("+ XTF=6.037 PTF=0 CJE=22.01P MJE=.377 VJE=.75 TR=46.91N")
        lines.append("+ XCJC=.5767 NC=2)")
        lines.append("")
    else:
        lines.append("* Примеры (закомментируйте или замените своими):")
        lines.append("* .MODEL MyDiode D (IS=2.52n RS=.568 N=1.752)")
        lines.append("* .MODEL MyTransistor NPN (BF=255.9 VAF=74.03)")
        lines.append("")
    
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 4: ДИРЕКТИВЫ АНАЛИЗА
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* ДИРЕКТИВЫ АНАЛИЗА")
    lines.append(f"* {separator_dash}")
    lines.append("*")
    lines.append("* Типы анализов:")
    lines.append("*   .TRAN <шаг> <время_останова> — переходный процесс")
    lines.append("*   .AC <тип> <точек> <f_start> <f_stop> — частотный анализ")
    lines.append("*   .DC <источник> <начало> <конец> <шаг> — развёртка по DC")
    lines.append("*   .OP — рабочая точка")
    lines.append("*")
    lines.append("")
    
    if analysis_type == "TRAN":
        lines.append("* --- Переходный анализ ---")
        lines.append("* .TRAN <шаг_времени> <время_моделирования>")
        lines.append("* .TRAN 0.1ms 10ms")
        lines.append("")
    elif analysis_type == "AC":
        lines.append("* --- Частотный анализ (AC) ---")
        lines.append("* .AC <тип_развёртки> <точек_на_дек> <f_нач> <f_кон>")
        lines.append("* .AC DEC 100 10 1MEG")
        lines.append("")
    elif analysis_type == "DC":
        lines.append("* --- Развёртка по постоянному току (DC) ---")
        lines.append("* .DC <источник> <начало> <конец> <шаг>")
        lines.append("* .DC V1 0 10 0.1")
        lines.append("")
    elif analysis_type == "OP":
        lines.append("* --- Расчёт рабочей точки ---")
        lines.append("* .OP")
        lines.append("")
    
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 5: ДИРЕКТИВЫ ВЫВОДА
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* ДИРЕКТИВЫ ВЫВОДА ДАННЫХ")
    lines.append(f"* {separator_dash}")
    lines.append("*")
    lines.append("* Формат: .PRINT <тип_анализа> <переменные>")
    lines.append("*")
    lines.append("* Переменные:")
    lines.append("*   V(<узел>)     — напряжение на узле")
    lines.append("*   V(<узел1> <узел2>) — дифференциальное напряжение")
    lines.append("*   I(<источник>) — ток через источник")
    lines.append("*")
    lines.append("")
    
    lines.append("* --- Вывод напряжений ---")
    lines.append("* .PRINT TRAN V(1) V(2) V(3)")
    lines.append("")
    lines.append("* --- Вывод токов ---")
    lines.append("* .PRINT TRAN I(V1) I(V2)")
    lines.append("")
    lines.append("")
    
    # =========================================================================
    # СЕКЦИЯ 6: КОНЕЦ ФАЙЛА
    # =========================================================================
    lines.append(f"* {separator_dash}")
    lines.append("* КОНЕЦ СХЕМЫ")
    lines.append(f"* {separator_dash}")
    lines.append(".END")
    lines.append("")
    
    return "\n".join(lines)


def create_minimal_cir_template(circuit_name: str = "Untitled") -> str:
    """Создать минимальный шаблон SPICE netlist (только необходимое).

    Args:
        circuit_name: Название схемы

    Returns:
        Строка с содержимым минимального шаблона .cir
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    lines = []
    
    # Заголовок
    lines.append(f"* {circuit_name}")
    lines.append(f"* Создано: {timestamp}")
    lines.append("")
    lines.append("")
    
    # Компоненты
    lines.append("* --- Компоненты ---")
    lines.append("* V1 1 0 DC 5")
    lines.append("* R1 1 2 1k")
    lines.append("* C1 2 0 1uF")
    lines.append("")
    lines.append("")
    
    # Анализ
    lines.append("* --- Анализ ---")
    lines.append("* .TRAN 0.1ms 10ms")
    lines.append("")
    lines.append("")
    
    # Вывод
    lines.append("* --- Вывод ---")
    lines.append("* .PRINT TRAN V(2)")
    lines.append("")
    lines.append("")
    
    # Конец
    lines.append(".END")
    lines.append("")
    
    return "\n".join(lines)
