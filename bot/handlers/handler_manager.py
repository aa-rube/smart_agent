# smart_agent/bot/handlers/handler_manager.py
#Всегда пиши код без «поддержки старых версий». Если они есть в еодк - удаляй.

from __future__ import annotations

import logging
from pathlib import Path
from typing import Union

from aiogram import Router, F, Bot
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    InputMediaPhoto,
)

import bot.utils.database as db
import bot.utils.tokens as tk
from bot.config import get_file_path
from bot.utils.subscribe_partner_manager import ensure_partner_subs
from bot.handlers.payment_handler import show_rates as show_rates_handler

# =============================================================================
# Тексты
# =============================================================================
frst_text = (
    "👋 Привет!\n"
    "Добро пожаловать в *ИНСТРУМЕНТЫ РИЭЛТОРА*.\n"
    "Ты получил доступ к сервисам, которые помогают экономить время и привлекать больше клиентов.\n\n"
    "Выбери нужный инструмент 👇\n\n"
    "🏡 *Контент для соцсетей риелтора* — готовые публикации и идеи по подписке, чтобы регулярно вести свои соцсети.\n\n"
    "🧠 *Продвинутые инструменты* для лучших продаж и привлечения клиентов.\n\n"
    "✨ А так же наше закрытое сообщество для обсуждения, поддержки и обмена опытом."
)

ai_tools_text = (
    "📐 *Генератор красивых планировок* (*β-версия*) — создавай наглядные схемы квартир и домов.\n\n"
    "🛋️ *Генератор дизайна интерьера* — быстрые визуализации стиля и меблировки.\n\n"
    "🤖 *ИИ для закрытия возражений* — готовые аргументы и ответы на частые сомнения клиентов.\n\n"
    "✍️ *ИИ для написания отзывов от клиентов* — шаблоны благодарственных сообщений."
)

smm_description = (
    "📲 Наша SMM-команда ежедневно готовит профессиональный контент, который остаётся только опубликовать.\n"
    "Никакого ИИ — только опытные маркетологи с практикой в недвижимости.\n\n"
    "В течение месяца ты получишь:\n\n"
    "26 готовых тем для соцсетей и мессенджеров.\n\n"
    "Посты для ВКонтакте, Telegram, Instagram, Одноклассники.\n\n"
    "Сторис и истории для WhatsApp, Telegram, ВКонтакте, Instagram.\n\n"
    "Короткие ролики для WhatsApp, Telegram, Shorts, Reels, TikTok, ВКонтакте.\n\n"
    "💼 Всё создано, чтобы ты экономил время и получал заявки из своих соцсетей.\n\n"
    "🔐 Доступ только для подписчиков.\n"
    "Нажми «Оформить подписку» и пользуйся Всеми Инструментами Риэлтора без ограничений!"
)

HELP = "🆘 Нажмите на кнопку, чтобы обратиться в поддержку 👇"

# =============================================================================
# Клавиатуры
# =============================================================================
frst_kb_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🏡 Контент для соцсетей риелтора", callback_data="smm_content")],
        [InlineKeyboardButton(text="🧠 Продвинутые инструменты", callback_data="nav.ai_tools")],
        [InlineKeyboardButton(text="📐Планировки (Тестовая версия)", callback_data="floor_plan")],
        [InlineKeyboardButton(text="Наше сообщество", url="https://t.me/setrealtora")],
        [InlineKeyboardButton(text="Тех. поддержка", url="https://t.me/dashaadminrealtor")],
    ]
)

ai_tools_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🛋️ Генератор дизайна интерьера", callback_data="nav.design_home")],
        [InlineKeyboardButton(text="🤖 ИИ для закрытия возражений", callback_data="nav.objection_start")],
        [InlineKeyboardButton(text="✍️ ИИ для написания отзывов от клиентов", callback_data="nav.feedback_home")],
        [InlineKeyboardButton(text="✨ Summary диалога с клиентом", callback_data="nav.summary_home")],
        [InlineKeyboardButton(text="💎 Генератор продающих описаний объектов", callback_data="nav.descr_home")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")],
    ]
)

get_smm_subscribe_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")]
    ]
)


def help_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛟 Поддержка", url="https://t.me/admrecontent")
    return builder.as_markup()


