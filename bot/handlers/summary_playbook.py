# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\summary_playbook.py
from __future__ import annotations

import os
from typing import List, Optional, Dict
from datetime import datetime, timezone

import aiohttp
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ContentType, FSInputFile, InputMediaPhoto
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction

from bot.config import EXECUTOR_BASE_URL, get_file_path
from bot.states.states import SummaryStates
from bot.utils.chat_actions import run_long_operation_with_action

from bot.utils.redis_repo import summary_repo       # Redis: черновик (единый файл)
from bot.utils.database import (
    summary_add_entry as add_entry,
    summary_list_entries as list_entries,
    summary_get_entry as get_entry,
)
import bot.utils.database as db
from bot.utils.database import is_trial_active, trial_remaining_hours

# ============= Доступ / подписка (как в других скриптах) =============

def _is_sub_active(user_id: int) -> bool:
    raw = db.get_variable(user_id, "sub_until") or ""
    if not raw:
        return False
    try:
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

# Тексты уведомлений + кнопка «Оформить подписку»
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

# ============= UI текст =============
HOME_TEXT_TPL = ('''
🧠 *Саммари по переговорам*
Загрузите *аудио* разговора или вставьте *текст переписки!*

• Узнаете *сильные стороны* и *ошибки* коммуникации,
• Получите *краткое резюме*,
• Зафиксируете *договорённости и следующие шаги*.

{access_text}

Выберите формат:
''').strip()

def home_text(user_id: int) -> str:
    return HOME_TEXT_TPL.format(access_text=_format_access_text(user_id))

ASK_TEXT = "✍️ Пришлите сюда текст переписки (можно несколькими сообщениями). Когда закончите — нажмите «Сгенерировать саммари»."
ASK_AUDIO = "🎙️ Пришлите аудио (voice, audio или документ с аудио). Затем нажмите «Сгенерировать саммари»."
GEN_HINT = "Готово? Нажмите «Сгенерировать саммари» ниже."

GEN_RUNNING = "⏳ Обрабатываю запись… Идёт транскрибация и анализ."
GEN_ERROR = "⚠️ Не удалось выполнить анализ. Попробуйте ещё раз позже."
SAVED_OK = "💾 Саммари сохранено в историю."

HISTORY_TITLE = "🕘 История последних саммари"
HISTORY_EMPTY = "История пуста."

# ============= Клавиатуры =============
def kb_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎧 Отправить аудио", callback_data="summary.audio"),
         InlineKeyboardButton(text="📝 Вставить текст", callback_data="summary.text")],
        [InlineKeyboardButton(text="🕘 История", callback_data="summary.history")],
        [InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")]
    ])

def kb_back_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В начало", callback_data="nav.summary_home")]
    ])

def kb_ready() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Сгенерировать саммари", callback_data="summary.generate")],
        [InlineKeyboardButton(text="➕ Добавить ещё", callback_data="summary.add_more"),
         InlineKeyboardButton(text="🗑 Очистить", callback_data="summary.reset")],
        [InlineKeyboardButton(text="⬅️ В начало", callback_data="nav.summary_home")]
    ])

def kb_after_result() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить в историю", callback_data="summary.save")],
        [InlineKeyboardButton(text="⬅️ В начало", callback_data="nav.summary_home")]
    ])

