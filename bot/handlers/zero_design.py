# smart_agent/bot/handlers/zero_design.py

import aiohttp
import fitz
import bot.utils.tokens as tk

from aiogram import Router, F, Bot
from aiogram.types import *
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest

from bot.states.states import ZeroDesignStates
from executor.prompt_factory import create_prompt
from bot.text.texts import *
from bot.keyboards.inline import *
from bot.config import *
from bot.utils.image_processor import save_image_as_png
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import generate_design, download_image_from_url
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


async def _edit_or_replace_with_photo(bot: Bot, msg: Message, photo_path: str, caption: str, kb=None) -> None:
    """
    Поменять контент текущего сообщения на фото с подписью.
    Telegram не позволяет превращать текст в фото редактированием — тогда:
      1) попытка edit_media
      2) если не вышло — удаляем и отправляем новое фото (визуально как «апдейт»)
    """
    try:
        media = InputMediaPhoto(media=FSInputFile(photo_path), caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        # сообщение было текстовым или другой медиа-тип → удаляем и шлём фото заново
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(photo_path), caption=caption, reply_markup=kb)


# ===== callbacks =====

async def start_zero_design_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.message.chat.id

    if tk.get_tokens(user_id) > 0:
        # заменить текущее сообщение на карточку с фото-инструкцией
        await _edit_or_replace_with_photo(
            bot=bot,
            msg=callback.message,
            photo_path=get_file_path('img/bot/create.jpg'),
            caption=TEXT_GET_FILE_ZERO_DESIGN,
            kb=design_home_inline,
        )
        await state.set_state(ZeroDesignStates.waiting_for_file)
    else:
        # показать экран оплаты/подписки в том же сообщении
        if db.get_variable(user_id, 'have_sub') == '0':
            await _edit_text_or_caption(callback.message, SUB_FREE, sub(user_id))
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, sub(user_id))

    await callback.answer()


async def handle_room_type(callback: CallbackQuery, state: FSMContext):
    await state.update_data(room_type=callback.data.split('_', 1)[1])
    await callback.message.edit_text(TEXT_GET_FURNITURE_OPTION, reply_markup=get_furniture_kb())
    await state.set_state(ZeroDesignStates.waiting_for_furniture)
    await callback.answer()


async def handle_furniture(callback: CallbackQuery, state: FSMContext):
    await state.update_data(furniture_choice=callback.data)
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=get_style_kb())
    await state.set_state(ZeroDesignStates.waiting_for_style)
    await callback.answer()


async def handle_style(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id

    # нет токенов — показываем оплату/подписку в ТЕКУЩЕМ сообщении
    if tk.get_tokens(user_id) <= 0:
        if db.get_variable(user_id, 'have_sub') == '0':
            await _edit_text_or_caption(callback.message, SUB_FREE, sub(user_id))
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, sub(user_id))
        await state.clear()
        await callback.answer()
        return

    user_data = await state.get_data()
    image_path = user_data.get("image_path")
    room_type = user_data.get("room_type")
    furniture_choice = user_data.get("furniture_choice")

    # вытащим выбранный стиль из текущей клавиатуры
    style_choice = next(
        (btn.text for row in (callback.message.reply_markup.inline_keyboard or [])
         for btn in row if btn.callback_data == callback.data),
        "Модерн"
    )

    prompt = create_prompt(style=style_choice, room_type=room_type, furniture=furniture_choice)

    # визуально «апдейтим» экран: показываем, что генерируем
    await _edit_text_or_caption(callback.message, "⏳ Генерирую дизайн… Это может занять до 1–2 минут.")

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
                # сохраним в файл и заменим текущий экран на результат (фото + подпись)
                tmp_path = get_file_path(f"/img/tmp/result_{user_id}.png")
                with open(tmp_path, "wb") as f:
                    f.write(image_bytes)

                await _edit_or_replace_with_photo(
                    bot=bot,
                    msg=callback.message,
                    photo_path=tmp_path,
                    caption=TEXT_FINAL,
                    kb=None
                )
                print(f'REMOVE TOKENS: {tk.remove_tokens(user_id)}')

                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            else:
                await _edit_text_or_caption(
                    callback.message,
                    unsuccessful_try_later,
                    kb=design_home_inline
                )
        else:
            await _edit_text_or_caption(
                callback.message,
                we_are_so_sorry_try_again,
                kb=design_home_inline
             )

    finally:
        if image_path and os.path.exists(image_path):
            if safe_remove(image_path):
                print(f"Временный файл удален: {image_path}")
            else:
                print(f"Не удалось удалить временный файл (занят): {image_path}")
        await state.clear()
        await callback.answer()


# ===== message (file upload) =====

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
            await message.answer(TEXT_PHOTO_UPLOADED, reply_markup=get_room_type_kb())
            await state.set_state(ZeroDesignStates.waiting_for_room_type)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте ещё раз.")


def router(rt: Router):
    rt.callback_query.register(start_zero_design_flow, F.data == "0design")

    rt.message.register(
        handle_file,
        ZeroDesignStates.waiting_for_file,
        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT})
    )

    rt.callback_query.register(handle_room_type, ZeroDesignStates.waiting_for_room_type)
    rt.callback_query.register(handle_furniture, ZeroDesignStates.waiting_for_furniture)
    rt.callback_query.register(handle_style, ZeroDesignStates.waiting_for_style)
