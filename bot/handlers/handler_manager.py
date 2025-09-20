# smart_agent/bot/handlers/handler_manager.py
from __future__ import annotations

import bot.keyboards.inline as inline
from bot.keyboards.inline import *
from bot.text.texts import *
from bot.config import *
import bot.utils.tokens as tk
import bot.utils.admin_db as adb
import bot.utils.database as db
import logging
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from bot.utils.subscribe_partner_manager import ensure_partner_subs


frst_text = '''
👋 Привет!
Добро пожаловать в *ИНСТРУМЕНТЫ РИЭЛТОРА*.
Ты получил доступ к сервисам, которые помогают экономить время и привлекать больше клиентов.

Выбери нужный инструмент 👇

🏡 *Контент для соцсетей риелтора* — готовые публикации и идеи по подписке, чтобы регулярно вести свои соцсети.

🧠 *Продвинутые инструменты* для лучших продаж и привлечения клиентов.

✨ А так же наше закрытое сообщество для обсуждения, поддержки и обмена опытом.
'''


ai_tools_text = ''' 📐 *Генератор красивых планировок* (*β-версия*) — создавай наглядные схемы квартир и домов. 
🛋️ *Генератор дизайна интерьера* — быстрые визуализации стиля и меблировки. 
🤖 *ИИ для закрытия возражений* — готовые аргументы и ответы на частые сомнения клиентов. 
✍️ *ИИ для написания отзывов от клиентов* — шаблоны благодарственных сообщений. '''

smm_description = '''
📲 Наша SMM-команда ежедневно готовит профессиональный контент, который остаётся только опубликовать.
Никакого ИИ -  только опытные маркетологи с практикой в недвижимости.

В течение месяца ты получишь:

26 готовых тем для соцсетей и мессенджеров.

Посты для ВКонтакте, Telegram, Instagram, Одноклассники.

Сторис и истории для WhatsApp, Telegram, ВКонтакте, Instagram.

Короткие ролики для WhatsApp, Telegram, Shorts, Reels, TikTok, ВКонтакте.

💼 Всё создано, чтобы ты экономил время и получал заявки из своих соцсетей.

🔐 Доступ только для подписчиков.
Нажми «Оформить подписку» и пользуйся Всеми Инструментами Риэлтора без ограничений!
'''



# меню
frst_kb_inline = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🏡 Контент для соцсетей риелтора', callback_data='smm_content')],
        [InlineKeyboardButton(text='🧠 Продвинутые инструменты', callback_data='nav.ai_tools')],

        [InlineKeyboardButton(text='Наше сообщество', url='https://t.me/+DJfn6NyHmRAzMTdi')],
        [InlineKeyboardButton(text='Тех. поддержка', url='https://t.me/dashaadminrealtor')],
    ])

ai_tools_inline = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📐 Генератор красивых планировок",         callback_data="floor_plan"), ],
        [InlineKeyboardButton(text="🛋️ Генератор дизайна интерьера",           callback_data="nav.design_home"), ],
        [InlineKeyboardButton(text="🤖 ИИ для закрытия возражений",            callback_data="nav.objection_start"), ],
        [InlineKeyboardButton(text="✍️ ИИ для написания отзывов от клиентов",  callback_data="nav.feedback_home"), ],
        [InlineKeyboardButton(text="✨ Summary диалога с клиентом",            callback_data="nav.summary_home"), ],
        [InlineKeyboardButton(text="💎 Генератор продающих описаний объектов", callback_data="nav.descr_home"), ],
        [InlineKeyboardButton(text="⬅️ Назад",                                 callback_data="start_retry")]
    ])




# --- единый хелпер инициализации для Message | CallbackQuery ---
async def init_user_event(evt: Union[Message, CallbackQuery]) -> None:
    """
    Гарантирует, что пользователь есть в обеих БД и имеет дефолтные значения.
    Работает и для входящих сообщений, и для callback’ов.
    """
    if isinstance(evt, CallbackQuery):
        msg = evt.message
        username = evt.from_user.username if evt.from_user else ""
    else:
        msg = evt
        username = evt.from_user.username if evt.from_user else ""

    if not msg:
        return

    user_id = msg.chat.id

    # основная БД
    if not db.check_and_add_user(user_id):
        db.set_variable(user_id, 'tokens', 2)
        db.set_variable(user_id, 'have_sub', 0)

        # админская БД (подписки/уведомления)
        adb.init_notification_table()
        adb.inicialize_users(user_id, username or "")


# --- helpers for editing current message (callbacks) ---
async def _edit_text_safe(cb: CallbackQuery, text: str, kb=None):
    # инициализация для callback
    await init_user_event(cb)

    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        try:
            await cb.message.edit_caption(caption=text, reply_markup=kb)
        except TelegramBadRequest:
            try:
                await cb.message.edit_reply_markup(reply_markup=kb)
            except TelegramBadRequest:
                pass
    await cb.answer()


