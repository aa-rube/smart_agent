from __future__ import annotations

import os
import fitz
import aiohttp
from typing import Optional

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, ContentType
)
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest

import bot.utils.database as db
from bot.config import get_file_path
from bot.utils.database import is_trial_active, trial_remaining_hours
from bot.states.states import RedesignStates, ZeroDesignStates
from executor.prompt_factory import create_prompt
from bot.utils.image_processor import save_image_as_png
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import generate_design, download_image_from_url
from bot.utils.file_utils import safe_remove


# =============================================================================
# Доступ / подписка
# =============================================================================

def _is_sub_active(user_id: int) -> bool:
    raw = db.get_variable(user_id, "sub_until") or ""
    if not raw:
        return False
    try:
        from datetime import datetime
        today = datetime.utcnow().date()
        return today <= datetime.fromisoformat(raw).date()
    except Exception:
        return False

def _format_access_text(user_id: int) -> str:
    trial_hours = trial_remaining_hours(user_id)
    if _is_sub_active(user_id):
        sub_until = db.get_variable(user_id, "sub_until")
        return f'✅ Подписка активна до *{sub_until}*'
    if trial_hours > 0:
        return f'🆓 Бесплатный доступ активен ещё *~{trial_hours} ч.*'
    return '😢 Бесплатный период завершён. Оформи подписку, чтобы продолжить.'

def _has_access(user_id: int) -> bool:
    return is_trial_active(user_id) or _is_sub_active(user_id)


# =============================================================================
# Тексты
# =============================================================================

def _start_screen_text(user_id: int) -> str:
    tokens_text = _format_access_text(user_id)
    return f"""
*1️⃣ Выбери режим:*
• 🛋 *Редизайн интерьера* — загрузите фото помещения и выберите стиль.
• 🆕 *Дизайн с нуля* — фото пустого помещения, выбор мебели и стиля.

2️⃣ Получи результат за 1–2 минуты 💡

{tokens_text}

Загрузи файл, когда будешь готов 👇
""".strip()

_TEXT_GET_FILE_REDESIGN_TPL = """
1️⃣ Загрузи *фото помещения* — подойдёт изображение (jpeg/jpg/png), PDF (1 стр.) или прямая ссылка на картинку.

2️⃣ Выбери интерьерный стиль и получи обновлённый дизайн.

{tokens_text}

Жду файл 👇
""".strip()

def text_get_file_redesign(user_id: int) -> str:
    return _TEXT_GET_FILE_REDESIGN_TPL.format(tokens_text=_format_access_text(user_id))

_TEXT_GET_FILE_ZERO_TPL = """
1️⃣ Загрузи *фото интерьера* (jpeg/jpg/png), PDF (1 стр.) или ссылку на изображение.

2️⃣ Выбери тип помещения, меблировку и стиль — и получишь готовую визуализацию.

{tokens_text}

Жду файл 👇
""".strip()

def text_get_file_zero(user_id: int) -> str:
    return _TEXT_GET_FILE_ZERO_TPL.format(tokens_text=_format_access_text(user_id))

TEXT_GET_STYLE = "Ок! Теперь выбери стиль оформления 🖼️"
TEXT_FINAL = "✅ Готово! Вот результат."
ERROR_WRONG_INPUT = "❌ Пожалуйста, отправь изображение (jpg/png), PDF (1 страница) или прямую ссылку на картинку."
ERROR_PDF_PAGES = "❌ В PDF должно быть не больше одной страницы."
ERROR_LINK = "❌ Не удалось скачать изображение по ссылке. Нужна прямая ссылка на файл (jpg/png)."
SORRY_TRY_AGAIN = "😔 Не удалось сгенерировать изображение. Попробуйте ещё раз."
UNSUCCESSFUL_TRY_LATER = "😔 Не удалось скачать сгенерированное изображение. Попробуйте позже."

SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)


# =============================================================================
# Клавиатуры
# =============================================================================

def kb_design_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛋 Редизайн интерьера", callback_data="redesign")],
            [InlineKeyboardButton(text="🆕 Дизайн с нуля", callback_data="0design")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")],
        ]
    )

