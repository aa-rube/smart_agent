# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\design_planes.py
from __future__ import annotations

import os
import fitz
import aiohttp

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, ContentType
)
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest

from typing import Optional

import bot.utils.tokens as tk
import bot.utils.database as db
from bot.config import get_file_path
from bot.states.states import DesignStates, ZeroDesignStates
from executor.prompt_factory import create_floor_plan_prompt, create_prompt
from bot.utils.image_processor import save_image_as_png
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import (
    generate_floor_plan,         # для Редизайна
    generate_design,             # для Zero-Design
    download_image_from_url,     # загрузка результата по URL (Zero-Design)
)
from bot.utils.file_utils import safe_remove
from bot.utils import youmoney


# =============================================================================
# ТЕКСТЫ (ТОЛЬКО ДЛЯ БЛОКА ДИЗАЙНА)
# =============================================================================

def _format_tokens_text(user_id: int) -> str:
    is_sub = db.get_variable(user_id, 'have_sub')
    tokens = db.get_variable(user_id, 'tokens')
    try:
        tokens_int = int(tokens)
    except Exception:
        tokens_int = 0

    if tokens_int <= 0:
        return '😢 У вас закончились генерации. Пополните, используя команду /sub'
    if is_sub == '1':
        return f'🔋 У тебя есть *{tokens_int} генераций дизайна*'
    return f'🎁 У тебя есть *{tokens_int} бесплатных дизайна* на тест — используй с умом 😉'


def _start_screen_text(user_id: int) -> str:
    tokens_text = _format_tokens_text(user_id)
    return f"""
*1️⃣ Выбери, что нужно:*

\t• 🛋 *Редизайн интерьера* — получите реалистичный дизайн помещения в новом стиле, сохранив планировку.

\t• 🆕 *Дизайн с нуля* — загрузи фото пустого помещения — получи визуализацию, которая помогает продавать дороже.

2️⃣ Получи готовый дизайн за 1–2 минуты 💡

{tokens_text}

Готов? Загружай файл прямо сюда 👇
""".strip()


_TEXT_GET_FILE_REDESIGN_TPL = """
1️⃣ Загрузи *план/фото помещения* — подойдёт ссылка, фото (jpeg, jpg, png), скан или PDF.

2️⃣ Получи готовый дизайн за 1–2 минуты 💡

{tokens_text}

Готов? Загружай файл прямо сюда 👇
""".strip()


def text_get_file_redesign(user_id: int) -> str:
    return _TEXT_GET_FILE_REDESIGN_TPL.format(tokens_text=_format_tokens_text(user_id))


TEXT_GET_STYLE = "Отлично! Теперь выбери стиль оформления 🖼️"
TEXT_FINAL = "✅ Готово!\nТвоя обновленная визуализация теперь готова влюблять в себя покупателей!"
ERROR_WRONG_INPUT = "❌ Пожалуйста, отправь фото, PDF (1 страница) или ссылку на изображение."
ERROR_PDF_PAGES = "❌ Ошибка! В PDF-файле должно быть не больше одной страницы."
ERROR_LINK = "❌ Не удалось загрузить изображение по этой ссылке. Убедись, что она ведет прямо на картинку (jpg, png)."
SORRY_TRY_AGAIN = "😔 К сожалению, не удалось сгенерировать изображение. Попробуйте ещё раз."
UNSUCCESSFUL_TRY_LATER = "😔 Не удалось скачать сгенерированное изображение. Попробуйте позже."

TEXT_GET_FILE_ZERO = "Загрузи фото помещения — подойдёт ссылка или фото в jpeg, jpg, png или PDF."
TEXT_PHOTO_UPLOADED = "Отлично! 📸\nТеперь выбери, какое это помещение:"
TEXT_GET_FURNITURE_OPTION = """
Хочешь дизайн с мебелью или без?
🛋 С мебелью — сразу видно, как может выглядеть готовый интерьер.
▫️ Без мебели — чистое пространство, акцент на отделке и ощущении масштаба. Прекрасный вариант для новостроек.

Выбери вариант 👇
(Если не уверен — рекомендую с мебелью для вау-эффекта)
""".strip()

