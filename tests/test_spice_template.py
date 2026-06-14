"""
Тесты для генератора шаблонов SPICE netlist.
"""

import pytest
from utils.spice_template import create_cir_template, create_minimal_cir_template, wrap_netlist_in_template


class TestSpiceTemplate:
    """Тесты генерации шаблонов SPICE"""

    def test_default_template(self):
        """Тест шаблона по умолчанию"""
        template = create_cir_template()
        
        # Проверка наличия всех секций
        assert "КОМПОНЕНТЫ СХЕМЫ" in template
        assert "МОДЕЛИ КОМПОНЕНТОВ" in template
        assert "ДИРЕКТИВЫ АНАЛИЗА" in template
        assert "ДИРЕКТИВЫ ВЫВОДА ДАННЫХ" in template
        assert "КОНЕЦ СХЕМЫ" in template
        assert ".END" in template

    def test_custom_circuit_name(self):
        """Тест пользовательского названия"""
        template = create_cir_template(circuit_name="RC Filter")
        assert "* RC Filter" in template
        assert "* " + "=" * 66 in template

    def test_custom_description(self):
        """Тест описания схемы"""
        desc = "Simple RC low-pass filter with 1kHz cutoff"
        template = create_cir_template(description=desc)
        assert "Описание:" in template
        assert "Simple RC low-pass filter" in template

    def test_author_field(self):
        """Тест поля автора"""
        template = create_cir_template(author="John Doe")
        assert "Автор: John Doe" in template

    def test_tran_analysis(self):
        """Тест переходного анализа"""
        template = create_cir_template(analysis_type="TRAN")
        assert ".TRAN 0.1ms 10ms" in template
        assert "Переходный анализ" in template

    def test_ac_analysis(self):
        """Тест частотного анализа"""
        template = create_cir_template(analysis_type="AC")
        assert ".AC DEC 100 10 1MEG" in template
        assert "Частотный анализ" in template

    def test_dc_sweep(self):
        """Тест развёртки DC"""
        template = create_cir_template(analysis_type="DC")
        assert ".DC V1 0 10 0.1" in template
        assert "Развёртка по постоянному току" in template

    def test_operating_point(self):
        """Тест рабочей точки"""
        template = create_cir_template(analysis_type="OP")
        assert ".OP" in template
        assert "Расчёт рабочей точки" in template

    def test_include_models(self):
        """Тест включения моделей"""
        template = create_cir_template(include_models=True)
        assert ".MODEL 1N4148 D" in template
        assert ".MODEL 2N2222 NPN" in template
        assert "Пример: диод 1N4148" in template

    def test_exclude_models(self):
        """Тест исключения моделей"""
        template = create_cir_template(include_models=False)
        # Активные модели НЕ должны быть
        lines = template.split('\n')
        active_model_lines = [line for line in lines if line.startswith('.MODEL')]
        assert len(active_model_lines) == 0, "Не должно быть активных моделей при include_models=False"
        # Закомментированные примеры ДОЛЖНЫ быть
        assert "* Примеры" in template

    def test_minimal_template(self):
        """Тест минимального шаблона"""
        template = create_minimal_cir_template(circuit_name="Test")
        assert "* Test" in template
        assert "Компоненты" in template
        assert ".TRAN 0.1ms 10ms" in template
        assert ".PRINT TRAN V(2)" in template
        assert ".END" in template

    def test_template_structure_order(self):
        """Тест порядка секций в шаблоне"""
        template = create_cir_template()
        
        # Проверка что секции идут в правильном порядке
        header_pos = template.find("ЗАГОЛОВОК")
        components_pos = template.find("КОМПОНЕНТЫ")
        models_pos = template.find("МОДЕЛИ")
        analysis_pos = template.find("ДИРЕКТИВЫ АНАЛИЗА")
        output_pos = template.find("ДИРЕКТИВЫ ВЫВОДА")
        end_pos = template.find(".END")
        
        assert header_pos < components_pos < models_pos < analysis_pos < output_pos < end_pos

    def test_long_description_wrapping(self):
        """Тест переноса длинного описания"""
        long_desc = "This is a very long description that should be wrapped across multiple lines in the template output to ensure proper formatting"
        template = create_cir_template(description=long_desc)
        
        # Каждая строка описания должна начинаться с "* "
        lines = template.split("\n")
        desc_lines = [line for line in lines if "This is a very long" in line or "wrapped across" in line]
        assert len(desc_lines) > 0
        
    def test_timestamp_included(self):
        """Тест наличия временной метки"""
        template = create_cir_template()
        assert "Дата создания:" in template
        # Формат: YYYY-MM-DD HH:MM
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", template)


