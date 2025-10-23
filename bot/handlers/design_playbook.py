#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\design_playbook.py
from __future__ import annotations

import fitz
from typing import Optional
import os
import aiohttp
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, ContentType
)
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest

from bot.config import EXECUTOR_BASE_URL, get_file_path
from bot.handlers.payment_handler import (
    format_access_text,  # централизованный короткий статус доступа
    ensure_access,       # централизованная проверка/показ экрана подписки
)

from bot.states.states import RedesignStates, ZeroDesignStates

from bot.utils.image_processor import *
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.file_utils import safe_remove
import base64
import re
import uuid
from datetime import datetime

# NEW: persistent storage + DB repo for generations
from bot.utils.image_store import (
    init_image_store,
    save_bytes_as_png,
    build_image_path_for_msg_id,
    rename_for_new_msg_id,
)
from bot.utils.design_db import save_generation_record, get_generation_by_result_msg_id

# Инициализируем persistent-хранилище при импорте модуля
init_image_store()


async def _safe_answer(cb: CallbackQuery) -> None:
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass
    except Exception:
        pass


# =============================================================================
# Тексты
# =============================================================================

def _start_screen_text(user_id: int) -> str:
    return f"""
• 🛋 *Редизайн интерьера* — загрузи фото мебелированного помещения и выбери стиль.

• 🆕 *Дизайн с нуля* — загрузи фото пустого помещения, выбери стиль и мебель.
""".strip()

_TEXT_GET_FILE = """
Загрузи фото помещения -  Получи макет за 1–2 минуты 💡
Готов? Кидай файл сюда 👇
""".strip()

def text_get_file_redesign(user_id: int) -> str:
    return _TEXT_GET_FILE.format(tokens_text=format_access_text(user_id))

def text_get_file_zero(user_id: int) -> str:
    return _TEXT_GET_FILE.format(tokens_text=format_access_text(user_id))

TEXT_GET_STYLE = "Ок! Теперь выбери стиль оформления 🖼️"
TEXT_FINAL = "✅ Готово! Вот результат."
TEXT_WAIT = "⏳ Пожалуйста, подождите… генерируем новый вариант."
ERROR_WRONG_INPUT = "❌ Пожалуйста, отправь изображение (jpg/png), PDF (1 страница) или прямую ссылку на картинку."
ERROR_PDF_PAGES = "❌ В PDF должно быть не больше одной страницы."
ERROR_LINK = "❌ Не удалось скачать изображение по ссылке. Нужна прямая ссылка на файл (jpg/png)."
SORRY_TRY_AGAIN = "😔 Не удалось сгенерировать изображение. Попробуйте ещё раз."
UNSUCCESSFUL_TRY_LATER = "😔 Не удалось скачать сгенерированное изображение. Попробуйте позже."


# =============================================================================
# Клавиатуры
# =============================================================================

def kb_design_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛋 Редизайн интерьера", callback_data="redesign")],
            [InlineKeyboardButton(text="🆕 Дизайн с нуля", callback_data="0design")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")],
        ]
    )

def kb_style_choices() -> InlineKeyboardMarkup:
    styles = [
        "Современный", "Скандинавский", "Классика", "Минимализм",
        "Лофт", "Эко-стиль", "Неоклассика",
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

def kb_result_back_redesign() -> InlineKeyboardMarkup:
    """Кнопка на экране результата редизайна — вернуться к загрузке фото."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ Загрузить другое фото", callback_data="redesign.back_to_upload")]]
    )

def kb_result_back_zero() -> InlineKeyboardMarkup:
    """Кнопка на экране результата zero-design — вернуться к загрузке фото."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ Загрузить другое фото", callback_data="zerodesign.back_to_upload")]]
    )

# NEW: клавиатура с «Повторить» и «Назад»
def kb_result_actions(*, result_msg_id: int, back_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔁 Попробовать ещё раз", callback_data=f"d.rep={int(result_msg_id)}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
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
) -> Message:
    try:
        media = InputMediaPhoto(media=FSInputFile(file_path), caption=caption)
        new_msg: Message = await msg.edit_media(media=media, reply_markup=kb)
        return new_msg
    except TelegramBadRequest:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        new_msg = await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(file_path), caption=caption, reply_markup=kb)
        return new_msg

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

# --- helpers for data: URLs ---
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<b64>.+)$", re.I | re.S)

def _is_data_url(s: str) -> bool:
    return bool(_DATA_URL_RE.match(s or ""))

def _data_url_to_bytes(s: str) -> tuple[bytes, str]:
    m = _DATA_URL_RE.match(s or "")
    if not m:
        return b"", "application/octet-stream"
    mime = m.group("mime") or "application/octet-stream"
    return base64.b64decode(m.group("b64")), mime


# =============================================================================
# Главный экран «Дизайн»
# =============================================================================

