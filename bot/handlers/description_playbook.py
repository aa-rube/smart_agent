# smart_agent/bot/handlers/description_playbook.py
# Всегда пиши код без «поддержки старых версий». Если они есть - удаляй.

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple, Callable

import re
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

# ======================================================================
# Константы UI
# ======================================================================

TITLE_INTRO = (
    "Заполним короткую анкету и соберём структурированное описание объекта.\n"
    "Отвечайте по шагам — кнопками или числом. В любой момент можно:\n"
    "• ⬅️ Назад  • ⏭ Пропустить  • 🔄 Сброс\n\n"
    "Какой тип объекта?"
)
BTN_BACK = "⬅️ Назад"
BTN_SKIP = "⏭ Пропустить"
BTN_RESET = "🔄 Сброс"
BTN_DONE = "✅ Готово"
BTN_OTHER = "Ввести своё"

# Разделы верхнего уровня
TYPE_APT = "Квартира"
TYPE_COUNTRY = "Загородная"
TYPE_COMM = "Коммерческая"

# Вспомогательная пометка для FSM
AWAIT_TEXT_KEY = "__await_text_key__"
STACK = "__stack__"            # стек узлов для «Назад»
PAYLOAD = "__payload__"        # собранные данные
MULTI_TEMP = "__multi_temp__"  # временное хранилище для множественного выбора

# Для коммерческой логики
COMM_NEEDS_LAND_TYPES = {"Склад", "Производство", "Гостиница"}  # где потенциально уместен участок

# ======================================================================
# Состояния FSM
# ======================================================================

class Playbook(StatesGroup):
    idle = State()
    in_flow = State()       # основной поток (callback-и)
    waiting_text = State()  # ожидание ручного ввода для конкретного ключа

# ======================================================================
# Модель полезной нагрузки
# ======================================================================

@dataclass
class ResultModel:
    category: Optional[str] = None                 # Квартира / Загородная / Коммерческая
    # Квартира:
    apt_market: Optional[str] = None               # Новостройка / Вторичка
    apt_deadline: Optional[str] = None             # только новостройка
    apt_sale_method: Optional[str] = None          # только новостройка
    apt_rooms: Optional[str] = None                # Студия/1/2/3/4+
    apt_mortgage: Optional[str] = None             # Да/Нет
    apt_total_area: Optional[float] = None
    apt_kitchen_area: Optional[float] = None
    apt_floor: Optional[int] = None
    apt_floors_total: Optional[int] = None
    apt_bathroom: Optional[str] = None             # Совмещённый/Раздельный
    apt_windows: Optional[str] = None
    apt_house_type: Optional[str] = None           # Кирпич/Панель/...
    apt_lift: Optional[str] = None                 # Нет/Пасс/Груз/Оба
    apt_parking: Optional[str] = None              # Подземная/...
    apt_renovation: Optional[str] = None           # Требуется/Косметический/Евро/Диз
    apt_layout: Optional[str] = None               # Изолированные/Смежные/Смешанные
    apt_balcony: Optional[str] = None              # Нет/Балкон/Лоджия/Несколько
    apt_ceil_height: Optional[float] = None        # опционально

    # Загородная:
    country_kind: Optional[str] = None             # Дом/Дача/Коттедж/Таунхаус или Участок
    # Ветка «Дом/Дача/Коттедж/Таунхаус»
    house_area: Optional[float] = None
    land_area_sot: Optional[float] = None
    distance_km: Optional[float] = None
    house_floors: Optional[int] = None
    house_rooms: Optional[str] = None
    land_cat: Optional[str] = None                 # ИЖС/Сад/ЛПХ/КФХ/Иное
    house_state: Optional[str] = None
    house_wc: Optional[str] = None                 # В доме/На улице/Оба
    comms: List[str] = field(default_factory=list) # Электричество/Газ/Отопление/Вода/Канализация
    leisure: List[str] = field(default_factory=list) # Баня/Бассейн/Сауна/Другое
    wall_mat: Optional[str] = None                 # Кирпич/Брус/...
    country_parking: Optional[str] = None          # Гараж/Место/Навес/Нет
    access: Optional[str] = None                   # Асфальт/Остановки/ЖД/Грунтовка

    # Ветка «Участок»
    lot_land_cat: Optional[str] = None             # Поселения/Сельхоз/Пром/Иное
    lot_area_sot: Optional[float] = None
    lot_distance_km: Optional[float] = None
    lot_comms: List[str] = field(default_factory=list)  # Газ/Вода/Свет/По границе/Нет

    # Коммерческая:
    comm_type: Optional[str] = None                # Офис/ПСН/...
    comm_area: Optional[float] = None
    comm_land_area: Optional[float] = None         # при необходимости
    comm_building_type: Optional[str] = None       # БЦ/ТЦ/...
    comm_whole: Optional[str] = None               # Да/Нет
    comm_finish: Optional[str] = None              # Без/Черновая/Чистовая/Офисная
    comm_entrance: Optional[str] = None            # С улицы/Со двора/Отдельный второй
    comm_parking: Optional[str] = None             # Нет/Улица/Крытая/Подземная/Гостевая
    comm_layout: Optional[str] = None              # Open/cab/mixed

# ======================================================================
# Навигационные узлы и сценарий
# ======================================================================

# Узел: (question_text, keyboard_builder | None, handler)
# handler = функция, обрабатывающая нажатия/ввод и переводящая к следующему узлу.
# Для числовых полей используем пресеты + «Ввести своё» → waiting_text.

