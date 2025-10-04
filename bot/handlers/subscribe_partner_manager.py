# smart_agent/bot/handlers/subscribe_partner_manager.py
#Всегда пиши код без «поддержки старых версий». Если они есть - удаляй.

from __future__ import annotations

import logging
from typing import List, Dict, Union, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramAPIError
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Router, F

from bot.config import PARTNER_CHANNELS
from bot.handlers.payment_handler import build_trial_offer


# статусы, трактуемые как "подписан"
OK_STATUSES = {"creator", "administrator", "member"}


you_have_to_subscribe = ('''
👋 Привет! Это «Инструменты Риэлтора». Подпишись на наш канал, там тоже много полезного!
''')

# Текст, на который переключаемся при повторной проверке (редактирование по msg_id)
you_have_to_subscribe_retry = (
    "📢 Один шаг до старта, подпишись  👉 t.me/setrealtora и нажми кнопку ниже.\n\n"
    " • Кнопка: ✅ Проверить подписку"
)

# Единый колбэк для проверки подписок по кнопке
PARTNER_CHECK_CB = "partners.check"


def build_missing_subscribe_keyboard(
        channels: List[Dict[str, Union[int, str]]],
        sub_map: Dict[int, bool],
        *,
        retry_callback_data: Optional[str] = None,
        retry_button_text: str = "✅ Проверить подписку",
        columns: int = 1,
) -> InlineKeyboardMarkup:
    """
    Строит клавиатуру ТОЛЬКО по отсутствующим подпискам.
    Кнопка = URL из конфига, текст = label из конфига.
    """
    columns = max(1, min(columns, 4))
    rows: list[list[InlineKeyboardButton]] = []
    line: list[InlineKeyboardButton] = []

    for cfg in channels:
        chat_id: int = cfg["chat_id"]
        if sub_map.get(chat_id, True):
            continue  # уже подписан — кнопку не показываем

        url: str = cfg["url"]  # если нет — упадёт (ошибка данных), это ок
        label: str = str(cfg.get("label") or "Канал")

        btn = InlineKeyboardButton(text=f"Подписаться → {label}", url=url)

        if columns == 1:
            rows.append([btn])
        else:
            line.append(btn)
            if len(line) >= columns:
                rows.append(line)
                line = []

    if columns > 1 and line:
        rows.append(line)

    if retry_callback_data:
        rows.append([InlineKeyboardButton(text=retry_button_text, callback_data=retry_callback_data)])
        # rows.append([InlineKeyboardButton(text="❗️ Не подписываться", callback_data="skip_subscribe")])

    return InlineKeyboardMarkup(inline_keyboard=rows)



async def _is_subscribed(bot: Bot, chat_id: int, user_id: int) -> bool:
    """
    Возвращает True, если пользователь состоит в канале/группе.

    Оптимистичный режим: если проверить статусы невозможно (бот не в канале,
    не админ, нет прав, временная ошибка Telegram и пр.), возвращаем True,
    чтобы не блокировать пользователя из-за ошибки конфигурации.
    """
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        return getattr(member, "status", None) in OK_STATUSES

    except (TelegramBadRequest, TelegramForbiddenError, TelegramAPIError) as e:
        logging.warning("Membership check skipped (access/API): chat=%s user=%s err=%s", chat_id, user_id, e)
        return True

    except Exception as e:
        logging.exception("Membership check unexpected error: chat=%s user=%s", chat_id, user_id)
        return True


async def get_partner_subscription_map(
    bot: Bot,
    user_id: int,
    channels: Optional[List[Dict[str, Union[int, str]]]] = None,
) -> Dict[int, bool]:
    """
    Возвращает {chat_id: True/False} по всем каналам из списка.
    Ожидается список словарей вида:
      [{"chat_id": int, "url": str, "label": str}, ...]
    """
    items = channels if channels is not None else PARTNER_CHANNELS
    result: Dict[int, bool] = {}

    # никаких нормализаций — chat_id ДОЛЖЕН быть int
    for cfg in items:
        chat_id = cfg["chat_id"]  # если тут не int -> упадёт, и это ок (ошибка разработчика)
        result[chat_id] = await _is_subscribed(bot, chat_id, user_id)

    return result


