"""
Модуль библиотеки компонентов SpiceEDA.
Загрузка .lib файлов из resources/LIB/, поиск по имени, идеальные модели по умолчанию.
"""

import os
import re
from pathlib import Path
from typing import Dict, List, Optional


# Каталог библиотеки относительно корня проекта
LIB_DIR_NAME = "resources/LIB"

# Типы компонентов и соответствующие префиксы
COMPONENT_TYPES = {
    "diode": {"prefixes": ["D"], "subdir": "diodes"},
    "npn": {"prefixes": ["Q"], "subdir": "transistors"},
    "pnp": {"prefixes": ["Q"], "subdir": "transistors"},
    "nmos": {"prefixes": ["M"], "subdir": "transistors"},
    "pmos": {"prefixes": ["M"], "subdir": "transistors"},
    "zener": {"prefixes": ["D"], "subdir": "zeners"},
    "bjt": {"prefixes": ["Q"], "subdir": "transistors"},
    "mosfet": {"prefixes": ["M"], "subdir": "transistors"},
}


class ComponentModel:
    """Представляет одну SPICE-модель компонента"""

    def __init__(self, name: str, model_type: str, model_line: str, source: str):
        self.name = name              # 1N4001, 2N2222 и т.д.
        self.model_type = model_type  # D, NPN, PNP и т.д.
        self.model_line = model_line  # .model 1N4001 D (IS=2.52n ...)
        self.source = source          # "resources/LIB/diodes/1N4001.lib"
        self.is_ideal = source.startswith("ideal_")


