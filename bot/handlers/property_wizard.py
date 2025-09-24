# smart_agent/bot/handlers/property_wizard.py
# Всегда без "поддержки старых версий".
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Callable, Literal
from html import escape as _esc

import re
from aiogram import Router, F, Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# ──────────────────────────────────────────────────────────────────────────────
# ТЕКСТЫ (готовые промпты/подписи/лейблы)
# ──────────────────────────────────────────────────────────────────────────────

INTRO = (
    "Заполним короткую анкету и соберём параметры объекта. "
    "Выбирайте варианты кнопками или вводите значения.\n\n"
    "В каждом шаге доступны: «Назад», «Пропустить», «Сброс»."
)

ASK_OBJECT_TYPE = "Какой тип объекта?"
OBJECT_TYPES = ["Квартира", "Загородная", "Коммерческая"]

# Квартира → Рынок
ASK_FLAT_MARKET = "Рынок квартиры?"
FLAT_MARKETS = ["Новостройка", "Вторичка"]

ASK_FLAT_ROOMS = "Количество комнат?"
FLAT_ROOMS = ["Студия", "1", "2", "3", "4+"]

ASK_FLAT_MORTGAGE = "Подходит для ипотеки?"
YES_NO = ["Да", "Нет"]

ASK_TOTAL_AREA = "Укажите общую площадь (м²)"
ASK_KITCHEN_AREA = "Площадь кухни (м²)"
ASK_FLOOR = "Этаж квартиры?"
ASK_FLOORS = "Сколько этажей в доме?"

ASK_BATH = "Санузел?"
BATH_TYPES = ["Совмещённый", "Раздельный"]

ASK_WINDOWS = "Куда выходят окна?"
WINDOWS = ["Во двор", "На улицу", "На солнечную сторону", "Разное"]

ASK_HOUSE_TYPE = "Тип дома?"
HOUSE_TYPES = ["Кирпичный", "Панельный", "Блочный", "Монолитный", "Монолит-кирпич"]

ASK_ELEVATOR = "Лифт?"
ELEVATOR = ["Нет", "Пассажирский", "Грузовой", "Оба"]

ASK_PARKING_FLAT = "Парковка?"
PARKING_FLAT = ["Подземная", "Наземная", "Многоуровневая", "Открытая во дворе", "За шлагбаумом"]

ASK_RENOVATION = "Состояние ремонта?"
RENOVATION = ["Требуется", "Косметический", "Евро", "Дизайнерский"]

ASK_LAYOUT = "Планировка комнат?"
LAYOUT = ["Изолированные", "Смежные", "И то, и другое"]

ASK_BALCONY = "Балкон/лоджия?"
BALCONY = ["Нет", "Балкон", "Лоджия", "Несколько"]

ASK_HEIGHT = "Высота потолков (м)?"

# Новостройка only
ASK_NEWBUILD_DEADLINE = "Срок сдачи?"
NEWBUILD_DEADLINE = ["Q4-2025", "2026", "2027", "Другое…"]

ASK_NEWBUILD_SALE = "Способ продажи?"
NEWBUILD_SALE = ["ДКП", "Переуступка", "ФЗ-214"]

# Загородная
ASK_COUNTRY_KIND = "Тип объекта?"
COUNTRY_KIND = ["Дом", "Дача", "Коттедж", "Таунхаус", "Земельный участок"]

ASK_HOUSE_SQ = "Площадь дома (м²)?"
ASK_PLOT_SOT = "Площадь участка (сот.)?"
ASK_DISTANCE = "Расстояние от города (км)?"
ASK_HOUSE_STOREYS = "Этажей в доме?"
ASK_HOUSE_ROOMS = "Комнат?"

ASK_LAND_CAT_HOUSE = "Категория земель?"
LAND_CAT_HOUSE = ["ИЖС", "Садоводство", "ЛПХ", "КФХ", "Иное"]

