#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\plans.py
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
from bot.states.states import FloorPlanStates
from executor.prompt_factory import create_floor_plan_prompt
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import generate_floor_plan
from bot.utils.file_utils import safe_remove


# ===========================
# Доступ / подписка
# ===========================

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


# ===========================
# Тексты
# ===========================

_TEXT_GET_FILE_PLAN_TPL = """
1️⃣ Загрузи *план/чертёж помещения* — подойдёт изображение (jpg/png) или PDF (1 страница). Ссылка на картинку тоже ок.

2️⃣ Выбери стиль визуализации (скетч или реализм) и интерьерный стиль.

3️⃣ Получи макет за 1–2 минуты 💡

{tokens_text}

Готов? Кидай файл сюда 👇
""".strip()

def text_get_file_plan(user_id: int) -> str:
    return _TEXT_GET_FILE_PLAN_TPL.format(tokens_text=_format_access_text(user_id))

TEXT_GET_VIZ = "Выберите стиль визуализации плана:"
TEXT_GET_STYLE = "Отлично! Теперь выберите интерьерный стиль 🖼️"
TEXT_FINAL = "✅ Готово! Вот визуализация планировки."
ERROR_WRONG_INPUT = "❌ Пожалуйста, отправь изображение (jpg/png), PDF (1 страница) или прямую ссылку на картинку."
ERROR_PDF_PAGES = "❌ В PDF должно быть не больше одной страницы."
ERROR_LINK = "❌ Не удалось скачать изображение по ссылке. Нужна прямая ссылка на файл (jpg/png)."
SORRY_TRY_AGAIN = "😔 Не удалось сгенерировать изображение. Попробуйте ещё раз."

SUB_FREE = """
🎁 Бесплатный период завершён
Пробный доступ на 72 часа истёк — дальше только по подписке.

📦* Что даёт подписка:*
 — Полный доступ ко всем инструментам
 — Без ограничений по количеству запусков в период подписки*
Стоимость пакета всего 2500 рублей!
""".strip()

SUB_PAY = """
🪫 Подписка не активна
Срок подписки истёк или не был оформлен.

📦* Что даёт подписка:*
 — Полный доступ ко всем инструментам
 — Без ограничений по количеству запусков в период подписки*
Стоимость пакета всего 2500 рублей!
""".strip()


# ===========================
# Клавиатуры
# ===========================

SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)

def kb_back_to_tools() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")]]
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
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_result_back() -> InlineKeyboardMarkup:
    """Клавиатура на экране результата, чтобы вернуться к загрузке нового плана."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ Загрузить другой план", callback_data="plan.back_to_upload")]]
    )


# ===========================
# Хелперы редактирования
# ===========================

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


# ===========================
# Хендлеры: создание планировок
# ===========================

async def start_plans_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Старт сценария «Планировки»: проверяем доступ → просим загрузить план/чертёж.
    Стартовый коллбек: "floor_plan"
    """
    user_id = callback.message.chat.id

    if _has_access(user_id):
        await state.set_state(FloorPlanStates.waiting_for_file)
        await _edit_or_replace_with_photo_file(
            bot=bot,
            msg=callback.message,
            file_path=get_file_path('img/bot/plan.png'),
            caption=text_get_file_plan(user_id),
            kb=kb_back_to_tools(),
        )
    else:
        if not _is_sub_active(user_id):
            await _edit_text_or_caption(callback.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, SUBSCRIBE_KB)

    await callback.answer()


async def handle_plan_file(message: Message, state: FSMContext, bot: Bot):
    """
    Получаем файл/ссылку → конвертируем PDF (1 страница) в png → сохраняем → предлагаем стиль визуализации.
    """
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
        # сохраняем временно на диск сами (без save_image_as_png), имя — по user_id
        plan_path = get_file_path(f"img/tmp/plan_{user_id}.png")
        os.makedirs(os.path.dirname(plan_path), exist_ok=True)
        with open(plan_path, "wb") as f:
            f.write(image_bytes)

        await state.update_data(plan_path=plan_path)
        await message.answer(TEXT_GET_VIZ, reply_markup=kb_visualization_style())
        await state.set_state(FloorPlanStates.waiting_for_visualization_style)


async def handle_visualization_style(callback: CallbackQuery, state: FSMContext):
    viz_style = "sketch" if callback.data == "viz_sketch" else "realistic"
    await state.update_data(visualization_style=viz_style)
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=kb_style_choices())
    await state.set_state(FloorPlanStates.waiting_for_style)
    await callback.answer()


async def handle_style_plan(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Финиш: собрали viz+style → генерируем картинку из плана."""
    await callback.answer("Принято! Начинаю генерацию...")

    user_id = callback.from_user.id
    if not _has_access(user_id):
        if not _is_sub_active(user_id):
            await _edit_text_or_caption(callback.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, SUBSCRIBE_KB)
        await state.clear()
        return

    data = await state.get_data()
    plan_path = data.get("plan_path")
    viz = data.get("visualization_style")

    try:
        _, style_raw = (callback.data or "").split("_", 1)
        interior_style = style_raw
    except Exception:
        interior_style = "Модерн"

    prompt = create_floor_plan_prompt(
        visualization_style=viz,
        interior_style=interior_style
    )

    await _edit_text_or_caption(callback.message, "⏳ Генерирую визуализацию… Это может занять до 1–2 минут.")

    try:
        coro = generate_floor_plan(floor_plan_path=plan_path, prompt=prompt)
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            await _edit_or_replace_with_photo_url(bot, callback.message, image_url, TEXT_FINAL, kb=kb_result_back())
        else:
            await _edit_text_or_caption(callback.message, SORRY_TRY_AGAIN, kb=kb_back_to_tools())

    finally:
        if plan_path and os.path.exists(plan_path):
            if safe_remove(plan_path):
                print(f"Временный файл удален: {plan_path}")
            else:
                print(f"Не удалось удалить временный файл (занят): {plan_path}")
        await state.clear()


async def handle_plan_back_to_upload(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Кнопка «Назад» с экрана результата:
    1) убираем клавиатуру у сообщения, где нажали кнопку;
    2) отправляем новое сообщение с экраном «загрузите план»;
    3) переводим стейт в ожидание файла.
    """
    user_id = callback.from_user.id
    # 1) убрать клавиатуру у текущего сообщения
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    # 2) отправить новый экран загрузки
    await state.set_state(FloorPlanStates.waiting_for_file)
    await bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=FSInputFile(get_file_path('img/bot/plan.png')),
        caption=text_get_file_plan(user_id),
        reply_markup=kb_back_to_tools(),
    )

    await callback.answer()


# ===========================
# Router
# ===========================

def router(rt: Router):
    # Старт сценария планировок
    rt.callback_query.register(start_plans_flow, F.data == "floor_plan")

    # Загрузка файла → выбор виз-стиля
    rt.message.register(
        handle_plan_file,
        FloorPlanStates.waiting_for_file,
        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT})
    )
    # Виз-стиль → интерьерный стиль
    rt.callback_query.register(handle_visualization_style, FloorPlanStates.waiting_for_visualization_style)
    # Запуск генерации
    rt.callback_query.register(handle_style_plan, FloorPlanStates.waiting_for_style)
    # Кнопка «назад к загрузке плана» с экрана результата
    rt.callback_query.register(handle_plan_back_to_upload, F.data == "plan.back_to_upload")
