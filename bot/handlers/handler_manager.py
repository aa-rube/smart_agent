#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\handler_manager.py

from bot.handlers.subscribe_partner_manager import ensure_partner_subs
import bot.keyboards.inline as inline
from bot.keyboards.inline import *
from bot.text.texts import *
from bot.config import *
import bot.utils.tokens as tk
import bot.utils.admin_db as adb

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest


async def frst_msg(message: Message, state: FSMContext, bot: Bot):
    user_id = message.chat.id

    if not db.check_and_add_user(user_id):
        db.set_variable(user_id, 'tokens', 2)
        db.set_variable(user_id, 'have_sub', 0)
        adb.init_notification_table()
        adb.inicialize_users(user_id, message.from_user.username or "")

    skip = db.get_variable(user_id, 'skip_subscribe')

    if not skip:
        # Если не подписан — покажем ссылки и выйдем (тут message, не callback, поэтому допустимо отправить новое)
        if not await ensure_partner_subs(bot, message, retry_callback_data="start_retry", columns=2):
            return

    await message.answer_photo(FSInputFile(get_file_path('/img/bot/logo1.jpg')))
    await message.answer(frst_text, reply_markup=inline.frst_kb_inline)


# --- helpers for editing current message (callbacks) ---

async def _edit_text_safe(cb: CallbackQuery, text: str, kb=None):
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


# --- callbacks ---

async def check_subscribe_retry(callback: CallbackQuery, state: FSMContext, bot: Bot):
    # повторная проверка
    if not await ensure_partner_subs(bot, callback, retry_callback_data="start_retry", columns=2):
        await callback.answer("Похоже, ещё не на все каналы подписаны 🤏", show_alert=True)
        return

    # Всё ок — заменяем текущее сообщение на приветствие
    await _edit_text_safe(callback, frst_text, frst_kb_inline)


async def skip_subscribe(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    db.set_variable(user_id, 'tokens', 0)
    db.set_variable(user_id, 'skip_subscribe', True)

    await _edit_text_safe(callback, frst_text, frst_kb_inline)


async def show_rates(evt: Message | CallbackQuery):
    if isinstance(evt, CallbackQuery):
        await _edit_text_safe(evt, info_rates_message, select_rates_inline)
    else:
        await evt.answer(info_rates_message, reply_markup=select_rates_inline)


async def smm_content(callback: CallbackQuery):
    await _edit_text_safe(callback, smm_description, get_smm_subscribe_inline)


async def objection_start(callback: CallbackQuery):
    await _edit_text_safe(callback, objection_description, start_retry_inline)


async def my_profile(callback: CallbackQuery):
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


async def design_start(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    await _edit_text_safe(callback, start_plan(user_id), design_inline)


# --- commands (messages) ---

async def sub_cmd(message: Message, state: FSMContext, bot: Bot):
    user_id = message.chat.id
    await message.answer(SUB_PAY, reply_markup=inline.sub(user_id))


async def help_cmd(message: Message, state: FSMContext, bot: Bot):
    await message.answer(HELP, reply_markup=inline.help())


async def add_tokens(message: Message, state: FSMContext, bot: Bot):
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
    rt.callback_query.register(design_start_inline, F.data == 'design_start')
    rt.callback_query.register(check_subscribe_retry, F.data == 'start_retry')
    rt.callback_query.register(skip_subscribe, F.data == 'skip_subscribe')
    rt.callback_query.register(show_rates, F.data == 'show_rates')
    rt.callback_query.register(my_profile, F.data == 'my_profile')
    rt.callback_query.register(smm_content, F.data == 'smm_content')
    rt.callback_query.register(smm_content, F.data == 'smm_content')

    rt.callback_query.register(objection_start, F.data == 'objection_start')