ASK_COUNTRY_RENOV = "Состояние/ремонт?"
ASK_TOILET_COUNTRY = "Санузел?"
TOILET_COUNTRY = ["В доме", "На улице", "Оба"]

ASK_UTILS_COUNTRY = "Коммуникации?"  # мультивыбор
UTILS_COUNTRY = ["Электричество", "Газ", "Отопление", "Водоснабжение", "Канализация"]

ASK_FUN_COUNTRY = "Для отдыха?"
FUN_COUNTRY = ["Баня", "Бассейн", "Сауна", "Другое"]

ASK_WALLS = "Материал стен?"
WALLS = ["Кирпич", "Брус", "Бревно", "Газоблок", "Металл", "Иное"]

ASK_PARKING_COUNTRY = "Парковка?"
PARKING_COUNTRY = ["Гараж", "Парковочное место", "Навес", "Нет"]

ASK_ACCESS = "Транспортная доступность?"
ACCESS = ["Асфальт", "Остановки ОТ", "ЖД станция", "Грунтовка"]

# Земельный участок
ASK_LAND_CAT_PLOT = "Категория земель?"
LAND_CAT_PLOT = ["Поселения", "Сельхоз", "Пром", "Иное"]

ASK_UTILS_PLOT = "Коммуникации?"
UTILS_PLOT = ["Газ", "Вода", "Свет", "По границе", "Нет"]

# Коммерческая
ASK_COMM_KIND = "Вид объекта?"
COMM_KINDS = ["Офис", "ПСН", "Торговая площадь", "Склад", "Производство", "Общепит", "Гостиница"]

ASK_COMM_AREA = "Площадь (м²)?"
ASK_COMM_LAND = "Площадь участка (если есть)?"
COMM_LAND_PRESETS = ["Нет", "2", "5", "10", "Другое…"]

ASK_COMM_BUILDING = "Тип здания?"
COMM_BUILDING = ["Бизнес-центр", "ТЦ", "Админздание", "Жилой дом", "Другое"]

ASK_COMM_WHOLE = "Объект целиком?"
ASK_FINISH = "Отделка?"
FINISH = ["Без", "Черновая", "Чистовая", "Офисная"]

ASK_ENTRANCE = "Вход?"
ENTRANCE = ["С улицы", "Со двора", "Отдельный второй вход"]

ASK_COMM_PARKING = "Парковка?"
COMM_PARKING = ["Нет", "На улице", "Крытая", "Подземная", "Гостевая"]

ASK_LAYOUT_COMM = "Тип планировки?"
LAYOUT_COMM = ["Open space", "Кабинетная", "Смешанная"]

# Кнопки управления
BTN_BACK = "⬅️ Назад"
BTN_SKIP = "⏭ Пропустить"
BTN_RESET = "🗑 Сброс"
BTN_DONE = "✅ Готово"
BTN_ENTER_OWN = "✏️ Ввести своё"

def _h(text: str) -> str:
    return _esc(text or "")

# ──────────────────────────────────────────────────────────────────────────────
# FSM
# ──────────────────────────────────────────────────────────────────────────────

class PropertyWizard(StatesGroup):
    choosing_root = State()
    answering = State()          # единый шаг: ожидаем клик/ввод
    multiselect = State()        # для мультивыбора (ожидаем тэг-клики), подтверждаем "Готово"
    finish = State()

# ──────────────────────────────────────────────────────────────────────────────
# УТИЛИТЫ
# ──────────────────────────────────────────────────────────────────────────────

def kb_rows(labels: List[str], prefix: str) -> List[List[InlineKeyboardButton]]:
    rows: List[List[InlineKeyboardButton]] = []
    for text in labels:
        rows.append([InlineKeyboardButton(text=text, callback_data=f"{prefix}:{text}")])
    return rows

