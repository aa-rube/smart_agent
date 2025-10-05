# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\mailing.py

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
from bot.utils.redis_repo import set_nx_with_ttl


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


async def _send_mailing(bot: Bot, user_id: int, r) -> None:
    """
    Отправляет один пост пользователю из результата запроса к БД.
    r может быть dict или tuple в зависимости от способа получения данных.
    """
    if isinstance(r, dict):
        mailing = r
    else:
        # Если r - это tuple из fetchall(), преобразуем в dict
        mailing = {
            "id": r[0],
            "content_type": r[1],
            "caption": r[2],
            "payload": r[3] if isinstance(r[3], dict) else {}
        }
    
    await send_to_user(bot, user_id, mailing)


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


async def send_last_3_published_to_user(bot: Bot, user_id: int) -> bool:
    """
    Отправляет пользователю последние 3 поста, у которых publish_at <= now (МСК),
    строго от самого свежего к более ранним; затем — ОДНО сообщение с кнопкой «Назад».
    Маркеры (mailing_on / mailing_completed) игнорируются.
    """
    now = datetime.now(timezone.utc)
    try:
        rows = adb.get_last_3_published_mailings(now)
    except Exception as e:
        logging.exception("get_last_3_published_mailings failed: %s", e)
        return False

    if not rows:
        return False

    for r in rows:
        try:
            await send_to_user(bot, user_id, r)
        except Exception as e:
            logging.warning("[mailing] failed to send one of last 3 posts to %s: %s", user_id, e)

    return True


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
    logging.info(
        "[mailing] pending=%s at %s MSK",
        len(pending),
        datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S"),
    )

    if not pending:
        return

    # Получатели = активный триал ИЛИ активная подписка (next_charge_at в будущем)
    now_utc = datetime.now(timezone.utc)
    user_ids = _collect_recipients(now_utc)
    logging.info(
        "[mailing] recipients total=%s",
        len(user_ids),
    )

    if not user_ids:
        for m in pending:
            adb.mark_mailing_completed(m["id"])
            logging.info("[mailing] no recipients → mark completed id=%s", m["id"])
        return

    for m in pending:
        await broadcast(bot, m, user_ids)
        adb.mark_mailing_completed(m["id"])
        logging.info("[mailing] completed id=%s", m["id"])


def _collect_recipients(now_utc: datetime) -> List[int]:
    """
    Возвращает отсортированный список user_id, которые должны получить рассылку «на сейчас»:
    пользователи с активным триалом ИЛИ с активной подпиской.
    Вход: now_utc — aware datetime в UTC.
    """
    try:
        trial_ids = set(app_db.list_trial_active_user_ids(now_utc))
    except Exception:
        logging.exception("[mailing] list_trial_active_user_ids failed")
        trial_ids = set()
    try:
        paid_ids = set(billing_db.list_active_subscription_user_ids(now_utc))
    except Exception:
        logging.exception("[mailing] list_active_subscription_user_ids failed")
        paid_ids = set()
    all_ids = sorted(trial_ids | paid_ids)
    # подробный лог только при наличии получателей (чтобы не засорять)
    if all_ids:
        logging.info("[mailing] recipients breakdown: trial=%s, paid=%s, total=%s",
                     len(trial_ids), len(paid_ids), len(all_ids))
    return all_ids


# ──────────────────────────────────────────────────────────────────────────────
# Trial Engagement D2/D3 (пуши по триалу на 2-й и 3-й день)
# ──────────────────────────────────────────────────────────────────────────────
_D2_KEY_FMT = "trial_pushing:{uid}:1"  # Пуш на 24ч
_D3_KEY_FMT = "trial_pushing:{uid}:2"  # Пуш на 48ч
_DAYS_TTL_SEC = 14 * 24 * 3600

_TEXT_D2 = "Создай описание объекта за 1 минуту."
_TEXT_D3 = "Бот делает из убитой хрущевки шедевр! Ты уже пробовал?"


async def run_trial_engagement_scheduler(bot: Bot) -> None:
    """
    Планировщик пушей «Вовлечение во время триала»:
      • День 2 → пуш: _TEXT_D2
      • День 3 → пуш: _TEXT_D3

    Логика анти-спама: ставим маркеры в Redis на 14 дней:
      trial_pushing:{user_id}:1 — отправлен D2
      trial_pushing:{user_id}:2 — отправлен D3
    """
    now = datetime.now(timezone.utc)

    # Базовый пул – «активные клиенты»: кто в принципе может получать рассылки (карта/подписка ок).
    try:
        user_ids = billing_db.list_mailing_eligible_users(now)
    except Exception:
        logging.exception("[trial_engagement] list_mailing_eligible_users failed")
        user_ids = []

    if not user_ids:
        logging.info("[trial_engagement] no eligible users at %s", now.isoformat())
        return

    # Время старта триала по первой привязке карты
    try:
        started_map = billing_db.list_trial_started_map(user_ids)
    except Exception:
        logging.exception("[trial_engagement] list_trial_started_map failed")
        return

    sent_d2 = 0  # 24h
    sent_d3 = 0  # 48h

    for uid, started_at in started_map.items():
        if not started_at:
            continue
        hours = (now - started_at).total_seconds() / 3600.0

        # 24 часа (и не отправляли ранее)
        if hours >= 24:
            key = _D2_KEY_FMT.format(uid=uid)
            try:
                need_send = await set_nx_with_ttl(key, "1", _DAYS_TTL_SEC)
            except Exception:
                logging.exception("[trial_engagement] redis setnx 24h failed for %s", uid)
                need_send = False
            if need_send:
                try:
                    await bot.send_message(uid, _TEXT_D2)
                    sent_d2 += 1
                except Exception as e:
                    logging.warning("[trial_engagement] send 24h to %s failed: %s", uid, e)

        # 48 часов (и не отправляли ранее)
        if hours >= 48:
            key = _D3_KEY_FMT.format(uid=uid)
            try:
                need_send = await set_nx_with_ttl(key, "1", _DAYS_TTL_SEC)
            except Exception:
                logging.exception("[trial_engagement] redis setnx 48h failed for %s", uid)
                need_send = False
            if need_send:
                try:
                    await bot.send_message(uid, _TEXT_D3, disable_web_page_preview=True)
                    sent_d3 += 1
                except Exception as e:
                    logging.warning("[trial_engagement] send 48h to %s failed: %s", uid, e)

    logging.info("[trial_engagement] done: sent_24h=%s, sent_48h=%s, eligible=%s", sent_d2, sent_d3, len(user_ids))
