# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\feedback_playbook.py
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Union

import aiohttp
from aiogram import F, Bot, Router
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
    # ForceReply не нужен в этом варианте
)

from bot.config import EXECUTOR_BASE_URL
from bot.handlers.feedback.model.review_payload import ReviewPayload
from bot.utils.database import history_add, history_list, history_get
from bot.states.states import FeedbackStates
from bot.utils.redis_repo import feedback_repo

logger = logging.getLogger(__name__)

# =============================================================================
# UX Texts (copy)
# =============================================================================

ASK_CLIENT_NAME = (
    "Введите имя клиента (обязательно).\n\n"
    "Например: Мария П., Иван, Семья Коваленко"
)
ASK_AGENT_NAME = "Введите имя агента недвижимости."
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
ASK_TONE = "Выберите тон текста."
TONE_INFO = (
    "Тон оф войс:"
    "\n— Дружелюбный: теплее, разговорно"
    "\n— Нейтральный: спокойно, по делу"
    "\n— Официальный: без эмоций, формально"
)
ASK_LENGTH = "Выберите длину текста."
LENGTH_INFO = (
    "Длины:"
    "\n— Короткий: до 250 знаков"
    "\n— Средний: до 450 знаков"
    "\n— Развернутый: до 1200 знаков"
)
SUMMARY_TITLE = "Проверьте данные перед генерацией:"
BTN_GENERATE = "Сгенерировать"
BTN_EDIT = "Изменить поле"
BTN_CANCEL = "Закрыть"
BTN_BACK = "Назад"
BTN_NEXT = "Дальше"
BTN_SKIP = "Пропустить"
GENERATING = "Генерирую варианты…"
MUTATING = "Меняю вариант…"
MUTATING_STYLE = "Меняю стиль…"
ONE_MORE = "Ещё вариант…"
ERROR_TEXT = "Не удалось получить текст от сервиса. Попробуйте еще раз."
READY_FINAL = (
    "Черновик готов. Отправьте клиенту и опубликуйте при согласии.\n"
    "Сохранено в истории."
)
HISTORY_EMPTY = "История пуста. Сгенерируйте первый черновик."
RECENT_DRAFTS_TITLE = "Недавние черновики:"
MAIN_MENU_TITLE = "Главное меню:"
PICKED_TEMPLATE = "Вы выбрали вариант {idx}. Готово к выдаче?"
RETURN_TO_VARIANTS = "Вернитесь к вариантам выше или запросите ещё один."
VARIANT_HEAD = "Вариант {idx}\n\n"
VARIANT_HEAD_UPDATED = "Вариант {idx} (обновлён)\n\n"

DEFAULT_CITIES = ["Москва", "Санкт-Петербург", "Тамбов"]

# лимит длины inline-запроса в Telegram (приблизительно)
INLINE_QUERY_MAXLEN = 256

# длины
LENGTH_CHOICES = [("short", "Короткий ≤250"), ("medium", "Средний ≤450"), ("long", "Развернутый ≤1200")]
LENGTH_LIMITS = {"short": 250, "medium": 450, "long": 1200}
def _length_limit(code: Optional[str]) -> Optional[int]:
    return LENGTH_LIMITS.get(code or "")

# Чекбоксы
CHECK_OFF = "⬜"
CHECK_ON  = "✅"

# Коды типов сделки и их подписи
DEAL_CHOICES = [
    ("sale", "Продажа"),
    ("buy", "Покупка"),
    ("rent", "Аренда"),
    ("mortgage", "Ипотека"),
    ("social_mortgage", "Гос. поддержка"),
    ("maternity_capital", "Мат. капитал)"),
    ("custom", "Другое")
]

# =============================================================================
# UI Helpers
# =============================================================================
Event = Union[Message, CallbackQuery]

async def _safe_cb_answer(cq: CallbackQuery, text: str | None = None, *,
                         show_alert: bool = False, cache_time: int = 0) -> None:
    """
    Безопасный ACK для callback-query: не падаем, если уже поздно или уже отвечали.
    """
    try:
        await cq.answer(text=text, show_alert=show_alert, cache_time=cache_time)
    except TelegramBadRequest:
        pass
    except Exception:
        pass


async def send_text(
    msg: Message,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
) -> Message:
    """
    Отправка нового сообщения без парсинга разметки.
    """
    return await msg.answer(text, reply_markup=kb, parse_mode=None)


async def reply_plain(
    msg: Message,
    text: str,
) -> Message:
    """
    Ответ на сообщение (reply) без парсинга разметки.
    Используй для валидационных ошибок и коротких подсказок.
    """
    return await msg.reply(text, parse_mode=None)


async def ui_reply(
    event: Event,
    text: str,
    kb: Optional[InlineKeyboardMarkup] = None,
    *,
    state: Optional[FSMContext] = None,
    bot: Optional[Bot] = None,
    use_anchor: bool = True,
) -> Message:
    """
    Универсальный ответ:
      - если event = Message (пользователь прислал текст) → всегда отправляет НОВОЕ сообщение;
      - если event = CallbackQuery (пользователь нажал кнопку) → пытается РЕДАКТИРОВАТЬ текущее
        (предпочтительно по anchor_id из state), при неудаче отправляет новое.

    Возвращает фактическое сообщение (оригинальное отредактированное или свежее).
    При отправке нового в режиме callback — обновит anchor_id в FSM.
    """
    if isinstance(event, Message):
        # Всегда новое при текстовом вводе
        new_msg = await event.answer(text, reply_markup=kb, parse_mode=None)
        if state:
            await state.update_data(anchor_id=new_msg.message_id)
        return new_msg

    # CallbackQuery
    cq: CallbackQuery = event
    msg = cq.message
    chat_id = msg.chat.id
    message_id_to_edit: Optional[int] = None

    if use_anchor and state:
        d = await state.get_data()
        anchor_id = d.get("anchor_id")
        if anchor_id:
            message_id_to_edit = anchor_id

    # Если anchor не задан — редактируем сам cq.message
    if message_id_to_edit is None:
        message_id_to_edit = msg.message_id

    # Пытаемся редактировать
    try:
        if bot:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id_to_edit,
                text=text,
                reply_markup=kb,
                parse_mode=None,
            )
            # Если редактирование прошло — anchor остаётся прежним
            return msg
        else:
            # у объектов типа Message есть методы edit_*
            await msg.edit_text(text, reply_markup=kb, parse_mode=None)
            return msg
    except TelegramBadRequest:
        # Пытаемся редактировать caption
        try:
            await msg.edit_caption(caption=text, reply_markup=kb, parse_mode=None)
            return msg
        except TelegramBadRequest:
            # Пытаемся хотя бы разметку
            try:
                await msg.edit_reply_markup(reply_markup=kb)
                return msg
            except TelegramBadRequest:
                # Всё не получилось — отправляем новое
                new_msg = await msg.answer(text, reply_markup=kb, parse_mode=None)
                if state:
                    await state.update_data(anchor_id=new_msg.message_id)
                return new_msg


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


