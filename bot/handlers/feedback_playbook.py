from __future__ import annotations

from bot.handlers.feedback.model.history_item import HistoryItem
from bot.handlers.feedback.model.review_payload import ReviewPayload
from bot.states.states import FeedbackStates

import asyncio
import json
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
from aiogram import F, Bot, Router
from aiogram.types import *
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums.chat_action import ChatAction
from aiogram.fsm.context import FSMContext

from bot.config import EXECUTOR_BASE_URL

# ==========================
# Texts (UX copy)
# ==========================

ASK_CLIENT_NAME = (
    "Введите имя клиента (обязательно).\n\n"
    "Например: Мария П., Иван, Семья Коваленко"
)
ASK_AGENT_NAME = "Введите ваше имя (агента)."
ASK_COMPANY = "Укажите название компании (можно пропустить)."
ASK_CITY = (
    "Где проходила сделка? Выберите город ниже или введите свой.\n\n"
    "Можно также указать адрес (необязательно)."
)
ASK_CITY_INPUT = "Введите город (например: Москва / Баку / Тбилиси)."
ASK_ADDRESS = "Уточните адрес (улица, дом) или пропустите."
ASK_DEAL_TYPE = "Выберите тип сделки."
ASK_DEAL_CUSTOM = "Уточните тип сделки (свободный ввод)."
ASK_SITUATION = (
    "Опишите ситуацию: что делали, сроки, сложности, итог.\n"
    "Минимум 100 символов."
)
HINT_SITUATION = (
    "Подсказка:\n• Сроки: когда обратились / когда закрыли\n"
    "• Задача: что нужно было решить\n• Особенности: ограничения, сложности\n"
    "• Ход работы: как шли к результату\n• Итог: что клиент получил"
)
ASK_STYLE = "Выберите стиль черновика."
STYLE_INFO = (
    "Стили:"\
    "\n— Дружелюбный: теплее, разговорно"
    "\n— Нейтральный: спокойно, по делу"
    "\n— Официальный: без эмоций, формально"
    "\n— Лаконичный: короче (≈300–500 знаков)"
    "\n— Подробный: развернуто (до ≈1200 знаков)"
)
SUMMARY_TITLE = "Проверьте данные перед генерацией:"
BTN_GENERATE = "Сгенерировать"
BTN_EDIT = "Изменить поле"
BTN_CANCEL = "Закрыть"
BTN_BACK = "Назад"
BTN_NEXT = "Дальше"
BTN_SKIP = "Пропустить"
GENERATING = "Генерирую варианты…"
ERROR_TEXT = (
    "Не удалось получить текст от сервиса. Попробуйте еще раз."
)
READY_FINAL = (
    "Черновик готов. Отправьте клиенту и опубликуйте при согласии.\n"
    "Сохранено в истории."
)
HISTORY_EMPTY = "История пуста. Сгенерируйте первый черновик."

DEFAULT_CITIES = ["Москва", "Санкт-Петербург", "Тбилиси", "Баку", "Ереван", "Алматы"]


# naive in‑memory store; replace with DB/repo in prod
_HISTORY: Dict[int, List[HistoryItem]] = {}
_NEXT_HISTORY_ID = 1


def _history_add(user_id: int, payload: ReviewPayload, final_text: str) -> HistoryItem:
    global _NEXT_HISTORY_ID
    item = HistoryItem(item_id=_NEXT_HISTORY_ID, created_at=datetime.utcnow(), payload=payload, final_text=final_text)
    _NEXT_HISTORY_ID += 1
    _HISTORY.setdefault(user_id, []).insert(0, item)
    # keep last 100
    _HISTORY[user_id] = _HISTORY[user_id][:100]
    return item


# ==========================
# Small utils
# ==========================