# =============================================================================
# Инициализация пользователя
# =============================================================================
async def init_user_event(evt: Union[Message, CallbackQuery]) -> None:
    """
    Гарантирует, что пользователь есть в БД (дефолты ставятся в repo.ensure_user).
    Работает и для входящих сообщений, и для callback’ов.
    """
    if isinstance(evt, CallbackQuery):
        msg = evt.message
        username = evt.from_user.username if evt.from_user else ""
    else:
        msg = evt
        username = evt.from_user.username if evt.from_user else ""

    if not msg:
        return

    user_id = msg.chat.id

    # Основная БД: гарантируем пользователя и дефолты
    if not db.check_and_add_user(user_id):
        db.set_variable(user_id, "tokens", 2)
        db.set_variable(user_id, "have_sub", 0)
        # Примечание: отдельная admin_db больше не требуется


# =============================================================================
# Общие хелперы UI
# =============================================================================
async def _edit_text_safe(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Безопасно редактирует текст/подпись/клавиатуру текущего сообщения."""
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


async def send_menu_with_logo(bot: Bot, chat_id: int) -> None:
    """
    Главный экран одним сообщением: фото-логотип + caption + клавиатура.
    Фоллбэк — просто текст.
    """
    logo_rel = "img/bot/logo.png"  # путь внутри DATA_DIR
    logo_path = get_file_path(logo_rel)
    if Path(logo_path).exists():
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(logo_path),
                caption=frst_text,
                reply_markup=frst_kb_inline,
            )
            return
        except Exception as e:
            logging.exception("Failed to send logo with caption: %s", e)
    else:
        logging.warning("Logo not found: %s (resolved from %s)", logo_path, logo_rel)

    await bot.send_message(chat_id=chat_id, text=frst_text, reply_markup=frst_kb_inline)


async def _replace_with_menu_with_logo(callback: CallbackQuery) -> None:
    """
    Пытаемся обновить текущее сообщение на главное меню (фото + caption) БЕЗ удаления.
    1) edit_media (если было фото)
    2) edit_caption (если была подпись к медиа)
    3) edit_text (если было текстовое)
    Фоллбэк: отправляем новое сообщение с меню, старое не трогаем.
    """
    logo_rel = "img/bot/logo.png"
    logo_path = get_file_path(logo_rel)

    # Путь к картинке существует — пробуем заменить медиа
    if Path(logo_path).exists():
        try:
            media = InputMediaPhoto(media=FSInputFile(logo_path), caption=frst_text)
            await callback.message.edit_media(media=media, reply_markup=frst_kb_inline)
            await callback.answer()
            return
        except TelegramBadRequest:
            # Сообщение могло быть не медийным — пробуем обновить подпись
            try:
                await callback.message.edit_caption(caption=frst_text, reply_markup=frst_kb_inline)
                await callback.answer()
                return
            except TelegramBadRequest:
                # Как минимум заменим текст и клавиатуру
                try:
                    await callback.message.edit_text(frst_text, reply_markup=frst_kb_inline)
                    await callback.answer()
                    return
                except TelegramBadRequest:
                    pass
        except Exception as e:
            logging.exception("Failed to edit current message with logo: %s", e)
    else:
        logging.warning("Logo not found: %s (resolved from %s)", logo_path, logo_rel)

    # Финальный фоллбэк — просто отправим новое сообщение с меню, не удаляя старое
    await send_menu_with_logo(callback.bot, callback.message.chat.id)
    await callback.answer()


async def _edit_or_replace_with_photo_cb(
        callback: CallbackQuery,
        image_rel_path: str,
        caption: str,
        kb: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Меняет текущий экран на фото с подписью (через edit_media).
    Если редактировать нельзя (было текстовое сообщение) — удаляет и отправляет новое фото.
    Фоллбэк — редактирование текста.
    """
    img_path = get_file_path(image_rel_path)
    if Path(img_path).exists():
        media = InputMediaPhoto(media=FSInputFile(img_path), caption=caption)
        try:
            # пробуем заменить медиаконтент текущего сообщения
            await callback.message.edit_media(media=media, reply_markup=kb)
            await callback.answer()
            return
        except TelegramBadRequest:
            # если сообщение было текстом — удаляем и отправляем новое фото
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
        except Exception as e:
            logging.exception("Failed to edit/send photo for ai_tools: %s", e)

    # если файла нет или всё упало — хотя бы текстом
    await _edit_text_safe(callback, caption, kb)