async def design_home(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    user_id = callback.from_user.id

    cover_rel = "img/bot/main_design.png"
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
    # централизованная проверка (покажет экран подписки и сам ответит callback при отсутствии доступа)
    if not await ensure_access(callback):
        return
    await state.set_state(RedesignStates.waiting_for_file)
    await _edit_or_replace_with_photo_file(
        bot=bot,
        msg=callback.message,
        file_path=get_file_path('img/bot/design.png'),
        caption=text_get_file_redesign(user_id),
        kb=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")]]
        ),
    )
    await callback.answer()


async def handle_file_redesign(message: Message, state: FSMContext, bot: Bot):
    """Получаем файл для редизайна → затем спросим тип помещения и стиль."""
    if not await ensure_access(message):
        await state.clear()
        return
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
        # NEW: сохраняем исходник как <msg_id>.png в домашнем хранилище
        saved_path = save_bytes_as_png(image_bytes, message.message_id)
        await state.update_data(image_path=str(saved_path), src_msg_id=message.message_id)
        await message.answer("Какое это помещение?", reply_markup=kb_room_type())
        await state.set_state(RedesignStates.waiting_for_room_type)


async def handle_room_type_redesign(callback: CallbackQuery, state: FSMContext):
    await state.update_data(room_type=callback.data.split('_', 1)[1])
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=kb_style_choices())
    await state.set_state(RedesignStates.waiting_for_style)
    await callback.answer()


async def handle_style_redesign(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Генерация редизайна по фото + room_type + style."""
    # ВАЖНО: сразу закрываем callback, чтобы не «протух»
    await _safe_answer(callback)
    # централизованная проверка доступа (сама покажет экран подписки)
    if not await ensure_access(callback):
        await state.clear()
        return
    user_id = callback.from_user.id

    data = await state.get_data()
    image_path = data.get("image_path")
    room_type = data.get("room_type")
    try:
        _, style_choice = (callback.data or "").split("_", 1)
    except Exception:
        style_choice = "Модерн"

    await _edit_text_or_caption(callback.message, "⏳ Генерирую дизайн… Это может занять до 1–2 минут.")

    try:
        # Передаём структурные параметры; промпт собирается на стороне executor
        coro = generate_design(image_path=image_path, style=style_choice, room_type=room_type)
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            # Поддерживаем как http(s), так и data:URL
            if _is_data_url(image_url):
                image_bytes, _ = _data_url_to_bytes(image_url)
            else:
                image_bytes = await download_image_from_url(image_url)
            if image_bytes:
                # Сохраняем результат как <msg_id>.png (ожидаем edit в это же сообщение)
                planned_msg_id = callback.message.message_id
                planned_path = save_bytes_as_png(image_bytes, planned_msg_id)

                result_msg = await _edit_or_replace_with_photo_file(
                    bot=bot,
                    msg=callback.message,
                    file_path=str(planned_path),
                    caption=TEXT_FINAL,
                    kb=None
                )
                final_msg_id = result_msg.message_id
                final_path = planned_path if final_msg_id == planned_msg_id else rename_for_new_msg_id(planned_path, final_msg_id)

                # Вешаем «Повторить/Назад»
                kb = kb_result_actions(result_msg_id=final_msg_id, back_cb="redesign.back_to_upload")
                try:
                    await result_msg.edit_reply_markup(reply_markup=kb)
                except TelegramBadRequest:
                    pass

                # Сохраняем запись в БД
                try:
                    save_generation_record(
                        result_msg_id=final_msg_id,
                        user_id=user_id,
                        chat_id=callback.message.chat.id,
                        mode="redesign",
                        style=style_choice,
                        room_type=room_type,
                        furniture=None,
                        src_image_path=image_path or "",
                        result_image_path=str(final_path),
                    )
                except Exception as e:
                    print(f"[design_db] save_generation_record error: {e}")
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
        # Исходники не удаляем (persistent), чистим только state
        await state.clear()
        # НЕ отвечаем повторно — к этому моменту query уже может протухнуть


# =============================================================================
# ДИЗАЙН С НУЛЯ (Zero-Design)
# =============================================================================

async def start_zero_design_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.message.chat.id
    if not await ensure_access(callback):
        return
    await state.set_state(ZeroDesignStates.waiting_for_file)
    await _edit_or_replace_with_photo_file(
        bot=bot,
        msg=callback.message,
        file_path=get_file_path('img/bot/zero_design.png'),
        caption=text_get_file_zero(user_id),
        kb=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")]]
        ),
    )
    await callback.answer()


async def handle_file_zero(message: Message, state: FSMContext, bot: Bot):
    if not await ensure_access(message):
        await state.clear()
        return
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
        saved_path = save_bytes_as_png(image_bytes, message.message_id)
        await state.update_data(image_path=str(saved_path), src_msg_id=message.message_id)
        await message.answer("Какое это помещение?", reply_markup=kb_room_type())
        await state.set_state(ZeroDesignStates.waiting_for_room_type)


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
    # Сразу закрываем callback
    await _safe_answer(callback)
    if not await ensure_access(callback):
        await state.clear()
        return
    user_id = callback.from_user.id

    data = await state.get_data()
    image_path = data.get("image_path")
    room_type = data.get("room_type")
    furniture_choice = data.get("furniture_choice")

    try:
        _, style_choice = (callback.data or "").split("_", 1)
    except Exception:
        style_choice = "Модерн"

    await _edit_text_or_caption(callback.message, "⏳ Генерирую дизайн… Это может занять до 1–2 минут.")

    try:
        # Передаём структурные параметры; промпт собирается на стороне executor
        coro = generate_design(
            image_path=image_path,
            style=style_choice,
            room_type=room_type,
            furniture=furniture_choice
        )
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            if _is_data_url(image_url):
                image_bytes, _ = _data_url_to_bytes(image_url)
            else:
                image_bytes = await download_image_from_url(image_url)
            if image_bytes:
                planned_msg_id = callback.message.message_id
                planned_path = save_bytes_as_png(image_bytes, planned_msg_id)

                result_msg = await _edit_or_replace_with_photo_file(
                    bot=bot,
                    msg=callback.message,
                    file_path=str(planned_path),
                    caption=TEXT_FINAL,
                    kb=None
                )
                final_msg_id = result_msg.message_id
                final_path = planned_path if final_msg_id == planned_msg_id else rename_for_new_msg_id(planned_path, final_msg_id)

                kb = kb_result_actions(result_msg_id=final_msg_id, back_cb="zerodesign.back_to_upload")
                try:
                    await result_msg.edit_reply_markup(reply_markup=kb)
                except TelegramBadRequest:
                    pass

                try:
                    save_generation_record(
                        result_msg_id=final_msg_id,
                        user_id=user_id,
                        chat_id=callback.message.chat.id,
                        mode="zero",
                        style=style_choice,
                        room_type=room_type,
                        furniture=furniture_choice,
                        src_image_path=image_path or "",
                        result_image_path=str(final_path),
                    )
                except Exception as e:
                    print(f"[design_db] save_generation_record error: {e}")
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
        await state.clear()
        # Повторный answer убирать


# =============================================================================
# Back buttons from result → return to upload step
# =============================================================================
async def handle_redesign_back_to_upload(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Кнопка «Назад» с экрана результата редизайна:
    1) убрать клавиатуру у текущего сообщения;
    2) отправить экран «загрузите фото»;
    3) выставить состояние ожидания файла.
    """
    user_id = callback.from_user.id
    # 1) убрать клавиатуру
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    # 2) отправить новый экран загрузки
    await state.set_state(RedesignStates.waiting_for_file)
    await bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=FSInputFile(get_file_path('img/bot/design.png')),
        caption=text_get_file_redesign(user_id),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")]]
        ),
    )
    await callback.answer()