async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Edit text/caption/reply_markup of the same message to avoid spam."""
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
    try:
        await msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass


def _split_for_telegram(text: str, limit: int = 4000) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    chunk: List[str] = []
    length = 0
    for line in text.splitlines(True):
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


async def run_long_operation_with_action(*, bot: Bot, chat_id: int, action: ChatAction, coro: asyncio.Future | asyncio.Task | Any) -> Any:
    """Lightweight mimic of typing action wrapper."""
    # fire-and-forget pinger while waiting
    is_done = False

    async def _pinger():
        while not is_done:
            try:
                await bot.send_chat_action(chat_id, action)
            except Exception:
                pass
            await asyncio.sleep(4)

    pinger_task = asyncio.create_task(_pinger())
    try:
        result = await coro
        return result
    finally:
        is_done = True
        pinger_task.cancel()


async def _return_to_summary(msg: Message, state: FSMContext) -> None:
    """Показать сводку и очистить режим редактирования конкретного поля."""
    d = await state.get_data()
    await _edit_text_or_caption(msg, _summary_text(d), kb_summary())
    await state.update_data(edit_field=None)
    await state.set_state(FeedbackStates.showing_summary)


# ==========================
# Inline keyboards builders
# ==========================

def kb_only_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")]])


def kb_cities() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []
    for c in DEFAULT_CITIES:
        row.append(InlineKeyboardButton(text=c, callback_data=f"loc.city.{c}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="Ввести город", callback_data="loc.city.input"),
        InlineKeyboardButton(text="Указать адрес (опц.)", callback_data="loc.addr"),
    ])
    rows.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_deal_types() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Продажа", callback_data="deal.sale"),
            InlineKeyboardButton(text="Покупка", callback_data="deal.buy"),
        ],
        [
            InlineKeyboardButton(text="Аренда", callback_data="deal.rent"),
            InlineKeyboardButton(text="Лизинг", callback_data="deal.lease"),
        ],
        [InlineKeyboardButton(text="Другое", callback_data="deal.other")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_style() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Дружелюбный", callback_data="style.friendly"),
         InlineKeyboardButton(text="Нейтральный", callback_data="style.neutral")],
        [InlineKeyboardButton(text="Официальный", callback_data="style.formal"),
         InlineKeyboardButton(text="Лаконичный", callback_data="style.brief")],
        [InlineKeyboardButton(text="Подробный", callback_data="style.long")],
        [InlineKeyboardButton(text="О стилях", callback_data="style.info")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_summary() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=BTN_GENERATE, callback_data="gen.start")],
        [InlineKeyboardButton(text=BTN_EDIT, callback_data="edit.open")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_edit_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Имя клиента", callback_data="edit.client"),
         InlineKeyboardButton(text="Имя агента", callback_data="edit.agent")],
        [InlineKeyboardButton(text="Компания", callback_data="edit.company"),
         InlineKeyboardButton(text="Город", callback_data="edit.city")],
        [InlineKeyboardButton(text="Адрес", callback_data="edit.addr"),
         InlineKeyboardButton(text="Тип сделки", callback_data="edit.deal")],
        [InlineKeyboardButton(text="Ситуация", callback_data="edit.sit"),
         InlineKeyboardButton(text="Стиль", callback_data="edit.style")],
        [InlineKeyboardButton(text="Готово", callback_data="edit.done")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_variant(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Выбрать этот", callback_data=f"pick.{index}")],
        [InlineKeyboardButton(text="Сделать короче", callback_data=f"mutate.{index}.short"),
         InlineKeyboardButton(text="Сделать длиннее", callback_data=f"mutate.{index}.long")],
        [InlineKeyboardButton(text="Изменить стиль", callback_data=f"mutate.{index}.style")],
        [InlineKeyboardButton(text="Ещё вариант", callback_data=f"gen.more.{index}")],
        [InlineKeyboardButton(text="Экспорт .txt", callback_data=f"export.{index}.txt"),
         InlineKeyboardButton(text="Экспорт .md", callback_data=f"export.{index}.md")],
    ])


def kb_variants_common() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Показать все поля", callback_data="summary.show")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_final() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Экспорт .txt", callback_data="export.final.txt"),
         InlineKeyboardButton(text="Экспорт .md", callback_data="export.final.md")],
        [InlineKeyboardButton(text="Создать похожий", callback_data="clone.from.final")],
        [InlineKeyboardButton(text="В меню", callback_data="nav.menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_history(items: List[HistoryItem]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for it in items[:10]:
        label = f"#{it.item_id} · {it.created_at.strftime('%Y-%m-%d %H:%M')} · {it.payload.city} · {it.payload.deal_type}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"hist.open.{it.item_id}")])
    rows.append([InlineKeyboardButton(text="Поиск", callback_data="hist.search"), InlineKeyboardButton(text="В меню", callback_data="nav.menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================
# HTTP client (microservice)
# ==========================

async def _request_generate(payload: ReviewPayload, *, num_variants: int = 3, timeout_sec: int = 90) -> List[str]:
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/review/generate"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    body = asdict(payload)
    body.update({"num_variants": num_variants})
    async with aiohttp.ClientSession(timeout=t) as session:
        async with session.post(url, json=body) as resp:
            if resp.status != 200:
                detail = await _extract_error_detail(resp)
                raise RuntimeError(f"Executor HTTP {resp.status}: {detail}")
            data = await resp.json()
            variants = (data or {}).get("variants")
            if not variants:
                # Fallback: single text field
                txt = (data or {}).get("text", "").strip()
                if not txt:
                    raise RuntimeError("Executor returned no variants")
                return [txt]
            return [str(v).strip() for v in variants if str(v).strip()]


async def _request_mutate(base_text: str, *, operation: str, style: Optional[str], payload: ReviewPayload, timeout_sec: int = 60) -> str:
    """operation: 'short' | 'long' | 'style'"""
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/review/mutate"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    body = {
        "base_text": base_text,
        "operation": operation,
        "style": style,
        "context": asdict(payload),
    }
    async with aiohttp.ClientSession(timeout=t) as session:
        async with session.post(url, json=body) as resp:
            if resp.status != 200:
                detail = await _extract_error_detail(resp)
                raise RuntimeError(f"Executor HTTP {resp.status}: {detail}")
            data = await resp.json()
            txt = (data or {}).get("text", "").strip()
            if not txt:
                raise RuntimeError("Empty mutate text")
            return txt


async def _extract_error_detail(resp: aiohttp.ClientResponse) -> str:
    try:
        data = await resp.json()
        return data.get("detail") or data.get("error") or json.dumps(data)
    except Exception:
        return await resp.text()


# ==========================
# Rendering helpers
# ==========================

def _shorten(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _summary_text(d: Dict[str, Any]) -> str:
    lines = [SUMMARY_TITLE, ""]
    lines.append(f"• Клиент: {d.get('client_name')}")
    agent = d.get("agent_name")
    company = d.get("company")
    if company:
        lines.append(f"• Агент/компания: {agent}, {company}")
    else:
        lines.append(f"• Агент: {agent}")
    loc = d.get("city") or "—"
    addr = d.get("address")
    lines.append(f"• Локация: {loc}{', ' + addr if addr else ''}")
    deal_type = d.get("deal_type")
    if deal_type == "custom":
        deal_type = f"Другое: {d.get('deal_custom') or ''}"
    lines.append(f"• Тип сделки: {deal_type}")
    lines.append(f"• Ситуация: {_shorten(d.get('situation',''), 150)}")
    lines.append(f"• Стиль: {d.get('style')}")
    return "\n".join(lines)


# ==========================
# Flow handlers
# ==========================

async def start_feedback_flow(callback: CallbackQuery, state: FSMContext):
    """Entry point via callback 'nav.feedback_start' from your menu."""
    anchor_id = callback.message.message_id
    await state.update_data(anchor_id=anchor_id)
    await _edit_text_or_caption(callback.message, ASK_CLIENT_NAME, kb_only_cancel())
    await state.set_state(FeedbackStates.waiting_client)
    await callback.answer()


async def cancel_flow(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit_text_or_caption(callback.message, "Операция отменена. Чем займёмся?", None)
    await callback.answer()


async def handle_client_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 60:
        await message.reply("Имя должно быть 2–60 символов. Попробуйте снова.")
        return
    await state.update_data(client_name=name)
    d = await state.get_data()
    if d.get("edit_field") == "client":
        await _return_to_summary(message, state)
        return
    await message.answer(ASK_AGENT_NAME)
    await state.set_state(FeedbackStates.waiting_agent)


async def handle_agent_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 60:
        await message.reply("Имя должно быть 2–60 символов. Попробуйте снова.")
        return
    await state.update_data(agent_name=name)
    d = await state.get_data()
    if d.get("edit_field") == "agent":
        await _return_to_summary(message, state)
        return
    await message.answer(ASK_COMPANY, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_SKIP, callback_data="company.skip")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
    ]))
    await state.set_state(FeedbackStates.waiting_company)


async def handle_company_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(company=None)
    d = await state.get_data()
    if d.get("edit_field") == "company":
        await _return_to_summary(callback.message, state)
    else:
        await _edit_text_or_caption(callback.message, ASK_CITY, kb_cities())
        await state.set_state(FeedbackStates.waiting_city_mode)
    await callback.answer()


async def handle_company_name(message: Message, state: FSMContext):
    company = (message.text or "").strip()
    if company and (len(company) < 2 or len(company) > 80):
        await message.reply("Название компании 2–80 символов или пропустите.")
        return
    await state.update_data(company=company or None)
    d = await state.get_data()
    if d.get("edit_field") == "company":
        await _return_to_summary(message, state)
    else:
        await message.answer(ASK_CITY, reply_markup=kb_cities())
        await state.set_state(FeedbackStates.waiting_city_mode)


async def handle_city_choice(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    if data == "loc.city.input":
        await _edit_text_or_caption(callback.message, ASK_CITY_INPUT, kb_only_cancel())
        await state.set_state(FeedbackStates.waiting_city_input)
        await callback.answer()
        return
    if data == "loc.addr":
        await _edit_text_or_caption(callback.message, ASK_ADDRESS, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=BTN_SKIP, callback_data="addr.skip")],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
        ]))

        await state.set_state(FeedbackStates.waiting_address)
        await callback.answer()
        return
    # loc.city.{Name}
    if data.startswith("loc.city."):
        city = data.split(".", 2)[2]
        await state.update_data(city=city)
        # show short confirmation and next
        await _edit_text_or_caption(callback.message, f"Город: {city}.\n\nНужно указать адрес?", InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Да, указать адрес", callback_data="loc.addr")],
            [InlineKeyboardButton(text=BTN_NEXT, callback_data="loc.next")],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
        ]))
        await state.set_state(FeedbackStates.waiting_city_mode)
        await callback.answer()
        return

    if data == "loc.next":
        d = await state.get_data()
        if d.get("edit_field") == "city":
            await _return_to_summary(callback.message, state)
        else:
            # proceed to deal type
            await _edit_text_or_caption(callback.message, ASK_DEAL_TYPE, kb_deal_types())
            await state.set_state(FeedbackStates.waiting_deal_type)
        await callback.answer()
        return


async def handle_city_input(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if len(city) < 2:
        await message.reply("Слишком короткое название города. Попробуйте снова.")
        return
    await state.update_data(city=city)
    await message.answer(f"Город: {city}.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BTN_NEXT, callback_data="loc.next")],
        [InlineKeyboardButton(text="Указать адрес (опц.)", callback_data="loc.addr")],
    ]))
    await state.set_state(FeedbackStates.waiting_city_mode)


async def handle_address(message: Message, state: FSMContext):
    addr = (message.text or "").strip()
    await state.update_data(address=addr or None)
    d = await state.get_data()
    if d.get("edit_field") in ("address", "city"):
        await _return_to_summary(message, state)
    else:
        await message.answer(ASK_DEAL_TYPE, reply_markup=kb_deal_types())
        await state.set_state(FeedbackStates.waiting_deal_type)


async def handle_address_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(address=None)
    d = await state.get_data()
    if d.get("edit_field") in ("address", "city"):
        await _return_to_summary(callback.message, state)
    else:
        await _edit_text_or_caption(callback.message, ASK_DEAL_TYPE, kb_deal_types())
        await state.set_state(FeedbackStates.waiting_deal_type)
    await callback.answer()


async def handle_deal_type(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    if not data.startswith("deal."):
        await callback.answer()
        return
    deal_code = data.split(".", 1)[1]
    if deal_code == "other":
        await _edit_text_or_caption(callback.message, ASK_DEAL_CUSTOM, kb_only_cancel())
        await state.set_state(FeedbackStates.waiting_deal_custom)
        await callback.answer()
        return
    await state.update_data(deal_type=deal_code, deal_custom=None)
    d = await state.get_data()
    if d.get("edit_field") == "deal":
        await _return_to_summary(callback.message, state)
    else:
        await _edit_text_or_caption(callback.message, ASK_SITUATION, InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Показать подсказки", callback_data="sit.hints")]]))
        await state.set_state(FeedbackStates.waiting_situation)
    await callback.answer()


async def handle_deal_custom(message: Message, state: FSMContext):
    custom = (message.text or "").strip()
    if len(custom) < 2:
        await message.reply("Слишком короткое значение. Уточните тип сделки.")
        return
    await state.update_data(deal_type="custom", deal_custom=custom)
    d = await state.get_data()
    if d.get("edit_field") == "deal":
        await _return_to_summary(message, state)
    else:
        await message.answer(ASK_SITUATION, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Показать подсказки", callback_data="sit.hints")]]))
        await state.set_state(FeedbackStates.waiting_situation)


async def handle_situation_hints(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(HINT_SITUATION)
    await callback.answer()


async def handle_situation(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if len(txt) < 100 or len(txt) > 4000:
        await message.reply("Нужно 100–4000 символов. Добавьте деталей (сроки, результат, особенности).")
        return
    await state.update_data(situation=txt)
    d = await state.get_data()
    if d.get("edit_field") == "situation":
        await _return_to_summary(message, state)
    else:
        await message.answer(ASK_STYLE, reply_markup=kb_style())
        await state.set_state(FeedbackStates.waiting_style)


async def handle_style(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    if data == "style.info":
        await callback.message.answer(STYLE_INFO)
        await callback.answer()
        return
    if not data.startswith("style."):
        await callback.answer()
        return
    style = data.split(".", 1)[1]
    await state.update_data(style=style)
    d = await state.get_data()
    summary = _summary_text(d)
    await _edit_text_or_caption(callback.message, summary, kb_summary())
    await state.set_state(FeedbackStates.showing_summary)
    await callback.answer()


async def open_edit_menu(callback: CallbackQuery, state: FSMContext):
    await _edit_text_or_caption(callback.message, "Что изменить?", kb_edit_menu())
    await callback.answer()


async def edit_field_router(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    if data == "edit.client":
        await state.update_data(edit_field="client")
        await _edit_text_or_caption(callback.message, ASK_CLIENT_NAME, kb_only_cancel())
        await state.set_state(FeedbackStates.waiting_client)
    elif data == "edit.agent":
        await state.update_data(edit_field="agent")
        await _edit_text_or_caption(callback.message, ASK_AGENT_NAME, kb_only_cancel())
        await state.set_state(FeedbackStates.waiting_agent)
    elif data == "edit.company":
        await state.update_data(edit_field="company")
        await _edit_text_or_caption(callback.message, ASK_COMPANY, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=BTN_SKIP, callback_data="company.skip")],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
        ]))
        await state.set_state(FeedbackStates.waiting_company)
    elif data == "edit.city":
        await state.update_data(edit_field="city")
        await _edit_text_or_caption(callback.message, ASK_CITY, kb_cities())
        await state.set_state(FeedbackStates.waiting_city_mode)
    elif data == "edit.addr":
        await state.update_data(edit_field="address")
        await _edit_text_or_caption(callback.message, ASK_ADDRESS, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=BTN_SKIP, callback_data="addr.skip")],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
        ]))
        await state.set_state(FeedbackStates.waiting_address)
    elif data == "edit.deal":
        await state.update_data(edit_field="deal")
        await _edit_text_or_caption(callback.message, ASK_DEAL_TYPE, kb_deal_types())
        await state.set_state(FeedbackStates.waiting_deal_type)
    elif data == "edit.sit":
        await state.update_data(edit_field="situation")
        await _edit_text_or_caption(callback.message, ASK_SITUATION, InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Показать подсказки", callback_data="sit.hints")]]))
        await state.set_state(FeedbackStates.waiting_situation)
    elif data == "edit.style":
        await state.update_data(edit_field="style")
        await _edit_text_or_caption(callback.message, ASK_STYLE, kb_style())
        await state.set_state(FeedbackStates.waiting_style)
    elif data == "edit.done":
        d = await state.get_data()
        await _edit_text_or_caption(callback.message, _summary_text(d), kb_summary())
        await state.set_state(FeedbackStates.showing_summary)
    await callback.answer()


def _payload_from_state(d: Dict[str, Any]) -> ReviewPayload:
    return ReviewPayload(
        client_name=d.get("client_name"),
        agent_name=d.get("agent_name"),
        company=d.get("company"),
        city=d.get("city"),
        address=d.get("address"),
        deal_type=d.get("deal_type"),
        deal_custom=d.get("deal_custom"),
        situation=d.get("situation"),
        style=d.get("style"),
    )


async def start_generation(callback: CallbackQuery, state: FSMContext, bot: Bot):
    d = await state.get_data()
    try:
        payload = _payload_from_state(d)
    except Exception:
        await callback.message.answer("Не все поля заполнены. Вернитесь и заполните обязательные поля.")
        await callback.answer()
        return

    chat_id = callback.message.chat.id
    gen_msg = await callback.message.answer(GENERATING)
    new_anchor_id = gen_msg.message_id
    await state.update_data(anchor_id=new_anchor_id)

    async def _do():
        return await _request_generate(payload, num_variants=3)

    try:
        variants: List[str] = await run_long_operation_with_action(
            bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do()
        )
        # Save variants to state
        await state.update_data(variants=variants)

        # Show each with its own keyboard
        for idx, text in enumerate(variants, start=1):
            parts = _split_for_telegram(text)
            head = f"Вариант {idx}\n\n" + parts[0]
            try:
                await bot.edit_message_text(chat_id=chat_id, message_id=new_anchor_id, text=head, reply_markup=kb_variant(idx))
            except TelegramBadRequest:
                await callback.message.answer(head, reply_markup=kb_variant(idx))
            # tail
            for p in parts[1:]:
                await callback.message.answer(p)
        # common footer
        await callback.message.answer("Выберите подходящий вариант или измените его.", reply_markup=kb_variants_common())
        await state.set_state(FeedbackStates.browsing_variants)
    except Exception as e:
        try:
            await bot.edit_message_text(chat_id=chat_id,
                                        message_id=new_anchor_id,
                                        text=f"{ERROR_TEXT}\n\n{e}",
                                        reply_markup=InlineKeyboardMarkup(
                                            inline_keyboard=
                                            [[InlineKeyboardButton(text="Попробовать ещё раз", callback_data="gen.start")]]))
        except TelegramBadRequest:
            await callback.message.answer(f"{ERROR_TEXT}\n\n{e}",
                                          reply_markup=InlineKeyboardMarkup(
                                              inline_keyboard=
                                              [[InlineKeyboardButton(text="Попробовать ещё раз",callback_data="gen.start")]]))
        await state.set_state(FeedbackStates.showing_summary)
    finally:
        await callback.answer()


async def mutate_variant(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = callback.data  # mutate.{index}.short|long|style
    try:
        _, idx_str, op = data.split(".")
        idx = int(idx_str)
    except Exception:
        await callback.answer()
        return

    d = await state.get_data()
    variants: List[str] = d.get("variants", [])
    if idx < 1 or idx > len(variants):
        await callback.answer("Не найден вариант.")
        return

    base_text = variants[idx - 1]
    payload = _payload_from_state(d)

    if op == "style":
        # switch to style select, but remember which idx to mutate
        await state.update_data(mutating_idx=idx)
        await _edit_text_or_caption(callback.message, ASK_STYLE, kb_style())
        await state.set_state(FeedbackStates.waiting_style)
        await callback.answer()
        return

    operation = "short" if op == "short" else "long"

    chat_id = callback.message.chat.id
    gen_msg = await callback.message.answer("Меняю вариант…")
    new_anchor_id = gen_msg.message_id
    await state.update_data(anchor_id=new_anchor_id)

    async def _do():
        return await _request_mutate(base_text, operation=operation, style=None, payload=payload)

    try:
        new_text: str = await run_long_operation_with_action(bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do())
        variants[idx - 1] = new_text
        await state.update_data(variants=variants)
        parts = _split_for_telegram(new_text)
        head = f"Вариант {idx} (обновлён)\n\n" + parts[0]
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=new_anchor_id, text=head, reply_markup=kb_variant(idx))
        except TelegramBadRequest:
            await callback.message.answer(head, reply_markup=kb_variant(idx))
        for p in parts[1:]:
            await callback.message.answer(p)
    except Exception as e:
        await callback.message.answer(f"{ERROR_TEXT}\n\n{e}")
    finally:
        await callback.answer()


async def apply_style_after_mutate(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """If user selected 'mutate.{idx}.style' we ask style, then we come back here via style selection handler."""
    # This is handled by handle_style if mutating_idx exists in state while in waiting_style.
    await callback.answer()


async def handle_style_after_mutate(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Called from handle_style when mutating specific variant's style."""
    d = await state.get_data()
    mut_idx = d.get("mutating_idx")
    if not mut_idx:
        return  # normal style flow handled elsewhere

    style = d.get("style")
    variants: List[str] = d.get("variants", [])
    if mut_idx < 1 or mut_idx > len(variants):
        await callback.message.answer("Не найден вариант.")
        await state.update_data(mutating_idx=None)
        return

    base_text = variants[mut_idx - 1]
    payload = _payload_from_state(d)

    chat_id = callback.message.chat.id
    gen_msg = await callback.message.answer("Меняю стиль…")
    new_anchor_id = gen_msg.message_id
    await state.update_data(anchor_id=new_anchor_id)

    async def _do():
        return await _request_mutate(base_text, operation="style", style=style, payload=payload)

    try:
        new_text: str = await run_long_operation_with_action(bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do())
        variants[mut_idx - 1] = new_text
        await state.update_data(variants=variants, mutating_idx=None)
        parts = _split_for_telegram(new_text)
        head = f"Вариант {mut_idx} (обновлён)\n\n" + parts[0]
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=new_anchor_id, text=head, reply_markup=kb_variant(mut_idx))
        except TelegramBadRequest:
            await callback.message.answer(head, reply_markup=kb_variant(mut_idx))
        for p in parts[1:]:
            await callback.message.answer(p)
    except Exception as e:
        await callback.message.answer(f"{ERROR_TEXT}\n\n{e}")


