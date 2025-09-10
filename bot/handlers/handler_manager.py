# smart_agent/bot/handlers/handler_manager.py

import bot.keyboards.inline as inline
import bot.utils.admin_db as adb

import bot.utils.tokens as tk
from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from bot.text.texts import *  # frst_text, start_plan, HELP и т.п.


async def frst_msg(message: Message, state: FSMContext, bot: Bot):
    user_id = message.chat.id
    if not db.check_and_add_user(user_id):
        db.set_variable(user_id, 'tokens', 2)
        db.set_variable(user_id, 'have_sub', 0)

        # Админская БД для рассылок/подписок
        adb.init_notification_table()
        adb.inicialize_users(user_id, message.from_user.username or "")

    await message.answer_photo(FSInputFile('images/logo1.jpg'))
    await message.answer(frst_text, reply_markup=inline.frst_kb)


async def design_start(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """Старт экрана выбора с учетом токенов/подписки (приходит из inline-кнопки)."""
    user_id = callback.from_user.id  # у CallbackQuery нет .chat, берем из from_user

    await callback.message.answer(
        start_plan(user_id),
        reply_markup=inline.start_kb
    )
    await callback.answer()


async def sub(message: Message, state: FSMContext, bot: Bot):
    user_id = message.chat.id
    await message.answer(SUB_PAY, reply_markup=inline.sub(user_id))


async def help(message: Message, state: FSMContext, bot: Bot):
    await message.answer(HELP, reply_markup=inline.help())


async def add_tokens(message: Message, state: FSMContext, bot: Bot):
    user_id = message.chat.id
    tk.add_tokens(user_id, 100)
    await message.answer("Added 100 tokens, be happy")


def router(rt: Router):
    rt.message.register(frst_msg, CommandStart())
    rt.message.register(sub, Command("sub"))
    rt.message.register(add_tokens, Command("add"))
    rt.message.register(frst_msg, F.text == '🏁Главное меню')
    rt.message.register(help, F.text == '🧑‍💻Тех. поддержка')
    rt.callback_query.register(design_start, F.data == 'design_start')
