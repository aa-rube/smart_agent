# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\description_playbook.py
# Всегда пиши код без «поддержки старых версий».
# Секрет офигенного бота:
# если callback -> обновляем текущее сообщение (msg из update)
# если обычный text_message / command -> отправляем НОВОЕ сообщение
# Без «якорей», без залипаний. Если редактирование не удалось — фолбэк на новое сообщение.

from __future__ import annotations

from typing import Optional, List, Dict, Any, Tuple
import json
import re

from aiogram import Router, F, Bot
from html import escape as html_escape
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

# ====== Доступ / подписка (как в plans/design) ======
import bot.utils.database as db
from bot.utils.database import is_trial_active, trial_remaining_hours


# ==========================
# Подписка / доступ
# ==========================
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
        return f'✅ Подписка активна до <b>{html_escape(str(sub_until))}</b>'
    if trial_hours > 0:
        return f'🆓 Бесплатный доступ активен ещё ~{trial_hours} ч.'
    return '😢 Бесплатный период завершён. Оформи подписку, чтобы продолжить.'


def _has_access(user_id: int) -> bool:
    return is_trial_active(user_id) or _is_sub_active(user_id)


SUB_FREE = (
    "🎁 Бесплатный период завершён\n"
    "Пробный доступ на 72 часа истёк — дальше только по подписке.\n\n"
    "📦 <b>Что даёт подписка:</b>\n"
    "— Полный доступ ко всем инструментам\n"
    "— Без ограничений по количеству запусков в период подписки*\n"
    "Стоимость пакета всего 2500 рублей!"
)
SUB_PAY = (
    "🪫 Подписка не активна\n"
    "Срок подписки истёк или не был оформлен.\n\n"
    "📦 <b>Что даёт подписка:</b>\n"
    "— Полный доступ ко всем инструментам\n"
    "— Без ограничений по количеству запусков в период подписки*\n"
    "Стоимость пакета всего 2500 рублей!"
)
SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)

# ==========================
# FSM
# ==========================
class DPBStates(StatesGroup):
    waiting_input = State()  # ожидаем текст для «Ввести своё…»
    idle = State()           # показываем кнопки / навигацию


# ==========================
# Тексты / Промпты
# ==========================
DESC_INTRO = (
    "Заполните короткую анкету и получите структурированное ТЗ по объекту.\n"
    "🧩 Отвечайте по шагам — кнопками или кратким вводом. В конце пришлю сводку и JSON payload."
)

PROMPTS = {
    "root_type": "1️⃣ Какой тип объекта?",
    # Квартира
    "flat_market": "Рынок квартиры?",
    "flat_due": "Срок сдачи?",
    "flat_sale_method": "Способ продажи?",
    "flat_rooms": "Количество комнат?",
    "flat_mortgage": "Подходит для ипотеки?",
    "flat_total_area": "Укажите общую площадь (м²)",
    "flat_kitchen": "Площадь кухни (м²)",
    "flat_floor": "Этаж квартиры?",
    "flat_floors_total": "Сколько этажей в доме?",
    "flat_bathroom": "Санузел?",
    "flat_windows": "Куда выходят окна?",
    "flat_house_type": "Тип дома?",
    "flat_elevator": "Лифт?",
    "flat_parking": "Парковка?",
    "flat_repair": "Состояние ремонта?",
    "flat_layout": "Планировка комнат?",
    "flat_balcony": "Балкон или лоджия?",
    "flat_ceiling": "Высота потолков (м)? (опционально)",

    # Загородная
    "country_kind": "Тип объекта загородной недвижимости?",
    # Домовая ветка
    "c_house_area": "Площадь дома (м²)?",
    "c_land_area": "Площадь участка (сот.)?",
    "c_distance": "Расстояние от города (км)?",
    "c_house_floors": "Этажей в доме?",
    "c_rooms": "Комнат?",
    "c_land_category": "Категория земель?",
    "c_condition": "Состояние/ремонт?",
    "c_wc": "Санузел?",
    "c_comms": "Коммуникации? (выберите тэги и нажмите «Готово»)",
    "c_leisure": "Для отдыха? (выберите тэги и нажмите «Готово»)",
    "c_walls": "Материал стен?",
    "c_parking": "Парковка?",
    "c_access": "Транспортная доступность?",
    # Участок
    "p_land_category": "Категория земель?",
    "p_land_area": "Площадь участка (сот.)?",
    "p_distance": "Расстояние до города (км)?",
    "p_comms": "Коммуникации? (выберите тэги и нажмите «Готово»)",

    # Коммерческая
    "comm_kind": "Вид объекта?",
    "comm_area": "Площадь (м²)?",
    "comm_plot_area": "Площадь участка (если есть)?",
    "comm_building": "Тип здания?",
    "comm_whole": "Объект целиком?",
    "comm_finish": "Отделка?",
    "comm_entrance": "Вход?",
    "comm_parking": "Парковка?",
    "comm_layout": "Планировка?",
}

