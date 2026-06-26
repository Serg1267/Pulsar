"""
Модуль для работы с NGspice
Запуск симуляций, обработка результатов и автоматическое отображение графиков
"""

import os
import re
import signal
import subprocess
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

MAX_OUTPUT_LINES = 5000000       # абсолютный лимит — прервать симуляцию
MAX_OUTPUT_KEEP = 500000         # сколько строк хранить в памяти для отображения


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
        finished_callback: Callable[[bool, list[str]], None],
    ):
        """Поток для запуска симуляции — собирает всё в локальный list, передаёт батчем."""
        all_lines: list[str] = []
        def log(msg: str):
            all_lines.append(msg)

        def finish(success: bool):
            """Единая точка выхода с триммингом буфера."""
            if len(all_lines) > MAX_OUTPUT_KEEP:
                trimmed = all_lines[-MAX_OUTPUT_KEEP:]
            else:
                trimmed = all_lines
            finished_callback(success, trimmed)

        try:
            log(f"\n[INFO] Запуск NGspice для файла: {circuit_file.name}")
            log(f"[INFO] Путь: {circuit_file}")
            log("=" * 60)

            with open(circuit_file, 'r') as f:
                netlist_content = f.read()

            analysis_type = self._detect_analysis_type(netlist_content)
            log(f"[INFO] Обнаружен тип анализа: {analysis_type.upper()}")

            self._simulation_data = {'type': analysis_type}

            log("[INFO] Запуск симуляции...")

            self._process = subprocess.Popen(
                ["ngspice", "-b", str(circuit_file)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )

            self._running = True

            line_count = 0
            if self._process.stdout:
                try:
                    for line in self._process.stdout:
                        if self._stop_requested:
                            log("\n[STOP] Симуляция остановлена пользователем")
                            self._running = False
                            finish(False)
                            return

                        line_count += 1
                        if line_count > MAX_OUTPUT_LINES:
                            log(f"\n[WARN] Превышен лимит вывода ({MAX_OUTPUT_LINES} строк). Симуляция прервана.")
                            try:
                                if hasattr(signal, 'SIGKILL'):
                                    os.kill(self._process.pid, signal.SIGKILL)
                                else:
                                    self._process.kill()
                                if self._process.stdout:
                                    self._process.stdout.close()
                            except Exception:
                                pass
                            self._running = False
                            finish(False)
                            return

                        if line.strip():
                            log(line.rstrip())
                except ValueError:
                    log("\n[STOP] Вывод прерван")
                    self._running = False
                    finish(False)
                    return

            return_code = self._process.wait()

            if return_code == 0:
                log("\n" + "=" * 60)
                log("[SUCCESS] Симуляция завершена успешно")
            else:
                log("\n" + "=" * 60)
                log(f"[ERROR] Симуляция завершилась с кодом: {return_code}")

            finish(return_code == 0)

        except FileNotFoundError:
            log("[ERROR] NGspice не найден. Установите ngspice.")
            finish(False)
        except Exception as e:
            log(f"\n[ERROR] Ошибка симуляции: {e}")
            import traceback
            log(f"[ERROR] {traceback.format_exc()}")
            finish(False)
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
        """Остановить симуляцию (SIGKILL + закрыть stdout)"""
        if not self._running:
            return

        self._stop_requested = True
        if self._process and self._process.poll() is None:
            try:
                # SIGKILL гарантированно убивает процесс (в отличие от SIGTERM)
                if hasattr(signal, 'SIGKILL'):
                    os.kill(self._process.pid, signal.SIGKILL)
                else:
                    self._process.kill()
            except ProcessLookupError:
                pass  # процесс уже завершился
            try:
                if self._process.stdout:
                    self._process.stdout.close()
            except Exception:
                pass
