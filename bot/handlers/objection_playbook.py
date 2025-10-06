# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\objection_playbook.py
from __future__ import annotations

from typing import Optional, List
import logging
from pathlib import Path

import aiohttp
from aiogram import Router, F, Bot
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    InputMediaPhoto,
)

from bot.config import EXECUTOR_BASE_URL, get_file_path
from bot.states.states import ObjectionStates
from bot.utils.chat_actions import run_long_operation_with_action

# ===========================
# Доступ / подписка (как в plans)
# ===========================
import bot.utils.database as app_db          # триал / история / согласия
import bot.utils.billing_db as billing_db     # карты / подписки / платежный лог
from bot.utils.database import (
    is_trial_active, trial_remaining_hours
)

def _is_sub_active(user_id: int) -> bool:
    """
    Новая логика: считаем подписку активной, если у пользователя
    есть сохранённая карта для рекуррентных списаний (активная подписка).
    Дату sub_until больше не храним.
    """
    return bool(billing_db.has_saved_card(user_id))

def _format_access_text(user_id: int) -> str:
    trial_hours = trial_remaining_hours(user_id)
    # активный триал — показываем дату окончания, если есть
    if is_trial_active(user_id):
        try:
            until_dt = app_db.get_trial_until(user_id)
            if until_dt:
                return f'🆓 Бесплатный доступ активен до *{until_dt.date().isoformat()}* (~{trial_hours} ч.)'
        except Exception:
            pass
        return f'🆓 Бесплатный доступ активен ещё *~{trial_hours} ч.*'
    # нет триала — проверяем наличие активной подписки (карта сохранена)
    if _is_sub_active(user_id):
        return '✅ Подписка активна (автопродление включено)'
    return '😢 Доступ закончился. Оформи подписку, чтобы продолжить.'

def _has_access(user_id: int) -> bool:
    return bool(is_trial_active(user_id) or _is_sub_active(user_id))


# ============================================================================
# UX текст (целиком внутри файла)
# ============================================================================

OBJECTION_HOME_TEXT = (
    "🤖 *ИИ-помощник для закрытия возражений*\n\n"
    "Отправьте формулировку возражения — я предложу несколько живых ответов, которые помогают:\n"
    "✅ Мягко снять сомнения клиента\n"
    "✅ Показать ценность ваших услуг\n"
    "✅ Сохранить контакт и продвинуть сделку вперёд\n\n"
    "Готовы? Нажмите кнопку ниже и введите возражение."
)

ASK_OBJECTION = (
    "✍️ Напишите, как именно сформулировал возражение ваш клиент.\n"
    "_Например:_ «У этого застройщика плохие отзывы по другим ЖК»"
)

GENERATING = "⏳ Генерирую варианты ответов… это займет до минуты."
ERROR_TEXT = (
    "😔 Не получилось сгенерировать сценарий отработки возражения.\n"
    "Проверьте подключение и попробуйте ещё раз."
)

# Стартовый текст с информацией о доступе (как в plans)
def _text_objection_home(user_id: int) -> str:
    return f"{OBJECTION_HOME_TEXT}\n\n{_format_access_text(user_id)}"

# ===========================
# Тексты и клавиатуры подписки
# ===========================
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

SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)

# ============================================================================
# Клавиатуры (целиком внутри файла)
# ============================================================================