NodeId = str
NextResolver = Callable[[ResultModel], Optional[NodeId]]

# ------------------------------------------
# Утилиты клавиатур
# ------------------------------------------

def kb_rows(rows: list[list[tuple[str, str]]], add_nav: bool = True) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    for row in rows:
        keyboard.append([InlineKeyboardButton(text=txt, callback_data=cb) for txt, cb in row])

    if add_nav:
        keyboard.append([
            InlineKeyboardButton(text=BTN_BACK, callback_data="act:back"),
            InlineKeyboardButton(text=BTN_SKIP, callback_data="act:skip"),
            InlineKeyboardButton(text=BTN_RESET, callback_data="act:reset"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def kb_simple(options: List[str], key: str, cols: int = 2, add_nav=True) -> InlineKeyboardMarkup:
    rows: List[List[Tuple[str, str]]] = []
    row: List[Tuple[str, str]] = []
    for opt in options:
        row.append((opt, f"pick:{key}:{opt}"))
        if len(row) >= cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return kb_rows(rows, add_nav=add_nav)

def kb_numeric_presets(key: str, presets: List[str]) -> InlineKeyboardMarkup:
    rows: List[List[Tuple[str, str]]] = []
    row: List[Tuple[str, str]] = []
    for opt in presets:
        row.append((opt, f"num:{key}:{opt}"))
        if len(row) == 4:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([(BTN_OTHER, f"num:{key}:__other__")])
    return kb_rows(rows)

def kb_tagset(current: List[str], all_opts: List[str], key: str) -> InlineKeyboardMarkup:
    rows: List[List[Tuple[str, str]]] = []
    buf: List[Tuple[str, str]] = []
    for opt in all_opts:
        on = "●" if opt in current else "○"
        label = f"{on} {opt}"
        buf.append((label, f"tag:{key}:{opt}"))
        if len(buf) == 2:
            rows.append(buf); buf = []
    if buf:
        rows.append(buf)
    rows.append([(BTN_DONE, f"tag_done:{key}")])
    return kb_rows(rows)

# ------------------------------------------
# Карточки/вопросы
# ------------------------------------------

# Верхний уровень
ROOT_QUESTION = "Какой тип объекта?"
ROOT_KB = kb_simple([TYPE_APT, TYPE_COUNTRY, TYPE_COMM], "root", cols=1)

# Квартира
APT_MARKET_Q = "Рынок квартиры?"
APT_MARKET_KB = kb_simple(["Новостройка", "Вторичка"], "apt_market", cols=2)

APT_DEADLINE_Q = "Срок сдачи?"
APT_DEADLINE_KB = kb_simple(["Q4-2025", "2026", "2027", "Другое…"], "apt_deadline", cols=2)

APT_SALE_METHOD_Q = "Способ продажи?"
APT_SALE_METHOD_KB = kb_simple(["ДКП", "Переуступка", "ФЗ-214"], "apt_sale")

APT_ROOMS_Q = "Количество комнат?"
APT_ROOMS_KB = kb_simple(["Студия", "1", "2", "3", "4+"], "apt_rooms")

APT_MORTGAGE_Q = "Подходит для ипотеки?"
APT_MORTGAGE_KB = kb_simple(["Да", "Нет"], "apt_mortgage", cols=2)

APT_TOTAL_Q = "Укажите общую площадь (м²)"
APT_TOTAL_KB = kb_numeric_presets("apt_total_area", ["30", "40", "50"])

APT_KITCHEN_Q = "Площадь кухни (м²)"
APT_KITCHEN_KB = kb_numeric_presets("apt_kitchen_area", ["6", "8", "10", "12"])

APT_FLOOR_Q = "Этаж квартиры?"
APT_FLOOR_KB = kb_numeric_presets("apt_floor", ["1", "2", "3", "4", "5"])

APT_FLOORS_TOT_Q = "Сколько этажей в доме?"
APT_FLOORS_TOT_KB = kb_numeric_presets("apt_floors_total", ["5", "9", "12", "16"])

APT_BATH_Q = "Санузел?"
APT_BATH_KB = kb_simple(["Совмещённый", "Раздельный"], "apt_bathroom")

APT_WINDOWS_Q = "Куда выходят окна?"
APT_WINDOWS_KB = kb_simple(["Во двор", "На улицу", "На солнечную", "Разное"], "apt_windows", cols=2)

APT_HOUSE_TYPE_Q = "Тип дома?"
APT_HOUSE_TYPE_KB = kb_simple(["Кирпич", "Панель", "Блочный", "Монолит", "Монолит-кирпич"], "apt_house_type", cols=2)

APT_LIFT_Q = "Лифт?"
APT_LIFT_KB = kb_simple(["Нет", "Пассажирский", "Грузовой", "Оба"], "apt_lift", cols=2)

APT_PARK_Q = "Парковка?"
APT_PARK_KB = kb_simple(["Подземная", "Наземная", "Многоуровневая", "Двор", "Двор со шлагбаумом"], "apt_parking", cols=1)

APT_RENOV_Q = "Состояние ремонта?"
APT_RENOV_KB = kb_simple(["Требуется", "Косметический", "Евро", "Дизайнерский"], "apt_renovation", cols=2)

APT_LAYOUT_Q = "Планировка комнат?"
APT_LAYOUT_KB = kb_simple(["Изолированные", "Смежные", "Смешанные"], "apt_layout", cols=1)

APT_BALCONY_Q = "Балкон или лоджия?"
APT_BALCONY_KB = kb_simple(["Нет", "Балкон", "Лоджия", "Несколько"], "apt_balcony", cols=2)

APT_CEIL_Q = "Высота потолков (м)?"
APT_CEIL_KB = kb_numeric_presets("apt_ceil_height", ["2.5", "2.7", "3.0"])

# Загородная
COUNTRY_KIND_Q = "Загородная → Тип объекта"
COUNTRY_KIND_KB = kb_simple(["Дом", "Дача", "Коттедж", "Таунхаус", "Участок"], "country_kind", cols=1)

# Ветка «Дом/Дача/Коттедж/Таунхаус»
HOUSE_AREA_Q = "Площадь дома (м²)?"
HOUSE_AREA_KB = kb_numeric_presets("house_area", ["80", "120", "180"])

LAND_AREA_SOT_Q = "Площадь участка (сот.)?"
LAND_AREA_SOT_KB = kb_numeric_presets("land_area_sot", ["6", "10", "15", "20"])

DIST_KM_Q = "Расстояние от города (км)?"
DIST_KM_KB = kb_numeric_presets("distance_km", ["5", "10", "20", "30"])

HOUSE_FLOORS_Q = "Этажей в доме?"
HOUSE_FLOORS_KB = kb_numeric_presets("house_floors", ["1", "2", "3"])

HOUSE_ROOMS_Q = "Комнат?"
HOUSE_ROOMS_KB = kb_simple(["2", "3", "4", "5+"], "house_rooms", cols=2)

LAND_CAT_Q = "Категория земель?"
LAND_CAT_KB = kb_simple(["ИЖС", "Сад", "ЛПХ", "КФХ", "Иное"], "land_cat", cols=2)

HOUSE_STATE_Q = "Состояние/ремонт?"
HOUSE_STATE_KB = kb_simple(["Требуется", "Косметический", "Евро", "Дизайнерский"], "house_state", cols=2)

HOUSE_WC_Q = "Санузел?"
HOUSE_WC_KB = kb_simple(["В доме", "На улице", "Оба"], "house_wc", cols=1)

COMMS_Q = "Коммуникации? (множественный выбор)"
COMMS_ALL = ["Электричество", "Газ", "Отопление", "Вода", "Канализация"]

LEISURE_Q = "Для отдыха? (множественный выбор)"
LEISURE_ALL = ["Баня", "Бассейн", "Сауна", "Другое"]

WALL_MAT_Q = "Материал стен?"
WALL_MAT_KB = kb_simple(["Кирпич", "Брус", "Бревно", "Газоблок", "Металл", "Иное"], "wall_mat", cols=2)

COUNTRY_PARK_Q = "Парковка?"
COUNTRY_PARK_KB = kb_simple(["Гараж", "Место", "Навес", "Нет"], "country_parking", cols=2)

ACCESS_Q = "Транспортная доступность?"
ACCESS_KB = kb_simple(["Асфальт", "Остановки ОТ", "ЖД станция", "Грунтовка"], "access", cols=2)

# Ветка «Участок»
LOT_LAND_CAT_Q = "Категория земель?"
LOT_LAND_CAT_KB = kb_simple(["Поселения", "Сельхоз", "Пром", "Иное"], "lot_land_cat", cols=2)

LOT_AREA_Q = "Площадь участка (сот.)?"
LOT_AREA_KB = kb_numeric_presets("lot_area_sot", ["6", "10", "15", "20"])

LOT_DIST_Q = "Расстояние до города (км)?"
LOT_DIST_KB = kb_numeric_presets("lot_distance_km", ["5", "10", "20", "30"])

LOT_COMMS_Q = "Коммуникации? (множественный выбор)"
LOT_COMMS_ALL = ["Газ", "Вода", "Свет", "По границе", "Нет"]

# Коммерческая
COMM_TYPE_Q = "Вид объекта?"
COMM_TYPE_KB = kb_simple(["Офис", "ПСН", "Торговая", "Склад", "Производство", "Общепит", "Гостиница"], "comm_type", cols=1)

COMM_AREA_Q = "Площадь (м²)?"
COMM_AREA_KB = kb_numeric_presets("comm_area", ["50", "100", "200", "500"])

COMM_LAND_AREA_Q = "Площадь участка (если есть)?"
COMM_LAND_AREA_KB = kb_simple(["Нет", "2", "5", "10", "Другое…"], "comm_land_area", cols=2)

COMM_BUILDING_Q = "Тип здания?"
COMM_BUILDING_KB = kb_simple(["БЦ", "ТЦ", "Админздание", "Жилой дом", "Другое"], "comm_building_type", cols=2)

COMM_WHOLE_Q = "Объект целиком?"
COMM_WHOLE_KB = kb_simple(["Да", "Нет"], "comm_whole", cols=2)

COMM_FINISH_Q = "Отделка?"
COMM_FINISH_KB = kb_simple(["Без", "Черновая", "Чистовая", "Офисная"], "comm_finish", cols=2)

COMM_ENTR_Q = "Вход?"
COMM_ENTR_KB = kb_simple(["С улицы", "Со двора", "Отдельный второй вход"], "comm_entrance", cols=1)

COMM_PARK_Q = "Парковка?"
COMM_PARK_KB = kb_simple(["Нет", "Улица", "Крытая", "Подземная", "Гостевая"], "comm_parking", cols=2)

COMM_LAYOUT_Q = "Планировка?"
COMM_LAYOUT_KB = kb_simple(["Open space", "Кабинетная", "Смешанная"], "comm_layout", cols=1)

# ======================================================================
# Роутер
# ======================================================================

rt = Router()

# ======================================================================
# Вспомогательные функции
# ======================================================================

def _progress_line(m: ResultModel) -> str:
    filled = sum(1 for v in asdict(m).values() if v not in (None, [], ""))
    return f"🧩 Заполнено полей: {filled} • Категория: {m.category or '—'}"

async def _show(message_or_cb, text: str, kb: InlineKeyboardMarkup):
    """Аккуратно обновляем текущее сообщение; если нельзя — отправляем новое."""
    try:
        await message_or_cb.message.edit_text(f"{text}\n\n{_progress_line(ResultModel(**(await message_or_cb.bot.session.state.get_data(message_or_cb.from_user.id) or {})))}", reply_markup=kb)
    except Exception:

        if isinstance(message_or_cb, CallbackQuery):
            await message_or_cb.message.answer(f"{text}", reply_markup=kb)
        else:
            await message_or_cb.answer(f"{text}", reply_markup=kb)

async def _edit(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup):
    try:
        await cb.message.edit_text(f"{text}\n\n{_progress_line(ResultModel(**(await cb.bot.session.state.get_data(cb.from_user.id) or {})))}", reply_markup=kb)
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=kb)

async def _send(msg: Message, text: str, kb: InlineKeyboardMarkup):
    await msg.answer(text, reply_markup=kb)

def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "."))
    except Exception:
        return None

