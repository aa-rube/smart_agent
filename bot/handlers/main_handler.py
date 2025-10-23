# smart_agent/bot/handlers/main_handler.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Union, Optional

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

from bot.config import get_file_path, PARTNER_URL
from bot.handlers.subscribe_partner_manager import (
    ensure_partner_subs,
    PARTNER_CHECK_CB,
    is_subscribed,  # ← публичная проверка членства
)
from bot.handlers.payment_handler import (
    show_rates as show_rates_handler,
    membership_invite,  # ← вызов membership_service
)
import bot.utils.database as app_db
import bot.utils.billing_db as billing_db
from aiogram.types import User as TgUser

# Закрытый канал с готовыми постами (жёстко зашитый id)
EXAMPLES_CHAT_ID = -1003103282986
# Callback для кнопки «Подписаться на канал…»
POSTS_SUBSCRIBE_CB = "posts.subscribe_examples"


# =============================================================================
# Тексты
# =============================================================================
frst_text = '''
👋 Привет!
Добро пожаловать в *ИНСТРУМЕНТЫ РИЭЛТОРА*.
Выбирай, что нужно прямо сейчас 👇
'''
ai_tools_text = '''*Инструменты PRO* - все, что нужно для работы с клиентами и объектами недвижимости.'''
HELP = "🆘 Нажмите на кнопку, чтобы обратиться в поддержку 👇"
get_subscribe = 'Похоже, ещё не на все каналы подписаны 🤏'

# =============================================================================
# Клавиатуры
# =============================================================================
def has_active_paid_subscription(user_id: int) -> bool:
    """
    Строго «оплачено»: есть подписка status='active' и next_charge_at > сейчас (UTC).
    Триал сюда НЕ входит.
    """
    try:
        from bot.utils.billing_db import SessionLocal, Subscription
        from datetime import datetime, timezone
        with SessionLocal() as s:
            rec = (
                s.query(Subscription)
                 .filter(Subscription.user_id == user_id, Subscription.status == "active")
                 .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                 .first()
            )
            if not rec or not rec.next_charge_at:
                return False
            now_utc = datetime.now(timezone.utc)
            # next_charge_at уже timezone-aware в модели
            return rec.next_charge_at > now_utc
    except Exception:
        return False

async def build_posts_button(bot: Bot, user_id: int) -> Optional[InlineKeyboardButton]:
    """
    Возвращает одну кнопку под логику:
     • если НЕТ оплаченной подписки → показать «🏡 Смотреть примеры постов»
     • если ЕСТЬ оплаченная подписка И пользователь уже в канале → кнопка не нужна (None)
     • если ЕСТЬ оплаченная подписка И пользователя НЕТ в канале → показать «Подписаться»
       (нажатие запускает membership_service, который добавит/пришлёт инвайт)
    """
    # 1) Нет ОПЛАЧЕННОЙ подписки/триала → показываем «Смотреть примеры»
    #   (Требование: «если оформленной платной подписки/триала нет — оставить кнопку с примерами»)
    if not has_active_paid_subscription(user_id):
        return InlineKeyboardButton(text="🏡 Смотреть примеры постов", callback_data="smm_content")

    # 2) Есть оплаченная подписка → проверяем членство именно в EXAMPLES_CHAT_ID
    in_channel = await is_subscribed(bot, EXAMPLES_CHAT_ID, user_id)
    if in_channel:
        return None  # кнопку скрываем

    # 3) Оплата есть, но в канале не состоит → предлагаем подписаться
    return InlineKeyboardButton(text="🏡 Подписаться на канал с постами", callback_data=POSTS_SUBSCRIBE_CB)


async def build_main_menu_kb(bot: Bot, user_id: int) -> InlineKeyboardMarkup:
    """
    Главная клавиатура c новой логикой для кнопки постов:
     • если нет оплаченной подписки → «🏡 Смотреть примеры постов»
     • если подписка есть и пользователь уже в канале → кнопку убираем
     • если подписка есть, но пользователя нет в канале → «Подписаться на канал…»
    
    Названия «готовые посты/примеры» в зависимости от подписки не переключаем.
    """
    rows: list[list[InlineKeyboardButton]] = []
    
    # 1) Кнопка с постами (по правилам выше)
    try:
        posts_btn = await build_posts_button(bot, user_id)
    except Exception as e:
        logging.warning("build_posts_button failed for %s: %s", user_id, e)
        posts_btn = InlineKeyboardButton(text="🏡 Смотреть примеры постов", callback_data="smm_content")
    
    if posts_btn is not None:
        rows.append([posts_btn])
    
    # 2) Остальные кнопки
    rows.extend([
        [InlineKeyboardButton(text="📐 Обрисовщик планировок", callback_data="floor_plan")],
        [InlineKeyboardButton(text="🎨 Редизайн квартиры", callback_data="nav.design_home")],
        [InlineKeyboardButton(text="🧠 Инструменты PRO-риэлтора", callback_data="nav.ai_tools")],
        [InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")],
        [InlineKeyboardButton(text="Наше сообщество", url=PARTNER_URL)],
    ])
    
    return InlineKeyboardMarkup(inline_keyboard=rows)

ai_tools_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Описание объявлений", callback_data="nav.descr_home")],
        [InlineKeyboardButton(text="🗣 Отработка возражений", callback_data="nav.objection_start")],
        [InlineKeyboardButton(text="📊 Анализ диалогов", callback_data="nav.summary_home")],
        [InlineKeyboardButton(text="⭐ Составить отзыв", callback_data="nav.feedback_home")],

        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")],
    ]
)


