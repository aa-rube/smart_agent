# smart_agent/bot/handlers/payment_handler.py
#Всегда пиши код без «поддержки старых версий». Если они есть в еодк - удаляй.

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from bot.config import get_file_path

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)

import bot.utils.database as db
from bot.utils import youmoney

# ──────────────────────────────────────────────────────────────────────────────
# ТАРИФЫ И НАСТРОЙКИ
# ──────────────────────────────────────────────────────────────────────────────
TARIFFS: Dict[str, Dict] = {
    # Для примера делаем рекуррентной только 1m (остальные — разовые)
    "1m":  {"label": "1 месяц",   "months": 1,  "amount": "2490.00", "recurring": True, "trial_amount": "1.00", "trial_hours": 72},
    "3m":  {"label": "3 месяца",  "months": 3,  "amount": "6590.00"},
    "6m":  {"label": "6 месяцев", "months": 6,  "amount": "11390.00"},
    "12m": {"label": "12 месяцев","months": 12, "amount": "19900.00"},
}

RATES_TEXT = (
    "Тут вы можете оформить подписку на доступ:\n"
    "1 месяц / 2.490₽\n"
    "3 месяца / 6.590₽\n"
    "6 месяцев / 11.390₽ 🔥🔥\n"
    "12 месяцев / 19.990₽ 🔥🔥🔥\n"
)

PAY_TEXT = (
    "📦 Что даёт подписка:\n"
    " — Доступ ко всем инструментам на выбранный срок\n"
    " — Доступ ко всем инструментам\n"
    "Нажмите «Оплатить» для оформления."
)

# ──────────────────────────────────────────────────────────────────────────────
# КЛАВИАТУРЫ
# ──────────────────────────────────────────────────────────────────────────────

def kb_rates() -> InlineKeyboardMarkup:
    """Клавиатура выбора тарифа."""
    rows = [
        [
            InlineKeyboardButton(text="1 месяц",  callback_data="sub:choose:1m"),
            InlineKeyboardButton(text="3 месяца", callback_data="sub:choose:3m"),
            InlineKeyboardButton(text="6 месяцев", callback_data="sub:choose:6m"),
        ],
        [InlineKeyboardButton(text="12 месяцев", callback_data="sub:choose:12m")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_pay(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=url)],
            [InlineKeyboardButton(text="⬅️ Выбрать другой тариф", callback_data="show_rates")],
        ]
    )

