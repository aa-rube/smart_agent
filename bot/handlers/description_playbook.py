from __future__ import annotations
from typing import Optional, List, Dict, Any
import os
import re
import json

import aiohttp
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, InputMediaPhoto
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.config import EXECUTOR_BASE_URL, get_file_path
import bot.utils.database as db
from bot.utils.chat_actions import run_long_operation_with_action


# ==========================
# Состояния FSM
# ==========================
class DescriptionStates(StatesGroup):
    waiting_for_property_type = State()
    waiting_for_flat_market = State()
    waiting_for_rooms = State()
    waiting_for_mortgage = State()
    waiting_for_total_area = State()
    waiting_for_kitchen_area = State()
    waiting_for_floor = State()
    waiting_for_floors_total = State()
    waiting_for_bathroom = State()
    waiting_for_windows = State()
    waiting_for_house_type = State()
    waiting_for_elevator = State()
    waiting_for_parking = State()
    waiting_for_renovation = State()
    waiting_for_layout = State()
    waiting_for_balcony = State()
    waiting_for_ceiling_height = State()
    waiting_for_new_building_completion = State()
    waiting_for_new_building_sale_type = State()

    # Загородная недвижимость - Дом
    waiting_for_country_house_type = State()
    waiting_for_house_area = State()
    waiting_for_land_area = State()
    waiting_for_distance = State()
    waiting_for_house_floors = State()
    waiting_for_house_rooms = State()
    waiting_for_land_category_house = State()
    waiting_for_house_renovation = State()
    waiting_for_house_bathroom = State()
    waiting_for_house_utilities = State()
    waiting_for_house_recreation = State()
    waiting_for_house_wall_material = State()
    waiting_for_house_parking = State()
    waiting_for_house_transport = State()

    # Загородная недвижимость - Участок
    waiting_for_land_category = State()
    waiting_for_land_area_simple = State()
    waiting_for_land_distance = State()
    waiting_for_land_utilities = State()

    # Коммерческая недвижимость
    waiting_for_commercial_type = State()
    waiting_for_commercial_area = State()
    waiting_for_commercial_land_area = State()
    waiting_for_commercial_building_type = State()
    waiting_for_commercial_whole_object = State()
    waiting_for_commercial_condition = State()
    waiting_for_commercial_entrance = State()
    waiting_for_commercial_parking = State()
    waiting_for_commercial_layout = State()


# ==========================
# Тексты вопросов
# ==========================
ASK_PROPERTY_TYPE = "🏠 *Выберите тип недвижимости:*"
ASK_FLAT_MARKET = "🏢 *Рынок квартиры?*"
ASK_ROOMS = "🚪 *Количество комнат?*"
ASK_MORTGAGE = "🏦 *Подходит для ипотеки?*"
ASK_TOTAL_AREA = "📐 *Укажите общую площадь (м²)*"
ASK_KITCHEN_AREA = "👨‍🍳 *Площадь кухни (м²)*"
ASK_FLOOR = "🏢 *Этаж квартиры?*"
ASK_FLOORS_TOTAL = "🏗️ *Сколько этажей в доме?*"
ASK_BATHROOM = "🚽 *Санузел?*"
ASK_WINDOWS = "🪟 *Куда выходят окна?*"
ASK_HOUSE_TYPE = "🏘️ *Тип дома?*"
ASK_ELEVATOR = "🛗 *Лифт?*"
ASK_PARKING = "🅿️ *Парковка?*"
ASK_RENOVATION = "🔨 *Состояние ремонта?*"
ASK_LAYOUT = "📐 *Планировка комнат?*"
ASK_BALCONY = "🌿 *Балкон или лоджия?*"
ASK_CEILING_HEIGHT = "📏 *Высота потолков (м)?*"
ASK_NEW_BUILDING_COMPLETION = "📅 *Срок сдачи?*"
ASK_NEW_BUILDING_SALE_TYPE = "📄 *Способ продажи?*"

# Загородная недвижимость
ASK_COUNTRY_HOUSE_TYPE = "🏡 *Тип объекта?*"
ASK_HOUSE_AREA = "📐 *Площадь дома (м²)?*"
ASK_LAND_AREA = "🌳 *Площадь участка (сот.)?*"
ASK_DISTANCE = "📍 *Расстояние от города (км)?*"
ASK_HOUSE_FLOORS = "🏠 *Этажей в доме?*"
ASK_HOUSE_ROOMS = "🚪 *Комнат?*"
ASK_LAND_CATEGORY_HOUSE = "🏞️ *Категория земель?*"
ASK_HOUSE_RENOVATION = "🔨 *Состояние/ремонт?*"
ASK_HOUSE_BATHROOM = "🚽 *Санузел?*"
ASK_HOUSE_UTILITIES = "⚡ *Коммуникации?*"
ASK_HOUSE_RECREATION = "🎯 *Для отдыха?*"
ASK_HOUSE_WALL_MATERIAL = "🧱 *Материал стен?*"
ASK_HOUSE_PARKING = "🅿️ *Парковка?*"
ASK_HOUSE_TRANSPORT = "🚗 *Транспортная доступность?*"

