# smart_agent/bot/handlers/admin.py
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, ChatInviteLink
)
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime

import bot.keyboards.inline as inline
import bot.text.texts as texts
import bot.utils.admin_db as adb
import bot.config as cfg

router = Router()

# ====== Прайс (в копейках) ======
Price = {
    'Rate_1': 250000,    # 2 500 ₽
    'Rate_2': 650000,    # 6 500 ₽
    'Rate_3': 1250000,   # 12 500 ₽
    'Rate_4': 2400000    # 24 000 ₽
}

# ====== FSM ======
class PriceStates(StatesGroup):
    waiting_for_new_price = State()

class ChangeStartText(StatesGroup):
    GetText = State()

class ChangeTextOfRates(StatesGroup):
    GetText = State()

class CreateNewPostState(StatesGroup):
    GetText = State()

class CreateMailing(StatesGroup):
    GetText = State()

class EditPostState(StatesGroup):
    waiting_for_new_text = State()
    message_id = State()

# =========================
# Публичные команды/кнопки
# =========================
@router.message(Command('ShowRates'))
@router.callback_query(F.data == 'ShowRates')
async def show_rates(evt: Message | CallbackQuery):
    msg = evt if isinstance(evt, Message) else evt.message
    await msg.answer(texts.info_rates_message, reply_markup=inline.select_rates)
    if isinstance(evt, CallbackQuery):
        await evt.answer()

@router.callback_query(F.data.startswith("my_profile"))
async def my_profile(callback: CallbackQuery):
    info = adb.get_my_info(callback.message.chat.id)
    if info:
        temp = (
            f'Подписка: {"YES" if info[0] else "NO"}\n'
            f'Дата оплаты подписки: {info[1] or "-"}\n'
            f'Дата окончания подписки: {info[2] or "-"}'
        )
        await callback.message.answer(temp)
    await callback.answer()

# =========================
#      ADMIN MENU
# =========================
@router.message(Command('/admin'))
async def admin_menu(message: Message):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer("У вас нет доступа к админ панели.")
        return
    await message.answer(
        "--==AdminMenu==--\n"
        "<b>Список админ-команд</b>\n\n"
        "/ChangeStartMessages - <b>Изменить текст приветственного сообщения</b>\n"
        "/CreateNewPost - <b>Опубликовать новый пост</b>\n"
        "/EditPost - <b>Редактировать последние 5 постов</b>\n"
        "/GetAllUsers - <b>Получить данные всех пользователей</b>\n"
        "/ChangeMessageOfRates - <b>Изменить текст после 'Подписаться на контент'</b>\n"
        "/ChangePrice - <b>Изменить цены тарифов</b>\n\n"
        "Напоминания:\n"
        "/show_notifications — показать\n"
        "/set_notice (days) (message) — установить\n"
        "/delete_notification (days) — удалить\n",
        parse_mode="HTML"
    )

# ==== Изменение цен ====
@router.message(Command('ChangePrice'))
async def change_price(message: Message):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer("У вас нет доступа к админ панели.")
        return
    await message.answer("Выберите тариф, который хотите поменять", reply_markup=inline.change_price_btn)

@router.callback_query(F.data.startswith('SelectRate_'))
async def select_changed_price(callback: CallbackQuery, state: FSMContext):
    selected_rate = callback.data.split('_', 1)[1]
    display_price = Price[f'Rate_{selected_rate}'] // 100
    await state.update_data(selected_rate=selected_rate)
    await callback.message.answer(
        f"Введите новую цену для тарифа №{selected_rate} (текущая: {display_price} руб.)",
        parse_mode=None
    )
    await state.set_state(PriceStates.waiting_for_new_price)
    await callback.message.delete()
    await callback.answer()