async def gen_more_variant(callback: CallbackQuery, state: FSMContext, bot: Bot):
    # Ask microservice for one more variant
    d = await state.get_data()
    payload = _payload_from_state(d)

    chat_id = callback.message.chat.id
    gen_msg = await callback.message.answer("Ещё вариант…")
    new_anchor_id = gen_msg.message_id
    await state.update_data(anchor_id=new_anchor_id)

    async def _do():
        lst = await _request_generate(payload, num_variants=1)
        return lst[0]

    try:
        new_text: str = await run_long_operation_with_action(bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do())
        variants: List[str] = d.get("variants", [])
        variants.append(new_text)
        await state.update_data(variants=variants)
        idx = len(variants)
        parts = _split_for_telegram(new_text)
        head = f"Вариант {idx}\n\n" + parts[0]
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=new_anchor_id, text=head, reply_markup=kb_variant(idx))
        except TelegramBadRequest:
            await callback.message.answer(head, reply_markup=kb_variant(idx))
        for p in parts[1:]:
            await callback.message.answer(p)
    except Exception as e:
        await callback.message.answer(f"{ERROR_TEXT}\n\n{e}")
    finally:
        await callback.answer()


async def pick_variant(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    try:
        _, idx_str = data.split(".")
        idx = int(idx_str)
    except Exception:
        await callback.answer()
        return

    d = await state.get_data()
    variants: List[str] = d.get("variants", [])
    if idx < 1 or idx > len(variants):
        await callback.answer("Не найден вариант.")
        return

    await state.update_data(picked_idx=idx)
    await _edit_text_or_caption(callback.message, f"Вы выбрали вариант {idx}. Готово к выдаче?", InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Готово", callback_data="done.final")],
        [InlineKeyboardButton(text="Вернуться к вариантам", callback_data="gen.back")],
    ]))
    await callback.answer()


