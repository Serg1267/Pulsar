"""
Валидация SPICE netlist перед запуском симуляции.
Проверяет базовые требования: .END, GND, директивы анализа, компоненты.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class ValidationError:
    """Одна ошибка валидации"""
    severity: str  # "error" | "warning"
    message: str
    line_number: int | None = None  # 1-based, None если не привязана к строке
    suggestion: str | None = None  # Подсказка как исправить


@dataclass
class ValidationResult:
    """Результат валидации netlist"""
    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    analysis_type: str = "unknown"  # tran, dc, ac, op, unknown

    @property
    def error_count(self) -> int:
        return sum(1 for e in self.errors if e.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for e in self.errors if e.severity == "warning")

    def formatted_report(self) -> str:
        """Красивый отчёт для терминала"""
        lines = []
        lines.append("=" * 60)
        lines.append("ПРОВЕРКА NETLIST")
        lines.append("=" * 60)

        if self.is_valid:
            lines.append(f"✅ Netlist прошёл проверку")
            lines.append(f"   Предупреждений: {self.warning_count}")
        else:
            lines.append(f"❌ Найдено {self.error_count} ошибок, {self.warning_count} предупреждений")
            lines.append("")

            for err in self.errors:
                icon = "⛔" if err.severity == "error" else "⚠️"
                loc = f" (строка {err.line_number})" if err.line_number else ""
                lines.append(f"  {icon} {err.message}{loc}")
                if err.suggestion:
                    lines.append(f"     💡 {err.suggestion}")

        lines.append("")
        return "\n".join(lines)


def validate_netlist(file_path: Path) -> ValidationResult:
    """
    Валидировать SPICE netlist.

    Проверяет:
    - Файл существует и не пустой
    - Есть .END
    - Есть GND (узел 0 или node=GND)
    - Есть хотя бы один компонент (R, C, L, V, I, D, Q, M...)
    - Есть директива анализа (.TRAN, .DC, .AC, .OP)
    - Базовый синтаксис компонентов
    - Нет дубликатов refdes
    """
    errors: List[ValidationError] = []

    if not file_path.exists():
        return ValidationResult(
            is_valid=False,
            errors=[ValidationError("error", f"Файл не найден: {file_path}")],
        )

    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.split("\n")

    if not content.strip():
        return ValidationResult(
            is_valid=False,
            errors=[ValidationError("error", "Файл пустой")],
        )

    # --- 1. Проверка .END ---
    has_end = False
    end_line = None
    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if stripped == ".END":
            has_end = True
            end_line = i + 1
            break
        elif stripped.startswith(".END"):
            has_end = True
            end_line = i + 1
            break

    if not has_end:
        errors.append(ValidationError(
            "error",
            "Отсутствует директива .END — NGspice не завершит обработку",
        ))

    # --- 2. Проверка GND (узел 0, пропуская .SUBCKT блоки) ---
    has_ground = False
    ground_lines: List[int] = []
    inside_subckt = 0

    component_re = re.compile(
        r'^\s*([RCLVDIQMBJXF]\S*)\s+(\S+)\s+(\S+)', re.IGNORECASE
    )

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue
        upper = stripped.upper()
        if upper.startswith(".SUBCKT"):
            inside_subckt += 1
            continue
        if upper.startswith(".ENDS"):
            inside_subckt = max(0, inside_subckt - 1)
            continue
        if inside_subckt > 0 or stripped.startswith("."):
            continue

        match = component_re.match(stripped)
        if match:
            node1 = match.group(2)
            node2 = match.group(3)
            if node1 == "0" or node2 == "0":
                has_ground = True
                ground_lines.append(i + 1)
            # Проверить многосхемные элементы (3+ узлов)
            remaining = stripped[match.end():]
            extra_nodes = re.findall(r'\b(\d+)\b', remaining)
            if "0" in extra_nodes:
                has_ground = True
                ground_lines.append(i + 1)

    if not has_ground:
        errors.append(ValidationError(
            "error",
            "Нет заземления (узел 0) — SPICE требует опорную точку",
            suggestion="Подключите один из узлов к узлу '0' (GND)",
        ))

    # --- 3. Проверка компонентов (пропускаем .SUBCKT/.ENDS блоки) ---
    components: List[tuple] = []  # (refdes, line_number)
    refdes_set = set()
    inside_subckt = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("*"):
            continue

        if stripped.upper().startswith(".SUBCKT"):
            inside_subckt += 1
            continue
        if stripped.upper().startswith(".ENDS"):
            inside_subckt = max(0, inside_subckt - 1)
            continue
        if inside_subckt > 0:
            continue
        if stripped.startswith("."):
            continue

        match = component_re.match(stripped)
        if match:
            refdes = match.group(1).upper()
            components.append((refdes, i + 1))

            # Проверка дубликатов
            if refdes in refdes_set:
                errors.append(ValidationError(
                    "error",
                    f"Дубликат refdes: {refdes}",
                    line_number=i + 1,
                ))
            refdes_set.add(refdes)

    if not components:
        errors.append(ValidationError(
            "error",
            "Нет компонентов в схеме — нечего симулировать",
        ))

    # --- 4. Проверка директивы анализа ---
    analysis_types_found = []

    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if stripped.startswith(".TRAN"):
            analysis_types_found.append(("tran", i + 1))
        elif stripped.startswith(".DC"):
            analysis_types_found.append(("dc", i + 1))
        elif stripped.startswith(".AC"):
            analysis_types_found.append(("ac", i + 1))
        elif stripped.startswith(".OP"):
            analysis_types_found.append(("op", i + 1))

    if not analysis_types_found:
        errors.append(ValidationError(
            "error",
            "Нет директивы анализа (.TRAN, .DC, .AC, .OP)",
            suggestion="Добавьте, например: .TRAN 0 10m 0 1u для переходного процесса",
        ))

    # --- 5. Проверка .PRINT / .PLOT ---
    has_output = False
    has_op_analysis = False
    for i, line in enumerate(lines):
        stripped = line.strip().upper()
        if stripped.startswith(".PRINT") or stripped.startswith(".PLOT"):
            has_output = True
            break
    # .OP analysis alone is sufficient (no PRINT/PLOT needed)
    if not has_output:
        for atype, _ in analysis_types_found:
            if atype == 'op':
                has_op_analysis = True
                break

    if not has_output and not has_op_analysis and analysis_types_found:
        errors.append(ValidationError(
            "warning",
            "Нет директивы вывода (.PRINT / .PLOT) — результаты не будут показаны",
            suggestion="Добавьте, например: .PLOT TRAN V(out) или .PRINT TRAN V(out)",
        ))

    # --- 6. Базовый синтаксис компонентов ---
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("*") or stripped.startswith("."):
            continue

        # Строка похожа на компонент но не соответствует формату
        first_char = stripped[0].upper()
        if first_char in "RCLVDI":
            parts = stripped.split()
            if len(parts) < 3:
                errors.append(ValidationError(
                    "warning",
                    f"Подозрительная строка компонента (менее 3 полей): {stripped}",
                    line_number=i + 1,
                ))

    # --- Итог ---
    error_count = sum(1 for e in errors if e.severity == "error")
    is_valid = error_count == 0

    analysis_type = analysis_types_found[0][0] if analysis_types_found else "unknown"

    return ValidationResult(
        is_valid=is_valid,
        errors=errors,
        analysis_type=analysis_type,
    )
