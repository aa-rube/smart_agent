# smart_agent/bot/handlers/payment_handler.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple, List

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.filters import Command

from bot.config import get_file_path
from bot.utils import youmoney
import bot.utils.database as app_db
import bot.utils.billing_db as billing_db
from bot.utils.mailing import send_last_published_to_user

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# ТАРИФЫ
# ──────────────────────────────────────────────────────────────────────────────
TARIFFS: Dict[str, Dict] = {
    "1m": {"label": "1 месяц", "months": 1, "amount": "2490.00", "recurring": True, "trial_amount": "1.00", "trial_hours": 72},
    "3m": {"label": "3 месяца", "months": 3, "amount": "6490.00", "recurring": True, "trial_amount": "1.00", "trial_hours": 72},
    "6m": {"label": "6 месяцев", "months": 6, "amount": "11490.00", "recurring": True, "trial_amount": "1.00", "trial_hours": 72},
    "12m": {"label": "12 месяцев", "months": 12, "amount": "19900.00", "recurring": True, "trial_amount": "1.00", "trial_hours": 72},
}


RATES_TEXT = ('''
🎁 Хочешь смотреть контент для соцсетей риэлтора без ограничений?
Оформи пробный доступ на 3 дня ко всем нашим Инструментам всего за 1 ₽
А дальше выбери удобный абонемент:
    
    
1 месяц — 2 490 ₽
3 месяца — <s>7 470 ₽</s> => 6 490 ₽
6 месяцев — <s>14 940 ₽</s> => 11 490 ₽ 🔥
12 месяцев — <s>29 880 ₽</s> => 19 990 ₽'''
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
# ВНУТРЕННОЕ КЭШИРОВАНИЕ СОГЛАСИЯ (только для UI-чекбокса)
# ──────────────────────────────────────────────────────────────────────────────
# Храним state чекбокса в памяти: само согласие юридически фиксируем в app_db.add_consent
_CONSENT_FLAG: dict[int, bool] = {}
_LAST_PAY_URL: dict[int, str] = {}
_LAST_PAY_HEADER: dict[int, str] = {}


# ──────────────────────────────────────────────────────────────────────────────
# КЛАВИАТУРЫ
# ──────────────────────────────────────────────────────────────────────────────

def kb_rates() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 3 дня за 1₽", callback_data="sub:choose:1m")],
        [
            InlineKeyboardButton(text="1 месяц", callback_data="sub:choose:1m"),
            InlineKeyboardButton(text="3 месяца", callback_data="sub:choose:3m"),
            InlineKeyboardButton(text="6 месяцев", callback_data="sub:choose:6m"),
        ],
        [InlineKeyboardButton(text="12 месяцев", callback_data="sub:choose:12m")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")],
    ])


def _trial_status_line(user_id: int) -> Optional[str]:
    """Возвращает строку статуса, если активен триал."""
    try:

        until = app_db.get_trial_until(user_id)
        if until and app_db.is_trial_active(user_id):
            return f"Статус: до {until.date().isoformat()} (триал)"
    except Exception:
        pass
    return None


def kb_settings_main(user_id: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []

    # Статус: сначала триал; иначе — по факту наличия карты
    trial_line = _trial_status_line(user_id)
    if trial_line:
        rows.append([InlineKeyboardButton(text=trial_line, callback_data="noop")])
    else:
        if billing_db.has_saved_card(user_id):
            rows.append([InlineKeyboardButton(text="Статус: автопродление включено", callback_data="noop")])
        else:
            rows.append([InlineKeyboardButton(text="Статус: неактивна", callback_data="noop")])


    # Кнопка удаления карты
    if billing_db.has_saved_card(user_id):
        card = billing_db.get_user_card(user_id) or {}
        suffix = f"{(card.get('brand') or '').upper()} ••••{card.get('last4', '')}"
        rows.append([InlineKeyboardButton(text=f"🗑️ Удалить карту ({suffix})", callback_data="sub:cancel_all")])

    rows.append([InlineKeyboardButton(text="⬅️ К тарифам", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_cancel_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить карту", callback_data="sub:cancel_yes")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sub:cancel_no")],
    ])