# =============================================================================
# /start и основной экран
# =============================================================================
async def frst_msg(message: Message, bot: Bot) -> None:
    await init_user_event(message)

    user_id = message.chat.id
    skip = db.get_variable(user_id, "skip_subscribe")

    if not skip:
        # проверка партнёрских подписок (если не подписан — покажем ссылки и выйдем)
        if not await ensure_partner_subs(bot, message, retry_callback_data="start_retry", columns=2):
            return

    # главный экран: фото + caption в одном сообщении
    await send_menu_with_logo(bot, user_id)


# =============================================================================
# Колбэки
# =============================================================================
async def ai_tools(callback: CallbackQuery) -> None:
    """
    Переход в раздел «Продвинутые инструменты»:
    меняем текущий экран на картинку ai_tools.png + подпись + клавиатуру.
    """
    await init_user_event(callback)
    await _edit_or_replace_with_photo_cb(
        callback=callback,
        image_rel_path="img/bot/ai_tools.png",  # путь внутри DATA_DIR
        caption=ai_tools_text,
        kb=ai_tools_inline,
    )


async def check_subscribe_retry(callback: CallbackQuery, bot: Bot) -> None:
    await init_user_event(callback)

    if not await ensure_partner_subs(bot, callback, retry_callback_data="start_retry", columns=2):
        await callback.answer("Похоже, ещё не на все каналы подписаны 🤏", show_alert=True)
        return

    await _replace_with_menu_with_logo(callback)


async def skip_subscribe(callback: CallbackQuery) -> None:
    await init_user_event(callback)

    user_id = callback.from_user.id
    db.set_variable(user_id, "tokens", 0)
    db.set_variable(user_id, "skip_subscribe", True)

    await _replace_with_menu_with_logo(callback)


async def smm_content(callback: CallbackQuery) -> None:
    await init_user_event(callback)
    await _edit_or_replace_with_photo_cb(callback, image_rel_path="img/bot/smm.png", caption=smm_description,
                                         kb=get_smm_subscribe_inline)


async def my_profile(callback: CallbackQuery) -> None:
    await init_user_event(callback)

    user_id = callback.from_user.id
    have_sub = (db.get_variable(user_id, "have_sub") == "1")
    paid_at = db.get_variable(user_id, "sub_paid_at") or "-"
    sub_until = db.get_variable(user_id, "sub_until") or "-"

    text = (
        f"Подписка: {'YES' if have_sub else 'NO'}\n"
        f"Дата оплаты подписки: {paid_at}\n"
        f"Дата окончания подписки: {sub_until}"
    )
    await _edit_text_safe(callback, text)


# =============================================================================
# Команды
# =============================================================================
async def sub_cmd(message: Message) -> None:
    await init_user_event(message)
    # централизованный показ тарифов/оплаты
    await show_rates_handler(message)


async def help_cmd(message: Message) -> None:
    await init_user_event(message)
    await message.answer(HELP, reply_markup=help_kb())


async def add_tokens(message: Message) -> None:
    await init_user_event(message)
    user_id = message.chat.id
    tk.add_tokens(user_id, 100)
    await message.answer("Added 100 tokens, be happy")


# =============================================================================
# Router
# =============================================================================
def router(rt: Router) -> None:
    # messages
    rt.message.register(frst_msg, CommandStart())
    rt.message.register(sub_cmd, Command("sub"))
    rt.message.register(add_tokens, Command("add"))
    rt.message.register(frst_msg, Command("main"))
    rt.message.register(help_cmd, Command("support"))

    # callbacks
    rt.callback_query.register(ai_tools, F.data == "nav.ai_tools")
    rt.callback_query.register(check_subscribe_retry, F.data == "start_retry")
    rt.callback_query.register(skip_subscribe, F.data == "skip_subscribe")
    rt.callback_query.register(my_profile, F.data == "my_profile")
    rt.callback_query.register(smm_content, F.data == "smm_content")
