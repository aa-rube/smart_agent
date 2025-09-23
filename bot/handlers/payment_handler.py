# # smart_agent/bot/handlers/payment_handler.py
# #Всегда пиши код без «поддержки старых версий». Если они есть в еодк - удаляй
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)

from bot.config import get_file_path
import bot.utils.database as db
from bot.utils import youmoney


# ──────────────────────────────────────────────────────────────────────────────
# ТАРИФЫ И НАСТРОЙКИ
# ──────────────────────────────────────────────────────────────────────────────

TARIFFS: Dict[str, Dict] = {
    # Рекуррентной делаем 1m: пробный платёж 1 ₽ на 72 часа, далее автосписание
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


def kb_pay_with_consent(*, consent: bool, pay_url: Optional[str]) -> InlineKeyboardMarkup:
    """
    Экран оплаты:
      - чекбокс «Я ознакомлен и согласен»
      - если чекбокс нажат — показываем кнопку с URL
      - если не нажат — показываем кнопку-заглушку, которая просит поставить галочку
    """
    check = "✅ Я ознакомлен и согласен" if consent else "⬜️ Я ознакомлен и согласен"
    rows = [
        [InlineKeyboardButton(text=check, callback_data="tos:toggle")],
    ]
    if consent and pay_url:
        rows.append([InlineKeyboardButton(text="💳 Оплатить", url=pay_url)])
    # else:
    #     rows.append([InlineKeyboardButton(text="💳 Оплатить", callback_data="tos:need")])

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
    """Грубая идемпотентность на базе settings.db (если используется где-то ещё)."""
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
    ВАЖНО: ссылку создаём ОДИН раз, сохраняем в переменной и далее только показываем/прячем.
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


    amount = plan["amount"]
    months = plan["months"]

    description = f"Подписка на {plan['label']}"
    meta = {
        "user_id": str(user_id),
        "plan_code": code,
        "months": str(months),
        "v": "1",
    }

    # Создаём ссылку на оплату (один раз)
    pay_url: Optional[str] = None
    if plan.get("recurring"):
        first_amount = plan.get("trial_amount", "1.00")
        meta.update({
            "phase": "trial",
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

    # Сохраняем URL, чтобы не пересоздавать при кликах чекбокса
    try:
        db.set_variable(user_id, "yk:last_pay_url", pay_url or "")
    except Exception:
        logging.exception("Failed to store last pay url for user %s", user_id)

    # Читаем состояние согласия (только влияет на показ URL)
    consent_raw = db.get_variable(user_id, "tos:accepted_at")
    consent = bool(consent_raw)

    msg_id = await _edit_safe(cb, text, kb_pay_with_consent(consent=consent, pay_url=pay_url if consent else None))

    # Сохраняем id сообщения с кнопкой, чтобы удалить после успешной оплаты
    try:
        db.set_variable(user_id, "yk:last_pay_msg_id", str(msg_id or ""))
    except Exception:
        logging.exception("Failed to store last pay message id for user %s", user_id)


# ──────────────────────────────────────────────────────────────────────────────
# Доп. хендлеры чек-бокса согласия
# ──────────────────────────────────────────────────────────────────────────────

async def toggle_tos(cb: CallbackQuery) -> None:
    """
    Переключатель чекбокса.
    Никаких пересозданий платежа: читаем сохранённый URL и просто
    показываем/прячем кнопку с ссылкой.
    """
    user_id = cb.from_user.id
    cur = db.get_variable(user_id, "tos:accepted_at")
    if cur:
        db.set_variable(user_id, "tos:accepted_at", "")  # снимаем галочку
    else:
        db.set_variable(user_id, "tos:accepted_at", datetime.utcnow().isoformat(timespec="seconds") + "Z")

    consent = not bool(cur)
    pay_url = db.get_variable(user_id, "yk:last_pay_url") or None

    try:
        await cb.message.edit_reply_markup(
            reply_markup=kb_pay_with_consent(consent=consent, pay_url=pay_url if consent else None)
        )
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

        # Неуспешные кейсы
        if (event in ("payment.canceled", "payment.expired") or status in ("canceled", "expired")):
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

        user_id = int(metadata.get("user_id") or 0)
        if not user_id:
            return 400, "missing user_id in metadata"

        # Аудит + идемпотентность по payment_id
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

        # Разбор плана (из метаданных); фоллбэк — по сумме
        code = metadata.get("plan_code")
        months = int(metadata.get("months") or 0)

        if not code or code not in TARIFFS:
            amount_val = str(obj.get("amount", {}).get("value") or "")
            for c, pl in TARIFFS.items():
                if amount_val == pl["amount"]:
                    code, months = c, pl["months"]
                    break

        if not code:
            code = "1m"
            months = months or TARIFFS["1m"]["months"]

        is_recurring = str(metadata.get("is_recurring") or "0") == "1"
        phase = str(metadata.get("phase") or "").strip()  # "trial" | "renewal" | ""

        db.check_and_add_user(user_id)
        paid_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"

        if is_recurring:
            # Сохраняем способ оплаты и создаём/обновляем запись подписки
            if pm_id:
                db.set_variable(user_id, "yk:payment_method_id", pm_id)
            trial_hours = int(str(metadata.get("trial_hours") or "72"))
            plan_amount = str(metadata.get("plan_amount") or TARIFFS.get(code, {}).get("amount", "2490.00"))
            interval_m = int(TARIFFS.get(code, {}).get("months", 1))

            if phase == "trial":
                # ⚑ Первый платёж на 1 ₽: открываем демо-период и планируем автосписание через trial_hours
                trial_until_iso = db.set_trial(user_id, hours=trial_hours)
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

                # Открываем доступ на период trial
                db.set_variable(user_id, "have_sub", "1")
                db.set_variable(user_id, "sub_paid_at", paid_at)
                db.set_variable(user_id, "sub_until", trial_until_iso[:10])

                sub_until = trial_until_iso[:10]
            else:
                # ⚑ Рекуррентное списание (полная сумма после trial)
                sub_until = _compute_sub_until(interval_m)
                db.set_variable(user_id, "have_sub", "1")
                db.set_variable(user_id, "sub_paid_at", paid_at)
                db.set_variable(user_id, "sub_until", sub_until)
                # переносим дату следующего списания только ПОСЛЕ успешного списания
                try:
                    from dateutil.relativedelta import relativedelta
                    next_at = datetime.utcnow() + relativedelta(months=+interval_m)
                except Exception:
                    next_at = datetime.utcnow() + timedelta(days=30 * interval_m)
                try:
                    db.subscription_mark_charged(metadata.get("subscription_id"), next_charge_at=next_at)
                except Exception:
                    # на всякий, если id подписки не передан — апдейтим по user_id
                    try:
                        db.subscription_mark_charged_for_user(user_id=user_id, next_charge_at=next_at)
                    except Exception:
                        logging.exception("Failed to bump next_charge_at after renewal for user %s", user_id)
        else:
            # Разовый платёж
            db.set_variable(user_id, "have_sub", "1")
            sub_until = _compute_sub_until(months)
            db.set_variable(user_id, "sub_paid_at", paid_at)
            db.set_variable(user_id, "sub_until", sub_until)

        # Идемпотентность
        try:
            db.payment_log_mark_processed(payment_id)
        except Exception:
            logging.exception("payment_log_mark_processed failed for %s", payment_id)

        # Удаляем сообщение с кнопкой оплаты (если оно было)
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

        # Уведомление пользователю
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ Оплата прошла успешно!\n\n"
                    f"Тариф: *{TARIFFS.get(code, {}).get('label', code)}*\n"
                    f"Подписка активна до: *{sub_until}*"
                )
            )
            # ленивый импорт, чтобы избежать циклических зависимостей
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
    # Выбор тарифа
    rt.callback_query.register(choose_rate, F.data.startswith("sub:choose:"))
    # Чекбокс согласия и блокирующая «Оплатить» без согласия
    rt.callback_query.register(toggle_tos, F.data == "tos:toggle")
    rt.callback_query.register(need_tos,   F.data == "tos:need")
