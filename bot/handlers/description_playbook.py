# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\description_playbook.py
# секрет офигенного бота: тебе не нужен якорь.
# Пользуйся такой схемой:
# -если callback -> обновляем сообщение, msg_id берем из update
# -если обычный text_message, command -> отправляй новое сообщение.
# Используй fallback если изменить не удалось.
# Все, никаких anchors которые нужно настраивать, никаких залипаний, кучи сообщение и мисс-кликов.

from __future__ import annotations
from typing import Optional, List, Dict
import os

import aiohttp
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, InputMediaPhoto
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction

from bot.config import EXECUTOR_BASE_URL, get_file_path
from bot.states.states import DescriptionStates
from bot.utils.chat_actions import run_long_operation_with_action
import executor.ai_config as ai_cfg  # варианты кнопок из конфига

# ====== Доступ / подписка (как в plans/design) ======
import bot.utils.database as db
from bot.utils.database import is_trial_active, trial_remaining_hours

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

def text_descr_intro(user_id: int) -> str:
    """Стартовый текст с информацией о доступе (как в plans)."""
    return f"{DESC_INTRO}\n\n{_format_access_text(user_id)}\n\n{ASK_TYPE}"


# ==========================
# Клавиатуры
# ==========================
def kb_type()    -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_TYPES,   "desc_type_",   1)
def kb_class()   -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_CLASSES,"desc_class_",  1)
def kb_complex() -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_COMPLEX,"desc_complex_",1)
def kb_area()    -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_AREA,   "desc_area_",   1)

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

async def _edit_or_replace_with_photo_file(
    bot: Bot, msg: Message, file_path: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    """
    Поменять текущее сообщение на фото с подписью и клавиатурой.
    Если редактирование невозможно (сообщение было текстовым и т.п.) — удаляем и шлём новое фото.
    """
    try:
        media = InputMediaPhoto(media=FSInputFile(file_path), caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        # удаляем старое и отправляем новое фото (визуально как «апдейт» экрана)
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(file_path), caption=caption, reply_markup=kb)

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
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_skip_comment() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить комментарий", callback_data="desc_comment_skip")]
    ])

def kb_retry() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Ещё раз", callback_data="description")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")]
    ])

# Кнопка к офферу подписки
SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)

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
DESCR_HOME_IMG_REL = "img/bot/descr_home.png"

async def start_description_flow(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Старт: пытаемся заменить текущее сообщение на картинку (главный экран раздела)
    с подписью (DESC_INTRO + ASK_TYPE) и кнопками. Если файла нет — фолбэк на текст.
    """
    user_id = cb.message.chat.id
    # Контроль доступа (как в plans/design)
    if not _has_access(user_id):
        # Сообщение об отсутствии доступа идентично подходу в plans.py
        if not _is_sub_active(user_id):
            await _edit_text_or_caption(cb.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(cb.message, SUB_PAY, SUBSCRIBE_KB)
        await cb.answer()
        return

    await state.clear()
    caption = text_descr_intro(user_id)
    img_path = get_file_path(DESCR_HOME_IMG_REL)

    if os.path.exists(img_path):
        await _edit_or_replace_with_photo_file(bot, cb.message, img_path, caption, kb_type())
    else:
        await _edit_text_or_caption(cb.message, caption, kb_type())

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
    # Повторный контроль доступа перед генерацией (на случай, если стейт «завис»)
    user_id = message.chat.id
    if not _has_access(user_id):
        # Тексты как в plans.py
        text = SUB_FREE if not _is_sub_active(user_id) else SUB_PAY
        try:
            await message.edit_text(text, reply_markup=SUBSCRIBE_KB)
        except TelegramBadRequest:
            try:
                await message.edit_caption(caption=text, reply_markup=SUBSCRIBE_KB)
            except TelegramBadRequest:
                await message.answer(text, reply_markup=SUBSCRIBE_KB)
        await state.clear()
        return

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
    rt.callback_query.register(start_description_flow, F.data == "nav.descr_home")
    rt.callback_query.register(start_description_flow, F.data == "desc_start")

    # пошаговые выборы
    rt.callback_query.register(handle_type,    F.data.startswith("desc_type_"))
    rt.callback_query.register(handle_class,   F.data.startswith("desc_class_"))
    rt.callback_query.register(handle_complex, F.data.startswith("desc_complex_"))
    rt.callback_query.register(handle_area,    F.data.startswith("desc_area_"))

    # свободный комментарий / пропуск
    rt.message.register(handle_comment_message, DescriptionStates.waiting_for_comment, F.text)
    rt.callback_query.register(handle_comment_skip, F.data == "desc_comment_skip", DescriptionStates.waiting_for_comment)