async def run_long_operation_with_action(
    *, bot: Bot, chat_id: int, action: ChatAction, coro: asyncio.Future | asyncio.Task | Any
) -> Any:
    """Показывает чат-экшен, пока выполняется долгая операция."""
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
        return await coro
    finally:
        is_done = True
        pinger_task.cancel()


async def _return_to_summary(event: Event, state: FSMContext) -> None:
    """
    Показать сводку, соблюдая правило вывода:
      - если event = Message → отправляем новое сообщение;
      - если event = CallbackQuery → редактируем текущее (anchor).
    """
    d = await state.get_data()
    await ui_reply(event, _summary_text(d), kb_summary(), state=state)


def _ensure_deal_types(d: Dict[str, Any]) -> List[str]:
    """
    Берём массив выбранных типов сделки из FSM:
      - если уже есть deal_types -> возвращаем;
      - если остался старый deal_type (строка) -> оборачиваем в массив;
      - иначе пустой список.
    """
    if "deal_types" in d and isinstance(d["deal_types"], list):
        return [str(x) for x in d["deal_types"] if x]
    if d.get("deal_type"):
        return [str(d["deal_type"])]
    return []


# =============================================================================
# Keyboards (builders)
# =============================================================================
def kb_only_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.ai_tools")]]
    )


def kb_with_skip(skip_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_SKIP, callback_data=skip_cb)],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
        ]
    )


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
    rows.append(
        [
            InlineKeyboardButton(text="Ввести город", callback_data="loc.city.input"),
            InlineKeyboardButton(text="Указать адрес (опц.)", callback_data="loc.addr"),
        ]
    )
    rows.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_city_next_or_addr() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_NEXT, callback_data="loc.next")],
            [InlineKeyboardButton(text="Указать адрес (опц.)", callback_data="loc.addr")],
        ]
    )


def kb_city_addr_question() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Да, указать адрес", callback_data="loc.addr")],
            [InlineKeyboardButton(text=BTN_NEXT, callback_data="loc.next")],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
        ]
    )


def kb_deal_types_ms(d: Dict[str, Any]) -> InlineKeyboardMarkup:
    """
    Мультивыбор типов сделки с чекбоксами.
    Кнопки:
      - deal.toggle.<code>  — переключить чекбокс
      - deal.custom         — ввести/изменить «Другое…»
      - deal.custom.clear   — очистить «Другое»
      - deal.clear          — снять все галочки
      - deal.next           — продолжить
    """
    selected = set(_ensure_deal_types(d))
    rows: List[List[InlineKeyboardButton]] = []

    # Ряды с чекбоксами по DEAL_CHOICES
    row: List[InlineKeyboardButton] = []
    for code, label in DEAL_CHOICES:
        mark = CHECK_ON if code in selected else CHECK_OFF
        row.append(InlineKeyboardButton(text=f"{mark} {label}", callback_data=f"deal.toggle.{code}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Другое
    custom = (d.get("deal_custom") or "").strip()
    if custom:
        # показываем значение и действия
        rows.append([InlineKeyboardButton(text=f"✏️ Другое: {custom}", callback_data="deal.custom")])
        rows.append([
            InlineKeyboardButton(text="Изменить", callback_data="deal.custom"),
            InlineKeyboardButton(text="Очистить", callback_data="deal.custom.clear"),
        ])
    else:
        rows.append([InlineKeyboardButton(text="➕ Другое…", callback_data="deal.custom")])

    # Управление
    rows.append([
        InlineKeyboardButton(text="Сбросить", callback_data="deal.clear"),
        InlineKeyboardButton(text=BTN_NEXT, callback_data="deal.next"),
    ])
    # Отмена
    rows.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_tone() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Дружелюбный", callback_data="tone.friendly"),
            InlineKeyboardButton(text="Нейтральный", callback_data="tone.neutral"),
        ],
        [
            InlineKeyboardButton(text="Официальный", callback_data="tone.formal"),
        ],
        [InlineKeyboardButton(text="О тоне", callback_data="tone.info")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_length() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Короткий (≤250)", callback_data="length.short"),
            InlineKeyboardButton(text="Средний (≤450)", callback_data="length.medium"),
        ],
        [
            InlineKeyboardButton(text="Развернутый (≤1200)", callback_data="length.long"),
        ],
        [InlineKeyboardButton(text="О длине", callback_data="length.info")],
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
        [
            InlineKeyboardButton(text="Имя клиента", callback_data="edit.client"),
            InlineKeyboardButton(text="Имя агента", callback_data="edit.agent"),
        ],
        [
            InlineKeyboardButton(text="Компания", callback_data="edit.company"),
            InlineKeyboardButton(text="Город", callback_data="edit.city"),
        ],
        [
            InlineKeyboardButton(text="Адрес", callback_data="edit.addr"),
            InlineKeyboardButton(text="Тип сделки", callback_data="edit.deal"),
        ],
        [
            InlineKeyboardButton(text="Ситуация", callback_data="edit.sit"),
            InlineKeyboardButton(text="Тон", callback_data="edit.tone"),
        ],
        [InlineKeyboardButton(text="Длина", callback_data="edit.length")],
        [InlineKeyboardButton(text="Готово", callback_data="edit.done")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_variant(index: int, total: int) -> InlineKeyboardMarkup:
    """
    Клавиатура варианта с навигацией 'Посмотреть N' над кнопкой 'Выбрать этот'.
    """
    rows: List[List[InlineKeyboardButton]] = []
    # Навигация
    nav: List[InlineKeyboardButton] = []
    if total > 1:
        if index > 1:
            nav.append(InlineKeyboardButton(text=f"Посмотреть {index-1} вариант", callback_data=f"view.{index-1}"))
        if index < total:
            nav.append(InlineKeyboardButton(text=f"Посмотреть {index+1} вариант", callback_data=f"view.{index+1}"))
    if nav:
        rows.append(nav)
    # Действия
    rows.append([InlineKeyboardButton(text="Выбрать этот", callback_data=f"pick.{index}")])
    rows.append([
        InlineKeyboardButton(text="Сделать короче", callback_data=f"mutate.{index}.short"),
        InlineKeyboardButton(text="Сделать длиннее", callback_data=f"mutate.{index}.long"),
    ])
    rows.append([InlineKeyboardButton(text="Изменить тон", callback_data=f"mutate.{index}.style")])
    rows.append([InlineKeyboardButton(text="Ещё вариант", callback_data=f"gen.more.{index}")])
    rows.append([
        InlineKeyboardButton(text="Экспорт .txt", callback_data=f"export.{index}.txt"),
        InlineKeyboardButton(text="Экспорт .md", callback_data=f"export.{index}.md"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_variants_common() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Показать все поля", callback_data="summary.show")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_final() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Экспорт .txt", callback_data="export.final.txt"),
            InlineKeyboardButton(text="Экспорт .md", callback_data="export.final.md"),
        ],
        [InlineKeyboardButton(text="Создать похожий", callback_data="clone.from.final")],
        [InlineKeyboardButton(text="В меню", callback_data="nav.menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_history(items: List[Any]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for it in items[:10]:
        # it — ORM ReviewHistory
        label = f"#{it.id} · {it.created_at.strftime('%Y-%m-%d %H:%M')} · {it.city or '—'} · {it.deal_type or '—'}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"hist.open.{it.id}")])
    rows.append(
        [
            InlineKeyboardButton(text="Поиск", callback_data="hist.search"),
            InlineKeyboardButton(text="В меню", callback_data="nav.menu"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_menu_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать отзыв", callback_data="nav.feedback_start")],
            [InlineKeyboardButton(text="История", callback_data="hist.open")],
        ]
    )


def kb_try_again_gen() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Попробовать ещё раз", callback_data="gen.start")]]
    )


def kb_situation_hints() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Показать подсказки", callback_data="sit.hints")]]
    )

def _inline_prefill_text(src: str) -> str:
    # убираем переводы строк/лишние пробелы и обрезаем до лимита
    s = " ".join((src or "").split())
    if len(s) > INLINE_QUERY_MAXLEN:
        s = s[: INLINE_QUERY_MAXLEN - 1].rstrip() + "…"
    return s

def kb_situation_insert_btn(draft: str) -> InlineKeyboardMarkup:
    # Кнопка вставит @bot <draft> в поле ввода текущего чата
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Вставить текст", switch_inline_query_current_chat=_inline_prefill_text(draft))],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.cancel")],
        ]
    )