SUB_FREE = """
🎁 Упс… Бесплатный лимит исчерпан
Ты использовал 2 бесплатных запроса — дальше только по подписке.

📦* Что даёт подписка:*
 — Пакет из 100 любых генераций
 — Доступ к 2D/3D и любым стилям
Стоимость пакета всего 2500 рублей!
""".strip()

SUB_PAY = """
🪫 Упс… Лимит токенов исчерпан — теперь нужно обновить подписку.

📦* Что даёт подписка:*
 — Пакет из 100 любых генераций
 — Доступ к 2D/3D и любым стилям
Стоимость пакета всего 2500 рублей!
""".strip()


# =============================================================================
# КЛАВИАТУРЫ (ТОЛЬКО ДЛЯ БЛОКА ДИЗАЙНА)
# =============================================================================

def kb_design_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛋 Редизайн интерьера", callback_data="redesign")],
            [InlineKeyboardButton(text="🆕 Дизайн с нуля", callback_data="0design")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")],
        ]
    )


def kb_visualization_style() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖊️ Скетч-стиль", callback_data="viz_sketch")],
            [InlineKeyboardButton(text="📸 Реалистичный стиль", callback_data="viz_realistic")],
        ]
    )


def kb_style_choices() -> InlineKeyboardMarkup:
    styles = [
        "Современный", "Скандинавский", "Классика", "Минимализм", "Хай-тек",
        "Лофт", "Эко-стиль", "Средиземноморский", "Барокко", "Неоклассика",
        "🔥 Случайный выбор ИИ",
    ]
    rows = [[InlineKeyboardButton(text=f"💎 {s}", callback_data=f"style_{s}")] for s in styles]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_room_type() -> InlineKeyboardMarkup:
    rooms = ["🍳 Кухня", "🛏 Спальня", "🛋 Гостиная", "🚿 Ванная", "🚪 Прихожая"]
    rows = []
    line = []
    for r in rooms:
        line.append(InlineKeyboardButton(text=r, callback_data=f"room_{r}"))
        if len(line) == 2:
            rows.append(line)
            line = []
    if line:
        rows.append(line)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_furniture() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛋 С мебелью", callback_data="furniture_yes")],
            [InlineKeyboardButton(text="▫️ Без мебели", callback_data="furniture_no")],
        ]
    )


def kb_subscribe(user_id: int) -> InlineKeyboardMarkup:
    url = youmoney.create_pay(user_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", url=url)]]
    )


def kb_back_to_tools() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")]]
    )


# =============================================================================
# ХЕЛПЕРЫ ОБНОВЛЕНИЯ/ЗАМЕНЫ СООБЩЕНИЯ
# =============================================================================

async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Обновить текст/подпись/клавиатуру текущего сообщения (без создания нового)."""
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
    """
    Поменять контент текущего сообщения на фото с подписью (из файла).
    Если не удаётся заменить — удаляем старое и отправляем фото заново.
    """
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
    """Заменить на фото по URL; при неудаче — отправить заново новым сообщением."""
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
# ГЛАВНЫЙ ЭКРАН «ДИЗАЙН» (nav.design_home)
# =============================================================================

async def design_home(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Показываем карточку раздела: пытаемся заменить текущее сообщение
    на картинку main_design.jpg + caption; при неудаче — фолбэк на текст.
    """
    await state.clear()
    user_id = callback.from_user.id

    main_rel = "img/bot/main_design.jpg"  # data/img/bot/main_design.jpg
    main_path = get_file_path(main_rel)
    caption = _start_screen_text(user_id)

    if os.path.exists(main_path):
        await _edit_or_replace_with_photo_file(bot, callback.message, main_path, caption, kb_design_home())
    else:
        await _edit_text_or_caption(callback.message, caption, kb_design_home())

    await callback.answer()