async def _push(state: FSMContext, node: NodeId):
    data = await state.get_data()
    stack = data.get(STACK, [])
    stack.append(node)
    await state.update_data(**{STACK: stack})

async def _pop(state: FSMContext) -> Optional[NodeId]:
    data = await state.get_data()
    stack = data.get(STACK, [])
    if not stack:
        return None
    stack.pop()
    await state.update_data(**{STACK: stack})
    return stack[-1] if stack else None

async def _goto(cb: CallbackQuery, state: FSMContext, node: NodeId, text: str, kb: InlineKeyboardMarkup):
    await _push(state, node)
    await _edit(cb, f"{text}\n\n{_progress_line(ResultModel(**(await state.get_data()).get(PAYLOAD, {})))}", kb)

async def _goto_msg(msg: Message, state: FSMContext, node: NodeId, text: str, kb: InlineKeyboardMarkup):
    await _push(state, node)
    await _send(msg, text, kb)

def _payload_to_model(payload: Dict) -> ResultModel:
    # Преобразуем спасённый payload в dataclass
    safe = {}
    for k, v in payload.items():
        safe[k] = v
    return ResultModel(**safe)

# ======================================================================
# Старт
# ======================================================================

@rt.message(F.text == "/description")
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Playbook.in_flow)
    await state.update_data(**{PAYLOAD: ResultModel().__dict__, STACK: []})
    await _goto_msg(message, state, "root", TITLE_INTRO, ROOT_KB)