ASK_LAND_CATEGORY = "🏞️ *Категория земель?*"
ASK_LAND_AREA_SIMPLE = "🌳 *Площадь участка (сот.)?*"
ASK_LAND_DISTANCE = "📍 *Расстояние до города (км)?*"
ASK_LAND_UTILITIES = "⚡ *Коммуникации?*"

# Коммерческая недвижимость
ASK_COMMERCIAL_TYPE = "🏢 *Вид объекта?*"
ASK_COMMERCIAL_AREA = "📐 *Площадь помещения (м²)?*"
ASK_COMMERCIAL_LAND_AREA = "🌳 *Площадь участка (если есть)?*"
ASK_COMMERCIAL_BUILDING_TYPE = "🏛️ *Тип здания?*"
ASK_COMMERCIAL_WHOLE_OBJECT = "🏢 *Объект целиком?*"
ASK_COMMERCIAL_CONDITION = "🔨 *Отделка?*"
ASK_COMMERCIAL_ENTRANCE = "🚪 *Вход?*"
ASK_COMMERCIAL_PARKING = "🅿️ *Парковка?*"
ASK_COMMERCIAL_LAYOUT = "📐 *Планировка?*"

GENERATING = "⏳ *Генерирую описание… это займёт до минуты.*"
ERROR_TEXT = "😔 *Не получилось сгенерировать описание. Попробуйте ещё раз.*"

DESC_INTRO = """🏠 *Создание продающего описания*

Заполните характеристики объекта для генерации профессионального описания.

_Выберите тип недвижимости:_"""