def kb_controls(
    with_done: bool = False, done_disabled: bool = False, prefix_done: str = "done"
) -> List[List[InlineKeyboardButton]]:
    rows: List[List[InlineKeyboardButton]] = []
    if with_done:
        done_text = BTN_DONE if not done_disabled else "✅ Готово (выберите хотя бы один)"
        rows.append([InlineKeyboardButton(text=done_text, callback_data=f"{prefix_done}:ok" if not done_disabled else "noop")])
    rows.append([
        InlineKeyboardButton(text=BTN_BACK, callback_data="nav:back"),
        InlineKeyboardButton(text=BTN_SKIP, callback_data="nav:skip"),
        InlineKeyboardButton(text=BTN_RESET, callback_data="nav:reset"),
    ])
    return rows

def make_kb_single(labels: List[str], prefix: str, with_own: bool = False) -> InlineKeyboardMarkup:
    rows = kb_rows(labels, prefix)
    if with_own:
        rows.append([InlineKeyboardButton(text=BTN_ENTER_OWN, callback_data=f"{prefix}:__own")])
    rows.extend(kb_controls())
    return InlineKeyboardMarkup(inline_keyboard=rows)

def make_kb_numeric_presets(presets: List[str], prefix: str, allow_own: bool = True) -> InlineKeyboardMarkup:
    rows = kb_rows(presets, prefix)
    if allow_own and all("Другое" not in p for p in presets):
        rows.append([InlineKeyboardButton(text=BTN_ENTER_OWN, callback_data=f"{prefix}:__own")])
    rows.extend(kb_controls())
    return InlineKeyboardMarkup(inline_keyboard=rows)

def make_kb_multiselect(options: List[str], prefix: str, selected: Optional[List[str]] = None) -> InlineKeyboardMarkup:
    selected = selected or []
    rows: List[List[InlineKeyboardButton]] = []
    for opt in options:
        active = "● " if opt in selected else "○ "
        rows.append([InlineKeyboardButton(text=f"{active}{opt}", callback_data=f"{prefix}:{opt}")])
    rows.extend(kb_controls(with_done=True, done_disabled=(len(selected) == 0), prefix_done=prefix))
    return InlineKeyboardMarkup(inline_keyboard=rows)

def split_summary(payload: Dict[str, Any]) -> str:
    non_empty = {k: v for k, v in payload.items() if v not in (None, "", [], {})}
    if not non_empty:
        return "Пока ничего не выбрано."
    keys = list(non_empty.keys())
    show = keys[:8]  # короткий чек-лист
    return "Собрано: " + ", ".join(show) + (" …" if len(keys) > 8 else "")

def try_parse_float(txt: str) -> Optional[float]:
    try:
        val = float(txt.replace(",", ".").strip())
        return val if val >= 0 else None
    except Exception:
        return None

def try_parse_int(txt: str) -> Optional[int]:
    if not re.fullmatch(r"\d{1,5}", txt.strip()):
        return None
    return int(txt.strip())

# ──────────────────────────────────────────────────────────────────────────────
# ПОСЛЕДОВАТЕЛЬНОСТИ ВОПРОСОВ И ЗАВИСИМОСТИ
# ──────────────────────────────────────────────────────────────────────────────

# Определим "шаг": (ключ, тип, вопрос, варианты/пресеты, валидатор/преобразователь, мульти?, зависимость)
# type: 'choice' | 'number' | 'text' | 'multichoice'
StepType = Literal["choice", "number", "text", "multichoice"]

@dataclass
class Step:
    key: str
    stype: StepType
    question: str
    options: Optional[List[str]] = None          # для choice/multichoice
    presets: Optional[List[str]] = None          # для number
    depend: Optional[Callable[[Dict[str, Any]], bool]] = None  # показывать ли шаг
    number_kind: Optional[Literal["int", "float"]] = None
    hint_units: Optional[str] = None

# Зависимости
def show_newbuild_only(data: Dict[str, Any]) -> bool:
    return data.get("flat_market") == "Новостройка"

