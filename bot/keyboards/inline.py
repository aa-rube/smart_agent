from typing import List, Dict, Union, Optional

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.utils import youmoney

start_retry = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")]
    ]
)

design_start = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="design_start")]
    ]
)

floor_plan = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="floor_plan")]
    ]
)


frst_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text='🏡 Контент для соцсетей риелтора', callback_data='smm_content')],
        [InlineKeyboardButton(text="📐 Генератор красивых планировок", callback_data="floor_plan"), ],
        [InlineKeyboardButton(text="🛋️ Генератор дизайна интерьера", callback_data="design_start"), ],
        [InlineKeyboardButton(text="🤖 ИИ для закрытия возражений", callback_data="non"), ],
        [InlineKeyboardButton(text="✍️ ИИ для написания отзывов от клиентов", callback_data="non3"), ],
        [InlineKeyboardButton(text="💎 Генератор продающих описаний объектов", callback_data="non2"), ],

        [InlineKeyboardButton(text='Наше сообщество', url='https://t.me/+DJfn6NyHmRAzMTdi')],
        [InlineKeyboardButton(text='Тех. поддержка', url='https://t.me/dashaadminrealtor')],
    ]
)

design_inline = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="🛋 Редизайн интерьера", callback_data="redesign")
        ],
        [
            InlineKeyboardButton(text="🆕 Дизайн с нуля", callback_data="0design")
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")
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


# Клавиатура выбора тарифа
get_smm_subscribe = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")]
    ]
)

select_rates = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text='1 месяц', callback_data='Rate_1'),
        InlineKeyboardButton(text='3 месяца', callback_data='Rate_2'),
        InlineKeyboardButton(text='6 месяцев', callback_data='Rate_3')
    ],
    [
        InlineKeyboardButton(text='12 месяцев', callback_data='Rate_4')
    ],
    [
        InlineKeyboardButton(text='⬅️ Назад', callback_data='smm_content')
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
    [InlineKeyboardButton(text='Подписаться на контент', callback_data='show_rates')],
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