# Клавиатура оплаты с чекбоксом согласия.
# Если consent=False — кнопка «Оплатить» идёт КОЛБЭКОМ без URL.
# Если consent=True — появляется URL для оплаты.
def kb_pay_with_consent(*, consent: bool, pay_url: Optional[str]) -> InlineKeyboardMarkup:
    check = "✅ Я ознакомлен и согласен" if consent else "⬜️ Я ознакомлен и согласен"
    rows = [
        [InlineKeyboardButton(text=check, callback_data="tos:toggle")],
    ]
    if consent and pay_url:
        rows.append([InlineKeyboardButton(text="💳 Оплатить", url=pay_url)])
    else:
        rows.append([InlineKeyboardButton(text="💳 Оплатить", callback_data="tos:need")])
    rows.append([InlineKeyboardButton(text="⬅️ Выбрать другой тариф", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────────────────────────────────────────────────────────────────────────
# UI/HELPERS
# ──────────────────────────────────────────────────────────────────────────────

async def _edit_safe(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> Optional[int]:
    """
    Редактируем сообщение (или отвечаем новым) и возвращаем message_id,
    чтобы потом можно было удалить «кнопку оплаты».
    """
    msg_id: Optional[int] = None
    try:
        m = await cb.message.edit_text(text, reply_markup=kb)
        msg_id = m.message_id if isinstance(m, Message) else cb.message.message_id
    except Exception:
        try:
            m = await cb.message.edit_caption(caption=text, reply_markup=kb)
            if isinstance(m, Message):
                msg_id = m.message_id
        except Exception:
            m = await cb.message.answer(text, reply_markup=kb)
            if isinstance(m, Message):
                msg_id = m.message_id
    await cb.answer()
    return msg_id


def _plan_by_code(code: str) -> Optional[Dict]:
    return TARIFFS.get(code)


def _compute_sub_until(months: int) -> str:
    """
    Возвращает дату конца подписки ISO (YYYY-MM-DD).
    Если нет dateutil.relativedelta, используем 30д * мес.
    """
    try:
        from dateutil.relativedelta import relativedelta
        until = datetime.utcnow() + relativedelta(months=+months)
    except Exception:
        until = datetime.utcnow() + timedelta(days=30 * months)
    return until.date().isoformat()


def _is_payment_processed(user_id: int, payment_id: str) -> bool:
    """Грубая идемпотентность на базе settings.db."""
    key = f"yk:paid:{payment_id}"
    return bool(db.get_variable(user_id, key))


def _mark_payment_processed(user_id: int, payment_id: str) -> None:
    key = f"yk:paid:{payment_id}"
    db.set_variable(user_id, key, "1")


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC HANDLERS: Показ тарифов → Выбор тарифа → Ссылка на оплату
# ──────────────────────────────────────────────────────────────────────────────

async def show_rates(evt: Message | CallbackQuery) -> None:
    """Единая точка входа «Показать тарифы» (сообщение или колбэк)."""
    text = RATES_TEXT
    if isinstance(evt, CallbackQuery):
        await _edit_safe(evt, text, kb_rates())
    else:
        await evt.answer(text, reply_markup=kb_rates())


async def choose_rate(cb: CallbackQuery) -> None:
    """
    sub:choose:<code> → создаём платёж и показываем кнопку «Оплатить».
    Метаданные платежа содержат план, месяцы и токены — это используем в вебхуке.
    """
    user_id = cb.from_user.id
    try:
        _, _, code = cb.data.split(":", 2)  # sub:choose:<code>
    except Exception:
        await _edit_safe(cb, "Не удалось определить тариф. Попробуйте ещё раз.", kb_rates())
        return

    plan = _plan_by_code(code)
    if not plan:
        await _edit_safe(cb, "Такого тарифа нет. Выберите из списка.", kb_rates())
        return

    # Создаём ссылку на оплату через централизованный youmoney.create_pay_ex
    amount = plan["amount"]
    months = plan["months"]

    description = f"Подписка на {plan['label']}"
    meta = {
        "user_id": str(user_id),
        "plan_code": code,
        "months": str(months),
        "v": "1",
    }

    # Рекуррентная подписка: первый платёж 1 ₽ c сохранением карты
    pay_url: Optional[str] = None
    if plan.get("recurring"):
        first_amount = plan.get("trial_amount", "1.00")
        meta.update({
            "is_recurring": "1",
            "trial_hours": str(plan.get("trial_hours", 72)),
            "plan_amount": amount,
        })
        try:
            pay_url = youmoney.create_pay_ex(
                user_id=user_id,
                amount_rub=first_amount,
                description=f"{description} (пробный период)",
                metadata=meta,
                save_payment_method=True,
            )
        except Exception as e:
            logging.exception("Failed to create trial payment: %s", e)
            await _edit_safe(cb, "Не удалось создать платёж. Попробуйте позже.", kb_rates())
            return
    else:
        # Разовый платёж
        try:
            pay_url = youmoney.create_pay_ex(
                user_id=user_id,
                amount_rub=amount,
                description=description,
                metadata=meta,
            )
        except Exception as e:
            logging.exception("Failed to create YooKassa payment: %s", e)
            await _edit_safe(cb, "Не удалось создать платёж. Попробуйте позже.", kb_rates())
            return

    text = f"{description}\n\n{PAY_TEXT}"

    # Читаем согласие
    consent_raw = db.get_variable(user_id, "tos:accepted_at")
    consent = bool(consent_raw)
    msg_id = await _edit_safe(cb, text, kb_pay_with_consent(consent=consent, pay_url=pay_url))
    # сохраним id сообщения с кнопкой, чтобы удалить после успешной оплаты
    try:
        db.set_variable(user_id, "yk:last_pay_msg_id", str(msg_id or ""))
    except Exception:
        logging.exception("Failed to store last pay message id for user %s", user_id)


# ──────────────────────────────────────────────────────────────────────────────
# Доп. хендлеры чек-бокса согласия
# ──────────────────────────────────────────────────────────────────────────────
async def toggle_tos(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    cur = db.get_variable(user_id, "tos:accepted_at")
    if cur:
        db.set_variable(user_id, "tos:accepted_at", "")  # снимаем
    else:
        db.set_variable(user_id, "tos:accepted_at", datetime.utcnow().isoformat(timespec="seconds") + "Z")

    # Перерисуем клавиатуру, если это экран оплаты
    # Переиспользуем последний URL, если он был сохранён в сообщении (мы его не храним отдельно),
    # поэтому просто заново построим кнопки: если consent=false — URL не нужен.
    consent = not bool(cur)
    # Текст оставляем как есть, только заменяем клавиатуру:
    try:
        await cb.message.edit_reply_markup(reply_markup=kb_pay_with_consent(consent=consent, pay_url=None))
    except Exception:
        pass
    await cb.answer()


async def need_tos(cb: CallbackQuery) -> None:
    await cb.answer("Поставьте отметку «Я ознакомлен и согласен», чтобы продолжить.", show_alert=True)


# ──────────────────────────────────────────────────────────────────────────────
# WEBHOOK: централизованный обработчик успешных платежей
# ──────────────────────────────────────────────────────────────────────────────

async def process_yookassa_webhook(bot: Bot, payload: Dict) -> Tuple[int, str]:
    """
    Центральная обработка вебхука YooKassa.
    Возвращает (http_status, message_for_log).
    """
    try:
        event = payload.get("event")
        obj = payload.get("object") or {}
        payment_id = obj.get("id")
        status = obj.get("status")
        metadata = obj.get("metadata") or {}
        payment_method = obj.get("payment_method") or {}
        pm_id = payment_method.get("id")

        if not payment_id or not status:
            return 400, "missing payment_id/status"

        # Неуспешные завершения/отмена — уведомляем пользователя картинкой и текстом
        if (event in ("payment.canceled", "payment.expired")
                or status in ("canceled", "expired")):
            try:
                user_id_raw = (payload.get("object") or {}).get("metadata", {}).get("user_id")
                user_id_fail = int(user_id_raw) if user_id_raw is not None else None
            except Exception:
                user_id_fail = None

            if user_id_fail:
                try:
                    cover_path = get_file_path("data/img/bot/no_pay.png")
                    photo = FSInputFile(cover_path)
                    caption = (
                        "❌ *Оплата не прошла*\n\n"
                        "Платёж был отменён или не завершён.\n"
                        "Если списания не было — вы можете попробовать оплатить снова из раздела тарифов."
                    )
                    await bot.send_photo(chat_id=user_id_fail, photo=photo, caption=caption, parse_mode="Markdown")
                except Exception as e:
                    logging.warning("Failed to send fail payment notice to %s: %s", user_id_fail, e)
            return 200, f"fail event={event} status={status}"

        # Интересуют только успешные кейсы (или ожидание подтверждения/capture)
        if event not in ("payment.succeeded", "payment.waiting_for_capture"):
            return 200, f"skip event={event}"

        # если уже обработали — отвечаем 200 (важно для идемпотентности)
        user_id = int((metadata.get("user_id") or 0))
        if not user_id:
            return 400, "missing user_id in metadata"

        # --- АУДИТ И ИДЕМПОТЕНТНОСТЬ ЧЕРЕЗ ЛОГ ПЛАТЕЖЕЙ ---
        try:
            db.payment_log_upsert(
                payment_id=payment_id,
                user_id=user_id,
                amount_value=str(obj.get("amount", {}).get("value") or ""),
                amount_currency=str(obj.get("amount", {}).get("currency") or "RUB"),
                event=str(event or ""),
                status=str(status or ""),
                metadata=metadata,
                raw_payload=payload,
            )
            if db.payment_log_is_processed(payment_id):
                return 200, "already processed"
        except Exception:
            logging.exception("payment_log_upsert failed for %s", payment_id)

        # разбор плана (из метаданных); фоллбэк — по сумме
        code = metadata.get("plan_code")
        months = int(metadata.get("months") or 0)

        if not code or code not in TARIFFS:
            # попытка сопоставить по сумме
            amount_val = str(obj.get("amount", {}).get("value") or "")
            for c, pl in TARIFFS.items():
                if amount_val == pl["amount"]:
                    code, months = c, pl["months"]
                    break

        if not code:
            # не смогли сопоставить — но деньги пришли; начислим дефолт (1м)
            code = "1m"
            months = months or TARIFFS["1m"]["months"]

        # подписка / разовый платёж
        is_recurring = str(metadata.get("is_recurring") or "0") == "1"

        db.check_and_add_user(user_id)
        paid_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

        if is_recurring:
            # Сохраняем способ оплаты и создаём/обновляем запись подписки
            if pm_id:
                db.set_variable(user_id, "yk:payment_method_id", pm_id)
            trial_hours = int(str(metadata.get("trial_hours") or "72"))
            plan_amount = str(metadata.get("plan_amount") or TARIFFS.get(code, {}).get("amount", "2490.00"))
            interval_m = int(TARIFFS.get(code, {}).get("months", 1))
            # trial до:
            trial_until_iso = db.set_trial(user_id, hours=trial_hours)

            # создаём/обновляем сущность подписки
            db.subscription_upsert(
                user_id=user_id,
                plan_code=code,
                interval_months=interval_m,
                amount_value=plan_amount,
                amount_currency=str(obj.get("amount", {}).get("currency") or "RUB"),
                payment_method_id=pm_id or db.get_variable(user_id, "yk:payment_method_id"),
                next_charge_at=datetime.utcnow() + timedelta(hours=trial_hours),
                status="active",
            )

            # права доступа сразу открываем на trial
            db.set_variable(user_id, "have_sub", "1")
            db.set_variable(user_id, "sub_paid_at", paid_at)
            db.set_variable(user_id, "sub_until", trial_until_iso[:10])  # на период trial

            sub_until = trial_until_iso[:10]
        else:
            # разовый платёж — как раньше
            db.set_variable(user_id, "have_sub", "1")
            sub_until = _compute_sub_until(months)
            db.set_variable(user_id, "sub_paid_at", paid_at)
            db.set_variable(user_id, "sub_until", sub_until)

        # пометим платёж обработанным (идемпотентность)
        try:
            db.payment_log_mark_processed(payment_id)
        except Exception:
            logging.exception("payment_log_mark_processed failed for %s", payment_id)

        # пытаемся удалить предыдущее сообщение с кнопкой
        try:
            msg_id_raw = db.get_variable(user_id, "yk:last_pay_msg_id")
            if msg_id_raw:
                msg_id_int = int(msg_id_raw)
                try:
                    await bot.delete_message(chat_id=user_id, message_id=msg_id_int)
                except Exception as e:
                    logging.warning("delete_message failed for user %s, msg %s: %s", user_id, msg_id_int, e)
                finally:
                    db.set_variable(user_id, "yk:last_pay_msg_id", "")
        except Exception:
            logging.exception("Failed to delete last pay message for user %s", user_id)

        # Отправим пользователю уведомление
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ Оплата прошла успешно!\n\n"
                    f"Тариф: *{TARIFFS.get(code, {}).get('label', code)}*\n"
                    f"Подписка активна до: *{sub_until}*"
                )
            )
            # Показываем главное меню после оплаты (ленивый импорт, чтобы избежать циклического импорта)
            try:
                from bot.handlers.handler_manager import send_menu_with_logo as _send_menu_with_logo
                await _send_menu_with_logo(bot, user_id)
            except Exception as e:
                logging.warning("Failed to send main menu after payment for user %s: %s", user_id, e)
        except Exception as e:
            logging.warning("Failed to notify user %s after payment: %s", user_id, e)

        return 200, "ok"

    except Exception as e:
        logging.exception("Webhook processing error: %s", e)
        return 500, f"error: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# ROUTER
# ──────────────────────────────────────────────────────────────────────────────

def router(rt: Router) -> None:
    # Показ тарифов
    rt.callback_query.register(show_rates, F.data == "show_rates")
    # Выбор тарифа (новая схема)
    rt.callback_query.register(choose_rate, F.data.startswith("sub:choose:"))

    # Back-compat: если в проекте где-то остались старые callback'и
    # Rate_1 / Rate_2 / Rate_3 / Rate_4 → маппим на 1m/3m/6m/12m
    async def legacy_choose(cb: CallbackQuery) -> None:
        m = {"Rate_1": "1m", "Rate_2": "3m", "Rate_3": "6m", "Rate_4": "12m"}
        code = m.get(cb.data)
        if code:
            cb.data = f"sub:choose:{code}"
            await choose_rate(cb)
        else:
            await _edit_safe(cb, "Тариф не распознан.", kb_rates())

    rt.callback_query.register(legacy_choose, F.data.in_({"Rate_1", "Rate_2", "Rate_3", "Rate_4"}))

    # Чекбокс согласия и блокирующая «Оплатить» без согласия
    rt.callback_query.register(toggle_tos, F.data == "tos:toggle")
    rt.callback_query.register(need_tos,   F.data == "tos:need")
