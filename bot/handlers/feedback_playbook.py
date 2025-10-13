# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\feedback_playbook.py
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
import uuid
from typing import Any, Dict, List, Optional, Union

import aiohttp
from aiogram import F, Bot, Router
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import *
from pathlib import Path
from bot.config import EXECUTOR_BASE_URL, get_file_path
from bot.utils.database import history_add, history_get, history_list_cases, history_get_case_variants
from bot.states.states import FeedbackStates
from bot.utils.redis_repo import feedback_repo
import bot.utils.logging_config as logging_config

# module logger
log = logging_config.logger

# =============================================================================
# Доступ / подписка
# =============================================================================
import bot.utils.database as app_db          # триал / история / согласия
import bot.utils.billing_db as billing_db     # карты / подписки / платежный лог
from bot.utils.database import (
    is_trial_active, trial_remaining_hours
)

@dataclass
class ReviewPayload:
    client_name: str
    agent_name: str
    company: Optional[str] = None
    city: Optional[str] = None
    address: Optional[str] = None
    deal_type: Optional[str] = None  # CSV: sale|buy|rent|lease|custom
    deal_custom: Optional[str] = None
    situation: Optional[str] = None
    style: Optional[str] = None  # friendly|neutral|formal

def _is_sub_active(user_id: int) -> bool:
    return bool(billing_db.has_saved_card(user_id))

def _format_access_text(user_id: int) -> str:
    trial_hours = trial_remaining_hours(user_id)
    # при активном триале показываем точную дату окончания
    if is_trial_active(user_id):
        try:
            until_dt = app_db.get_trial_until(user_id)
            if until_dt:
                return f'🆓 Бесплатный доступ активен до *{until_dt.date().isoformat()}* (~{trial_hours} ч.)'
        except Exception:
            pass
        return f'🆓 Бесплатный доступ активен ещё *~{trial_hours} ч.*'
    # нет триала — проверяем рекуррентную подписку (карта сохранена)
    if _is_sub_active(user_id):
        return '✅ Подписка активна (автопродление включено)'
    return '😢 Доступ закончился. Оформи подписку, чтобы продолжить.'

def _has_access(user_id: int) -> bool:
    return bool(is_trial_active(user_id) or _is_sub_active(user_id))

# Тексты и кнопка «Оформить подписку» (как в plans)
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

# Доп. форматтер стартового текста «Отзывы» с информацией о доступе
def _feedback_home_text(user_id: int) -> str:
    return f"{MAIN_MENU_TITLE}\n\n{_format_access_text(user_id)}"

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
ASK_CITY_INPUT = "Введите город (например: Москва / Питер )."
ASK_ADDRESS = "Уточните адрес (улица, дом) или пропустите."
ASK_DEAL_TYPE = "Выберите тип сделки."
ASK_DEAL_CUSTOM = "Уточните тип сделки (свободный ввод)."
ASK_SITUATION = (
    "Опишите ситуацию: что делали, сроки, сложности, итог.\n"
    "Минимум 50 символов."
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
BTN_BACK = "⬅️ Назад"
BTN_NEXT = "Дальше"
BTN_SKIP = "Пропустить"
GENERATING = "⏳ Генерирую…"
ERROR_TEXT = "Не удалось получить текст от сервиса. Попробуйте еще раз."
READY_FINAL = (
    "Черновик готов. Отправьте клиенту и опубликуйте при согласии.\n"
    "Сохранено в истории."
)
HISTORY_EMPTY = "История пуста. Сгенерируйте первый черновик."
RECENT_DRAFTS_TITLE = "Недавние черновики:"
MAIN_MENU_TITLE = ('''
Помоги клиенту ярко рассказать о твоей работе. 
🎯 Жми «Создать отзыв»
'''
)
RETURN_TO_VARIANTS = "Вернитесь к вариантам выше или запросите ещё один."
VARIANT_HEAD = "Вариант {idx}\n\n"
VARIANT_HEAD_UPDATED = "Вариант {idx} (обновлён)\n\n"

DEFAULT_CITIES = ["Москва", "Санкт-Петербург"]

# лимит длины inline-запроса в Telegram (приблизительно)
INLINE_QUERY_MAXLEN = 256

# длины
LENGTH_CHOICES = [("short", "Короткий ≤250"), ("medium", "Средний ≤450"), ("long", "Развернутый ≤1200")]
LENGTH_LIMITS = {"short": 250, "medium": 450, "long": 1200}
def _length_limit(code: Optional[str]) -> Optional[int]:
    return LENGTH_LIMITS.get(code or "")

# Точки «прицеливания» для мутаций: к какому объёму стремиться
# Короткий — чуть ниже порога, Средний — середина диапазона, Длинный — заметно длиннее среднего
LENGTH_TARGETS = {"short": 220, "medium": 380, "long": 900}
def _length_target(code: Optional[str]) -> Optional[int]:
    return LENGTH_TARGETS.get(code or "")

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
    ("maternity_capital", "Мат. капитал)")
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
        inline_keyboard=[[InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")]]
    )


def kb_with_skip(skip_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BTN_SKIP, callback_data=skip_cb)],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")],
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
    rows.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")])
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
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")],
        ]
    )


