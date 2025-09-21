# smart_agent/bot/widgets/calendar_picker.py
"""
Инлайн-календарь для выбора даты по callback-кнопкам.

Принципы:
- Самостоятельно обрабатывает ТОЛЬКО навигацию по месяцам (cal.nav:...) и тех.клики (cal.ignore).
- Кнопки дней отдают callback в формате: cal.date:YYYY-MM-DD (их ловит внешний код).
- Показывает счётчик постов в каждом дне: "1(2)" — 1 число, (2) поста.
- Открывается на переданной base_date.
- Без якорей: если это callback — редактируем сообщение, если нет — отправляем новое.

Подключение:
    from bot.widgets.calendar_picker import open_calendar, router as calendar_router
    ...
    calendar_router(rt)  # в сборщике роутов

Открыть календарь:
    await open_calendar(message_or_callback.message, date.today())

Ловить выбор:
    rt.callback_query.register(on_pick, F.data.startswith("cal.date:"))
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import calendar as pycal
from typing import Dict, Optional, Callable

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest

# опционально используем adb для счётчиков, если есть подходящая функция
try:
    import bot.utils.admin_db as adb  # type: ignore
except Exception:  # pragma: no cover
    adb = None  # noqa

CB_PREFIX = "cal"
WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
MONTHS_RU = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]

# ───────────────────────── helpers ─────────────────────────

def _month_bounds(y: int, m: int) -> tuple[date, date]:
    first = date(y, m, 1)
    if m == 12:
        last = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(y, m + 1, 1) - timedelta(days=1)
    return first, last

def _get_counts_map(month_first: date, month_last: date) -> Dict[str, int]:
    """
    Пытаемся получить словарь {'YYYY-MM-DD': count} для дней месяца.
    Ищем функцию в adb:
        - get_mailing_counts_map(start_iso, end_iso) -> dict[str,int]
    Если нет — вернём пустой словарь (счётчики будут 0).
    """
    try:
        if adb is None:
            return {}
        func = getattr(adb, "get_mailing_counts_map", None)
        if callable(func):
            return func(month_first.isoformat(), month_last.isoformat()) or {}
    except Exception:
        pass
    return {}

def _fmt_day_button(d: date, selected: Optional[date], today: date, counts: int) -> str:
    """
    Текст кнопки дня:
      - "1"             (нет постов)
      - "1(2)"          (2 поста)
      - выделение выбранного: "[1]" или "[1(2)]"
      - отметка сегодня: "•1" / "•1(2)"
    """
    base = f"{d.day}"
    if counts > 0:
        base += f"({counts})"
    if selected and d == selected:
        base = f"[{base}]"
    elif d == today:
        base = f"•{base}"
    return base

def _safe_edit(msg: Message, *, text: str, kb: InlineKeyboardMarkup) -> None:
    """
    Если msg пришло из callback — пробуем редактировать.
    Если падает — отправляем новое.
    """
    async def _do():
        try:
            await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                try:
                    await msg.edit_reply_markup(reply_markup=kb)
                    return
                except TelegramBadRequest:
                    pass
            await msg.answer(text, reply_markup=kb, parse_mode="HTML")
    return _do()

# ───────────────────────── rendering ─────────────────────────

def _build_month_markup(y: int, m: int, selected: Optional[date] = None) -> InlineKeyboardMarkup:
    month_first, month_last = _month_bounds(y, m)
    counts_map = _get_counts_map(month_first, month_last)

    cal = pycal.Calendar(firstweekday=0)  # Monday=0
    weeks = cal.monthdatescalendar(y, m)
    today = date.today()

    header = [
        InlineKeyboardButton(text="◀", callback_data=f"{CB_PREFIX}.nav:{y}-{m:02d}-01|dir=prev|sel={selected.isoformat() if selected else ''}"),
        InlineKeyboardButton(text=f"{MONTHS_RU[m-1]} {y}", callback_data=f"{CB_PREFIX}.ignore"),
        InlineKeyboardButton(text="▶", callback_data=f"{CB_PREFIX}.nav:{y}-{m:02d}-01|dir=next|sel={selected.isoformat() if selected else ''}"),
    ]

    wd = [InlineKeyboardButton(text=w, callback_data=f"{CB_PREFIX}.ignore") for w in WEEKDAYS_RU]

    rows = [header, wd]
    for week in weeks:
        row = []
        for d in week:
            if d.month != m:
                # дни соседних месяцев — пустышки
                row.append(InlineKeyboardButton(text=" ", callback_data=f"{CB_PREFIX}.ignore"))
                continue
            cnt = counts_map.get(d.isoformat(), 0)
            text = _fmt_day_button(d, selected, today, cnt)
            row.append(
                InlineKeyboardButton(text=text, callback_data=f"{CB_PREFIX}.date:{d.isoformat()}")
            )
        rows.append(row)

    # нижняя строчка
    today_cb = f"{CB_PREFIX}.nav:{today.year}-{today.month:02d}-01|dir=stay|sel={today.isoformat()}"
    rows.append([
        InlineKeyboardButton(text="Сегодня", callback_data=today_cb),
        InlineKeyboardButton(text=" ", callback_data=f"{CB_PREFIX}.ignore"),
        InlineKeyboardButton(text=" ", callback_data=f"{CB_PREFIX}.ignore"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

# ───────────────────────── public API ─────────────────────────

async def open_calendar(msg: Message, base: date, selected: Optional[date] = None) -> None:
    """
    Открыть (или перерисовать) календарь.
    - msg: message, над которым работаем
    - base: дата, месяц которой показываем
    - selected: дата, которую подсветить (по умолчанию base)
    """
    selected = selected or base
    kb = _build_month_markup(base.year, base.month, selected)
    text = "Выберите дату:"
    await _safe_edit(msg, text=text, kb=kb)

# ───────────────────────── handlers ─────────────────────────

async def _on_nav(callback: CallbackQuery):
    """
    cal.nav:YYYY-MM-01|dir=prev|sel=YYYY-MM-DD
    """
    payload = callback.data.split(":", 1)[1]
    left, right = payload.split("|", 1)
    y, m, _ = left.split("-")
    params = dict(kv.split("=", 1) for kv in right.split("|") if "=" in kv)

    y = int(y)
    m = int(m)
    dir_ = params.get("dir", "stay")
    sel_str = params.get("sel") or ""
    selected = None
    if sel_str:
        try:
            selected = datetime.strptime(sel_str, "%Y-%m-%d").date()
        except Exception:
            selected = None

    if dir_ == "prev":
        if m == 1:
            y -= 1
            m = 12
        else:
            m -= 1
    elif dir_ == "next":
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1
    # stay -> оставляем как есть

    kb = _build_month_markup(y, m, selected)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        # если вдруг не получилось — перерисуем целиком
        await callback.message.edit_text("Выберите дату:", reply_markup=kb)
    await callback.answer()

async def _on_ignore(callback: CallbackQuery):
    await callback.answer()

def router(rt: Router):
    """
    Регистрируем только навигацию календаря.
    День (cal.date:YYYY-MM-DD) ловит внешний код.
    """
    rt.callback_query.register(_on_nav, F.data.startswith(f"{CB_PREFIX}.nav:"))
    rt.callback_query.register(_on_ignore, F.data.startswith(f"{CB_PREFIX}.ignore"))
