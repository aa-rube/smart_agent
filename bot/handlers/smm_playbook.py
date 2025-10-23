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
from bot.utils.mailing import send_last_3_published_to_user

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# CAPTION LIMITS
# ──────────────────────────────────────────────────────────────────────────────
# Относительные пути внутри DATA_DIR (разрешаются через get_file_path)
POST_STORIES_REL = "post_1.jpg"
POST_EXPERT_REL  = "post_2.jpg"
POST_EDU_REL     = "post_3.MOV"

def _safe_caption(text: str, limit: int = 1024) -> str:
    """
    Безопасно обрезает caption до лимита Telegram (по умолчанию 1024 символа).
    Если текст длиннее — добавляет многоточие.
    """
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"

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

def _stories_caption() -> str:
    return (
        "Примеры сторис, которые есть в нашем боте:\n\n"
        "РАЗВЛЕКАТЕЛЬНЫЙ КОНТЕНТ\n"
        "Готовый контент с юмором и жизненными ситуациями, которые твои подписчики будут узнавать и обожать."
    )

def _expert_caption() -> str:
    return (
        "ЭКСПЕРТНЫЙ КОНТЕНТ\n"
        "Примеры постов\n"
        "Глубокие аналитические посты, советы по сделкам и продвинутые лайфхаки. "
        "Закрепи за собой статус главного эксперта в своем городе!\n\n"
        "Пример поста:\n"
        "Застройщик-банкрот: варианты для покупателя \n\n"
        "❗️Ситуация, которой боится каждый дольщик: вложили деньги в квартиру, дом ещё строится — и вдруг "
        "застройщик объявляет себя банкротом.\n\n"
        "Давайте разберёмся, что происходит дальше:\n\n"
        "1️⃣ Стройка может быть заморожена — до решения суда и назначения арбитражного управляющего работы чаще всего приостанавливаются\n\n"
        "2️⃣ Назначается конкурсный управляющий — он собирает требования всех кредиторов, в том числе дольщиков, и определяет порядок расчётов\n\n"
        "3️⃣ Возможные сценарии для покупателей:\n"
        "- дом достроят другой компанией (по решению Фонда защиты прав дольщиков или региона);\n"
        "- дольщики получат денежную компенсацию.\n\n"
        "В редких случаях стройку признают невозможной, и тогда вернуть деньги можно только через конкурсную массу — имущество, "
        "из которого погашаются долги перед кредиторами банкрота в законной очерёдности (что почти всегда дольше и меньше суммы вложений).\n\n"
        "4️⃣ Роль Фонда защиты прав дольщиков — если застройщик был участником системы эскроу-счетов, то деньги возвращаются через банк. "
        "Если же нет — забирать придётся через Фонд и суд.\n\n"
        "‼️ Что важно помнить:\n"
        "— Проверяйте, как именно оформлен ваш договор (ДДУ или иной);\n"
        "— Сохраняйте все платёжные документы; \n"
        "— Объединяйтесь с другими дольщиками — так легче отстаивать права.\n\n"
        "🤝🏼 И помните — сейчас все сделки проходят через эскроу-счета, а значит — застрахованы."
    )