class TestWrapNetlistInTemplate:
    """Тесты обёртки raw netlist в шаблон"""

    def test_basic_wrap(self):
        """Тест базовой обёртки"""
        raw = """V1 1 0 DC 5
R1 1 2 1k
C1 2 0 1uF
.TRAN 0.1ms 10ms
.PRINT TRAN V(2)
.END"""
        template = wrap_netlist_in_template(raw, circuit_name="RC Filter")
        
        assert "RC Filter" in template
        assert "КОМПОНЕНТЫ СХЕМЫ" in template
        assert "МОДЕЛИ КОМПОНЕНТОВ" in template
        assert "ДИРЕКТИВЫ АНАЛИЗА" in template
        assert "ДИРЕКТИВЫ ВЫВОДА ДАННЫХ" in template
        assert ".END" in template

    def test_components_extracted(self):
        """Тест извлечения компонентов"""
        raw = """V1 1 0 DC 5
R1 1 2 1k
C1 2 0 1uF
L1 2 3 10mH
.END"""
        template = wrap_netlist_in_template(raw, circuit_name="Test")
        
        assert "V1 1 0 DC 5" in template
        assert "R1 1 2 1k" in template
        assert "C1 2 0 1uF" in template
        assert "L1 2 3 10mH" in template
        # Проверка секций
        assert "* --- Источники ---" in template
        assert "* --- Пассивные компоненты ---" in template

    def test_models_extracted(self):
        """Тест извлечения моделей"""
        raw = """D1 3 0 1N4148
.MODEL 1N4148 D (IS=2.52n RS=.568 N=1.752)
.END"""
        template = wrap_netlist_in_template(raw, circuit_name="Diode Test")
        
        assert ".MODEL 1N4148 D" in template
        assert "D1 3 0 1N4148" in template
        assert "* --- Полупроводники ---" in template

    def test_directives_extracted(self):
        """Тест извлечения директив анализа"""
        raw = """V1 1 0 DC 5
R1 1 2 1k
.TRAN 0.1ms 10ms
.AC DEC 100 10 1MEG
.PRINT TRAN V(2)
.END"""
        template = wrap_netlist_in_template(raw, circuit_name="Test")
        
        assert ".TRAN 0.1ms 10ms" in template
        assert ".AC DEC 100 10 1MEG" in template
        assert ".PRINT TRAN V(2)" in template

    def test_comments_filtered(self):
        """Тест фильтрации комментариев lepton-netlist"""
        raw = """* lepton-netlist generated
* Created by gnetlist
V1 1 0 DC 5
R1 1 2 1k
.END"""
        template = wrap_netlist_in_template(raw, circuit_name="Test")
        
        assert "* lepton-netlist" not in template
        assert "* Created by gnetlist" not in template
        assert "V1 1 0 DC 5" in template
        assert "R1 1 2 1k" in template

    def test_empty_netlist(self):
        """Тест пустого netlist"""
        raw = ""
        template = wrap_netlist_in_template(raw, circuit_name="Empty")
        
        assert "Empty" in template
        # Должны быть примеры-заглушки
        assert "* V1 1 0 DC 5" in template
        assert "* R1 1 2 1k" in template
        assert ".END" in template

    def test_custom_author(self):
        """Тест поля автора"""
        raw = "V1 1 0 DC 5\n.END"
        template = wrap_netlist_in_template(raw, circuit_name="Test", author="John Doe")
        
        assert "Автор: John Doe" in template

    def test_section_order(self):
        """Тест порядка секций"""
        raw = """V1 1 0 DC 5
R1 1 2 1k
.MODEL 1N4148 D (IS=2.52n)
.TRAN 0.1ms 10ms
.PRINT TRAN V(2)
.END"""
        template = wrap_netlist_in_template(raw, circuit_name="Test")
        
        header_pos = template.find("КОМПОНЕНТЫ")
        models_pos = template.find("МОДЕЛИ")
        analysis_pos = template.find("ДИРЕКТИВЫ АНАЛИЗА")
        output_pos = template.find("ДИРЕКТИВЫ ВЫВОДА")
        end_pos = template.find(".END")
        
        assert header_pos < models_pos < analysis_pos < output_pos < end_pos

    def test_same_format_as_create_template(self):
        """Тест что формат совпадает с create_cir_template"""
        raw = """V1 1 0 DC 5
R1 1 2 1k
.TRAN 0.1ms 10ms
.PRINT TRAN V(2)
.END"""
        wrapped = wrap_netlist_in_template(raw, circuit_name="Test")
        created = create_cir_template(circuit_name="Test", analysis_type="TRAN")
        
        # Проверить одинаковые подсказки в секциях
        assert "Формат: <имя> <узел+> <узел-> <значение>" in wrapped
        assert "Формат: <имя> <узел+> <узел-> <значение>" in created
        
        assert "Формат: .MODEL <имя> <тип> (<параметры>)" in wrapped
        assert "Формат: .MODEL <имя> <тип> (<параметры>)" in created
        
        assert "Формат: .PRINT <тип_анализа> <переменные>" in wrapped
        assert "Формат: .PRINT <тип_анализа> <переменные>" in created

    def test_no_output_section(self):
        """Тест секции вывода когда её нет"""
        raw = """V1 1 0 DC 5
R1 1 2 1k
.TRAN 0.1ms 10ms
.END"""
        template = wrap_netlist_in_template(raw, circuit_name="Test")
        
        # Должны быть примеры-заглушки вывода
        assert "* .PRINT TRAN V(1) V(2) V(3)" in template
        assert "* .PRINT TRAN I(V1) I(V2)" in template

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
