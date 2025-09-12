# smart_agent/bot/handlers/subscribe_partner_manager.py

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramAPIError
from aiogram.types import Message, CallbackQuery

from bot.text.texts import *
from bot.keyboards.inline import *
from bot.config import PARTNER_CHANNELS

# статусы, трактуемые как "подписан"
OK_STATUSES = {"creator", "administrator", "member"}


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
        columns=columns,
    )

    if isinstance(event, CallbackQuery) and reply_msg:
        # для callback — обновляем текущее сообщение
        await _edit_text_or_caption(reply_msg, you_have_to_subscribe, kb)
        await event.answer()  # закрыть "часики"
    else:
        # для обычного Message — отправляем новое (как и раньше)
        await reply_msg.answer(you_have_to_subscribe, reply_markup=kb)

    return False
