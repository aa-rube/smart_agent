from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils import youmoney


frst_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать (работает по подписке)", callback_data="start"),],
        [InlineKeyboardButton(text='Подписаться на контент', callback_data='ShowRates')],
        [InlineKeyboardButton(text='Наше сообщество', url='https://t.me/+DJfn6NyHmRAzMTdi')],
        [InlineKeyboardButton(text='Тех. поддержка', url='https://t.me/dashaadminrealtor')],
        [InlineKeyboardButton(text='Мой профиль', callback_data='my_profile')],
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


#welcomebot
# Клавиатура выбора тарифа
select_rates = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text='1 месяц', callback_data='Rate_1'),
        InlineKeyboardButton(text='3 месяца', callback_data='Rate_2'),
        InlineKeyboardButton(text='6 месяцев', callback_data='Rate_3'),
    ],
    [
        InlineKeyboardButton(text='12 месяцев', callback_data='Rate_4'),
    ]
])

# Клавиатура изменения цены
change_price_btn = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='1 месяц', callback_data='SelectRate_1')],
    [InlineKeyboardButton(text='6 месяцев', callback_data='SelectRate_3')],
    [InlineKeyboardButton(text='3 месяца', callback_data='SelectRate_2')],
    [InlineKeyboardButton(text='12 месяцев', callback_data='SelectRate_4')],
])

# Стартовое меню
start_batons = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='Подписаться на контент', callback_data='ShowRates')],
    [InlineKeyboardButton(text='Наше сообщество', url='https://t.me/+DJfn6NyHmRAzMTdi')],
    [InlineKeyboardButton(text='Тех. поддержка', url='https://t.me/dashaadminrealtor')],
    [InlineKeyboardButton(text='Мой профиль', callback_data='my_profile')],
])

# Кнопки для рассылки
btn_mailing = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text='Да, начать рассылку', callback_data='go_mailing'),
        InlineKeyboardButton(text="Изменить сообщение", callback_data='stop_mailing'),
    ]
])

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
