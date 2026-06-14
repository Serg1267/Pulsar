from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

from EDA.core.models.schematic import PinDirection, Pin, Component
from EDA.core.parser.sym_parser import SymParser


# Путь к библиотеке символов (каталог со всеми .sym файлами, включая подкаталоги)
LIB_SYM_DIR = Path(__file__).resolve().parent / "sym"
LIB_SYM_DIR.mkdir(parents=True, exist_ok=True)

# Пути системных библиотек Lepton EDA / gEDA
SYSTEM_LIB_PATHS = [
    "/usr/share/geda/sym/",
    "/usr/share/lepton-eda/sym/",
    "/usr/share/doc/lepton-eda/examples/gTAG/sym/",
    str(Path.home() / ".geda/sym/"),
]


@dataclass
class PinDef:
    """Описание вывода компонента в библиотеке (относительно центра символа)."""
    name: str
    rel_x: float
    rel_y: float
    label: str = ""
    direction: PinDirection = PinDirection.PASSIVE


@dataclass
class LibrarySymbol:
    """Загруженный символ из .sym файла, готовый к размещению на схеме."""
    id: str
    description: str = ""
    pins: List[PinDef] = field(default_factory=list)
    attributes: Dict[str, str] = field(default_factory=dict)
    source_path: Optional[str] = None
    sym_data: Optional[Any] = None

    def to_component(self, refdes: str, x: float, y: float,
                     rotation: float = 0.0, **attrs) -> Component:
        pins = [
            Pin(id=f"{refdes}_{p.name}", name=p.name, component_id=refdes,
                rel_x=p.rel_x, rel_y=p.rel_y, direction=p.direction)
            for p in self.pins
        ]
        props = {
            **self.attributes,
            "sym_id": self.id,
            "sym_path": self.source_path,
            "_sym_data": self.sym_data,
            **attrs,
        }
        return Component(
            id=refdes, refdes=refdes, part_type=self.id,
            pins=pins, x=x, y=y, rotation=rotation, properties=props
        )


class ComponentLibrary:
    """Библиотека компонентов: загрузка, поиск, управление .sym файлами."""

    def __init__(self):
        self._symbols: Dict[str, LibrarySymbol] = {}
        self._sym_files: Dict[str, str] = {}     # id -> filepath для быстрого доступа
        self._category_index: Dict[str, List[str]] = {}  # категория -> список id
        self.load_all()

    # ---- Загрузка ----

    def load_all(self) -> int:
        """Сканирует LIB_SYM_DIR и загружает все .sym файлы (рекурсивно)."""
        self._symbols.clear()
        self._sym_files.clear()
        self._category_index.clear()

        if not LIB_SYM_DIR.exists():
            return 0

        count = 0
        for sym_file in sorted(LIB_SYM_DIR.rglob("*.sym")):
            try:
                sym = load_lepton_sym(str(sym_file))
                self._add(sym, sym_file)
                count += 1
            except Exception as e:
                print(f"Ошибка загрузки {sym_file.name}: {e}")
        return count

    def _add(self, sym: LibrarySymbol, filepath: Path):
        self._symbols[sym.id] = sym
        self._sym_files[sym.id] = str(filepath)
        # Определяем категорию по имени родительского каталога
        cat = filepath.parent.name if filepath.parent != LIB_SYM_DIR else "other"
        self._category_index.setdefault(cat, []).append(sym.id)

    def add(self, sym: LibrarySymbol):
        """Добавляет символ в индекс (без файла на диске)."""
        self._symbols[sym.id] = sym

    # ---- Доступ ----

    def get(self, name: str) -> Optional[LibrarySymbol]:
        return self._symbols.get(name)

    def list_all(self) -> List[str]:
        return sorted(self._symbols.keys())

    def list_categories(self) -> List[str]:
        return sorted(self._category_index.keys())

    def list_by_category(self, category: str) -> List[str]:
        return sorted(self._category_index.get(category, []))

    def get_category(self, name: str) -> str:
        """Возвращает категорию символа (имя подкаталога)."""
        fp = self._sym_files.get(name, "")
        p = Path(fp)
        if p.parent == LIB_SYM_DIR or not fp:
            return "other"
        return p.parent.name

    # ---- Управление ----

    def delete_symbol(self, name: str) -> bool:
        if name not in self._symbols:
            return False
        sym_file = Path(self._sym_files.get(name, ""))
        if sym_file.exists():
            try:
                sym_file.unlink()
            except Exception as e:
                print(f"Ошибка удаления {sym_file}: {e}")
                return False
        self._symbols.pop(name, None)
        self._sym_files.pop(name, None)
        for cat, ids in self._category_index.items():
            if name in ids:
                ids.remove(name)
        return True

    # ---- Импорт из системы ----

    def import_from_system(self, src_path: str) -> bool:
        src = Path(src_path)
        if not src.exists():
            return False
        dst = LIB_SYM_DIR / src.name
        if dst.exists():
            return True
        try:
            import shutil
            shutil.copy2(str(src), str(dst))
            sym = load_lepton_sym(str(dst))
            self._add(sym, dst)
            return True
        except Exception as e:
            print(f"Ошибка импорта {src.name}: {e}")
            return False

    @staticmethod
    def get_system_symbols() -> List[dict]:
        """Ищет .sym файлы в системных путях Lepton EDA / gEDA."""
        result = []
        for p in SYSTEM_LIB_PATHS:
            p_path = Path(p)
            if not p_path.exists():
                continue
            for f in p_path.rglob("*.sym"):
                result.append({
                    "path": str(f),
                    "name": f.stem,
                    "dir": str(f.parent),
                })
        return sorted(result, key=lambda x: x["name"])

    # ---- Инстанцирование ----

    def instantiate(self, symbol_id: str, refdes: str,
                    x: float, y: float, rotation: float = 0.0,
                    **attrs) -> Component:
        sym = self.get(symbol_id)
        if not sym:
            raise KeyError(f"Symbol '{symbol_id}' не найден в библиотеке")
        return sym.to_component(refdes, x, y, rotation, **attrs)


def load_lepton_sym(filepath: str) -> LibrarySymbol:
    """Парсит .sym файл Lepton EDA / gEDA и возвращает LibrarySymbol."""
    parser = SymParser()
    data = parser.parse_file(filepath)

    # Центр символа в координатах .sym файла
    bbox = data.bounding_box
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0

    pins = []
    for i, p in enumerate(data.pins):
        name = (p.pinlabel or p.pinnumber or f"P{i}").strip()
        pins.append(PinDef(
            name=name,
            rel_x=round(p.x1 - cx),   # mil, относительно центра символа
            rel_y=round(p.y1 - cy),   # mil
            label=(p.pinlabel or p.pinnumber or ""),
        ))

    return LibrarySymbol(
        id=Path(filepath).stem,
        description=data.device or Path(filepath).stem,
        pins=pins,
        attributes={
            "device": data.device,
            "value": data.default_value,
            **data.attributes,
        },
        source_path=filepath,
        sym_data=data,
    )
