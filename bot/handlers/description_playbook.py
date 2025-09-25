# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\description_playbook.py
#Всегда пиши код без «поддержки старых версий». Если они есть в коде - удаляй.

# Секрет офигенного бота: тебе не нужен якорь.
# Пользуйся такой схемой:
# -если callback -> обновляем сообщение, msg_id берем из update
# -если обычный text_message, command -> отправляй новое сообщение.
# Используй fallback если изменить не удалось.
# Все, никаких anchors которые нужно настраивать, никаких залипаний, кучи сообщение и мисс-кликов.

from typing import Optional, List, Dict, Set
from aiogram.types import CallbackQuery as _CbType  # type hint clarity
import os
import re

import aiohttp
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, InputMediaPhoto
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction

from bot.config import EXECUTOR_BASE_URL, get_file_path
from bot.states.states import DescriptionStates
from bot.utils.chat_actions import run_long_operation_with_action
import executor.ai_config as ai_cfg  # варианты кнопок из конфига

# ====== Доступ / подписка (как в plans/design) ======
import bot.utils.database as db
from bot.utils.database import is_trial_active, trial_remaining_hours

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
        return f'✅ Подписка активна до *{sub_until}*'
    if trial_hours > 0:
        return f'🆓 Бесплатный доступ активен ещё *~{trial_hours} ч.*'
    return '😢 Бесплатный период завершён. Оформи подписку, чтобы продолжить.'

def _has_access(user_id: int) -> bool:
    return is_trial_active(user_id) or _is_sub_active(user_id)

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
    """Стартовый текст с информацией о доступе (как в plans)."""
    return f"{DESC_INTRO}\n\n{_format_access_text(user_id)}\n\n{ASK_TYPE}"

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
        ("dkp", "ДКП"), ("cession", "Переуступка"), ("fz214", "ФЗ-214"),
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
# Клавиатуры
# ==========================
def kb_type()    -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_TYPES,   "desc_type_",   1)
def kb_class()   -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_CLASSES,"desc_class_",  1)
def kb_complex() -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_COMPLEX,"desc_complex_",1)
def kb_area()    -> InlineKeyboardMarkup: return _kb_from_map(ai_cfg.DESCRIPTION_AREA,   "desc_area_",   1)

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
    # Кнопка «Назад» (если нужна единая навигация по боту)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_enum(key: str) -> InlineKeyboardMarkup:
    """Клавиатура для перечислимого поля + «Свой вариант…»."""
    # поддержка и FLAT, и COUNTRY
    opts = FLAT_ENUMS.get(key, []) or COUNTRY_ENUMS.get(key, [])
    rows: list[list[InlineKeyboardButton]] = []
    for code, label in opts:
        rows.append([InlineKeyboardButton(text=label, callback_data=f"desc_enum_{key}_{code}")])
    rows.append([InlineKeyboardButton(text="✍️ Свой вариант…", callback_data=f"desc_enum_other_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_skip_field(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"desc_flat_skip_{key}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")]
    ])

def _kb_multi_enum(key: str, selected: Optional[Set[str]] = None) -> InlineKeyboardMarkup:
    """
    Мультивыбор с чекбоксами + кнопка «Готово».
    """
    sel = selected or set()
    opts = COUNTRY_MULTI_ENUMS.get(key, [])
    rows: list[list[InlineKeyboardButton]] = []
    for code, label in opts:
        mark = "✅ " if code in sel else ""
        rows.append([InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"desc_multi_{key}_{code}")])
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data=f"desc_multi_done_{key}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def kb_retry() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Ещё раз", callback_data="description")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")]
    ])