def show_comm_land(data: Dict[str, Any]) -> bool:
    kind = data.get("comm_kind")
    return kind in {"Склад", "Производство", "Гостиница"}  # «земля» чаще релевантна им

def is_country_house_branch(data: Dict[str, Any]) -> bool:
    return data.get("country_kind") in {"Дом", "Дача", "Коттедж", "Таунхаус"}

def is_country_plot_branch(data: Dict[str, Any]) -> bool:
    return data.get("country_kind") == "Земельный участок"

# Ветки

FLAT_FLOW: List[Step] = [
    Step("flat_market", "choice", ASK_FLAT_MARKET, FLAT_MARKETS),
    Step("newbuild_deadline", "choice", ASK_NEWBUILD_DEADLINE, NEWBUILD_DEADLINE, depend=show_newbuild_only),
    Step("newbuild_sale", "choice", ASK_NEWBUILD_SALE, NEWBUILD_SALE, depend=show_newbuild_only),

    Step("rooms", "choice", ASK_FLAT_ROOMS, FLAT_ROOMS),
    Step("mortgage", "choice", ASK_FLAT_MORTGAGE, YES_NO),
    Step("total_area", "number", ASK_TOTAL_AREA, presets=["30", "40", "50", "Другое…"], number_kind="float", hint_units="м²"),
    Step("kitchen_area", "number", ASK_KITCHEN_AREA, presets=["6", "8", "10", "12", "Другое…"], number_kind="float", hint_units="м²"),
    Step("floor", "number", ASK_FLOOR, presets=["1", "2", "3", "4", "5", "Другое…"], number_kind="int"),
    Step("floors_total", "number", ASK_FLOORS, presets=["5", "9", "12", "16", "Другое…"], number_kind="int"),

    Step("bath", "choice", ASK_BATH, BATH_TYPES),
    Step("windows", "choice", ASK_WINDOWS, WINDOWS),
    Step("house_type", "choice", ASK_HOUSE_TYPE, HOUSE_TYPES),
    Step("elevator", "choice", ASK_ELEVATOR, ELEVATOR),
    Step("parking", "choice", ASK_PARKING_FLAT, PARKING_FLAT),
    Step("renovation", "choice", ASK_RENOVATION, RENOVATION),
    Step("layout", "choice", ASK_LAYOUT, LAYOUT),
    Step("balcony", "choice", ASK_BALCONY, BALCONY),
    Step("height", "number", ASK_HEIGHT, presets=["2.5", "2.7", "3.0", "Другое…"], number_kind="float", hint_units="м",),
]