# ==========================
# Утилиты
# ==========================
async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Обновить текст/подпись и клавиатуру текущего сообщения."""
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


async def _edit_or_replace_with_photo_file(
        bot: Bot, msg: Message, file_path: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    """Поменять текущее сообщение на фото с подписью."""
    try:
        media = InputMediaPhoto(media=FSInputFile(file_path), caption=caption, parse_mode="Markdown")
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(file_path),
                             caption=caption, reply_markup=kb, parse_mode="Markdown")


def _create_navigation_buttons(back_state: Optional[str] = None) -> List[InlineKeyboardButton]:
    """Создать кнопки навигации."""
    buttons = []
    if back_state:
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"desc_back_{back_state}"))
    buttons.append(InlineKeyboardButton(text="⏭️ Пропустить", callback_data="desc_skip"))
    buttons.append(InlineKeyboardButton(text="🔄 Сброс", callback_data="desc_reset"))
    return buttons


def _create_number_keyboard(presets: List[str], step_name: str, back_state: str) -> InlineKeyboardMarkup:
    """Создать клавиатуру для числового ввода с пресетами."""
    buttons = []
    row = []
    for i, preset in enumerate(presets):
        row.append(InlineKeyboardButton(text=preset, callback_data=f"desc_{step_name}_{preset}"))
        if len(row) == 2 or i == len(presets) - 1:
            buttons.append(row)
            row = []

    # Кнопка "Другое" для ручного ввода
    buttons.append([InlineKeyboardButton(text="✏️ Другое…", callback_data=f"desc_{step_name}_other")])

    # Кнопки навигации
    buttons.append(_create_navigation_buttons(back_state))

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _create_simple_keyboard(options: List[str], step_name: str, back_state: str,
                            columns: int = 2) -> InlineKeyboardMarkup:
    """Создать простую клавиатуру с вариантами ответов."""
    buttons = []
    row = []
    for i, option in enumerate(options):
        row.append(
            InlineKeyboardButton(text=option, callback_data=f"desc_{step_name}_{option.lower().replace(' ', '_')}"))
        if len(row) == columns or i == len(options) - 1:
            buttons.append(row)
            row = []

    # Кнопки навигации
    buttons.append(_create_navigation_buttons(back_state))

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _create_multi_select_keyboard(options: List[str], step_name: str, back_state: str,
                                  selected: List[str] = None) -> InlineKeyboardMarkup:
    """Создать клавиатуру для множественного выбора."""
    if selected is None:
        selected = []

    buttons = []
    for option in options:
        is_selected = option in selected
        emoji = "✅ " if is_selected else "◻️ "
        buttons.append([
            InlineKeyboardButton(
                text=f"{emoji}{option}",
                callback_data=f"desc_{step_name}_toggle_{option.lower().replace(' ', '_')}"
            )
        ])

    # Кнопка подтверждения
    buttons.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"desc_{step_name}_done")])

    # Кнопки навигации
    buttons.append(_create_navigation_buttons(back_state))

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==========================
# Клавиатуры для каждого шага
# ==========================
def kb_property_type() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🏢 Квартира", callback_data="desc_property_type_flat")],
        [InlineKeyboardButton(text="🏡 Загородная", callback_data="desc_property_type_country")],
        [InlineKeyboardButton(text="🏢 Коммерческая", callback_data="desc_property_type_commercial")],
        _create_navigation_buttons()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def kb_flat_market() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Новостройка", "Вторичка"], "flat_market", "property_type")


def kb_rooms() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Студия", "1", "2", "3", "4+"], "rooms", "flat_market")


def kb_mortgage() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Да", "Нет"], "mortgage", "rooms")


def kb_total_area() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["30", "40", "50", "70", "100"], "total_area", "mortgage")


def kb_kitchen_area() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["6", "9", "12", "15", "20"], "kitchen_area", "total_area")


def kb_floor() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["1", "2", "3", "4", "5", "6+"], "floor", "kitchen_area")


def kb_floors_total() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["5", "9", "12", "16", "25+"], "floors_total", "floor")


def kb_bathroom() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Совмещённый", "Раздельный"], "bathroom", "floors_total")


def kb_windows() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Во двор", "На улицу", "На солнечную", "Разное"], "windows", "bathroom")


def kb_house_type() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Кирпич", "Панель", "Блочный", "Монолит", "Монолит-кирпич"], "house_type",
                                   "windows")


def kb_elevator() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Нет", "Пассажирский", "Грузовой", "Оба"], "elevator", "house_type")


def kb_parking() -> InlineKeyboardMarkup:
    options = ["Подземная", "Наземная", "Многоуровневая", "Двор", "Двор со шлагбаумом"]
    return _create_multi_select_keyboard(options, "parking", "elevator")


def kb_renovation() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Требуется", "Косметический", "Евро", "Дизайнерский"], "renovation", "parking")


def kb_layout() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Изолированные", "Смежные", "Смешанные"], "layout", "renovation")


def kb_balcony() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Нет", "Балкон", "Лоджия", "Несколько"], "balcony", "layout")


def kb_ceiling_height() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["2.5", "2.7", "3.0", "3.5+"], "ceiling_height", "balcony")


def kb_new_building_completion() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Q4-2025", "2026", "2027", "2028+"], "new_building_completion", "balcony")


def kb_new_building_sale_type() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["ДКП", "Переуступка", "ФЗ-214"], "new_building_sale_type",
                                   "new_building_completion")


# Загородная недвижимость
def kb_country_house_type() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Дом", "Дача", "Коттедж", "Таунхаус", "Участок"], "country_house_type",
                                   "property_type")


def kb_house_area() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["80", "120", "180", "250", "300+"], "house_area", "country_house_type")


def kb_land_area() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["6", "10", "15", "20", "30+"], "land_area", "house_area")


def kb_distance() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["5", "10", "20", "30", "50+"], "distance", "land_area")


def kb_house_floors() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["1", "2", "3", "4+"], "house_floors", "distance")


def kb_house_rooms() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["2", "3", "4", "5+"], "house_rooms", "house_floors")


def kb_land_category_house() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["ИЖС", "Садоводство", "ЛПХ", "КФХ", "Иное"], "land_category_house", "house_rooms")


def kb_house_renovation() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Требуется", "Косметический", "Евро", "Дизайнерский"], "house_renovation",
                                   "land_category_house")


def kb_house_bathroom() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["В доме", "На улице", "Оба"], "house_bathroom", "house_renovation")


def kb_house_utilities() -> InlineKeyboardMarkup:
    options = ["Электричество", "Газ", "Отопление", "Водоснабжение", "Канализация"]
    return _create_multi_select_keyboard(options, "house_utilities", "house_bathroom")


def kb_house_recreation() -> InlineKeyboardMarkup:
    options = ["Баня", "Бассейн", "Сауна", "Другое"]
    return _create_multi_select_keyboard(options, "house_recreation", "house_utilities")


def kb_house_wall_material() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Кирпич", "Брус", "Бревно", "Газоблок", "Металл", "Иное"], "house_wall_material",
                                   "house_recreation")


def kb_house_parking() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Гараж", "Парковочное место", "Навес", "Нет"], "house_parking",
                                   "house_wall_material")


def kb_house_transport() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Асфальт", "Остановка ОТ", "ЖД станция", "Грунтовка"], "house_transport",
                                   "house_parking")


def kb_land_category() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["ИЖС", "СНТ", "ДНП", "ЛПХ", "Иное"], "land_category", "country_house_type")


def kb_land_area_simple() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["6", "10", "15", "20", "30+"], "land_area_simple", "land_category")


def kb_land_distance() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["5", "10", "20", "30", "50+"], "land_distance", "land_area_simple")


def kb_land_utilities() -> InlineKeyboardMarkup:
    options = ["Газ", "Вода", "Свет", "По границе", "Нет"]
    return _create_multi_select_keyboard(options, "land_utilities", "land_distance")


# Коммерческая недвижимость
def kb_commercial_type() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Офис", "ПСН", "Торговая", "Склад", "Производство", "Общепит", "Гостиница"],
                                   "commercial_type", "property_type")


def kb_commercial_area() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["50", "100", "200", "500", "1000+"], "commercial_area", "commercial_type")


def kb_commercial_land_area() -> InlineKeyboardMarkup:
    return _create_number_keyboard(["2", "5", "10", "20", "50+"], "commercial_land_area", "commercial_area")


def kb_commercial_building_type() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["БЦ", "ТЦ", "Админздание", "Жилой дом", "Другое"], "commercial_building_type",
                                   "commercial_land_area")


def kb_commercial_whole_object() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Да", "Нет"], "commercial_whole_object", "commercial_building_type")


def kb_commercial_condition() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Без отделки", "Черновая", "Чистовая", "Офисная"], "commercial_condition",
                                   "commercial_whole_object")


def kb_commercial_entrance() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["С улицы", "Со двора", "Отдельный второй вход"], "commercial_entrance",
                                   "commercial_condition")


def kb_commercial_parking() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Нет", "Улица", "Крытая", "Подземная", "Гостевая"], "commercial_parking",
                                   "commercial_entrance")


def kb_commercial_layout() -> InlineKeyboardMarkup:
    return _create_simple_keyboard(["Open space", "Кабинетная", "Смешанная"], "commercial_layout", "commercial_parking")


# ==========================
# Функции навигации
# ==========================
async def _get_previous_state(current_state: str) -> Optional[str]:
    """Получить предыдущее состояние на основе текущего."""
    state_mapping = {
        # Квартира
        "waiting_for_flat_market": "waiting_for_property_type",
        "waiting_for_rooms": "waiting_for_flat_market",
        "waiting_for_mortgage": "waiting_for_rooms",
        "waiting_for_total_area": "waiting_for_mortgage",
        "waiting_for_kitchen_area": "waiting_for_total_area",
        "waiting_for_floor": "waiting_for_kitchen_area",
        "waiting_for_floors_total": "waiting_for_floor",
        "waiting_for_bathroom": "waiting_for_floors_total",
        "waiting_for_windows": "waiting_for_bathroom",
        "waiting_for_house_type": "waiting_for_windows",
        "waiting_for_elevator": "waiting_for_house_type",
        "waiting_for_parking": "waiting_for_elevator",
        "waiting_for_renovation": "waiting_for_parking",
        "waiting_for_layout": "waiting_for_renovation",
        "waiting_for_balcony": "waiting_for_layout",
        "waiting_for_ceiling_height": "waiting_for_balcony",
        "waiting_for_new_building_completion": "waiting_for_balcony",
        "waiting_for_new_building_sale_type": "waiting_for_new_building_completion",

        # Загородная - Дом
        "waiting_for_country_house_type": "waiting_for_property_type",
        "waiting_for_house_area": "waiting_for_country_house_type",
        "waiting_for_land_area": "waiting_for_house_area",
        "waiting_for_distance": "waiting_for_land_area",
        "waiting_for_house_floors": "waiting_for_distance",
        "waiting_for_house_rooms": "waiting_for_house_floors",
        "waiting_for_land_category_house": "waiting_for_house_rooms",
        "waiting_for_house_renovation": "waiting_for_land_category_house",
        "waiting_for_house_bathroom": "waiting_for_house_renovation",
        "waiting_for_house_utilities": "waiting_for_house_bathroom",
        "waiting_for_house_recreation": "waiting_for_house_utilities",
        "waiting_for_house_wall_material": "waiting_for_house_recreation",
        "waiting_for_house_parking": "waiting_for_house_wall_material",
        "waiting_for_house_transport": "waiting_for_house_parking",

        # Загородная - Участок
        "waiting_for_land_category": "waiting_for_country_house_type",
        "waiting_for_land_area_simple": "waiting_for_land_category",
        "waiting_for_land_distance": "waiting_for_land_area_simple",
        "waiting_for_land_utilities": "waiting_for_land_distance",

        # Коммерческая
        "waiting_for_commercial_type": "waiting_for_property_type",
        "waiting_for_commercial_area": "waiting_for_commercial_type",
        "waiting_for_commercial_land_area": "waiting_for_commercial_area",
        "waiting_for_commercial_building_type": "waiting_for_commercial_land_area",
        "waiting_for_commercial_whole_object": "waiting_for_commercial_building_type",
        "waiting_for_commercial_condition": "waiting_for_commercial_whole_object",
        "waiting_for_commercial_entrance": "waiting_for_commercial_condition",
        "waiting_for_commercial_parking": "waiting_for_commercial_entrance",
        "waiting_for_commercial_layout": "waiting_for_commercial_parking",
    }
    return state_mapping.get(current_state)


async def _go_to_previous_step(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Вернуться на предыдущий шаг."""
    current_state = await state.get_state()
    previous_state = await _get_previous_state(current_state)

    if previous_state:
        await state.set_state(previous_state)
        await _show_current_step(cb.message, state, bot)
    else:
        await cb.answer("Это первый шаг")