def kb_apt_condition() -> InlineKeyboardMarkup:
    """
    Блок выбора состояния квартиры (кнопки) + «Назад».
    """
    rows = [
        [InlineKeyboardButton(text="1. Дизайнерский ремонт",      callback_data="desc_cond_designer")],
        [InlineKeyboardButton(text="2. «Евро-ремонт»",            callback_data="desc_cond_euro")],
        [InlineKeyboardButton(text="3. Косметический",            callback_data="desc_cond_cosmetic")],
        [InlineKeyboardButton(text="4. Требует ремонта",          callback_data="desc_cond_need")],
        [InlineKeyboardButton(text="⬅️ Назад",                    callback_data="desc_cond_back")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

APT_COND_LABELS = {
    "designer": "Дизайнерский ремонт",
    "euro":     "Евро-ремонт",
    "cosmetic": "Косметический",
    "need":     "Требует ремонта",
}

def kb_skip_comment() -> InlineKeyboardMarkup:
    """Кнопка «Пропустить» для необязательного финального шага."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="desc_comment_skip")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")],
    ])


# Кнопка к офферу подписки
SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)

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
        await _edit_or_replace_with_photo_file(bot, cb.message, img_path, caption, kb_type())
    else:
        await _edit_text_or_caption(cb.message, caption, kb_type())

    await state.set_state(DescriptionStates.waiting_for_type)

async def handle_type(cb: CallbackQuery, state: FSMContext):
    """
    type = flat / house / land ...
    - flat  → НОВЫЙ сценарий «Квартира»: карта вопросов из ТЗ
    - house → пропускаем «новостройка/ЖК», сразу спрашиваем расположение
    - иное → спрашиваем «новостройка/ЖК» (как раньше)
    """
    await _cb_ack(cb)
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
        await _edit_text_or_caption(cb.message, FLAT_ASK_MARKET, _kb_enum("market"))
        await state.set_state(DescriptionStates.waiting_for_comment)
        return
    elif val in {"country", "zagorod"}:
        # Новый сценарий «Загородная»
        await state.update_data(
            __country_mode=True,
            __flat_mode=False,
            __form_keys=["country_object_type"],
            __form_step=0,
            __awaiting_other_key=None,
            __awaiting_free_comment=False
        )
        await _edit_text_or_caption(cb.message, COUNTRY_ASK_OBJECT_TYPE, _kb_enum("country_object_type"))
        await state.set_state(DescriptionStates.waiting_for_comment)
        return
    elif val == "house" or val == "land":
        # СКИП «новостройка/ЖК» для дома, идём сразу к расположению
        await _edit_text_or_caption(cb.message, ASK_AREA, kb_area())
        await state.set_state(DescriptionStates.waiting_for_area)
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
    await _edit_text_or_caption(cb.message, ASK_AREA, kb_area())
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
        await _edit_text_or_caption(cb.message, ASK_FORM_APT_COND, kb_apt_condition())
    else:
        await _edit_text_or_caption(cb.message, _form_prompt_for_key(first_key))
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

async def _ask_next_flat_step(msg: Message, state: FSMContext):
    data = await state.get_data()
    keys: list[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    if step >= len(keys):
        # Все поля собраны → переходим к свободному комментарию
        await state.update_data(__awaiting_free_comment=True)
        await _edit_text_or_caption(msg, ASK_FREE_COMMENT, kb_skip_comment())
        return

    key = keys[step]

    # Все поля для квартиры задаются кнопками (опционально — «Свой вариант…»)
    if key in {
        "market", "completion_term", "sale_method", "rooms", "mortgage_ok",
        "total_area", "kitchen_area", "floor", "floors_total",
        "bathroom_type", "windows", "house_type", "lift", "parking",
        "renovation", "layout", "balcony", "ceiling_height_m"
    }:
        await _edit_text_or_caption(msg, _flat_prompt_for_key(key), _kb_enum(key))
        return
    # На всякий случай (не должно сработать в квартирном сценарии)
    await _edit_text_or_caption(msg, _form_prompt_for_key(key))

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

async def _ask_next_country_step(msg: Message, state: FSMContext):
    data = await state.get_data()
    keys: list[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    if step >= len(keys):
        await state.update_data(__awaiting_free_comment=True)
        await _edit_text_or_caption(msg, ASK_FREE_COMMENT, kb_skip_comment())
        return

    key = keys[step]
    # мультивыбор
    if key in COUNTRY_MULTI_KEYS:
        selected = set(data.get(key) or [])
        await _edit_text_or_caption(msg, _country_prompt_for_key(key), _kb_multi_enum(key, selected))
        return
    # обычные перечисления
    await _edit_text_or_caption(msg, _country_prompt_for_key(key), _kb_enum(key))

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
        "sale_method":       data.get("sale_method"),      # ДКП / Переуступка / ФЗ-214
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

    async def _do_req():
        return await _request_description_text(fields)

    try:
        text = await run_long_operation_with_action(
            bot=bot, chat_id=message.chat.id, action=ChatAction.TYPING, coro=_do_req()
        )
        parts = _split_for_telegram(text)

        # редактируем anchor результатом
        try:
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=anchor_id,
                text=parts[0],
                reply_markup=kb_retry()
            )
        except TelegramBadRequest:
            await message.answer(parts[0], reply_markup=kb_retry())

        for p in parts[1:]:
            await message.answer(p)

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

    # «Свой вариант…» для перечислимых полей
    other_key = data.get("__awaiting_other_key")
    if other_key:
        if len(user_text) < 2:
            await message.answer("Добавьте чуть подробнее, хотя бы пару символов.")
            return
        # для country и flat — сохраняем и двигаемся дальше
        # для country и flat — сохраняем и двигаемся дальше
        await state.update_data(**{other_key: user_text}, __awaiting_other_key=None)
        step = int(data.get("__form_step") or 0) + 1
        await state.update_data(__form_step=step)
        if data.get("__country_mode"):
            await _ask_next_country_step(message, state)
        else:
            await _ask_next_flat_step(message, state)
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

    form_keys: List[str] = data.get("__form_keys") or []
    step: int = int(data.get("__form_step") or 0)

    # Если почему-то нет последовательности — заново попросим старт
    if not form_keys:
        await message.answer("Давайте начнём сначала. " + ASK_TYPE,
                             reply_markup=_kb_from_map(ai_cfg.DESCRIPTION_TYPES, "desc_type_", 1))
        return

    current_key = form_keys[step]
    # Валидация и сохранение
    err = _validate_and_store(current_key, user_text, data)
    if err:
        await message.answer(f"⚠️ {err}\n\n{_form_prompt_for_key(current_key)}")
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
        await _ask_next_flat_step(message, state)
        return
    if data.get("__country_mode"):
        await _ask_next_country_step(message, state)
        return

    if step < len(form_keys):
        next_key = form_keys[step]
        if next_key == "apt_condition":
            await message.answer(ASK_FORM_APT_COND, reply_markup=kb_apt_condition())
            return
        await message.answer(_form_prompt_for_key(next_key))
        return

    await state.update_data(__awaiting_free_comment=True)
    await message.answer(ASK_FREE_COMMENT, reply_markup=kb_skip_comment())

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

    # Показываем предыдущий вопрос (текстовый ввод)
    await _edit_text_or_caption(cb.message, _form_prompt_for_key(prev_key))
    await _cb_ack(cb)

# ==========================
# Квартира: обработчики перечислений/пропусков
# ==========================
async def handle_enum_select(cb: CallbackQuery, state: FSMContext):
    await _cb_ack(cb)
    data = await state.get_data()
    if not (data.get("__flat_mode") or data.get("__country_mode")):
        return

    payload = cb.data.removeprefix("desc_enum_")  # key_code
    try:
        key, code = payload.split("_", 1)
    except ValueError:
        return

    # ищем опцию и в FLAT, и в COUNTRY
    label = next((lbl for c, lbl in (FLAT_ENUMS.get(key, []) or [] ) if c == code), None)
    if label is None:
        label = next((lbl for c, lbl in (COUNTRY_ENUMS.get(key, []) or [] ) if c == code), code)
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
        await _ask_next_flat_step(cb.message, state)
    elif data.get("__country_mode"):
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
    payload = cb.data.removeprefix("desc_multi_")  # key_code
    try:
        key, code = payload.split("_", 1)
    except ValueError:
        return
    if key not in COUNTRY_MULTI_ENUMS:
        return
    current: List[str] = list(data.get(key) or [])
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
# Router
# ==========================
def router(rt: Router):
    # старт
    rt.callback_query.register(start_description_flow, F.data == "nav.descr_home")
    rt.callback_query.register(start_description_flow, F.data == "desc_start")

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
    rt.callback_query.register(handle_country_multi_toggle, F.data.startswith("desc_multi_"), DescriptionStates.waiting_for_comment)
    rt.callback_query.register(handle_country_multi_done,   F.data.startswith("desc_multi_done_"), DescriptionStates.waiting_for_comment)

    # анкета + свободный комментарий / пропуск
    rt.message.register(handle_comment_message, DescriptionStates.waiting_for_comment, F.text)
    rt.callback_query.register(handle_comment_skip, F.data == "desc_comment_skip", DescriptionStates.waiting_for_comment)