# =============================================================================
# HTTP client (microservice)
# =============================================================================
async def _request_generate(
    payload: ReviewPayload,
    *,
    num_variants: int = 3,
    timeout_sec: int = 90,
    **extra: Any,  # прокидываем новые поля (tone, length_hint и т.п.)
) -> List[str]:
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/review/generate"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    body = asdict(payload)
    body.update({"num_variants": num_variants})
    if extra:
        # добавляем только непустые значения
        body.update({k: v for k, v in extra.items() if v is not None})
    async with aiohttp.ClientSession(timeout=t) as session:
        async with session.post(url, json=body) as resp:
            if resp.status != 200:
                detail = await _extract_error_detail(resp)
                raise RuntimeError(f"Executor HTTP {resp.status}: {detail}")
            data = await resp.json()
            variants = (data or {}).get("variants")
            if not variants:
                txt = (data or {}).get("text", "").strip()
                if not txt:
                    raise RuntimeError("Executor returned no variants")
                return [txt]
            return [str(v).strip() for v in variants if str(v).strip()]


async def _request_mutate(
    base_text: str,
    *,
    operation: str,
    style: Optional[str],
    payload: ReviewPayload,
    timeout_sec: int = 60,
    **extra: Any,  # на будущее: tone/length_hint и т.п.
) -> str:
    """operation: 'short' | 'long' | 'style'"""
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/review/mutate"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    body = {
        "base_text": base_text,
        "operation": operation,
        "style": style,
        "context": asdict(payload),
    }
    if extra:
        body.update({k: v for k, v in extra.items() if v is not None})
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