COUNTRY_FLOW: List[Step] = [
    Step("country_kind", "choice", ASK_COUNTRY_KIND, COUNTRY_KIND),

    # Дом/Дача/Коттедж/Таунхаус
    Step("house_sq", "number", ASK_HOUSE_SQ, presets=["80", "120", "180", "Другое…"], number_kind="float", hint_units="м²", depend=is_country_house_branch),
    Step("plot_sot", "number", ASK_PLOT_SOT, presets=["6", "10", "15", "20", "Другое…"], number_kind="float", hint_units="сот.", depend=is_country_house_branch),
    Step("distance", "number", ASK_DISTANCE, presets=["5", "10", "20", "30", "Другое…"], number_kind="int", hint_units="км", depend=is_country_house_branch),
    Step("storeys", "number", ASK_HOUSE_STOREYS, presets=["1", "2", "3", "Другое…"], number_kind="int", depend=is_country_house_branch),
    Step("rooms", "number", ASK_HOUSE_ROOMS, presets=["2", "3", "4", "5", "Другое…"], number_kind="int", depend=is_country_house_branch),
    Step("land_cat_house", "choice", ASK_LAND_CAT_HOUSE, LAND_CAT_HOUSE, depend=is_country_house_branch),
    Step("country_renov", "choice", ASK_COUNTRY_RENOV, RENOVATION, depend=is_country_house_branch),
    Step("toilet_country", "choice", ASK_TOILET_COUNTRY, TOILET_COUNTRY, depend=is_country_house_branch),
    Step("utils_country", "multichoice", ASK_UTILS_COUNTRY, UTILS_COUNTRY, depend=is_country_house_branch),
    Step("fun_country", "choice", ASK_FUN_COUNTRY, FUN_COUNTRY, depend=is_country_house_branch),
    Step("walls", "choice", ASK_WALLS, WALLS, depend=is_country_house_branch),
    Step("parking_country", "choice", ASK_PARKING_COUNTRY, PARKING_COUNTRY, depend=is_country_house_branch),
    Step("access", "choice", ASK_ACCESS, ACCESS, depend=is_country_house_branch),

    # Участок
    Step("land_cat_plot", "choice", ASK_LAND_CAT_PLOT, LAND_CAT_PLOT, depend=is_country_plot_branch),
    Step("plot_sot_only", "number", ASK_PLOT_SOT, presets=["6", "10", "15", "20", "Другое…"], number_kind="float", hint_units="сот.", depend=is_country_plot_branch),
    Step("distance_only", "number", ASK_DISTANCE, presets=["5", "10", "20", "30", "Другое…"], number_kind="int", hint_units="км", depend=is_country_plot_branch),
    Step("utils_plot", "multichoice", ASK_UTILS_PLOT, UTILS_PLOT, depend=is_country_plot_branch),
]

COMM_FLOW: List[Step] = [
    Step("comm_kind", "choice", ASK_COMM_KIND, COMM_KINDS),
    Step("comm_area", "number", ASK_COMM_AREA, presets=["50", "100", "200", "500", "Другое…"], number_kind="float", hint_units="м²"),
    Step("comm_land", "number", ASK_COMM_LAND, presets=COMM_LAND_PRESETS, number_kind="float", hint_units="сот./м²", depend=show_comm_land),
    Step("comm_building", "choice", ASK_COMM_BUILDING, COMM_BUILDING),
    Step("comm_whole", "choice", ASK_COMM_WHOLE, YES_NO),
    Step("finish", "choice", ASK_FINISH, FINISH),
    Step("entrance", "choice", ASK_ENTRANCE, ENTRANCE),
    Step("comm_parking", "choice", ASK_COMM_PARKING, COMM_PARKING),
    Step("layout_comm", "choice", ASK_LAYOUT_COMM, LAYOUT_COMM),
]

# ──────────────────────────────────────────────────────────────────────────────
# CORE: переходы, навигация, рендер клавиатур, сбор payload
# ──────────────────────────────────────────────────────────────────────────────

def get_flow(data: Dict[str, Any]) -> Tuple[str, List[Step]]:
    root = data.get("__root")
    if root == "Квартира":
        return root, FLAT_FLOW
    if root == "Загородная":
        return root, COUNTRY_FLOW
    if root == "Коммерческая":
        return root, COMM_FLOW
    return "", []

def visible_steps(flow: List[Step], data: Dict[str, Any]) -> List[Step]:
    out: List[Step] = []
    for st in flow:
        if st.depend is None or st.depend(data):
            out.append(st)
    return out

async def show_root(message: Message) -> None:
    text = f"{_h(INTRO)}\n\n<b>{_h(ASK_OBJECT_TYPE)}</b>"
    kb = make_kb_single(OBJECT_TYPES, "root")
    await safe_edit_or_send(message, text, kb)

