#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\description_playbook.py
from typing import Optional, List, Dict, Set
from aiogram.types import CallbackQuery as _CbType
import re

import aiohttp
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, InputMediaPhoto
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiohttp import web
from yarl import URL
import os

from bot.config import EXECUTOR_BASE_URL, get_file_path
from bot.config import EXECUTOR_CALLBACK_TOKEN, BOT_PUBLIC_BASE_URL
from bot.states.states import DescriptionStates

# ====== Доступ / подписка  ======
import bot.utils.database as app_db          # триал/согласия/история
import bot.utils.billing_db as billing_db     # карты/подписки/лог платежей
from bot.utils.database import is_trial_active, trial_remaining_hours


# ==========================
# Навигация (Назад/Выход) и резюме
# ==========================
def _compose_summary(d: Dict) -> str:
    """
    Полное резюме из уже заполненных пользователем полей (любой тип объекта).
    Короткое и компактное: ключевые «шапочные» пункты + параметры по мере наличия.
    """
    def _tlabel(raw: str | None) -> str:
        return {
            "flat": "квартира",
            "house": "дом",
            "land": "участок",
            "country": "загородная",
            "commercial": "коммерческая",
        }.get((raw or "").strip(), (raw or "").strip())

    def _add(parts: list[str], title: str, value) -> None:
        if value is None or value == "" or value == []:
            return
        if isinstance(value, (list, set, tuple)):
            v = ", ".join([str(x) for x in value if str(x).strip()])
        else:
            v = str(value)
        v = v.strip()
        if not v:
            return
        parts.append(f"{title}: {v}")

    # Шапка (сделка, тип, ветка)
    head: list[str] = []
    if (dt := d.get("deal_type")):
        head.append("Аренда" if dt == "rent" else "Продажа")
    if (tp := d.get("type")):
        head.append(_tlabel(tp))
    if d.get("__flat_mode") and d.get("market"):
        head.append(str(d.get("market")))  # Новостройка/Вторичка
    if d.get("__country_mode") and d.get("country_object_type"):
        head.append(str(d.get("country_object_type")))  # Дом/Участок/...
    if d.get("__commercial_mode") and d.get("comm_object_type"):
        head.append(str(d.get("comm_object_type")))  # Офис/ПСН/...

    # Тело (параметры)
    body: list[str] = []

    # Этаж / этажность (красиво слепляем 5/17)
    floor = d.get("floor")
    floors_total = d.get("floors_total")
    if floor and floors_total:
        body.append(f"Этаж: {floor}/{floors_total}")
    elif floor:
        body.append(f"Этаж: {floor}")
    elif floors_total:
        body.append(f"Этажность: {floors_total}")

    # Площадь/комнаты/кухня (числа -> с единицами; диапазоны оставляем как есть)
    ta = d.get("total_area")
    ka = d.get("kitchen_area")
    rooms = d.get("rooms")
    _add(body, "Площадь", f"{ta} м²" if isinstance(ta, (int, float)) else ta)
    if rooms is not None:
        _add(body, "Комнаты", rooms)
    if ka is not None:
        _add(body, "Кухня", f"{ka} м²" if isinstance(ka, (int, float)) else ka)

    # Квартира — доп. Атрибуты
    if d.get("__flat_mode"):
        _add(body, "Срок сдачи", d.get("completion_term"))
        _add(body, "Способ продажи", d.get("sale_method"))
        _add(body, "Ипотека", d.get("mortgage_ok"))
        _add(body, "Санузел", d.get("bathroom_type"))
        _add(body, "Окна", d.get("windows"))
        _add(body, "Тип дома", d.get("house_type"))
        _add(body, "Лифт", d.get("lift"))
        _add(body, "Парковка", d.get("parking"))
        _add(body, "Ремонт", d.get("renovation") or d.get("apt_condition"))
        _add(body, "Планировка", d.get("layout"))
        _add(body, "Балкон", d.get("balcony"))
        ch = d.get("ceiling_height_m")
        if ch:
            _add(body, "Потолки", f"{ch} м")

    # Загородная — дом/участок и мультивыборы
    if d.get("__country_mode"):
        _add(body, "Площадь дома", d.get("country_house_area_m2"))
        _add(body, "Участок", d.get("country_plot_area_sotki"))
        _add(body, "Дистанция", d.get("country_distance_km"))
        _add(body, "Этажей", d.get("country_floors"))
        _add(body, "Комнаты", d.get("country_rooms"))
        if d.get("country_object_type") and "участ" not in str(d.get("country_object_type")).lower():
            _add(body, "Категория земель", d.get("country_land_category_house"))
        else:
            _add(body, "Категория земель", d.get("country_land_category_plot"))
        _add(body, "Состояние", d.get("country_renovation"))
        _add(body, "Санузел", d.get("country_toilet"))
        _add(body, "Материал стен", d.get("country_wall_material"))
        _add(body, "Парковка", d.get("country_parking"))
        _add(body, "Доступность", d.get("country_transport"))

        # Мультивыборы: коды -> метки
        def _labels_from_codes(key: str, codes: list[str] | set[str] | None) -> str | None:
            if not codes:
                return None
            cmap = {c: l for c, l in COUNTRY_MULTI_ENUMS.get(key, [])}
            items = []
            for c in _normalize_multi_selected(key, codes):
                items.append(cmap.get(c, c))
            return ", ".join(items) if items else None

        _add(body, "Коммуникации", _labels_from_codes("country_utilities", d.get("country_utilities")))
        _add(body, "Для отдыха", _labels_from_codes("country_leisure", d.get("country_leisure")))
        _add(body, "Коммуникации (участок)", _labels_from_codes("country_communications_plot", d.get("country_communications_plot")))

    # Коммерческая
    if d.get("__commercial_mode"):
        _add(body, "Площадь помещения", d.get("total_area"))
        la = d.get("land_area")
        _add(body, "Площадь участка", f"{la}" if la is not None else None)
        _add(body, "Тип здания", d.get("comm_building_type"))
        _add(body, "Объект целиком", d.get("comm_whole_object"))
        _add(body, "Отделка", d.get("comm_finish"))
        _add(body, "Вход", d.get("comm_entrance"))
        _add(body, "Парковка", d.get("comm_parking"))
        _add(body, "Планировка", d.get("comm_layout"))

    # Общие поля (если были пройдены в анкете для любого типа)
    _add(body, "Год/состояние", d.get("year_or_condition"))
    _add(body, "Коммуникации (текст)", d.get("utilities"))
    _add(body, "Локация", d.get("location"))
    _add(body, "Особенности", d.get("features"))

    # Формирование строки: шапка через запятую, параметры — через точку с запятой
    head_str = ", ".join([h for h in head if h])
    body_str = "; ".join(body)
    if head_str and body_str:
        return f"{head_str}; {body_str}"
    return head_str or body_str or ""

async def _with_summary(state: FSMContext, text: str) -> str:
    d = await state.get_data()
    summary = _compose_summary(d)
    return (f"• {summary}\n\n{text}") if summary else text

def _kb_add_back_exit(rows: list[list[InlineKeyboardButton]]) -> list[list[InlineKeyboardButton]]:
    """
    Унифицированный нижний ряд для всех экранов: Назад/Выход.
    'Назад' -> desc_back; 'Выход' -> desc_start (главное меню).
    """
    rows.append([
        InlineKeyboardButton(text="⬅️ Назад", callback_data="desc_back"),
        InlineKeyboardButton(text="🚪 Выход", callback_data="desc_start"),
    ])
    return rows



def _is_sub_active(user_id: int) -> bool:
    """
    Новая модель: активная подписка = привязанная НЕ удалённая карта
    (автопродление включено). Дату sub_until больше не читаем из variables.
    """
    return bool(billing_db.has_saved_card(user_id))

def _format_access_text(user_id: int) -> str:
    trial_hours = trial_remaining_hours(user_id)
    # Есть активный триал — показываем дату окончания, если доступна
    if is_trial_active(user_id):
        try:
            until_dt = app_db.get_trial_until(user_id)
            if until_dt:
                return f'🆓 Бесплатный доступ активен до *{until_dt.date().isoformat()}* (~{trial_hours} ч.)'
        except Exception:
            pass
        return f'🆓 Бесплатный доступ активен ещё *~{trial_hours} ч.*'
    # Нет триала — проверяем подписку
    if _is_sub_active(user_id):
        return '✅ Подписка активна (автопродление включено)'
    return '😢 Бесплатный период завершён. Оформи подписку, чтобы продолжить.'

def _has_access(user_id: int) -> bool:
    return bool(is_trial_active(user_id) or _is_sub_active(user_id))

# ==========================
# Безопасный ACK callback-запроса (чтобы не получить "query is too old")
# ==========================
async def _cb_ack(cb: _CbType, text: Optional[str] = None, show_alert: bool = False) -> None:
    """
    Немедленно отвечаем на callback, а любые ошибки игнорируем.
    Так мы снимаем "песочные часы" у пользователя и избегаем TelegramBadRequest.
    """
    try:
        await cb.answer(text=text, show_alert=show_alert, cache_time=0)
    except TelegramBadRequest:
        # query уже протух/закрыт — просто игнорируем
        pass
    except Exception:
        # на всякий случай — не роняем обработчик
        pass

# ==========================
# Тексты
# ==========================
DESC_INTRO  = """Заполните короткую анкету и получите продающее описание вашего объекта для Авито, ЦИАН или ваших соцсетей.
Наш алгоритм обучен на детятках тысяч самых конверсионных описаний.

🧩 Давайте соберём базовые характеристики объекта. Отвечайте по шагам:
"""
ASK_TYPE    = "1️⃣ Выберите тип недвижимости:"
ASK_DEAL    = "0️⃣ Выберите тип сделки:"
ASK_CLASS   = "2️⃣ Уточните класс квартиры:"
ASK_COMPLEX = "3️⃣ Объект в новостройке / ЖК?"
ASK_AREA    = "4️⃣ Где расположен объект?"
# Далее вместо свободного комментария идёт обязательная анкета (структурированные шаги)
ASK_FORM_TOTAL_AREA      = "5️⃣ Введите общую площадь объекта (в м²). Пример: 56.4"
ASK_FORM_FLOORS_TOTAL    = "6️⃣ Введите этажность здания (количество этажей в доме). Пример: 17"
ASK_FORM_FLOOR           = "7️⃣ Введите этаж расположения объекта. Пример: 5"
ASK_FORM_KITCHEN_AREA    = "8️⃣ Введите площадь кухни (в м²). Если не применимо — укажите 0. Пример: 10.5"
ASK_FORM_ROOMS           = "9️⃣ Укажите количество комнат (для жилых объектов). Если не применимо — укажите 0. Пример: 2"
ASK_FORM_YEAR_COND       = "🔟 Укажите год постройки ИЛИ состояние: «новостройка», «вторичка», «требуется ремонт». Примеры: 2012 / новостройка"
ASK_FORM_UTILITIES       = "1️⃣1️⃣ Перечислите коммуникации через запятую: отопление, вода, газ, электричество, интернет. Пример: отопление, вода, электричество"
ASK_FORM_APT_COND        = "🔟 Выберите состояние квартиры:"
ASK_FORM_LOCATION        = "1️⃣2️⃣ Укажите локацию: район и ближайшее метро/транспорт. Пример: Пресненский, м. Улица 1905 года"
ASK_FORM_FEATURES        = "1️⃣3️⃣ Укажите особенности/удобства через запятую (балкон, парковка, лифт, охрана и т.д.). Пример: балкон, лифт, консьерж"
ASK_FREE_COMMENT         = "1️⃣4️⃣ При желании добавьте свободный комментарий про объект — детали планировки, состояние, окружение и т.п.\n\n✍️ Отправьте текст одним сообщением (минимум 50 символов).\nЕсли комментарий не нужен — нажмите «Пропустить»."