def kb_history(items: List[Dict]) -> InlineKeyboardMarkup:
    rows = []
    for it in items:
        label = f"#{it['id']} • {it['created_at'][5:16]} • {it['source_type']}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"summary.history.open:{it['id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ В начало", callback_data="nav.summary_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ============= Вспомогалки =============
async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_caption(caption=text, reply_markup=kb, parse_mode="Markdown")
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
    Если сообщение было текстовым/другим типом — удаляем и отправляем фото заново.
    """
    try:
        media = InputMediaPhoto(media=FSInputFile(file_path), caption=caption, parse_mode="Markdown")
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        # не получилось заменить — удаляем и отправляем новое сообщение с фото
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(
            chat_id=msg.chat.id,
            photo=FSInputFile(file_path),
            caption=caption,
            reply_markup=kb,
            parse_mode="Markdown",
        )

def _split(text: str, limit: int = 3800) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts, chunk, ln = [], [], 0
    for line in text.splitlines(True):
        if ln + len(line) > limit and chunk:
            parts.append("".join(chunk))
            chunk, ln = [line], len(line)
        else:
            chunk.append(line); ln += len(line)
    if chunk:
        parts.append("".join(chunk))
    return parts

async def _save_tg_file_locally(bot: Bot, file_id: str, rel_path: str) -> str:
    file = await bot.get_file(file_id)
    data = await bot.download_file(file.file_path)
    abs_path = get_file_path(rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "wb") as f:
        f.write(data.read())
    return abs_path

async def _build_payload(user_id: int, chat_id: int) -> dict:
    draft = await summary_repo.get_draft(user_id)
    return {
        "user_id": user_id,
        "source": {"channel": "telegram", "chat_id": chat_id},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": draft.get("input") or {},  # {'type':'text',...} | {'type':'audio',...}
    }

async def _analyze(payload: dict, *, timeout_sec: int = 120) -> dict:
    """
    Ждём от бэкенда такой ответ:
    {
      "summary": "Короткое резюме",
      "strengths": ["...","..."],
      "mistakes": ["...","..."],
      "decisions": ["...","..."]
    }
    """
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/summary/analyze"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=t) as s:
        async with s.post(url, json=payload) as r:
            if r.status != 200:
                # пробуем вытащить деталь
                try:
                    err = await r.json()
                except Exception:
                    err = await r.text()
                raise RuntimeError(f"HTTP {r.status}: {err}")
            return await r.json()

def _render_result(res: dict) -> str:
    s = res.get("summary") or "—"
    strengths = res.get("strengths") or []
    mistakes = res.get("mistakes") or []
    decisions = res.get("decisions") or []

    fmt = [
        "✅ *Краткое резюме*",
        s.strip(),
        "",
        "💪 *Сильные стороны*",
        ("\n".join(f"• {x}" for x in strengths) or "—"),
        "",
        "⚠️ *Ошибки / риски*",
        ("\n".join(f"• {x}" for x in mistakes) or "—"),
        "",
        "📌 *Договорённости и next steps*",
        ("\n".join(f"• {x}" for x in decisions) or "—"),
    ]
    return "\n".join(fmt)

# ============= Экраны =============
async def summary_home(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await summary_repo.clear(callback.from_user.id)
    user_id = callback.from_user.id
    # Пытаемся показать карточку раздела с картинкой; если файла нет — фолбэк на текст
    rel = "img/bot/summary.png"           # data/img/bot/summary.png
    path = get_file_path(rel)
    if os.path.exists(path):
        await _edit_or_replace_with_photo_file(bot, callback.message, path, home_text(user_id), kb_home())
    else:
        await _edit_text_or_caption(callback.message, home_text(user_id), kb_home())
    await callback.answer()

# --- Текстовый поток ---
async def choose_text(callback: CallbackQuery, state: FSMContext):
    # фиксируем стадию и тип входа
    await summary_repo.set_stage(callback.from_user.id, "waiting_text")
    await summary_repo.set_input_text(callback.from_user.id, "", append=False)
    await _edit_text_or_caption(
        callback.message,
        f"{ASK_TEXT}\n\n{_format_access_text(callback.from_user.id)}",
        kb_ready()
    )
    await state.set_state(SummaryStates.waiting_for_text)
    await callback.answer()

async def handle_text(message: Message, state: FSMContext):
    user_id = message.from_user.id
    # аппендим текст прямо в Redis
    await summary_repo.set_input_text(user_id, message.text, append=True)
    draft = await summary_repo.get_draft(user_id)
    new_txt = (draft.get("input") or {}).get("text", "") if (draft.get("input") or {}).get("type") == "text" else ""
    await message.answer(
        f"Получено ~{len(new_txt)} символов.\n\n{GEN_HINT}\n\n{_format_access_text(user_id)}",
        reply_markup=kb_ready()
    )
    await state.set_state(SummaryStates.ready_to_generate)

# --- Аудио поток ---
async def choose_audio(callback: CallbackQuery, state: FSMContext):
    await summary_repo.set_stage(callback.from_user.id, "waiting_audio")
    await _edit_text_or_caption(
        callback.message,
        f"{ASK_AUDIO}\n\n{_format_access_text(callback.from_user.id)}",
        kb_ready()
    )
    await state.set_state(SummaryStates.waiting_for_audio)
    await callback.answer()

async def handle_audio(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id

    if message.voice:
        local = await _save_tg_file_locally(bot, message.voice.file_id, f"audio/tmp/sum_{user_id}_{message.message_id}.ogg")
        tg_meta = {"kind": "voice", "file_id": message.voice.file_id, "duration": message.voice.duration}
    elif message.audio:
        # расширение по mime
        ext = ".mp3" if (message.audio.mime_type or "").endswith("mpeg") else ".ogg"
        local = await _save_tg_file_locally(bot, message.audio.file_id, f"audio/tmp/sum_{user_id}_{message.message_id}{ext}")
        tg_meta = {"kind": "audio", "file_id": message.audio.file_id, "duration": message.audio.duration}
    elif message.document and (message.document.mime_type or "").startswith("audio/"):
        local = await _save_tg_file_locally(bot, message.document.file_id, f"audio/tmp/sum_{user_id}_{message.message_id}.ogg")
        tg_meta = {"kind": "doc-audio", "file_id": message.document.file_id}
    else:
        await message.answer("Это не аудио. Пришлите voice, audio или документ с аудио.")
        return

    await summary_repo.set_input_audio(user_id, local_path=local, telegram_meta=tg_meta)
    await message.answer(
        f"Файл получен: `{os.path.basename(local)}`\n\n{GEN_HINT}\n\n{_format_access_text(user_id)}",
        reply_markup=kb_ready(),
        parse_mode="Markdown"
    )
    await state.set_state(SummaryStates.ready_to_generate)

# --- Кнопки «готово/сброс/добавить» ---
async def add_more(callback: CallbackQuery, state: FSMContext):
    draft = await summary_repo.get_draft(callback.from_user.id)
    typ = (draft.get("input") or {}).get("type")
    if typ == "text":
        await _edit_text_or_caption(
            callback.message,
            f"Добавьте ещё текст и снова нажмите «Сгенерировать саммари».\n\n{_format_access_text(callback.from_user.id)}",
            kb_ready()
        )
        await state.set_state(SummaryStates.waiting_for_text)
    elif typ == "audio":
        await _edit_text_or_caption(
            callback.message,
            f"Пришлите ещё один аудио-файл и снова нажмите «Сгенерировать саммари».\n\n{_format_access_text(callback.from_user.id)}",
            kb_ready()
        )
        await state.set_state(SummaryStates.waiting_for_audio)
    else:
        await _edit_text_or_caption(callback.message, home_text(callback.from_user.id), kb_home())
    await callback.answer()

async def reset_draft(callback: CallbackQuery, state: FSMContext):
    await summary_repo.clear(callback.from_user.id)
    await state.clear()
    await _edit_text_or_caption(callback.message, home_text(callback.from_user.id), kb_home())
    await callback.answer("Очищено")

# --- Генерация и показ результата ---
async def generate_summary(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    # Блокируем генерацию без доступа и показываем инфо/кнопку подписки
    if not _has_access(user_id):
        if not _is_sub_active(user_id):
            await _edit_text_or_caption(callback.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, SUBSCRIBE_KB)
        await callback.answer()
        return

    payload = await _build_payload(user_id, chat_id)

    async def _do():
        return await _analyze(payload)

    try:
        res = await run_long_operation_with_action(
            bot=bot,
            chat_id=chat_id,
            action=ChatAction.TYPING,
            coro=_do()
        )
        text = _render_result(res)
        parts = _split(text)
        # первый кусок — редактируем текущий
        await _edit_text_or_caption(callback.message, parts[0], kb_after_result())
        # хвост — отдельными сообщениями
        for p in parts[1:]:
            await bot.send_message(chat_id, p, parse_mode="Markdown")
        # сохраним в черновик последний результат (для кнопки «Сохранить»)
        await summary_repo.set_last_result(user_id, res)
        await summary_repo.set_last_payload(user_id, payload)
    except Exception as e:
        await _edit_text_or_caption(callback.message, f"{GEN_ERROR}\n\n`{e}`", kb_ready())
    finally:
        await callback.answer()

async def save_to_history(callback: CallbackQuery):
    user_id = callback.from_user.id
    draft = await summary_repo.get_draft(user_id)
    res = draft.get("last_result")
    if not res:
        await callback.answer("Нет результата для сохранения", show_alert=True)
        return
    payload = draft.get("last_payload") or {}
    source_type = (payload.get("input") or {}).get("type", "unknown")
    entry_id = add_entry(
        user_id=user_id,
        source_type=source_type,
        options={},                 # опций сейчас нет — по ТЗ и UX они скрыты
        payload=payload,
        result=res
    )
    await _edit_text_or_caption(callback.message, f"{SAVED_OK}\nID: `{entry_id}`", kb_back_home())
    await callback.answer()

# --- История ---
async def open_history(callback: CallbackQuery):
    items = list_entries(callback.from_user.id, limit=10)
    if not items:
        await _edit_text_or_caption(callback.message, HISTORY_EMPTY, kb_back_home())
        await callback.answer()
        return
    await _edit_text_or_caption(callback.message, HISTORY_TITLE, kb_history(items))
    await callback.answer()

async def open_history_item(callback: CallbackQuery):
    _, sid = callback.data.split(":", 1)
    try:
        hid = int(sid)
    except ValueError:
        await callback.answer(); return
    rec = get_entry(callback.from_user.id, hid)
    if not rec:
        await callback.answer("Запись не найдена", show_alert=True); return
    txt = _render_result(rec.get("result") or {})
    parts = _split(f"*Запись #{rec['id']}*\n{txt}")
    await _edit_text_or_caption(callback.message, parts[0], kb_back_home())
    for p in parts[1:]:
        await callback.message.answer(p, parse_mode="Markdown")
    await callback.answer()

# ============= Маршруты =============
def router(rt: Router):
    # вход по требованию
    rt.callback_query.register(summary_home, F.data == "nav.summary_home")

    # выбор источника
    rt.callback_query.register(choose_text, F.data == "summary.text")
    rt.callback_query.register(choose_audio, F.data == "summary.audio")

    # приём контента
    rt.message.register(handle_text, SummaryStates.waiting_for_text, F.text)
    rt.message.register(
        handle_audio,
        SummaryStates.waiting_for_audio,
        F.content_type.in_({ContentType.VOICE, ContentType.AUDIO, ContentType.DOCUMENT})
    )

    # готовность/сброс/добавление
    rt.callback_query.register(generate_summary, F.data == "summary.generate")
    rt.callback_query.register(add_more, F.data == "summary.add_more")
    rt.callback_query.register(reset_draft, F.data == "summary.reset")

    # история
    rt.callback_query.register(open_history, F.data == "summary.history")
    rt.callback_query.register(open_history_item, F.data.startswith("summary.history.open:"))

    # после результата
    rt.callback_query.register(save_to_history, F.data == "summary.save")