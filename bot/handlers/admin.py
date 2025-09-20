# smart_agent/bot/handlers/admin.py
from __future__ import annotations

from datetime import datetime
from typing import List

from aiogram import Router, F, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    LabeledPrice,
    ChatInviteLink,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

import bot.config as cfg
import bot.utils.admin_db as adb
import bot.text.texts as texts  # глобальные тексты: start_message, info_rates_message
from bot.states.states import (
    PriceStates,
    ChangeStartText,
    ChangeTextOfRates,
    CreateNewPostState,
    CreateMailing,
    EditPostState,
)

# =============================================================================
# UX тексты (относятся к админке)
# =============================================================================
ADMIN_MENU_TEXT = (
    "--==admin_menu==--\n"
    "<b>Список админ-команд</b>\n\n"
    "/change_start_message — <b>Изменить текст приветственного сообщения</b>\n"
    "/create_new_post — <b>Опубликовать новый пост</b>\n"
    "/edit_post_ — <b>Редактировать последние 5 постов</b>\n"
    "/change_message_of_rates — <b>Изменить текст после «Подписаться на контент»</b>\n"
    "/change_price — <b>Изменить цены тарифов</b>\n\n"
    "Напоминания:\n"
    "/show_notifications — показать\n"
    "/set_notice (days) (message) — установить\n"
    "/delete_notification (days) — удалить\n"
)
NO_ACCESS_TEXT = "У вас нет доступа к админ панели."
ASK_RATE_TO_CHANGE_TEXT = "Выберите тариф, который хотите поменять"
ASK_NEW_PRICE_TEXT_TPL = "Введите новую цену для тарифа №{n} (текущая: {cur} руб.)"
ASK_RATES_INFO_TEXT = "Напишите текст для сообщения после кнопки 'Подписаться на контент'"
ASK_START_TEXT = "Напишите текст для /start"
TEXT_UPDATED_OK = "Текст обновлён.\n\n{}"
START_TEXT_UPDATED_OK = "Готово! Новый текст:\n\n{}"
NO_POSTS_TO_EDIT = "Нет постов для редактирования."
SELECT_POST_TO_EDIT = "Выберите пост для редактирования:"
ASK_NEW_POST_TEXT = "Введите новый текст для поста."
POST_EDITED_OK = "Пост отредактирован."
ASK_NEW_POST_FOR_CHANNEL = "Отправьте пост для публикации в канал"
POST_PUBLISHED_OK = "Пост опубликован."
ASK_MAILING_TEXT = "Отправьте сообщение для рассылки"
CONFIRM_MAILING_TEXT_TPL = "Начать рассылку сообщения:\n\n{}"
MAILING_DONE = "Рассылка завершена!"
UNKNOWN_TARIFF = "Неизвестный тариф"
SUCCESS_PAYMENT_TPL = (
    "Оплата прошла успешно!\n"
    "Сумма: {amount:.2f} {currency}\n"
    "Тариф: {months} месяц(ев)"
)
PERSONAL_INVITE_TPL = "Ваша персональная ссылка для вступления:\n{}"
INVITE_ERROR_TPL = "Ошибка при создании ссылки: {}"
POSTS_HEADER = "Ниже посты этого месяца ↓"
SUB_EXPIRED_MSG = (
    "Ваша подписка истекла. Чтобы восстановить доступ, оформите новую подписку."
)

# =============================================================================
# Клавиатуры (только те, что использует админка)
# =============================================================================

# Клавиатура изменения цены
CHANGE_PRICE_KB = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="1 месяц", callback_data="SelectRate_1")],
        [InlineKeyboardButton(text="6 месяцев", callback_data="SelectRate_3")],
        [InlineKeyboardButton(text="3 месяца", callback_data="SelectRate_2")],
        [InlineKeyboardButton(text="12 месяцев", callback_data="SelectRate_4")],
    ]
)

# Кнопки подтверждения/изменения текста для рассылки
BTN_MAILING = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="Да, начать рассылку", callback_data="go_mailing"),
            InlineKeyboardButton(text="Изменить сообщение", callback_data="stop_mailing"),
        ]
    ]
)

# Динамическая клавиатура выбора поста для редактирования
def _posts_kb(posts: List[dict]) -> InlineKeyboardMarkup:
    rows = []
    for post in posts:
        btn = InlineKeyboardButton(
            text=f"Пост от {post['date'].strftime('%d.%m.%Y')}",
            callback_data=f"edit_post_{post['message_id']}",
        )
        rows.append([btn])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# =============================================================================
