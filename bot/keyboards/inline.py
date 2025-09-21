#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\keyboards\inline.py

from typing import List, Dict, Union, Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_style_kb():
    builder = InlineKeyboardBuilder()
    styles = [
        "Современный", "Скандинавский", "Классика", "Минимализм", "Хай-тек",
        "Лофт", "Эко-стиль", "Средиземноморский", "Барокко",
        "Неоклассика"
    ]
    for style in styles:
        builder.button(text=f"💎 {style}", callback_data=f"style_{style}")
    builder.button(text="🔥 Случайный выбор ИИ", callback_data="style_🔥 Случайный выбор ИИ")
    builder.adjust(1)
    return builder.as_markup()


def get_room_type_kb():
    builder = InlineKeyboardBuilder()
    rooms = ["🍳 Кухня", "🛏 Спальня", "🛋 Гостиная", "🚿 Ванная", "🚪 Прихожая"]
    for room in rooms:
        # Используем текст с эмодзи как данные для колбэка
        builder.button(text=room, callback_data=f"room_{room}")
    builder.adjust(2)  # Располагаем по 2 кнопки в ряд
    return builder.as_markup()


def get_furniture_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛋 С мебелью", callback_data="furniture_yes")
    builder.button(text="▫️ Без мебели", callback_data="furniture_no")
    return builder.as_markup()


def get_visualization_style_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="🖊️ Скетч-стиль", callback_data="viz_sketch")
    builder.button(text="📸 Реалистичный стиль", callback_data="viz_realistic")
    return builder.as_markup()


# Динамическая генерация клавиатуры для редактирования постов
def generate_edit_posts_kb(posts):
    buttons = []
    for post in posts:
        btn = InlineKeyboardButton(
            text=f"Пост от {post['date'][:10]}",
            callback_data=f"edit_post_{post['message_id']}"
        )
        buttons.append([btn])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_missing_subscribe_keyboard(
        channels: List[Dict[str, Union[int, str]]],
        sub_map: Dict[int, bool],
        *,
        retry_callback_data: Optional[str] = None,
        columns: int = 1,
) -> InlineKeyboardMarkup:
    """
    Строит клавиатуру ТОЛЬКО по отсутствующим подпискам.
    Кнопка = URL из конфига, текст = label из конфига.
    """
    columns = max(1, min(columns, 4))
    rows: list[list[InlineKeyboardButton]] = []
    line: list[InlineKeyboardButton] = []

    for cfg in channels:
        chat_id: int = cfg["chat_id"]
        if sub_map.get(chat_id, True):
            continue  # уже подписан — кнопку не показываем

        url: str = cfg["url"]  # если нет — упадёт (ошибка данных), это ок
        label: str = str(cfg.get("label") or "Канал")

        btn = InlineKeyboardButton(text=f"Подписаться → {label}", url=url)

        if columns == 1:
            rows.append([btn])
        else:
            line.append(btn)
            if len(line) >= columns:
                rows.append(line)
                line = []

    if columns > 1 and line:
        rows.append(line)

    if retry_callback_data:
        rows.append([InlineKeyboardButton(text="✅ Проверить", callback_data=retry_callback_data)])
        rows.append([InlineKeyboardButton(text="❗️ Не подписываться", callback_data="skip_subscribe")])

    return InlineKeyboardMarkup(inline_keyboard=rows)