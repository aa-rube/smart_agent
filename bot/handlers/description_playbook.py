# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\description_playbook.py
from __future__ import annotations
from typing import Optional, List, Dict

import aiohttp
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction

from bot.config import EXECUTOR_BASE_URL
from bot.states.states import DescriptionStates
from bot.utils.chat_actions import run_long_operation_with_action
import executor.ai_config as ai_cfg  # варианты кнопок из конфига

# ==========================
# Тексты
# ==========================
DESC_INTRO  = "🧩 Давайте соберём базовые характеристики объекта. Отвечайте по шагам:"
ASK_TYPE    = "1️⃣ Выберите тип недвижимости:"
ASK_CLASS   = "2️⃣ Уточните класс квартиры:"
ASK_COMPLEX = "3️⃣ Объект в новостройке / ЖК?"
ASK_AREA    = "4️⃣ Где расположен объект?"
ASK_COMMENT = (
    "5️⃣ Добавьте свободный комментарий про объект — планировка, площадь, этаж, состояние, окружение и т.д.\n\n"
    "✍️ Просто отправьте текст одним сообщением.\nЕсли комментарий не нужен — нажмите «Пропустить»."
)

GENERATING = "⏳ Генерирую описание… это займёт до минуты."
ERROR_TEXT = "😔 Не получилось сгенерировать описание. Попробуйте ещё раз."