# Прайс (в копейках)
# =============================================================================

Price = {
    "Rate_1": 250000,   # 2 500 ₽
    "Rate_2": 650000,   # 6 500 ₽
    "Rate_3": 1250000,  # 12 500 ₽
    "Rate_4": 2400000,  # 24 000 ₽
}

# =============================================================================
# ХЕНДЛЕРЫ
# =============================================================================

async def admin_menu(message: Message):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return
    await message.answer(ADMIN_MENU_TEXT, parse_mode="HTML")

# ---- Изменение цен ----

async def change_price(message: Message):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return
    await message.answer(ASK_RATE_TO_CHANGE_TEXT, reply_markup=CHANGE_PRICE_KB)

async def select_changed_price(callback: CallbackQuery, state: FSMContext):
    selected_rate = callback.data.split("_", 1)[1]
    display_price = Price[f"Rate_{selected_rate}"] // 100
    await state.update_data(selected_rate=selected_rate)
    await callback.message.answer(
        ASK_NEW_PRICE_TEXT_TPL.format(n=selected_rate, cur=display_price),
        parse_mode=None,
    )
    await state.set_state(PriceStates.waiting_for_new_price)
    await callback.message.delete()
    await callback.answer()

async def process_new_price(message: Message, state: FSMContext):
    text = (message.text or "").replace(",", ".").replace(" ", "")
    try:
        price_float = float(text)
    except ValueError:
        await message.answer("Введите корректное число, например: 1500 или 1500.00")
        return
    new_price = int(round(price_float * 100))
    data = await state.get_data()
    sel = data.get("selected_rate")
    if not sel:
        await message.answer("Ошибка состояния, попробуйте снова /ChangePrice")
        await state.clear()
        return
    Price[f"Rate_{sel}"] = new_price
    await message.answer(f"Цена тарифа Rate_{sel} обновлена до {new_price // 100} руб.")
    await state.clear()

# ---- Изменение стартовых текстов ----

async def change_message_of_rates(message: Message, state: FSMContext):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return
    await message.answer(ASK_RATES_INFO_TEXT)
    await state.set_state(ChangeTextOfRates.GetText)

async def change_text_of_rates_set_text(message: Message, state: FSMContext):
    texts.info_rates_message = message.text or ""
    await state.clear()
    await message.answer(TEXT_UPDATED_OK.format(texts.info_rates_message))

async def change_start_messages_start(message: Message, state: FSMContext):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return
    await message.answer(ASK_START_TEXT)
    await state.set_state(ChangeStartText.GetText)

async def change_start_messages_get_text(message: Message, state: FSMContext):
    texts.start_message = message.text or ""
    await state.clear()
    await message.answer(START_TEXT_UPDATED_OK.format(texts.start_message))

# ---- Посты в канал ----

async def list_last_posts(message: Message):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return
    posts = sorted(adb.get_posts_from_start_of_month(), key=lambda x: x["date"], reverse=True)[:5]
    if not posts:
        await message.answer(NO_POSTS_TO_EDIT)
        return
    await message.answer(SELECT_POST_TO_EDIT, reply_markup=_posts_kb(posts))

async def start_post_edit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    message_id = int(callback.data.split("_")[-1])
    await state.set_state(EditPostState.waiting_for_new_text)
    await state.update_data(message_id=message_id)
    await callback.message.answer(ASK_NEW_POST_TEXT)
    await callback.answer()

async def apply_post_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    message_id = data["message_id"]
    try:
        await message.bot.edit_message_text(
            chat_id=cfg.CONTENT_CHANNEL_ID,
            message_id=message_id,
            text=message.text,
        )
        await message.answer(POST_EDITED_OK)
    except Exception as e:
        await message.answer(f"Ошибка при редактировании поста: {e}", parse_mode=None)
    await state.clear()

async def create_new_post_get_text(message: Message, state: FSMContext):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return
    await message.answer(ASK_NEW_POST_FOR_CHANNEL)
    await state.set_state(CreateNewPostState.GetText)