async def handle_zero_back_to_upload(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Кнопка «Назад» с экрана результата zero-design:
    1) убрать клавиатуру у текущего сообщения;
    2) отправить экран «загрузите фото»;
    3) выставить состояние ожидания файла.
    """
    user_id = callback.from_user.id
    # 1) убрать клавиатуру
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    # 2) отправить новый экран загрузки
    await state.set_state(ZeroDesignStates.waiting_for_file)
    await bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=FSInputFile(get_file_path('img/bot/zero_design.png')),
        caption=text_get_file_zero(user_id),
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")]]
        ),
    )
    await callback.answer()


# =============================================================================
# RETRY: «Попробовать ещё раз» под результатом
# =============================================================================
async def handle_retry_generation(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    callback_data: d.rep=<result_msg_id>
    Логика:
      1) читаем запись из БД по result_msg_id;
      2) отправляем «песочные часы» КАК ФОТО ИЗ МЕНЮ (media), чтобы потом editMessageMedia сохранить msg_id;
      3) заново вызываем executor с теми же параметрами;
      4) editMessageMedia → картинка, вешаем клавиатуру;
      5) сохраняем новую запись с новым result_msg_id.
    """
    await _safe_answer(callback)
    data = (callback.data or "").strip()
    try:
        _, val = data.split("=", 1)  # d.rep=<id>
        ref_msg_id = int(val)
    except Exception:
        await callback.answer("Ошибка параметров.", show_alert=True)
        return

    rec = get_generation_by_result_msg_id(ref_msg_id)
    if rec is None:
        await callback.answer("Не найдена запись для повтора.", show_alert=True)
        return

    mode = rec.mode
    room_type = rec.room_type
    style_choice = rec.style
    furniture_choice = rec.furniture
    src_image_path = rec.src_image_path

    cover_rel = 'img/bot/design.png' if mode == 'redesign' else 'img/bot/zero_design.png'
    wait_msg = await bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=FSInputFile(get_file_path(cover_rel)),
        caption=TEXT_WAIT
    )

    coro = generate_design(
        image_path=src_image_path,
        style=style_choice,
        room_type=room_type,
        furniture=furniture_choice
    )
    image_url = await run_long_operation_with_action(
        bot=bot,
        chat_id=callback.from_user.id,
        action=ChatAction.UPLOAD_PHOTO,
        coro=coro
    )

    if not image_url:
        await _edit_text_or_caption(wait_msg, SORRY_TRY_AGAIN)
        return

    if _is_data_url(image_url):
        image_bytes, _ = _data_url_to_bytes(image_url)
    else:
        image_bytes = await download_image_from_url(image_url)
    if not image_bytes:
        await _edit_text_or_caption(wait_msg, UNSUCCESSFUL_TRY_LATER)
        return

    planned_msg_id = wait_msg.message_id
    planned_path = save_bytes_as_png(image_bytes, planned_msg_id)

    result_msg = await _edit_or_replace_with_photo_file(
        bot=bot,
        msg=wait_msg,
        file_path=str(planned_path),
        caption=TEXT_FINAL,
        kb=None
    )
    final_msg_id = result_msg.message_id
    final_path = planned_path if final_msg_id == planned_msg_id else rename_for_new_msg_id(planned_path, final_msg_id)

    back_cb = "redesign.back_to_upload" if mode == "redesign" else "zerodesign.back_to_upload"
    kb = kb_result_actions(result_msg_id=final_msg_id, back_cb=back_cb)
    try:
        await result_msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

    try:
        save_generation_record(
            result_msg_id=final_msg_id,
            user_id=callback.from_user.id,
            chat_id=callback.message.chat.id,
            mode=mode,
            style=style_choice,
            room_type=room_type,
            furniture=furniture_choice,
            src_image_path=src_image_path,
            result_image_path=str(final_path),
        )
    except Exception as e:
        print(f"[design_db] save_generation_record (retry) error: {e}")


