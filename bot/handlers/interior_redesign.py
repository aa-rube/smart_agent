#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\interior_redesign.py

import aiohttp
import bot.keyboards.inline as inline
import bot.utils.database as db
import os
import bot.utils.tokens as tk

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ContentType, BufferedInputFile, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from bot.states.states import RedesignStates
from executor.prompt_factory import create_prompt
from bot.text.texts import *
from bot.utils.image_processor import save_image_as_png
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import generate_design, download_image_from_url
from bot.utils.file_utils import safe_remove



async def start_redesign_flow(callback: CallbackQuery, state: FSMContext):
    user_id = callback.message.chat.id
    if tk.get_tokens(user_id) > 0:
        await callback.message.answer_photo(FSInputFile('images/int.jpg'), TEXT_GET_PHOTO_REDESIGN)
        await state.set_state(RedesignStates.waiting_for_photo)
    else:
        if db.get_variable(user_id, 'have_sub') == '0':
            await callback.message.answer(SUB_FREE, reply_markup=inline.sub(user_id))
        else:
            await callback.message.answer(SUB_PAY, reply_markup=inline.sub(user_id))
    await callback.answer()


async def handle_photo(message: Message, state: FSMContext, bot: Bot):
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    user_id = message.from_user.id
    image_bytes = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()
    elif message.document and message.document.mime_type.startswith('image/'):
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()
    elif message.text and (message.text.startswith('http://') or message.text.startswith('https://')):
        url = message.text
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200 and 'image' in resp.headers.get('Content-Type', ''):
                        image_bytes = await resp.read()
                    else:
                        await message.answer(ERROR_LINK)
                        return
        except Exception:
            await message.answer(ERROR_LINK)
            return
    else:
        await message.answer(ERROR_WRONG_INPUT_REDESIGN)
        return

    if image_bytes:
        saved_path = await save_image_as_png(image_bytes, user_id)
        if saved_path:
            await state.update_data(image_path=saved_path)
            await message.answer(TEXT_GET_ROOM_TYPE, reply_markup=inline.get_room_type_kb())
            await state.set_state(RedesignStates.waiting_for_room_type)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте еще раз.")


async def handle_room_type(callback: CallbackQuery, state: FSMContext):
    await state.update_data(room_type=callback.data.split('_', 1)[1])
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=inline.get_style_kb())
    await state.set_state(RedesignStates.waiting_for_style)
    await callback.answer()


async def handle_style(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    if tk.get_tokens(user_id) <= 0:
        # ... блок проверки токенов ...
        if db.get_variable(user_id, 'have_sub') == '0':
            await callback.message.answer(SUB_FREE, reply_markup=inline.sub(user_id))
        else:
            await callback.message.answer(SUB_PAY, reply_markup=inline.sub(user_id))
        await state.clear()
        await callback.answer()
        return

    user_data = await state.get_data()
    image_path = user_data.get("image_path")
    room_type = user_data.get("room_type")
    style_choice = next((btn.text for row in callback.message.reply_markup.inline_keyboard for btn in row if btn.callback_data == callback.data), "Модерн")

    prompt = create_prompt(style=style_choice, room_type=room_type)

    await callback.message.delete()

    try:
        coro = generate_design(image_path=image_path, prompt=prompt)
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            # Скачиваем фото по полученному URL
            image_bytes = await download_image_from_url(image_url)
            if image_bytes:
                # Отправляем скачанные байты, а не URL
                await bot.send_photo(
                    chat_id=user_id,
                    photo=BufferedInputFile(image_bytes, filename="result.png"),
                    caption=TEXT_FINAL
                )
                print(f'REMOVE TOKENS: {tk.remove_tokens(user_id)}')
            else:
                await bot.send_message(user_id, "😔 Не удалось скачать сгенерированное изображение. Попробуйте позже.")
        else:
            await bot.send_message(user_id, "😔 К сожалению, не удалось сгенерировать изображение. Попробуйте еще раз.")

    finally:
        if image_path and os.path.exists(image_path):
            if safe_remove(image_path):
                print(f"Временный файл удален: {image_path}")
            else:
                print(f"Не удалось удалить временный файл (занят): {image_path}")
        await state.clear()
        await callback.answer()


def router(rt: Router):
    rt.callback_query.register(start_redesign_flow, F.data == "redesign")
    rt.message.register(handle_photo, RedesignStates.waiting_for_photo,
                        F.content_type.in_({ContentType.PHOTO, ContentType.TEXT}))
    rt.callback_query.register(handle_room_type, RedesignStates.waiting_for_room_type)
    rt.callback_query.register(handle_style, RedesignStates.waiting_for_style)