def _edu_caption() -> str:
    return (
        "ПОЗНАВАТЕЛЬНЫЙ КОНТЕНТ\n"
        "Примеры видео\n"
        "Простые карточки и видео, которые объясняют сложные темы клиентам: ипотека, налоги, документы. "
        "Ты станешь для них гидом в мире недвижимости!"
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

async def _send_photo_to_chat(bot: Bot, chat_id: int, image_rel_path: str, caption: str) -> bool:
    """
    Отправляет ОДНО сообщение с фото и подписью (caption).
    Если файл не найден — отправляет текст как fallback.
    """
    try:
        img_path = get_file_path(image_rel_path)
    except Exception:
        img_path = image_rel_path

    safe = _safe_caption(caption)
    try:
        if Path(img_path).exists():
            await bot.send_photo(chat_id=chat_id, photo=FSInputFile(img_path), caption=safe)
            return True
        else:
            logger.warning("Photo not found: resolved=%s (rel=%s)", img_path, image_rel_path)
    except Exception:
        logger.warning("Failed to send photo %s (resolved=%s)", image_rel_path, img_path, exc_info=True)

    try:
        await bot.send_message(chat_id=chat_id, text=caption)
        return True
    except Exception:
        logger.warning("Fallback text failed for photo %s", image_rel_path, exc_info=True)
        return False

async def _send_video_to_chat(bot: Bot, chat_id: int, video_rel_path: str, caption: str) -> bool:
    """
    Отправляет ОДНО сообщение с видеороликом (send_video) и подписью (caption).
    Если видео отсутствует — шлёт только текст как fallback.
    """
    try:
        vid_path = get_file_path(video_rel_path)
    except Exception:
        vid_path = video_rel_path

    safe = _safe_caption(caption)
    try:
        if Path(vid_path).exists():
            try:
                # Попытка отправить как видео (Telegram иногда не любит MOV)
                await bot.send_video(chat_id=chat_id, video=FSInputFile(vid_path), caption=safe)
                return True
            except Exception:
                # Фоллбэк: отправим как документ, чтобы пользователь всё равно получил файл
                logger.debug("send_video failed for %s, fallback to send_document", vid_path, exc_info=True)
                await bot.send_document(chat_id=chat_id, document=FSInputFile(vid_path), caption=safe)
                return True
        else:
            logger.warning("Video not found: resolved=%s (rel=%s)", vid_path, video_rel_path)
    except Exception:
        logger.warning("Failed to send video %s (resolved=%s)", video_rel_path, vid_path, exc_info=True)

    try:
        await bot.send_message(chat_id=chat_id, text=caption)
        return True
    except Exception:
        logger.warning("Fallback text failed for video %s", video_rel_path, exc_info=True)
        return False

# ──────────────────────────────────────────────────────────────────────────────
# SMM FLOW
# ──────────────────────────────────────────────────────────────────────────────

async def smm_content(cb: CallbackQuery) -> None:
    """
    Последовательно отправляет 3 отдельных сообщения:
      1) Фото + caption: «Развлекательный контент» (post_1.jpg)
      2) Фото + caption: «Экспертный контент» (post_2.jpg) — caption обрезается до лимита TG
      3) Видео + caption: «Познавательный контент» (post_3.MOV)
    И затем 4) тех.сообщение с кнопкой «Назад».
    """
    await _init_user_from_cb(cb)
    chat_id = cb.message.chat.id
    bot = cb.bot

    # 1) Развлекательный контент (сторис) — фото + caption
    try:
        await _send_photo_to_chat(bot, chat_id, POST_STORIES_REL, _stories_caption())
    except Exception:
        logger.warning("smm_content: failed to send stories block", exc_info=True)

    # 2) Экспертный контент (посты) — фото + длинный caption (обрезаем до лимита)
    try:
        await _send_photo_to_chat(bot, chat_id, POST_EXPERT_REL, _expert_caption())
    except Exception:
        logger.warning("smm_content: failed to send expert block", exc_info=True)

    # 3) Познавательный контент (видео) — video + caption (fallback: документ)
    try:
        await _send_video_to_chat(bot, chat_id, POST_EDU_REL, _edu_caption())
    except Exception:
        logger.warning("smm_content: failed to send edu video block", exc_info=True)

    # 4) Техническое сообщение с кнопкой «Назад»
    try:
        await bot.send_message(
            chat_id=chat_id,
            text="Чтобы вернуться в главное меню, нажмите «Назад».",
            reply_markup=back_kb(),
        )
    except Exception:
        logger.warning("smm_content: failed to send back button", exc_info=True)

    await cb.answer()


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
            text=('''
Спасибо за подписку, теперь ты можешь оценить все инструменты без ограничений!
Теперь каждый день тебе будут приходить готовые посты для твоих соц сетей'''
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