async def safe_edit_or_send(message: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except TelegramBadRequest:
        await message.answer(text, reply_markup=kb, parse_mode="HTML")

def build_prompt(step: Step, data: Dict[str, Any]) -> Tuple[str, InlineKeyboardMarkup]:
    summary = split_summary({k: v for k, v in data.items() if not k.startswith("__")})
    header = f"<b>{_h(step.question)}</b>\n\n<i>{_h(summary)}</i>"
    if step.stype == "choice":
        return header, make_kb_single(step.options or [], step.key)
    if step.stype == "multichoice":
        selected = data.get(step.key, []) or []
        return header, make_kb_multiselect(step.options or [], step.key, selected)
    if step.stype == "number":
        kb = make_kb_numeric_presets(step.presets or [], step.key, allow_own=True)
        details = f"Формат: {step.number_kind or 'число'}"
        if step.hint_units:
            details += f" ({step.hint_units})"
        return f"{header}\n\n{_h(details)}", kb
    # text
    return header, make_kb_single([BTN_ENTER_OWN], step.key)
# ──────────────────────────────────────────────────────────────────────────────
# РОУТЕР И ХЕНДЛЕРЫ
# ──────────────────────────────────────────────────────────────────────────────

rt = Router()

@rt.callback_query(F.data == "nav.description")  # entry point по меню
async def start_wizard(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(PropertyWizard.choosing_root)
    await show_root(cb.message)
    await cb.answer()

@rt.callback_query(PropertyWizard.choosing_root, F.data.startswith("root:"))
async def choose_root(cb: CallbackQuery, state: FSMContext):
    _, value = cb.data.split(":", 1)
    await state.update_data(__root=value, __idx=0)
    # первый видимый шаг ветки
    data = await state.get_data()
    _, flow = get_flow(data)
    steps = visible_steps(flow, data)
    if not steps:
        await finalize(cb.message, state)
        await cb.answer()
        return
    await state.set_state(PropertyWizard.answering)
    text, kb = build_prompt(steps[0], data)
    await safe_edit_or_send(cb.message, text, kb)
    await cb.answer()

# Управление навигацией
@rt.callback_query(PropertyWizard.answering, F.data == "nav:reset")
@rt.callback_query(PropertyWizard.multiselect, F.data == "nav:reset")
async def on_reset(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(PropertyWizard.choosing_root)
    await show_root(cb.message)
    await cb.answer("Сброшено.")

@rt.callback_query(PropertyWizard.answering, F.data == "nav:skip")
@rt.callback_query(PropertyWizard.multiselect, F.data == "nav:skip")
async def on_skip(cb: CallbackQuery, state: FSMContext):
    await go_next(cb.message, state, skip=True)
    await cb.answer("Пропущено.")

@rt.callback_query(PropertyWizard.answering, F.data == "nav:back")
@rt.callback_query(PropertyWizard.multiselect, F.data == "nav:back")
async def on_back(cb: CallbackQuery, state: FSMContext):
    await go_prev(cb.message, state)
    await cb.answer()

# Выбор для choice/number пресеты/own
@rt.callback_query(PropertyWizard.answering, F.data.regexp(r"^([a-zA-Z_]+):(.*)$"))
async def on_click_answer(cb: CallbackQuery, state: FSMContext):
    key, value = cb.data.split(":", 1)
    data = await state.get_data()
    _, flow = get_flow(data)
    steps = visible_steps(flow, data)
    idx = int(data.get("__idx", 0))
    if idx >= len(steps) or steps[idx].key != key:
        await cb.answer()
        return
    step = steps[idx]

    # own input switch
    if value == "__own":
        # попросим ввести вручную
        await cb.message.answer("Введите значение сообщением. Формат — число/текст по вопросу.")
        await cb.answer()
        return

    # фиксируем выбранное значение
    stored: Any = value
    if step.stype == "number":
        # «Нет» в площадях участка → сохраняем 0 / None
        if key == "comm_land" and value == "Нет":
            stored = None
        else:
            if step.number_kind == "int":
                parsed = try_parse_int(value)
                if parsed is None:
                    await cb.answer("Нужно число", show_alert=False)
                    return
                stored = parsed
            else:
                parsed = try_parse_float(value)
                if parsed is None:
                    await cb.answer("Нужно число", show_alert=False)
                    return
                stored = parsed

    await state.update_data(**{key: stored})

    # multichoice переключается в другом хендлере
    await go_next(cb.message, state)
    await cb.answer()

# Собираем «своё» значение числом или текстом
@rt.message(PropertyWizard.answering, F.text)
async def on_text_answer(msg: Message, state: FSMContext):
    data = await state.get_data()
    _, flow = get_flow(data)
    steps = visible_steps(flow, data)
    idx = int(data.get("__idx", 0))
    if idx >= len(steps):
        await msg.answer("Неожиданный ввод. Продолжим.")
        await finalize(msg, state)
        return
    step = steps[idx]
    raw = (msg.text or "").strip()

    if step.stype == "number":
        if step.number_kind == "int":
            val = try_parse_int(raw)
            if val is None:
                await msg.answer("Введите целое число.")
                return
        else:
            val = try_parse_float(raw)
            if val is None:
                await msg.answer("Введите число (0.0).")
                return
        await state.update_data(**{step.key: val})
    else:
        await state.update_data(**{step.key: raw})

    await go_next(msg, state)

# Мультивыбор: тэг-кнопки
@rt.callback_query(PropertyWizard.multiselect, F.data.regexp(r"^([a-zA-Z_]+):(.*)$"))
async def on_multiselect_toggle(cb: CallbackQuery, state: FSMContext):
    key, value = cb.data.split(":", 1)
    if value == "ok":  # Готово
        await state.set_state(PropertyWizard.answering)
        await go_next(cb.message, state)
        await cb.answer("Сохранено.")
        return

    data = await state.get_data()
    arr: List[str] = list(data.get(key, []) or [])
    if value in arr:
        arr.remove(value)
    else:
        arr.append(value)
    await state.update_data(**{key: arr})

    # перерисовать клавиатуру
    _, flow = get_flow(data)
    steps = visible_steps(flow, data)
    idx = int(data.get("__idx", 0))
    step = steps[idx]
    text, kb = build_prompt(step, await state.get_data())
    await safe_edit_or_send(cb.message, text, kb)
    await cb.answer()

# ──────────────────────────────────────────────────────────────────────────────
# Переходы и финал
# ──────────────────────────────────────────────────────────────────────────────

async def go_next(msg: Message, state: FSMContext, skip: bool = False):
    data = await state.get_data()
    root, flow = get_flow(data)
    steps = visible_steps(flow, data)
    idx = int(data.get("__idx", 0))

    if skip:
        # ничего не сохраняем, просто шагаем дальше
        idx += 1
    else:
        idx += 1

    if idx >= len(steps):
        await finalize(msg, state)
        return

    await state.update_data(__idx=idx)

    step = steps[idx]
    # если следующий шаг — мультивыбор
    if step.stype == "multichoice":
        await state.set_state(PropertyWizard.multiselect)
    else:
        await state.set_state(PropertyWizard.answering)

    text, kb = build_prompt(step, await state.get_data())
    await safe_edit_or_send(msg, text, kb)

async def go_prev(msg: Message, state: FSMContext):
    data = await state.get_data()
    _, flow = get_flow(data)
    steps = visible_steps(flow, data)
    idx = max(0, int(data.get("__idx", 0)) - 1)
    await state.update_data(__idx=idx)

    step = steps[idx]
    if step.stype == "multichoice":
        await state.set_state(PropertyWizard.multiselect)
    else:
        await state.set_state(PropertyWizard.answering)

    text, kb = build_prompt(step, await state.get_data())
    await safe_edit_or_send(msg, text, kb)

async def finalize(msg: Message, state: FSMContext):
    data = await state.get_data()
    payload = build_payload(data)
    summary = render_summary(payload)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Начать заново", callback_data="nav:reset")
    ]])

    try:
        await msg.bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
    except Exception:
        pass

    def _render_json_for_html(obj: Dict[str, Any]) -> str:
        import json
        return _h(json.dumps(obj, ensure_ascii=False, indent=2))

    json_block = _render_json_for_html(payload)
    text = (
        "Готово! Собрали предварительную информацию по объекту.\n\n"
        f"{_h(summary)}\n\n"
        "<b>JSON payload:</b>\n"
        f"<pre><code>{json_block}</code></pre>"
    )
    await msg.answer(text, reply_markup=kb, parse_mode="HTML")
    await state.set_state(PropertyWizard.finish)