def kb_style_choices() -> InlineKeyboardMarkup:
    styles = [
        "Современный", "Скандинавский", "Классика", "Минимализм", "Хай-тек",
        "Лофт", "Эко-стиль", "Средиземноморский", "Барокко", "Неоклассика",
        "🔥 Случайный выбор ИИ",
    ]
    rows = [[InlineKeyboardButton(text=f"💎 {s}", callback_data=f"style_{s}")] for s in styles]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_room_type() -> InlineKeyboardMarkup:
    rooms = ["🍳 Кухня", "🛏 Спальня", "🛋 Гостиная", "🚿 Ванная", "🚪 Прихожая"]
    rows, line = [], []
    for r in rooms:
        line.append(InlineKeyboardButton(text=r, callback_data=f"room_{r}"))
        if len(line) == 2:
            rows.append(line); line = []
    if line: rows.append(line)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_furniture() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛋 С мебелью", callback_data="furniture_yes")],
            [InlineKeyboardButton(text="▫️ Без мебели", callback_data="furniture_no")],
        ]
    )


# =============================================================================
# Хелперы редактирования
# =============================================================================

async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode=None)
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_caption(caption=text, reply_markup=kb, parse_mode=None)
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

async def _edit_or_replace_with_photo_file(
    bot: Bot, msg: Message, file_path: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    try:
        media = InputMediaPhoto(media=FSInputFile(file_path), caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(file_path), caption=caption, reply_markup=kb)

async def _edit_or_replace_with_photo_url(
    bot: Bot, msg: Message, url: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    try:
        media = InputMediaPhoto(media=url, caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=url, caption=caption, reply_markup=kb)


# =============================================================================
# Главный экран «Дизайн»
# =============================================================================

async def design_home(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = callback.from_user.id

    cover_rel = "img/bot/main_design.jpg"
    cover_path = get_file_path(cover_rel)
    caption = _start_screen_text(user_id)

    if os.path.exists(cover_path):
        await _edit_or_replace_with_photo_file(bot, callback.message, cover_path, caption, kb_design_home())
    else:
        await _edit_text_or_caption(callback.message, caption, kb_design_home())

    await callback.answer()


# =============================================================================
# РЕДИЗАЙН (по фото)
# =============================================================================

async def start_redesign_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Начало редизайна — просим загрузить фото/скан/ссылку."""
    user_id = callback.message.chat.id

    if _has_access(user_id):
        await state.set_state(RedesignStates.waiting_for_file)
        await _edit_or_replace_with_photo_file(
            bot=bot,
            msg=callback.message,
            file_path=get_file_path('img/bot/design.jpg'),
            caption=text_get_file_redesign(user_id),
            kb=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")]]
            ),
        )
    else:
        await _edit_text_or_caption(callback.message, _format_access_text(user_id), SUBSCRIBE_KB)

    await callback.answer()


async def handle_file_redesign(message: Message, state: FSMContext, bot: Bot):
    """Получаем файл для редизайна → затем спросим тип помещения и стиль."""
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    user_id = message.from_user.id
    image_bytes: bytes | None = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()
    elif message.document and message.document.mime_type and message.document.mime_type.startswith('image/'):
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()
    elif message.document and message.document.mime_type == 'application/pdf':
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        pdf_bytes = (await bot.download_file(file.file_path)).read()
        doc = fitz.open("pdf", pdf_bytes)
        if doc.page_count != 1:
            await message.answer(ERROR_PDF_PAGES)
            return
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=200)
        image_bytes = pix.tobytes("png")
        doc.close()
    elif message.text and (message.text.startswith('http://') or message.text.startswith('https://')):
        url = message.text.strip()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200 and 'image' in (resp.headers.get('Content-Type') or ''):
                        image_bytes = await resp.read()
                    else:
                        await message.answer(ERROR_LINK)
                        return
        except Exception:
            await message.answer(ERROR_LINK)
            return
    else:
        await message.answer(ERROR_WRONG_INPUT)
        return

    if image_bytes:
        saved_path = get_file_path(f"img/tmp/redesign_{user_id}.png")
        os.makedirs(os.path.dirname(saved_path), exist_ok=True)
        with open(saved_path, "wb") as f:
            f.write(image_bytes)

        await state.update_data(image_path=saved_path)
        await message.answer("Какое это помещение?", reply_markup=kb_room_type())
        await state.set_state(RedesignStates.waiting_for_room_type)


async def handle_room_type_redesign(callback: CallbackQuery, state: FSMContext):
    await state.update_data(room_type=callback.data.split('_', 1)[1])
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=kb_style_choices())
    await state.set_state(RedesignStates.waiting_for_style)
    await callback.answer()


async def handle_style_redesign(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Генерация редизайна по фото + room_type + style."""
    user_id = callback.from_user.id
    if not _has_access(user_id):
        await _edit_text_or_caption(callback.message, _format_access_text(user_id), SUBSCRIBE_KB)
        await state.clear()
        await callback.answer()
        return

    data = await state.get_data()
    image_path = data.get("image_path")
    room_type = data.get("room_type")
    try:
        _, style_choice = (callback.data or "").split("_", 1)
    except Exception:
        style_choice = "Модерн"

    prompt = create_prompt(style=style_choice, room_type=room_type)

    await _edit_text_or_caption(callback.message, "⏳ Генерирую дизайн… Это может занять до 1–2 минут.")

    try:
        coro = generate_design(image_path=image_path, prompt=prompt)
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            image_bytes = await download_image_from_url(image_url)
            if image_bytes:
                tmp_path = get_file_path(f"img/tmp/result_{user_id}.png")
                os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
                with open(tmp_path, "wb") as f:
                    f.write(image_bytes)

                await _edit_or_replace_with_photo_file(
                    bot=bot,
                    msg=callback.message,
                    file_path=tmp_path,
                    caption=TEXT_FINAL,
                    kb=None
                )
                try: os.remove(tmp_path)
                except OSError: pass
            else:
                await _edit_text_or_caption(
                    callback.message,
                    UNSUCCESSFUL_TRY_LATER,
                    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                        text="⬅️ Назад", callback_data="nav.design_home")]]))
        else:
            await _edit_text_or_caption(
                callback.message,
                SORRY_TRY_AGAIN,
                kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text="⬅️ Назад", callback_data="nav.design_home")]]))
    finally:
        if image_path and os.path.exists(image_path):
            if safe_remove(image_path):
                print(f"Временный файл удален: {image_path}")
            else:
                print(f"Не удалось удалить временный файл (занят): {image_path}")
        await state.clear()
        await callback.answer()