def all_subscribed(sub_map: Dict[int, bool]) -> bool:
    """True, если нет ни одного False."""
    return all(sub_map.values()) if sub_map else True


async def _edit_text_or_caption(message: Message, text: str, kb=None) -> None:
    """
    Пытаемся обновить текущее сообщение:
    1) edit_text
    2) edit_caption (если это медиа)
    3) edit_reply_markup (если текст/подпись менять нельзя)
    """
    try:
        await message.edit_text(text, reply_markup=kb)
        return
    except TelegramBadRequest:
        pass

    try:
        await message.edit_caption(caption=text, reply_markup=kb)
        return
    except TelegramBadRequest:
        pass

    try:
        await message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        # уже нечего редактировать — игнорируем
        pass


async def ensure_partner_subs(
    bot: Bot,
    event: Union[Message, CallbackQuery],
    *,
    retry_callback_data: Optional[str] = None,
    channels: Optional[List[Dict[str, Union[int, str]]]] = None,
    columns: int = 1,
) -> bool:
    """
    Проверяет подписки на ВСЕ каналы.
    Если чего-то не хватает — показывает клавиатуру с недостающими.
    Для CallbackQuery — РЕДАКТИРУЕТ текущее сообщение (не отправляет новое).
    Возвращает False, если подписки не полные; True — если всё ок.
    """
    if isinstance(event, CallbackQuery):
        user_id = event.from_user.id
        reply_msg = event.message
    else:
        user_id = event.from_user.id
        reply_msg = event

    items = channels if channels is not None else PARTNER_CHANNELS
    if not items:
        return True

    sub_map = await get_partner_subscription_map(bot, user_id, items)

    if all_subscribed(sub_map):
        return True

    kb = build_missing_subscribe_keyboard(
        items,
        sub_map,
        retry_callback_data=retry_callback_data,
        retry_button_text="✅ Проверить подписку",
        columns=columns,
    )

    if isinstance(event, CallbackQuery) and reply_msg:
        # для callback — обновляем текущее сообщение
        # При повторной проверке меняем текст по msg_id на «второй» вариант
        await _edit_text_or_caption(reply_msg, you_have_to_subscribe_retry, kb)
        await event.answer()  # закрыть "часики"
    else:
        # для обычного Message — отправляем новое (как и раньше)
        await reply_msg.answer(you_have_to_subscribe, reply_markup=kb)

    return False


# ─────────────────────────────────────────────────────────────────────────────
# Новый публичный колбэк-хендлер «Проверить подписку»
# Оставляем старый ensure_partner_subs для первого показа из /start (Message),
# этот — для повторных проверок по кнопке (CallbackQuery).
# ─────────────────────────────────────────────────────────────────────────────
async def partner_check_cb(callback: CallbackQuery, bot: Bot) -> None:
    """
    Повторная проверка подписки по кнопке. Редактирует текущее сообщение:
      • если не подписан — показывает второй текст и кнопку «✅ Проверить подписку»
      • если подписан — просто подтверждает (дальнейшие шаги — в вызывающем сценарии)
    """
    ok = await ensure_partner_subs(
        bot=bot,
        event=callback,
        retry_callback_data=PARTNER_CHECK_CB,
        columns=1,
    )
    if ok:
        # Подписка подтверждена — сразу предлагаем оффер «3 дня за 1 ₽»
        await callback.answer("Подписка подтверждена ✅", show_alert=False)
        text, kb = build_trial_offer(callback.from_user.id)
        await _edit_text_or_caption(callback.message, text, kb)


def router(rt: Router) -> None:
    """
    Роутер только для кнопки повторной проверки подписки.
    Первый показ выполняется там, где вызывают ensure_partner_subs(...) из /start.
    """
    rt.callback_query.register(partner_check_cb, F.data == PARTNER_CHECK_CB)