# =============================================================================
# РЕДИЗАЙН ИНТЕРЬЕРА (по фото/плану)
# =============================================================================

async def start_design_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Начало редизайна: проверяем токены → показываем экран загрузки файла.
    """
    user_id = callback.message.chat.id

    if tk.get_tokens(user_id) > 0:
        await state.set_state(DesignStates.waiting_for_file)
        await _edit_or_replace_with_photo_file(
            bot=bot,
            msg=callback.message,
            file_path=get_file_path('img/bot/plan.jpg'),
            caption=text_get_file_redesign(user_id),
            kb=kb_back_to_tools(),
        )
    else:
        if db.get_variable(user_id, 'have_sub') == '0':
            await _edit_text_or_caption(callback.message, SUB_FREE, kb_subscribe(user_id))
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, kb_subscribe(user_id))

    await callback.answer()


async def handle_file_redesign(message: Message, state: FSMContext, bot: Bot):
    """Загрузка файла/ссылки для редизайна → ждём выбор типа визуализации."""
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
            await message.answer("Выберите стиль визуализации:", reply_markup=kb_visualization_style())
            await state.set_state(DesignStates.waiting_for_visualization_style)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте ещё раз.")


async def handle_visualization_style(callback: CallbackQuery, state: FSMContext):
    """Выбор: скетч/реализм → дальше выбор интерьерного стиля (список)."""
    viz_style = "sketch" if callback.data == "viz_sketch" else "realistic"
    await state.update_data(visualization_style=viz_style)
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=kb_style_choices())
    await state.set_state(DesignStates.waiting_for_style)
    await callback.answer()


async def handle_style_redesign(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Финиш редизайна: генерируем по plan+style."""
    await callback.answer("Принято! Начинаю генерацию...")

    user_id = callback.from_user.id
    if tk.get_tokens(user_id) <= 0:
        if db.get_variable(user_id, 'have_sub') == '0':
            await _edit_text_or_caption(callback.message, SUB_FREE, kb_subscribe(user_id))
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, kb_subscribe(user_id))
        await state.clear()
        return

    user_data = await state.get_data()
    image_path = user_data.get("image_path")
    visualization_style = user_data.get("visualization_style")

    try:
        _, style_raw = (callback.data or "").split("_", 1)
        interior_style = style_raw
    except Exception:
        interior_style = "Модерн"

    prompt = create_floor_plan_prompt(
        visualization_style=visualization_style,
        interior_style=interior_style
    )

    await _edit_text_or_caption(callback.message, "⏳ Генерирую визуализацию… Это может занять до 1–2 минут.")

    try:
        coro = generate_floor_plan(floor_plan_path=image_path, prompt=prompt)
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            await _edit_or_replace_with_photo_url(bot, callback.message, image_url, TEXT_FINAL, kb=None)
            tk.remove_tokens(user_id)
        else:
            await _edit_text_or_caption(callback.message, SORRY_TRY_AGAIN, kb=kb_back_to_tools())

    finally:
        if image_path and os.path.exists(image_path):
            if safe_remove(image_path):
                print(f"Временный файл удален: {image_path}")
            else:
                print(f"Не удалось удалить временный файл (занят): {image_path}")
        await state.clear()


# =============================================================================
# ZERO-DESIGN (ДИЗАЙН С НУЛЯ) — ОТЛИЧИТЕЛЬНЫЕ МЕТОДЫ
# -----------------------------------------------------------------------------
# В этом сценарии после загрузки фото:
# 1) Выбираем тип помещения → 2) Выбираем «с мебелью / без» → 3) Выбираем стиль
# После чего генерируется итоговая картинка (generate_design), результат скачивается
# по URL и отправляется как фото (если замена невозможна — новым сообщением).
# =============================================================================

