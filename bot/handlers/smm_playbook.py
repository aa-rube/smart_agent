# smart_agent/bot/handlers/smm_playbook.py
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    InputMediaPhoto,
)

from bot.config import get_file_path
from bot.utils import database as app_db
from bot.utils import billing_db
from bot.utils.mailing import send_last_3_published_to_user

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# ТЕКСТЫ / КЛАВИАТУРЫ
# ──────────────────────────────────────────────────────────────────────────────

SMM_DESCRIPTION = (
    "Готовый контент для риэлторов и агентств недвижимости.\n"
    "Мемы, видео, сторис и профессиональные комментарии к новостям рынка.\n"
    "📲 Каждый день в 09:00 по МСК ты получаешь новый пост — тебе остаётся только выложить в свои соцсети.\n"
    "Никакого ИИ — всё создаёт маркетолог с опытом в недвижимости.\n"
    "✅ 30 постов и рассылок в месяц\n"
    "✅ Контент для WhatsApp, Telegram, ВКонтакте, Instagram, YouTube, TikTok\n"
    "💼 Экономь время и получай больше заявок!\n\n"
    "🎁 Подпишись на 3 дня за 1 рубль!"
)

def kb_smm_subscribe() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🎁 Оформить подписку", callback_data="show_rates")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")],
        ]
    )

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")]]
    )

# ──────────────────────────────────────────────────────────────────────────────
# ЛОКАЛЬНЫЕ ХЕЛПЕРЫ
# ──────────────────────────────────────────────────────────────────────────────

async def _edit_text_safe(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Безопасное редактирование текущего сообщения (text/caption/reply_markup)."""
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        try:
            await cb.message.edit_caption(caption=text, reply_markup=kb)
        except TelegramBadRequest:
            try:
                await cb.message.edit_reply_markup(reply_markup=kb)
            except TelegramBadRequest:
                pass
    await cb.answer()

async def _edit_or_replace_with_photo_cb(
    callback: CallbackQuery,
    image_rel_path: str,
    caption: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Меняет текущий экран на фото с подписью (edit_media). Если нельзя — удаляет и шлёт новое фото.
    Фоллбэк — редактирование текста.
    """
    img_path = get_file_path(image_rel_path)
    if Path(img_path).exists():
        media = InputMediaPhoto(media=FSInputFile(img_path), caption=caption)
        try:
            await callback.message.edit_media(media=media, reply_markup=kb)
            await callback.answer()
            return
        except TelegramBadRequest:
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.bot.send_photo(
                chat_id=callback.message.chat.id,
                photo=FSInputFile(img_path),
                caption=caption,
                reply_markup=kb,
            )
            await callback.answer()
            return
        except Exception:
            logger.exception("Failed to edit/send SMM photo")

    await _edit_text_safe(callback, caption, kb)

async def _init_user_from_cb(cb: CallbackQuery) -> None:
    """Гарантируем наличие пользователя в БД по данным колбэка."""
    try:
        tg_from = cb.from_user
        if tg_from:
            app_db.check_and_add_user(tg_from.id, chat_id=cb.message.chat.id, username=tg_from.username or None)
    except Exception:
        logger.debug("init user skipped", exc_info=True)

# ──────────────────────────────────────────────────────────────────────────────
# SMM FLOW
# ──────────────────────────────────────────────────────────────────────────────

async def smm_content(cb: CallbackQuery) -> None:
    """
    Точка входа «SMM контент».
    Если есть доступ (триал или привязанная карта) — отправляем 3 последних примера постов.
    Иначе — показываем промо-экран SMM с кнопкой к тарифам.
    """
    await _init_user_from_cb(cb)
    user_id = cb.from_user.id

    try:
        has_access = app_db.is_trial_active(user_id) or billing_db.has_saved_card(user_id)
    except Exception as e:
        logger.warning("Access check failed for %s: %s", user_id, e)
        has_access = False

    if has_access:
        try:
            await cb.message.delete()
        except TelegramBadRequest:
            pass
        except Exception as e:
            logger.warning("Failed to delete triggering message for %s: %s", user_id, e)

        try:
            await send_last_3_published_to_user(cb.bot, user_id)
        except Exception as e:
            logger.warning("Failed to send last 3 published mailings to %s: %s", user_id, e)

        try:
            await cb.bot.send_message(
                chat_id=user_id,
                text="Чтобы вернуться в главное меню, нажмите «Назад».",
                reply_markup=back_kb(),
            )
        except Exception as e:
            logger.warning("Failed to send back prompt to %s: %s", user_id, e)

        try:
            await cb.answer()
        except Exception:
            pass
        return

    await _edit_or_replace_with_photo_cb(
        cb,
        image_rel_path="img/bot/smm.png",
        caption=SMM_DESCRIPTION,
        kb=kb_smm_subscribe(),
    )

# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC: онбординг SMM после успешной оплаты (вызов из payment_handler)
# ──────────────────────────────────────────────────────────────────────────────

async def send_onboarding_after_payment(bot: Bot, user_id: int) -> None:
    """
    Короткая памятка + примеры сразу после оплаты.
    Порядок важен: сначала текст про 09:00, затем примеры (чтобы «загляните ниже» было корректно).
    """
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                "Что дальше:\n"
                "• Каждый день в 09:00 (МСК) вы получаете новый пост.\n"
            ),
        )
    except Exception:
        logger.warning("Failed to send SMM onboarding text", exc_info=True)

    try:
        await send_last_3_published_to_user(bot, user_id)
    except Exception:
        logger.warning("Failed to send SMM examples", exc_info=True)

# ──────────────────────────────────────────────────────────────────────────────
# ROUTER
# ──────────────────────────────────────────────────────────────────────────────

def router(rt: Router) -> None:
    rt.callback_query.register(smm_content, F.data == "smm_content")