# ==========================
# Утилиты редактирования
# ==========================
async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Обновить текст/подпись и клавиатуру текущего сообщения (без создания нового)."""
    try:
        await msg.edit_text(text, reply_markup=kb); return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_caption(caption=text, reply_markup=kb); return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

def _split_for_telegram(text: str, limit: int = 4000) -> List[str]:
    """Нарезает ответ на куски <= limit символов по строкам/абзацам."""
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    chunk: List[str] = []
    length = 0
    for line in text.splitlines(True):  # сохраняем \n
        if length + len(line) > limit and chunk:
            parts.append("".join(chunk)); chunk = [line]; length = len(line)
        else:
            chunk.append(line); length += len(line)
    if chunk:
        parts.append("".join(chunk))
    return parts

# ==========================
# Клавиатуры из конфига
# ==========================
def _kb_from_map(m: Dict[str, str], prefix: str, columns: int = 1) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key, label in m.items():
        btn = InlineKeyboardButton(text=label, callback_data=f"{prefix}{key}")
        if columns <= 1:
            rows.append([btn])
        else:
            row.append(btn)
            if len(row) >= columns:
                rows.append(row); row = []
    if row:
        rows.append(row)
    # Кнопка «Назад» (если нужна единая навигация по боту)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_tools")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_type()    -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_TYPES,   "desc_type_",   1)
def kb_class()   -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_CLASSES,"desc_class_",  1)
def kb_complex() -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_COMPLEX,"desc_complex_",1)
def kb_area()    -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_AREA,   "desc_area_",   1)

def kb_skip_comment() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить комментарий", callback_data="desc_comment_skip")]
    ])

def kb_retry() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Ещё раз", callback_data="description")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="ai_tools")]
    ])

# ==========================
# HTTP к контроллеру
# ==========================
async def _request_description_text(fields: dict, *, timeout_sec: int = 70) -> str:
    """
    Шлём СЫРЫЕ поля в executor (/api/v1/description/generate) и ждём чистый текст.
    fields = {type, apt_class?, in_complex, area, comment}
    """
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/description/generate"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=t) as session:
        async with session.post(url, json=fields) as resp:
            if resp.status != 200:
                try:
                    data = await resp.json()
                    detail = data.get("detail") or data.get("error") or str(data)
                except Exception:
                    detail = await resp.text()
                raise RuntimeError(f"Executor HTTP {resp.status}: {detail}")
            data = await resp.json()
            txt = (data or {}).get("text", "").strip()
            if not txt:
                raise RuntimeError("Executor returned empty text")
            return txt

# ==========================
# Шаги (callbacks)
# ==========================
async def start_description_flow(cb: CallbackQuery, state: FSMContext):
    """Старт: редактируем текущее сообщение → ввод типа объекта."""
    await state.clear()
    await _edit_text_or_caption(cb.message, f"{DESC_INTRO}\n\n{ASK_TYPE}", kb_type())
    await state.set_state(DescriptionStates.waiting_for_type)
    await cb.answer()

async def handle_type(cb: CallbackQuery, state: FSMContext):
    """
    type = flat / house / land ...
    - flat  → спрашиваем класс квартиры
    - house → ПРОПУСКАЕМ «новостройка/ЖК», сразу спрашиваем расположение
    - иное → спрашиваем «новостройка/ЖК» (как раньше)
    """
    val = cb.data.removeprefix("desc_type_")
    await state.update_data(type=val)

    if val == "flat":
        await _edit_text_or_caption(cb.message, ASK_CLASS, kb_class())
        await state.set_state(DescriptionStates.waiting_for_class)
    elif val == "house" or val == "land":
        # СКИП «новостройка/ЖК» для дома, идём сразу к расположению
        await _edit_text_or_caption(cb.message, ASK_AREA, kb_area())
        await state.set_state(DescriptionStates.waiting_for_area)
    else:
        await _edit_text_or_caption(cb.message, ASK_COMPLEX, kb_complex())
        await state.set_state(DescriptionStates.waiting_for_complex)

    await cb.answer()

async def handle_class(cb: CallbackQuery, state: FSMContext):
    """apt_class = econom / comfort / business / premium (только для квартир)."""
    val = cb.data.removeprefix("desc_class_")
    await state.update_data(apt_class=val)
    # после класса — вопрос про новостройку/ЖК
    await _edit_text_or_caption(cb.message, ASK_COMPLEX, kb_complex())
    await state.set_state(DescriptionStates.waiting_for_complex)
    await cb.answer()

async def handle_complex(cb: CallbackQuery, state: FSMContext):
    """in_complex = yes / no"""
    val = cb.data.removeprefix("desc_complex_")
    await state.update_data(in_complex=val)
    await _edit_text_or_caption(cb.message, ASK_AREA, kb_area())
    await state.set_state(DescriptionStates.waiting_for_area)
    await cb.answer()

async def handle_area(cb: CallbackQuery, state: FSMContext):
    """area = city / out → затем просим свободный комментарий (или «Пропустить»)."""
    val = cb.data.removeprefix("desc_area_")
    await state.update_data(area=val)
    await _edit_text_or_caption(cb.message, ASK_COMMENT, kb_skip_comment())
    await state.set_state(DescriptionStates.waiting_for_comment)
    await cb.answer()

# ==========================
# Финал (message/skip)
# ==========================
async def _generate_and_output(
    message: Message,
    state: FSMContext,
    bot: Bot,
    comment: Optional[str],
    *,
    reuse_anchor: bool = False,   # <-- если True, НЕ срываем якорь (используем текущее сообщение)
) -> None:
    """
    Собираем сырые поля и шлём их в executor.
    Если reuse_anchor=True — редактируем текущее сообщение (без создания нового).
    """
    data = await state.get_data()

    fields = {
        "type":       data.get("type"),
        "apt_class":  (data.get("apt_class") if data.get("type") == "flat" else None),
        "in_complex": data.get("in_complex"),
        "area":       data.get("area"),
        "comment":    (comment or "").strip(),
    }
    # Для ДОМА — принудительно обнуляем in_complex (не применимо)
    if data.get("type") == "house":
        fields["in_complex"] = None

    if reuse_anchor:
        # НЕ срываем якорь: редактируем текущее сообщение
        try:
            await message.edit_text(GENERATING)
        except TelegramBadRequest:
            # если нельзя редактировать (например, это была подпись к фото) — попробуем подпись
            try:
                await message.edit_caption(caption=GENERATING)
            except TelegramBadRequest:
                pass
        anchor_id = message.message_id
    else:
        # создаём НОВОЕ сообщение-экран
        gen_msg = await message.answer(GENERATING)
        anchor_id = gen_msg.message_id

    async def _do_req():
        return await _request_description_text(fields)

    try:
        text = await run_long_operation_with_action(
            bot=bot, chat_id=message.chat.id, action=ChatAction.TYPING, coro=_do_req()
        )
        parts = _split_for_telegram(text)

        # редактируем anchor результатом
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=anchor_id,
                text=parts[0],
                reply_markup=kb_retry()
            )
        except TelegramBadRequest:
            await message.answer(parts[0], reply_markup=kb_retry())

        for p in parts[1:]:
            await message.answer(p)

    except Exception:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=anchor_id,
                text=ERROR_TEXT,
                reply_markup=kb_retry()
            )
        except TelegramBadRequest:
            await message.answer(ERROR_TEXT, reply_markup=kb_retry())

    finally:
        await state.clear()

async def handle_comment_message(message: Message, state: FSMContext, bot: Bot):
    await _generate_and_output(message, state, bot, comment=message.text or "", reuse_anchor=False)

async def handle_comment_skip(cb: CallbackQuery, state: FSMContext, bot: Bot):
    await _edit_text_or_caption(cb.message, "Комментарий пропущен. Начинаю генерацию…")
    await _generate_and_output(cb.message, state, bot, comment=None, reuse_anchor=True)
    await cb.answer()

# ==========================
# Router
# ==========================
def router(rt: Router):
    # старт
    rt.callback_query.register(start_description_flow, F.data == "description")
    rt.callback_query.register(start_description_flow, F.data == "desc_start")

    # пошаговые выборы
    rt.callback_query.register(handle_type,    F.data.startswith("desc_type_"))
    rt.callback_query.register(handle_class,   F.data.startswith("desc_class_"))
    rt.callback_query.register(handle_complex, F.data.startswith("desc_complex_"))
    rt.callback_query.register(handle_area,    F.data.startswith("desc_area_"))

    # свободный комментарий / пропуск
    rt.message.register(handle_comment_message, DescriptionStates.waiting_for_comment, F.text)
    rt.callback_query.register(handle_comment_skip, F.data == "desc_comment_skip", DescriptionStates.waiting_for_comment)
