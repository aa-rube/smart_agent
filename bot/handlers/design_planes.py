# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\design_planes.py
import os
import fitz
import aiohttp
import bot.utils.tokens as tk

from aiogram import Router, F, Bot
from aiogram.types import (
    Message,
    CallbackQuery,
    ContentType,
    FSInputFile,
    InputMediaPhoto,
)
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest

from bot.states.states import DesignStates
from executor.prompt_factory import create_floor_plan_prompt
from bot.text.texts import *
from bot.keyboards.inline import *
from bot.utils.image_processor import save_image_as_png
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import generate_floor_plan
from bot.utils.file_utils import safe_remove


# ===== helpers: редактирование текущего сообщения =====

async def _edit_text_or_caption(msg: Message, text: str, kb=None) -> None:
    """Обновить текст/подпись и клавиатуру текущего сообщения."""
    try:
        await msg.edit_text(text, reply_markup=kb)
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_caption(caption=text, reply_markup=kb)
        return
    except TelegramBadRequest:
        pass
    # если вообще ничего редактировать нельзя — попробуем хотя бы клавиатуру
    try:
        await msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass


async def _edit_or_replace_with_photo_file(bot: Bot, msg: Message, file_path: str, caption: str, kb=None) -> None:
    """
    Поменять контент текущего сообщения на фото с подписью (из файла).
    Если сообщение было текстовым — удаляем и отправляем фото заново.
    """
    try:
        media = InputMediaPhoto(media=FSInputFile(file_path), caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(file_path), caption=caption, reply_markup=kb)


async def _edit_or_replace_with_photo_url(bot: Bot, msg: Message, url: str, caption: str, kb=None) -> None:
    """
    Поменять контент текущего сообщения на фото с подписью (по URL).
    Если сообщение было текстовым — удаляем и отправляем фото заново.
    """
    try:
        media = InputMediaPhoto(media=url, caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=url, caption=caption, reply_markup=kb)


# ===== callbacks =====

async def start_design_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.message.chat.id

    if tk.get_tokens(user_id) > 0:
        await state.set_state(DesignStates.waiting_for_file)
        # заменяем текущее сообщение на фото с подписью «загрузите файл»
        await _edit_or_replace_with_photo_file(
            bot=bot,
            msg=callback.message,
            file_path='images/plan.jpg',
            caption=text_get_file(user_id),  # функция из texts, подставит инфо о токенах
            kb=start_retry  # ваша исходная клавиатура
        )
    else:
        # показываем оффер пополнить/подписаться в том же сообщении
        if db.get_variable(user_id, 'have_sub') == '0':
            await _edit_text_or_caption(callback.message, SUB_FREE, sub(user_id))
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, sub(user_id))

    await callback.answer()


async def handle_visualization_style(callback: CallbackQuery, state: FSMContext):
    viz_style_text = "sketch" if callback.data == "viz_sketch" else "realistic"
    await state.update_data(visualization_style=viz_style_text)
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=get_style_kb())
    await state.set_state(DesignStates.waiting_for_style)
    await callback.answer()


async def handle_style(callback: CallbackQuery, state: FSMContext, bot: Bot):
    await callback.answer("Принято! Начинаю генерацию...")

    user_id = callback.from_user.id
    if tk.get_tokens(user_id) <= 0:
        if db.get_variable(user_id, 'have_sub') == '0':
            await _edit_text_or_caption(callback.message, SUB_FREE, sub(user_id))
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, sub(user_id))
        await state.clear()
        return

    user_data = await state.get_data()
    image_path = user_data.get("image_path")
    visualization_style = user_data.get("visualization_style")

    interior_style = next(
        (btn.text.strip('💎 ') for row in (callback.message.reply_markup.inline_keyboard or [])
         for btn in row if btn.callback_data == callback.data),
        "Модерн"
    )

    # Создаём промпт без plan_type
    prompt = create_floor_plan_prompt(
        visualization_style=visualization_style,
        interior_style=interior_style
    )

    # вместо удаления — показываем «идёт генерация» в текущем сообщении
    await _edit_text_or_caption(callback.message, "⏳ Генерирую визуализацию… Это может занять до 1–2 минут.")

    try:
        coro = generate_floor_plan(floor_plan_path=image_path, prompt=prompt)
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro
        )

        if image_url:
            # меняем текущее сообщение на готовую картинку по URL
            await _edit_or_replace_with_photo_url(
                bot=bot,
                msg=callback.message,
                url=image_url,
                caption=TEXT_FINAL,
                kb=None
            )
            print(f'REMOVE TOKENS: {tk.remove_tokens(user_id)}')
        else:
            await _edit_text_or_caption(
                callback.message,
                we_are_so_sorry_try_again,
                kb=floor_plan
            )

    finally:
        if image_path and os.path.exists(image_path):
            if safe_remove(image_path):
                print(f"Временный файл удален: {image_path}")
            else:
                print(f"Не удалось удалить временный файл (занят): {image_path}")
        await state.clear()


# ===== message (upload stage) =====

async def handle_file(message: Message, state: FSMContext, bot: Bot):
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    user_id = message.from_user.id
    image_bytes = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()

    elif message.document and message.document.mime_type and message.document.mime_type.startswith('image/'):
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
                    if resp.status == 200 and 'image' in (resp.headers.get('Content-Type') or ''):
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
            # сразу переходим к выбору стиля визуализации (это уже новый шаг по сообщению)
            await message.answer("Выберите стиль визуализации:", reply_markup=get_visualization_style_kb())
            await state.set_state(DesignStates.waiting_for_visualization_style)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте ещё раз.")


# ===== router =====

def router(rt: Router):
    rt.callback_query.register(start_design_flow, F.data == "floor_plan")

    rt.message.register(
        handle_file,
        DesignStates.waiting_for_file,
        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT})
    )

    rt.callback_query.register(handle_visualization_style, DesignStates.waiting_for_visualization_style)
    rt.callback_query.register(handle_style, DesignStates.waiting_for_style)
