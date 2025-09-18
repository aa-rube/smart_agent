#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\objection_playbook.py

from __future__ import annotations

import aiohttp
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction

from bot.config import EXECUTOR_BASE_URL
from bot.states.states import ObjectionStates  # добавьте в ваш states.py (см. снизу)
from bot.utils.chat_actions import run_long_operation_with_action
from bot.keyboards.inline import *
from bot.text.texts import *

back_btn = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.objection_start")]
    ]
)

# ==========================
# Утилиты редактирования
# ==========================

async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Обновить текст/подпись и клавиатуру текущего сообщения (без создания нового)."""
    try:
        await msg.edit_text(text, reply_markup=kb)
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_caption(caption=text, reply_markup=kb)
        return
    except TelegramBadRequest:
        pass
    # если совсем нельзя — хотя бы клавиатуру
    try:
        await msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

# ==========================
# HTTP-клиент к контроллеру
# ==========================

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
                # попытаемся показать деталь, если она есть
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
# Разделение длинного ответа
# ==========================

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

# ==========================
# Колбэки / сообщения
# ==========================

async def start_objection_flow(callback: CallbackQuery, state: FSMContext):
    """
    Начало сценария: редактируем текущее сообщение, просим ввести возражение,
    сохраняем message_id как «якорь», чтобы дальше редактировать именно его.
    """
    anchor_id = callback.message.message_id
    await state.update_data(anchor_id=anchor_id)
    await _edit_text_or_caption(callback.message, ASK_OBJECTION, back_btn)
    await state.set_state(ObjectionStates.waiting_for_question)
    await callback.answer()

async def retry_objection(callback: CallbackQuery, state: FSMContext):
    """
    «Попробовать ещё раз» — возвращаемся к вводу.
    """
    data = await state.get_data()
    if not data.get("anchor_id"):
        await state.update_data(anchor_id=callback.message.message_id)
    await _edit_text_or_caption(callback.message, ASK_OBJECTION, back_btn)
    await state.set_state(ObjectionStates.waiting_for_question)
    await callback.answer()

async def handle_question(message: Message, state: FSMContext, bot: Bot):
    """
    Пользователь прислал формулировку возражения.
    ▶ Срываем якорь: всегда отправляем НОВОЕ сообщение «Генерирую…»
    ▶ Сохраняем его message_id как новый anchor_id
    ▶ По готовности редактируем именно это новое сообщение.
    """
    chat_id = message.chat.id

    # 1) срываем якорь: создаём новое сообщение-экран
    gen_msg = await message.answer(GENERATING)
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

        # 3) редактируем НОВОЕ сообщение результатом
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=new_anchor_id,
                text=parts[0],
                reply_markup=objection_playbook_retry_inline
            )
        except TelegramBadRequest:
            # на всякий случай, если вдруг нельзя редактировать — шлём новым
            await message.answer(parts[0], reply_markup=objection_playbook_retry_inline)

        # 4) хвост длинного ответа — отдельными сообщениями
        for p in parts[1:]:
            await message.answer(p)

    except Exception:
        # ошибка — показываем retry в ТЕКУЩЕМ новом сообщении
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=new_anchor_id,
                text=ERROR_TEXT,
                reply_markup=objection_playbook_retry_inline
            )
        except TelegramBadRequest:
            await message.answer(ERROR_TEXT, reply_markup=objection_playbook_retry_inline)

    finally:
        # остаёмся ждать следующего текста
        await state.set_state(ObjectionStates.waiting_for_question)


# ==========================
# Маршруты
# ==========================

def router(rt: Router):
    # старт из любого экрана по кнопке
    rt.callback_query.register(start_objection_flow, F.data == "objection")
    rt.callback_query.register(retry_objection, F.data == "obj_retry")
    rt.callback_query.register(retry_objection, F.data == "obj_start")

    # ввод текста пользователем
    rt.message.register(handle_question, ObjectionStates.waiting_for_question, F.text)