GENERATING = "⏳ Генерирую описание… это займёт до минуты."
ERROR_TEXT = "😔 Не получилось сгенерировать описание. Попробуйте ещё раз."

COUNTRY_ASK_AREA = "Где расположен загородный объект?"

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

def text_descr_intro(user_id: int) -> str:
    """Стартовый текст с информацией о доступе (как в plans). Начинаем с типа сделки."""
    return f"{DESC_INTRO}\n\n{_format_access_text(user_id)}\n\n{ASK_DEAL}"

# ==========================
# Квартира: новые тексты / опции
# ==========================
FLAT_ASK_MARKET          = "1️⃣ Выберите рынок: новостройка или вторичка."
FLAT_ASK_COMPLETION_TERM = "Укажите срок сдачи (квартал и год). Пример: 4 кв. 2026"
FLAT_ASK_SALE_METHOD     = "Выберите способ продажи (для новостроек)."
FLAT_ASK_ROOMS           = "Укажите количество комнат."
FLAT_ASK_MORTGAGE        = "Подходит для ипотеки?"
FLAT_ASK_BATHROOM        = "Санузел:"
FLAT_ASK_WINDOWS         = "Окна:"
FLAT_ASK_HOUSETYPE       = "Тип дома:"
FLAT_ASK_LIFT            = "Лифт:"
FLAT_ASK_PARKING         = "Парковка:"
FLAT_ASK_RENOVATION      = "Ремонт:"
FLAT_ASK_LAYOUT          = "Планировка комнат:"
FLAT_ASK_BALCONY         = "Балкон/лоджия:"
FLAT_ASK_CEILING         = "Высота потолков (м, опционально). Пример: 2.7"
FLAT_ASK_TOTAL_AREA      = "Общая площадь (м²): выберите диапазон"
FLAT_ASK_KITCHEN_AREA    = "Площадь кухни (м²): выберите диапазон"
FLAT_ASK_FLOOR           = "Этаж квартиры: выберите вариант"
FLAT_ASK_FLOORS_TOTAL    = "Этажность дома: выберите вариант"

# Справочник опций для кнопок (код, метка)
DESCRIPTION_CLASSES = {
    "econom": "Эконом",
    "comfort": "Комфорт",
    "business": "Бизнес",
    "premium": "Премиум",
}

DESCRIPTION_COMPLEX = {
    "yes": "Да (новостройка/ЖК)",
    "no": "Нет",
}

DESCRIPTION_AREA = {
    "city": "В черте города",
    "out": "За городом",
}

FLAT_ENUMS: dict[str, list[tuple[str, str]]] = {
    "market": [
        ("new", "Новостройка"), ("secondary", "Вторичка"),
    ],
    "completion_term": [
        ("ready",  "Сдан"),
        ("2025Q4", "4 кв. 2025"),
        ("2026Q1", "1 кв. 2026"), ("2026Q2", "2 кв. 2026"),
        ("2026Q3", "3 кв. 2026"), ("2026Q4", "4 кв. 2026"),
        ("2027Q1", "1 кв. 2027"), ("2027Q2", "2 кв. 2027"),
        ("2027Q3", "3 кв. 2027"), ("2027Q4", "4 кв. 2027"),
    ],
    "sale_method": [
        ("dkp", "ДКП"), ("cession", "Переуступка"), ("fz214", "ДДУ"),
    ],
    "rooms": [
        ("studio", "Студия"), ("1", "1"), ("2", "2"), ("3", "3"), ("4plus", "4+"),
    ],
    "mortgage_ok": [
        ("yes", "Да"), ("no", "Нет"),
    ],
    "total_area": [
        ("lt30",  "До 30"), ("30-40", "30–40"), ("40-50", "40–50"),
        ("50-60", "50–60"), ("60-80", "60–80"), ("80-100", "80–100"), ("100+", "100+"),
    ],
    "kitchen_area": [
        ("0-5",  "0–5"), ("6-9", "6–9"), ("10-12", "10–12"),
        ("13-15","13–15"), ("16+", "16+"),
    ],
    "floor": [
        ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5"),
        ("6-9", "6–9"), ("10-14", "10–14"), ("15-19", "15–19"), ("20+", "20+"),
    ],
    "floors_total": [
        ("1-5", "1–5"), ("6-9", "6–9"), ("10-14", "10–14"), ("15-19", "15–19"), ("20+", "20+"),
    ],
    "bathroom_type": [
        ("combined", "Совмещённый"), ("separate", "Раздельный"),
    ],
    "windows": [
        ("yard", "Во двор"), ("street", "На улицу"),
        ("sunny", "На солнечную сторону"), ("mixed", "Разное"),
    ],
    "house_type": [
        ("brick", "Кирпичный"), ("panel", "Панельный"),
        ("block", "Блочный"), ("monolith", "Монолитный"), ("mono_brick", "Монолит-кирпич"),
    ],
    "lift": [
        ("none", "Нет"), ("passenger", "Пассажирский"),
        ("cargo", "Грузовой"), ("both", "Оба"),
    ],
    "parking": [
        ("underground", "Подземная"), ("ground", "Наземная"),
        ("multilevel", "Многоуровневая"), ("yard_open", "Открытая во дворе"),
        ("gated", "За шлагбаумом"),
    ],
    "renovation": [
        ("need", "Требуется"), ("cosmetic", "Косметический"),
        ("euro", "Евро"), ("designer", "Дизайнерский"),
    ],
    "layout": [
        ("isolated", "Изолированные"), ("adjacent", "Смежные"),
        ("mixed", "И то, и другое"),
    ],
    "balcony": [
        ("none", "Нет"), ("balcony", "Балкон"),
        ("loggia", "Лоджия"), ("several", "Несколько"),
    ],
    "ceiling_height_m": [
        ("skip", "Пропустить"),
        ("<=2.5", "≤ 2.5"), ("2.6-2.8", "2.6–2.8"),
        ("2.9-3.1", "2.9–3.1"), (">=3.2", "3.2+"),
    ],
}

# ==========================
# Загородная: тексты / опции
# ==========================
COUNTRY_GROUP_ASK            = "Выберите группу загородного объекта:"
COUNTRY_ASK_OBJECT_TYPE      = "1️⃣ Выберите тип загородного объекта:"
COUNTRY_ASK_HOUSE_AREA       = "Площадь дома (м²): выберите диапазон"
COUNTRY_ASK_PLOT_AREA        = "Площадь участка (сотки): выберите диапазон"
COUNTRY_ASK_DISTANCE         = "Расстояние от города (км): выберите диапазон"
COUNTRY_ASK_FLOORS           = "Этажей в доме:"
COUNTRY_ASK_ROOMS            = "Количество комнат:"
COUNTRY_ASK_LAND_CATEGORY_H  = "Категория земель:"
COUNTRY_ASK_RENOVATION       = "Состояние/ремонт:"
COUNTRY_ASK_TOILET           = "Санузел:"
COUNTRY_ASK_UTILITIES        = "Коммуникации (множественный выбор):"
COUNTRY_ASK_LEISURE          = "Для отдыха (множественный выбор):"
COUNTRY_ASK_WALL_MATERIAL    = "Материал стен:"
COUNTRY_ASK_PARKING          = "Парковка:"
COUNTRY_ASK_TRANSPORT        = "Транспортная доступность:"

COUNTRY_ASK_LAND_CATEGORY_P  = "Категория земель:"
COUNTRY_ASK_PLOT_COMM        = "Коммуникации (множественный выбор):"

# одиночные перечисления
COUNTRY_ENUMS: dict[str, list[tuple[str, str]]] = {
    # Ветка выбора типа внутри «Загородная»
    "country_object_type": [
        ("house",     "Дом"),
        ("dacha",     "Дача"),
        ("cottage",   "Коттедж"),
        ("townhouse", "Таунхаус"),
        ("plot",      "Земельный участок"),
    ],
    # Дом/Дача/Коттедж/Таунхаус
    "country_house_area_m2": [
        ("lt50", "до 50"), ("50-100", "50–100"), ("100-150", "100–150"),
        ("150-200", "150–200"), ("200-300", "200–300"), ("300+", "300+"),
    ],
    "country_plot_area_sotki": [
        ("lt4", "до 4"), ("5-6","5–6"), ("7-10","7–10"),
        ("11-15","11–15"), ("16-20","16–20"), ("20+","20+"),
    ],
    "country_distance_km": [
        ("lt5","до 5"), ("6-10","6–10"), ("11-20","11–20"),
        ("21-30","21–30"), ("31-50","31–50"), ("50+","50+"),
    ],
    "country_floors": [
        ("1","1"), ("2","2"), ("3","3"), ("4+","4+"),
    ],
    "country_rooms": [
        ("1","1"), ("2","2"), ("3","3"), ("4","4"), ("5+","5+"),
    ],
    "country_land_category_house": [
        ("izhs","ИЖС"), ("sad","садоводство"), ("lph","ЛПХ"), ("kfh","КФХ"), ("other","Иное"),
    ],
    "country_renovation": [
        ("need", "Требуется"), ("cosmetic","Косметический"),
        ("euro","Евро"), ("designer","Дизайнерский"),
    ],
    "country_toilet": [
        ("indoor","В доме"), ("outdoor","На улице"), ("both","Оба"),
    ],
    "country_wall_material": [
        ("brick","Кирпич"), ("timber","Брус"), ("log","Бревно"),
        ("aerated","Газоблок"), ("metal","Металл"), ("other","Иное"),
    ],
    "country_parking": [
        ("garage","Гараж"), ("place","Парковочное место"),
        ("carport","Навес"), ("none","Нет"),
    ],
    "country_transport": [
        ("asphalt","Асфальт"), ("bus","Остановка ОТ"), ("rail","ЖД станция"), ("dirt","Грунтовка"),
    ],
    # Земельный участок
    "country_land_category_plot": [
        ("izhs","ИЖС"), ("snt","СНТ"), ("dnp","ДНП"), ("fh","ФХ"), ("lph","ЛПХ"),
    ],
}

# многократный выбор (ключ -> список код/метка)
COUNTRY_MULTI_ENUMS: dict[str, list[tuple[str, str]]] = {
    "country_utilities": [
        ("electricity","Электричество"), ("gas","Газ"), ("heating","Отопление"),
        ("water","Водоснабжение"), ("sewage","Канализация"),
    ],
    "country_leisure": [
        ("banya","Баня"), ("pool","Бассейн"), ("sauna","Сауна"), ("other","Другое"),
    ],
    "country_communications_plot": [
        ("gas","Газ"), ("water","Вода"), ("electricity","Свет"),
        ("border","По границе"), ("none","Нет"),
    ],
}

# ==========================
# Утилиты мультивыбора
# ==========================
def _multi_opts_map(key: str) -> Dict[str, str]:
    """
    Возвращает мапу код->метка для мультивыбора.
    """
    return {code: label for code, label in COUNTRY_MULTI_ENUMS.get(key, [])}