@rt.callback_query(F.data == "act:reset", Playbook.in_flow)
async def act_reset(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(Playbook.in_flow)
    await state.update_data(**{PAYLOAD: ResultModel().__dict__, STACK: []})
    await _edit(cb, TITLE_INTRO, ROOT_KB)
    await cb.answer("Форма сброшена")

@rt.callback_query(F.data == "act:back", Playbook.in_flow)
async def act_back(cb: CallbackQuery, state: FSMContext):
    prev = await _pop(state)
    if not prev:
        await _edit(cb, TITLE_INTRO, ROOT_KB)
        await cb.answer()
        return
    # Показываем соответствующий вопрос предыдущего узла
    await render_node(cb, state, prev, from_back=True)
    await cb.answer()

@rt.callback_query(F.data == "act:skip", Playbook.in_flow)
async def act_skip(cb: CallbackQuery, state: FSMContext):
    # Переходим к следующему по сценарию от текущего узла, ничего не записывая
    data = await state.get_data()
    stack = data.get(STACK, [])
    current = stack[-1] if stack else "root"
    await go_next(cb, state, current, skipped=True)
    await cb.answer("Пропущено")

# ======================================================================
# Основная логика выбора
# ======================================================================

@rt.callback_query(F.data.startswith("pick:"), Playbook.in_flow)
async def on_pick(cb: CallbackQuery, state: FSMContext):
    # pick:key:value
    _, key, value = cb.data.split(":", 2)
    data = await state.get_data()
    payload: Dict = data.get(PAYLOAD, {})
    payload[key_to_payload(key)] = as_value(key, value)
    await state.update_data(**{PAYLOAD: payload})

    # Особые случаи для ветвления
    if key == "root":
        payload["category"] = value
        await state.update_data(**{PAYLOAD: payload, STACK: []})
        await _push(state, "root")
        await render_node(cb, state, "root")
        await cb.answer(f"Выбрано: {value}")
        return

    await go_next(cb, state, current_node_of(state), skipped=False)
    await cb.answer(f"Выбрано: {value}")

@rt.callback_query(F.data.startswith("num:"), Playbook.in_flow)
async def on_numeric(cb: CallbackQuery, state: FSMContext):
    # num:key:value   (value == __other__ → ждём текст)
    _, key, value = cb.data.split(":", 2)
    if value == "__other__":
        await state.update_data(**{AWAIT_TEXT_KEY: key})
        await state.set_state(Playbook.waiting_text)
        try:
            await cb.message.edit_text(
                f"Введите число для «{key_human(key)}» (допустимы целые или дробные, точка/запятая).",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=BTN_BACK, callback_data="act:back"),
                     InlineKeyboardButton(text=BTN_RESET, callback_data="act:reset")]
                ])
            )
        except TelegramBadRequest:
            await cb.message.answer(
                f"Введите число для «{key_human(key)}».",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=BTN_BACK, callback_data="act:back"),
                     InlineKeyboardButton(text=BTN_RESET, callback_data="act:reset")]
                ])
            )
        await cb.answer()
        return

    num = _num(value)
    if num is None:
        await cb.answer("Некорректное число", show_alert=True)
        return
    data = await state.get_data()
    payload: Dict = data.get(PAYLOAD, {})
    payload[key_to_payload(key)] = num
    await state.update_data(**{PAYLOAD: payload})

    await go_next(cb, state, current_node_of(state), skipped=False)
    await cb.answer("Значение установлено")

