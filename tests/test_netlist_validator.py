"""
Тесты для валидатора netlist.
"""

import pytest
import tempfile
import os
from pathlib import Path

from simulator.netlist_validator import validate_netlist, ValidationError


VALID_RC_NETLIST = """\
* RC circuit - transient analysis
V1 1 0 DC 0 SIN(0 5 1k)
R1 1 2 1k
C1 2 0 1u
.TRAN 0 10m 0 1u
.PRINT TRAN V(2)
.END
"""

NO_GND_NETLIST = """\
* No ground
V1 1 2 DC 5
R1 1 2 1k
.TRAN 0 10m
.END
"""

NO_END_NETLIST = """\
* No .END directive
V1 1 0 DC 5
R1 1 0 1k
.TRAN 0 10m
"""

NO_ANALYSIS_NETLIST = """\
* No analysis directive
V1 1 0 DC 5
R1 1 0 1k
.PRINT TRAN V(1)
.END
"""

NO_COMPONENTS_NETLIST = """\
* No components
.TRAN 0 10m
.PRINT TRAN V(1)
.END
"""

EMPTY_NETLIST = ""

DUPLICATE_REFDES_NETLIST = """\
* Duplicate refdes
R1 1 0 1k
R1 2 0 2k
V1 1 2 DC 5
.TRAN 0 10m
.END
"""

VALID_DC_NETLIST = """\
* DC sweep
V1 1 0 DC 0
R1 1 2 1k
R2 2 0 2k
.DC V1 0 10 0.1
.PRINT DC V(2)
.END
"""

VALID_OP_NETLIST = """\
* Operating point
V1 1 0 DC 10
R1 1 2 1k
R2 2 0 1k
.OP
.PRINT OP V(2)
.END
"""


def _write_netlist(content: str) -> Path:
    """Создать временный файл с netlist"""
    fd, path = tempfile.mkstemp(suffix=".cir")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return Path(path)


class TestValidNetlists:
    """Тесты валидных netlist'ов"""

    def test_valid_rc_transient(self):
        path = _write_netlist(VALID_RC_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is True
        assert result.error_count == 0
        assert result.analysis_type == "tran"
        os.unlink(path)

    def test_valid_dc_sweep(self):
        path = _write_netlist(VALID_DC_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is True
        assert result.analysis_type == "dc"
        os.unlink(path)

    def test_valid_op(self):
        path = _write_netlist(VALID_OP_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is True
        assert result.analysis_type == "op"
        os.unlink(path)


class TestInvalidNetlists:
    """Тесты невалидных netlist'ов"""

    def test_no_ground(self):
        path = _write_netlist(NO_GND_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is False
        assert result.error_count >= 1
        assert any("заземления" in e.message.lower() or "узел 0" in e.message.lower() for e in result.errors)
        os.unlink(path)

    def test_no_end(self):
        path = _write_netlist(NO_END_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is False
        assert any(".END" in e.message or "директива .END" in e.message for e in result.errors)
        os.unlink(path)

    def test_no_analysis(self):
        path = _write_netlist(NO_ANALYSIS_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is False
        assert any("директивы анализа" in e.message.lower() or ".TRAN" in e.message for e in result.errors)
        os.unlink(path)

    def test_no_components(self):
        path = _write_netlist(NO_COMPONENTS_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is False
        assert any("компонентов" in e.message.lower() or "нечего" in e.message.lower() for e in result.errors)
        os.unlink(path)

    def test_empty_file(self):
        path = _write_netlist(EMPTY_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is False
        assert any("пустой" in e.message.lower() for e in result.errors)
        os.unlink(path)

    def test_duplicate_refdes(self):
        path = _write_netlist(DUPLICATE_REFDES_NETLIST)
        result = validate_netlist(path)
        assert result.is_valid is False
        assert any("дубликат" in e.message.lower() or "R1" in e.message for e in result.errors)
        os.unlink(path)

    def test_file_not_exists(self):
        result = validate_netlist(Path("/nonexistent/file.cir"))
        assert result.is_valid is False
        assert result.error_count >= 1


class TestWarnings:
    """Тесты предупреждений"""

    def test_no_print_warning(self):
        path = _write_netlist("""\
* No .PRINT
V1 1 0 DC 5
R1 1 0 1k
.TRAN 0 10m
.END
""")
        result = validate_netlist(path)
        assert result.is_valid is True  # warning не блокирует
        assert result.warning_count >= 1
        assert any(".PRINT" in e.message for e in result.errors)
        os.unlink(path)


class TestFormattedReport:
    """Тесты форматированного отчёта"""

    def test_report_format(self):
        path = _write_netlist(NO_GND_NETLIST)
        result = validate_netlist(path)
        report = result.formatted_report()
        assert "ПРОВЕРКА NETLIST" in report
        assert "⛔" in report or "error" in report.lower()
        os.unlink(path)

    def test_valid_report_format(self):
        path = _write_netlist(VALID_RC_NETLIST)
        result = validate_netlist(path)
        report = result.formatted_report()
        assert "прошёл проверку" in report
        os.unlink(path)
