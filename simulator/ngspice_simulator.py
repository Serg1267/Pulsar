"""
Модуль для работы с NGspice
Запуск симуляций, обработка результатов и автоматическое отображение графиков
"""

import re
import subprocess
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List


class NGspiceSimulator:
    """Класс для запуска NGspice симуляций"""

    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._stop_requested = False
        self._simulation_data: Dict[str, Any] = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def simulation_data(self) -> Dict[str, Any]:
        return self._simulation_data

    def run_simulation(
        self,
        circuit_file: Path,
        output_callback: Callable[[str], None],
        finished_callback: Callable[[bool], None],
    ) -> bool:
        """
        Запустить симуляцию NGspice

        Args:
            circuit_file: Путь к файлу схемы (.cir, .sp)
            output_callback: Функция для вывода
            finished_callback: Функция при завершении (успех/ошибка)

        Returns:
            True если процесс запущен, False если ошибка
        """
        if self._running:
            output_callback("[ERROR] Симуляция уже запущена")
            return False

        if not circuit_file.exists():
            output_callback(f"[ERROR] Файл схемы не найден: {circuit_file}")
            return False

        self._stop_requested = False
        self._simulation_data = {}

        # Запустить в отдельном потоке
        thread = threading.Thread(
            target=self._run_simulation_thread,
            args=(circuit_file, output_callback, finished_callback),
            daemon=True,
        )
        thread.start()

        return True

    def _run_simulation_thread(
        self,
        circuit_file: Path,
        output_callback: Callable[[str], None],
        finished_callback: Callable[[bool], None],
    ):
        """Поток для запуска симуляции"""
        try:
            output_callback(f"\n[INFO] Запуск NGspice для файла: {circuit_file.name}")
            output_callback(f"[INFO] Путь: {circuit_file}")
            output_callback("=" * 60)

            # Проверить тип анализа
            with open(circuit_file, 'r') as f:
                netlist_content = f.read()

            analysis_type = self._detect_analysis_type(netlist_content)
            output_callback(f"[INFO] Обнаружен тип анализа: {analysis_type.upper()}")

            # Сохранить тип анализа
            self._simulation_data = {
                'type': analysis_type,
            }

            output_callback("[INFO] Запуск симуляции...")

            # Запустить NGspice в batch режиме
            self._process = subprocess.Popen(
                ["ngspice", "-b", str(circuit_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            self._running = True

            # Читать вывод
            if self._process.stdout:
                for line in self._process.stdout:
                    if self._stop_requested:
                        self._process.terminate()
                        output_callback("\n[STOP] Симуляция остановлена пользователем")
                        self._running = False
                        finished_callback(False)
                        return

                    if line.strip():
                        output_callback(line.rstrip())

            return_code = self._process.wait()

            if return_code == 0:
                output_callback("\n" + "=" * 60)
                output_callback("[SUCCESS] Симуляция завершена успешно")
                finished_callback(True)
            else:
                output_callback("\n" + "=" * 60)
                output_callback(f"[ERROR] Симуляция завершилась с кодом: {return_code}")
                finished_callback(False)

        except FileNotFoundError:
            output_callback("[ERROR] NGspice не найден. Установите ngspice.")
            finished_callback(False)
        except Exception as e:
            output_callback(f"\n[ERROR] Ошибка симуляции: {e}")
            import traceback
            output_callback(f"[ERROR] {traceback.format_exc()}")
            finished_callback(False)
        finally:
            self._running = False

    def _detect_analysis_type(self, netlist: str) -> str:
        """Определить тип анализа из netlist (пропуская комментарии)"""
        for line in netlist.split('\n'):
            s = line.strip()
            if not s or s.startswith('*') or s.startswith(';'):
                continue
            if re.match(r'\.TRAN\b', s, re.IGNORECASE):
                return 'tran'
            if re.match(r'\.DC\b', s, re.IGNORECASE):
                return 'dc'
            if re.match(r'\.AC\b', s, re.IGNORECASE):
                return 'ac'
            if re.match(r'\.OP\b', s, re.IGNORECASE):
                return 'op'
        return 'unknown'

    def stop_simulation(self):
        """Остановить симуляцию"""
        if not self._running:
            return

        self._stop_requested = True
        if self._process and self._process.poll() is None:
            self._process.terminate()
