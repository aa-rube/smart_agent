# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\mailing.py
# Всегда без "поддержки старых версий": никаких file_ids в payload, только payload["items"] для media_group.
# Секрет офигенного бота: никакие anchors не нужны (тут они и не используются).

from __future__ import annotations

import logging
from typing import Dict, Any, List
from zoneinfo import ZoneInfo
from datetime import datetime

from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo

import bot.utils.admin_db as adb

MSK = ZoneInfo("Europe/Moscow")


CHUNK_SIZE = 10  # Telegram ограничивает медиа-группу 10 элементами


def _chunk(items: List[Any], n: int = CHUNK_SIZE) -> List[List[Any]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def _build_media_group(items: List[Dict[str, str]], caption: str | None) -> List[List[InputMediaPhoto | InputMediaVideo]]:
    """
    Возвращает список "чанков" медиа-объектов для последовательных вызовов send_media_group.
    Подпись ставится только на первый элемент каждого чанка (ограничение Telegram).
    """
    result: List[List[InputMediaPhoto | InputMediaVideo]] = []
    for chunk in _chunk(items, CHUNK_SIZE):
        media_chunk: List[InputMediaPhoto | InputMediaVideo] = []
        for idx, it in enumerate(chunk):
            t = (it.get("type") or "photo").lower()
            fid = it["file_id"]
            cap = caption if (idx == 0 and caption) else None
            if t == "video":
                media_chunk.append(InputMediaVideo(media=fid, caption=cap))
            else:
                media_chunk.append(InputMediaPhoto(media=fid, caption=cap))
        result.append(media_chunk)
    return result


async def send_to_user(bot: Bot, user_id: int, mailing: Dict[str, Any]) -> None:
    """
    Отправляет ОДНУ публикацию ОДНОМУ пользователю, согласно структуре записи из БД.
    Ожидаемый формат:
      mailing = {
        "content_type": "text|photo|video|audio|animation|media_group",
        "caption": str | None,
        "payload": dict  # для text: {"text": "..."}; для single-media: {"file_id":"..."}; для media_group: {"items":[{"type":"photo|video","file_id":"..."}]}
      }
    """
    ctype: str = mailing["content_type"]
    caption: str | None = mailing.get("caption")
    payload: Dict[str, Any] = mailing.get("payload") or {}

    if ctype == "text":
        await bot.send_message(user_id, payload.get("text", ""))
        return

    if ctype == "photo":
        await bot.send_photo(user_id, payload["file_id"], caption=caption or None)
        return

    if ctype == "video":
        await bot.send_video(user_id, payload["file_id"], caption=caption or None)
        return

    if ctype == "audio":
        await bot.send_audio(user_id, payload["file_id"], caption=caption or None)
        return

    if ctype == "animation":
        await bot.send_animation(user_id, payload["file_id"], caption=caption or None)
        return

    if ctype == "media_group":
        items: List[Dict[str, str]] = payload.get("items") or []
        if not items:
            # Пустой альбом — просто ничего не шлём
            return
        for media_chunk in _build_media_group(items, caption):
            await bot.send_media_group(user_id, media_chunk)
        return

    # Неизвестный тип — молча пропускаем


async def preview_to_chat(bot: Bot, chat_id: int, mailing: Dict[str, Any]) -> None:
    """
    Превью публикации в админ-чате. Просто используем тот же механизм, что и для пользователя.
    """
    await send_to_user(bot, chat_id, mailing)


async def broadcast(bot: Bot, mailing: Dict[str, Any], user_ids: List[int]) -> None:
    """
    Отправляет одну запись сразу списку пользователей.
    Ошибки по отдельным пользователям логируем в stdout, остальные не блокируем.
    """
    for uid in user_ids:
        try:
            await send_to_user(bot, int(uid), mailing)
        except Exception as e:
            print(f"[mailing] send error to {uid}: {e}")


async def run_mailing_scheduler(bot: Bot) -> None:
    """
    Вызывать из внешнего планировщика (APScheduler/cron).
    Берёт все Mailings, у которых:
      - mailing_on = 1
      - mailing_completed = 0
      - publish_at <= NOW()
    Шлёт подписчикам и помечает как выполненные.
    """
    pending = adb.get_pending_mailings()
    logging.info("[mailing] %s pending at %s MSK", len(pending), datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S"))

    if not pending:
        return

    user_ids = adb.get_active_user_ids()
    logging.info("[mailing] recipients=%s", len(user_ids))

    if not user_ids:
        for m in pending:
            adb.mark_mailing_completed(m["id"])
            logging.info("[mailing] no recipients → mark completed id=%s", m["id"])
        return

    for m in pending:
        await broadcast(bot, m, user_ids)
        adb.mark_mailing_completed(m["id"])
        logging.info("[mailing] completed id=%s", m["id"])
