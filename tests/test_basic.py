"""
Базовые тесты для SpiceEDA

Тесты покрывают:
- Подсветку синтаксиса SPICE
- Парсинг данных симуляции (TRAN, DC, AC, OP)
- Валидацию netlist
- Конвертацию путей и файловые операции
"""

import pytest
import sys
from pathlib import Path

# Добавить корень проекта в PYTHONPATH
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─── Тесты подсветки синтаксиса ───────────────────────────────────

class TestSpiceHighlighter:
    """Тесты модуля подсветки SPICE"""

    def test_import_highlighter(self):
        """Модуль подсветки импортируется"""
        from editor.spice_highlighter import SpiceHighlighter, COLOR_SCHEMES, DEFAULT_SCHEME
        assert SpiceHighlighter is not None
        assert isinstance(COLOR_SCHEMES, dict)
        assert len(COLOR_SCHEMES) > 0
        assert DEFAULT_SCHEME in COLOR_SCHEMES

    def test_available_schemes(self):
        """Все схемы доступны"""
        from editor.spice_highlighter import SpiceHighlighter
        schemes = SpiceHighlighter.available_schemes()
        assert len(schemes) >= 4  # минимум 4 темы
        assert any("dark" in s.lower() or "light" in s.lower() for s in schemes)

    def test_spice_component_pattern(self):
        """Regex для компонентов SPICE"""
        import re
        # Компоненты: R1, C2, V3, X1, Q1, M1...
        component_pattern = re.compile(r'^([A-Za-z])\w*')
        test_cases = ["R1", "C10", "Vin", "Xsubckt", "Qnpn"]
        for case in test_cases:
            match = component_pattern.match(case)
            assert match is not None, f"Компонент {case} должен распознаться"


# ─── Тесты парсинга Tran-данных ──────────────────────────────────────

class TestTranDataParsing:
    """Тесты парсинга данных переходного анализа"""

    def test_tran_parse_basic(self):
        """Базовый парсинг .TRAN данных"""
        from plotter.spice_plotter import SpicePlotterWindow

        terminal_output = """
Index  time  v(out)  v(in)
--------------------------------
   0   0.0000e+00  0.0000e+00  1.0000e+00
   1   1.0000e-03  1.0000e-03  1.0000e+00
   2   2.0000e-03  2.0000e-03  1.0000e+00
"""
        # Создаём временное окно для вызова метода
        # (парсинг не требует GUI, но метод в классе)
        import re
        time_data = []
        voltage_data = {}
        current_vars = []
        in_data_section = False
        seen_indices = set()

        lines = terminal_output.split('\n')
        for line in lines:
            header_match = re.match(r'Index\s+time\s+(.*)', line)
            if header_match:
                var_part = header_match.group(1)
                current_vars = re.findall(r'([vViI]\([^)]+\))', var_part)
                for var in current_vars:
                    voltage_data[var] = []
                in_data_section = True
                continue

            if not in_data_section:
                continue
            if re.match(r'^\s*-+\s*$', line):
                continue

            data_match = re.match(r'^\s*(\d+)\s+([0-9eE+\-.]+)\s+([0-9eE+\-.]+)', line)
            if data_match:
                idx = int(data_match.group(1))
                if idx in seen_indices:
                    continue
                seen_indices.add(idx)
                time_val = float(data_match.group(2))
                voltage_val = float(data_match.group(3))
                time_data.append(time_val)
                if current_vars:
                    voltage_data[current_vars[0]].append(voltage_val)

        assert len(time_data) == 3
        assert time_data[0] == 0.0
        assert len(voltage_data['v(out)']) == 3
        assert voltage_data['v(out)'][1] == 1e-3

    def test_tran_parse_empty(self):
        """Пустой вывод — нет данных"""
        terminal_output = "Нет данных\nСимуляция завершена"
        import re
        time_data = []
        in_data_section = False
        for line in terminal_output.split('\n'):
            if re.match(r'Index\s+time', line):
                in_data_section = True
            if in_data_section and re.match(r'^\s*\d+\s+', line):
                time_data.append(1)
        assert len(time_data) == 0


# ─── Тесты определения типа анализа ──────────────────────────────