def kb_pay_with_consent(*, consent: bool, pay_url: Optional[str]) -> InlineKeyboardMarkup:
    check = "✅ Я ознакомлен и согласен" if consent else "⬜️ Я ознакомлен и согласен"
    rows: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton(text=check, callback_data="tos:toggle")]]
    if consent and pay_url:
        rows.append([InlineKeyboardButton(text="💳 Оплатить", url=pay_url)])

    rows.append([InlineKeyboardButton(text="⬅️ Выбрать другой тариф", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

async def _edit_safe(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> Optional[int]:
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


def _compute_next_time_from_months(months: int) -> datetime:
    try:
        from dateutil.relativedelta import relativedelta
        return datetime.now(timezone.utc) + relativedelta(months=+months)
    except Exception:
        return datetime.now(timezone.utc) + timedelta(days=30 * months)


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC: Показ тарифов / выбор тарифа / ссылка на оплату
# ──────────────────────────────────────────────────────────────────────────────

async def show_rates(evt: Message | CallbackQuery) -> None:
    if isinstance(evt, CallbackQuery):
        await _edit_safe(evt, RATES_TEXT, kb_rates())
    else:
        await evt.answer(RATES_TEXT, reply_markup=kb_rates())


async def choose_rate(cb: CallbackQuery) -> None:
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

    description = f"Подписка на {plan['label']}"
    meta = {
        "user_id": str(user_id),
        "plan_code": code,
        "months": str(plan["months"]),
        "v": "2",  # версия схемы метаданных
    }

    if plan.get("recurring"):
        first_amount = plan.get("trial_amount", "1.00")
        meta.update({
            "phase": "trial",
            "is_recurring": "1",
            "trial_hours": str(plan.get("trial_hours", 72)),
            "plan_amount": plan["amount"],
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
            # Fallback: магазин не умеет рекуррентные платежи — делаем без токена
            logger.error("Recurring not allowed, fallback to tokenless trial: %s", e)
            meta_fallback = dict(meta)
            meta_fallback["is_recurring"] = "0"
            meta_fallback["phase"] = "trial_tokenless"
            pay_url = youmoney.create_pay_ex(
                user_id=user_id,
                amount_rub=first_amount,
                description=f"{description} (пробный период)",
                metadata=meta_fallback,
                save_payment_method=False,
            )
    else:
        # сейчас все планы рекуррентные
        pay_url = youmoney.create_pay_ex(
            user_id=user_id,
            amount_rub=plan["amount"],
            description=description,
            metadata=meta,
        )

    # Инициализируем состояние чекбокса (по умолчанию не отмечен)
    _CONSENT_FLAG[user_id] = _CONSENT_FLAG.get(user_id, False)
    _LAST_PAY_URL[user_id] = pay_url or ""
    _LAST_PAY_HEADER[user_id] = description


    await _edit_safe(
        cb,
        f"{description}\n\n{PRE_PAY_TEXT}",
        kb_pay_with_consent(consent=_CONSENT_FLAG[user_id], pay_url=None),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Чек-бокс согласия
# ──────────────────────────────────────────────────────────────────────────────

async def toggle_tos(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    new_state = not bool(_CONSENT_FLAG.get(user_id))
    _CONSENT_FLAG[user_id] = new_state
    if new_state:
        # Юридически фиксируем согласие
        try:
            app_db.add_consent(user_id, kind="tos")
        except Exception:
            logger.exception("Failed to record consent for user %s", user_id)

    header = _LAST_PAY_HEADER.get(user_id, "Оплата подписки")
    text = f"{header}\n\n{PAY_TEXT if new_state else PRE_PAY_TEXT}"
    pay_url = _LAST_PAY_URL.get(user_id) or None

    await _edit_safe(
        cb,
        text,
        kb_pay_with_consent(consent=new_state, pay_url=(pay_url if new_state else None))
    )


async def need_tos(cb: CallbackQuery) -> None:
    await cb.answer("Поставьте отметку «Я ознакомлен и согласен», чтобы продолжить.", show_alert=True)


# ──────────────────────────────────────────────────────────────────────────────
# WEBHOOK: успешные платежи YooKassa
# ──────────────────────────────────────────────────────────────────────────────

async def process_yookassa_webhook(bot: Bot, payload: Dict) -> Tuple[int, str]:
    try:
        event = payload.get("event")
        obj = payload.get("object") or {}
        payment_id = obj.get("id")
        status = obj.get("status")
        metadata = obj.get("metadata") or {}
        pmethod = obj.get("payment_method") or {}

        # помечаем попытку списания, если где-то создавали (не критично)
        try:
            if payment_id and status in ("succeeded", "canceled", "expired"):
                billing_db.mark_charge_attempt_status(payment_id=payment_id,
                                                      status=("succeeded" if status == "succeeded" else status))
        except Exception:
            pass

        if not payment_id or not status:
            return 400, "missing payment_id/status"

        # неуспех — просто уведомим
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
                        "Если списания не было — попробуйте оплатить снова из раздела тарифов."
                    )
                    await bot.send_photo(chat_id=user_id_fail, photo=photo, caption=caption, parse_mode="Markdown")
                except Exception as e:
                    logger.warning("Failed to send fail notice to %s: %s", user_id_fail, e)
            return 200, f"fail event={event} status={status}"

        if event not in ("payment.succeeded", "payment.waiting_for_capture"):
            return 200, f"skip event={event}"

        user_id = int(metadata.get("user_id") or 0)
        if not user_id:
            return 400, "missing user_id in metadata"

        # --- идемпотентность/аудит ---
        try:
            billing_db.payment_log_upsert(
                payment_id=payment_id,
                user_id=user_id,
                amount_value=str(obj.get("amount", {}).get("value") or ""),
                amount_currency=str(obj.get("amount", {}).get("currency") or "RUB"),
                event=str(event or ""),
                status=str(status or ""),
                metadata=metadata,
                raw_payload=payload,
            )
            if billing_db.payment_log_is_processed(payment_id):
                return 200, "already processed"
        except Exception:
            logger.exception("payment_log_upsert failed for %s", payment_id)

        # --- разбираем план/фазу ---
        code = str(metadata.get("plan_code") or "1m")
        months = int(metadata.get("months") or TARIFFS.get(code, {}).get("months", 1))
        is_recurring = str(metadata.get("is_recurring") or "0") == "1"
        phase = str(metadata.get("phase") or "").strip()  # "trial" | "renewal" | "trial_tokenless"

        # карточка провайдера (для сохранения)
        pm_token = pmethod.get("id")
        card_info = (pmethod.get("card") or {})
        brand = (card_info.get("card_type") or card_info.get("brand") or "") or None
        first6 = (card_info.get("first6") or "") or None
        last4 = (card_info.get("last4") or "") or None
        exp_month = card_info.get("expiry_month")
        exp_year = card_info.get("expiry_year")
        try:
            exp_month = int(exp_month) if exp_month is not None else None
        except Exception:
            exp_month = None
        try:
            exp_year = int(exp_year) if exp_year is not None else None
        except Exception:
            exp_year = None

        # Убедимся, что пользователь есть в app DB (для триала/истории)
        app_db.check_and_add_user(user_id)

        # Успешные сценарии
        if is_recurring and phase == "trial":
            # 1) сохраняем карту в справочник (id не нужен в подписке; храним токен провайдера)
            if pm_token:
                billing_db.card_upsert_from_provider(
                    user_id=user_id, provider=pmethod.get("type", "yookassa"),
                    pm_token=pm_token, brand=brand, first6=first6, last4=last4,
                    exp_month=exp_month, exp_year=exp_year,
                )
            # 2) включаем триал доступа
            trial_hours = int(str(metadata.get("trial_hours") or "72"))
            trial_until = app_db.set_trial(user_id, hours=trial_hours)  # datetime (UTC)
            # 3) создаём/обновляем подписку с next_charge_at после триала
            next_charge_at = datetime.now(timezone.utc) + timedelta(hours=trial_hours)
            billing_db.subscription_upsert(
                user_id=user_id, plan_code=code, interval_months=months,
                amount_value=str(metadata.get("plan_amount") or TARIFFS.get(code, {}).get("amount", "0.00")),
                amount_currency=str(obj.get("amount", {}).get("currency") or "RUB"),
                payment_method_id=pm_token,  # в подписке хранится провайдерский токен (string), не PK карты
                next_charge_at=next_charge_at,
                status="active",
            )
            # уведомление
            await _notify_after_payment(bot, user_id, code, trial_until.date().isoformat())

        elif is_recurring and phase == "renewal":
            # переносим next_charge_at вперёд на период тарифа
            next_at = _compute_next_time_from_months(months)
            updated_sub_id = billing_db.subscription_mark_charged_for_user(user_id=user_id, next_charge_at=next_at)
            if not updated_sub_id:
                # если нет подписки (крайний случай) — создадим
                billing_db.subscription_upsert(
                    user_id=user_id, plan_code=code, interval_months=months,
                    amount_value=TARIFFS.get(code, {}).get("amount", "0.00"),
                    amount_currency=str(obj.get("amount", {}).get("currency") or "RUB"),
                    payment_method_id=None,  # оставим карту как было (мы её не знаем в этом событии)
                    next_charge_at=next_at, status="active",
                )
            # уведомление с «до …» брать из next_at
            await _notify_after_payment(bot, user_id, code, next_at.date().isoformat())

        else:
            # Не рекуррентный кейс (включая trial_tokenless): только триал.
            trial_hours = int(str(metadata.get("trial_hours") or "72"))
            trial_until = app_db.set_trial(user_id, hours=trial_hours)
            await _notify_after_payment(bot, user_id, code, trial_until.date().isoformat())

        # помечаем как обработанный
        try:
            billing_db.payment_log_mark_processed(payment_id)
        except Exception:
            logger.exception("payment_log_mark_processed failed for %s", payment_id)

        return 200, "ok"

    except Exception as e:
        logger.exception("Webhook processing error: %s", e)
        return 500, f"error: {e}"


async def _notify_after_payment(bot: Bot, user_id: int, code: str, until_date_iso: str) -> None:
    try:
        await bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ Оплата прошла успешно!\n\n"
                f"Тариф: *{TARIFFS.get(code, {}).get('label', code)}*\n"
                f"Доступ активен до: *{until_date_iso}*"
            ),
            parse_mode="Markdown",
        )
        try:
            from bot.handlers.handler_manager import send_menu_with_logo as _send_menu_with_logo
            await _send_menu_with_logo(bot, user_id)
        except Exception as e:
            logger.warning("Failed to send main menu after payment for user %s: %s", user_id, e)
        # Финт ушами: сразу после активации кидаем пользователю ближайший
        # уже отправленный пост (publish_at <= now). ВАЖНО: не следующий.
        try:
            await send_last_published_to_user(bot, user_id)
        except Exception as e:
            logger.warning("Failed to send last published mailing to %s: %s", user_id, e)
    except Exception as e:
        logger.warning("Failed to notify user %s after payment: %s", user_id, e)


# ──────────────────────────────────────────────────────────────────────────────
# Настройки (/settings) и удаление карты
# ──────────────────────────────────────────────────────────────────────────────

async def open_settings_cmd(msg: Message) -> None:
    user_id = msg.from_user.id
    logger.info("settings user_id=%s has_card=%s trial_active=%s",
                user_id, billing_db.has_saved_card(user_id), app_db.is_trial_active(user_id))
    text = (
        "⚙️ *Настройки подписки*\n"
        "Здесь можно управлять подпиской и удалить привязанную карту.\n\n"
        "• *Удалить карту* — немедленно остановит автосписания (подписка не отменяется, доступ действует до оплаченной даты)."
    )
    await msg.answer(text, reply_markup=kb_settings_main(user_id), parse_mode="Markdown")


async def cancel_request(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    card = billing_db.get_user_card(user_id) or {}
    suffix = f"{(card.get('brand') or '').upper()} ••••{card.get('last4', '')}"
    text = (
        f"Удалить карту *{suffix}*?\n\n"
        "• Автосписания прекратятся.\n"
        "• Подписка НЕ отменяется, доступ останется до оплаченной даты.\n"
        "• Данные карты будут удалены."
    )
    await _edit_safe(cb, text, kb_cancel_confirm())


async def cancel_no(cb: CallbackQuery) -> None:
    await _edit_safe(cb, "Действие отменено. Вы в настройках подписки.", kb_settings_main(cb.from_user.id))


async def cancel_yes(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    try:
        affected = billing_db.delete_user_card_and_detach_subscriptions(user_id=user_id)
        logger.info("Card deleted for user %s; detached from %s subscriptions", user_id, affected)
    except Exception:
        logger.exception("Failed to delete card for user %s", user_id)
        await _edit_safe(cb, "Не удалось удалить карту. Попробуйте позже.", kb_settings_main(user_id))
        return
    await _edit_safe(cb, "✅ Карта удалена. Автосписания остановлены. Подписка не отменена.", kb_settings_main(user_id))


# ──────────────────────────────────────────────────────────────────────────────
# Управление подпиской (простая версия): показываем апгрейды
# ──────────────────────────────────────────────────────────────────────────────

def _upgrade_options_from(code: str) -> list[tuple[str, str]]:
    cur_m = TARIFFS[code]["months"]
    opts = [(c, p["label"]) for c, p in TARIFFS.items() if p["months"] > cur_m]
    return sorted(opts, key=lambda x: TARIFFS[x[0]]["months"])


def _current_plan_code_guess() -> str:
    return "1m"


def kb_manage_menu() -> InlineKeyboardMarkup:
    cur_code = _current_plan_code_guess()
    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text=f"Текущий план: {TARIFFS[cur_code]['label']}", callback_data="noop")]
    ]
    for code, label in _upgrade_options_from(cur_code):
        rows.append([InlineKeyboardButton(text=f"Повысить до: {label}", callback_data=f"sub:upgrade:{code}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def open_manage(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    # Управление доступно, если есть активный триал или карта (рекуррент)
    if not (app_db.is_trial_active(user_id) or billing_db.has_saved_card(user_id)):
        await _edit_safe(cb, "Подписка не активна. Выберите тариф для оформления:", kb_rates())
        return
    await _edit_safe(
        cb,
        "Управление подпиской:\nВы можете повысить тариф. Изменения вступят в силу со следующего списания.",
        kb_manage_menu()
    )


async def upgrade_plan(cb: CallbackQuery) -> None:
    try:
        _, _, code = cb.data.split(":", 2)  # sub:upgrade:<code>
    except Exception:
        await _edit_safe(cb, "Не удалось определить новый тариф.", kb_manage_menu())
        return

    if code not in TARIFFS:
        await _edit_safe(cb, "Такого тарифа нет.", kb_manage_menu())
        return

    await _edit_safe(
        cb,
        f"Готово! Новый план будет применён со следующего автосписания: *{TARIFFS[code]['label']}*.",
        kb_manage_menu()
    )


# ──────────────────────────────────────────────────────────────────────────────
# ROUTER
# ──────────────────────────────────────────────────────────────────────────────

def router(rt: Router) -> None:
    # /settings
    rt.message.register(open_settings_cmd, Command("settings"))

    # тарифы
    rt.callback_query.register(show_rates, F.data == "show_rates")
    rt.callback_query.register(choose_rate, F.data.startswith("sub:choose:"))

    # согласие
    rt.callback_query.register(toggle_tos, F.data == "tos:toggle")
    rt.callback_query.register(need_tos, F.data == "tos:need")

    # управление/апгрейд
    rt.callback_query.register(open_manage, F.data == "sub:manage")
    rt.callback_query.register(upgrade_plan, F.data.startswith("sub:upgrade:"))

    # удаление карты
    rt.callback_query.register(cancel_request, F.data == "sub:cancel_all")
    rt.callback_query.register(cancel_yes, F.data == "sub:cancel_yes")
    rt.callback_query.register(cancel_no, F.data == "sub:cancel_no")