# ==========================
# Опции
# ==========================
CHOICES: Dict[str, List[Tuple[str, str]]] = {
    "root_type": [("flat", "Квартира"), ("country", "Загородная"), ("commercial", "Коммерческая")],
    "flat_market": [("new", "Новостройка"), ("secondary", "Вторичка")],
    "flat_due": [("Q4-2025", "Q4-2025"), ("2026", "2026"), ("2027", "2027"), ("other", "Другое…")],
    "flat_sale_method": [("dkp", "ДКП"), ("cession", "Переуступка"), ("fz214", "ФЗ-214")],
    "flat_rooms": [("studio", "Студия"), ("1", "1"), ("2", "2"), ("3", "3"), ("4plus", "4+")],
    "flat_mortgage": [("yes", "Да"), ("no", "Нет")],
    "flat_bathroom": [("combined", "Совмещённый"), ("separate", "Раздельный")],
    "flat_windows": [("yard", "Во двор"), ("street", "На улицу"), ("sunny", "На солнечную"), ("mixed", "Разное")],
    "flat_house_type": [("brick", "Кирпич"), ("panel", "Панель"), ("block", "Блочный"),
                        ("monolith", "Монолит"), ("monolith_brick", "Монолит-кирпич")],
    "flat_elevator": [("no", "Нет"), ("passenger", "Пассажирский"), ("cargo", "Грузовой"), ("both", "Оба")],
    "flat_parking": [("underground", "Подземная"), ("ground", "Наземная"), ("multilevel", "Многоуровневая"),
                     ("yard_open", "Двор"), ("yard_gate", "Двор со шлагбаумом")],
    "flat_repair": [("need", "Требуется"), ("cosmetic", "Косметический"), ("euro", "Евро"), ("design", "Дизайнерский")],
    "flat_layout": [("isolated", "Изолированные"), ("adjacent", "Смежные"), ("mixed", "Смешанные")],
    "flat_balcony": [("no", "Нет"), ("balcony", "Балкон"), ("loggia", "Лоджия"), ("multi", "Несколько")],

    "country_kind": [
        ("house", "Дом"), ("dacha", "Дача"), ("cottage", "Коттедж"), ("townhouse", "Таунхаус"),
        ("plot", "Участок")
    ],
    "c_rooms": [("2", "2"), ("3", "3"), ("4", "4"), ("5plus", "5+")],
    "c_land_category": [("izhc", "ИЖС"), ("garden", "Сад"), ("lph", "ЛПХ"), ("kfh", "КФХ"), ("other", "Иное")],
    "c_condition": [("need", "Требуется"), ("cosmetic", "Косметический"), ("euro", "Евро"), ("design", "Дизайнерский")],
    "c_wc": [("indoor", "В доме"), ("outdoor", "На улице"), ("both", "Оба")],
    "c_comms": [("electric", "Электричество"), ("gas", "Газ"), ("heating", "Отопление"),
                ("water", "Вода"), ("sewage", "Канализация")],
    "c_leisure": [("banya", "Баня"), ("pool", "Бассейн"), ("sauna", "Сауна"), ("other", "Другое")],
    "c_walls": [("brick", "Кирпич"), ("timber", "Брус"), ("log", "Бревно"), ("aac", "Газоблок"), ("metal", "Металл"),
                ("other", "Иное")],
    "c_parking": [("garage", "Гараж"), ("place", "Место"), ("canopy", "Навес"), ("no", "Нет")],
    "c_access": [("asphalt", "Асфальт"), ("bus", "Остановки ОТ"), ("rail", "ЖД станция"), ("dirt", "Грунтовка")],

    "p_land_category": [("settlement", "Поселения"), ("agri", "Сельхоз"), ("industrial", "Пром"), ("other", "Иное")],
    "p_comms": [("gas", "Газ"), ("water", "Вода"), ("power", "Свет"), ("border", "По границе"), ("none", "Нет")],

    "comm_kind": [("office", "Офис"), ("psn", "ПСН"), ("retail", "Торговая"), ("warehouse", "Склад"),
                  ("production", "Производство"), ("horeca", "Общепит"), ("hotel", "Гостиница")],
    "comm_building": [("bc", "БЦ"), ("mall", "ТЦ"), ("admin", "Админздание"), ("res", "Жилой дом"), ("other", "Другое")],
    "comm_whole": [("yes", "Да"), ("no", "Нет")],
    "comm_finish": [("none", "Без"), ("shell", "Черновая"), ("finish", "Чистовая"), ("office", "Офисная")],
    "comm_entrance": [("street", "С улицы"), ("yard", "Со двора"), ("second", "Отдельный второй вход")],
    "comm_parking": [("none", "Нет"), ("street", "Улица"), ("covered", "Крытая"), ("underground", "Подземная"),
                     ("guest", "Гостевая")],
    "comm_layout": [("open", "Open space"), ("cabinets", "Кабинетная"), ("mixed", "Смешанная")],
}