# ──────────────────────────────────────────────────────────────────────────────
# Сводка и JSON
# ──────────────────────────────────────────────────────────────────────────────

def build_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    root = data.get("__root")
    payload: Dict[str, Any] = {"category": root}

    # Квартира
    if root == "Квартира":
        payload.update({
            "market": data.get("flat_market"),                               # Новостройка/Вторичка
            "deadline": data.get("newbuild_deadline"),                       # если новостройка
            "sale_type": data.get("newbuild_sale"),                          # если новостройка
            "rooms": data.get("rooms"),
            "mortgage": data.get("mortgage"),
            "total_area_m2": data.get("total_area"),
            "kitchen_area_m2": data.get("kitchen_area"),
            "floor": data.get("floor"),
            "floors_total": data.get("floors_total"),
            "bath": data.get("bath"),
            "windows": data.get("windows"),
            "house_type": data.get("house_type"),
            "elevator": data.get("elevator"),
            "parking": data.get("parking"),
            "renovation": data.get("renovation"),
            "layout": data.get("layout"),
            "balcony": data.get("balcony"),
            "height_m": data.get("height"),
        })

    # Загородная
    elif root == "Загородная":
        kind = data.get("country_kind")
        payload.update({"kind": kind})
        if is_country_house_branch(data):
            payload.update({
                "house_area_m2": data.get("house_sq"),
                "plot_area_sot": data.get("plot_sot"),
                "distance_km": data.get("distance"),
                "storeys": data.get("storeys"),
                "rooms": data.get("rooms"),
                "land_category": data.get("land_cat_house"),
                "renovation": data.get("country_renov"),
                "toilet": data.get("toilet_country"),
                "utilities": data.get("utils_country"),
                "recreation": data.get("fun_country"),
                "walls": data.get("walls"),
                "parking": data.get("parking_country"),
                "access": data.get("access"),
            })
        elif is_country_plot_branch(data):
            payload.update({
                "land_category": data.get("land_cat_plot"),
                "plot_area_sot": data.get("plot_sot_only"),
                "distance_km": data.get("distance_only"),
                "utilities": data.get("utils_plot"),
            })

    # Коммерческая
    elif root == "Коммерческая":
        payload.update({
            "kind": data.get("comm_kind"),
            "area_m2": data.get("comm_area"),
            "land_area": data.get("comm_land"),  # может быть None (Нет)
            "building_type": data.get("comm_building"),
            "whole_object": data.get("comm_whole"),
            "finish": data.get("finish"),
            "entrance": data.get("entrance"),
            "parking": data.get("comm_parking"),
            "layout": data.get("layout_comm"),
        })

    # очищаем служебные ключи
    return {k: v for k, v in payload.items() if v not in (None, "", [], {})}

def render_summary(p: Dict[str, Any]) -> str:
    lines = []
    for k, v in p.items():
        if isinstance(v, list):
            vv = ", ".join(map(str, v))
        else:
            vv = str(v)
        lines.append(f"• {k}: {vv}")
    return "\n".join(lines) if lines else "Пусто."

# ──────────────────────────────────────────────────────────────────────────────
# ПУБЛИЧНАЯ ТОЧКА ПОДКЛЮЧЕНИЯ
# ──────────────────────────────────────────────────────────────────────────────

def router() -> Router:
    """
    Подключите в приложение:
        from smart_agent.bot.handlers.property_wizard import router as property_router
        dp.include_router(property_router())
    Старт экрана: отправьте кнопку с callback_data="nav.description"
    """
    return rt