#########################################################################################################
################################## HTTP CLIENT: GENERATE FLOOR PLAN #####################################
#########################################################################################################
async def generate_design(
    image_path: str,
    *,
    style: str,
    room_type: str | None = None,
    furniture: str | None = None,
) -> str | None:
    """
    Клиент к executor: передаём исходное изображение и параметры,
    из которых executor соберёт промпт.
    """
    return await _post_image(
        "/api/v1/design/generate",
        image_path=image_path,
        style=style,
        room_type=room_type,
        furniture=furniture,
    )



async def _post_image(
    endpoint: str,
    *,
    image_path: str,
    style: str,
    room_type: str | None = None,
    furniture: str | None = None,
) -> str | None:
    # полезно иметь request-id и debug для логов executor'а
    req_id = f"dg-{uuid.uuid4().hex[:8]}-{int(datetime.utcnow().timestamp())}"
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}{endpoint}"
    try:
        # Читаем в память и закрываем файл сразу (на Windows это критично)
        with open(image_path, "rb") as f:
            file_bytes = f.read()

        form = aiohttp.FormData()
        form.add_field(
            "image",
            file_bytes,  # <-- bytes вместо открытого файла
            filename=os.path.basename(image_path),
            content_type="image/png",
        )
        # Передаём структурные поля вместо готового промпта
        form.add_field("style", style)
        if room_type:
            form.add_field("room_type", room_type)
        if furniture:
            form.add_field("furniture", furniture)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                params={"debug": "1"},
                data=form,
                timeout=600,
                headers={"X-Request-ID": req_id},
            ) as resp:
                if resp.status == 200:
                    js = await resp.json()
                    # 1) обычный url
                    url_val = js.get("url")
                    if url_val:
                        return url_val
                    # 2) фолбэк: images[0] (может быть data:URL)
                    imgs = js.get("images") or []
                    if isinstance(imgs, list) and imgs:
                        return imgs[0]
                    return None
                else:
                    txt = await resp.text()
                    print(f"Executor error {resp.status}: {txt}")
                    return None
    except Exception as e:
        print(f"HTTP client error: {e}")
        return None


# =============================================================================
# Router
# =============================================================================

def router(rt: Router) -> None:

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
    # Назад с результата к загрузке (редизайн)
    rt.callback_query.register(handle_redesign_back_to_upload, F.data == "redesign.back_to_upload")

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
    # Назад с результата к загрузке (zero-design)
    rt.callback_query.register(handle_zero_back_to_upload, F.data == "zerodesign.back_to_upload")

    # NEW: повторная генерация по кнопке под картинкой
    rt.callback_query.register(handle_retry_generation, F.data.startswith("d.rep="))