@rt.message(Playbook.waiting_text, F.text)
async def on_text_number(message: Message, state: FSMContext):
    data = await state.get_data()
    key = data.get(AWAIT_TEXT_KEY)
    if not key:
        await state.set_state(Playbook.in_flow)
        await message.answer("Вернёмся к анкете.")
        return
    val = _num(message.text.strip())
    if val is None:
        await message.answer("⚠️ Нужен числовой формат. Пример: 56.4")
        return
    payload: Dict = data.get(PAYLOAD, {})
    payload[key_to_payload(key)] = val
    await state.update_data(**{PAYLOAD: payload, AWAIT_TEXT_KEY: None})
    await state.set_state(Playbook.in_flow)
    # после текстового ввода — показываем следующий узел
    await go_next_msg(message, state, current_node_of(state), skipped=False)

@rt.callback_query(F.data.startswith("tag:"), Playbook.in_flow)
async def on_tag_toggle(cb: CallbackQuery, state: FSMContext):
    # tag:key:option
    _, key, opt = cb.data.split(":", 2)
    data = await state.get_data()
    temp: Dict[str, List[str]] = data.get(MULTI_TEMP, {})
    current = set(temp.get(key, []))
    if opt in current:
        current.remove(opt)
    else:
        current.add(opt)
    temp[key] = list(current)
    await state.update_data(**{MULTI_TEMP: temp})
    # перерисовать клавиатуру тэгов
    kb = kb_tagset(temp.get(key, []), tag_all_options(key), key)
    try:
        await cb.message.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass
    await cb.answer()

@rt.callback_query(F.data.startswith("tag_done:"), Playbook.in_flow)
async def on_tag_done(cb: CallbackQuery, state: FSMContext):
    # tag_done:key
    _, key = cb.data.split(":", 1)
    data = await state.get_data()
    temp: Dict[str, List[str]] = data.get(MULTI_TEMP, {})
    selected = temp.get(key, [])
    payload: Dict = data.get(PAYLOAD, {})
    payload[key_to_payload(key)] = selected
    await state.update_data(**{PAYLOAD: payload})
    await go_next(cb, state, current_node_of(state), skipped=False)
    await cb.answer(f"Выбрано: {', '.join(selected) if selected else 'ничего'}")

# ======================================================================
# Рендеринг узлов и сценарий переходов
# ======================================================================

def current_node_of(state: FSMContext) -> NodeId:
    # NB: использовать только внутри async с await state.get_data()
    # здесь просто заглушка (функция не async), реальную ноду берём при вызове
    return ""  # не используется напрямую