async def back_to_variants(callback: CallbackQuery, state: FSMContext):
    await _edit_text_or_caption(callback.message, "Вернитесь к вариантам выше или запросите ещё один.", kb_variants_common())
    await state.set_state(FeedbackStates.browsing_variants)
    await callback.answer()


async def finalize_choice(callback: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    idx = d.get("picked_idx")
    variants: List[str] = d.get("variants", [])
    if not idx or idx < 1 or idx > len(variants):
        await callback.answer("Сначала выберите вариант.")
        return

    final_text = variants[idx - 1]
    payload = _payload_from_state(d)

    # Save to history
    _history_add(callback.from_user.id, payload, final_text)

    await _edit_text_or_caption(callback.message, READY_FINAL, kb_final())
    await state.update_data(final_text=final_text)
    await callback.answer()


async def export_text(callback: CallbackQuery, state: FSMContext):
    data = callback.data  # export.{scope}.{fmt}
    try:
        _, scope, fmt = data.split(".")
    except Exception:
        await callback.answer()
        return

    d = await state.get_data()
    if scope == "final":
        text = d.get("final_text") or ""
        filename = f"review_final.{fmt}"
    else:
        # scope is index
        try:
            idx = int(scope)
        except Exception:
            await callback.answer()
            return
        variants: List[str] = d.get("variants", [])
        if idx < 1 or idx > len(variants):
            await callback.answer("Не найден вариант.")
            return
        text = variants[idx - 1]
        filename = f"review_variant_{idx}.{fmt}"

    buf = BufferedInputFile(text.encode("utf-8"), filename=filename)
    await callback.message.answer_document(buf)
    await callback.answer()


async def clone_from_final(callback: CallbackQuery, state: FSMContext):
    # Re-open summary with same fields
    d = await state.get_data()
    await _edit_text_or_caption(callback.message, _summary_text(d), kb_summary())
    await state.set_state(FeedbackStates.showing_summary)
    await callback.answer()


# ==========================
# History handlers (basic)
# ==========================

async def open_history(callback: CallbackQuery, state: FSMContext):
    items = _HISTORY.get(callback.from_user.id, [])
    if not items:
        await _edit_text_or_caption(callback.message, HISTORY_EMPTY, InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="nav.menu")]]))
        await callback.answer()
        return
    text_lines = ["Недавние черновики:"]
    for it in items[:10]:
        text_lines.append(
            f"#{it.item_id} · {it.created_at.strftime('%Y-%m-%d %H:%M')} · {it.payload.city} · {it.payload.deal_type} · {it.payload.client_name}"
        )
    await _edit_text_or_caption(callback.message, "\n".join(text_lines), kb_history(items))
    await callback.answer()


