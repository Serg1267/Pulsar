# -*- coding: utf-8 -*-
"""SerializationMixin — сохранение/загрузка .sch файлов."""

from __future__ import annotations


class SerializationMixin:
    """Миксин: сериализация схемы в .sch (JSON)."""

    def save_sch(self, filepath: str):
        """Сохранить схему в .sch (JSON)."""
        from EDA.app.serializer import save_sch as _save
        _save(self, filepath)

    def load_sch(self, filepath: str):
        """Загрузить схему из .sch (JSON), очистив текущую."""
        from EDA.app.serializer import load_sch as _load
        _load(self, filepath)