async def render_node(cb: CallbackQuery, state: FSMContext, node: NodeId, from_back: bool = False):
    data = await state.get_data()
    payload: Dict = data.get(PAYLOAD, {})
    model = _payload_to_model(payload)

    # ROOT
    if node == "root":
        await _edit(cb, ROOT_QUESTION, ROOT_KB)
        return

    # Квартира
    if node == "apt.market":
        await _edit(cb, APT_MARKET_Q, APT_MARKET_KB); return
    if node == "apt.deadline":
        await _edit(cb, APT_DEADLINE_Q, APT_DEADLINE_KB); return
    if node == "apt.sale":
        await _edit(cb, APT_SALE_METHOD_Q, APT_SALE_METHOD_KB); return
    if node == "apt.rooms":
        await _edit(cb, APT_ROOMS_Q, APT_ROOMS_KB); return
    if node == "apt.mortgage":
        await _edit(cb, APT_MORTGAGE_Q, APT_MORTGAGE_KB); return
    if node == "apt.total":
        await _edit(cb, APT_TOTAL_Q, APT_TOTAL_KB); return
    if node == "apt.kitchen":
        await _edit(cb, APT_KITCHEN_Q, APT_KITCHEN_KB); return
    if node == "apt.floor":
        await _edit(cb, APT_FLOOR_Q, APT_FLOOR_KB); return
    if node == "apt.floors_total":
        await _edit(cb, APT_FLOORS_TOT_Q, APT_FLOORS_TOT_KB); return
    if node == "apt.bath":
        await _edit(cb, APT_BATH_Q, APT_BATH_KB); return
    if node == "apt.windows":
        await _edit(cb, APT_WINDOWS_Q, APT_WINDOWS_KB); return
    if node == "apt.house_type":
        await _edit(cb, APT_HOUSE_TYPE_Q, APT_HOUSE_TYPE_KB); return
    if node == "apt.lift":
        await _edit(cb, APT_LIFT_Q, APT_LIFT_KB); return
    if node == "apt.parking":
        await _edit(cb, APT_PARK_Q, APT_PARK_KB); return
    if node == "apt.renov":
        await _edit(cb, APT_RENOV_Q, APT_RENOV_KB); return
    if node == "apt.layout":
        await _edit(cb, APT_LAYOUT_Q, APT_LAYOUT_KB); return
    if node == "apt.balcony":
        await _edit(cb, APT_BALCONY_Q, APT_BALCONY_KB); return
    if node == "apt.ceil":
        await _edit(cb, APT_CEIL_Q, APT_CEIL_KB); return

    # Загородная
    if node == "country.kind":
        await _edit(cb, COUNTRY_KIND_Q, COUNTRY_KIND_KB); return

    # Дом/Дача/Коттедж/Таунхаус
    if node == "house.area":
        await _edit(cb, HOUSE_AREA_Q, HOUSE_AREA_KB); return
    if node == "house.land_area":
        await _edit(cb, LAND_AREA_SOT_Q, LAND_AREA_SOT_KB); return
    if node == "house.dist":
        await _edit(cb, DIST_KM_Q, DIST_KM_KB); return
    if node == "house.floors":
        await _edit(cb, HOUSE_FLOORS_Q, HOUSE_FLOORS_KB); return
    if node == "house.rooms":
        await _edit(cb, HOUSE_ROOMS_Q, HOUSE_ROOMS_KB); return
    if node == "house.land_cat":
        await _edit(cb, LAND_CAT_Q, LAND_CAT_KB); return
    if node == "house.state":
        await _edit(cb, HOUSE_STATE_Q, HOUSE_STATE_KB); return
    if node == "house.wc":
        await _edit(cb, HOUSE_WC_Q, HOUSE_WC_KB); return
    if node == "house.comms":
        current = data.get(MULTI_TEMP, {}).get("comms", payload.get("comms", []))
        await _edit(cb, COMMS_Q, kb_tagset(current, COMMS_ALL, "comms")); return
    if node == "house.leisure":
        current = data.get(MULTI_TEMP, {}).get("leisure", payload.get("leisure", []))
        await _edit(cb, LEISURE_Q, kb_tagset(current, LEISURE_ALL, "leisure")); return
    if node == "house.wall":
        await _edit(cb, WALL_MAT_Q, WALL_MAT_KB); return
    if node == "house.parking":
        await _edit(cb, COUNTRY_PARK_Q, COUNTRY_PARK_KB); return
    if node == "house.access":
        await _edit(cb, ACCESS_Q, ACCESS_KB); return

    # Участок
    if node == "lot.land_cat":
        await _edit(cb, LOT_LAND_CAT_Q, LOT_LAND_CAT_KB); return
    if node == "lot.area":
        await _edit(cb, LOT_AREA_Q, LOT_AREA_KB); return
    if node == "lot.dist":
        await _edit(cb, LOT_DIST_Q, LOT_DIST_KB); return
    if node == "lot.comms":
        current = data.get(MULTI_TEMP, {}).get("lot_comms", payload.get("lot_comms", []))
        await _edit(cb, LOT_COMMS_Q, kb_tagset(current, LOT_COMMS_ALL, "lot_comms")); return

    # Коммерческая
    if node == "comm.type":
        await _edit(cb, COMM_TYPE_Q, COMM_TYPE_KB); return
    if node == "comm.area":
        await _edit(cb, COMM_AREA_Q, COMM_AREA_KB); return
    if node == "comm.land_area":
        await _edit(cb, COMM_LAND_AREA_Q, COMM_LAND_AREA_KB); return
    if node == "comm.building":
        await _edit(cb, COMM_BUILDING_Q, COMM_BUILDING_KB); return
    if node == "comm.whole":
        await _edit(cb, COMM_WHOLE_Q, COMM_WHOLE_KB); return
    if node == "comm.finish":
        await _edit(cb, COMM_FINISH_Q, COMM_FINISH_KB); return
    if node == "comm.entr":
        await _edit(cb, COMM_ENTR_Q, COMM_ENTR_KB); return
    if node == "comm.park":
        await _edit(cb, COMM_PARK_Q, COMM_PARK_KB); return
    if node == "comm.layout":
        await _edit(cb, COMM_LAYOUT_Q, COMM_LAYOUT_KB); return

    # Финал
    if node == "final":
        await show_final(cb, model)
        return

async def go_next(cb: CallbackQuery, state: FSMContext, current: NodeId, skipped: bool):
    data = await state.get_data()
    payload: Dict = data.get(PAYLOAD, {})
    model = _payload_to_model(payload)

    nxt = resolve_next(model, current, skipped)
    if nxt is None:
        await show_final(cb, model)
        return
    await _push(state, nxt)
    await render_node(cb, state, nxt)

async def go_next_msg(msg: Message, state: FSMContext, current: NodeId, skipped: bool):
    data = await state.get_data()
    payload: Dict = data.get(PAYLOAD, {})
    model = _payload_to_model(payload)

    nxt = resolve_next(model, current, skipped)
    if nxt is None:
        await show_final_msg(msg, model)
        return
    # показываем новый вопрос (после текстового ввода)
    await _push(state, nxt)
    # Вывести вопрос
    # Для простоты перегенерируем так же, как в render_node, но через message
    text, kb = node_to_text_kb(nxt, data, payload)
    await _send(msg, text, kb)