async def create_new_post(message: Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        msg = await message.bot.send_photo(cfg.CONTENT_CHANNEL_ID, file_id, caption=(message.caption or ""))
    elif message.video:
        file_id = message.video.file_id
        msg = await message.bot.send_video(cfg.CONTENT_CHANNEL_ID, file_id, caption=(message.caption or ""))
    else:
        msg = await message.bot.send_message(cfg.CONTENT_CHANNEL_ID, message.text or "")
    dt = datetime.fromisoformat(str(msg.date))
    adb.save_new_post(dt.strftime("%Y-%m-%d %H:%M:%S"), msg.message_id)
    await message.answer(POST_PUBLISHED_OK)
    await state.clear()

# ---- Рассылка ----

async def mailing_command(message: Message, state: FSMContext):
    if message.chat.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return
    await message.answer(ASK_MAILING_TEXT)
    await state.set_state(CreateMailing.GetText)

async def mailing_stop(callback: CallbackQuery, state: FSMContext):
    if callback.message.chat.id == cfg.ADMIN_ID:
        await callback.message.answer(ASK_MAILING_TEXT)
        await state.set_state(CreateMailing.GetText)
    await callback.answer()

async def mailing_accept(message: Message, state: FSMContext):
    text = message.text or (message.caption or "")
    await state.update_data(text_for_mailing=text)
    await message.answer(CONFIRM_MAILING_TEXT_TPL.format(text), reply_markup=BTN_MAILING)

async def go_mailing(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if callback.message.chat.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("text_for_mailing", "")
    users = adb.get_all_users() or []
    for user in users:
        try:
            await bot.send_message(chat_id=int(user[0]), text=text)
        except Exception as e:
            print(f"Ошибка рассылки пользователю {user[0]}: {e}")
    await callback.message.answer(MAILING_DONE)
    await state.clear()
    await callback.answer()

# ---- Оплата ----

async def payment(callback: CallbackQuery, bot: Bot):
    rate = callback.data
    tariff = rate.split("_")[1]
    title_map = {"1": "Тариф на 1 месяц", "2": "Тариф на 3 месяца", "3": "Тариф на 6 месяцев", "4": "Тариф на 12 месяцев"}
    month_map = {"1": 1, "2": 3, "3": 6, "4": 12}
    if rate not in Price or tariff not in title_map:
        await callback.message.answer(UNKNOWN_TARIFF)
        return
    prices = [LabeledPrice(label="Подписка", amount=Price[rate])]
    await bot.send_invoice(
        chat_id=callback.message.chat.id,
        title=title_map[tariff],
        description="Оплата подписки на контент",
        payload=str(month_map[tariff]),
        provider_token=cfg.PAYMENT_PROVIDER_TOKEN,
        currency="RUB",
        prices=prices,
        start_parameter="content-sub",
        need_email=True,
        send_email_to_provider=True,
        need_phone_number=False,
        send_phone_number_to_provider=False,
    )
    await callback.answer()

async def pre_checkout(pre_checkout_q, bot: Bot):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

async def successful_payment(message: Message, bot: Bot):
    payment_info = message.successful_payment
    months = int(payment_info.invoice_payload)
    amount_rub = payment_info.total_amount / 100
    await message.answer(
        SUCCESS_PAYMENT_TPL.format(amount=amount_rub, currency=payment_info.currency, months=months)
    )
    adb.add_sub_user(message.from_user.id, months)
    await create_invite(message, message.bot)
    await message.answer(POSTS_HEADER)
    await send_posts_of_month(message, message.bot)
    await notify_admin_about_new_sub(message.from_user.id, bot)

# ---- Вспомогательные ----

async def create_invite(message: Message, bot: Bot):
    try:
        invite_link: ChatInviteLink = await bot.create_chat_invite_link(
            chat_id=cfg.CONTENT_GROUP_ID,
            expire_date=None,
            member_limit=1,
            creates_join_request=False,
        )
        await message.answer(PERSONAL_INVITE_TPL.format(invite_link.invite_link), parse_mode=None)
    except Exception as e:
        await message.answer(INVITE_ERROR_TPL.format(e))

async def send_posts_of_month(message: Message, bot: Bot):
    posts = adb.get_posts_from_start_of_month()
    for post in posts:
        try:
            await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=post["channel_id"],
                message_id=post["message_id"],
            )
        except TelegramBadRequest as e:
            if "message to copy not found" in str(e):
                print(f"Пропускаю отсутствующий пост {post['message_id']}")
                continue
            else:
                raise
        except Exception as e:
            print(f"Ошибка копирования {post['message_id']}: {e}")

async def notify_admin_about_new_sub(user_id: int, bot: Bot):
    user_info = adb.check_user(user_id)
    if not user_info:
        return
    # user_info: (Rate, user_id, UserTag)
    text = f"<a href='https://t.me/{user_info[2]}'>Пользователь</a> оплатил подписку на: {user_info[0]} месяц(ев)."
    await bot.send_message(chat_id=cfg.ADMIN_GROUP_ID, text=text, parse_mode="HTML")

# ---- Плановые задачи (scheduler) ----

async def check_user_sub(bot: Bot):
    expired_users = adb.remove_expired_subscriptions()
    for user_id in expired_users:
        try:
            await bot.send_message(chat_id=int(user_id), text=SUB_EXPIRED_MSG, parse_mode="HTML")
        except Exception as e:
            print(f"Не удалось уведомить {user_id}: {e}")

async def notify_expiring_users(bot: Bot):
    for days in [10, 7, 3, 1]:
        users = adb.get_users_with_expiring_subscription(days_before=days)
        msg = adb.get_notification_message(days)
        if not msg or not users:
            continue
        for uid in users:
            try:
                await bot.send_message(chat_id=int(uid), text=msg, parse_mode="HTML")
            except Exception as e:
                print(f"Не удалось отправить {uid}: {e}")

async def check_sub_user_and_kick_for_group(bot: Bot):
    all_users = adb.get_all_users() or []
    from aiogram.enums.chat_member_status import ChatMemberStatus
    for user in all_users:
        user_id = int(user[0])
        end_sub = user[3]
        if not end_sub:
            continue
        try:
            end_dt = datetime.strptime(end_sub, "%Y-%m-%d").date()
        except Exception:
            continue
        from datetime import date
        if end_dt >= date.today():
            continue
        try:
            member = await bot.get_chat_member(chat_id=cfg.CONTENT_GROUP_ID, user_id=user_id)
        except Exception as e:
            print(f"get_chat_member {user_id}: {e}")
            continue
        if member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
            continue
        try:
            await bot.ban_chat_member(chat_id=cfg.CONTENT_GROUP_ID, user_id=user_id)
            await bot.unban_chat_member(chat_id=cfg.CONTENT_GROUP_ID, user_id=user_id)
        except Exception as e:
            print(f"remove {user_id}: {e}")

# =============================================================================
# РОУТЕР: регистрируем все обработчики в одном месте (как в примере)
# =============================================================================


def router(rt: Router):
    # Admin menu
    rt.message.register(admin_menu, Command("admin_menu"))

    # Change price
    rt.message.register(change_price, Command("change_price"))
    rt.callback_query.register(select_changed_price, F.data.startswith("SelectRate_"))
    rt.message.register(process_new_price, PriceStates.waiting_for_new_price, F.text)

    # Change texts
    rt.message.register(change_message_of_rates, Command("change_message_of_rates"))
    rt.message.register(change_text_of_rates_set_text, ChangeTextOfRates.GetText)  # без F.text — на случай вложений
    rt.message.register(change_start_messages_start, Command("change_start_message"))
    rt.message.register(change_start_messages_get_text, ChangeStartText.GetText, F.text)

    # Posts
    rt.message.register(list_last_posts, Command("edit_post"))
    rt.callback_query.register(start_post_edit, F.data.startswith("edit_post_"))
    rt.message.register(apply_post_edit, EditPostState.waiting_for_new_text, F.text)
    rt.message.register(create_new_post_get_text, Command("create_new_post"))
    rt.message.register(create_new_post, CreateNewPostState.GetText)  # принимаем текст/фото/видео

    # Mailing
    rt.message.register(mailing_command, Command("mailing"))
    rt.callback_query.register(mailing_stop, F.data == "stop_mailing")
    rt.message.register(mailing_accept, CreateMailing.GetText)  # без F.text — поддержка caption
    rt.callback_query.register(go_mailing, F.data == "go_mailing")

    # Payments
    rt.callback_query.register(payment, F.data.in_({"Rate_1", "Rate_2", "Rate_3", "Rate_4"}))
    rt.pre_checkout_query.register(pre_checkout)
    rt.message.register(successful_payment, F.successful_payment)