# =============================================================================
# ДИЗАЙН С НУЛЯ (Zero-Design)
# =============================================================================

async def start_zero_design_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.message.chat.id

    if _has_access(user_id):
        await state.set_state(ZeroDesignStates.waiting_for_file)
        await _edit_or_replace_with_photo_file(
            bot=bot,
            msg=callback.message,
            file_path=get_file_path('img/bot/zero_design.jpg'),
            caption=text_get_file_zero(user_id),
            kb=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")]]
            ),
        )
    else:
        await _edit_text_or_caption(callback.message, _format_access_text(user_id), SUBSCRIBE_KB)

    await callback.answer()


async def handle_file_zero(message: Message, state: FSMContext, bot: Bot):
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    user_id = message.from_user.id
    image_bytes: bytes | None = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()
    elif message.document and message.document.mime_type and message.document.mime_type.startswith('image/'):
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()
    elif message.document and message.document.mime_type == 'application/pdf':
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        pdf_bytes = (await bot.download_file(file.file_path)).read()
        doc = fitz.open("pdf", pdf_bytes)
        if doc.page_count != 1:
            await message.answer(ERROR_PDF_PAGES)
            return
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=200)
        image_bytes = pix.tobytes("png")
        doc.close()
    elif message.text and (message.text.startswith('http://') or message.text.startswith('https://')):
        url = message.text.strip()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200 and 'image' in (resp.headers.get('Content-Type') or ''):
                        image_bytes = await resp.read()
                    else:
                        await message.answer(ERROR_LINK)
                        return
        except Exception:
            await message.answer(ERROR_LINK)
            return
    else:
        await message.answer(ERROR_WRONG_INPUT)
        return

    if image_bytes:
        saved_path = await save_image_as_png(image_bytes, user_id)
        if saved_path:
            await state.update_data(image_path=saved_path)
            await message.answer("Какое это помещение?", reply_markup=kb_room_type())
            await state.set_state(ZeroDesignStates.waiting_for_room_type)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте ещё раз.")


