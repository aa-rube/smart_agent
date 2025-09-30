# # smart_agent/bot/handlers/payment_handler.py
# #Всегда пиши код без «поддержки старых версий». Если они есть - удаляй
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
from aiogram.filters import Command


# ──────────────────────────────────────────────────────────────────────────────
# ТАРИФЫ И НАСТРОЙКИ
# ──────────────────────────────────────────────────────────────────────────────

TARIFFS: Dict[str, Dict] = {
    # Все планы рекуррентные: 1 ₽ на 72 часа, далее автосписание по периоду плана
    "1m":  {"label": "1 месяц",   "months": 1,  "amount": "2490.00", "recurring": True, "trial_amount": "1.00", "trial_hours": 72},
    "3m":  {"label": "3 месяца",  "months": 3,  "amount": "6590.00", "recurring": True, "trial_amount": "1.00", "trial_hours": 72},
    "6m":  {"label": "6 месяцев", "months": 6,  "amount": "11390.00","recurring": True, "trial_amount": "1.00", "trial_hours": 72},
    "12m": {"label": "12 месяцев","months": 12, "amount": "19900.00","recurring": True, "trial_amount": "1.00", "trial_hours": 72},
}

RATES_TEXT = (
"""Тут вы можете оформить подписку на доступ:

1 месяц / 2.490₽
3 месяца /  ̶7̶4̶7̶0̶  6.490₽🔥
6 месяцев / ̶1̶4̶9̶4̶0̶  11.490₽ 🔥🔥
12 месяцев / ̶2̶9̶8̶8̶0̶  19.990₽ 🔥🔥🔥"""
)

