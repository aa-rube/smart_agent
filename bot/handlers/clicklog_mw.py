# smart_agent/bot/handlers/clicklog_mw.py
from __future__ import annotations
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message
import bot.utils.database as app_db

MAX_LEN = 256

def _compact(s: str, max_len: int = MAX_LEN) -> str:
    if not s:
        return ""
    s = " ".join(s.split())  # схлопываем все пробелы/переносы
    return s[:max_len]

class CallbackClickLogger(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[CallbackQuery, Dict[str, Any]], Awaitable[Any]],
        event: CallbackQuery,
        data: Dict[str, Any]
    ) -> Any:
        try:
            return await handler(event, data)
        finally:
            uid = event.from_user.id if event.from_user else None
            if uid:
                raw = event.data or ""
                app_db.event_add(user_id=uid, text=f"CB:{_compact(raw)}")

class MessageLogger(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any]
    ) -> Any:
        try:
            return await handler(event, data)
        finally:
            uid = event.from_user.id if event.from_user else None
            if not uid:
                return
            # Берём приоритетно text, затем caption; для команд — логируем как введено
            raw = event.text or event.caption or ""
            if not raw:
                # Неформатируемые типы: фото/видео/голос и т.п.
                kind = (event.content_type or "unknown").upper()
                app_db.event_add(user_id=uid, text=f"MSG:{kind}")
                return
            app_db.event_add(user_id=uid, text=f"TEXT:{_compact(raw)}")