def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")]]
    )


def help_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛟 Поддержка", url="https://t.me/dashaadminrealtor")
    return builder.as_markup()


# =============================================================================
# Инициализация пользователя
# =============================================================================
async def init_user(evt: Union[Message, CallbackQuery]) -> None:
    """
    Гарантирует, что пользователь есть в БД (дефолты ставятся в repo.ensure_user).
    Работает и для входящих сообщений, и для callback’ов.
    """
    if isinstance(evt, CallbackQuery):
        msg = evt.message
        tg_from: Optional[TgUser] = evt.from_user
    else:
        msg = evt
        tg_from = evt.from_user

    username = (tg_from.username if tg_from and tg_from.username else None)
    chat_id = msg.chat.id if msg else None
    user_id = tg_from.id if tg_from else (msg.chat.id if msg else None)
    if user_id is not None and chat_id is not None:
        app_db.check_and_add_user(user_id, chat_id=chat_id, username=username)

    if not msg:
        return


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
    kb = await build_main_menu_kb(bot, chat_id)
    if Path(logo_path).exists():
        try:
            await bot.send_photo(
                chat_id=chat_id,
                photo=FSInputFile(logo_path),
                caption=frst_text,
                reply_markup=kb,
            )
            return
        except Exception as e:
            logging.exception("Failed to send logo with caption: %s", e)
    else:
        logging.warning("Logo not found: %s (resolved from %s)", logo_path, logo_rel)

    await bot.send_message(chat_id=chat_id, text=frst_text, reply_markup=kb)


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
    kb = await build_main_menu_kb(callback.bot, callback.from_user.id)

    # Путь к картинке существует — пробуем заменить медиа
    if Path(logo_path).exists():
        try:
            media = InputMediaPhoto(media=FSInputFile(logo_path), caption=frst_text)
            await callback.message.edit_media(media=media, reply_markup=kb)
            await callback.answer()
            return
        except TelegramBadRequest:
            # Сообщение могло быть не медийным — пробуем обновить подпись
            try:
                await callback.message.edit_caption(caption=frst_text, reply_markup=kb)
                await callback.answer()
                return
            except TelegramBadRequest:
                # Как минимум заменим текст и клавиатуру
                try:
                    await callback.message.edit_text(frst_text, reply_markup=kb)
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
async def first_msg(message: Message, bot: Bot) -> None:
    await init_user(message)
    user_id = message.from_user.id
    if not await ensure_partner_subs(bot, message, retry_callback_data=PARTNER_CHECK_CB, columns=2):
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
    await init_user(callback)
    await _edit_or_replace_with_photo_cb(
        callback=callback,
        image_rel_path="img/bot/ai_tools.png",  # путь внутри DATA_DIR
        caption=ai_tools_text,
        kb=ai_tools_inline,
    )


async def check_subscribe_retry(callback: CallbackQuery, bot: Bot) -> None:
    await init_user(callback)

    if not await ensure_partner_subs(bot, callback, retry_callback_data=PARTNER_CHECK_CB, columns=2):
        await callback.answer(get_subscribe, show_alert=True)
        return

    await _replace_with_menu_with_logo(callback)


async def posts_subscribe_cb(callback: CallbackQuery) -> None:
    """
    Нажатие «Подписаться на канал с постами».
    Вызывает payment_handler.membership_invite и даёт пользователю понятный ответ.
    """
    await init_user(callback)
    try:
        await membership_invite(callback.from_user.id)
        # Не знаем режим (прямое добавление/ссылка в ЛС), даём универсальный ответ
        await callback.answer("Готово! Если добавление напрямую не сработает, пришлём ссылку в личку.", show_alert=False)
    except Exception:
        await callback.answer("Не удалось подписать сейчас. Попробуйте позже.", show_alert=True)


# =============================================================================
# Команды
# =============================================================================
async def sub_cmd(message: Message) -> None:
    await init_user(message)
    await show_rates_handler(message)


async def help_cmd(message: Message) -> None:
    await init_user(message)
    await message.answer(HELP, reply_markup=help_kb())


def router(rt: Router) -> None:

    rt.message.register(first_msg, CommandStart())
    rt.message.register(first_msg, Command("main"))
    rt.message.register(sub_cmd,  Command("sub"))
    rt.message.register(help_cmd, Command("support"))

    # callbacks
    rt.callback_query.register(ai_tools, F.data == "nav.ai_tools")
    rt.callback_query.register(check_subscribe_retry, F.data == "start_retry")
    rt.callback_query.register(first_msg, F.data == "main")
    rt.callback_query.register(posts_subscribe_cb, F.data == POSTS_SUBSCRIBE_CB)