async def _skip_current_step(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Пропустить текущий шаг."""
    current_state = await state.get_state()
    data = await state.get_data()

    # Сохраняем пропущенное значение
    state_name = current_state.replace("waiting_for_", "")
    data[state_name] = None
    await state.update_data(**data)

    # Переходим к следующему шагу
    await _go_to_next_step(cb.message, state, bot)


async def _reset_flow(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Сбросить весь процесс."""
    await state.clear()
    await start_description_flow(cb, state, bot)


async def _go_to_next_step(message: Message, state: FSMContext, bot: Bot):
    """Перейти к следующему шагу на основе текущих данных."""
    current_state = await state.get_state()
    data = await state.get_data()

    # Определяем следующий шаг на основе текущего состояния и данных
    next_state = await _get_next_state(current_state, data)

    if next_state:
        await state.set_state(next_state)
        await _show_current_step(message, state, bot)
    else:
        # Все шаги завершены - генерируем описание
        await _generate_description(message, state, bot)


async def _get_next_state(current_state: str, data: Dict[str, Any]) -> Optional[State]:
    """Определить следующий шаг на основе текущего состояния и данных."""
    state_flow = {
        # Начало
        "waiting_for_property_type": {
            "flat": DescriptionStates.waiting_for_flat_market,
            "country": DescriptionStates.waiting_for_country_house_type,
            "commercial": DescriptionStates.waiting_for_commercial_type,
        },

        # Квартира
        "waiting_for_flat_market": DescriptionStates.waiting_for_rooms,
        "waiting_for_rooms": DescriptionStates.waiting_for_mortgage,
        "waiting_for_mortgage": DescriptionStates.waiting_for_total_area,
        "waiting_for_total_area": DescriptionStates.waiting_for_kitchen_area,
        "waiting_for_kitchen_area": DescriptionStates.waiting_for_floor,
        "waiting_for_floor": DescriptionStates.waiting_for_floors_total,
        "waiting_for_floors_total": DescriptionStates.waiting_for_bathroom,
        "waiting_for_bathroom": DescriptionStates.waiting_for_windows,
        "waiting_for_windows": DescriptionStates.waiting_for_house_type,
        "waiting_for_house_type": DescriptionStates.waiting_for_elevator,
        "waiting_for_elevator": DescriptionStates.waiting_for_parking,
        "waiting_for_parking": DescriptionStates.waiting_for_renovation,
        "waiting_for_renovation": DescriptionStates.waiting_for_layout,
        "waiting_for_layout": DescriptionStates.waiting_for_balcony,
        "waiting_for_balcony": {
            "новостройка": DescriptionStates.waiting_for_new_building_completion,
            "default": DescriptionStates.waiting_for_ceiling_height,
        },
        "waiting_for_new_building_completion": DescriptionStates.waiting_for_new_building_sale_type,
        "waiting_for_new_building_sale_type": DescriptionStates.waiting_for_ceiling_height,
        "waiting_for_ceiling_height": None,  # Конец цепочки

        # Загородная - Дом
        "waiting_for_country_house_type": {
            "участок": DescriptionStates.waiting_for_land_category,
            "default": DescriptionStates.waiting_for_house_area,
        },
        "waiting_for_house_area": DescriptionStates.waiting_for_land_area,
        "waiting_for_land_area": DescriptionStates.waiting_for_distance,
        "waiting_for_distance": DescriptionStates.waiting_for_house_floors,
        "waiting_for_house_floors": DescriptionStates.waiting_for_house_rooms,
        "waiting_for_house_rooms": DescriptionStates.waiting_for_land_category_house,
        "waiting_for_land_category_house": DescriptionStates.waiting_for_house_renovation,
        "waiting_for_house_renovation": DescriptionStates.waiting_for_house_bathroom,
        "waiting_for_house_bathroom": DescriptionStates.waiting_for_house_utilities,
        "waiting_for_house_utilities": DescriptionStates.waiting_for_house_recreation,
        "waiting_for_house_recreation": DescriptionStates.waiting_for_house_wall_material,
        "waiting_for_house_wall_material": DescriptionStates.waiting_for_house_parking,
        "waiting_for_house_parking": DescriptionStates.waiting_for_house_transport,
        "waiting_for_house_transport": None,

        # Загородная - Участок
        "waiting_for_land_category": DescriptionStates.waiting_for_land_area_simple,
        "waiting_for_land_area_simple": DescriptionStates.waiting_for_land_distance,
        "waiting_for_land_distance": DescriptionStates.waiting_for_land_utilities,
        "waiting_for_land_utilities": None,

        # Коммерческая
        "waiting_for_commercial_type": DescriptionStates.waiting_for_commercial_area,
        "waiting_for_commercial_area": DescriptionStates.waiting_for_commercial_land_area,
        "waiting_for_commercial_land_area": DescriptionStates.waiting_for_commercial_building_type,
        "waiting_for_commercial_building_type": DescriptionStates.waiting_for_commercial_whole_object,
        "waiting_for_commercial_whole_object": DescriptionStates.waiting_for_commercial_condition,
        "waiting_for_commercial_condition": DescriptionStates.waiting_for_commercial_entrance,
        "waiting_for_commercial_entrance": DescriptionStates.waiting_for_commercial_parking,
        "waiting_for_commercial_parking": DescriptionStates.waiting_for_commercial_layout,
        "waiting_for_commercial_layout": None,
    }

    next_step = state_flow.get(current_state)

    if isinstance(next_step, dict):
        # Есть ветвление на основе данных
        key = data.get("flat_market", "").lower() if "flat_market" in data else data.get("country_house_type",
                                                                                         "").lower()
        return next_step.get(key, next_step.get("default"))

    return next_step


async def _show_current_step(message: Message, state: FSMContext, bot: Bot):
    """Показать текущий шаг с соответствующим вопросом и клавиатурой."""
    current_state = await state.get_state()

    state_to_question = {
        "waiting_for_property_type": (ASK_PROPERTY_TYPE, kb_property_type()),
        "waiting_for_flat_market": (ASK_FLAT_MARKET, kb_flat_market()),
        "waiting_for_rooms": (ASK_ROOMS, kb_rooms()),
        "waiting_for_mortgage": (ASK_MORTGAGE, kb_mortgage()),
        "waiting_for_total_area": (ASK_TOTAL_AREA, kb_total_area()),
        "waiting_for_kitchen_area": (ASK_KITCHEN_AREA, kb_kitchen_area()),
        "waiting_for_floor": (ASK_FLOOR, kb_floor()),
        "waiting_for_floors_total": (ASK_FLOORS_TOTAL, kb_floors_total()),
        "waiting_for_bathroom": (ASK_BATHROOM, kb_bathroom()),
        "waiting_for_windows": (ASK_WINDOWS, kb_windows()),
        "waiting_for_house_type": (ASK_HOUSE_TYPE, kb_house_type()),
        "waiting_for_elevator": (ASK_ELEVATOR, kb_elevator()),
        "waiting_for_parking": (ASK_PARKING, kb_parking()),
        "waiting_for_renovation": (ASK_RENOVATION, kb_renovation()),
        "waiting_for_layout": (ASK_LAYOUT, kb_layout()),
        "waiting_for_balcony": (ASK_BALCONY, kb_balcony()),
        "waiting_for_ceiling_height": (ASK_CEILING_HEIGHT, kb_ceiling_height()),
        "waiting_for_new_building_completion": (ASK_NEW_BUILDING_COMPLETION, kb_new_building_completion()),
        "waiting_for_new_building_sale_type": (ASK_NEW_BUILDING_SALE_TYPE, kb_new_building_sale_type()),

        # Загородная
        "waiting_for_country_house_type": (ASK_COUNTRY_HOUSE_TYPE, kb_country_house_type()),
        "waiting_for_house_area": (ASK_HOUSE_AREA, kb_house_area()),
        "waiting_for_land_area": (ASK_LAND_AREA, kb_land_area()),
        "waiting_for_distance": (ASK_DISTANCE, kb_distance()),
        "waiting_for_house_floors": (ASK_HOUSE_FLOORS, kb_house_floors()),
        "waiting_for_house_rooms": (ASK_HOUSE_ROOMS, kb_house_rooms()),
        "waiting_for_land_category_house": (ASK_LAND_CATEGORY_HOUSE, kb_land_category_house()),
        "waiting_for_house_renovation": (ASK_HOUSE_RENOVATION, kb_house_renovation()),
        "waiting_for_house_bathroom": (ASK_HOUSE_BATHROOM, kb_house_bathroom()),
        "waiting_for_house_utilities": (ASK_HOUSE_UTILITIES, kb_house_utilities()),
        "waiting_for_house_recreation": (ASK_HOUSE_RECREATION, kb_house_recreation()),
        "waiting_for_house_wall_material": (ASK_HOUSE_WALL_MATERIAL, kb_house_wall_material()),
        "waiting_for_house_parking": (ASK_HOUSE_PARKING, kb_house_parking()),
        "waiting_for_house_transport": (ASK_HOUSE_TRANSPORT, kb_house_transport()),
        "waiting_for_land_category": (ASK_LAND_CATEGORY, kb_land_category()),
        "waiting_for_land_area_simple": (ASK_LAND_AREA_SIMPLE, kb_land_area_simple()),
        "waiting_for_land_distance": (ASK_LAND_DISTANCE, kb_land_distance()),
        "waiting_for_land_utilities": (ASK_LAND_UTILITIES, kb_land_utilities()),

        # Коммерческая
        "waiting_for_commercial_type": (ASK_COMMERCIAL_TYPE, kb_commercial_type()),
        "waiting_for_commercial_area": (ASK_COMMERCIAL_AREA, kb_commercial_area()),
        "waiting_for_commercial_land_area": (ASK_COMMERCIAL_LAND_AREA, kb_commercial_land_area()),
        "waiting_for_commercial_building_type": (ASK_COMMERCIAL_BUILDING_TYPE, kb_commercial_building_type()),
        "waiting_for_commercial_whole_object": (ASK_COMMERCIAL_WHOLE_OBJECT, kb_commercial_whole_object()),
        "waiting_for_commercial_condition": (ASK_COMMERCIAL_CONDITION, kb_commercial_condition()),
        "waiting_for_commercial_entrance": (ASK_COMMERCIAL_ENTRANCE, kb_commercial_entrance()),
        "waiting_for_commercial_parking": (ASK_COMMERCIAL_PARKING, kb_commercial_parking()),
        "waiting_for_commercial_layout": (ASK_COMMERCIAL_LAYOUT, kb_commercial_layout()),
    }

    question, keyboard = state_to_question.get(current_state, ("Шаг не найден", None))

    if keyboard:
        await _edit_text_or_caption(message, question, keyboard)


# ==========================
# Обработчики
# ==========================
async def start_description_flow(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Начать процесс описания."""
    await state.clear()
    await state.set_state(DescriptionStates.waiting_for_property_type)

    # Показываем стартовый экран с фото
    img_path = get_file_path("img/bot/descr_home.png")
    if os.path.exists(img_path):
        await _edit_or_replace_with_photo_file(bot, cb.message, img_path, DESC_INTRO, kb_property_type())
    else:
        await _edit_text_or_caption(cb.message, DESC_INTRO, kb_property_type())

    await cb.answer()


async def handle_property_type(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик выбора типа недвижимости."""
    property_type = cb.data.replace("desc_property_type_", "")
    await state.update_data(property_type=property_type)
    await _go_to_next_step(cb.message, state, bot)
    await cb.answer()


async def handle_simple_selection(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик простого выбора (кнопки)."""
    data_parts = cb.data.split("_")
    step_name = data_parts[1]
    value = "_".join(data_parts[2:])

    # Преобразуем значение в читаемый формат
    value_readable = value.replace("_", " ").title()

    await state.update_data({step_name: value_readable})
    await _go_to_next_step(cb.message, state, bot)
    await cb.answer()


async def handle_number_selection(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик выбора числа."""
    data_parts = cb.data.split("_")
    step_name = data_parts[1]
    value = data_parts[2]

    if value == "other":
        # Запрос ручного ввода
        await cb.message.answer("✏️ Введите значение:")
        # Здесь нужно перейти в состояние ожидания ручного ввода
        # Для простоты пропускаем эту логику
        await cb.answer()
        return

    await state.update_data({step_name: value})
    await _go_to_next_step(cb.message, state, bot)
    await cb.answer()


async def handle_multi_select_toggle(cb: CallbackQuery, state: FSMContext):
    """Обработчик переключения множественного выбора."""
    data_parts = cb.data.split("_")
    step_name = data_parts[1]
    value = "_".join(data_parts[3:])
    value_readable = value.replace("_", " ").title()

    data = await state.get_data()
    current_values = data.get(step_name, [])

    if value_readable in current_values:
        current_values.remove(value_readable)
    else:
        current_values.append(value_readable)

    await state.update_data({step_name: current_values})

    # Обновляем клавиатуру с новым состоянием
    current_state = await state.get_state()
    state_name = current_state.replace("waiting_for_", "")
    back_state = await _get_previous_state(current_state)
    back_state_name = back_state.replace("waiting_for_", "") if back_state else None

    # Создаем обновленную клавиатуру
    keyboard = globals()[f"kb_{state_name}"]()
    await _edit_text_or_caption(cb.message, globals()[f"ASK_{state_name.upper()}"], keyboard)

    await cb.answer()


async def handle_multi_select_done(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик завершения множественного выбора."""
    await _go_to_next_step(cb.message, state, bot)
    await cb.answer()


async def handle_back(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик кнопки Назад."""
    await _go_to_previous_step(cb, state, bot)
    await cb.answer()


async def handle_skip(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик кнопки Пропустить."""
    await _skip_current_step(cb, state, bot)
    await cb.answer()


async def handle_reset(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Обработчик кнопки Сброс."""
    await _reset_flow(cb, state, bot)
    await cb.answer()


# ==========================
# Генерация описания
# ==========================
async def _generate_description(message: Message, state: FSMContext, bot: Bot):
    """Сгенерировать описание на основе собранных данных."""
    data = await state.get_data()

    # Показываем сообщение о генерации
    await _edit_text_or_caption(message, GENERATING)

    try:
        # Отправляем данные на сервер для генерации
        description_text = await _send_generation_request(data)

        # Показываем результат
        result_text = f"🏠 *Ваше описание готово!*\n\n{description_text}"

        # Кнопка для повторной генерации
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Сгенерировать ещё", callback_data="desc_start")],
            [InlineKeyboardButton(text="⬅️ На главную", callback_data="nav.main")]
        ])

        await _edit_text_or_caption(message, result_text, keyboard)

    except Exception as e:
        error_text = f"{ERROR_TEXT}\n\nОшибка: {str(e)}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="desc_start")],
            [InlineKeyboardButton(text="⬅️ На главную", callback_data="nav.main")]
        ])
        await _edit_text_or_caption(message, error_text, keyboard)


async def _send_generation_request(data: Dict[str, Any]) -> str:
    """Отправить запрос на генерацию описания."""
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/description/generate"

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data) as response:
            if response.status == 200:
                result = await response.json()
                return result.get("text", "Описание не было сгенерировано.")
            else:
                raise Exception(f"HTTP {response.status}: {await response.text()}")