PRE_PAY_TEXT = (
    "📦 Что даёт подписка:\n"
    " — Доступ ко всем инструментам на выбранный срок\n"
    " — Доступ ко всем инструментам\n"
    "Нажимая «Я ознакомлен и согласен», вы принимаете "
    "<a href=\"https://setrealtora.ru/agreement\">условия</a>."
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

def kb_settings_main(user_id: int) -> InlineKeyboardMarkup:
    """
    Меню настроек, доступно только по команде /settings.
    Здесь доступна кнопка удаления подписки и карты.
    """
    rows = []
    # Показать краткий статус
    cur_code = db.get_variable(user_id, "sub_plan_code") or "—"
    sub_until = db.get_variable(user_id, "sub_until") or "—"
    rows.append([InlineKeyboardButton(text=f"Статус: до {sub_until} (план: {cur_code})", callback_data="noop")])
    # Управление (без удаления)
    if _is_subscription_active(user_id):
        rows.append([InlineKeyboardButton(text="⚙️ Управлять подпиской", callback_data="sub:manage")])
    # Кнопка удалить и отказаться (только если карта привязана)
    pm_id = db.get_variable(user_id, "yk:payment_method_id")
    if pm_id:
        rows.append([InlineKeyboardButton(text="🗑️ Удалить и отказаться", callback_data="sub:cancel_all")])
    rows.append([InlineKeyboardButton(text="⬅️ К тарифам", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_cancel_confirm() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✅ Да, отменить и удалить", callback_data="sub:cancel_yes"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="sub:cancel_no"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_pay_with_consent(*, consent: bool, pay_url: Optional[str], show_manage: bool = False) -> InlineKeyboardMarkup:
    """
    Экран оплаты:
      - чекбокс «Я ознакомлен и согласен»
      - если чекбокс нажат — показываем кнопку с URL
      - если не нажат — показываем кнопку-заглушку, которая просит поставить галочку
      - если у пользователя активный триал/подписка — показываем «Управлять подпиской»
    """
    check = "✅ Я ознакомлен и согласен" if consent else "⬜️ Я ознакомлен и согласен"
    rows = [
        [InlineKeyboardButton(text=check, callback_data="tos:toggle")],
    ]
    if consent and pay_url:
        rows.append([InlineKeyboardButton(text="💳 Оплатить", url=pay_url)])
    # else:
    #     rows.append([InlineKeyboardButton(text="💳 Оплатить", callback_data="tos:need")])

    if show_manage:
        rows.append([InlineKeyboardButton(text="⚙️ Управлять подпиской", callback_data="sub:manage")])

    rows.append([InlineKeyboardButton(text="⬅️ Выбрать другой тариф", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_manage_menu(user_id: int) -> InlineKeyboardMarkup:
    rows = []
    cur_code = _current_plan_code(user_id)
    rows.append([InlineKeyboardButton(text=f"Текущий план: {TARIFFS[cur_code]['label']}", callback_data="noop")])
    upgrades = _upgrade_options(user_id)
    if upgrades:
        for code, label in upgrades:
            rows.append([InlineKeyboardButton(text=f"Повысить до: {label}", callback_data=f"sub:upgrade:{code}")])
    else:
        rows.append([InlineKeyboardButton(text="Доступны все планы", callback_data="noop")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="show_rates")])
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
        m = await cb.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        msg_id = m.message_id if isinstance(m, Message) else cb.message.message_id
    except Exception:
        try:
            m = await cb.message.edit_caption(caption=text, reply_markup=kb, parse_mode="HTML")
            if isinstance(m, Message):
                msg_id = m.message_id
        except Exception:
            m = await cb.message.answer(text, reply_markup=kb, parse_mode="HTML")
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


def _is_subscription_active(user_id: int) -> bool:
    """Есть активный доступ (триал или оплаченный период)."""
    try:
        until = db.get_variable(user_id, "sub_until") or ""
        if not until:
            return False
        d_until = datetime.fromisoformat(until).date()
        return d_until >= datetime.utcnow().date()
    except Exception:
        return False

def _has_saved_card(user_id: int) -> bool:
    """Есть ли сохранённый способ оплаты у провайдера (привязана ли карта)."""
    return bool(db.get_variable(user_id, "yk:payment_method_id"))


def _current_plan_code(user_id: int) -> str:
    """Текущий план пользователя (для апгрейда)."""
    code = db.get_variable(user_id, "sub_plan_code") or ""
    return code if code in TARIFFS else "1m"


def _upgrade_options(user_id: int) -> list[tuple[str, str]]:
    """
    Вернёт пары (code, label) только для планов, у которых months > current.
    """
    cur = _current_plan_code(user_id)
    cur_m = TARIFFS[cur]["months"]
    opts: list[tuple[str, str]] = []
    for code, pl in TARIFFS.items():
        if pl["months"] > cur_m:
            opts.append((code, pl["label"]))
    opts.sort(key=lambda x: TARIFFS[x[0]]["months"])
    return opts


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
            # Фолбэк для магазинов без рекуррентных платежей
            err_txt = str(getattr(e, "args", [""])[0] or e)
            if "can't make recurring payments" in err_txt.lower() or "forbidden" in err_txt.lower():
                logging.error("Recurring not allowed for this shop. Falling back to tokenless trial 1 RUB")
                # создаём обычный платёж на 1 ₽ БЕЗ сохранения способа оплаты
                try:
                    meta_fallback = dict(meta)
                    # помечаем, что это триал без токена — чтобы в вебхуке не создавать рекуррентную подписку
                    meta_fallback["is_recurring"] = "0"
                    meta_fallback["phase"] = "trial_tokenless"
                    pay_url = youmoney.create_pay_ex(
                        user_id=user_id,
                        amount_rub=first_amount,
                        description=f"{description} (пробный период)",
                        metadata=meta_fallback,
                        save_payment_method=False,
                    )
                    # для UI дадим понятный текст
                    db.set_variable(user_id, "yk:recurring_disabled", "1")
                except Exception as e2:
                    logging.exception("Fallback (tokenless trial) also failed: %s", e2)
                    await _edit_safe(cb, "Не удалось создать платёж. Попробуйте позже.", kb_rates())
                    return
            else:
                logging.exception("Failed to create trial payment: %s", e)
                await _edit_safe(cb, "Не удалось создать платёж. Попробуйте позже.", kb_rates())
                return
    else:
        # сейчас все планы рекуррентные; этот блок не должен выполниться
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

    # Сохраняем заголовок (описание), чтобы уметь подменять текст при переключении чекбокса
    try:
        db.set_variable(user_id, "yk:last_pay_header", description)
    except Exception:
        logging.exception("Failed to store last pay header for user %s", user_id)

    # Если согласие уже проставлено — показываем текст без ссылки, иначе с кликабельной ссылкой
    consent_raw = db.get_variable(user_id, "tos:accepted_at")
    consent = bool(consent_raw)
    text = f"{description}\n\n{PAY_TEXT if consent else PRE_PAY_TEXT}"

    # Сохраняем URL, чтобы не пересоздавать при кликах чекбокса
    try:
        db.set_variable(user_id, "yk:last_pay_url", pay_url or "")
    except Exception:
        logging.exception("Failed to store last pay url for user %s", user_id)

    show_manage = _is_subscription_active(user_id)

    msg_id = await _edit_safe(
        cb,
        text,
        kb_pay_with_consent(consent=consent, pay_url=pay_url if consent else None, show_manage=show_manage)
    )

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
    показываем/прячем кнопку со ссылкой.
    """
    user_id = cb.from_user.id
    cur = db.get_variable(user_id, "tos:accepted_at")
    if cur:
        db.set_variable(user_id, "tos:accepted_at", "")  # снимаем галочку
    else:
        db.set_variable(user_id, "tos:accepted_at", datetime.utcnow().isoformat(timespec="seconds") + "Z")

    consent = not bool(cur)
    pay_url = db.get_variable(user_id, "yk:last_pay_url") or None
    header = db.get_variable(user_id, "yk:last_pay_header") or "Оплата подписки"
    # Меняем и текст, и клавиатуру: до согласия — текст с кликабельной ссылкой, после — обычный текст без ссылки
    new_text = f"{header}\n\n{PAY_TEXT if consent else PRE_PAY_TEXT}"

    await _edit_safe(
        cb,
        new_text,
        kb_pay_with_consent(
            consent=consent,
            pay_url=pay_url if consent else None,
            show_manage=_is_subscription_active(user_id)
        )
    )


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
        # локальные хелперы управления меню подписки
        async def _notify_success_and_menu(_user_id: int, _code: str, _sub_until: str) -> None:
            try:
                await bot.send_message(
                    chat_id=_user_id,
                    text=(
                        f"✅ Оплата прошла успешно!\n\n"
                        f"Тариф: *{TARIFFS.get(_code, {}).get('label', _code)}*\n"
                        f"Подписка активна до: *{_sub_until}*"
                    )
                )
                try:
                    from bot.handlers.handler_manager import send_menu_with_logo as _send_menu_with_logo
                    await _send_menu_with_logo(bot, _user_id)
                except Exception as e:
                    logging.warning("Failed to send main menu after payment for user %s: %s", _user_id, e)
            except Exception as e:
                logging.warning("Failed to notify user %s after payment: %s", _user_id, e)

        event = payload.get("event")
        obj = payload.get("object") or {}
        payment_id = obj.get("id")
        status = obj.get("status")
        metadata = obj.get("metadata") or {}
        payment_method = obj.get("payment_method") or {}
        pm_id = payment_method.get("id")
        # если знаем payment_id → пометим исходную попытку (если она была создана с записью)
        try:
            if payment_id and status in ("succeeded", "canceled", "expired"):
                from bot.utils import database as _db_for_attempts
                _db_for_attempts.mark_charge_attempt_status(
                    payment_id=payment_id,
                    status="succeeded" if status == "succeeded" else status
                )
        except Exception:
            pass

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

        # базовая валидация
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
        phase = str(metadata.get("phase") or "").strip()  # "trial" | "renewal" | "trial_tokenless"
        subscription_id_meta = metadata.get("subscription_id")
        try:
            subscription_id_meta = int(subscription_id_meta) if subscription_id_meta is not None else None
        except Exception:
            subscription_id_meta = None

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
                # Первый платёж 1 ₽: открываем демо-период, планируем автосписание
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
                db.set_variable(user_id, "sub_plan_code", code)

                sub_until = trial_until_iso[:10]
            elif phase == "renewal":
                # Успешное автосписание после триала (или последующих периодов)
                sub_until = _compute_sub_until(interval_m)
                db.set_variable(user_id, "have_sub", "1")
                db.set_variable(user_id, "sub_paid_at", paid_at)
                db.set_variable(user_id, "sub_until", sub_until)
                db.set_variable(user_id, "sub_plan_code", code)
                # перенос next_charge_at только после успеха
                try:
                    from dateutil.relativedelta import relativedelta
                    next_at = datetime.utcnow() + relativedelta(months=+interval_m)
                except Exception:
                    next_at = datetime.utcnow() + timedelta(days=30 * interval_m)
                try:
                    db.subscription_mark_charged(subscription_id_meta, next_charge_at=next_at)
                except Exception:
                    try:
                        db.subscription_mark_charged_for_user(user_id=user_id, next_charge_at=next_at)
                    except Exception:
                        logging.exception("Failed to bump next_charge_at after renewal for user %s", user_id)
            else:
                # защитная ветка на случай странных метаданных
                logging.info("Recurring payment with unexpected phase=%s; no state change", phase)
        else:
            # НЕ рекуррентные оплаты (включая триал без токена)
            # 1) trial_tokenless → просто открыть демо и НЕ создавать рекуррентную подписку
            if phase == "trial_tokenless":
                trial_hours = int(str(metadata.get("trial_hours") or "72"))
                trial_until_iso = db.set_trial(user_id, hours=trial_hours)
                db.set_variable(user_id, "have_sub", "1")
                db.set_variable(user_id, "sub_paid_at", paid_at)
                db.set_variable(user_id, "sub_until", trial_until_iso[:10])
                sub_until = trial_until_iso[:10]
                db.set_variable(user_id, "sub_plan_code", code)
            else:
                # Обычный разовый платёж (не должен встречаться в текущей схеме)
                db.set_variable(user_id, "have_sub", "1")
                sub_until = _compute_sub_until(months)
                db.set_variable(user_id, "sub_paid_at", paid_at)
                db.set_variable(user_id, "sub_until", sub_until)
                db.set_variable(user_id, "sub_plan_code", code)

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
        await _notify_success_and_menu(user_id, code, sub_until)

        return 200, "ok"

    except Exception as e:
        logging.exception("Webhook processing error: %s", e)
        return 500, f"error: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# Настройки (/settings) и безопасная отмена подписки + удаление карты
# ──────────────────────────────────────────────────────────────────────────────

async def open_settings_cmd(msg: Message) -> None:
    """
    Команда /settings открывает меню настроек.
    Только здесь доступна кнопка «Удалить и отказаться».
    """
    user_id = msg.from_user.id
    text = (
        "⚙️ *Настройки подписки*\n"
        "Здесь вы можете управлять подпиской, а также полностью удалить подписку и привязанную карту.\n\n"
        "• *Управлять подпиской* — повысить тариф, посмотреть статус.\n"
        "• *Удалить и отказаться* — немедленно отключит доступ и удалит сохранённую карту."
    )
    await msg.answer(text, reply_markup=kb_settings_main(user_id), parse_mode="Markdown")

async def cancel_request(cb: CallbackQuery) -> None:
    """
    Шаг подтверждения перед окончательной отменой.
    """
    text = (
        "Вы уверены, что хотите *немедленно отменить подписку* и *удалить карту*?\n\n"
        "• Доступ будет закрыт сразу.\n"
        "• Автосписания прекращаются.\n"
        "• Привязанный способ оплаты будет удалён."
    )
    await _edit_safe(cb, text, kb_cancel_confirm())

async def cancel_no(cb: CallbackQuery) -> None:
    """Возврат в настройки без отмены."""
    user_id = cb.from_user.id
    await _edit_safe(cb, "Действие отменено. Вы в настройках подписки.", kb_settings_main(user_id))

async def cancel_yes(cb: CallbackQuery) -> None:
    """
    Полная отмена: закрыть доступ, отменить подписку, удалить способ оплаты.
    """
    user_id = cb.from_user.id
    # 1) Пытаемся отменить ВСЕ активные подписки в БД
    try:
        db.subscription_cancel_for_user(user_id=user_id)
    except Exception:
        logging.exception("Failed to cancel subscription for user %s", user_id)

    # 2) Удаляем карту у платёжного провайдера (если поддерживается)
    try:
        pm_id = db.get_variable(user_id, "yk:payment_method_id")
        if pm_id:
            youmoney.detach_payment_method(pm_id)
    except Exception:
        logging.exception("Failed to detach payment method for user %s", user_id)

    # 3) Чистим локальные ключи доступа
    try:
        db.set_variable(user_id, "have_sub", "")
        db.set_variable(user_id, "sub_paid_at", "")
        db.set_variable(user_id, "sub_until", (datetime.utcnow() - timedelta(days=1)).date().isoformat())
        db.set_variable(user_id, "sub_plan_code", "")
        # ключевое: локально стираем токен, чтобы billing_loop его не увидел
        db.set_variable(user_id, "yk:payment_method_id", "")
    except Exception:
        logging.exception("Failed to clear sub state for user %s", user_id)

    # 4) Сообщение пользователю
    await _edit_safe(
        cb,
        "✅ Подписка отменена, карта удалена.\nДоступ закрыт. Вы всегда можете оформить новый тариф из раздела тарифов.",
        kb_rates()
    )


# ──────────────────────────────────────────────────────────────────────────────
# Управление подпиской: меню и апгрейд
# ──────────────────────────────────────────────────────────────────────────────

async def open_manage(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    if not _is_subscription_active(user_id):
        await _edit_safe(cb, "Подписка не активна. Выберите тариф для оформления:", kb_rates())
        return
    await _edit_safe(
        cb,
        "Управление подпиской:\nВы можете повысить тариф. Изменения вступят в силу со следующего списания.",
        kb_manage_menu(user_id)
    )


async def upgrade_plan(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    try:
        _, _, code = cb.data.split(":", 2)  # sub:upgrade:<code>
    except Exception:
        await _edit_safe(cb, "Не удалось определить новый тариф.", kb_manage_menu(user_id))
        return

    if code not in TARIFFS:
        await _edit_safe(cb, "Такого тарифа нет.", kb_manage_menu(user_id))
        return

    # Если рекурренты недоступны для магазина — предлагаем переоформить вручную
    if db.get_variable(user_id, "yk:recurring_disabled"):
        await _edit_safe(
            cb,
            "Автопродления недоступны. Пожалуйста, переоформите подписку из списка тарифов.",
            kb_rates()
        )
        return

    new_plan = TARIFFS[code]
    try:
        # Предпочтительно обновить существующую подписку без изменения next_charge_at
        db.subscription_update_plan(
            user_id=user_id,
            plan_code=code,
            interval_months=new_plan["months"],
            amount_value=new_plan["amount"],
        )
    except Exception:
        try:
            sub = getattr(db, "subscription_get_for_user", lambda **_: None)(user_id=user_id)  # может не существовать
            next_charge_at = (sub or {}).get("next_charge_at", datetime.utcnow() + timedelta(days=3))
            db.subscription_upsert(
                user_id=user_id,
                plan_code=code,
                interval_months=new_plan["months"],
                amount_value=new_plan["amount"],
                amount_currency="RUB",
                payment_method_id=db.get_variable(user_id, "yk:payment_method_id"),
                next_charge_at=next_charge_at,
                status="active",
            )
        except Exception as e:
            logging.exception("Failed to upgrade plan for user %s: %s", user_id, e)
            await _edit_safe(cb, "Не удалось обновить подписку. Попробуйте позже.", kb_manage_menu(user_id))
            return

    db.set_variable(user_id, "sub_plan_code", code)
    await _edit_safe(
        cb,
        f"Готово! Новый план: *{new_plan['label']}*.\nИзменения вступят в силу со следующего автосписания.",
        kb_manage_menu(user_id)
    )


# ──────────────────────────────────────────────────────────────────────────────
# ROUTER
# ──────────────────────────────────────────────────────────────────────────────

def router(rt: Router) -> None:
    # Команда настроек (только здесь доступно удаление подписки и карты)
    rt.message.register(open_settings_cmd, Command("settings"))
    # Показ тарифов
    rt.callback_query.register(show_rates, F.data == "show_rates")
    # Выбор тарифа
    rt.callback_query.register(choose_rate, F.data.startswith("sub:choose:"))
    # Чекбокс согласия и блокирующая «Оплатить» без согласия
    rt.callback_query.register(toggle_tos, F.data == "tos:toggle")
    rt.callback_query.register(need_tos,   F.data == "tos:need")
    # Меню управления подпиской и апгрейд
    rt.callback_query.register(open_manage, F.data == "sub:manage")
    rt.callback_query.register(upgrade_plan, F.data.startswith("sub:upgrade:"))
    # Отмена подписки и удаление карты (доступно только из /settings)
    rt.callback_query.register(cancel_request, F.data == "sub:cancel_all")
    rt.callback_query.register(cancel_yes,     F.data == "sub:cancel_yes")
    rt.callback_query.register(cancel_no,      F.data == "sub:cancel_no")