# =============================================================================
# Rendering helpers
# =============================================================================
def _shorten(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _summary_text(d: Dict[str, Any]) -> str:
    lines = [SUMMARY_TITLE, ""]
    lines.append(f"• Клиент: {d.get('client_name')}")
    agent = d.get("agent_name")
    company = d.get("company")
    lines.append(f"• Агент/компания: {agent}, {company}" if company else f"• Агент: {agent}")
    loc = d.get("city") or "—"
    addr = d.get("address")
    lines.append(f"• Локация: {loc}{', ' + addr if addr else ''}")
    # Тип сделки (мультивыбор + опционально «другое»)
    deal_types = _ensure_deal_types(d)
    human_map = {code: title for code, title in DEAL_CHOICES}
    human_list = [human_map.get(code, code) for code in deal_types]
    if d.get("deal_custom"):
        human_list.append(f"Другое: {d.get('deal_custom')}")
    deal_line = ", ".join(human_list) if human_list else "—"
    lines.append(f"• Тип сделки: {deal_line}")
    lines.append(f"• Ситуация: {_shorten(d.get('situation', ''), 150)}")
    # Тон / Длина
    tone = d.get("tone") or d.get("style")  # для совместимости
    length_code = d.get("length")
    length_human = {"short": "короткий (≤250)", "medium": "средний (≤450)", "long": "развернутый (≤1200)"}\
        .get(length_code, "—")
    lines.append(f"• Тон: {tone or '—'}")
    lines.append(f"• Длина: {length_human}")
    return "\n".join(lines)


def _payload_from_state(d: Dict[str, Any]) -> ReviewPayload:
    # Для обратной совместимости с микросервисом:
    # в поле deal_type передаём коды через запятую; если задано deal_custom,
    # добавляем код 'custom' (если его нет в списке).
    deal_types = _ensure_deal_types(d)
    deal_custom = d.get("deal_custom")
    codes = list(dict.fromkeys(deal_types))  # уникальные, в порядке выбора
    if deal_custom and "custom" not in codes:
        codes.append("custom")
    deal_type_str = ",".join(codes) if codes else None
    return ReviewPayload(
        client_name=d.get("client_name"),
        agent_name=d.get("agent_name"),
        company=d.get("company"),
        city=d.get("city"),
        address=d.get("address"),
        deal_type=deal_type_str,
        deal_custom=deal_custom,
        situation=d.get("situation"),
        # В payload.style теперь передаём ТОН
        style=d.get("tone") or d.get("style"),
    )


# =============================================================================
# Flow handlers
# =============================================================================
async def start_feedback_flow(callback: CallbackQuery, state: FSMContext):
    # якорим текущее сообщение для последующих редактирований
    await state.update_data(anchor_id=callback.message.message_id)
    await ui_reply(callback, ASK_CLIENT_NAME, kb_only_cancel(), state=state)
    await state.set_state(FeedbackStates.waiting_client)
    await callback.answer()
    # Redis: старт сессии
    await feedback_repo.start(
        callback.from_user.id,
        meta={"chat_id": callback.message.chat.id, "flow": "review"},
    )
    await feedback_repo.set_stage(callback.from_user.id, "waiting_client")


async def cancel_flow(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await ui_reply(callback, "Операция отменена. Чем займёмся?", None, state=state)
    await callback.answer()
    await feedback_repo.set_fields(callback.from_user.id, {"status": "cancelled"})


async def handle_client_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 60:
        await reply_plain(message, "Имя должно быть 2–60 символов. Попробуйте снова.")
        return
    await state.update_data(client_name=name)
    await feedback_repo.set_fields(message.from_user.id, {"client_name": name, "stage": "waiting_agent"})
    d = await state.get_data()
    if d.get("edit_field") == "client":
        await _return_to_summary(message, state)
        return
    await ui_reply(message, ASK_AGENT_NAME, state=state)
    await state.set_state(FeedbackStates.waiting_agent)


async def handle_agent_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 60:
        await reply_plain(message, "Имя должно быть 2–60 символов. Попробуйте снова.")
        return
    await state.update_data(agent_name=name)
    await feedback_repo.set_fields(message.from_user.id, {"agent_name": name})
    d = await state.get_data()
    if d.get("edit_field") == "agent":
        await _return_to_summary(message, state)
        return
    await ui_reply(message, ASK_COMPANY, kb_with_skip("company.skip"), state=state)
    await state.set_state(FeedbackStates.waiting_company)


async def handle_company_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(company=None)
    await feedback_repo.set_fields(callback.from_user.id, {"company": None})
    d = await state.get_data()
    if d.get("edit_field") == "company":
        await _return_to_summary(callback, state)
    else:
        await ui_reply(callback, ASK_CITY, kb_cities(), state=state)
        await state.set_state(FeedbackStates.waiting_city_mode)
    await callback.answer()


async def handle_company_name(message: Message, state: FSMContext):
    company = (message.text or "").strip()
    if company and (len(company) < 2 or len(company) > 80):
        await reply_plain(message, "Название компании 2–80 символов или пропустите.")
        return
    await state.update_data(company=company or None)
    await feedback_repo.set_fields(message.from_user.id, {"company": company or None})
    d = await state.get_data()
    if d.get("edit_field") == "company":
        await _return_to_summary(message, state)
    else:
        await ui_reply(message, ASK_CITY, kb_cities(), state=state)
        await state.set_state(FeedbackStates.waiting_city_mode)


async def handle_city_choice(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    if data == "loc.city.input":
        await ui_reply(callback, ASK_CITY_INPUT, kb_only_cancel(), state=state)
        await state.set_state(FeedbackStates.waiting_city_input)
        await callback.answer()
        return

    if data == "loc.addr":
        await ui_reply(callback, ASK_ADDRESS, kb_with_skip("addr.skip"), state=state)
        await state.set_state(FeedbackStates.waiting_address)
        await callback.answer()
        return

    if data.startswith("loc.city."):
        city = data.split(".", 2)[2]
        await state.update_data(city=city)
        await feedback_repo.set_fields(callback.from_user.id, {"city": city})
        await ui_reply(
            callback,
            f"Город: {city}.\n\nНужно указать адрес?",
            kb_city_addr_question(),
            state=state,
        )
        await state.set_state(FeedbackStates.waiting_city_mode)
        await callback.answer()
        return

    if data == "loc.next":
        d = await state.get_data()
        if d.get("edit_field") == "city":
            await _return_to_summary(callback, state)
        else:
            await ui_reply(callback, ASK_DEAL_TYPE, kb_deal_types_ms(d), state=state)
            await state.set_state(FeedbackStates.waiting_deal_type)
        await callback.answer()
        return


async def handle_city_input(message: Message, state: FSMContext):
    city = (message.text or "").strip()
    if len(city) < 2:
        await reply_plain(message, "Слишком короткое название города. Попробуйте снова.")
        return
    await state.update_data(city=city)
    await feedback_repo.set_fields(message.from_user.id, {"city": city})
    await send_text(message, f"Город: {city}.", kb_city_next_or_addr())
    await state.set_state(FeedbackStates.waiting_city_mode)


async def handle_address(message: Message, state: FSMContext):
    addr = (message.text or "").strip()
    await state.update_data(address=addr or None)
    await feedback_repo.set_fields(message.from_user.id, {"address": addr or None})
    d = await state.get_data()
    if d.get("edit_field") in ("address", "city"):
        await _return_to_summary(message, state)
    else:
        await ui_reply(message, ASK_DEAL_TYPE, kb_deal_types_ms(d), state=state)
        await state.set_state(FeedbackStates.waiting_deal_type)


async def handle_address_skip(callback: CallbackQuery, state: FSMContext):
    await state.update_data(address=None)
    await feedback_repo.set_fields(callback.from_user.id, {"address": None})
    d = await state.get_data()
    if d.get("edit_field") in ("address", "city"):
        await _return_to_summary(callback, state)
    else:
        await ui_reply(callback, ASK_DEAL_TYPE, kb_deal_types_ms(d), state=state)
        await state.set_state(FeedbackStates.waiting_deal_type)
    await callback.answer()


async def handle_deal_type(callback: CallbackQuery, state: FSMContext):
    data = callback.data or ""
    if not data.startswith("deal."):
        return await callback.answer()

    d = await state.get_data()
    selected = set(_ensure_deal_types(d))

    # deal.toggle.<code>
    if data.startswith("deal.toggle."):
        code = data.split(".", 2)[2]
        if code in selected:
            selected.remove(code)
        else:
            selected.add(code)
        await state.update_data(deal_types=list(selected))
        await feedback_repo.set_fields(callback.from_user.id, {"deal_types": list(selected)})
        # перерисовываем клавиатуру
        d = await state.get_data()
        await ui_reply(callback, ASK_DEAL_TYPE, kb_deal_types_ms(d), state=state)
        return await callback.answer()

    # Очистить весь выбор
    if data == "deal.clear":
        await state.update_data(deal_types=[], deal_custom=None)
        await feedback_repo.set_fields(callback.from_user.id, {"deal_types": [], "deal_custom": None})
        d = await state.get_data()
        await ui_reply(callback, ASK_DEAL_TYPE, kb_deal_types_ms(d), state=state)
        return await callback.answer("Выбор очищен")

    # Ввод/изменение «Другое…»
    if data == "deal.custom":
        await ui_reply(callback, ASK_DEAL_CUSTOM, kb_only_cancel(), state=state)
        await state.set_state(FeedbackStates.waiting_deal_custom)
        return await callback.answer()

    if data == "deal.custom.clear":
        await state.update_data(deal_custom=None)
        await feedback_repo.set_fields(callback.from_user.id, {"deal_custom": None})
        d = await state.get_data()
        await ui_reply(callback, ASK_DEAL_TYPE, kb_deal_types_ms(d), state=state)
        return await callback.answer("Очищено")

    # Продолжить
    if data == "deal.next":
        # если редактировали поле – возвращаемся к сводке
        if d.get("edit_field") == "deal":
            await _return_to_summary(callback, state)
        else:
            await ui_reply(callback, ASK_SITUATION, kb_situation_hints(), state=state)
            await state.set_state(FeedbackStates.waiting_situation)
        return await callback.answer()

    return await callback.answer()


async def handle_deal_custom(message: Message, state: FSMContext):
    custom = (message.text or "").strip()
    if len(custom) < 2:
        await reply_plain(message, "Слишком короткое значение. Уточните тип сделки.")
        return
    # сохраняем текст «Другое», код 'custom' добавим при сборке payload,
    # а пользователю снова покажем мультивыбор (чтобы мог добавить галочки)
    d = await state.get_data()
    deal_types = _ensure_deal_types(d)
    await state.update_data(deal_custom=custom, deal_types=deal_types)
    await feedback_repo.set_fields(message.from_user.id, {"deal_custom": custom, "deal_types": deal_types})
    # Возвращаемся к мультивыбору типов
    d = await state.get_data()
    await ui_reply(message, ASK_DEAL_TYPE, kb_deal_types_ms(d), state=state)
    await state.set_state(FeedbackStates.waiting_deal_type)


async def handle_situation_hints(callback: CallbackQuery, state: FSMContext):
    # Показываем подсказки в том же сообщении (редактируем)
    await ui_reply(callback, HINT_SITUATION, kb_situation_hints(), state=state)
    await callback.answer()


async def handle_situation(message: Message, state: FSMContext):
    txt = (message.text or "").strip()
    if len(txt) < 100 or len(txt) > 4000:
        await state.update_data(situation_draft=txt)
        await feedback_repo.set_fields(message.from_user.id, {"status": "validation_error", "situation_draft": txt})
        await message.answer(
            "Нужно 100–4000 символов. Добавьте деталей (сроки, результат, особенности).",
            reply_markup=kb_situation_insert_btn(txt),
        )
        return
    await state.update_data(situation=txt)
    await feedback_repo.set_fields(message.from_user.id, {"situation": txt, "stage": "waiting_tone"})
    d = await state.get_data()
    if d.get("edit_field") == "situation":
        await _return_to_summary(message, state)
    else:
        # Переходим к выбору тона
        await ui_reply(message, ASK_TONE, kb_tone(), state=state)
        await state.set_state(FeedbackStates.waiting_tone)




async def handle_tone(callback: CallbackQuery, state: FSMContext, bot: Optional[Bot] = None):
    # ранний ACK (ветка со сменой тона может быть долгой)
    await _safe_cb_answer(callback)
    data = callback.data
    if data == "tone.info":
        await ui_reply(callback, TONE_INFO, kb_tone(), state=state)
        await _safe_cb_answer(callback)
        return
    if not data.startswith("tone."):
        await _safe_cb_answer(callback)
        return
    tone = data.split(".", 1)[1]
    await state.update_data(tone=tone)
    await feedback_repo.set_fields(callback.from_user.id, {"tone": tone})
    d = await state.get_data()

    # Если мы выбирали тон в режиме мутации варианта — сразу меняем текст
    mut_idx = d.get("mutating_idx")
    if mut_idx:
        variants: List[str] = d.get("variants", [])
        if 1 <= mut_idx <= len(variants):
            base_text = variants[mut_idx - 1]
            await ui_reply(callback, "Меняю тон…", state=state)
            chat_id = callback.message.chat.id
            async def _do():
                payload = _payload_from_state(await state.get_data())
                return await _request_mutate(base_text, operation="style", style=tone, payload=payload)
            try:
                new_text: str = await run_long_operation_with_action(
                    bot=bot or callback.bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do()
                )
                variants[mut_idx - 1] = new_text
                await state.update_data(variants=variants, mutating_idx=None)
                parts = _split_for_telegram(new_text)
                head = VARIANT_HEAD_UPDATED.format(idx=mut_idx) + parts[0]
                total = len(variants)
                await ui_reply(callback, head, kb_variant(mut_idx, total), state=state)
                for p in parts[1:]:
                    await send_text(callback.message, p)
                await state.update_data(viewer_idx=mut_idx)
                await feedback_repo.set_fields(callback.from_user.id, {"viewer_idx": mut_idx})
            except Exception as e:
                await ui_reply(callback, f"{ERROR_TEXT}\n\n{e}", state=state)
        await _safe_cb_answer(callback)
        return

    # Иначе — после тона спрашиваем длину
    await ui_reply(callback, ASK_LENGTH, kb_length(), state=state)
    await state.set_state(FeedbackStates.waiting_length)
    await _safe_cb_answer(callback)
    await feedback_repo.set_stage(callback.from_user.id, "waiting_length")

async def handle_length(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    if data == "length.info":
        await ui_reply(callback, LENGTH_INFO, kb_length(), state=state)
        return await callback.answer()
    if not data.startswith("length."):
        return await callback.answer()
    length_code = data.split(".", 1)[1]
    if length_code not in {"short", "medium", "long"}:
        return await callback.answer()
    await state.update_data(length=length_code)
    await feedback_repo.set_fields(callback.from_user.id, {"length": length_code})
    d = await state.get_data()
    await ui_reply(callback, _summary_text(d), kb_summary(), state=state)
    await state.set_state(FeedbackStates.showing_summary)
    await callback.answer()
    await feedback_repo.set_stage(callback.from_user.id, "summary")


async def open_edit_menu(callback: CallbackQuery, state: FSMContext):
    await ui_reply(callback, "Что изменить?", kb_edit_menu(), state=state)
    await callback.answer()


async def edit_field_router(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    await callback.answer()
    if data == "edit.client":
        await state.update_data(edit_field="client")
        await ui_reply(callback, ASK_CLIENT_NAME, kb_only_cancel(), state=state)
        await state.set_state(FeedbackStates.waiting_client)
    elif data == "edit.agent":
        await state.update_data(edit_field="agent")
        await ui_reply(callback, ASK_AGENT_NAME, kb_only_cancel(), state=state)
        await state.set_state(FeedbackStates.waiting_agent)
    elif data == "edit.company":
        await state.update_data(edit_field="company")
        await ui_reply(callback, ASK_COMPANY, kb_with_skip("company.skip"), state=state)
        await state.set_state(FeedbackStates.waiting_company)
    elif data == "edit.city":
        await state.update_data(edit_field="city")
        await ui_reply(callback, ASK_CITY, kb_cities(), state=state)
        await state.set_state(FeedbackStates.waiting_city_mode)
    elif data == "edit.addr":
        await state.update_data(edit_field="address")
        await ui_reply(callback, ASK_ADDRESS, kb_with_skip("addr.skip"), state=state)
        await state.set_state(FeedbackStates.waiting_address)
    elif data == "edit.deal":
        await state.update_data(edit_field="deal")
        d = await state.get_data()
        await ui_reply(callback, ASK_DEAL_TYPE, kb_deal_types_ms(d), state=state)
        await state.set_state(FeedbackStates.waiting_deal_type)
    elif data == "edit.sit":
        await state.update_data(edit_field="situation")
        await ui_reply(callback, ASK_SITUATION, kb_situation_hints(), state=state)
        await state.set_state(FeedbackStates.waiting_situation)
    elif data == "edit.tone" or data == "edit.style":  # alias: edit.style -> тон
        await state.update_data(edit_field="tone")
        await ui_reply(callback, ASK_TONE, kb_tone(), state=state)
        await state.set_state(FeedbackStates.waiting_tone)
    elif data == "edit.length":
        await state.update_data(edit_field="length")
        await ui_reply(callback, ASK_LENGTH, kb_length(), state=state)
        await state.set_state(FeedbackStates.waiting_length)
    elif data == "edit.done":
        d = await state.get_data()
        await ui_reply(callback, _summary_text(d), kb_summary(), state=state)
        await state.set_state(FeedbackStates.showing_summary)


async def start_generation(callback: CallbackQuery, state: FSMContext, bot: Bot):
    # ACK РАНО, чтобы не словить "query is too old"
    await _safe_cb_answer(callback)

    d = await state.get_data()
    try:
        payload = _payload_from_state(d)
    except Exception:
        await ui_reply(callback, "Не все поля заполнены. Вернитесь и заполните обязательные поля.", state=state)
        await callback.answer()
        return

    # Показываем "генерирую…" (редактируем текущий якорь)
    await ui_reply(callback, GENERATING, state=state)

    chat_id = callback.message.chat.id

    async def _do():
        # передаём пожелание по длине
        length_hint = _length_limit((await state.get_data()).get("length"))
        # Redis: сохраняем запрос перед отправкой
        await feedback_repo.set_fields(
            callback.from_user.id,
            {"status": "generating", "payload": asdict(payload), "length_hint": length_hint},
        )
        return await _request_generate(payload, num_variants=3, length_hint=length_hint)

    try:
        variants: List[str] = await run_long_operation_with_action(
            bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do()
        )
        await state.update_data(variants=variants)
        await feedback_repo.set_fields(callback.from_user.id, {"status": "variants_ready", "variants_json": variants})

        # Показываем только первый вариант + навигация
        total = len(variants)
        idx = 1
        parts = _split_for_telegram(variants[0])
        head = VARIANT_HEAD.format(idx=idx) + parts[0]
        await ui_reply(callback, head, kb_variant(idx, total), state=state, bot=bot)
        for p in parts[1:]:
            await send_text(callback.message, p)
        # Позиция зрителя и якорь в Redis
        anchor_id = (await state.get_data()).get("anchor_id")
        await state.update_data(viewer_idx=idx)
        await feedback_repo.set_fields(
            callback.from_user.id, {"viewer_idx": idx, "anchor_msg_id": anchor_id}
        )
        await state.set_state(FeedbackStates.browsing_variants)
    except Exception as e:
        await ui_reply(callback, f"{ERROR_TEXT}\n\n{e}", kb_try_again_gen(), state=state, bot=bot)
        await state.set_state(FeedbackStates.showing_summary)
        await feedback_repo.set_error(callback.from_user.id, str(e))
    finally:
        # повторный ACK не обязателен; на всякий случай — безопасно
        await _safe_cb_answer(callback)


async def mutate_variant(callback: CallbackQuery, state: FSMContext, bot: Bot):
    # ранний ACK
    await _safe_cb_answer(callback)
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
        await state.update_data(mutating_idx=idx)
        await ui_reply(callback, ASK_TONE, kb_tone(), state=state)
        await state.set_state(FeedbackStates.waiting_tone)
        await _safe_cb_answer(callback)
        return

    operation = "short" if op == "short" else "long"

    await ui_reply(callback, MUTATING, state=state)  # редактируем якорь
    await feedback_repo.set_fields(callback.from_user.id, {"status": "mutating", "operation": operation, "idx": idx})
    chat_id = callback.message.chat.id

    async def _do():
        return await _request_mutate(base_text, operation=operation, style=None, payload=payload)

    try:
        new_text: str = await run_long_operation_with_action(
            bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do()
        )
        variants[idx - 1] = new_text
        await state.update_data(variants=variants)
        await feedback_repo.set_fields(callback.from_user.id, {"status": "variants_ready", "variants_json": variants})
        parts = _split_for_telegram(new_text)
        head = VARIANT_HEAD_UPDATED.format(idx=idx) + parts[0]
        total = len(variants)
        await ui_reply(callback, head, kb_variant(idx, total), state=state, bot=bot)
        for p in parts[1:]:
            await send_text(callback.message, p)
        # если редактировали текущий — фиксируем viewer_idx
        await state.update_data(viewer_idx=idx)
        await feedback_repo.set_fields(callback.from_user.id, {"viewer_idx": idx})
    except Exception as e:
        await ui_reply(callback, f"{ERROR_TEXT}\n\n{e}", state=state)
        await feedback_repo.set_error(callback.from_user.id, str(e))
    finally:
        await _safe_cb_answer(callback)


# (обработчик изменения тона для мутации теперь встроен в handle_tone)
async def gen_more_variant(callback: CallbackQuery, state: FSMContext, bot: Bot):
    # ранний ACK
    await _safe_cb_answer(callback)
    d = await state.get_data()
    payload = _payload_from_state(d)

    await ui_reply(callback, ONE_MORE, state=state)
    await feedback_repo.set_fields(callback.from_user.id, {"status": "generating_more"})
    chat_id = callback.message.chat.id

    async def _do():
        length_hint = _length_limit((await state.get_data()).get("length"))
        lst = await _request_generate(payload, num_variants=1, length_hint=length_hint)
        return lst[0]

    try:
        new_text: str = await run_long_operation_with_action(
            bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do()
        )
        variants: List[str] = d.get("variants", [])
        variants.append(new_text)
        await state.update_data(variants=variants)
        await feedback_repo.set_fields(callback.from_user.id, {"status": "variants_ready", "variants_json": variants})
        idx = len(variants)
        total = len(variants)
        parts = _split_for_telegram(new_text)
        head = VARIANT_HEAD.format(idx=idx) + parts[0]
        # Показываем новый (последний) вариант и запоминаем позицию
        await ui_reply(callback, head, kb_variant(idx, total), state=state, bot=bot)
        for p in parts[1:]:
            await send_text(callback.message, p)
        await state.update_data(viewer_idx=idx)
        await feedback_repo.set_fields(callback.from_user.id, {"viewer_idx": idx})
    except Exception as e:
        await ui_reply(callback, f"{ERROR_TEXT}\n\n{e}", state=state)
        await feedback_repo.set_error(callback.from_user.id, str(e))
    finally:
        await _safe_cb_answer(callback)


async def view_variant(callback: CallbackQuery, state: FSMContext, bot: Optional[Bot] = None):
    """Показать другой вариант по кнопке 'Посмотреть N'."""
    await _safe_cb_answer(callback)
    data = callback.data  # view.{index}
    try:
        _, idx_str = data.split(".")
        idx = int(idx_str)
    except Exception:
        return
    d = await state.get_data()
    variants: List[str] = d.get("variants", [])
    total = len(variants)
    if idx < 1 or idx > total:
        return
    parts = _split_for_telegram(variants[idx - 1])
    head = VARIANT_HEAD.format(idx=idx) + parts[0]
    await ui_reply(callback, head, kb_variant(idx, total), state=state, bot=bot or callback.bot)
    for p in parts[1:]:
        await send_text(callback.message, p)
    await state.update_data(viewer_idx=idx)
    await feedback_repo.set_fields(callback.from_user.id, {"viewer_idx": idx})
    await _safe_cb_answer(callback)


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
    await ui_reply(callback, PICKED_TEMPLATE.format(idx=idx),
                   InlineKeyboardMarkup(
                       inline_keyboard=[
                           [InlineKeyboardButton(text="Готово", callback_data="done.final")],
                           [InlineKeyboardButton(text="Вернуться к вариантам", callback_data="gen.back")],
                       ]
                   ),
                   state=state)
    await callback.answer()


async def back_to_variants(callback: CallbackQuery, state: FSMContext):
    await ui_reply(callback, RETURN_TO_VARIANTS, kb_variants_common(), state=state)
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

    # save to DB history
    history_add(callback.from_user.id, asdict(payload), final_text)

    await ui_reply(callback, READY_FINAL, kb_final(), state=state)
    await state.update_data(final_text=final_text)
    await callback.answer()
    await feedback_repo.set_fields(
        callback.from_user.id,
        {"status": "finalized", "final_idx": idx, "final_text": final_text},
    )
    await feedback_repo.finish(callback.from_user.id)


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
    d = await state.get_data()
    await ui_reply(callback, _summary_text(d), kb_summary(), state=state)
    await state.set_state(FeedbackStates.showing_summary)
    await callback.answer()


# =============================================================================
# History
# =============================================================================
async def open_history(callback: CallbackQuery, state: FSMContext):
    items = history_list(callback.from_user.id, limit=10)
    if not items or len(items) == 0:
        await ui_reply(
            callback,
            HISTORY_EMPTY,
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="nav.menu")]]
            ),
            state=state,
        )
        await callback.answer()
        return

    text_lines = [RECENT_DRAFTS_TITLE]
    for it in items[:10]:
        text_lines.append(
            f"#{it.id} · {it.created_at.strftime('%Y-%m-%d %H:%M')} · {it.city or '—'} · {it.deal_type or '—'} · {it.client_name or '—'}"
        )
    await ui_reply(callback, "\n".join(text_lines), kb_history(items), state=state)
    await callback.answer()


async def history_open_item(callback: CallbackQuery, state: FSMContext):
    # hist.open.{id}
    try:
        _, _, id_str = callback.data.split(".")
        item_id = int(id_str)
    except Exception:
        await callback.answer()
        return

    item = history_get(callback.from_user.id, item_id)
    if not item:
        await callback.answer("Не найдено.")
        return

    # humanize deal types
    human_map = {code: title for code, title in DEAL_CHOICES}
    codes = [c for c in (item.deal_type or "").split(",") if c]
    human_list = [human_map.get(c, c) for c in codes if c]
    if item.deal_custom:
        human_list.append(f"Другое: {item.deal_custom}")
    deal_line = ", ".join(human_list) if human_list else "—"

    header = f"#{item.id} · {item.created_at.strftime('%Y-%m-%d %H:%M')}"
    body = (
        f"Клиент: {item.client_name or '—'}\n"
        f"Агент: {item.agent_name or '—'}{(', ' + item.company) if item.company else ''}\n"
        f"Локация: {item.city or '—'}{(', ' + item.address) if item.address else ''}\n"
        f"Тип: {deal_line}\n"
        f"Стиль: {item.style or '—'}\n\n"
        f"{item.final_text}"
    )
    parts = _split_for_telegram(header + "\n\n" + body)
    await ui_reply(
        callback,
        parts[0],
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Создать похожий", callback_data=f"hist.{item.id}.clone")],
                [
                    InlineKeyboardButton(text="Экспорт .txt", callback_data=f"hist.{item.id}.export.txt"),
                    InlineKeyboardButton(text="Экспорт .md", callback_data=f"hist.{item.id}.export.md"),
                ],
                [InlineKeyboardButton(text="В историю", callback_data="hist.back")],
            ]
        ),
        state=state,
    )
    for ptxt in parts[1:]:
        await send_text(callback.message, ptxt)
    await callback.answer()