# ==========================
# Роутер
# ==========================
def setup_description_router(router: Router):
    """Настройка роутера для обработки описаний."""

    # Старт
    router.callback_query.register(start_description_flow, F.data == "nav.descr_home")
    router.callback_query.register(start_description_flow, F.data == "desc_start")

    # Основные обработчики выбора
    router.callback_query.register(handle_property_type, F.data.startswith("desc_property_type_"))

    # Простые выборы (кнопки)
    simple_handlers = [
        "flat_market", "rooms", "mortgage", "bathroom", "windows", "house_type",
        "elevator", "renovation", "layout", "balcony", "new_building_completion",
        "new_building_sale_type", "country_house_type", "house_rooms",
        "land_category_house", "house_renovation", "house_bathroom",
        "house_wall_material", "house_parking", "house_transport", "land_category",
        "commercial_type", "commercial_building_type", "commercial_whole_object",
        "commercial_condition", "commercial_entrance", "commercial_parking", "commercial_layout"
    ]

    for handler in simple_handlers:
        router.callback_query.register(
            handle_simple_selection,
            F.data.startswith(f"desc_{handler}_")
        )

    # Числовые выборы
    number_handlers = [
        "total_area", "kitchen_area", "floor", "floors_total", "ceiling_height",
        "house_area", "land_area", "distance", "house_floors",
        "land_area_simple", "land_distance", "commercial_area", "commercial_land_area"
    ]

    for handler in number_handlers:
        router.callback_query.register(
            handle_number_selection,
            F.data.startswith(f"desc_{handler}_")
        )

    # Множественный выбор
    multi_select_handlers = ["parking", "house_utilities", "house_recreation", "land_utilities"]

    for handler in multi_select_handlers:
        router.callback_query.register(
            handle_multi_select_toggle,
            F.data.startswith(f"desc_{handler}_toggle_")
        )
        router.callback_query.register(
            handle_multi_select_done,
            F.data == f"desc_{handler}_done"
        )

    # Навигация
    router.callback_query.register(handle_back, F.data.startswith("desc_back_"))
    router.callback_query.register(handle_skip, F.data == "desc_skip")
    router.callback_query.register(handle_reset, F.data == "desc_reset")


# Экспорт роутера
description_router = Router()
setup_description_router(description_router)


def router(rt: Router):
    rt.include_router(description_router)