async def start_zero_design_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Старт Zero-Design: экран загрузки фото + переход в состояние ожидания файла."""
    user_id = callback.message.chat.id

    if tk.get_tokens(user_id) > 0:
        await state.set_state(ZeroDesignStates.waiting_for_file)
        await _edit_or_replace_with_photo_file(
            bot=bot,
            msg=callback.message,
            file_path=get_file_path('img/bot/zero_design.jpg'),
            caption=TEXT_GET_FILE_ZERO,
            kb=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")]]
            ),
        )
    else:
        if db.get_variable(user_id, 'have_sub') == '0':
            await _edit_text_or_caption(callback.message, SUB_FREE, kb_subscribe(user_id))
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, kb_subscribe(user_id))

    await callback.answer()


async def handle_file_zero(message: Message, state: FSMContext, bot: Bot):
    """Загрузка файла для Zero-Design → выбор типа помещения."""
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
            await message.answer(TEXT_PHOTO_UPLOADED, reply_markup=kb_room_type())
            await state.set_state(ZeroDesignStates.waiting_for_room_type)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте ещё раз.")


async def handle_room_type(callback: CallbackQuery, state: FSMContext):
    """Zero-Design: выбор типа помещения → выбор мебели."""
    await state.update_data(room_type=callback.data.split('_', 1)[1])
    await callback.message.edit_text(TEXT_GET_FURNITURE_OPTION, reply_markup=kb_furniture())
    await state.set_state(ZeroDesignStates.waiting_for_furniture)
    await callback.answer()


async def handle_furniture(callback: CallbackQuery, state: FSMContext):
    """Zero-Design: выбор меблировки → выбор стиля."""
    await state.update_data(furniture_choice=callback.data)  # furniture_yes | furniture_no
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=kb_style_choices())
    await state.set_state(ZeroDesignStates.waiting_for_style)
    await callback.answer()


async def handle_style_zero(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Zero-Design: генерируем по фото + room_type + furniture + style."""
    user_id = callback.from_user.id

    if tk.get_tokens(user_id) <= 0:
        if db.get_variable(user_id, 'have_sub') == '0':
            await _edit_text_or_caption(callback.message, SUB_FREE, kb_subscribe(user_id))
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, kb_subscribe(user_id))
        await state.clear()
        await callback.answer()
        return

    user_data = await state.get_data()
    image_path = user_data.get("image_path")
    room_type = user_data.get("room_type")
    furniture_choice = user_data.get("furniture_choice")

    # стиль из callback_data вида "style_<Название>"
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
                # сохраняем временно и отправляем как фото
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
                tk.remove_tokens(user_id)

                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            else:
                await _edit_text_or_caption(callback.message, UNSUCCESSFUL_TRY_LATER,
                                            kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                                                text="⬅️ Назад", callback_data="nav.design_home")]]))
        else:
            await _edit_text_or_caption(callback.message, SORRY_TRY_AGAIN,
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
# ROUTER
# =============================================================================

def router(rt: Router):
    # Главный экран раздела «Дизайн»
    rt.callback_query.register(design_home, F.data == 'nav.design_home')

    # Редизайн
    rt.callback_query.register(start_design_flow, F.data == "floor_plan")
    rt.message.register(
        handle_file_redesign,
        DesignStates.waiting_for_file,
        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT})
    )
    rt.callback_query.register(handle_visualization_style, DesignStates.waiting_for_visualization_style)
    rt.callback_query.register(handle_style_redesign, DesignStates.waiting_for_style)

    # Zero-Design (Дизайн с нуля)
    rt.callback_query.register(start_zero_design_flow, F.data == "0design")
    rt.message.register(
        handle_file_zero,
        ZeroDesignStates.waiting_for_file,
        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT})
    )
    rt.callback_query.register(handle_room_type, ZeroDesignStates.waiting_for_room_type)
    rt.callback_query.register(handle_furniture, ZeroDesignStates.waiting_for_furniture)
    rt.callback_query.register(handle_style_zero, ZeroDesignStates.waiting_for_style)