# Пресеты чисел (кнопки + «Ввести своё…»)
PRESETS: Dict[str, List[str]] = {
    "flat_total_area": ["30", "40", "50"],
    "flat_kitchen": ["8", "10", "12"],
    "flat_floor": ["1", "2", "3", "4", "5"],
    "flat_floors_total": ["5", "9", "12", "16"],
    "flat_ceiling": ["2.5", "2.7", "3.0"],

    "c_house_area": ["80", "120", "180"],
    "c_land_area": ["6", "10", "15", "20"],
    "c_distance": ["5", "10", "20", "30"],
    "c_house_floors": ["1", "2", "3"],

    "p_land_area": ["6", "10", "15", "20"],
    "p_distance": ["5", "10", "20", "30"],

    "comm_area": ["50", "100", "200", "500"],
    "comm_plot_area": ["0", "2", "5", "10"],
}

# Поля маппятся к типу ввода
FIELD_META: Dict[str, Dict[str, Any]] = {
    # типы: choice / number / multitag
    "root_type": {"kind": "choice"},
    # flat
    "flat_market": {"kind": "choice"},
    "flat_due": {"kind": "choice"},
    "flat_sale_method": {"kind": "choice"},
    "flat_rooms": {"kind": "choice"},
    "flat_mortgage": {"kind": "choice"},
    "flat_total_area": {"kind": "number", "type": "float"},
    "flat_kitchen": {"kind": "number", "type": "float"},
    "flat_floor": {"kind": "number", "type": "int"},
    "flat_floors_total": {"kind": "number", "type": "int"},
    "flat_bathroom": {"kind": "choice"},
    "flat_windows": {"kind": "choice"},
    "flat_house_type": {"kind": "choice"},
    "flat_elevator": {"kind": "choice"},
    "flat_parking": {"kind": "choice"},
    "flat_repair": {"kind": "choice"},
    "flat_layout": {"kind": "choice"},
    "flat_balcony": {"kind": "choice"},
    "flat_ceiling": {"kind": "number", "type": "float", "optional": True},
    # country
    "country_kind": {"kind": "choice"},
    "c_house_area": {"kind": "number", "type": "float"},
    "c_land_area": {"kind": "number", "type": "float"},
    "c_distance": {"kind": "number", "type": "float"},
    "c_house_floors": {"kind": "number", "type": "int"},
    "c_rooms": {"kind": "choice"},
    "c_land_category": {"kind": "choice"},
    "c_condition": {"kind": "choice"},
    "c_wc": {"kind": "choice"},
    "c_comms": {"kind": "multitag"},   # множественный выбор
    "c_leisure": {"kind": "multitag"},
    "c_walls": {"kind": "choice"},
    "c_parking": {"kind": "choice"},
    "c_access": {"kind": "choice"},
    # plot
    "p_land_category": {"kind": "choice"},
    "p_land_area": {"kind": "number", "type": "float"},
    "p_distance": {"kind": "number", "type": "float"},
    "p_comms": {"kind": "multitag"},
    # commercial
    "comm_kind": {"kind": "choice"},
    "comm_area": {"kind": "number", "type": "float"},
    "comm_plot_area": {"kind": "number", "type": "float", "optional": True},
    "comm_building": {"kind": "choice"},
    "comm_whole": {"kind": "choice"},
    "comm_finish": {"kind": "choice"},
    "comm_entrance": {"kind": "choice"},
    "comm_parking": {"kind": "choice"},
    "comm_layout": {"kind": "choice"},
}


# ==========================
# Утилиты UI
# ==========================
CTL_BACK = InlineKeyboardButton(text="⬅️ Назад", callback_data="dpb:back")
CTL_SKIP = InlineKeyboardButton(text="⏭ Пропустить", callback_data="dpb:skip")
CTL_RESET = InlineKeyboardButton(text="🗑 Сброс", callback_data="dpb:reset")