async def history_open_item(callback: CallbackQuery, state: FSMContext):
    # hist.open.{id}
    try:
        _, _, id_str = callback.data.split(".")
        item_id = int(id_str)
    except Exception:
        await callback.answer()
        return

    items = _HISTORY.get(callback.from_user.id, [])
    item = next((x for x in items if x.item_id == item_id), None)
    if not item:
        await callback.answer("Не найдено.")
        return

    p = item.payload
    header = f"#{item.item_id} · {item.created_at.strftime('%Y-%m-%d %H:%M')}"
    body = (
        f"Клиент: {p.client_name}\nАгент: {p.agent_name}{', ' + p.company if p.company else ''}\n"
        f"Локация: {p.city}{', ' + p.address if p.address else ''}\nТип: {p.deal_type}{' (' + p.deal_custom + ')' if p.deal_custom else ''}\nСтиль: {p.style}\n\n"
        + item.final_text
    )
    parts = _split_for_telegram(header + "\n\n" + body)
    try:
        await _edit_text_or_caption(
            callback.message, parts[0],
            InlineKeyboardMarkup(inline_keyboard=
                  [[InlineKeyboardButton(text="Создать похожий", callback_data=f"hist.{item.item_id}.clone")],
                  [InlineKeyboardButton(text="Экспорт .txt", callback_data=f"hist.{item.item_id}.export.txt"),
                  InlineKeyboardButton(text="Экспорт .md", callback_data=f"hist.{item.item_id}.export.md")],
                  [InlineKeyboardButton(text="В историю", callback_data="hist.back")]]))

    except TelegramBadRequest:
        await callback.message.answer(parts[0])
    for ptxt in parts[1:]:
        await callback.message.answer(ptxt)
    await callback.answer()


