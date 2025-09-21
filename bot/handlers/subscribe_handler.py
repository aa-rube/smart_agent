# smart_agent/bot/handlers/subscribe_handler.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)

import bot.utils.tokens as tk
import bot.utils.database as db
from bot.utils import youmoney

# ──────────────────────────────────────────────────────────────────────────────
# ТАРИФЫ И НАСТРОЙКИ
# ──────────────────────────────────────────────────────────────────────────────
# Правила: токены "за месяц" кратно месяцу тарифа. При желании скорректируй.
TARIFFS: Dict[str, Dict] = {
    "1m":  {"label": "1 месяц",  "months": 1,  "amount": "2500.00",  "tokens": 100},
    "3m":  {"label": "3 месяца", "months": 3,  "amount": "6500.00",  "tokens": 300},
    "6m":  {"label": "6 месяцев","months": 6,  "amount": "12500.00", "tokens": 600},
    "12m": {"label": "12 месяцев","months": 12,"amount": "24000.00", "tokens": 1200},
}

RATES_TEXT = (
    "Тут вы можете приобрести нашу подписку по тарифам:\n"
    "1 месяц / 2.500₽\n"
    "3 месяца / 6.500₽ (скидка 10🔥)\n"
    "6 месяцев / 12.500₽ (скидка 15🔥)\n"
    "12 месяцев / 24.000₽ (скидка 20🔥)\n"
)

PAY_TEXT = (
    "📦 Что даёт подписка:\n"
    " — Пакет генераций на выбранный срок (смотри условия тарифа)\n"
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


# ──────────────────────────────────────────────────────────────────────────────
# UI/HELPERS
# ──────────────────────────────────────────────────────────────────────────────

async def _edit_safe(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except Exception:
        try:
            await cb.message.edit_caption(caption=text, reply_markup=kb)
        except Exception:
            await cb.message.answer(text, reply_markup=kb)
    await cb.answer()


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
    tokens = plan["tokens"]

    description = f"Подписка на {plan['label']} ({tokens} генераций)"
    meta = {
        "user_id": str(user_id),
        "plan_code": code,
        "months": str(months),
        "tokens": str(tokens),
        "v": "1",
    }

    try:
        payment_url = youmoney.create_pay_ex(
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
    await _edit_safe(cb, text, kb_pay(payment_url))


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

        if not payment_id or not status:
            return 400, "missing payment_id/status"

        # интересует только успешное завершение
        if event not in ("payment.succeeded", "payment.waiting_for_capture"):
            return 200, f"skip event={event}"

        # если уже обработали — отвечаем 200 (важно для идемпотентности)
        user_id = int((metadata.get("user_id") or 0))
        if not user_id:
            return 400, "missing user_id in metadata"

        if _is_payment_processed(user_id, payment_id):
            return 200, "already processed"

        # разбор плана (из метаданных); фоллбэк — по сумме
        code = metadata.get("plan_code")
        months = int(metadata.get("months") or 0)
        tokens = int(metadata.get("tokens") or 0)

        if not code or code not in TARIFFS:
            # попытка сопоставить по сумме
            amount_val = str(obj.get("amount", {}).get("value") or "")
            for c, pl in TARIFFS.items():
                if amount_val == pl["amount"]:
                    code, months, tokens = c, pl["months"], pl["tokens"]
                    break

        if not code:
            # не смогли сопоставить — но деньги пришли; начислим дефолт (1м)
            code = "1m"
            months = months or TARIFFS["1m"]["months"]
            tokens = tokens or TARIFFS["1m"]["tokens"]

        # начисляем
        db.check_and_add_user(user_id)
        tk.add_tokens(user_id, tokens)
        db.set_variable(user_id, "have_sub", "1")

        paid_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        sub_until = _compute_sub_until(months)
        db.set_variable(user_id, "sub_paid_at", paid_at)
        db.set_variable(user_id, "sub_until", sub_until)

        _mark_payment_processed(user_id, payment_id)

        # Отправим пользователю уведомление
        try:
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"✅ Оплата прошла успешно!\n\n"
                    f"Тариф: *{TARIFFS.get(code, {}).get('label', code)}*\n"
                    f"Начислено: *{tokens}* генераций\n"
                    f"Подписка активна до: *{sub_until}*"
                )
            )
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
