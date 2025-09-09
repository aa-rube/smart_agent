#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\zero_design.py

import aiohttp
import fitz
import bot.keyboards.inline as inline
import bot.utils.database as db
import os
import bot.utils.tokens as tk

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ContentType, BufferedInputFile, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from bot.states.states import ZeroDesignStates
from executor.prompt_factory import create_prompt
from bot.text.texts import *
from bot.utils.image_processor import save_image_as_png
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import generate_design, download_image_from_url
from bot.utils.file_utils import safe_remove



async def start_zero_design_flow(callback: CallbackQuery, state: FSMContext):
    user_id = callback.message.chat.id
    if tk.get_tokens(user_id) > 0:
        await callback.message.answer_photo(FSInputFile('images/create.jpg'), caption=TEXT_GET_FILE_ZERO_DESIGN)
        await state.set_state(ZeroDesignStates.waiting_for_file)
    else:
        if db.get_variable(user_id, 'have_sub') == '0':
            await callback.message.answer(SUB_FREE, reply_markup=inline.sub(user_id))
        else:
            await callback.message.answer(SUB_PAY, reply_markup=inline.sub(user_id))
    await callback.answer()


async def handle_file(message: Message, state: FSMContext, bot: Bot):
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
    elif message.document and message.document.mime_type == 'application/pdf':
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        pdf_bytes = (await bot.download_file(file.file_path)).read()
        doc = fitz.open("pdf", pdf_bytes)
        if doc.page_count != 1:
            await message.answer(ERROR_PDF_PAGES)
            return
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=200)
        image_bytes = pix.tobytes("png")
        doc.close()
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
        await message.answer(ERROR_WRONG_INPUT)
        return

    if image_bytes:
        saved_path = await save_image_as_png(image_bytes, user_id)
        if saved_path:
            await state.update_data(image_path=saved_path)
            await message.answer(TEXT_PHOTO_UPLOADED, reply_markup=inline.get_room_type_kb())
            await state.set_state(ZeroDesignStates.waiting_for_room_type)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте еще раз.")


async def handle_room_type(callback: CallbackQuery, state: FSMContext):
    await state.update_data(room_type=callback.data.split('_', 1)[1])
    await callback.message.edit_text(TEXT_GET_FURNITURE_OPTION, reply_markup=inline.get_furniture_kb())
    await state.set_state(ZeroDesignStates.waiting_for_furniture)
    await callback.answer()


async def handle_furniture(callback: CallbackQuery, state: FSMContext):
    await state.update_data(furniture_choice=callback.data)
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=inline.get_style_kb())
    await state.set_state(ZeroDesignStates.waiting_for_style)
    await callback.answer()


async def handle_style(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    if tk.get_tokens(user_id) <= 0:
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
    furniture_choice = user_data.get("furniture_choice")
    style_choice = next((btn.text for row in callback.message.reply_markup.inline_keyboard for btn in row if
                         btn.callback_data == callback.data), "Модерн")

    furniture_text = "with furniture" if furniture_choice == "furniture_yes" else "without furniture"
    prompt = create_prompt(style=style_choice, room_type=room_type, furniture=furniture_choice)

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
            image_bytes = await download_image_from_url(image_url)
            if image_bytes:
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
    rt.callback_query.register(start_zero_design_flow, F.data == "0design")
    rt.message.register(handle_file, ZeroDesignStates.waiting_for_file,
                        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT}))

    rt.callback_query.register(handle_room_type, ZeroDesignStates.waiting_for_room_type)
    rt.callback_query.register(handle_furniture, ZeroDesignStates.waiting_for_furniture)
    rt.callback_query.register(handle_style, ZeroDesignStates.waiting_for_style)