@router.message(PriceStates.waiting_for_new_price)
async def process_new_price(message: Message, state: FSMContext):
    text = (message.text or "").replace(',', '.').replace(' ', '')
    try:
        price_float = float(text)
    except ValueError:
        await message.answer("Введите корректное число, например: 1500 или 1500.00")
        return
    new_price = int(round(price_float * 100))
    data = await state.get_data()
    sel = data.get('selected_rate')
    if not sel:
        await message.answer("Ошибка состояния, попробуйте снова /ChangePrice")
        await state.clear()
        return
    Price[f'Rate_{sel}'] = new_price
    await message.answer(f"Цена тарифа Rate_{sel} обновлена до {new_price // 100} руб.")
    await state.clear()

# ==== Изменение стартовых текстов ====
@router.message(Command("ChangeMessageOfRates"))
async def change_message_of_rates(message: Message, state: FSMContext):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer("У вас нет доступа к админ панеле.")
        return
    await message.answer("Напишите текст для сообщения после кнопки 'Подписаться на контент'")
    await state.set_state(ChangeTextOfRates.GetText)

@router.message(ChangeTextOfRates.GetText)
async def change_text_of_rates_set_text(message: Message, state: FSMContext):
    texts.info_rates_message = message.text or ""
    await state.clear()
    await message.answer("Текст обновлён.\n\n" + texts.info_rates_message)

@router.message(Command('ChangeStartMessages'))
async def change_start_messages_start(message: Message, state: FSMContext):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer("У вас нет доступа к админ панеле.")
        return
    await message.answer("Напишите текст для /start")
    await state.set_state(ChangeStartText.GetText)

@router.message(ChangeStartText.GetText)
async def change_start_messages_get_text(message: Message, state: FSMContext):
    texts.start_message = message.text or ""
    await state.clear()
    await message.answer("Готово! Новый текст:\n\n" + texts.start_message)

# ==== Посты в канал ====
def _posts_kb(posts: list):
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    rows = []
    for post in posts:
        btn = InlineKeyboardButton(
            text=f"Пост от {post['date'].strftime('%d.%m.%Y')}",
            callback_data=f"edit_post_{post['message_id']}"
        )
        rows.append([btn])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.message(Command("EditPost"))
async def list_last_posts(message: Message):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer("У вас нет доступа к админ панеле.")
        return
    posts = sorted(adb.get_posts_from_start_of_month(), key=lambda x: x["date"], reverse=True)[:5]
    if not posts:
        await message.answer("Нет постов для редактирования.")
        return
    await message.answer("Выберите пост для редактирования:", reply_markup=_posts_kb(posts))

@router.callback_query(F.data.startswith("edit_post_"))
async def start_post_edit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    message_id = int(callback.data.split("_")[-1])
    await state.set_state(EditPostState.waiting_for_new_text)
    await state.update_data(message_id=message_id)
    await callback.message.answer("Введите новый текст для поста.")
    await callback.answer()

@router.message(EditPostState.waiting_for_new_text)
async def apply_post_edit(message: Message, state: FSMContext):
    data = await state.get_data()
    message_id = data["message_id"]
    try:
        await message.bot.edit_message_text(
            chat_id=cfg.CONTENT_CHANNEL_ID,
            message_id=message_id,
            text=message.text
        )
        await message.answer("Пост отредактирован.")
    except Exception as e:
        await message.answer(f"Ошибка при редактировании поста: {e}", parse_mode=None)
    await state.clear()

@router.message(Command('CreateNewPost'))
async def create_new_post_get_text(message: Message, state: FSMContext):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer("У вас нет доступа к админ панеле.")
        return
    await message.answer("Отправьте пост для публикации в канал")
    await state.set_state(CreateNewPostState.GetText)

@router.message(CreateNewPostState.GetText)
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
    await message.answer("Пост опубликован.")
    await state.clear()

# ==== Рассылка ====
@router.message(Command('mailing'))
async def mailing_command(message: Message, state: FSMContext):
    if message.chat.id != cfg.ADMIN_ID:
        await message.answer("У вас нет доступа к админ панели.")
        return
    await message.answer("Отправьте сообщение для рассылки")
    await state.set_state(CreateMailing.GetText)