def resolve_next(m: ResultModel, current: NodeId, skipped: bool) -> Optional[NodeId]:
    """Определяем следующий узел на основе текущей модели и позиции."""
    # Начало от root
    if current in ("", "root"):
        if not m.category:
            return "root"
        if m.category == TYPE_APT:
            return "apt.market"
        if m.category == TYPE_COUNTRY:
            return "country.kind"
        if m.category == TYPE_COMM:
            return "comm.type"

    # ===== Квартира =====
    if current == "apt.market":
        if m.apt_market == "Новостройка":
            return "apt.deadline"
        return "apt.rooms"

    if current == "apt.deadline":
        return "apt.sale"

    if current == "apt.sale":
        return "apt.rooms"

    if current == "apt.rooms":
        return "apt.mortgage"

    if current == "apt.mortgage":
        return "apt.total"

    if current == "apt.total":
        return "apt.kitchen"

    if current == "apt.kitchen":
        return "apt.floor"

    if current == "apt.floor":
        return "apt.floors_total"

    if current == "apt.floors_total":
        return "apt.bath"

    if current == "apt.bath":
        return "apt.windows"

    if current == "apt.windows":
        return "apt.house_type"

    if current == "apt.house_type":
        return "apt.lift"

    if current == "apt.lift":
        return "apt.parking"

    if current == "apt.parking":
        return "apt.renov"

    if current == "apt.renov":
        return "apt.layout"

    if current == "apt.layout":
        return "apt.balcony"

    if current == "apt.balcony":
        return "apt.ceil"

    if current == "apt.ceil":
        return "final"

    # ===== Загородная =====
    if current == "country.kind":
        if m.country_kind in {"Дом", "Дача", "Коттедж", "Таунхаус"}:
            return "house.area"
        if m.country_kind == "Участок":
            return "lot.land_cat"
        return "final"

    # Дом/Дача/Коттедж/Таунхаус
    if current == "house.area":
        return "house.land_area"
    if current == "house.land_area":
        return "house.dist"
    if current == "house.dist":
        return "house.floors"
    if current == "house.floors":
        return "house.rooms"
    if current == "house.rooms":
        return "house.land_cat"
    if current == "house.land_cat":
        return "house.state"
    if current == "house.state":
        return "house.wc"
    if current == "house.wc":
        return "house.comms"
    if current == "house.comms":
        return "house.leisure"
    if current == "house.leisure":
        return "house.wall"
    if current == "house.wall":
        return "house.parking"
    if current == "house.parking":
        return "house.access"
    if current == "house.access":
        return "final"

    # Участок
    if current == "lot.land_cat":
        return "lot.area"
    if current == "lot.area":
        return "lot.dist"
    if current == "lot.dist":
        return "lot.comms"
    if current == "lot.comms":
        return "final"

    # ===== Коммерческая =====
    if current == "comm.type":
        return "comm.area"
    if current == "comm.area":
        # Площадь участка — только если тип из набора и/или будет «объект целиком»
        if m.comm_type in COMM_NEEDS_LAND_TYPES:
            return "comm.land_area"
        else:
            return "comm.building"
    if current == "comm.land_area":
        return "comm.building"
    if current == "comm.building":
        return "comm.whole"
    if current == "comm.whole":
        # если тип предполагает землю, но участок не спросили (например, тип не из набора) — пропускаем
        return "comm.finish"
    if current == "comm.finish":
        return "comm.entr"
    if current == "comm.entr":
        return "comm.park"
    if current == "comm.park":
        return "comm.layout"
    if current == "comm.layout":
        return "final"

    return None

def key_to_payload(key: str) -> str:
    # Простое сопоставление key → поле модели
    return {
        # root и общие
        "root": "category",

        # apt
        "apt_market": "apt_market",
        "apt_deadline": "apt_deadline",
        "apt_sale": "apt_sale_method",
        "apt_rooms": "apt_rooms",
        "apt_mortgage": "apt_mortgage",
        "apt_total_area": "apt_total_area",
        "apt_kitchen_area": "apt_kitchen_area",
        "apt_floor": "apt_floor",
        "apt_floors_total": "apt_floors_total",
        "apt_bathroom": "apt_bathroom",
        "apt_windows": "apt_windows",
        "apt_house_type": "apt_house_type",
        "apt_lift": "apt_lift",
        "apt_parking": "apt_parking",
        "apt_renovation": "apt_renovation",
        "apt_layout": "apt_layout",
        "apt_balcony": "apt_balcony",
        "apt_ceil_height": "apt_ceil_height",

        # country
        "country_kind": "country_kind",

        "house_area": "house_area",
        "land_area_sot": "land_area_sot",
        "distance_km": "distance_km",
        "house_floors": "house_floors",
        "house_rooms": "house_rooms",
        "land_cat": "land_cat",
        "house_state": "house_state",
        "house_wc": "house_wc",
        "comms": "comms",
        "leisure": "leisure",
        "wall_mat": "wall_mat",
        "country_parking": "country_parking",
        "access": "access",

        "lot_land_cat": "lot_land_cat",
        "lot_area_sot": "lot_area_sot",
        "lot_distance_km": "lot_distance_km",
        "lot_comms": "lot_comms",

        # comm
        "comm_type": "comm_type",
        "comm_area": "comm_area",
        "comm_land_area": "comm_land_area",
        "comm_building_type": "comm_building_type",
        "comm_whole": "comm_whole",
        "comm_finish": "comm_finish",
        "comm_entrance": "comm_entrance",
        "comm_parking": "comm_parking",
        "comm_layout": "comm_layout",
    }.get(key, key)