class ComponentLibrary:
    """Библиотека компонентов — загрузка, поиск, идеальные модели"""

    def __init__(self, project_root: Optional[str] = None):
        if project_root is None:
            project_root = str(Path(__file__).resolve().parent.parent)
        self.lib_dir = Path(project_root) / LIB_DIR_NAME
        self._models: Dict[str, ComponentModel] = {}  # name -> model
        self._scan_library()

    def _scan_library(self):
        """Просканировать все .lib файлы в каталоге библиотеки"""
        if not self.lib_dir.exists():
            self.lib_dir.mkdir(parents=True, exist_ok=True)
            return

        for root, dirs, files in os.walk(self.lib_dir):
            for filename in files:
                if filename.endswith(".lib"):
                    filepath = Path(root) / filename
                    self._parse_lib_file(filepath)

    def _parse_lib_file(self, filepath: Path):
        """Парсить .lib файл и извлечь .model директивы"""
        try:
            content = filepath.read_text(encoding="utf-8")
        except Exception:
            return

        # Извлечь все .model строки
        for match in re.finditer(r'^\s*\.model\s+(\S+)\s+(\S+)\s*(.*)', content, re.MULTILINE | re.IGNORECASE):
            model_name = match.group(1)
            model_type = match.group(2)
            model_params = match.group(3).strip()

            # Собрать полную строку .model (может быть многострочной)
            full_line = f".model {model_name} {model_type} {model_params}"
            # Убрать хвостовые скобки если они разбиты по строкам
            full_line = full_line.replace('\n', ' ').replace('\r', '')

            # Относительный путь
            try:
                rel_path = str(filepath.relative_to(self.lib_dir.parent.parent))
            except ValueError:
                rel_path = str(filepath)

            self._models[model_name.upper()] = ComponentModel(
                name=model_name,
                model_type=model_type,
                model_line=full_line,
                source=rel_path
            )
            # Пометить модели из ideal/ каталога
            if "ideal" in str(filepath).lower() and "ideal" in filepath.parts:
                self._models[model_name.upper()].is_ideal = True

    def get_model(self, name: str) -> Optional[ComponentModel]:
        """Получить модель по имени (регистронезависимо)"""
        return self._models.get(name.upper())

    def find_models(self, query: str) -> List[ComponentModel]:
        """Найти модели по подстроке (регистронезависимо)"""
        query_upper = query.upper()
        return [
            model for name, model in self._models.items()
            if query_upper in name
        ]

    # Маппинг типа компонента → поиск идеальной модели
    IDEAL_MODEL_MAP = {
        "diode": {"by": "name", "value": "diode"},
        "zener": {"by": "name", "value": "zener"},
        "npn": {"by": "name", "value": "Q_npn"},
        "pnp": {"by": "name", "value": "Q_pnp"},
        "nmos": {"by": "name", "value": "nmos"},
        "pmos": {"by": "name", "value": "pmos"},
        "bjt": {"by": "name", "value": "Q_npn"},  # по умолчанию NPN
        "mosfet": {"by": "name", "value": "nmos"},  # по умолчанию NMOS
    }

    def get_ideal_model(self, component_type: str) -> Optional[ComponentModel]:
        """Получить идеальную модель для типа компонента"""
        rule = self.IDEAL_MODEL_MAP.get(component_type.lower())
        if not rule:
            return None
        search_value = rule["value"].upper()
        for name, model in self._models.items():
            if model.is_ideal and name == search_value:
                return model
        return None

    def get_component_prefix(self, component_type: str) -> str:
        """Получить SPICE-префикс для типа (D для диода, Q для BJT и т.д.)"""
        type_info = COMPONENT_TYPES.get(component_type.lower())
        if type_info:
            return type_info["prefixes"][0]
        # По умолчанию
        type_map = {"diode": "D", "zener": "D", "npn": "Q", "pnp": "Q",
                     "nmos": "M", "pmos": "M", "bjt": "Q", "mosfet": "M"}
        return type_map.get(component_type.lower(), "X")

    def list_available(self) -> Dict[str, List[str]]:
        """Вернуть список доступных моделей по категориям"""
        result: Dict[str, List[str]] = {}
        for name, model in sorted(self._models.items()):
            category = "ideal" if model.is_ideal else model.model_type.lower()
            if category not in result:
                result[category] = []
            result[category].append(name)
        return result

    def get_library_context(self) -> str:
        """Сгенерировать текстовый контекст библиотеки для AI"""
        if not self._models:
            return ""

        lines = ["--- КОМПОНЕНТНАЯ БИБЛИОТЕКА (resources/LIB/) ---"]
        lines.append("Доступные модели компонентов. Если пользователь указывает конкретную модель,")
        lines.append("найди её ниже и вставь .model + строку компонента в netlist.")
        lines.append("Если модель НЕ указана — используй идеальную модель (ideal_*).")
        lines.append("")

        # Группировка по типам
        by_type: Dict[str, List[ComponentModel]] = {}
        for model in self._models.values():
            t = "IDEAL" if model.is_ideal else model.model_type
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(model)

        for model_type, models in sorted(by_type.items()):
            lines.append(f"[{model_type}]")
            for m in sorted(models, key=lambda x: x.name):
                lines.append(f"  {m.name}: {m.model_line}")
            lines.append("")

        lines.append("--- КОНЕЦ БИБЛИОТЕКИ ---")
        return "\n".join(lines)

    def get_next_refdes(self, netlist_text: str, component_type: str) -> str:
        """Определить следующий свободный refdes для типа компонента

        Пример: netlist содержит D1, D2 → вернёт D3 для diode
        """
        prefix = self.get_component_prefix(component_type)

        # Найти все существующие refdes с этим префиксом
        pattern = rf'^{prefix}(\d+)\b'
        max_num = 0
        for line in netlist_text.split('\n'):
            match = re.match(pattern, line.strip(), re.IGNORECASE)
            if match:
                num = int(match.group(1))
                if num > max_num:
                    max_num = num

        return f"{prefix}{max_num + 1}"

    def generate_component_line(self, component_type: str, model_name: str,
                                 nodes: List[str], refdes: Optional[str] = None) -> str:
        """Сгенерировать строку компонента SPICE

        component_type: "diode", "npn", "nmos" и т.д.
        model_name: "1N4001", "ideal_diode" и т.д.
        nodes: список узлов ["1", "2"] для диода, ["C", "B", "E"] для BJT
        refdes: если None — определить автоматически (но нужен netlist)
        """
        prefix = self.get_component_prefix(component_type)
        if refdes is None:
            refdes = f"{prefix}1"  # фолбэк

        # Диод: Dname anode cathode model
        if component_type in ("diode", "zener"):
            if len(nodes) >= 2:
                return f"{refdes} {nodes[0]} {nodes[1]} {model_name}"
            return f"{refdes} 1 2 {model_name}"

        # BJT: Qname collector base emitter model
        if component_type in ("npn", "pnp", "bjt"):
            if len(nodes) >= 3:
                return f"{refdes} {nodes[0]} {nodes[1]} {nodes[2]} {model_name}"
            return f"{refdes} C B E {model_name}"

        # MOSFET: Mname drain gate source bulk(опц) model
        if component_type in ("nmos", "pmos", "mosfet"):
            if len(nodes) >= 3:
                bulk = nodes[3] if len(nodes) > 3 else "0"
                return f"{refdes} {nodes[0]} {nodes[1]} {nodes[2]} {bulk} {model_name}"
            return f"{refdes} D G S 0 {model_name}"

        # Фолбэк
        return f"{refdes} {' '.join(nodes)} {model_name}"


# ─── Глобальный экземпляр ──────────────────────────────────────────
_lib_instance: Optional[ComponentLibrary] = None


def get_library(project_root: Optional[str] = None) -> ComponentLibrary:
    """Получить глобальный экземпляр библиотеки"""
    global _lib_instance
    if _lib_instance is None:
        _lib_instance = ComponentLibrary(project_root)
    return _lib_instance


def reload_library(project_root: Optional[str] = None):
    """Перезагрузить библиотеку (после добавления новых .lib файлов)"""
    global _lib_instance
    _lib_instance = ComponentLibrary(project_root)