def kb_home_entry() -> InlineKeyboardMarkup:
    """Экран модуля: старт + назад в инструменты."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧠 Отработать возражение", callback_data="objection")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")],
        ]
    )

def kb_back_to_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.objection_start")]
        ]
    )

def kb_retry() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Попробовать ещё раз", callback_data="obj_retry")],
            [InlineKeyboardButton(text="⬅️ В раздел", callback_data="nav.objection_start")],
        ]
    )


# ============================================================================
# Утилиты редактирования
# ============================================================================

async def _edit_text_or_caption(
    msg: Message,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
    *,
    parse_mode: Optional[str] = "Markdown",
) -> None:
    """Обновить текст/подпись и клавиатуру текущего сообщения (без создания нового)."""
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_caption(caption=text, reply_markup=kb, parse_mode=parse_mode)
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

def _split_for_telegram(text: str, limit: int = 4000) -> List[str]:
    """Нарезает ответ на куски <= limit символов по абзацам/строкам."""
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    chunk: List[str] = []
    length = 0
    for line in text.splitlines(True):  # сохраняем \n
        if length + len(line) > limit and chunk:
            parts.append("".join(chunk))
            chunk = [line]
            length = len(line)
        else:
            chunk.append(line)
            length += len(line)
    if chunk:
        parts.append("".join(chunk))
    return parts

# ============================================================================
# Редактирование с картинкой (фото + caption) с фоллбэками
# ============================================================================

async def _edit_or_replace_with_photo_cb(
    callback: CallbackQuery,
    image_rel_path: str,
    caption: str,
    kb: InlineKeyboardMarkup | None = None,
) -> None:
    """
    Пытаемся заменить текущий экран на фото с подписью:
    1) edit_media (если сообщение медийное)
    2) если было текстовым — удаляем его и отправляем новое фото
    3) если файла нет/ошибка — хотя бы обновим текст/клавиатуру
    """
    img_path = get_file_path(image_rel_path)
    if Path(img_path).exists():
        try:
            media = InputMediaPhoto(media=FSInputFile(img_path), caption=caption, parse_mode="Markdown")
            await callback.message.edit_media(media=media, reply_markup=kb)
            await callback.answer()
            return
        except TelegramBadRequest:
            # Было текстовое сообщение — удаляем и отправляем новое фото
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            try:
                await callback.bot.send_photo(
                    chat_id=callback.message.chat.id,
                    photo=FSInputFile(img_path),
                    caption=caption,
                    reply_markup=kb,
                    parse_mode="Markdown",
                )
                await callback.answer()
                return
            except Exception as e:
                logging.exception("Failed to send objection_home photo: %s", e)
        except Exception as e:
            logging.exception("Failed to edit objection_home media: %s", e)

    # Фоллбэк — обновим хотя бы текст текущего сообщения
    await _edit_text_or_caption(callback.message, caption, kb)
    await callback.answer()

# ============================================================================
# HTTP-клиент к контроллеру
# ============================================================================

async def _request_objection_text(question: str, *, timeout_sec: int = 70) -> str:
    """
    Отправляет вопрос в контроллер и возвращает чистый текст сценария.
    Исключения поднимает наверх — UI часть их отловит и покажет retry.
    """
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/objection/generate"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=t) as session:
        async with session.post(url, json={"question": question}) as resp:
            if resp.status != 200:
                # попробуем вытащить деталь
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

# ============================================================================
# Экраны и обработчики
# ============================================================================

async def objection_home(callback: CallbackQuery, state: FSMContext):
    """
    Домашний экран раздела «Закрытие возражений».
    """
    await state.clear()
    user_id = callback.message.chat.id
    await _edit_or_replace_with_photo_cb(
        callback=callback,
        image_rel_path="img/bot/objection.png",
        caption=_text_objection_home(user_id),
        kb=kb_home_entry(),
    )

async def start_objection_flow(callback: CallbackQuery, state: FSMContext):
    """
    Начало сценария: редактируем текущее сообщение, просим ввести возражение,
    сохраняем message_id как «якорь», чтобы дальше редактировать именно его.
    """
    user_id = callback.message.chat.id
    if not _has_access(user_id):
        # как в plans: различаем окончание трейла и неактивную подписку
        if not _is_sub_active(user_id):
            await _edit_text_or_caption(callback.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, SUBSCRIBE_KB)
        await state.clear()
        await callback.answer()
        return

    await state.update_data(anchor_id=callback.message.message_id)
    await _edit_text_or_caption(callback.message, ASK_OBJECTION, kb_back_to_home())
    await state.set_state(ObjectionStates.waiting_for_question)
    await callback.answer()

async def retry_objection(callback: CallbackQuery, state: FSMContext):
    """
    «Попробовать ещё раз» — возвращаемся к вводу.
    """
    user_id = callback.message.chat.id
    if not _has_access(user_id):
        if not _is_sub_active(user_id):
            await _edit_text_or_caption(callback.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, SUBSCRIBE_KB)
        await state.clear()
        await callback.answer()
        return

    data = await state.get_data()
    if not data.get("anchor_id"):
        await state.update_data(anchor_id=callback.message.message_id)
    await _edit_text_or_caption(callback.message, ASK_OBJECTION, kb_back_to_home())
    await state.set_state(ObjectionStates.waiting_for_question)
    await callback.answer()

async def handle_question(message: Message, state: FSMContext, bot: Bot):
    """
    Пользователь прислал формулировку возражения.
    ▶ Срываем якорь: всегда отправляем НОВОЕ сообщение «Генерирую…»
    ▶ Сохраняем его message_id как новый anchor_id
    ▶ По готовности редактируем именно это новое сообщение.
    """
    user_id = message.from_user.id
    if not _has_access(user_id):
        # Сообщаем о доступе и выходим, чтобы не жечь лимиты
        text = SUB_FREE if not _is_sub_active(user_id) else SUB_PAY
        await message.answer(text, reply_markup=SUBSCRIBE_KB)
        await state.clear()
        return

    chat_id = message.chat.id

    # 1) срываем якорь: создаём новое сообщение-экран
    gen_msg = await message.answer(GENERATING, parse_mode="Markdown")
    new_anchor_id = gen_msg.message_id
    await state.update_data(anchor_id=new_anchor_id)

    # 2) оборачиваем запрос к контроллеру «пишет…»
    async def _do_request():
        return await _request_objection_text(message.text)

    try:
        text = await run_long_operation_with_action(
            bot=bot,
            chat_id=chat_id,
            action=ChatAction.TYPING,
            coro=_do_request()
        )

        parts = _split_for_telegram(text)

        # Ответ от исполнителя содержит HTML-разметку — рендерим в HTML
        # 3) редактируем НОВОЕ сообщение результатом
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=new_anchor_id,
                text=parts[0],
                reply_markup=kb_retry(),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            # если нельзя редактировать — шлём новым сообщением
            await message.answer(parts[0], reply_markup=kb_retry(), parse_mode="HTML")

        # 4) хвост длинного ответа — отдельными сообщениями
        for p in parts[1:]:
            await message.answer(p, parse_mode="HTML")

    except Exception:
        # ошибка — показываем retry в ТЕКУЩЕМ новом сообщении
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=new_anchor_id,
                text=ERROR_TEXT,
                reply_markup=kb_retry(),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            await message.answer(ERROR_TEXT, reply_markup=kb_retry(), parse_mode="HTML")

    finally:
        # остаёмся ждать следующего текста
        await state.set_state(ObjectionStates.waiting_for_question)

# ============================================================================
# Маршруты
# ============================================================================

def router(rt: Router) -> None:

    # вход в раздел
    rt.callback_query.register(objection_home, F.data == "nav.objection_start")

    # старт из домашнего экрана
    rt.callback_query.register(start_objection_flow, F.data == "objection")

    # «попробовать ещё раз» и «начать ввод»
    rt.callback_query.register(retry_objection, F.data == "obj_retry")
    rt.callback_query.register(retry_objection, F.data == "obj_start")

    # ввод текста пользователем
    rt.message.register(handle_question, ObjectionStates.waiting_for_question, F.text)