async def history_export(callback: CallbackQuery, state: FSMContext):
    # hist.{id}.export.{fmt}
    try:
        _, id_str, _, fmt = callback.data.split(".")
        item_id = int(id_str)
    except Exception:
        await callback.answer()
        return
    item = history_get(callback.from_user.id, item_id)
    if not item:
        await callback.answer("Не найдено.")
        return
    buf = BufferedInputFile(item.final_text.encode("utf-8"), filename=f"review_{item.id}.{fmt}")
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
    item = history_get(callback.from_user.id, item_id)
    if not item:
        await callback.answer("Не найдено.")
        return
    # распаковываем поля payload из ORM-объекта истории
    # конвертируем deal_type CSV в deal_types list (без 'custom')
    codes = [c for c in (item.deal_type or "").split(",") if c]
    base_codes = [c for c in codes if c != "custom"]
    payload_dict = {
        "client_name": item.client_name,
        "agent_name":  item.agent_name,
        "company":     item.company,
        "city":        item.city,
        "address":     item.address,
        # состояние использует deal_types + deal_custom
        "deal_types":  base_codes,
        "deal_custom": item.deal_custom,
        "situation":   item.situation,
        "style":       item.style,
    }
    await state.update_data(**payload_dict)
    await ui_reply(callback, _summary_text(payload_dict), kb_summary(), state=state)
    await state.set_state(FeedbackStates.showing_summary)
    await callback.answer()


