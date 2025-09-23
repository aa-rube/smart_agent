#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\calendar_picker.py

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

# Шаг выбора минут
MINUTE_STEP = 5  # 5 минут

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
            # пробуем новую сигнатуру (с only_pending), если её нет — откатываемся к старой
            try:
                return func(month_first.isoformat(), month_last.isoformat(), only_pending=True) or {}
            except TypeError:
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

    # нижняя строчка (спец-режим dir=today гарантированно прыгает в текущий месяц/день)
    today_cb = f"{CB_PREFIX}.nav:{today.year}-{today.month:02d}-01|dir=today|sel={today.isoformat()}"
    rows.append([
        InlineKeyboardButton(text="Сегодня", callback_data=today_cb),
        InlineKeyboardButton(text=" ", callback_data=f"{CB_PREFIX}.ignore"),
        InlineKeyboardButton(text=" ", callback_data=f"{CB_PREFIX}.ignore"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- TIME PICKER UI ----------
def _build_hours_markup(d: date, selected_hour: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    Сетка часов 00–23 (24-часовой формат), 6 колонок × 4 строки.
    Клик по часу переводит на выбор минут.
    """
    rows = []
    header = [
        InlineKeyboardButton(text=f"{d.strftime('%d.%m.%Y')}: выберите час", callback_data=f"{CB_PREFIX}.ignore")
    ]
    rows.append(header)

    def _btn_text(h: int) -> str:
        t = f"{h:02d}"
        return f"[{t}]" if (selected_hour is not None and h == selected_hour) else t

    row: list[InlineKeyboardButton] = []
    for h in range(24):
        row.append(InlineKeyboardButton(text=_btn_text(h), callback_data=f"{CB_PREFIX}.hour:{d.isoformat()}|h={h:02d}"))
        if (h + 1) % 6 == 0:
            rows.append(row)
            row = []
    if row:
        # добиваем пустышками до 6 колонок
        while len(row) < 6:
            row.append(InlineKeyboardButton(text=" ", callback_data=f"{CB_PREFIX}.ignore"))
        rows.append(row)

    # низ
    rows.append([
        InlineKeyboardButton(text="↩︎ К выбору даты", callback_data=f"{CB_PREFIX}.time.back:{d.isoformat()}"),
        InlineKeyboardButton(text="🕓 Оставить текущее", callback_data=f"{CB_PREFIX}.keep:{d.isoformat()}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_minutes_markup(d: date, h: int, selected_minute: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    Выбор минут с шагом 5: две строки по 6 кнопок: 00..25 / 30..55.
    Есть «Изменить час» и «Готово» (кнопка генерит cal.done:YYYY-MM-DDTHH:MM).
    """
    rows = []
    header = [
        InlineKeyboardButton(text=f"{d.strftime('%d.%m.%Y')} — {h:02d}:__", callback_data=f"{CB_PREFIX}.ignore")
    ]
    rows.append(header)

    def _btn_text(m: int) -> str:
        t = f"{m:02d}"
        return f"[{t}]" if (selected_minute is not None and m == selected_minute) else t

    # 00..25
    r1 = [InlineKeyboardButton(text=_btn_text(m), callback_data=f"{CB_PREFIX}.min:{d.isoformat()}|h={h:02d}|m={m:02d}")
          for m in range(0, 30, MINUTE_STEP)]
    # 30..55
    r2 = [InlineKeyboardButton(text=_btn_text(m), callback_data=f"{CB_PREFIX}.min:{d.isoformat()}|h={h:02d}|m={m:02d}")
          for m in range(30, 60, MINUTE_STEP)]

    rows.append(r1)
    rows.append(r2)

    # Низ: назад к часам / Готово
    # Кнопка «Готово» активируется только после выбора минуты (иначе — заглушка)
    if selected_minute is not None:
        done_cb = f"{CB_PREFIX}.done:{d.isoformat()}T{h:02d}:{selected_minute:02d}"
        done_btn = InlineKeyboardButton(text=f"✅ Готово: {h:02d}:{selected_minute:02d}", callback_data=done_cb)
    else:
        done_btn = InlineKeyboardButton(text="✅ Готово", callback_data=f"{CB_PREFIX}.ignore")

    rows.append([
        InlineKeyboardButton(text="⏪ Изменить час", callback_data=f"{CB_PREFIX}.hour.back:{d.isoformat()}"),
        done_btn,
        InlineKeyboardButton(text="🕓 Оставить текущее", callback_data=f"{CB_PREFIX}.keep:{d.isoformat()}"),
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

async def open_time_picker(msg: Message, d: date, hour: Optional[int] = None, minute: Optional[int] = None) -> None:
    """
    Открыть выбор времени для выбранной даты.
    Если hour не задан — показываем сетку часов 00–23.
    Если hour задан — показываем сетку минут (шаг 5) и кнопку «Готово».

    ⚠️ После клика по «Готово» прилетит callback с data:
       cal.done:YYYY-MM-DDTHH:MM  — ловите его во внешнем коде.
    """
    if hour is None:
        kb = _build_hours_markup(d, None)
        text = "Выберите время:\n<b>Шаг минут — 5</b>"
        await _safe_edit(msg, text=text, kb=kb)
        return

    kb = _build_minutes_markup(d, hour, selected_minute=minute)
    text = f"Выберите минуты для <b>{d.strftime('%d.%m.%Y')} {hour:02d}:__</b>\n<b>Шаг минут — 5</b>"
    await _safe_edit(msg, text=text, kb=kb)

# ───────────────────────── handlers ─────────────────────────

async def _on_date(callback: CallbackQuery):
    """
    cal.date:YYYY-MM-DD  -> после выбора дня сразу открываем выбор времени.
    """
    date_str = callback.data.split(":", 1)[1]
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        await callback.answer()
        return
    # показываем сетку часов (далее — минуты)
    await open_time_picker(callback.message, d)
    await callback.answer()

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
    elif dir_ == "today":
        # Жёстко прыгаем в текущий месяц и выделяем сегодняшний день
        t = date.today()
        y, m = t.year, t.month
        selected = t
    # stay -> оставляем как есть

    kb = _build_month_markup(y, m, selected)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest as e:
        # Если разметка не изменилась — это нормально (например, уже на текущем месяце)
        low = str(e).lower()
        if "message is not modified" in low:
            # Попробуем легонько «пошевелить» текст невидимым символом, чтобы Телеграм принял апдейт
            try:
                await callback.message.edit_text("Выберите дату:\u2060", reply_markup=kb)
            except TelegramBadRequest:
                # Совсем без изменений — ок, просто молча подтверждаем клик
                pass
        else:
            # Другая ошибка — попробуем перерисовать целиком
            try:
                await callback.message.edit_text("Выберите дату:", reply_markup=kb)
            except TelegramBadRequest:
                pass
    await callback.answer()

async def _on_ignore(callback: CallbackQuery):
    await callback.answer()

async def _on_pick_hour(callback: CallbackQuery):
    """
    cal.hour:YYYY-MM-DD|h=HH  -> открываем выбор минут.
    """
    payload = callback.data.split(":", 1)[1]
    date_str, rest = payload.split("|", 1)
    params = dict(kv.split("=", 1) for kv in rest.split("|") if "=" in kv)
    h = int(params.get("h", "0"))
    d = datetime.strptime(date_str, "%Y-%m-%d").date()

    kb = _build_minutes_markup(d, h, selected_minute=None)
    try:
        await callback.message.edit_text(
            f"Выберите минуты для <b>{d.strftime('%d.%m.%Y')} {h:02d}:__</b>\n<b>Шаг минут — 5</b>",
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

async def _on_pick_min(callback: CallbackQuery):
    """
    cal.min:YYYY-MM-DD|h=HH|m=MM  -> подсветить минуту и показать «Готово».
    """
    payload = callback.data.split(":", 1)[1]
    date_str, rest = payload.split("|", 1)
    params = dict(kv.split("=", 1) for kv in rest.split("|") if "=" in kv)
    h = int(params.get("h", "0"))
    m = int(params.get("m", "0"))
    d = datetime.strptime(date_str, "%Y-%m-%d").date()

    kb = _build_minutes_markup(d, h, selected_minute=m)
    try:
        await callback.message.edit_text(
            f"Выберите минуты для <b>{d.strftime('%d.%m.%Y')} {h:02d}:{m:02d}</b>\n<b>Шаг минут — 5</b>",
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

async def _on_time_back(callback: CallbackQuery):
    """
    cal.time.back:YYYY-MM-DD -> вернуться к месячному календарю с выделенным днём.
    """
    date_str = callback.data.split(":", 1)[1]
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    await open_calendar(callback.message, base=d, selected=d)
    await callback.answer()

async def _on_hour_back(callback: CallbackQuery):
    """
    cal.hour.back:YYYY-MM-DD -> вернуться к сетке часов.
    """
    date_str = callback.data.split(":", 1)[1]
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    kb = _build_hours_markup(d, None)
    try:
        await callback.message.edit_text(
            "Выберите время:\n<b>Шаг минут — 5</b>",
            reply_markup=kb,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        await callback.message.edit_reply_markup(reply_markup=kb)
    await callback.answer()

def router(rt: Router):
    """
    Регистрируем навигацию календаря и выбор времени.
    День (cal.date:YYYY-MM-DD) и подтверждение времени (cal.done:...)
    ловит внешний код (например, чтобы сохранить дату/время).
    """
    # Выбор дня → сразу переходим к выбору времени
    rt.callback_query.register(_on_date, F.data.startswith(f"{CB_PREFIX}.date:"))
    rt.callback_query.register(_on_nav, F.data.startswith(f"{CB_PREFIX}.nav:"))
    rt.callback_query.register(_on_ignore, F.data.startswith(f"{CB_PREFIX}.ignore"))
    # Выбор времени
    rt.callback_query.register(_on_pick_hour, F.data.startswith(f"{CB_PREFIX}.hour:"))
    rt.callback_query.register(_on_pick_min, F.data.startswith(f"{CB_PREFIX}.min:"))
    rt.callback_query.register(_on_time_back, F.data.startswith(f"{CB_PREFIX}.time.back:"))
    rt.callback_query.register(_on_hour_back, F.data.startswith(f"{CB_PREFIX}.hour.back:"))