def kb_deal_types_ms(d: Dict[str, Any]) -> InlineKeyboardMarkup:
    """
    Мультивыбор типов сделки с чекбоксами.
    Кнопки:
      - deal.toggle.<code> — переключить чекбокс
      - deal.custom — ввести/изменить «Другое…»
      - deal.custom.clear — очистить «Другое»
      - deal.clear — снять все галочки
      - deal.next — продолжить
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
    rows.append([InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")])
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
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")],
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
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_summary() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=BTN_GENERATE, callback_data="gen.start")],
        [InlineKeyboardButton(text=BTN_EDIT, callback_data="edit.open")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")],
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
    # Изменение длины теперь через одно действие с выбором целевого размера
    rows.append([InlineKeyboardButton(text="Изменить длину", callback_data=f"mutate.{index}.length")])
    rows.append([InlineKeyboardButton(text="Изменить тон", callback_data=f"mutate.{index}.style")])
    rows.append([InlineKeyboardButton(text="Экспорт .txt", callback_data=f"export.{index}.txt")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="nav.menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_variants_common() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Показать все поля", callback_data="summary.show")],
        [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_final() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="Экспорт .txt", callback_data="export.final.txt"),
        ],
        [InlineKeyboardButton(text="Создать похожий", callback_data="clone.from.final")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="nav.menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_history(items: List[Any]) -> InlineKeyboardMarkup:
    """
    Одна кнопка = один кейс (case_id).
    Для записей без case_id оставляем старую модель (одна запись = одна кнопка).
    """
    rows: List[List[InlineKeyboardButton]] = []
    for it in items[:10]:
        label = f"#{it.id} · {it.created_at.strftime('%Y-%m-%d %H:%M')} · {it.city or '—'} · {it.deal_type or '—'}"
        if it.case_id:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"hist.case.{it.case_id}")])
        else:
            rows.append([InlineKeyboardButton(text=label, callback_data=f"hist.open.{it.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="nav.menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_menu_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Создать отзыв", callback_data="nav.feedback_start")],
            [InlineKeyboardButton(text="История", callback_data="hist.open")],
            [InlineKeyboardButton(text=BTN_BACK, callback_data="nav.ai_tools")],
        ]
    )

# =============================================================================
# Media helpers (edit current message to photo or send new)
# =============================================================================
async def _edit_or_replace_with_photo(
    *, bot: Bot, msg: Message, photo_path: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    """
    Пытаемся заменить текущее сообщение на фото с подписью.
    Если нельзя редактировать (был текст/другая медиа) — удаляем и отправляем новое фото.
    """
    try:
        media = InputMediaPhoto(media=FSInputFile(photo_path), caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        # сообщение было не-фото → отправим новое
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(photo_path), caption=caption, reply_markup=kb)


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
            [InlineKeyboardButton(text="Продолжить редактирование",
                                  switch_inline_query_current_chat=_inline_prefill_text(draft))],
            [InlineKeyboardButton(text=BTN_CANCEL, callback_data="nav.feedback_home")],
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
    lines = [SUMMARY_TITLE, "", f"• Клиент: {d.get('client_name')}"]
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


def _state_from_history_item(item) -> Dict[str, Any]:
    """
    Готовит словарь для state из одной записи истории ReviewHistory (как в history_clone).
    """
    codes = [c for c in (item.deal_type or "").split(",") if c]
    base_codes = [c for c in codes if c != "custom"]
    return {
        "client_name": item.client_name,
        "agent_name":  item.agent_name,
        "company":     item.company,
        "city":        item.city,
        "address":     item.address,
        "deal_types":  base_codes,
        "deal_custom": item.deal_custom,
        "situation":   item.situation,
        # в state ключ «tone» (совместим с style)
        "tone":        item.style,
    }


# =============================================================================
# Flow handlers
# =============================================================================
async def start_feedback_flow(callback: CallbackQuery, state: FSMContext):
    # Проверка доступа как в plans: без подписки не пускаем в сценарий
    user_id = callback.message.chat.id
    if not _has_access(user_id):
        text = SUB_FREE if not _is_sub_active(user_id) else SUB_PAY
        await ui_reply(callback, text, SUBSCRIBE_KB, state=state)
        await callback.answer()
        return

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


# (cancel_flow больше не нужен — выходим через глобальный nav.feedback_home)


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
    if len(txt) < 50 or len(txt) > 4000:
        await state.update_data(situation_draft=txt)
        await feedback_repo.set_fields(message.from_user.id, {"status": "validation_error", "situation_draft": txt})
        await message.answer(
            "Нужно 50–4000 символов. Добавьте деталей (сроки, результат, особенности).",
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
                # сохраняем текущую целевую длину, чтобы стиль не «схлопывал» текст в medium
                cur_len = (await state.get_data()).get("length")
                return await _request_mutate(
                    base_text, operation="style", style=tone, payload=payload, length=cur_len
                )
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

    d = await state.get_data()

    # === Режим «изменить длину» для конкретного варианта ===
    mut_idx = d.get("mutating_length_idx")
    if mut_idx:
        # ранний ACK, чтобы не протух callback
        await _safe_cb_answer(callback)
        variants: List[str] = d.get("variants", [])
        if not (1 <= mut_idx <= len(variants)):
            await callback.answer("Не найден вариант.")
            return None

        base_text = variants[mut_idx - 1]
        cur_len = len(base_text or "")

        # Явное соответствие выбору пользователя
        target = length_code
        target_hint = _length_target(target) or _length_limit(target)

        # Для medium выбираем направление к целевой точке (около 380) с толерансом в 30 символов
        op: Optional[str]
        if target == "short":
            op = "short"
        elif target == "long":
            op = "long"
        else:  # target == "medium"
            medium_target = _length_target("medium") or 380
            if abs(cur_len - medium_target) <= 30:
                op = None  # уже близко — не трогаем
            else:
                op = "long" if cur_len < medium_target else "short"

        # Обновим глобальное пожелание длины — пригодится для дальнейших генераций/догенераций
        await state.update_data(length=length_code)
        await feedback_repo.set_fields(callback.from_user.id, {"length": length_code})

        if op is None:
            # Ничего менять не требуется — оставим как есть, без «обновлён»
            await state.update_data(mutating_length_idx=None, viewer_idx=mut_idx)
            await feedback_repo.set_fields(callback.from_user.id, {"viewer_idx": mut_idx})
            return None

        await ui_reply(callback, GENERATING, state=state)
        chat_id = callback.message.chat.id
        async def _do():
            payload = _payload_from_state(await state.get_data())
            return await _request_mutate(
                base_text,
                operation=op,
                style=None,
                payload=payload,
                length=target,           # <-- важно! (short|medium|long)
                length_hint=target_hint, # (опц.) бэко-совместимость
            )
        try:
            new_text: str = await run_long_operation_with_action(
                bot=callback.bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do()
            )
            variants[mut_idx - 1] = new_text
            await state.update_data(variants=variants, mutating_length_idx=None)
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
        return None

    # === Базовый сценарий выбора длины в основном флоу ===
    await state.update_data(length=length_code)
    await feedback_repo.set_fields(callback.from_user.id, {"length": length_code})
    d = await state.get_data()
    await ui_reply(callback, _summary_text(d), kb_summary(), state=state)
    await state.set_state(FeedbackStates.showing_summary)
    await callback.answer()
    await feedback_repo.set_stage(callback.from_user.id, "summary")
    return None


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

    # Повторная проверка доступа перед обращением к сервису (как в plans.handle_style_plan)
    user_id = callback.message.chat.id
    if not _has_access(user_id):
        text = SUB_FREE if not _is_sub_active(user_id) else SUB_PAY
        await ui_reply(callback, text, SUBSCRIBE_KB, state=state)
        await state.clear()
        return

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
        # передаём категорию длины (short|medium|long) — это понимает исполнитель
        length_code = (await state.get_data()).get("length")
        length_hint = _length_limit(length_code)  # оставим для обратной совместимости
        # Redis: сохраняем запрос перед отправкой
        await feedback_repo.set_fields(
            callback.from_user.id,
            {"status": "generating", "payload": asdict(payload), "length_hint": length_hint},
        )
        return await _request_generate(
            payload,
            num_variants=3,
            length=length_code,         # <-- важно!
            length_hint=length_hint     # (опц.) бэко-совместимость
        )

    try:
        variants: List[str] = await run_long_operation_with_action(
            bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do()
        )
        await state.update_data(variants=variants)
        await feedback_repo.set_fields(callback.from_user.id, {"status": "variants_ready", "variants_json": variants})

        # Присваиваем case_id (единый для набора вариантов) и автосохраняем все варианты в историю
        d = await state.get_data()
        case_id = d.get("case_id") or uuid.uuid4().hex
        await state.update_data(case_id=case_id)
        await feedback_repo.set_fields(callback.from_user.id, {"case_id": case_id})
        
        # Сохраняем каждый вариант как элемент коллекции кейса
        for v in variants:
            try:
                history_add(callback.from_user.id, asdict(payload), v, case_id=case_id)
            except Exception as e:
                log.exception("history_add failed for case %s: %s", case_id, e)

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

    # Защита на мутациях: если подписка слетела — не даём продолжить
    user_id = callback.message.chat.id
    if not _has_access(user_id):
        text = SUB_FREE if not _is_sub_active(user_id) else SUB_PAY
        await ui_reply(callback, text, SUBSCRIBE_KB, state=state)
        await state.clear()
        return

    data = callback.data  # mutate.{index}.short|long|style|length
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

    if op == "length":
        # Режим изменения длины выбранного варианта:
        # открываем такой же набор размеров, как в основном флоу.
        await state.update_data(mutating_length_idx=idx)
        await ui_reply(callback, ASK_LENGTH, kb_length(), state=state)
        await state.set_state(FeedbackStates.waiting_length)
        await _safe_cb_answer(callback)
        return

    # Back-compat для старых сообщений: прямые команды short/long
    operation = "short" if op == "short" else "long"

    await ui_reply(callback, GENERATING, state=state)  # редактируем якорь
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

    # Проверка доступа для «Ещё вариант»
    user_id = callback.message.chat.id
    if not _has_access(user_id):
        text = SUB_FREE if not _is_sub_active(user_id) else SUB_PAY
        await ui_reply(callback, text, SUBSCRIBE_KB, state=state)
        await state.clear()
        return

    d = await state.get_data()
    payload = _payload_from_state(d)

    await ui_reply(callback, GENERATING, state=state)
    await feedback_repo.set_fields(callback.from_user.id, {"status": "generating_more"})
    chat_id = callback.message.chat.id

    async def _do():
        length_code = (await state.get_data()).get("length")
        length_hint = _length_limit(length_code)
        lst = await _request_generate(
            payload,
            num_variants=1,
            length=length_code,         # <-- важно!
            length_hint=length_hint
        )
        return lst[0]

    try:
        new_text: str = await run_long_operation_with_action(
            bot=bot, chat_id=chat_id, action=ChatAction.TYPING, coro=_do()
        )
        variants: List[str] = d.get("variants", [])
        variants.append(new_text)
        await state.update_data(variants=variants)
        await feedback_repo.set_fields(callback.from_user.id, {"status": "variants_ready", "variants_json": variants})
        
        # Автосохранение нового варианта в тот же кейс (case_id)
        case_id = d.get("case_id")
        if not case_id:
            case_id = uuid.uuid4().hex
            await state.update_data(case_id=case_id)
            await feedback_repo.set_fields(callback.from_user.id, {"case_id": case_id})
        try:
            history_add(callback.from_user.id, asdict(payload), new_text, case_id=case_id)
        except Exception as e:
            log.exception("history_add (more) failed for case %s: %s", case_id, e)
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

    # Автосохранение уже было выполнено при генерации/дозапросе —
    # здесь не дублируем запись, чтобы не плодить копии

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
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    # 1) пробуем по user_id; 2) бэко-совместимость: если пусто — пробуем по chat_id
    items = history_list_cases(user_id, limit=10)
    if (not items or len(items) == 0) and chat_id != user_id:
        items = history_list_cases(chat_id, limit=10)
    
    if not items or len(items) == 0:
        await ui_reply(
            callback,
            HISTORY_EMPTY,
            InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️Назад", callback_data="nav.menu")]]
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

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    item = history_get(user_id, item_id)
    # бэко-совместимость: возможно запись лежит под chat_id
    if not item and chat_id != user_id:
        item = history_get(chat_id, item_id)
    
    if not item:
        await callback.answer("Не найдено.")
        return

    # Открываем одиночную запись без case_id тем же интерфейсом вариантов (список из 1 элемента)
    prefill = _state_from_history_item(item)
    await state.update_data(**prefill, variants=[item.final_text], case_id=item.case_id, viewer_idx=1, picked_idx=None)
    parts = _split_for_telegram(item.final_text)
    head = VARIANT_HEAD.format(idx=1) + parts[0]
    await ui_reply(callback, head, kb_variant(1, 1), state=state)
    for p in parts[1:]:
        await send_text(callback.message, p)
    await state.set_state(FeedbackStates.browsing_variants)
    await _safe_cb_answer(callback)


async def history_open_case(callback: CallbackQuery, state: FSMContext, bot: Optional[Bot] = None):
    # hist.case.{case_id}
    try:
        _, _, case_id = callback.data.split(".", 2)
    except Exception:
        await callback.answer()
        return

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    items = history_get_case_variants(user_id, case_id)
    if (not items or len(items) == 0) and chat_id != user_id:
        items = history_get_case_variants(chat_id, case_id)
    if not items:
        await callback.answer("Не найдено.")
        return

    # Подготавливаем state из первой записи кейса и показываем первый вариант с общей клавиатурой
    first = items[0]
    prefill = _state_from_history_item(first)
    variants = [it.final_text for it in items]
    await state.update_data(**prefill, variants=variants, case_id=first.case_id, viewer_idx=1, picked_idx=None)

    parts = _split_for_telegram(variants[0])
    head = VARIANT_HEAD.format(idx=1) + parts[0]
    total = len(variants)
    await ui_reply(callback, head, kb_variant(1, total), state=state, bot=bot or callback.bot)
    for p in parts[1:]:
        await send_text(callback.message, p)
    await state.set_state(FeedbackStates.browsing_variants)
    await _safe_cb_answer(callback)





async def history_clone(callback: CallbackQuery, state: FSMContext):
    # hist.{id}.clone → prefill and go to summary
    try:
        _, id_str, _ = callback.data.split(".")
        item_id = int(id_str)
    except Exception:
        await callback.answer()
        return
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    item = history_get(user_id, item_id)
    if not item and chat_id != user_id:
        item = history_get(chat_id, item_id)
    
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
# Navigation (внутреннее меню «Отзывы»)
# =============================================================================
async def go_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    # Картинка-обложка меню «Отзывы»
    rel = "data/img/bot/feed_back.png"  # можно и "img/bot/feed_back.png" — get_file_path поймёт оба варианта
    path = get_file_path(rel)
    bot = callback.bot
    user_id = callback.message.chat.id
    caption = _feedback_home_text(user_id)
    try:
        if Path(path).exists():
            await _edit_or_replace_with_photo(
                bot=bot,
                msg=callback.message,
                photo_path=path,
                caption=caption,
                kb=kb_menu_main(),
            )
        else:
            log.warning("Menu image not found: %s (resolved from %s)", path, rel)
            # Фолбэк на текст, если файла нет
            await ui_reply(callback, caption, kb_menu_main(), state=state)
    except Exception as e:
        log.exception("Failed to display menu image: %s", e)
        await ui_reply(callback, caption, kb_menu_main(), state=state)
    finally:
        # Безопасный ACK
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass


# =============================================================================
# Router
# =============================================================================
def router(rt: Router) -> None:

    # start & cancel & menu
    rt.callback_query.register(start_feedback_flow, F.data == "nav.feedback_start")
    rt.callback_query.register(go_menu, F.data == "nav.menu")
    # Также можно повесить отдельный вход в модуль «Отзывы»:
    rt.callback_query.register(go_menu, F.data == "nav.feedback_home")

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

    # mutations & view
    rt.callback_query.register(mutate_variant, F.data.startswith("mutate."))
    rt.callback_query.register(view_variant, F.data.startswith("view."))

    # history
    rt.callback_query.register(open_history, F.data == "hist.open")
    rt.callback_query.register(history_open_item, F.data.startswith("hist.open."))
    rt.callback_query.register(history_open_case, F.data.startswith("hist.case."))
    # rt.callback_query.register(history_export, F.data.contains(".export."))
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