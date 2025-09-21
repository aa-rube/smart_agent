# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\interior_redesign.py
import aiohttp
import bot.utils.tokens as tk
from aiogram import Router, F, Bot
from aiogram.types import *
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest

from bot.states.states import RedesignStates
from executor.prompt_factory import create_prompt
from bot.text.texts import *
from bot.config import *
from bot.utils.image_processor import save_image_as_png
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.ai_processor import generate_design, download_image_from_url
from bot.utils.file_utils import safe_remove
from bot.utils.database import is_trial_active
from aiogram.utils.keyboard import InlineKeyboardBuilder


SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)


def _has_access(user_id: int) -> bool:
    return is_trial_active(user_id) or tk.get_tokens(user_id) > 0

design_home_inline = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.design_home")]
    ])


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
    Если сообщение было текстовым (Telegram не даёт менять тип) — удаляем и отправляем фото заново.
    """
    try:
        media = InputMediaPhoto(media=FSInputFile(photo_path), caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        # сообщение было текстовым / другой тип медиа
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(photo_path), caption=caption, reply_markup=kb)


# ===== callbacks =====
async def start_redesign_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.message.chat.id

    if _has_access(user_id):
        # показать экран «загрузите фото» в текущем сообщении (как апдейт)
        await _edit_or_replace_with_photo(
            bot=bot,
            msg=callback.message,
            photo_path=get_file_path('data/img/bot/design.jpg'),
            caption=text_get_photo_redesign(user_id),  # функция из texts, подставит {tokens_text}
            kb=design_home_inline
        )
        await state.set_state(RedesignStates.waiting_for_photo)
    else:
        # показать оффер подписки / оплаты — тоже редактированием
        if not (db.get_variable(user_id, 'have_sub') == '1'):
            await _edit_text_or_caption(callback.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, SUBSCRIBE_KB)

    await callback.answer()


async def handle_room_type(callback: CallbackQuery, state: FSMContext):
    await state.update_data(room_type=callback.data.split('_', 1)[1])
    await callback.message.edit_text(TEXT_GET_STYLE, reply_markup=get_style_kb())
    await state.set_state(RedesignStates.waiting_for_style)
    await callback.answer()


async def handle_style(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id

    # нет доступа — триал кончился и токенов нет
    if not _has_access(user_id):
        if not (db.get_variable(user_id, 'have_sub') == '1'):
            await _edit_text_or_caption(callback.message, SUB_FREE, SUBSCRIBE_KB)
        else:
            await _edit_text_or_caption(callback.message, SUB_PAY, SUBSCRIBE_KB)
        await state.clear()
        await callback.answer()
        return

    user_data = await state.get_data()
    image_path = user_data.get("image_path")
    room_type = user_data.get("room_type")

    # вытаскиваем выбранный стиль из нажатой кнопки
    style_choice = next(
        (btn.text for row in (callback.message.reply_markup.inline_keyboard or [])
         for btn in row if btn.callback_data == callback.data),
        "Модерн"
    )

    prompt = create_prompt(style=style_choice, room_type=room_type)

    # визуально обновим экран на «идёт генерация»
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
                # временно сохраним, затем заменим текущий экран на фото-результат
                # лучше без начального слэша, но get_file_path умеет и так
                tmp_path = get_file_path(f"img/tmp/redesign_{user_id}.png")
                # гарантируем, что каталог существует
                os.makedirs(os.path.dirname(tmp_path), exist_ok=True)
                with open(tmp_path, "wb") as f:
                    f.write(image_bytes)

                await _edit_or_replace_with_photo(
                    bot=bot,
                    msg=callback.message,
                    photo_path=tmp_path,
                    caption=TEXT_FINAL,
                    kb=None
                )
                if not is_trial_active(user_id) and tk.get_tokens(user_id) > 0:
                    tk.remove_tokens(user_id)
                print('Generation done')

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


# ===== message (upload stage) =====
async def handle_photo(message: Message, state: FSMContext, bot: Bot):
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
        await message.answer(ERROR_WRONG_INPUT_REDESIGN)
        return

    if image_bytes:
        saved_path = await save_image_as_png(image_bytes, user_id)
        if saved_path:
            await state.update_data(image_path=saved_path)
            await message.answer(TEXT_GET_ROOM_TYPE, reply_markup=get_room_type_kb())
            await state.set_state(RedesignStates.waiting_for_room_type)
        else:
            await message.answer("Произошла ошибка при обработке файла. Попробуйте ещё раз.")


def router(rt: Router):
    rt.callback_query.register(start_redesign_flow, F.data == "redesign")

    rt.message.register(
        handle_photo,
        RedesignStates.waiting_for_photo,
        F.content_type.in_({ContentType.PHOTO, ContentType.TEXT, ContentType.DOCUMENT})
    )

    rt.callback_query.register(handle_room_type, RedesignStates.waiting_for_room_type)
    rt.callback_query.register(handle_style, RedesignStates.waiting_for_style)