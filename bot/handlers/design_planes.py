#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\design_planes.py

import os
import fitz
import aiohttp
import bot.keyboards.inline as inline
import bot.utils.database as db
import bot.utils.tokens as tk

from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, ContentType, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction

from bot.states.states import DesignStates
from executor.prompt_factory import create_floor_plan_prompt
from bot.text.texts import *
from bot.utils.image_processor import save_image_as_png
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import generate_floor_plan
from bot.utils.file_utils import safe_remove



# Функция start_design_flow без изменений
async def start_design_flow(callback: CallbackQuery, state: FSMContext):
    user_id = callback.message.chat.id
    if tk.get_tokens(user_id) > 0:
        await callback.message.answer_photo(FSInputFile('images/plan.jpg'), caption=TEXT_GET_FILE)
        await state.set_state(DesignStates.waiting_for_file)
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

            # --- СРАЗУ ПЕРЕХОДИМ К ВЫБОРУ СТИЛЯ ВИЗУАЛИЗАЦИИ ---
            await message.answer("Выберите стиль визуализации:", reply_markup=inline.get_visualization_style_kb())
            await state.set_state(DesignStates.waiting_for_visualization_style)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте еще раз.")


# Функция handle_visualization_style без изменений
async def handle_visualization_style(callback: CallbackQuery, state: FSMContext):
    viz_style_text = "sketch" if callback.data == "viz_sketch" else "realistic"
    await state.update_data(visualization_style=viz_style_text)
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=inline.get_style_kb())
    await state.set_state(DesignStates.waiting_for_style)
    await callback.answer()


# --- ИЗМЕНЕНИЕ В ЭТОЙ ФУНКЦИИ ---
async def handle_style(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer("Принято! Начинаю генерацию...")

    user_id = callback.from_user.id
    if tk.get_tokens(user_id) <= 0:
        if db.get_variable(user_id, 'have_sub') == '0':
            await callback.message.answer(SUB_FREE, reply_markup=inline.sub(user_id))
        else:
            await callback.message.answer(SUB_PAY, reply_markup=inline.sub(user_id))
        await state.clear()
        return

    user_data = await state.get_data()
    image_path = user_data.get("image_path")
    # plan_type больше не нужен
    visualization_style = user_data.get("visualization_style")
    interior_style = next(
        (btn.text.strip('💎 ') for row in callback.message.reply_markup.inline_keyboard for btn in row if
         btn.callback_data == callback.data), "Модерн")

    # Создаем промпт без plan_type
    prompt = create_floor_plan_prompt(
        visualization_style=visualization_style,
        interior_style=interior_style
    )

    await callback.message.delete()

    try:
        coro = generate_floor_plan(floor_plan_path=image_path, prompt=prompt)
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            # Отправляем фото по URL, так как GPT-4-Vision возвращает URL
            await bot.send_photo(
                chat_id=user_id,
                photo=image_url,
                caption=TEXT_FINAL
            )
            print(f'REMOVE TOKENS: {tk.remove_tokens(user_id)}')
        else:
            await bot.send_message(user_id, "😔 К сожалению, не удалось сгенерировать изображение. Попробуйте еще раз.")

    finally:
        if image_path and os.path.exists(image_path):
            if safe_remove(image_path):
                print(f"Временный файл удален: {image_path}")
            else:
                print(f"Не удалось удалить временный файл (занят): {image_path}")
        await state.clear()

# --- ИЗМЕНЕНИЕ В РОУТЕРЕ ---
def router(rt: Router):
    rt.callback_query.register(start_design_flow, F.data == "design")
    rt.message.register(handle_file, DesignStates.waiting_for_file,
                        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT}))
    
    # Удаляем регистрацию handle_plan_type
    rt.callback_query.register(handle_visualization_style, DesignStates.waiting_for_visualization_style)
    rt.callback_query.register(handle_style, DesignStates.waiting_for_style)