def _normalize_multi_selected(key: str, selected_raw: Optional[List[str] | Set[str]]) -> Set[str]:
    """
    Превращает произвольный список выбранных значений (коды или метки)
    в корректный набор КОДОВ. Нужен на случай, если в стейте оказались метки.
    """
    if not selected_raw:
        return set()
    opts = COUNTRY_MULTI_ENUMS.get(key, [])
    code_by_label = {label: code for code, label in opts}
    codes = set()
    for v in selected_raw:
        if v in code_by_label.values():  # уже код
            codes.add(v)
        else:
            # возможно это метка
            code = code_by_label.get(v)
            if code:
                codes.add(code)
    return codes

# ==========================
# Коммерческая: тексты / опции
# ==========================
COMM_ASK_GROUP                = "1️⃣ Выберите вид объекта коммерческой недвижимости:"
COMM_ASK_TOTAL_AREA           = "Площадь помещения (м²). Пример: 250"
COMM_ASK_LAND_AREA            = "Площадь участка (если применимо, м²/сотки). Если не нужно — укажите 0."
COMM_ASK_BUILDING_TYPE        = "Тип здания: выберите вариант"
COMM_ASK_WHOLE_OBJECT         = "Объект целиком? Выберите вариант"
COMM_ASK_FINISH               = "Состояние/отделка: выберите вариант"
COMM_ASK_ENTRANCE             = "Вход: выберите вариант"
COMM_ASK_PARKING_COMM         = "Парковка: выберите вариант"
COMM_ASK_LAYOUT               = "Тип планировки: выберите вариант"

# одиночные перечисления для коммерческой недвижимости
COMM_ENUMS: dict[str, list[tuple[str, str]]] = {
    "comm_object_type": [
        ("office", "Офис"),
        ("psn", "Свободного назначения (ПСН)"),
        ("retail", "Торговая площадь"),
        ("warehouse", "Склад"),
        ("production", "Производство"),
        ("food", "Общепит"),
        ("hotel", "Гостиница"),
    ],
    "comm_building_type": [
        ("bc", "Бизнес-центр"),
        ("mall", "ТЦ"),
        ("admin", "админ. здание"),
        ("residential", "Жилой дом"),
        ("other", "Другое"),
    ],
    "comm_whole_object": [
        ("yes", "Да"), ("no", "Нет"),
    ],
    "comm_finish": [
        ("none", "Без отделки"),
        ("shell", "Черновая"),
        ("clean", "Чистовая"),
        ("office", "Офисная"),
    ],
    "comm_entrance": [
        ("street", "С улицы"),
        ("yard", "Со двора"),
        ("second", "Отдельный второй вход"),
    ],
    "comm_parking": [
        ("none", "Нет"),
        ("street", "На улице"),
        ("covered", "Крытая"),
        ("underground", "Подземная"),
        ("guest", "Гостевая"),
    ],
    "comm_layout": [
        ("open", "Open space"),
        ("cabinets", "Кабинетная"),
        ("mixed", "Смешанная"),
    ],
}



# Клавиатура выбора вида в «Коммерческой»
def kb_commercial_entry() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for code, label in COMM_ENUMS["comm_object_type"]:
        rows.append([InlineKeyboardButton(text=label, callback_data=f"desc_comm_entry_{code}")])
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================
# Клавиатуры
# ==========================

# --- НОВОЕ: стартовая клавиатура с объединением «Дом» + «Земельный участок» в «Загородная недвижимость»
def kb_type_merged() -> InlineKeyboardMarkup:
    """
    Фиксированный стартовый экран без зависимости от ai_cfg.DESCRIPTION_TYPES.
    Только нужные три кнопки.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="Квартира",                  callback_data="desc_type_flat")],
        [InlineKeyboardButton(text="Загородная недвижимость",   callback_data="desc_type_country")],
        [InlineKeyboardButton(text="Коммерческая недвижимость", callback_data="desc_type_commercial")],
    ]
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- НОВОЕ: первый шаг — тип сделки
def kb_deal() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="Продажа", callback_data="desc_deal_sale"),
            InlineKeyboardButton(text="Аренда", callback_data="desc_deal_rent")
         ],
        [InlineKeyboardButton(text="🗂 История запросов", callback_data="desc_history")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- НОВОЕ: первый шаг внутри «Загородная» — только два варианта
def kb_country_entry() -> InlineKeyboardMarkup:
    """
    Загородные сценарии: Дом / Земельный участок.
    Дальше используется существующая логика house/plot.
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="Дом",               callback_data="desc_country_entry_house")],
        [InlineKeyboardButton(text="Земельный участок", callback_data="desc_country_entry_plot")],
    ]
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)
def kb_class()   -> InlineKeyboardMarkup: return _kb_from_map(DESCRIPTION_CLASSES,"desc_class_",  1)
def kb_complex() -> InlineKeyboardMarkup: return _kb_from_map(DESCRIPTION_COMPLEX,"desc_complex_",1)
def kb_area()    -> InlineKeyboardMarkup: return _kb_from_map(DESCRIPTION_AREA,   "desc_area_",   1)

