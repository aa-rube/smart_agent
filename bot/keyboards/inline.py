from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from utils import youmoney


frst_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Начать", callback_data="start"),
        ],
    ]
)


start_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🏗  Дизайн планировок", callback_data="design"),
        ],
        [
            InlineKeyboardButton(text="🛋 Редизайн интерьера", callback_data="redesign")
        ],
        [
            InlineKeyboardButton(text="🆕 Дизайн с нуля", callback_data="0design")
        ]
    ]
)


def sub(user_id):
    payment_url = youmoney.create_pay(user_id)
    print(payment_url)
    sub = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Оформить подписку", url=payment_url),
            ]
        ]
    )
    return sub

def help():
    builder = InlineKeyboardBuilder()
    builder.button(text="🛟 Поддержка", url="https://t.me/admrecontent")
    return builder.as_markup()


# def get_plan_type_kb():
#     builder = InlineKeyboardBuilder()
#     builder.button(text="🔲 2D визуализация", callback_data="plan_2d")
#     builder.button(text="🏠 3D визуализация", callback_data="plan_3d")
#     return builder.as_markup()


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
    builder.adjust(2) # Располагаем по 2 кнопки в ряд
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