def _row(*btns: InlineKeyboardButton) -> List[List[InlineKeyboardButton]]:
    return [[*btns]]

def _with_controls(kb_rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    kb_rows.append([CTL_BACK, CTL_SKIP, CTL_RESET])
    return InlineKeyboardMarkup(inline_keyboard=kb_rows)

def _kb_choice(field: str, options: List[Tuple[str, str]]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for val, label in options:
        rows.append([InlineKeyboardButton(text=label, callback_data=f"dpb:sel:{field}:{val}")])
    return _with_controls(rows)

def _kb_number(field: str, presets: List[str]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if presets:
        # раскладываем по 3 в ряд
        row: List[InlineKeyboardButton] = []
        for p in presets:
            row.append(InlineKeyboardButton(text=p, callback_data=f"dpb:num:{field}:{p}"))
            if len(row) == 3:
                rows.append(row); row = []
        if row:
            rows.append(row)
    rows.append([InlineKeyboardButton(text="Ввести своё…", callback_data=f"dpb:other:{field}")])
    return _with_controls(rows)

def _kb_multitag(field: str, options: List[Tuple[str, str]], selected: List[str]) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    sel_set = set(selected or [])
    # две колонки
    row: List[InlineKeyboardButton] = []
    for val, label in options:
        mark = "✅ " if val in sel_set else "☐ "
        btn = InlineKeyboardButton(text=mark + label, callback_data=f"dpb:toggle:{field}:{val}")
        row.append(btn)
        if len(row) == 2:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Готово", callback_data=f"dpb:done:{field}")])
    return _with_controls(rows)

def _preview_short(data: Dict[str, Any]) -> str:
    # компактная строка «собрано уже»
    parts: List[str] = []
    t = data.get("root_type")
    if t == "flat":
        if data.get("flat_market") == "new":
            parts.append("Квартира, новостройка")
        elif data.get("flat_market") == "secondary":
            parts.append("Квартира, вторичка")
        else:
            parts.append("Квартира")
        if data.get("flat_rooms"):
            parts.append(f"комнат: {data.get('flat_rooms')}")
        if data.get("flat_total_area"):
            parts.append(f"{data.get('flat_total_area')} м²")
    elif t == "country":
        k = data.get("country_kind")
        if k:
            parts.append(f"Загородная: {k}")
        if data.get("c_house_area"):
            parts.append(f"{data.get('c_house_area')} м²")
        if data.get("c_land_area"):
            parts.append(f"{data.get('c_land_area')} сот.")
    elif t == "commercial":
        k = data.get("comm_kind")
        parts.append("Коммерческая" + (f": {k}" if k else ""))
        if data.get("comm_area"):
            parts.append(f"{data.get('comm_area')} м²")
    if not parts:
        return ""
    return "🧾 Собрано уже: " + " • ".join(parts)

async def _edit_text(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Единая функция вывода с безопасным HTML и без предпросмотра ссылок."""
    try:
        await msg.edit_text(
            text,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except TelegramBadRequest:
        await msg.answer(
            text,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


# ==========================
# Логика маршрутов (построение шагов)
# ==========================
def _flat_steps(market: Optional[str]) -> List[str]:
    base = []
    if market == "new":
        base += ["flat_due", "flat_sale_method"]
    # общие поля
    base += [
        "flat_rooms", "flat_mortgage", "flat_total_area", "flat_kitchen",
        "flat_floor", "flat_floors_total", "flat_bathroom", "flat_windows",
        "flat_house_type", "flat_elevator", "flat_parking", "flat_repair",
        "flat_layout", "flat_balcony", "flat_ceiling"
    ]
    return base

def _country_steps(kind: Optional[str]) -> List[str]:
    if kind == "plot":
        return ["p_land_category", "p_land_area", "p_distance", "p_comms"]
    # дом/дача/коттедж/таунхаус
    return [
        "c_house_area", "c_land_area", "c_distance", "c_house_floors", "c_rooms",
        "c_land_category", "c_condition", "c_wc", "c_comms", "c_leisure",
        "c_walls", "c_parking", "c_access"
    ]

def _comm_steps(kind: Optional[str]) -> List[str]:
    steps = ["comm_area"]
    if kind in {"warehouse", "production"}:
        steps.append("comm_plot_area")
    steps += ["comm_building", "comm_whole", "comm_finish", "comm_entrance", "comm_parking", "comm_layout"]
    return steps

def _build_steps(data: Dict[str, Any]) -> List[str]:
    steps: List[str] = []
    t = data.get("root_type")
    if t == "flat":
        steps = ["flat_market"] + _flat_steps(data.get("flat_market"))
    elif t == "country":
        steps = ["country_kind"] + _country_steps(data.get("country_kind"))
    elif t == "commercial":
        steps = ["comm_kind"] + _comm_steps(data.get("comm_kind"))
    else:
        steps = ["root_type"]
    return steps


# ==========================
# Валидация ввода чисел
# ==========================
def _parse_number(s: str, num_type: str) -> Optional[float | int]:
    s = s.strip().replace(",", ".")
    if num_type == "int":
        if re.fullmatch(r"\d{1,4}", s):
            return int(s)
        return None
    try:
        val = float(s)
        if val < 0:
            return None
        return val
    except Exception:
        return None


# ==========================
# Показ текущего шага
# ==========================
async def _show_step(
    target_msg: Message,
    state: FSMContext,
    *,
    as_edit: bool = True,
) -> None:
    data = await state.get_data()
    steps: List[str] = data.get("steps") or []
    idx = int(data.get("idx") or 0)

    # если шагов нет — начнём
    if not steps:
        steps = ["root_type"]
        idx = 0
        await state.update_data(steps=steps, idx=idx)

    # если закончились — финал
    if idx >= len(steps):
        await _finish(target_msg, state, as_edit=as_edit)
        return

    field = steps[idx]
    prompt = PROMPTS.get(field, "Уточните:")

    # Заголовок + превью
    preview = _preview_short(data)
    head = f"{prompt}\n\n{preview}" if preview else prompt

    meta = FIELD_META.get(field, {"kind": "choice"})
    kind = meta["kind"]

    # Клавиатуры
    kb: Optional[InlineKeyboardMarkup] = None
    if kind == "choice":
        kb = _kb_choice(field, CHOICES[field])
    elif kind == "number":
        kb = _kb_number(field, PRESETS.get(field, []))
    elif kind == "multitag":
        kb = _kb_multitag(field, CHOICES[field], data.get(field, []))
    else:
        kb = _with_controls([])

    if as_edit:
        await _edit_text(target_msg, head, kb)
    else:
        await target_msg.answer(head, reply_markup=kb)

    # Состояние
    await state.set_state(DPBStates.idle)


# ==========================
# Финал
# ==========================
async def _finish(target_msg: Message, state: FSMContext, *, as_edit: bool) -> None:
    data = await state.get_data()
    payload = _make_payload(data)

    # Человеческая сводка
    pretty = _pretty_summary(payload)
    # Безопасный JSON-блок для Telegram HTML
    json_str = json.dumps(payload, ensure_ascii=False, indent=2)
    json_block = "<pre><code>" + html_escape(json_str) + "</code></pre>"
    text = "✅ Готово! Вот сводка и JSON payload.\n\n" + html_escape(pretty) + "\n\n" + json_block

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Новый опрос", callback_data="dpb:reset")]
    ])
    if as_edit:
        await _edit_text(target_msg, text, kb)
    else:
        await target_msg.answer(
            text,
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    await state.set_state(DPBStates.idle)


def _make_payload(d: Dict[str, Any]) -> Dict[str, Any]:
    t = d.get("root_type")
    out: Dict[str, Any] = {"type": t}

    if t == "flat":
        out.update({
            "market": d.get("flat_market"),  # new / secondary
        })
        if d.get("flat_market") == "new":
            out["due"] = d.get("flat_due")   # Q4-2025 / 2026 / 2027 / text
            out["sale_method"] = d.get("flat_sale_method")  # dkp/cession/fz214

        out.update({
            "rooms": d.get("flat_rooms"),
            "mortgage": d.get("flat_mortgage"),
            "total_area_m2": d.get("flat_total_area"),
            "kitchen_m2": d.get("flat_kitchen"),
            "floor": d.get("flat_floor"),
            "floors_total": d.get("flat_floors_total"),
            "bathroom": d.get("flat_bathroom"),
            "windows": d.get("flat_windows"),
            "house_type": d.get("flat_house_type"),
            "elevator": d.get("flat_elevator"),
            "parking": d.get("flat_parking"),
            "repair": d.get("flat_repair"),
            "layout": d.get("flat_layout"),
            "balcony": d.get("flat_balcony"),
            "ceiling_m": d.get("flat_ceiling"),
        })

    elif t == "country":
        kind = d.get("country_kind")
        out["kind"] = kind  # house/dacha/cottage/townhouse/plot
        if kind == "plot":
            out.update({
                "land_category": d.get("p_land_category"),
                "land_area_sot": d.get("p_land_area"),
                "distance_km": d.get("p_distance"),
                "comms": d.get("p_comms", []),
            })
        else:
            out.update({
                "house_area_m2": d.get("c_house_area"),
                "land_area_sot": d.get("c_land_area"),
                "distance_km": d.get("c_distance"),
                "house_floors": d.get("c_house_floors"),
                "rooms": d.get("c_rooms"),
                "land_category": d.get("c_land_category"),
                "condition": d.get("c_condition"),
                "wc": d.get("c_wc"),
                "comms": d.get("c_comms", []),
                "leisure": d.get("c_leisure", []),
                "walls": d.get("c_walls"),
                "parking": d.get("c_parking"),
                "access": d.get("c_access"),
            })

    elif t == "commercial":
        kind = d.get("comm_kind")
        out["kind"] = kind
        out.update({
            "area_m2": d.get("comm_area"),
        })
        if kind in {"warehouse", "production"}:
            out["plot_area"] = d.get("comm_plot_area")  # м²/сотки — далее на стороне исполнителя можно нормализовать
        out.update({
            "building": d.get("comm_building"),
            "whole": d.get("comm_whole"),
            "finish": d.get("comm_finish"),
            "entrance": d.get("comm_entrance"),
            "parking": d.get("comm_parking"),
            "layout": d.get("comm_layout"),
        })

    return out


def _pretty_summary(p: Dict[str, Any]) -> str:
    parts: List[str] = [f"Тип: {p.get('type') or '-'}"]
    t = p.get("type")
    if t == "flat":
        mrk = p.get("market")
        parts.append(f"Рынок: {mrk or '-'}")
        if mrk == "new":
            parts.append(f"Срок сдачи: {p.get('due') or '-'}")
            parts.append(f"Способ продажи: {p.get('sale_method') or '-'}")
        parts += [
            f"Комнаты: {p.get('rooms') or '-'}",
            f"Ипотека: {p.get('mortgage') or '-'}",
            f"Площадь: {p.get('total_area_m2') or '-'} м², кухня {p.get('kitchen_m2') or '-'} м²",
            f"Этаж/этажность: {p.get('floor') or '-'} / {p.get('floors_total') or '-'}",
            f"С/у: {p.get('bathroom') or '-'}, окна: {p.get('windows') or '-'}",
            f"Дом: {p.get('house_type') or '-'}, лифт: {p.get('elevator') or '-'}, парковка: {p.get('parking') or '-'}",
            f"Ремонт: {p.get('repair') or '-'}, планировка: {p.get('layout') or '-'}, балкон: {p.get('balcony') or '-'}",
            f"Потолки: {p.get('ceiling_m') or '-'} м",
        ]
    elif t == "country":
        k = p.get("kind")
        parts.append(f"Вид: {k or '-'}")
        if k == "plot":
            parts += [
                f"Категория: {p.get('land_category') or '-'}",
                f"Площадь участка: {p.get('land_area_sot') or '-'} сот.",
                f"До города: {p.get('distance_km') or '-'} км",
                f"Коммуникации: {', '.join(p.get('comms') or []) or '-'}",
            ]
        else:
            parts += [
                f"Дом: {p.get('house_area_m2') or '-'} м², участок {p.get('land_area_sot') or '-'} сот.",
                f"До города: {p.get('distance_km') or '-'} км, этажей: {p.get('house_floors') or '-'}",
                f"Комнат: {p.get('rooms') or '-'}, категория земель: {p.get('land_category') or '-'}",
                f"Состояние: {p.get('condition') or '-'}, санузел: {p.get('wc') or '-'}",
                f"Коммуникации: {', '.join(p.get('comms') or []) or '-'}",
                f"Для отдыха: {', '.join(p.get('leisure') or []) or '-'}",
                f"Материал стен: {p.get('walls') or '-'}, парковка: {p.get('parking') or '-'}",
                f"Доступность: {p.get('access') or '-'}",
            ]
    elif t == "commercial":
        parts += [
            f"Вид: {p.get('kind') or '-'}",
            f"Площадь: {p.get('area_m2') or '-'}",
        ]
        if p.get("kind") in {"warehouse", "production"}:
            parts.append(f"Площадь участка: {p.get('plot_area') or '-'}")
        parts += [
            f"Здание: {p.get('building') or '-'}, объект целиком: {p.get('whole') or '-'}",
            f"Отделка: {p.get('finish') or '-'}, вход: {p.get('entrance') or '-'}",
            f"Парковка: {p.get('parking') or '-'}, планировка: {p.get('layout') or '-'}",
        ]
    return "\n".join("• " + s for s in parts)


# ==========================
# Навигация / обработчики
# ==========================
async def _start_or_require_access(cb: CallbackQuery, state: FSMContext) -> bool:
    user_id = cb.message.chat.id
    if not _has_access(user_id):
        if not _is_sub_active(user_id):
            await _edit_text(cb.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text(cb.message, SUB_PAY, SUBSCRIBE_KB)
        await cb.answer()
        return False
    await state.clear()
    intro = f"{DESC_INTRO}\n\n{_format_access_text(user_id)}\n\n{PROMPTS['root_type']}"
    kb = _kb_choice("root_type", CHOICES["root_type"])
    await _edit_text(cb.message, intro, kb)
    await state.update_data(steps=["root_type"], idx=0)
    await state.set_state(DPBStates.idle)
    await cb.answer()
    return True


async def start_description_flow(cb: CallbackQuery, state: FSMContext):
    await _start_or_require_access(cb, state)


# === Выбор опции (choice) ===
async def on_select(cb: CallbackQuery, state: FSMContext, bot: Bot):
    _, _, field, value = cb.data.split(":", 3)
    data = await state.get_data()

    # Сохраняем выбор
    data[field] = value

    # Если выбор влияет на ветку — перестроим steps
    if field == "root_type":
        data["steps"] = _build_steps(data)
        data["idx"] = 1  # следующий после root_type
    elif field == "flat_market":
        # перестраиваем плоский список под рынок
        steps = ["flat_market"] + _flat_steps(value)
        data["steps"] = ["root_type"] + steps
        data["idx"] = data["steps"].index("flat_market") + 1
    elif field == "country_kind":
        steps = ["country_kind"] + _country_steps(value)
        data["steps"] = ["root_type"] + ["country_kind"] + _country_steps(value)
        data["idx"] = data["steps"].index("country_kind") + 1
    elif field == "comm_kind":
        steps = ["comm_kind"] + _comm_steps(value)
        data["steps"] = ["root_type"] + steps
        data["idx"] = data["steps"].index("comm_kind") + 1
    else:
        # просто идём дальше
        steps: List[str] = data.get("steps") or _build_steps(data)
        idx: int = int(data.get("idx") or 0)
        # убедимся, что в пределах
        if idx < len(steps) and steps[idx] == field:
            data["idx"] = idx + 1
        else:
            # если десинхрон — найдём следующий
            try:
                data["idx"] = steps.index(field) + 1
            except ValueError:
                data["idx"] = idx + 1
        data["steps"] = steps

    await state.update_data(**data)
    await _show_step(cb.message, state, as_edit=True)
    await cb.answer()


# === Пресет числа ===
async def on_number_preset(cb: CallbackQuery, state: FSMContext):
    _, _, field, val = cb.data.split(":", 3)
    meta = FIELD_META.get(field, {})
    num_t = meta.get("type", "float")
    parsed = _parse_number(val, num_t)
    data = await state.get_data()

    if parsed is None:
        await cb.answer("Невалидное число", show_alert=True)
        return

    data[field] = parsed

    # автопереход
    steps: List[str] = data.get("steps") or _build_steps(data)
    idx = int(data.get("idx") or 0)
    if idx < len(steps) and steps[idx] == field:
        data["idx"] = idx + 1
    else:
        try:
            data["idx"] = steps.index(field) + 1
        except ValueError:
            data["idx"] = idx + 1

    await state.update_data(**data)
    await _show_step(cb.message, state, as_edit=True)
    await cb.answer()


# === «Ввести своё» для числа/срока ===
async def on_other(cb: CallbackQuery, state: FSMContext):
    _, _, field = cb.data.split(":", 2)
    await state.update_data(wait_field=field)
    await state.set_state(DPBStates.waiting_input)
    await _edit_text(cb.message, "✍️ Введите значение в ответном сообщении.\n"
                                 "Для чисел — только число. Для срока (например, «Q2-2026»).")
    await cb.answer()


# === Мультивыбор: toggle / done ===
async def on_toggle(cb: CallbackQuery, state: FSMContext):
    _, _, field, val = cb.data.split(":", 3)
    data = await state.get_data()
    current: List[str] = list(data.get(field) or [])
    if val in current:
        current.remove(val)
    else:
        current.append(val)
    await state.update_data(**{field: current})
    # обновим клавиатуру (редактирование)
    kb = _kb_multitag(field, CHOICES[field], current)
    preview = _preview_short(await state.get_data())
    prompt = PROMPTS.get(field, "Выберите:")
    head = f"{prompt}\n\n{preview}" if preview else prompt
    await _edit_text(cb.message, head, kb)
    await cb.answer()


async def on_done(cb: CallbackQuery, state: FSMContext):
    _, _, field = cb.data.split(":", 2)
    data = await state.get_data()
    steps: List[str] = data.get("steps") or _build_steps(data)
    idx = int(data.get("idx") or 0)
    if idx < len(steps) and steps[idx] == field:
        data["idx"] = idx + 1
    else:
        try:
            data["idx"] = steps.index(field) + 1
        except ValueError:
            data["idx"] = idx + 1
    await state.update_data(**data)
    await _show_step(cb.message, state, as_edit=True)
    await cb.answer()


# === Текстовый ввод значения ===
async def on_text_input(msg: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("wait_field")
    if not field:
        # если что-то пошло не так — покажем текущий шаг
        await _show_step(msg, state, as_edit=False)
        return

    meta = FIELD_META.get(field, {})
    kind = meta.get("kind", "number")
    val_raw = (msg.text or "").strip()

    if field == "flat_due" and kind == "choice":
        # «Другое…» для срока сдачи — принимаем строку без валидации
        data[field] = val_raw
    elif kind == "number":
        num_t = meta.get("type", "float")
        parsed = _parse_number(val_raw, num_t)
        if parsed is None:
            await msg.answer("⚠️ Введите корректное число.")
            return
        data[field] = parsed
    else:
        # общий фолбэк
        data[field] = val_raw

    # очистим ожидание
    data.pop("wait_field", None)

    # сместим индекс на следующий шаг
    steps: List[str] = data.get("steps") or _build_steps(data)
    idx = int(data.get("idx") or 0)
    if idx < len(steps) and steps[idx] == field:
        data["idx"] = idx + 1
    else:
        try:
            data["idx"] = steps.index(field) + 1
        except ValueError:
            data["idx"] = idx + 1

    await state.update_data(**data)
    await _show_step(msg, state, as_edit=False)


# === Назад / Пропуск / Сброс ===
async def on_back(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    steps: List[str] = data.get("steps") or _build_steps(data)
    idx = max(0, int(data.get("idx") or 0) - 1)
    # очистим значение текущего «предыдущего» поля (чтобы повторно спросить)
    if steps:
        field_to_clear = steps[idx] if idx < len(steps) else None
        if field_to_clear:
            data.pop(field_to_clear, None)
    data["idx"] = idx
    await state.update_data(**data)
    await _show_step(cb.message, state, as_edit=True)
    await cb.answer()


async def on_skip(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    steps: List[str] = data.get("steps") or _build_steps(data)
    idx = int(data.get("idx") or 0)
    if idx < len(steps):
        field = steps[idx]
        # помечаем пропуск как None
        data[field] = None
        data["idx"] = idx + 1
    await state.update_data(**data)
    await _show_step(cb.message, state, as_edit=True)
    await cb.answer()


async def on_reset(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    intro = f"{DESC_INTRO}\n\n{_format_access_text(cb.message.chat.id)}\n\n{PROMPTS['root_type']}"
    kb = _kb_choice("root_type", CHOICES["root_type"])
    await _edit_text(cb.message, intro, kb)
    await state.update_data(steps=["root_type"], idx=0)
    await state.set_state(DPBStates.idle)
    await cb.answer("Сброшено")


# ==========================
# Router
# ==========================
def router(rt: Router):
    # старт
    rt.callback_query.register(start_description_flow, F.data == "nav.descr_home")
    rt.callback_query.register(start_description_flow, F.data == "desc_start")

    # выборы / числа / теги
    rt.callback_query.register(on_select, F.data.startswith("dpb:sel:"), DPBStates.idle)
    rt.callback_query.register(on_number_preset, F.data.startswith("dpb:num:"), DPBStates.idle)
    rt.callback_query.register(on_other, F.data.startswith("dpb:other:"), DPBStates.idle)
    rt.callback_query.register(on_toggle, F.data.startswith("dpb:toggle:"), DPBStates.idle)
    rt.callback_query.register(on_done, F.data.startswith("dpb:done:"), DPBStates.idle)

    # навигация
    rt.callback_query.register(on_back, F.data == "dpb:back")
    rt.callback_query.register(on_skip, F.data == "dpb:skip")
    rt.callback_query.register(on_reset, F.data == "dpb:reset")

    # текстовый ввод для «Ввести своё…»
    rt.message.register(on_text_input, DPBStates.waiting_input, F.text)
