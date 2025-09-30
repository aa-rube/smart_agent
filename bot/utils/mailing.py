# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\mailing.py
# Всегда без "поддержки старых версий": никаких file_ids в payload, только payload["items"] для media_group.
# Секрет офигенного бота: никакие anchors не нужны (тут они и не используются).

from __future__ import annotations

import logging
from typing import Dict, Any, List
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from html import escape as _html_escape

from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo

import bot.utils.admin_db as adb
import bot.utils.database as app_db
import bot.utils.billing_db as billing_db


MSK = ZoneInfo("Europe/Moscow")


CHUNK_SIZE = 10  # Telegram ограничивает медиа-группу 10 элементами


def _chunk(items: List[Any], n: int = CHUNK_SIZE) -> List[List[Any]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def _safe_cap(caption: str | None) -> str | None:
    if not caption:
        return None
    # экранируем под HTML, чтобы никакая разметка не ломала подпись
    return _html_escape(caption, quote=False)

def _build_media_group(items: List[Dict[str, str]], caption: str | None) -> List[List[InputMediaPhoto | InputMediaVideo]]:
    """
    Возвращает список "чанков" медиа-объектов для последовательных вызовов send_media_group.
    Подпись ставится только на первый элемент каждого чанка (ограничение Telegram).
    """
    result: List[List[InputMediaPhoto | InputMediaVideo]] = []
    safe_caption = _safe_cap(caption)
    for chunk in _chunk(items, CHUNK_SIZE):
        media_chunk: List[InputMediaPhoto | InputMediaVideo] = []
        for idx, it in enumerate(chunk):
            t = (it.get("type") or "photo").lower()
            fid = it["file_id"]
            cap = safe_caption if (idx == 0 and safe_caption) else None
            if t == "video":
                media_chunk.append(InputMediaVideo(media=fid, caption=cap, parse_mode="HTML"))
            else:
                media_chunk.append(InputMediaPhoto(media=fid, caption=cap, parse_mode="HTML"))
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
        await bot.send_photo(user_id, payload["file_id"], caption=_safe_cap(caption), parse_mode="HTML")
        return

    if ctype == "video":
        await bot.send_video(user_id, payload["file_id"], caption=_safe_cap(caption), parse_mode="HTML")
        return

    if ctype == "audio":
        await bot.send_audio(user_id, payload["file_id"], caption=_safe_cap(caption), parse_mode="HTML")
        return

    if ctype == "animation":
        await bot.send_animation(user_id, payload["file_id"], caption=_safe_cap(caption), parse_mode="HTML")
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


async def send_last_published_to_user(bot: Bot, user_id: int) -> None:
    """
    Находит и отправляет пользователю последнюю уже опубликованную рассылку
    с датой publish_at <= сейчас. Ни в коем случае не «следующую».
    """
    now = datetime.now(timezone.utc)
    m = adb.get_last_published_mailing(now)
    if not m:
        return
    try:
        await send_to_user(bot, user_id, m)
    except Exception as e:
        logging.warning("[mailing] failed to send last published to %s: %s", user_id, e)


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

    # Получатели = активный триал ИЛИ активная подписка (next_charge_at в будущем)
    now = datetime.now(timezone.utc)
    trial_ids = set(app_db.list_trial_active_user_ids(now))
    paid_ids  = set(billing_db.list_active_subscription_user_ids(now))
    user_ids = sorted(trial_ids | paid_ids)
    logging.info("[mailing] recipients(trial=%s, paid=%s, total=%s)", len(trial_ids), len(paid_ids), len(user_ids))

    if not user_ids:
        for m in pending:
            adb.mark_mailing_completed(m["id"])
            logging.info("[mailing] no recipients → mark completed id=%s", m["id"])
        return

    for m in pending:
        await broadcast(bot, m, user_ids)
        adb.mark_mailing_completed(m["id"])
        logging.info("[mailing] completed id=%s", m["id"])