def as_value(key: str, value: str):
    # Для некоторых ключей оставляем строки как есть; числовые обрабатываются отдельно
    return value

def key_human(key: str) -> str:
    return {
        "apt_total_area": "Общая площадь",
        "apt_kitchen_area": "Площадь кухни",
        "apt_floor": "Этаж квартиры",
        "apt_floors_total": "Этажность дома",
        "apt_ceil_height": "Высота потолков",
        "house_area": "Площадь дома",
        "land_area_sot": "Площадь участка",
        "distance_km": "Расстояние до города",
        "house_floors": "Этажей в доме",
        "lot_area_sot": "Площадь участка (сот.)",
        "lot_distance_km": "Расстояние до города (км)",
        "comm_area": "Площадь помещения",
        "comm_land_area": "Площадь участка",
    }.get(key, key)

def tag_all_options(key: str) -> List[str]:
    return {
        "comms": COMMS_ALL,
        "leisure": LEISURE_ALL,
        "lot_comms": LOT_COMMS_ALL,
    }.get(key, [])

def node_to_text_kb(node: NodeId, data: Dict, payload: Dict) -> Tuple[str, InlineKeyboardMarkup]:
    # Нужен для варианта после текстового ввода
    if node == "apt.total": return (APT_TOTAL_Q, APT_TOTAL_KB)
    # ... (для краткости — используем уже готовый render_node обычно)
    # fallback
    return ("Дальше…", ROOT_KB)

# ======================================================================
# Финал
# ======================================================================

def _pretty_summary(m: ResultModel) -> str:
    lines: List[str] = [f"📦 Итоги • {m.category or '—'}"]
    if m.category == TYPE_APT:
        lines += [
            f"Рынок: {m.apt_market or '—'}",
            *( [f"Срок сдачи: {m.apt_deadline}"] if m.apt_market == "Новостройка" and m.apt_deadline else [] ),
            *( [f"Способ продажи: {m.apt_sale_method}"] if m.apt_market == "Новостройка" and m.apt_sale_method else [] ),
            f"Комнат: {m.apt_rooms or '—'}  • Ипотека: {m.apt_mortgage or '—'}",
            f"Площадь: {m.apt_total_area or '—'} м²  • Кухня: {m.apt_kitchen_area or '—'} м²",
            f"Этаж/Этажность: {m.apt_floor or '—'}/{m.apt_floors_total or '—'}",
            f"Санузел: {m.apt_bathroom or '—'}  • Окна: {m.apt_windows or '—'}",
            f"Дом: {m.apt_house_type or '—'}  • Лифт: {m.apt_lift or '—'}",
            f"Парковка: {m.apt_parking or '—'}  • Ремонт: {m.apt_renovation or '—'}",
            f"Планировка: {m.apt_layout or '—'}  • Балкон/лоджия: {m.apt_balcony or '—'}",
            f"Потолки: {m.apt_ceil_height or '—'} м",
        ]
    elif m.category == TYPE_COUNTRY:
        lines += [f"Тип: {m.country_kind or '—'}"]
        if m.country_kind in {"Дом", "Дача", "Коттедж", "Таунхаус"}:
            lines += [
                f"Дом: {m.house_area or '—'} м²  • Участок: {m.land_area_sot or '—'} сот.",
                f"Расстояние: {m.distance_km or '—'} км  • Этажей: {m.house_floors or '—'}  • Комнат: {m.house_rooms or '—'}",
                f"Земкат: {m.land_cat or '—'}  • Состояние: {m.house_state or '—'}  • Санузел: {m.house_wc or '—'}",
                f"Коммуникации: {', '.join(m.comms) if m.comms else '—'}",
                f"Для отдыха: {', '.join(m.leisure) if m.leisure else '—'}",
                f"Материал: {m.wall_mat or '—'}  • Парковка: {m.country_parking or '—'}  • Доступность: {m.access or '—'}",
            ]
        else:
            lines += [
                f"Земкат: {m.lot_land_cat or '—'}  • Площадь: {m.lot_area_sot or '—'} сот.",
                f"Расстояние: {m.lot_distance_km or '—'} км",
                f"Коммуникации: {', '.join(m.lot_comms) if m.lot_comms else '—'}",
            ]
    elif m.category == TYPE_COMM:
        lines += [
            f"Вид: {m.comm_type or '—'}  • Площадь: {m.comm_area or '—'} м²",
            f"Участок: {m.comm_land_area if m.comm_land_area is not None else '—'}",
            f"Здание: {m.comm_building_type or '—'}  • Объект целиком: {m.comm_whole or '—'}",
            f"Отделка: {m.comm_finish or '—'}  • Вход: {m.comm_entrance or '—'}",
            f"Парковка: {m.comm_parking or '—'}  • Планировка: {m.comm_layout or '—'}",
        ]
    return "\n".join(lines)

async def show_final(cb: CallbackQuery, m: ResultModel):
    summary = _pretty_summary(m)
    payload_json = asdict(m)
    text = f"{summary}\n\n<code>{payload_json}</code>"
    try:
        await cb.message.edit_text(text)
    except TelegramBadRequest:
        await cb.message.answer(text)

async def show_final_msg(msg: Message, m: ResultModel):
    summary = _pretty_summary(m)
    payload_json = asdict(m)
    text = f"{summary}\n\n<code>{payload_json}</code>"
    await msg.answer(text)

# ======================================================================
# Регистрация наружу
# ======================================================================

def router(parent: Router) -> None:
    parent.include_router(rt)