async def history_export(callback: CallbackQuery, state: FSMContext):
    # hist.{id}.export.{fmt}
    try:
        _, id_str, _, fmt = callback.data.split(".")
        item_id = int(id_str)
    except Exception:
        await callback.answer()
        return
    items = _HISTORY.get(callback.from_user.id, [])
    item = next((x for x in items if x.item_id == item_id), None)
    if not item:
        await callback.answer("Не найдено.")
        return
    buf = BufferedInputFile(item.final_text.encode("utf-8"), filename=f"review_{item.item_id}.{fmt}")
    await callback.message.answer_document(buf)
    await callback.answer()


async def history_clone(callback: CallbackQuery, state: FSMContext):
    # hist.{id}.clone → prefill and go to summary
    try:
        _, id_str, _ = callback.data.split(".")
        item_id = int(id_str)
    except Exception:
        await callback.answer()
        return
    items = _HISTORY.get(callback.from_user.id, [])
    item = next((x for x in items if x.item_id == item_id), None)
    if not item:
        await callback.answer("Не найдено.")
        return

    await state.update_data(**asdict(item.payload))
    await _edit_text_or_caption(callback.message, _summary_text(asdict(item.payload)), kb_summary())
    await state.set_state(FeedbackStates.showing_summary)
    await callback.answer()