# --- /start и основной экран ---
async def frst_msg(message: Message, state: FSMContext, bot: Bot):
    await init_user_event(message)

    user_id = message.chat.id
    skip = db.get_variable(user_id, 'skip_subscribe')

    if not skip:
        # проверка партнёрских подписок (если не подписан — покажем ссылки и выйдем)
        if not await ensure_partner_subs(bot, message, retry_callback_data="start_retry", columns=2):
            return

    # Отправка логотипа: путь внутри DATA_DIR (без ведущего слэша).
    logo_rel = "img/bot/logo1.jpg"
    logo_path = get_file_path(logo_rel)
    try:
        if Path(logo_path).exists():
            await message.answer_photo(FSInputFile(logo_path))
        else:
            logging.warning("Logo not found: %s (resolved from %s)", logo_path, logo_rel)
    except Exception as e:
        # Не блокируем сценарий приветствия, просто логируем проблему.
        logging.exception("Failed to send logo photo: %s", e)
    await message.answer(frst_text, reply_markup=frst_kb_inline)


async def ai_tools(callback: CallbackQuery):
    await init_user_event(callback)
    await _edit_text_safe(callback, ai_tools_text, ai_tools_inline)


# --- callbacks (все редактируют текущее сообщение) ---
async def check_subscribe_retry(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await init_user_event(callback)

    if not await ensure_partner_subs(bot, callback, retry_callback_data="start_retry", columns=2):
        await callback.answer("Похоже, ещё не на все каналы подписаны 🤏", show_alert=True)
        return

    await _edit_text_safe(callback, frst_text, frst_kb_inline)


async def skip_subscribe(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await init_user_event(callback)

    user_id = callback.from_user.id
    db.set_variable(user_id, 'tokens', 0)
    db.set_variable(user_id, 'skip_subscribe', True)

    await _edit_text_safe(callback, frst_text, frst_kb_inline)


async def show_rates(evt: Message | CallbackQuery):
    if isinstance(evt, CallbackQuery):
        await init_user_event(evt)
        await _edit_text_safe(evt, info_rates_message, select_rates_inline)
    else:
        await init_user_event(evt)
        await evt.answer(info_rates_message, reply_markup=select_rates_inline)


async def smm_content(callback: CallbackQuery):
    await init_user_event(callback)
    await _edit_text_safe(callback, smm_description, get_smm_subscribe_inline)


async def my_profile(callback: CallbackQuery):
    await init_user_event(callback)

    info = adb.get_my_info(callback.from_user.id)
    if info:
        text = (
            f'Подписка: {"YES" if info[0] else "NO"}\n'
            f'Дата оплаты подписки: {info[1] or "-"}\n'
            f'Дата окончания подписки: {info[2] or "-"}'
        )
        await _edit_text_safe(callback, text)
    else:
        await _edit_text_safe(callback, "Профиль не найден.")


async def design_home(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await init_user_event(callback)

    user_id = callback.from_user.id
    await _edit_text_safe(callback, start_plan(user_id), design_inline)


# --- commands (messages) ---

async def sub_cmd(message: Message, state: FSMContext, bot: Bot):
    await init_user_event(message)
    user_id = message.chat.id
    await message.answer(SUB_PAY, reply_markup=inline.sub(user_id))


async def help_cmd(message: Message, state: FSMContext, bot: Bot):
    await init_user_event(message)
    await message.answer(HELP, reply_markup=inline.help())


async def add_tokens(message: Message, state: FSMContext, bot: Bot):
    await init_user_event(message)
    user_id = message.chat.id
    tk.add_tokens(user_id, 100)
    await message.answer("Added 100 tokens, be happy")


def router(rt: Router):
    # messages
    rt.message.register(frst_msg, CommandStart())
    rt.message.register(sub_cmd, Command("sub"))
    rt.message.register(add_tokens, Command("add"))
    rt.message.register(frst_msg, Command("main"))
    rt.message.register(help_cmd, Command("support"))

    # callbacks (все редактируют текущее сообщение)
    rt.callback_query.register(ai_tools, F.data == 'nav.ai_tools')
    rt.callback_query.register(design_home, F.data == 'nav.design_home')
    rt.callback_query.register(check_subscribe_retry, F.data == 'start_retry')
    rt.callback_query.register(skip_subscribe, F.data == 'skip_subscribe')
    rt.callback_query.register(show_rates, F.data == 'show_rates')
    rt.callback_query.register(my_profile, F.data == 'my_profile')
    rt.callback_query.register(smm_content, F.data == 'smm_content')