# --- НОВОЕ: кнопки расположения для «Загородной недвижимости»
def kb_country_area() -> InlineKeyboardMarkup:
    """
    Две фиксированные кнопки:
    — За городом  -> area=out
    — В черте города -> area=city
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="За городом",      callback_data="desc_country_area_out")],
        [InlineKeyboardButton(text="В черте города",  callback_data="desc_country_area_city")],
    ]
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ==========================
# Утилиты редактирования
# ==========================
async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    """Обновить текст/подпись и клавиатуру текущего сообщения (без создания нового)."""
    try:
        await msg.edit_text(text, reply_markup=kb); return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_caption(caption=text, reply_markup=kb); return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

async def _send_step(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None, *, new: bool = False) -> None:
    """
    Унифицированный вывод шага:
    - new=False → редактируем текущее сообщение (для callback-сценариев).
    - new=True → отправляем НОВОЕ сообщение (для текстового ввода).
    """
    if new:
        await msg.answer(text, reply_markup=kb)
    else:
        await _edit_text_or_caption(msg, text, kb)

async def _edit_or_replace_with_photo_file(
    bot: Bot, msg: Message, file_path: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    """
    Поменять текущее сообщение на фото с подписью и клавиатурой.
    Если редактирование невозможно (сообщение было текстовым и т.п.) — удаляем и шлём новое фото.
    """
    try:
        media = InputMediaPhoto(media=FSInputFile(file_path), caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        # удаляем старое и отправляем новое фото (визуально как «апдейт» экрана)
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(file_path), caption=caption, reply_markup=kb)

def _split_for_telegram(text: str, limit: int = 4000) -> List[str]:
    """Нарезает ответ на куски <= limit символов по строкам/абзацам."""
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    chunk: List[str] = []
    length = 0
    for line in text.splitlines(True):  # сохраняем \n
        if length + len(line) > limit and chunk:
            parts.append("".join(chunk)); chunk = [line]; length = len(line)
        else:
            chunk.append(line); length += len(line)
    if chunk:
        parts.append("".join(chunk))
    return parts

# ==========================
# Клавиатуры из конфига
# ==========================
def _kb_from_map(m: Dict[str, str], prefix: str, columns: int = 1) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key, label in m.items():
        btn = InlineKeyboardButton(text=label, callback_data=f"{prefix}{key}")
        if columns <= 1:
            rows.append([btn])
        else:
            row.append(btn)
            if len(row) >= columns:
                rows.append(row); row = []
    if row:
        rows.append(row)
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_enum(key: str) -> InlineKeyboardMarkup:
    """Клавиатура для перечислимого поля + «Свой вариант…»."""
    # поддержка FLAT / COUNTRY / COMM
    opts = FLAT_ENUMS.get(key, []) or COUNTRY_ENUMS.get(key, []) or COMM_ENUMS.get(key, [])
    rows: list[list[InlineKeyboardButton]] = []
    for code, label in opts:
        rows.append([InlineKeyboardButton(text=label, callback_data=f"desc_enum_{key}_{code}")])
    rows.append([InlineKeyboardButton(text="✍️ Свой вариант…", callback_data=f"desc_enum_other_{key}")])
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_skip_field(key: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"desc_flat_skip_{key}")]]
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_multi_enum(key: str, selected: Optional[Set[str]] = None) -> InlineKeyboardMarkup:
    """
    Мультивыбор с чекбоксами + кнопка «Готово».
    """
    # Используем только коды (на случай, если передали метки)
    sel = _normalize_multi_selected(key, selected or set())
    opts = COUNTRY_MULTI_ENUMS.get(key, [])
    rows: list[list[InlineKeyboardButton]] = []
    for code, label in opts:
        # Требование: смайлик только у выбранных, у остальных — «чистая» метка
        text = f"✅ {label}" if code in sel else label
        rows.append([InlineKeyboardButton(text=text, callback_data=f"desc_multi_{key}_{code}")])
    rows.append([InlineKeyboardButton(text="Готово ➡️", callback_data=f"desc_multi_done_{key}")])
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_back_only() -> InlineKeyboardMarkup:
    """
    Инлайн-клавиатура с единственной кнопкой «Назад».
    Нужна для текстовых шагов без предустановленных вариантов.
    Поведение, как и в остальных клавиатурах — уходит на первый экран алгоритма.
    """
    rows: list[list[InlineKeyboardButton]] = []
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_history_list(items: list[dict]) -> InlineKeyboardMarkup:
    """
    Список последних записей истории (кнопка на каждую запись).
    """
    rows: list[list[InlineKeyboardButton]] = []
    if not items:
        rows.append([InlineKeyboardButton(text="Записей пока нет", callback_data="noop")])
    else:
        for it in items:
            title = f"#{it['id']} • {it['created_at']} • {it.get('preview','')}"
            rows.append([InlineKeyboardButton(text=title[:64], callback_data=f"desc_hist_item_{it['id']}")])

    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="desc_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_retry() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Ещё раз", callback_data="description")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.descr_home")]  # внутренняя «Назад»
    ])

def _kb_history_item(entry_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔁 Повторить запрос", callback_data=f"desc_hist_repeat_{entry_id}")],
        [InlineKeyboardButton(text="🗑 Удалить",          callback_data=f"desc_hist_del_{entry_id}")],
        [InlineKeyboardButton(text="⬅️ К списку",        callback_data="desc_history")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_apt_condition() -> InlineKeyboardMarkup:
    """
    Блок выбора состояния квартиры (кнопки) + «Назад».
    """
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="1. Дизайнерский ремонт",      callback_data="desc_cond_designer")],
        [InlineKeyboardButton(text="2. «Евро-ремонт»",            callback_data="desc_cond_euro")],
        [InlineKeyboardButton(text="3. Косметический",            callback_data="desc_cond_cosmetic")],
        [InlineKeyboardButton(text="4. Требует ремонта",          callback_data="desc_cond_need")],
    ]
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================
# HTTP callback от executor'а (fire-and-forget результат описания)
# ==========================
async def _cb_description_result(request: web.Request):
    """
    Приём результата генерации от executor'а.
    Если якорь — медиа (фото/видео), редактируем caption вместо текста,
    чтобы не создавать новое сообщение.
    """
    try:
        data = await request.json()
        token  = (data.get("token") or "").strip()
        if EXECUTOR_CALLBACK_TOKEN and token != EXECUTOR_CALLBACK_TOKEN:
            return web.json_response({"error": "forbidden"}, status=403)

        chat_id = int(data["chat_id"])
        msg_id  = int(data["msg_id"])
        text    = (data.get("text") or "").strip()
        error   = (data.get("error") or "").strip()
        fields  = data.get("fields") or {}
    except Exception as e:
        return web.json_response({"error": "bad_request", "detail": str(e)}, status=400)

    bot: Bot = request.app["bot"]

    # --- Ошибка от executor'а: заменить якорь на ERROR_TEXT (text -> caption -> новое) ---
    if error and not text:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=ERROR_TEXT, reply_markup=kb_retry())
        except TelegramBadRequest:
            try:
                await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=ERROR_TEXT, reply_markup=kb_retry())
            except TelegramBadRequest:
                await bot.send_message(chat_id, ERROR_TEXT, reply_markup=kb_retry())
        return web.json_response({"ok": True})

    # --- Успешный текст: первый чанк заменяет якорь (text -> caption -> новое), хвост — отдельными сообщениями ---
    parts = _split_for_telegram(text)
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=parts[0], reply_markup=kb_retry())
    except TelegramBadRequest:
        try:
            await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=parts[0], reply_markup=kb_retry())
        except TelegramBadRequest:
            sent = await bot.send_message(chat_id, parts[0], reply_markup=kb_retry())
            msg_id = sent.message_id
    for p in parts[1:]:
        await bot.send_message(chat_id, p)

    # --- История (user_id == chat_id) ---
    try:
        app_db.description_add(user_id=chat_id, fields=fields, result_text=text)
    except Exception:
        pass

    return web.json_response({"ok": True})


APT_COND_LABELS = {
    "designer": "Дизайнерский ремонт",
    "euro":     "Евро-ремонт",
    "cosmetic": "Косметический",
    "need":     "Требует ремонта",
}

def kb_skip_comment() -> InlineKeyboardMarkup:
    """Кнопка «Пропустить» для необязательного финального шага."""
    rows = [[InlineKeyboardButton(text="⏭ Пропустить", callback_data="desc_comment_skip")]]
    _kb_add_back_exit(rows)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Кнопка к офферу подписки
SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)


def mount_internal_routes(app: web.Application, bot: Bot):
    """
    Вызывается при старте: добавляет POST /api/v1/description/result и кладёт bot в app['bot'].
    """
    app["bot"] = bot
    app.router.add_post("/api/v1/description/result", _cb_description_result)

# ==========================
# HTTP к контроллеру
# ==========================
async def _request_description_text(fields: dict, *, timeout_sec: int = 70) -> str:
    """
    Шлём СЫРЫЕ поля в executor (/api/v1/description/generate) и ждём чистый текст.
    fields = {type, apt_class?, in_complex, area, comment}
    """
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/description/generate"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=t) as session:
        async with session.post(url, json=fields) as resp:
            if resp.status != 200:
                try:
                    data = await resp.json()
                    detail = data.get("detail") or data.get("error") or str(data)
                except Exception:
                    detail = await resp.text()
                raise RuntimeError(f"Executor HTTP {resp.status}: {detail}")
            data = await resp.json()
            txt = (data or {}).get("text", "").strip()
            if not txt:
                raise RuntimeError("Executor returned empty text")
            return txt

# --- Новый: асинхронная постановка задачи без ожидания результата ---
async def _request_description_async(fields: dict, *, chat_id: int, msg_id: int, timeout_sec: int = 10) -> None:
    """
    Отправляет задачу в executor и НЕ ждёт результата.
    Executor позже вызовет наш callback.
    """
    if not BOT_PUBLIC_BASE_URL:
        raise RuntimeError("BOT_PUBLIC_BASE_URL is not set")
    callback_url = str(URL(BOT_PUBLIC_BASE_URL) / "api" / "v1" / "description" / "result")

    payload = dict(fields)
    payload.update({
        "callback_url": callback_url,
        "callback_token": EXECUTOR_CALLBACK_TOKEN,
        "chat_id": chat_id,
        "msg_id": msg_id,
    })

    url = f"{EXECUTOR_BASE_URL.rstrip('/')}/api/v1/description/generate"
    t = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=t) as session:
        async with session.post(url, json=payload) as resp:
            if resp.status not in (200, 202):
                try:
                    data = await resp.json()
                    detail = data.get("detail") or data.get("error") or str(data)
                except Exception:
                    detail = await resp.text()
                raise RuntimeError(f"Executor HTTP {resp.status}: {detail}")

# ==========================
# Шаги (callbacks)
# ==========================
DESCR_HOME_IMG_REL = "img/bot/descr_home.png"

async def start_description_flow(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Старт: пытаемся заменить текущее сообщение на картинку (главный экран раздела)
    с подписью (DESC_INTRO + ASK_TYPE) и кнопками. Если файла нет — фолбэк на текст.
    """
    await _cb_ack(cb)
    user_id = cb.message.chat.id
    # Контроль доступа (как в plans/design)
    if not _has_access(user_id):
        # Сообщение об отсутствии доступа идентично подходу в plans.py
        if not _is_sub_active(user_id):
            await _edit_text_or_caption(cb.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(cb.message, SUB_PAY, SUBSCRIBE_KB)
        return

    await state.clear()
    caption = text_descr_intro(user_id)
    img_path = get_file_path(DESCR_HOME_IMG_REL)

    if os.path.exists(img_path):
        await _edit_or_replace_with_photo_file(bot, cb.message, img_path, caption, kb_deal())
    else:
        await _edit_text_or_caption(cb.message, caption, kb_deal())

    # Используем существующий стейт, но первым шагом ждём выбор сделки
    await state.set_state(DescriptionStates.waiting_for_type)
    await state.update_data(deal_type=None)

async def handle_deal(cb: CallbackQuery, state: FSMContext):
    """Тип сделки = sale / rent → затем спрашиваем тип недвижимости."""
    await _cb_ack(cb)
    payload = cb.data.removeprefix("desc_deal_")
    if payload not in {"sale", "rent"}:
        return
    await state.update_data(deal_type=payload)
    # Переходим к выбору типа недвижимости
    await _edit_text_or_caption(cb.message, ASK_TYPE, kb_type_merged())
    # Стейт оставляем тем же (waiting_for_type), дальше сработает handle_type

async def handle_type(cb: CallbackQuery, state: FSMContext):
    """
    type = flat / house / land ...
    - flat → НОВЫЙ сценарий «Квартира»: карта вопросов из ТЗ
    - house → пропускаем «новостройка/ЖК», сразу спрашиваем расположение
    - иное → спрашиваем «новостройка/ЖК» (как раньше)
    """
    await _cb_ack(cb)
    data = await state.get_data()
    if not data.get("deal_type"):
        # просим сперва указать тип сделки
        await _edit_text_or_caption(cb.message, f"Сначала укажите тип сделки.\n\n{ASK_DEAL}", kb_deal())
        return
    val = cb.data.removeprefix("desc_type_")
    await state.update_data(type=val)

    if val == "flat":
        # Новый сценарий для квартиры: начинаем с рынка
        await state.update_data(
            __form_keys=["market"],
            __form_step=0,
            __flat_mode=True,
            __awaiting_other_key=None,
            __awaiting_free_comment=False
        )
        await _edit_text_or_caption(cb.message, await _with_summary(state, FLAT_ASK_MARKET), _kb_enum("market"))
        await state.set_state(DescriptionStates.waiting_for_comment)
        return
    elif val in {"country"}:
        await state.update_data(
            __country_mode=True,
            __flat_mode=False,
            __form_keys=[],            # заполним после выбора варианта
            __form_step=0,
            __awaiting_other_key=None,
            __awaiting_free_comment=False
        )
        await _edit_text_or_caption(cb.message, await _with_summary(state, COUNTRY_GROUP_ASK), kb_country_entry())
        await state.set_state(DescriptionStates.waiting_for_comment)
        return
    elif val == "house" or val == "land":
        # СКИП «новостройка/ЖК» для дома, идём сразу к расположению
        await _edit_text_or_caption(cb.message, ASK_AREA, kb_area())
        await state.set_state(DescriptionStates.waiting_for_area)
    elif val in {"commercial"}:
        # Вход в коммерческую недвижимость: сначала вид объекта
        await state.update_data(
            __commercial_mode=True,
            __country_mode=False,
            __flat_mode=False,
            __form_keys=["comm_object_type"],
            __form_step=0,
            __awaiting_other_key=None,
            __awaiting_free_comment=False,
        )
        await _edit_text_or_caption(cb.message, await _with_summary(state, COMM_ASK_GROUP), kb_commercial_entry())
        await state.set_state(DescriptionStates.waiting_for_comment)
        return
    else:
        await _edit_text_or_caption(cb.message, ASK_COMPLEX, kb_complex())
        await state.set_state(DescriptionStates.waiting_for_complex)

async def handle_class(cb: CallbackQuery, state: FSMContext):
    """apt_class = econom / comfort / business / premium (только для квартир)."""
    await _cb_ack(cb)
    val = cb.data.removeprefix("desc_class_")
    await state.update_data(apt_class=val)
    # после класса — вопрос про новостройку/ЖК
    await _edit_text_or_caption(cb.message, ASK_COMPLEX, kb_complex())
    await state.set_state(DescriptionStates.waiting_for_complex)

async def handle_complex(cb: CallbackQuery, state: FSMContext):
    """in_complex = yes / no"""
    await _cb_ack(cb)
    val = cb.data.removeprefix("desc_complex_")
    await state.update_data(in_complex=val)
    await _edit_text_or_caption(cb.message, await _with_summary(state, ASK_AREA), kb_area())
    await state.set_state(DescriptionStates.waiting_for_area)

async def handle_area(cb: CallbackQuery, state: FSMContext):
    """area = city / out → затем просим свободный комментарий (или «Пропустить»)."""
    await _cb_ack(cb)
    val = cb.data.removeprefix("desc_area_")
    await state.update_data(area=val)

    # Инициализируем последовательность обязательных шагов анкеты
    data = await state.get_data()
    obj_type = (data.get("type") or "").strip()  # flat/house/land/office/...

    # Персонализированные наборы вопросов по типам:
    # - flat (квартира): всё релевантно (включая этаж, кухня, комнаты, год/состояние)
    # - house (дом): нет «этаж» (floor), есть этажность дома, комнаты, кухня, год/состояние
    # - office (офис): этажность здания и этаж офиса, без «кухни» и «комнат»
    # - land (земля/участок): только площадь, коммуникации, локация, особенности — НЕТ этажности/этажей/кухни/комнат/года
    if obj_type == "flat":
        form_keys: List[str] = [
            "total_area",
            "floors_total",
            "floor",
            "kitchen_area",
            "rooms",
            "apt_condition",   # <-- для квартиры состояние по кнопкам
            "utilities",
            "location",
            "features",
        ]
    elif obj_type == "house":
        form_keys = [
            "total_area",
            "floors_total",
            "kitchen_area",
            "rooms",
            "year_or_condition",
            "utilities",
            "location",
            "features",
        ]
    elif obj_type == "office":
        form_keys = [
            "total_area",
            "floors_total",
            "floor",
            "year_or_condition",
            "utilities",
            "location",
            "features",
        ]
    elif obj_type == "land":
        form_keys = [
            "total_area",
            "utilities",
            "location",
            "features",
        ]
    else:
        # безопасный дефолт — минимально общий набор
        form_keys = ["total_area", "utilities", "location", "features"]

    await state.update_data(__form_keys=form_keys, __form_step=0, __awaiting_free_comment=False)

    # Попросим первый шаг
    first_key = form_keys[0]
    if first_key == "apt_condition":
        # если по какой-то причине первым идёт состояние — показываем кнопки
        await _edit_text_or_caption(cb.message, await _with_summary(state, ASK_FORM_APT_COND), kb_apt_condition())
    else:
        # текстовый шаг: добавить кнопку «Назад»
        await _edit_text_or_caption(cb.message, await _with_summary(state, _form_prompt_for_key(first_key)), _kb_back_only())
    await state.set_state(DescriptionStates.waiting_for_comment)  # используем существующий стейт как «анкета»

# ==========================
# Квартира: шаги/подсказки
# ==========================
def _flat_after_market_keys() -> list[str]:
    """Поля, которые идут для обеих веток рынка."""
    return [
        "rooms", "mortgage_ok",
        "total_area", "kitchen_area", "floor", "floors_total",
        "bathroom_type", "windows", "house_type", "lift", "parking",
        "renovation", "layout", "balcony", "ceiling_height_m",
    ]

def _flat_prompt_for_key(key: str) -> str:
    return {
        "market":            FLAT_ASK_MARKET,
        "completion_term":   FLAT_ASK_COMPLETION_TERM,
        "sale_method":       FLAT_ASK_SALE_METHOD,
        "rooms":             FLAT_ASK_ROOMS,
        "mortgage_ok":       FLAT_ASK_MORTGAGE,
        "total_area":        FLAT_ASK_TOTAL_AREA,
        "kitchen_area":      FLAT_ASK_KITCHEN_AREA,
        "floor":             FLAT_ASK_FLOOR,
        "floors_total":      FLAT_ASK_FLOORS_TOTAL,
        "bathroom_type":     FLAT_ASK_BATHROOM,
        "windows":           FLAT_ASK_WINDOWS,
        "house_type":        FLAT_ASK_HOUSETYPE,
        "lift":              FLAT_ASK_LIFT,
        "parking":           FLAT_ASK_PARKING,
        "renovation":        FLAT_ASK_RENOVATION,
        "layout":            FLAT_ASK_LAYOUT,
        "balcony":           FLAT_ASK_BALCONY,
        "ceiling_height_m":  FLAT_ASK_CEILING,
    }.get(key, "Введите значение:")

async def _ask_next_flat_step(msg: Message, state: FSMContext, *, new: bool = False):
    data = await state.get_data()
    keys: list[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    if step >= len(keys):
        # Все поля собраны → переходим к свободному комментарию
        await state.update_data(__awaiting_free_comment=True)
        await _send_step(msg, await _with_summary(state, ASK_FREE_COMMENT), kb_skip_comment(), new=new)
        return

    key = keys[step]

    # Все поля для квартиры задаются кнопками (опционально — «Свой вариант…»)
    if key in {
        "market", "completion_term", "sale_method", "rooms", "mortgage_ok",
        "total_area", "kitchen_area", "floor", "floors_total",
        "bathroom_type", "windows", "house_type", "lift", "parking",
        "renovation", "layout", "balcony", "ceiling_height_m"
    }:
        await _send_step(msg, await _with_summary(state, _flat_prompt_for_key(key)), _kb_enum(key), new=new)
        return
    # На всякий случай (если вдруг шаг без предустановленных вариантов)
    await _send_step(msg, await _with_summary(state, _form_prompt_for_key(key)), _kb_back_only(), new=new)

# ==========================
# Коммерческая: шаги/подсказки
# ==========================
def _commercial_prompt_for_key(key: str) -> str:
    return {
        "comm_object_type":   COMM_ASK_GROUP,
        "total_area":         COMM_ASK_TOTAL_AREA,
        "land_area":          COMM_ASK_LAND_AREA,
        "comm_building_type": COMM_ASK_BUILDING_TYPE,
        "comm_whole_object":  COMM_ASK_WHOLE_OBJECT,
        "comm_finish":        COMM_ASK_FINISH,
        "comm_entrance":      COMM_ASK_ENTRANCE,
        "comm_parking":       COMM_ASK_PARKING_COMM,
        "comm_layout":        COMM_ASK_LAYOUT,
    }.get(key, "Выберите вариант:")

async def _ask_next_commercial_step(msg: Message, state: FSMContext, *, new: bool = False):
    data = await state.get_data()
    keys: list[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    if step >= len(keys):
        await state.update_data(__awaiting_free_comment=True)
        await _send_step(msg, await _with_summary(state, ASK_FREE_COMMENT), kb_skip_comment(), new=new)
        return

    key = keys[step]
    # перечисления
    if key in {
        "comm_object_type", "comm_building_type", "comm_whole_object",
        "comm_finish", "comm_entrance", "comm_parking", "comm_layout"
    }:
        await _send_step(msg, await _with_summary(state, _commercial_prompt_for_key(key)), _kb_enum(key), new=new)
        return
    # числовые/текстовые поля — показываем подсказку + кнопку «Назад»
    await _send_step(msg, await _with_summary(state, _commercial_prompt_for_key(key)), _kb_back_only(), new=new)

def _country_prompt_for_key(key: str) -> str:
    return {
        "country_object_type":        COUNTRY_ASK_OBJECT_TYPE,
        "country_house_area_m2":      COUNTRY_ASK_HOUSE_AREA,
        "country_plot_area_sotki":    COUNTRY_ASK_PLOT_AREA,
        "country_distance_km":        COUNTRY_ASK_DISTANCE,
        "country_floors":             COUNTRY_ASK_FLOORS,
        "country_rooms":              COUNTRY_ASK_ROOMS,
        "country_land_category_house":COUNTRY_ASK_LAND_CATEGORY_H,
        "country_renovation":         COUNTRY_ASK_RENOVATION,
        "country_toilet":             COUNTRY_ASK_TOILET,
        "country_utilities":          COUNTRY_ASK_UTILITIES,
        "country_leisure":            COUNTRY_ASK_LEISURE,
        "country_wall_material":      COUNTRY_ASK_WALL_MATERIAL,
        "country_parking":            COUNTRY_ASK_PARKING,
        "country_transport":          COUNTRY_ASK_TRANSPORT,
        # plot-ветка
        "country_land_category_plot": COUNTRY_ASK_LAND_CATEGORY_P,
        "country_communications_plot":COUNTRY_ASK_PLOT_COMM,
    }.get(key, "Выберите вариант:")

COUNTRY_MULTI_KEYS = {"country_utilities", "country_leisure", "country_communications_plot"}

async def _ask_next_country_step(msg: Message, state: FSMContext, *, new: bool = False):
    data = await state.get_data()
    keys: list[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    if step >= len(keys):
        await state.update_data(__awaiting_free_comment=True)
        await _send_step(msg, await _with_summary(state, ASK_FREE_COMMENT), kb_skip_comment(), new=new)
        return

    key = keys[step]
    # мультивыбор
    if key in COUNTRY_MULTI_KEYS:
        selected = _normalize_multi_selected(key, data.get(key) or [])
        await _send_step(msg, await _with_summary(state, _country_prompt_for_key(key)), _kb_multi_enum(key, selected), new=new)
        return
    # обычные перечисления
    await _send_step(msg, await _with_summary(state, _country_prompt_for_key(key)), _kb_enum(key), new=new)

# ==========================
# Анкета: валидация и переходы
# ==========================
def _parse_float(val: str) -> Optional[float]:
    try:
        x = float(val.replace(",", ".").strip())
        return x if x >= 0 else None
    except Exception:
        return None

def _parse_int(val: str) -> Optional[int]:
    if not re.fullmatch(r"\d{1,4}", val.strip()):
        return None
    return int(val.strip())

def _normalize_list(val: str) -> str:
    items = [s.strip() for s in val.split(",") if s.strip()]
    # удалим дубли, сохраняя порядок
    seen = set(); out = []
    for it in items:
        key = it.lower()
        if key not in seen:
            seen.add(key); out.append(it)
    return ", ".join(out)

def _form_prompt_for_key(key: str) -> str:
    return {
        "total_area":       ASK_FORM_TOTAL_AREA,
        "land_area":        COMM_ASK_LAND_AREA,
        "floors_total":     ASK_FORM_FLOORS_TOTAL,
        "floor":            ASK_FORM_FLOOR,
        "kitchen_area":     ASK_FORM_KITCHEN_AREA,
        "rooms":            ASK_FORM_ROOMS,
        "year_or_condition":ASK_FORM_YEAR_COND,
        "apt_condition":    ASK_FORM_APT_COND,
        "utilities":        ASK_FORM_UTILITIES,
        "location":         ASK_FORM_LOCATION,
        "features":         ASK_FORM_FEATURES,
        "completion_term":  FLAT_ASK_COMPLETION_TERM,
        "ceiling_height_m": FLAT_ASK_CEILING,
        # commercial
        "comm_building_type": COMM_ASK_BUILDING_TYPE,
        "comm_whole_object":  COMM_ASK_WHOLE_OBJECT,
        "comm_finish":        COMM_ASK_FINISH,
        "comm_entrance":      COMM_ASK_ENTRANCE,
        "comm_parking":       COMM_ASK_PARKING_COMM,
        "comm_layout":        COMM_ASK_LAYOUT,
    }.get(key, "Введите значение:")

def _validate_and_store(key: str, text: str, data: Dict) -> Optional[str]:
    """Возвращает None, если ок. Иначе — текст ошибки для пользователя."""
    t = text.strip()
    if key == "total_area":
        v = _parse_float(t)
        if v is None or v <= 0:
            return "Введите положительное число в формате м². Пример: 56.4"
        data["total_area"] = v
        return None
    if key == "land_area":
        v = _parse_float(t)
        if v is None or v < 0:
            return "Введите число (м²/сотки) или 0, если не применимо."
        data["land_area"] = v
        return None
    if key == "floors_total":
        v = _parse_int(t)
        if v is None or v <= 0:
            return "Введите целое число этажей. Пример: 17"
        data["floors_total"] = v
        return None
    if key == "floor":
        v = _parse_int(t)
        if v is None or v <= 0:
            return "Введите корректный номер этажа. Пример: 5"
        floors_total = int(data.get("floors_total") or 0)
        if floors_total and (v < 1 or v > floors_total):
            return f"Этаж должен быть от 1 до {floors_total}."
        data["floor"] = v
        return None
    if key == "kitchen_area":
        v = _parse_float(t)
        if v is None or v < 0:
            return "Введите число (м²). Если не применимо — 0."
        data["kitchen_area"] = v
        return None
    if key == "rooms":
        v = _parse_int(t)
        if v is None or v < 0:
            return "Введите неотрицательное целое число комнат. Пример: 2"
        data["rooms"] = v
        return None
    if key == "year_or_condition":
        if re.fullmatch(r"\d{4}", t):
            data["year_or_condition"] = t
            return None
        norm = t.lower()
        if norm in {"новостройка", "вторичка", "требуется ремонт"}:
            data["year_or_condition"] = norm
            return None
        return "Укажите год (например, 2012) или одно из: новостройка, вторичка, требуется ремонт."
    if key == "utilities":
        data["utilities"] = _normalize_list(t)
        return None
    if key == "location":
        if len(t) < 3:
            return "Опишите район и транспорт хотя бы несколькими словами."
        data["location"] = t
        return None
    if key == "features":
        data["features"] = _normalize_list(t)
        return None
    if key == "completion_term":
        if len(t) < 4:
            return "Укажите квартал и год. Пример: 4 кв. 2026"
        data["completion_term"] = t
        return None
    if key == "ceiling_height_m":
        # опциональное поле
        if not t or t.lower().startswith("проп"):
            data["ceiling_height_m"] = None
            return None
        v = _parse_float(t)
        if v is None or v <= 0:
            return "Введите число в метрах. Пример: 2.7 или нажмите «Пропустить»."
        data["ceiling_height_m"] = v
        return None
    # по умолчанию — просто сохранить
    data[key] = t
    return None

# ==========================
# Финал (message/skip)
# ==========================
async def _generate_and_output(
    message: Message,
    state: FSMContext,
    bot: Bot,
    comment: Optional[str],
    *,
    reuse_anchor: bool = False,   # <-- если True, НЕ срываем якорь (используем текущее сообщение)
) -> None:
    """
    Собираем сырые поля и шлём их в executor.
    Если reuse_anchor=True — редактируем текущее сообщение (без создания нового).
    """
    # Повторный контроль доступа перед генерацией (на случай, если стейт «завис»)
    user_id = message.chat.id
    if not _has_access(user_id):
        # Тексты как в plans.py
        text = SUB_FREE if not _is_sub_active(user_id) else SUB_PAY
        try:
            await message.edit_text(text, reply_markup=SUBSCRIBE_KB)
        except TelegramBadRequest:
            try:
                await message.edit_caption(caption=text, reply_markup=SUBSCRIBE_KB)
            except TelegramBadRequest:
                await message.answer(text, reply_markup=SUBSCRIBE_KB)
        await state.clear()
        return

    data = await state.get_data()

    fields = {
        "deal_type":  data.get("deal_type"),  # sale / rent
        "type":       data.get("type"),
        "apt_class":  (data.get("apt_class") if data.get("type") == "flat" else None),
        "in_complex": data.get("in_complex"),
        "area":       data.get("area"),
        "comment":    (comment or "").strip(),
        # Новые структурированные поля анкеты
        "total_area":        data.get("total_area"),
        "floors_total":      data.get("floors_total"),
        "floor":             data.get("floor"),
        "kitchen_area":      data.get("kitchen_area"),
        "rooms":             data.get("rooms"),
        "year_or_condition": data.get("year_or_condition"),
        "utilities":         data.get("utilities"),
        "location_exact":    data.get("location"),
        "features":          data.get("features"),
        # --- для Квартиры (новая карта) ---
        "market":            data.get("market"),           # Новостройка / Вторичка
        "completion_term":   data.get("completion_term"),  # для новостройки
        "sale_method":       data.get("sale_method"),      # ДКП / Переуступка / ФЗ-214/дду
        "mortgage_ok":       data.get("mortgage_ok"),      # Да / Нет
        "bathroom_type":     data.get("bathroom_type"),
        "windows":           data.get("windows"),
        "house_type":        data.get("house_type"),
        "lift":              data.get("lift"),
        "parking":           data.get("parking"),
        "renovation":        data.get("renovation"),
        "layout":            data.get("layout"),
        "balcony":           data.get("balcony"),
        "ceiling_height_m":  data.get("ceiling_height_m"),
        # --- для Загородной (новая карта) ---
        "country_object_type":        data.get("country_object_type"),
        "country_house_area_m2":      data.get("country_house_area_m2"),
        "country_plot_area_sotki":    data.get("country_plot_area_sotki"),
        "country_distance_km":        data.get("country_distance_km"),
        "country_floors":             data.get("country_floors"),
        "country_rooms":              data.get("country_rooms"),
        "country_land_category_house":data.get("country_land_category_house"),
        "country_renovation":         data.get("country_renovation"),
        "country_toilet":             data.get("country_toilet"),
        "country_utilities":          data.get("country_utilities"),
        "country_leisure":            data.get("country_leisure"),
        "country_wall_material":      data.get("country_wall_material"),
        "country_parking":            data.get("country_parking"),
        "country_transport":          data.get("country_transport"),
        # plot-ветка
        "country_land_category_plot": data.get("country_land_category_plot"),
        "country_communications_plot":data.get("country_communications_plot"),
        # --- для Коммерческой (новая карта) ---
        "comm_object_type":   data.get("comm_object_type"),
        "land_area":          data.get("land_area"),
        "comm_building_type": data.get("comm_building_type"),
        "comm_whole_object":  data.get("comm_whole_object"),
        "comm_finish":        data.get("comm_finish"),
        "comm_entrance":      data.get("comm_entrance"),
        "comm_parking":       data.get("comm_parking"),
        "comm_layout":        data.get("comm_layout"),
    }
    # Для ДОМА — принудительно обнуляем in_complex (не применимо)
    if data.get("type") == "house":
        fields["in_complex"] = None

    if reuse_anchor:
        # НЕ срываем якорь: редактируем текущее сообщение
        try:
            await message.edit_text(GENERATING)
        except TelegramBadRequest:
            # если нельзя редактировать (например, это была подпись к фото) — попробуем подпись
            try:
                await message.edit_caption(caption=GENERATING)
            except TelegramBadRequest:
                pass
        anchor_id = message.message_id
    else:
        # создаём НОВОЕ сообщение-экран
        gen_msg = await message.answer(GENERATING)
        anchor_id = gen_msg.message_id

    # --- Новый режим: fire-and-forget, ответ придёт на callback и заменит это сообщение ---
    try:
        await _request_description_async(fields, chat_id=message.chat.id, msg_id=anchor_id)
    except Exception:
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=anchor_id,
                text=ERROR_TEXT,
                reply_markup=kb_retry()
            )
        except TelegramBadRequest:
            await message.answer(ERROR_TEXT, reply_markup=kb_retry())
    finally:
        await state.clear()

async def handle_comment_message(message: Message, state: FSMContext, bot: Bot):
    """
    waiting_for_comment работает в два этапа:
    1) обязательная анкета (__form_keys);
    2) необязательный свободный комментарий (можно «Пропустить»).
    """
    user_text = (message.text or "").strip()
    data = await state.get_data()

    # Если не выбран тип сделки — вернём на первый шаг
    if not data.get("deal_type"):
        await message.answer(f"Сначала выберите тип сделки.\n\n{ASK_DEAL}", reply_markup=kb_deal())
        return

    # «Свой вариант…» для перечислимых полей
    other_key = data.get("__awaiting_other_key")
    if other_key:
        if len(user_text) < 2:
            await message.answer("Опишите чуть подробнее, хотя бы пару символов.")
            return
        # для country и flat — сохраняем и двигаемся дальше
        # для country и flat — сохраняем и двигаемся дальше
        await state.update_data(**{other_key: user_text}, __awaiting_other_key=None)
        step = int(data.get("__form_step") or 0) + 1
        await state.update_data(__form_step=step)
        if data.get("__country_mode"):
            await _ask_next_country_step(message, state, new=True)
        else:
            await _ask_next_flat_step(message, state, new=True)
        return

    # Этап 2: свободный комментарий?
    if data.get("__awaiting_free_comment"):
        # Минимальная длина свободного комментария — 50 символов (или пользователь нажимает «Пропустить»)
        if len(user_text) < 50:
            remain = 50 - len(user_text)
            await message.answer(
                "✍️ Свободный комментарий слишком короткий. "
                f"Добавьте ещё хотя бы {remain} симв. или нажмите «Пропустить».",
                reply_markup=kb_skip_comment()
            )
            return
        # Пользователь прислал достаточный текст — генерируем с этим комментарием
        await _generate_and_output(
            message,
            state,
            bot,
            comment=user_text,
            reuse_anchor=False
        )
        return

    # Этап 1: анкета
    if data.get("__flat_mode"):
        form_keys: List[str] = data.get("__form_keys") or []
        step: int = int(data.get("__form_step") or 0)
        if form_keys and step < len(form_keys):
            current_key = form_keys[step]
            if current_key in {
                "market", "completion_term", "sale_method", "rooms", "mortgage_ok",
                "total_area", "kitchen_area", "floor", "floors_total",
                "bathroom_type", "windows", "house_type", "lift", "parking",
                "renovation", "layout", "balcony", "ceiling_height_m"
            }:
                await message.answer("Пожалуйста, выберите вариант кнопкой ниже.", reply_markup=_kb_enum(current_key))
                return

    # Блокируем произвольный ввод для country: только кнопки
    if data.get("__country_mode"):
        form_keys: List[str] = data.get("__form_keys") or []
        step: int = int(data.get("__form_step") or 0)
        if form_keys and step < len(form_keys):
            current_key = form_keys[step]
            if current_key in COUNTRY_MULTI_KEYS:
                await message.answer("Это поле — множественный выбор. Пожалуйста, используйте кнопки ниже.", reply_markup=_kb_multi_enum(current_key, set(data.get(current_key) or [])))
                return
            else:
                await message.answer("Пожалуйста, выберите вариант кнопкой ниже.", reply_markup=_kb_enum(current_key))
                return

    # Блокируем произвольный ввод для commercial: только кнопки для перечислений
    if data.get("__commercial_mode"):
        form_keys: List[str] = data.get("__form_keys") or []
        step: int = int(data.get("__form_step") or 0)
        if form_keys and step < len(form_keys):
            current_key = form_keys[step]
            if current_key in {
                "comm_object_type", "comm_building_type", "comm_whole_object",
                "comm_finish", "comm_entrance", "comm_parking", "comm_layout"
            }:
                await message.answer("Пожалуйста, выберите вариант кнопкой ниже.", reply_markup=_kb_enum(current_key))
                return
            # для числовых полей разрешаем ввод

    form_keys: List[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    # Если почему-то нет последовательности — заново попросим старт
    if not form_keys:
        await message.answer("Давайте начнём сначала. " + ASK_TYPE, reply_markup=kb_type_merged())
        return

    current_key = form_keys[step]
    # Валидация и сохранение
    err = _validate_and_store(current_key, user_text, data)
    if err:
        await message.answer(f"⚠️ {err}\n\n{_form_prompt_for_key(current_key)}", reply_markup=_kb_back_only())
        return

    # Сохраняем изменения после валидации
    await state.update_data(**{k: data.get(k) for k in [
        "total_area","floors_total","floor","kitchen_area","rooms",
        "year_or_condition","utilities","location","features"
    ]})

    # Следующий шаг или переход к свободному комментарию
    step += 1
    await state.update_data(__form_step=step)

    if data.get("__flat_mode"):
        await _ask_next_flat_step(message, state, new=True)
        return
    if data.get("__country_mode"):
        await _ask_next_country_step(message, state, new=True)
        return
    if data.get("__commercial_mode"):
        await _ask_next_commercial_step(message, state, new=True)
        return

    if step < len(form_keys):
        next_key = form_keys[step]
        if next_key == "apt_condition":
            await message.answer(await _with_summary(state, ASK_FORM_APT_COND), reply_markup=kb_apt_condition())
            return
        # текстовый шаг из message-контекста — показать «Назад»
        await message.answer(await _with_summary(state, _form_prompt_for_key(next_key)), reply_markup=_kb_back_only())
        return

    await state.update_data(__awaiting_free_comment=True)
    await message.answer(await _with_summary(state, ASK_FREE_COMMENT), reply_markup=kb_skip_comment())

async def handle_comment_skip(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """Пропуск свободного комментария (после анкеты)."""
    data = await state.get_data()
    if not data.get("__awaiting_free_comment"):
        # Если нажали не вовремя — просто повторим вопрос
        await _cb_ack(cb)
        return
    # СНАЧАЛА ACK, затем длинная операция
    await _cb_ack(cb)
    await _edit_text_or_caption(cb.message, "Комментарий пропущен. Начинаю генерацию…")
    await _generate_and_output(cb.message, state, bot, comment=None, reuse_anchor=True)

# ==========================
# Обработчики блока «Состояние квартиры» (кнопки)
# ==========================
async def handle_apt_condition_select(cb: CallbackQuery, state: FSMContext):
    """
    Принимает выбор состояния квартиры (кнопки) в рамках анкеты.
    Сохраняет значение и переводит на следующий шаг анкеты.
    """
    data = await state.get_data()
    form_keys: List[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    # Защита: если текущий шаг не про apt_condition — игнорируем
    if step >= len(form_keys) or form_keys[step] != "apt_condition":
        await _cb_ack(cb)
        return

    code = cb.data.removeprefix("desc_cond_")
    label = APT_COND_LABELS.get(code)
    if not label:
        await _cb_ack(cb)
        return

    # Сохраняем «человеческое» значение
    await state.update_data(apt_condition=label)

    # Переходим к следующему шагу
    step += 1
    await state.update_data(__form_step=step)
    if step < len(form_keys):
        next_key = form_keys[step]
        # Если вдруг подряд снова apt_condition (не должно быть) — повторим клавиатуру
        if next_key == "apt_condition":
            await _edit_text_or_caption(cb.message, ASK_FORM_APT_COND, kb_apt_condition())
        else:
            await _edit_text_or_caption(cb.message, _form_prompt_for_key(next_key))
    else:
        # анкета завершена — переходим к свободному комментарию
        await state.update_data(__awaiting_free_comment=True)
        await _edit_text_or_caption(cb.message, ASK_FREE_COMMENT, kb_skip_comment())
    # ack/уведомление уже сделано ранее; повторный answer не нужен

async def handle_apt_condition_back(cb: CallbackQuery, state: FSMContext):
    """
    Кнопка «Назад» внутри блока состояния:
    Возвращаемся на предыдущий текстовый шаг анкеты.
    """
    data = await state.get_data()
    form_keys: List[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    # Если мы не на apt_condition — игнор
    if step >= len(form_keys) or form_keys[step] != "apt_condition":
        await _cb_ack(cb)
        return

    # Шаг назад
    prev_step = max(0, step - 1)
    await state.update_data(__form_step=prev_step)
    prev_key = form_keys[prev_step]

    # Показываем предыдущий вопрос (текстовый ввод) + «Назад»
    await _edit_text_or_caption(cb.message, _form_prompt_for_key(prev_key), _kb_back_only())
    await _cb_ack(cb)

# ==========================
# Квартира: обработчики перечислений/пропусков
# ==========================
async def handle_enum_select(cb: CallbackQuery, state: FSMContext):
    await _cb_ack(cb)
    data = await state.get_data()
    if not (data.get("__flat_mode") or data.get("__country_mode") or data.get("__commercial_mode")):
        return

    payload = cb.data.removeprefix("desc_enum_")  # key_code
    try:
        # ключи вроде country_object_type / comm_building_type и т.п.
        key, code = payload.rsplit("_", 1)
    except ValueError:
        return

    # ищем опцию в FLAT / COUNTRY / COMM
    label = next((lbl for c, lbl in (FLAT_ENUMS.get(key, []) or [] ) if c == code), None)
    if label is None:
        label = next((lbl for c, lbl in (COUNTRY_ENUMS.get(key, []) or [] ) if c == code), None)
    if label is None:
        label = next((lbl for c, lbl in (COMM_ENUMS.get(key, []) or [] ) if c == code), code)
    # поддержка «Пропустить» для опциональных полей
    if key == "ceiling_height_m" and code == "skip":
        await state.update_data(**{key: None})
    else:
        await state.update_data(**{key: label})

    # Особая логика после выбора рынка (квартира)
    if key == "market" and data.get("__form_step") == 0:
        after = _flat_after_market_keys()
        if code == "new":
            new_keys = ["market", "completion_term", "sale_method"] + after
        else:
            new_keys = ["market"] + after
        await state.update_data(__form_keys=new_keys)

    # Ветка «Загородная»: ветвление после выбора типа объекта
    if data.get("__country_mode") and key == "country_object_type" and data.get("__form_step") == 0:
        if code == "plot":
            new_keys = [
                "country_object_type",
                "country_land_category_plot",
                "country_plot_area_sotki",
                "country_distance_km",
                "country_communications_plot",
            ]
        else:
            new_keys = [
                "country_object_type",
                "country_house_area_m2",
                "country_plot_area_sotki",
                "country_distance_km",
                "country_floors",
                "country_rooms",
                "country_land_category_house",
                "country_renovation",
                "country_toilet",
                "country_utilities",
                "country_leisure",
                "country_wall_material",
                "country_parking",
                "country_transport",
            ]
        await state.update_data(__form_keys=new_keys)

    step = int(data.get("__form_step") or 0) + 1
    await state.update_data(__form_step=step)
    if data.get("__flat_mode"):
        await _ask_next_flat_step(cb.message, state)  # callback → редактируем
    elif data.get("__country_mode"):
        await _ask_next_country_step(cb.message, state)
    elif data.get("__commercial_mode"):
        await _ask_next_commercial_step(cb.message, state)

# --- НОВОЕ: обработчик первого шага внутри «Загородная» (Дом / Земельный участок)
async def handle_country_entry(cb: CallbackQuery, state: FSMContext):
    """
    Пользователь выбрал «Дом» или «Земельный участок» с объединённой кнопки «Загородная недвижимость».
    Здесь маппим выбор на существующую логику country_object_type: house/plot.
    """
    await _cb_ack(cb)
    data = await state.get_data()
    if not data.get("__country_mode"):
        return

    payload = cb.data
    if payload not in {"desc_country_entry_house", "desc_country_entry_plot"}:
        return

    # Возьмём «человеческие» метки из справочника
    def _label_for(enum_key: str, code: str) -> str:
        opts = COUNTRY_ENUMS.get(enum_key, [])
        for c, lbl in opts:
            if c == code:
                return lbl
        return code

    if payload.endswith("_house"):
        # как будто пользователь выбрал country_object_type=house
        label = _label_for("country_object_type", "house")
        await state.update_data(country_object_type=label)
        new_keys = [
            "country_object_type",
            "country_house_area_m2",
            "country_plot_area_sotki",
            "country_distance_km",
            "country_floors",
            "country_rooms",
            "country_land_category_house",
            "country_renovation",
            "country_toilet",
            "country_utilities",
            "country_leisure",
            "country_wall_material",
            "country_parking",
            "country_transport",
        ]
    else:
        # plot
        label = _label_for("country_object_type", "plot")
        await state.update_data(country_object_type=label)
        new_keys = [
            "country_object_type",
            "country_land_category_plot",
            "country_plot_area_sotki",
            "country_distance_km",
            "country_communications_plot",
        ]

    # Технически считаем, что первый ключ уже выбран → начинаем со следующего шага
    await state.update_data(__form_keys=new_keys, __form_step=1)
    # Прежде чем идти по анкете — спросим расположение (За городом / В черте города)
    await _edit_text_or_caption(cb.message, await _with_summary(state, COUNTRY_ASK_AREA), kb_country_area())

# --- НОВОЕ: обработчик выбора вида для «Коммерческой»
async def handle_commercial_entry(cb: CallbackQuery, state: FSMContext):
    await _cb_ack(cb)
    data = await state.get_data()
    if not data.get("__commercial_mode"):
        return
    if not cb.data.startswith("desc_comm_entry_"):
        return
    code = cb.data.removeprefix("desc_comm_entry_")
    # сохранить «человеческую» метку
    label = next((lbl for c, lbl in COMM_ENUMS["comm_object_type"] if c == code), code)
    await state.update_data(comm_object_type=label)
    # Настроить последовательность общих параметров
    new_keys = [
        "comm_object_type",
        "total_area",
        "land_area",
        "comm_building_type",
        "comm_whole_object",
        "comm_finish",
        "comm_entrance",
        "comm_parking",
        "comm_layout",
    ]
    # Технически первый ключ уже выбран → шаг со следующего
    await state.update_data(__form_keys=new_keys, __form_step=1)
    await _ask_next_commercial_step(cb.message, state)

async def handle_country_area(cb: CallbackQuery, state: FSMContext):
    """
    Сохраняем area для «Загородной недвижимости»:
    - desc_country_area_out  -> area='out'
    - desc_country_area_city -> area='city'
    После выбора продолжаем анкету по «country».
    """
    await _cb_ack(cb)
    data = await state.get_data()
    if not data.get("__country_mode"):
        return
    payload = cb.data
    if payload == "desc_country_area_out":
        await state.update_data(area="out")
    elif payload == "desc_country_area_city":
        await state.update_data(area="city")
    else:
        return
    # Переходим к следующему шагу анкеты
    await _ask_next_country_step(cb.message, state)

async def handle_enum_other(cb: CallbackQuery, state: FSMContext):
    await _cb_ack(cb)
    data = await state.get_data()
    if not (data.get("__flat_mode") or data.get("__country_mode")):
        return
    key = cb.data.removeprefix("desc_enum_other_")
    await state.update_data(__awaiting_other_key=key)
    await _edit_text_or_caption(cb.message, f"✍️ Напишите свой вариант для поля. Отправьте одним сообщением.")

async def handle_flat_skip_field(cb: CallbackQuery, state: FSMContext):
    await _cb_ack(cb)
    data = await state.get_data()
    if not data.get("__flat_mode"):
        return
    key = cb.data.removeprefix("desc_flat_skip_")
    await state.update_data(**{key: None})
    step = int(data.get("__form_step") or 0) + 1
    await state.update_data(__form_step=step)
    await _ask_next_flat_step(cb.message, state)

async def handle_country_multi_toggle(cb: CallbackQuery, state: FSMContext):
    """Тоггл для мультивыбора в «Загородная»."""
    await _cb_ack(cb)
    data = await state.get_data()
    if not data.get("__country_mode"):
        return
    payload = cb.data.removeprefix("desc_multi_")  # key_code ИЛИ префикс "done_..."
    # Защита от случая, когда маршрутизация поменялась и сюда попало "done_*"
    if payload.startswith("done_"):
        return
    try:
        # важный фикс: ключи вида country_utilities содержат '_'
        # поэтому режем с конца, а не с начала
        key, code = payload.rsplit("_", 1)
    except ValueError:
        return
    if key not in COUNTRY_MULTI_ENUMS:
        return
    # Берём текущее состояние как КОДЫ (если вдруг были сохранены метки)
    current: List[str] = list(_normalize_multi_selected(key, data.get(key) or []))
    if code in current:
        current = [c for c in current if c != code]
    else:
        current.append(code)
    await state.update_data(**{key: current})
    # перерисовываем ту же клавиатуру
    await _edit_text_or_caption(cb.message, _country_prompt_for_key(key), _kb_multi_enum(key, set(current)))

async def handle_country_multi_done(cb: CallbackQuery, state: FSMContext):
    """Подтверждение мультивыбора и переход к следующему шагу."""
    await _cb_ack(cb)
    data = await state.get_data()
    if not data.get("__country_mode"):
        return
    # key = cb.data.removeprefix("desc_multi_done_")
    # просто идём дальше по шагам
    step = int(data.get("__form_step") or 0) + 1
    await state.update_data(__form_step=step)
    await _ask_next_country_step(cb.message, state)

# ==========================
# История запросов: просмотр/удаление/повтор
# ==========================
async def handle_history_entry(cb: CallbackQuery):
    await _cb_ack(cb)
    user_id = cb.message.chat.id
    items = app_db.description_list(user_id=user_id, limit=10)
    await _edit_text_or_caption(cb.message, "🗂 История запросов (последние 10):", _kb_history_list(items))

async def handle_history_item(cb: CallbackQuery):
    await _cb_ack(cb)
    user_id = cb.message.chat.id
    try:
        entry_id = int(cb.data.removeprefix("desc_hist_item_"))
    except Exception:
        return
    entry = app_db.description_get(user_id=user_id, entry_id=entry_id)
    if not entry:
        await _edit_text_or_caption(cb.message, "Запись не найдена или удалена.",
                                    _kb_history_list(app_db.description_list(user_id, 10)))
        return
    # Покажем меню (кнопки) в текущем сообщении и отправим ПОЛНЫЙ текст отдельным(и) сообщением(ями)
    header = f"📝 Запись #{entry['id']} от {entry['created_at']}\n\nТекст записи отправлен ниже 👇"
    await _edit_text_or_caption(cb.message, header, _kb_history_item(entry_id))

    full_text = (entry.get("result_text") or "").strip()
    if not full_text:
        await cb.message.answer("Текст записи пуст.")
        return
    parts = _split_for_telegram(full_text)
    for i, part in enumerate(parts):
        is_last = (i == len(parts) - 1)
        if is_last:
            # На последний чанк вешаем клавиатуру записи
            await cb.message.answer(part, reply_markup=_kb_history_item(entry_id))
        else:
            await cb.message.answer(part)

async def handle_history_delete(cb: CallbackQuery):
    await _cb_ack(cb)
    user_id = cb.message.chat.id
    try:
        entry_id = int(cb.data.removeprefix("desc_hist_del_"))
    except Exception:
        return
    app_db.description_delete(user_id=user_id, entry_id=entry_id)
    items = app_db.description_list(user_id=user_id, limit=10)
    await _edit_text_or_caption(cb.message, "🗂 История запросов (обновлено):", _kb_history_list(items))

async def handle_history_repeat(cb: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Повторить запрос == отправить текст результата на доработку как свободный комментарий.
    """
    await _cb_ack(cb)
    user_id = cb.message.chat.id
    try:
        entry_id = int(cb.data.removeprefix("desc_hist_repeat_"))
    except Exception:
        return
    entry = app_db.description_get(user_id=user_id, entry_id=entry_id)
    if not entry:
        await _edit_text_or_caption(cb.message, "Запись не найдена или удалена.",
                                    _kb_history_list(app_db.description_list(user_id, 10)))
        return
    # Отправляем «повторную генерацию» с комментарием = прежний результат (для доработки)
    # Стейт очищаем, чтобы не мешали старые шаги
    await state.clear()
    await _edit_text_or_caption(cb.message, "🔁 Отправляю текст на доработку…")
    await _generate_and_output(cb.message, state, bot, comment=entry["result_text"], reuse_anchor=True)

# ==========================
# Назад/Выход
# ==========================
async def handle_back(cb: CallbackQuery, state: FSMContext):
    """
    Универсальный «Назад».
    - В анкете: step-- и показать предыдущий вопрос.
    - Из свободного комментария: вернуться на последний шаг анкеты.
    - Из 'свой вариант…': вернуться к клавиатуре соответствующего перечисления.
    - На ранних экранах: type -> deal; country/commercial entry -> type; area -> complex/type.
    """
    await _cb_ack(cb)
    data = await state.get_data()
    # 1) Если ждём «свой вариант»
    other = data.get("__awaiting_other_key")
    if other:
        await state.update_data(__awaiting_other_key=None)
        await _edit_text_or_caption(cb.message, await _with_summary(state, "Выберите вариант или укажите свой."), _kb_enum(other))
        return

    # 2) Если свободный комментарий
    if data.get("__awaiting_free_comment"):
        await state.update_data(__awaiting_free_comment=False)
        keys: list[str] = data.get("__form_keys") or []
        step = max(0, (len(keys) - 1))
        await state.update_data(__form_step=step)
        # отрисуем соответствующий шаг для режима
        if data.get("__flat_mode"):
            await _ask_next_flat_step(cb.message, state)
        elif data.get("__country_mode"):
            await _ask_next_country_step(cb.message, state)
        elif data.get("__commercial_mode"):
            await _ask_next_commercial_step(cb.message, state)
        return

    # 3) Если в анкете
    if data.get("__flat_mode") or data.get("__country_mode") or data.get("__commercial_mode"):
        step: int = int(data.get("__form_step") or 0)
        prev = step - 1
        # первый шаг в режимах -> вернуться на «экраны входа»
        if data.get("__flat_mode") and step <= 0:
            await _edit_text_or_caption(cb.message, await _with_summary(state, ASK_TYPE), kb_type_merged())
            await state.update_data(__flat_mode=False, __form_keys=[], __form_step=0)
            await state.set_state(DescriptionStates.waiting_for_type)
            return
        if data.get("__country_mode") and step <= 1:
            await _edit_text_or_caption(cb.message, await _with_summary(state, COUNTRY_GROUP_ASK), kb_country_entry())
            await state.update_data(__form_step=0)
            return
        if data.get("__commercial_mode") and step <= 1:
            await _edit_text_or_caption(cb.message, await _with_summary(state, COMM_ASK_GROUP), kb_commercial_entry())
            await state.update_data(__form_step=0)
            return
        # обычный шаг анкеты --
        prev = max(0, prev)
        await state.update_data(__form_step=prev)
        if data.get("__flat_mode"):
            await _ask_next_flat_step(cb.message, state)
        elif data.get("__country_mode"):
            await _ask_next_country_step(cb.message, state)
        else:
            await _ask_next_commercial_step(cb.message, state)
        return

    # 4) Ранние экраны: type -> deal, area->complex/type
    current = await state.get_state()
    if current == DescriptionStates.waiting_for_type:
        await _edit_text_or_caption(cb.message, await _with_summary(state, ASK_DEAL), kb_deal())
        return
    if current == DescriptionStates.waiting_for_area:
        # если был complex — вернём на complex, иначе — к типу
        if data.get("in_complex") is not None:
            await _edit_text_or_caption(cb.message, await _with_summary(state, ASK_COMPLEX), kb_complex())
            await state.set_state(DescriptionStates.waiting_for_complex)
        else:
            await _edit_text_or_caption(cb.message, await _with_summary(state, ASK_TYPE), kb_type_merged())
            await state.set_state(DescriptionStates.waiting_for_type)
        return
    if current == DescriptionStates.waiting_for_complex:
        await _edit_text_or_caption(cb.message, await _with_summary(state, ASK_CLASS), kb_class())
        await state.set_state(DescriptionStates.waiting_for_complex)  # остаёмся в разделе
        return

    # Фолбэк: показать выбор сделки
    await _edit_text_or_caption(cb.message, await _with_summary(state, ASK_DEAL), kb_deal())

# ==========================
# Router
# ==========================
from .clicklog_mw import CallbackClickLogger, MessageLogger
def router(rt: Router) -> None:
    # messages
    rt.message.outer_middleware(MessageLogger())
    rt.callback_query.outer_middleware(CallbackClickLogger())

    # старт
    rt.callback_query.register(start_description_flow, F.data == "nav.descr_home")
    rt.callback_query.register(start_description_flow, F.data == "desc_start")

    # первый шаг — тип сделки
    rt.callback_query.register(handle_deal, F.data.startswith("desc_deal_"))

    # пошаговые выборы
    rt.callback_query.register(handle_type,    F.data.startswith("desc_type_"))
    rt.callback_query.register(handle_class,   F.data.startswith("desc_class_"))
    rt.callback_query.register(handle_complex, F.data.startswith("desc_complex_"))
    rt.callback_query.register(handle_area,    F.data.startswith("desc_area_"))

    # состояние квартиры (кнопки) — в рамках анкеты
    rt.callback_query.register(handle_apt_condition_select, F.data.startswith("desc_cond_"), DescriptionStates.waiting_for_comment)
    rt.callback_query.register(handle_apt_condition_back,   F.data == "desc_cond_back",      DescriptionStates.waiting_for_comment)

    # Квартира: перечисления, свой вариант, пропуск опционального поля
    rt.callback_query.register(handle_enum_other, F.data.startswith("desc_enum_other_"), DescriptionStates.waiting_for_comment)
    rt.callback_query.register(handle_enum_select, F.data.startswith("desc_enum_"),       DescriptionStates.waiting_for_comment)
    rt.callback_query.register(handle_flat_skip_field, F.data.startswith("desc_flat_skip_"), DescriptionStates.waiting_for_comment)
    # Загородная: мультивыбор
    # ВАЖНО: регистрируем "Готово" ПЕРЕД общим тогглом, иначе done уедет в toggle-хэндлер.
    rt.callback_query.register(handle_country_multi_done,   F.data.startswith("desc_multi_done_"), DescriptionStates.waiting_for_comment)
    rt.callback_query.register(handle_country_multi_toggle, F.data.startswith("desc_multi_"),      DescriptionStates.waiting_for_comment)

    # Загородная: первый упрощённый шаг (Дом/Земельный участок)
    rt.callback_query.register(handle_country_entry, F.data.in_(["desc_country_entry_house", "desc_country_entry_plot"]), DescriptionStates.waiting_for_comment)
    # Коммерческая: выбор вида объекта
    rt.callback_query.register(handle_commercial_entry, F.data.startswith("desc_comm_entry_"), DescriptionStates.waiting_for_comment)
    # Загородная: выбор расположения
    rt.callback_query.register(handle_country_area, F.data.in_(["desc_country_area_out", "desc_country_area_city"]), DescriptionStates.waiting_for_comment)

    # анкета + свободный комментарий / пропуск
    rt.message.register(handle_comment_message, DescriptionStates.waiting_for_comment, F.text)
    rt.callback_query.register(handle_comment_skip, F.data == "desc_comment_skip", DescriptionStates.waiting_for_comment)

    # История: список / запись / удалить / повторить
    rt.callback_query.register(handle_history_entry, F.data == "desc_history")
    rt.callback_query.register(handle_history_item, F.data.startswith("desc_hist_item_"))
    rt.callback_query.register(handle_history_delete, F.data.startswith("desc_hist_del_"))
    rt.callback_query.register(handle_history_repeat, F.data.startswith("desc_hist_repeat_"))

    # Назад
    rt.callback_query.register(handle_back, F.data == "desc_back")


# ==========================
# Публичная регистрация HTTP-эндпоинтов (aiohttp)
# ==========================
def register_http_endpoints(app: web.Application, bot: Bot):
    """
    Вызывается из run.py после создания app.
    """
    app["bot"] = bot
    # Совместимость: оба пути, чтобы не зависеть от того, какой использует executor
    app.router.add_post("/description/callback", _cb_description_result)
    app.router.add_post("/api/v1/description/result", _cb_description_result)