async def history_back(callback: CallbackQuery, state: FSMContext):
    await open_history(callback, state)


# =============================================================================
# Navigation
# =============================================================================
async def go_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await ui_reply(callback, MAIN_MENU_TITLE, kb_menu_main(), state=state)
    await callback.answer()


# =============================================================================
# Router
# =============================================================================
def router(rt: Router):
    # start & cancel & menu
    rt.callback_query.register(start_feedback_flow, F.data == "nav.feedback_start")
    rt.callback_query.register(go_menu, F.data == "nav.menu")

    # company skip / address skip
    rt.callback_query.register(handle_company_skip, F.data == "company.skip")
    rt.callback_query.register(handle_address_skip, F.data == "addr.skip")

    # city selection hub
    rt.callback_query.register(handle_city_choice, F.data.startswith("loc.city"))
    rt.callback_query.register(handle_city_choice, F.data == "loc.addr")
    rt.callback_query.register(handle_city_choice, F.data == "loc.next")

    # deal type (мультивыбор/чекбоксы)
    rt.callback_query.register(handle_deal_type, F.data.startswith("deal."))

    # situation hints
    rt.callback_query.register(handle_situation_hints, F.data == "sit.hints")

    # tone & length selection
    rt.callback_query.register(handle_tone, F.data.startswith("tone."))
    rt.callback_query.register(handle_length, F.data.startswith("length."))
    # Алиас на случай старых кнопок (если где-то остались): style.* -> перенаправим в tone.*
    rt.callback_query.register(
        lambda cq, **_: None,  # no-op, чтобы не падать
        F.data.startswith("style.")
    )

    # edit menu
    rt.callback_query.register(open_edit_menu, F.data == "edit.open")
    rt.callback_query.register(edit_field_router, F.data.startswith("edit."))

    # generation
    rt.callback_query.register(start_generation, F.data == "gen.start")

    # mutations & pick & more
    rt.callback_query.register(mutate_variant, F.data.startswith("mutate."))
    rt.callback_query.register(gen_more_variant, F.data.startswith("gen.more."))
    rt.callback_query.register(view_variant, F.data.startswith("view."))
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

    # TEXT INPUTS — обязательно через StateFilter(...)
    rt.message.register(handle_client_name, StateFilter(FeedbackStates.waiting_client), F.text)
    rt.message.register(handle_agent_name, StateFilter(FeedbackStates.waiting_agent), F.text)
    rt.message.register(handle_company_name, StateFilter(FeedbackStates.waiting_company), F.text)
    rt.message.register(handle_city_input, StateFilter(FeedbackStates.waiting_city_input), F.text)
    rt.message.register(handle_address, StateFilter(FeedbackStates.waiting_address), F.text)
    rt.message.register(handle_deal_custom, StateFilter(FeedbackStates.waiting_deal_custom), F.text)
    rt.message.register(handle_situation, StateFilter(FeedbackStates.waiting_situation), F.text)