@router.callback_query(F.data == 'stop_mailing')
async def mailing_stop(callback: CallbackQuery, state: FSMContext):
    if callback.message.chat.id == cfg.ADMIN_ID:
        await callback.message.answer("Отправьте сообщение для рассылки")
        await state.set_state(CreateMailing.GetText)
    await callback.answer()

@router.message(CreateMailing.GetText)
async def mailing_accept(message: Message, state: FSMContext):
    text = message.text or (message.caption or "")
    await state.update_data(TextForMiling=text)
    await message.answer(f'Начать рассылку сообщения:\n\n{text}', reply_markup=inline.btn_mailing)

@router.callback_query(F.data == 'go_mailing')
async def go_mailing(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if callback.message.chat.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("TextForMiling", "")
    users = adb.get_all_users() or []
    for user in users:
        try:
            await bot.send_message(chat_id=int(user[0]), text=text)
        except Exception as e:
            print(f"Ошибка рассылки пользователю {user[0]}: {e}")
    await callback.message.answer("Рассылка завершена!")
    await state.clear()
    await callback.answer()

# ==== Оплата ====
@router.callback_query(F.data.in_({'Rate_1', 'Rate_2', 'Rate_3', 'Rate_4'}))
async def payment(callback: CallbackQuery, bot: Bot):
    rate = callback.data
    tariff = rate.split('_')[1]
    title_map = {'1': 'Тариф на 1 месяц', '2': 'Тариф на 3 месяца', '3': 'Тариф на 6 месяцев', '4': 'Тариф на 12 месяцев'}
    month_map = {'1': 1, '2': 3, '3': 6, '4': 12}
    if rate not in Price or tariff not in title_map:
        await callback.message.answer("Неизвестный тариф")
        return
    prices = [LabeledPrice(label='Подписка', amount=Price[rate])]
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

@router.pre_checkout_query()
async def pre_checkout(pre_checkout_q, bot: Bot):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)

@router.message(F.successful_payment)
async def successful_payment(message: Message, bot: Bot):
    payment_info = message.successful_payment
    months = int(payment_info.invoice_payload)
    amount_rub = payment_info.total_amount / 100
    await message.answer(
        f"Оплата прошла успешно!\n"
        f"Сумма: {amount_rub:.2f} {payment_info.currency}\n"
        f"Тариф: {months} месяц(ев)"
    )
    adb.add_sub_user(message.from_user.id, months)
    await create_invite(message, message.bot)
    await message.answer("Ниже посты этого месяца ↓")
    await send_posts_of_month(message, message.bot)
    await notify_admin_about_new_sub(message.from_user.id, bot)

# ==== Вспомогательные ====
async def create_invite(message: Message, bot: Bot):
    try:
        invite_link: ChatInviteLink = await bot.create_chat_invite_link(
            chat_id=cfg.CONTENT_GROUP_ID,
            expire_date=None,
            member_limit=1,
            creates_join_request=False
        )
        await message.answer(f"Ваша персональная ссылка для вступления:\n{invite_link.invite_link}", parse_mode=None)
    except Exception as e:
        await message.answer(f"Ошибка при создании ссылки: {e}")

async def send_posts_of_month(message: Message, bot: Bot):
    posts = adb.get_posts_from_start_of_month()
    for post in posts:
        try:
            await bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=post["channel_id"],
                message_id=post["message_id"]
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
    # user_info: (Rate,user_id,UserTag)
    text = f"<a href='https://t.me/{user_info[2]}'>Пользователь</a> оплатил подписку на: {user_info[0]} месяц(ев)."
    await bot.send_message(chat_id=cfg.ADMIN_GROUP_ID, text=text, parse_mode="HTML")

# ==== Плановые задачи (scheduler) ====
async def check_user_sub(bot: Bot):
    expired_users = adb.remove_expired_subscriptions()
    for user_id in expired_users:
        try:
            await bot.send_message(
                chat_id=int(user_id),
                text="Ваша подписка истекла. Чтобы восстановить доступ, оформите новую подписку.",
                parse_mode="HTML"
            )
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
