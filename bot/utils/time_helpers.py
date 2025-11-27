# smart_agent/bot/utils/time_helpers.py
"""
Единые функции для работы со временем в МСК.
Все временные метки должны работать в контексте МСК.
При хранении в БД (MySQL DATETIME не хранит timezone) - конвертируем в UTC.
При чтении из БД - конвертируем naive datetime в МСК.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bot.config import TIMEZONE

# UTC для хранения в БД (MySQL DATETIME не хранит timezone)
UTC = timezone.utc


def now_msk() -> datetime:
    """Возвращает текущее время в МСК (aware datetime)."""
    return datetime.now(TIMEZONE)


def to_msk(dt: datetime) -> datetime:
    """
    Конвертирует datetime в МСК.
    Если dt naive - предполагается, что это UTC (из БД).
    """
    if dt.tzinfo is None:
        # Naive datetime из БД - предполагаем UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TIMEZONE)


def to_aware_msk(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Нормализует datetime к aware МСК.
    Если dt None - возвращает None.
    Если dt naive - предполагается, что это UTC (из БД).
    """
    if dt is None:
        return None
    return to_msk(dt)


def msk_str(dt: Optional[datetime], fmt: str = "%Y-%m-%d %H:%M") -> str:
    """
    Форматирует datetime в строку МСК.
    Если dt None - возвращает "—".
    """
    if dt is None:
        return "—"
    dt_msk = to_aware_msk(dt)
    return dt_msk.strftime(fmt) if dt_msk else "—"


def to_utc_for_db(dt: datetime) -> datetime:
    """
    Конвертирует datetime в UTC для сохранения в БД.
    Если dt naive - предполагается, что это уже МСК (но лучше передавать aware).
    """
    if dt.tzinfo is None:
        # Если naive - предполагаем МСК
        dt = dt.replace(tzinfo=TIMEZONE)
    return dt.astimezone(UTC)


def from_db_naive(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Конвертирует naive datetime из БД (который хранится как UTC) в МСК.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Naive из БД - предполагаем UTC
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(TIMEZONE)