async def history_back(callback: CallbackQuery, state: FSMContext):
    await open_history(callback, state)


# ==========================
# Navigation helpers
# ==========================

async def go_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await _edit_text_or_caption(callback.message, "Главное меню:",
                                InlineKeyboardMarkup(inline_keyboard=
                                                     [[InlineKeyboardButton(text="Создать отзыв", callback_data="nav.feedback_start")],
                                                      [InlineKeyboardButton(text="История", callback_data="hist.open")]]))
    await callback.answer()


# ==========================
# Router registration
# ==========================

def router(rt: Router):
    # start & cancel & menu
    rt.callback_query.register(start_feedback_flow, F.data == "nav.feedback_start")
    rt.callback_query.register(cancel_flow, F.data == "nav.cancel")
    rt.callback_query.register(go_menu, F.data == "nav.menu")

    # company skip / address skip
    rt.callback_query.register(handle_company_skip, F.data == "company.skip")
    rt.callback_query.register(handle_address_skip, F.data == "addr.skip")

    # city selection hub
    rt.callback_query.register(handle_city_choice, F.data.startswith("loc.city"))
    rt.callback_query.register(handle_city_choice, F.data == "loc.addr")
    rt.callback_query.register(handle_city_choice, F.data == "loc.next")

    # deal type
    rt.callback_query.register(handle_deal_type, F.data.startswith("deal."))

    # situation hints
    rt.callback_query.register(handle_situation_hints, F.data == "sit.hints")

    # style selection
    rt.callback_query.register(handle_style, F.data.startswith("style."))

    # edit menu
    rt.callback_query.register(open_edit_menu, F.data == "edit.open")
    rt.callback_query.register(edit_field_router, F.data.startswith("edit."))

    # generation
    rt.callback_query.register(start_generation, F.data == "gen.start")

    # mutations & pick & more
    rt.callback_query.register(mutate_variant, F.data.startswith("mutate."))
    rt.callback_query.register(gen_more_variant, F.data.startswith("gen.more."))
    rt.callback_query.register(pick_variant, F.data.startswith("pick."))
    rt.callback_query.register(back_to_variants, F.data == "gen.back")

    # finalize & export & clone
    rt.callback_query.register(finalize_choice, F.data == "done.final")
    rt.callback_query.register(export_text, F.data.startswith("export."))
    rt.callback_query.register(clone_from_final, F.data == "clone.from.final")

    # history
    rt.callback_query.register(open_history, F.data == "hist.open")
    rt.callback_query.register(history_open_item, F.data.startswith("hist.open."))
    rt.callback_query.register(history_export, F.data.contains(".export."))
    rt.callback_query.register(history_clone, F.data.contains(".clone"))
    rt.callback_query.register(history_back, F.data == "hist.back")

    # TEXT INPUTS
    rt.message.register(handle_client_name, FeedbackStates.waiting_client, F.text)
    rt.message.register(handle_agent_name, FeedbackStates.waiting_agent, F.text)
    rt.message.register(handle_company_name, FeedbackStates.waiting_company, F.text)
    rt.message.register(handle_city_input, FeedbackStates.waiting_city_input, F.text)
    rt.message.register(handle_address, FeedbackStates.waiting_address, F.text)
    rt.message.register(handle_deal_custom, FeedbackStates.waiting_deal_custom, F.text)
    rt.message.register(handle_situation, FeedbackStates.waiting_situation, F.text)