async def handle_room_type_zero(callback: CallbackQuery, state: FSMContext):
    await state.update_data(room_type=callback.data.split('_', 1)[1])
    await callback.message.edit_text(
        "Хочешь дизайн с мебелью или без?\n\n"
        "🛋 С мебелью — сразу видно, как может выглядеть готовый интерьер.\n"
        "▫️ Без мебели — чистое пространство, акцент на отделке.",
        reply_markup=kb_furniture()
    )
    await state.set_state(ZeroDesignStates.waiting_for_furniture)
    await callback.answer()


async def handle_furniture_zero(callback: CallbackQuery, state: FSMContext):
    await state.update_data(furniture_choice=callback.data)  # furniture_yes | furniture_no
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=kb_style_choices())
    await state.set_state(ZeroDesignStates.waiting_for_style)
    await callback.answer()


async def handle_style_zero(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id

    if not _has_access(user_id):
        await _edit_text_or_caption(callback.message, _format_access_text(user_id), SUBSCRIBE_KB)
        await state.clear()
        await callback.answer()
        return

    data = await state.get_data()
    image_path = data.get("image_path")
    room_type = data.get("room_type")
    furniture_choice = data.get("furniture_choice")

    try:
        _, style_choice = (callback.data or "").split("_", 1)
    except Exception:
        style_choice = "Модерн"

    prompt = create_prompt(style=style_choice, room_type=room_type, furniture=furniture_choice)

    await _edit_text_or_caption(callback.message, "⏳ Генерирую дизайн… Это может занять до 1–2 минут.")

    try:
        coro = generate_design(image_path=image_path, prompt=prompt)
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            image_bytes = await download_image_from_url(image_url)
            if image_bytes:
                tmp_path = get_file_path(f"img/tmp/result_{user_id}.png")
                os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
                with open(tmp_path, "wb") as f:
                    f.write(image_bytes)

                await _edit_or_replace_with_photo_file(
                    bot=bot,
                    msg=callback.message,
                    file_path=tmp_path,
                    caption=TEXT_FINAL,
                    kb=None
                )
                try: os.remove(tmp_path)
                except OSError: pass
            else:
                await _edit_text_or_caption(
                    callback.message,
                    UNSUCCESSFUL_TRY_LATER,
                    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                        text="⬅️ Назад", callback_data="nav.design_home")]]))
        else:
            await _edit_text_or_caption(
                callback.message,
                SORRY_TRY_AGAIN,
                kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                    text="⬅️ Назад", callback_data="nav.design_home")]]))
    finally:
        if image_path and os.path.exists(image_path):
            if safe_remove(image_path):
                print(f"Временный файл удален: {image_path}")
            else:
                print(f"Не удалось удалить временный файл (занят): {image_path}")
        await state.clear()
        await callback.answer()


# =============================================================================
# Router
# =============================================================================

def router(rt: Router):
    # Главный экран раздела «Дизайн»
    rt.callback_query.register(design_home, F.data == 'nav.design_home')

    # Редизайн
    rt.callback_query.register(start_redesign_flow, F.data == "redesign")
    rt.message.register(
        handle_file_redesign,
        RedesignStates.waiting_for_file,
        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT})
    )
    rt.callback_query.register(handle_room_type_redesign, RedesignStates.waiting_for_room_type)
    rt.callback_query.register(handle_style_redesign, RedesignStates.waiting_for_style)

    # Дизайн с нуля
    rt.callback_query.register(start_zero_design_flow, F.data == "0design")
    rt.message.register(
        handle_file_zero,
        ZeroDesignStates.waiting_for_file,
        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT})
    )
    rt.callback_query.register(handle_room_type_zero, ZeroDesignStates.waiting_for_room_type)
    rt.callback_query.register(handle_furniture_zero, ZeroDesignStates.waiting_for_furniture)
    rt.callback_query.register(handle_style_zero, ZeroDesignStates.waiting_for_style)