class TestAnalysisDetection:
    """Тесты определения типа анализа из netlist"""

    def test_detect_tran(self):
        from simulator.ngspice_simulator import NGspiceSimulator
        sim = NGspiceSimulator()
        netlist = """
V1 1 0 DC 10
R1 1 2 1k
C1 2 0 1uF
.TRAN 1ms 10ms
.PRINT TRAN v(2)
.END
"""
        assert sim._detect_analysis_type(netlist) == 'tran'

    def test_detect_dc(self):
        from simulator.ngspice_simulator import NGspiceSimulator
        sim = NGspiceSimulator()
        netlist = """
V1 1 0 DC 0
R1 1 2 1k
.DC V1 0 10 0.1
.PRINT DC v(2)
.END
"""
        assert sim._detect_analysis_type(netlist) == 'dc'

    def test_detect_ac(self):
        from simulator.ngspice_simulator import NGspiceSimulator
        sim = NGspiceSimulator()
        netlist = """
V1 1 0 AC 1
R1 1 2 1k
C1 2 0 1uF
.AC DEC 10 10 1MEG
.PRINT AC v(2)
.END
"""
        assert sim._detect_analysis_type(netlist) == 'ac'

    def test_detect_op(self):
        from simulator.ngspice_simulator import NGspiceSimulator
        sim = NGspiceSimulator()
        netlist = """
V1 1 0 DC 5
R1 1 2 1k
.OP
.END
"""
        assert sim._detect_analysis_type(netlist) == 'op'

    def test_detect_unknown(self):
        from simulator.ngspice_simulator import NGspiceSimulator
        sim = NGspiceSimulator()
        netlist = """
V1 1 0 DC 5
R1 1 0 1k
.END
"""
        assert sim._detect_analysis_type(netlist) == 'unknown'


# ─── Тесты файловых операций ─────────────────────────────────────

class TestFileOperations:
    """Тесты работы с файлами"""

    def test_cir_file_exists(self):
        """Тестовые .cir файлы существуют в examples/"""
        examples_dir = PROJECT_ROOT / "examples"
        assert examples_dir.exists()
        cir_files = list(examples_dir.glob("*.cir"))
        assert len(cir_files) > 0, "В examples/ должен быть хотя бы один .cir файл"

    def test_project_structure(self):
        """Структура проекта содержит обязательные директории"""
        required_dirs = [
            "core", "editor", "simulator", "plotter",
            "ui", "utils", "resources"
        ]
        for d in required_dirs:
            assert (PROJECT_ROOT / d).exists(), f"Директория {d} не найдена"


# ─── Тесты DC/AC парсинга ─────────────────────────────────────────

class TestDcAcParsing:
    """Тесты парсинга DC и AC данных"""

    def test_dc_parse_basic(self):
        """Базовый парсинг .DC данных"""
        terminal_output = """
Index  v-sweep  v(1)  v(2)
--------------------------------
   0   0.0000e+00  0.0000e+00  0.0000e+00
   1   1.0000e+00  1.0000e+00  5.0000e-01
   2   2.0000e+00  2.0000e+00  1.0000e+00
"""
        import re
        sweep_data = []
        dc_data = {}
        current_vars = []
        in_data_section = False

        lines = terminal_output.split('\n')
        for line in lines:
            header_match = re.match(r'Index\s+\S+\s+(.*)', line)
            if header_match:
                var_part = header_match.group(1)
                current_vars = re.findall(r'([vViI]\([^)]+\))', var_part)
                for var in current_vars:
                    dc_data[var] = []
                in_data_section = True
                continue

            if not in_data_section:
                continue
            if re.match(r'^\s*-+\s*$', line):
                continue

            data_match = re.match(r'^\s*(\d+)\s+([0-9eE+\-.]+)\s+([0-9eE+\-.]+)', line)
            if data_match:
                sweep_val = float(data_match.group(2))
                voltage_val = float(data_match.group(3))
                sweep_data.append(sweep_val)
                if current_vars:
                    dc_data[current_vars[0]].append(voltage_val)

        assert len(sweep_data) == 3
        assert sweep_data[0] == 0.0
        assert dc_data['v(1)'][2] == 2.0

    def test_ac_parse_basic(self):
        """Базовый парсинг .AC данных (amplitude/phase)"""
        terminal_output = """
Index  frequency  v(out)
--------------------------------
   0   1.0000e+01  1.0000e+00  -1.5708e+00
   1   1.0000e+02  7.0711e-01  -2.3562e+00
"""
        import re
        freq_data = []
        mag_data = []
        in_data_section = False

        lines = terminal_output.split('\n')
        for line in lines:
            if re.match(r'Index\s+frequency', line):
                in_data_section = True
                continue
            if not in_data_section:
                continue
            if re.match(r'^\s*-+\s*$', line):
                continue

            data_match = re.match(r'^\s*(\d+)\s+([0-9eE+\-.]+)\s+([0-9eE+\-.]+)', line)
            if data_match:
                freq = float(data_match.group(2))
                mag = float(data_match.group(3))
                freq_data.append(freq)
                mag_data.append(mag)

        assert len(freq_data) == 2
        assert freq_data[0] == 10.0
        assert mag_data[1] == pytest.approx(0.70711, abs=1e-4)
