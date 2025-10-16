# smart_agent/bot/handlers/payment_handler.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Optional, Tuple, List

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.filters import Command

from yookassa.domain.exceptions.forbidden_error import ForbiddenError
from bot.config import get_file_path
from bot.utils import youmoney
import bot.utils.database as app_db
import bot.utils.billing_db as billing_db
from bot.utils.redis_repo import yookassa_dedup, invalidate_payment_ok_cache

logger = logging.getLogger(__name__)

# Локальная константа времени МСК для форматирования дат в UI
MSK = ZoneInfo("Europe/Moscow")

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
🎁 Хочешь получить полный доступ ко всем инструментам без ограничений?
Оформи пробную подписку на 3 дня всего за 1 ₽,
а далее выбери удобный абонемент:

1 месяц — 2 490 ₽
3 месяца — <s>7 470 ₽</s> => 6 490 ₽
6 месяцев — <s>14 940 ₽</s> => 11 490 ₽ 🔥
12 месяцев — <s>29 880 ₽</s> => 19 990 ₽
'''
)

PRE_PAY_TEXT = (
    "📦 Что даёт подписка:\n"
    " — Доступ ко всем инструментам на выбранный срок\n"
    " — Автопродление при оплате картой или через СБП с привязкой (зависит от банка)\n"
    "Нажимая «Я ознакомлен и согласен», вы принимаете "
    "<a href=\"https://setrealtora.ru/agreement\">условия</a>."
)

PAY_TEXT = (
    "📦 Что даёт подписка:\n"
    " - Доступ ко всем инструментам на выбранный срок\n"
    "Нажмите «Оплатить» для оформления."
)

# ──────────────────────────────────────────────────────────────────────────────
# ПУБЛИЧНЫЕ ТЕКСТЫ ПРО ДОСТУП (централизовано)
# ──────────────────────────────────────────────────────────────────────────────
SUB_FREE = (
    "🎁 Бесплатный период завершён\n"
    "Пробный доступ на 72 часа истёк — дальше только по подписке.\n\n"
    "📦* Что даёт подписка:*\n"
    " — Полный доступ ко всем инструментам\n"
    " — Без ограничений по количеству запусков в период подписки*\n"
    "Стоимость пакета всего 2500 рублей!"
)

SUB_PAY = (
    "🪫 Подписка не активна\n"
    "Срок подписки истёк или не был оформлен.\n\n"
    "📦* Что даёт подписка:*\n"
    " — Полный доступ ко всем инструментам\n"
    " — Без ограничений по количеству запусков в период подписки*\n"
    "Стоимость пакета всего 2500 рублей!"
)

# ──────────────────────────────────────────────────────────────────────────────
# ВНУТРЕННОЕ КЭШИРОВАНИЕ СОГЛАСИЯ (только для UI-чекбокса)
# ──────────────────────────────────────────────────────────────────────────────
# Храним state чекбокса в памяти: само согласие юридически фиксируем в app_db.add_consent
_CONSENT_FLAG: dict[int, bool] = {}
# Раздельные ссылки под разные способы (карта/СБП)
_LAST_PAY_URL_CARD: dict[int, str] = {}
_LAST_PAY_URL_SBP: dict[int, str] = {}
_LAST_PAY_HEADER: dict[int, str] = {}

# Буфер выбранного тарифа до подтверждения согласия (генерацию ссылок откладываем):
_PENDING_SELECTION: dict[int, Dict[str, str]] = {}


# ──────────────────────────────────────────────────────────────────────────────
# TIME HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def _to_msk_str(dt: Optional[datetime]) -> str:
    """Возвращает dt в МСК 'YYYY-MM-DD HH:MM'. Если dt None — '—'.
    Если dt naive — считаем, что это UTC.
    """
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK).strftime("%Y-%m-%d %H:%M")


# ──────────────────────────────────────────────────────────────────────────────
# КЛАВИАТУРЫ
# ──────────────────────────────────────────────────────────────────────────────

# Публичная кнопка «Оформить подписку» для редиректа из любых модулей
SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)

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
        has_card = False
        try:
            has_card = billing_db.has_saved_card(user_id)
        except Exception:
            has_card = False
        rows.append([
            InlineKeyboardButton(
                text=("Статус: автопродление включено" if has_card else "Статус: неактивна"),
                callback_data="noop"
            )
        ])
    # Доп. строка: статус ретраев (если есть активная подписка)
    try:
        from bot.utils.billing_db import SessionLocal, Subscription
        with SessionLocal() as s:
            rec = (
                s.query(Subscription)
                .filter(Subscription.user_id == user_id, Subscription.status == "active")
                .order_by(Subscription.updated_at.desc())
                .first()
            )
            if rec:
                last_try = _to_msk_str(rec.last_attempt_at)
                fails = int(rec.consecutive_failures or 0)
                rows.append([InlineKeyboardButton(
                    text=f"Ретраи: {fails}/6, последняя попытка: {last_try}",
                    callback_data="noop"
                )])
    except Exception:
        pass


    # Кнопка удаления карты
    if (lambda _uid: (billing_db.has_saved_card(_uid) if hasattr(billing_db, 'has_saved_card') else False))(user_id):
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


def kb_pay_with_consent(*, consent: bool, pay_url_card: Optional[str], pay_url_sbp: Optional[str]) -> InlineKeyboardMarkup:
    check = "✅ Я ознакомлен и согласен" if consent else "⬜️ Я ознакомлен и согласен"
    rows: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton(text=check, callback_data="tos:toggle")]]
    if consent:
        btns: List[InlineKeyboardButton] = []
        if pay_url_card:
            btns.append(InlineKeyboardButton(text="💳 Оформить подписку (карта)", url=pay_url_card))
        if pay_url_sbp:
            btns.append(InlineKeyboardButton(text="🏦 Оформить подписку через СБП", url=pay_url_sbp))
        if btns:
            rows.append(btns)
    rows.append([InlineKeyboardButton(text="⬅️ Выбрать другой тариф", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────────────────────────────────────────────────────────────────────────
# HELPER: оффер триала 3 дня за 1 ₽ с прямой кнопкой оплаты (без чекбокса)
# ──────────────────────────────────────────────────────────────────────────────
def build_trial_offer(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """
    Строит текст и клавиатуру с кнопкой «💳 Активировать за 1 ₽» (пробный доступ 3 дня),
    как первый тариф 1m с триалом. Ссылка — результат youmoney.create_pay_ex.
    """
    plan = TARIFFS["1m"]
    description = f"Подписка на {plan['label']}"
    meta = {
        "user_id": str(user_id),
        "plan_code": "1m",
        "months": str(plan["months"]),
        "v": "2",
        "phase": "trial",
        "is_recurring": "1",
        "trial_hours": str(plan.get("trial_hours", 72)),
        "plan_amount": plan["amount"],
    }

    # генерируем ссылку оплаты на 1 ₽ с сохранением карты (рекуррент)
    try:
        pay_url = youmoney.create_pay_ex(
            user_id=user_id,
            amount_rub=plan.get("trial_amount", "1.00"),
            description=f"{description} (пробный период)",
            metadata=meta,
            save_payment_method=True,
        )
    except Exception:
        # Fallback: если магазин не умеет рекуррент — токен не сохраняем, но даём триал
        meta_fallback = dict(meta, is_recurring="0", phase="trial_tokenless")
        pay_url = youmoney.create_pay_ex(
            user_id=user_id,
            amount_rub=plan.get("trial_amount", "1.00"),
            description=f"{description} (пробный период)",
            metadata=meta_fallback,
            save_payment_method=False,
        )

    text = (
        "🎁 Спасибо за подписку, наш подарок для тебя все инструменты — 3 дня за 1 ₽.\n\n"
        "После этого подписка автоматически продлевается — 2490 ₽/мес."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Активировать за 1 ₽", url=pay_url or "")],
        ]
    )
    return text, kb


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


def _create_links_for_selection(user_id: int) -> tuple[Optional[str], Optional[str]]:
    """
    Генерирует ссылки оплаты (карта/СБП) для ранее выбранного тарифа в _PENDING_SELECTION[user_id].
    Возвращает (pay_url_card, pay_url_sbp). Ошибки логируются и дают None.
    Логику фолбэков (403/400) обрабатываем внутри.
    """
    sel = _PENDING_SELECTION.get(user_id) or {}
    code = sel.get("code")
    description = sel.get("description") or "Оплата подписки"
    plan = _plan_by_code(code) if code else None

    if not plan:
        return (None, None)

    pay_url_card: Optional[str] = None
    pay_url_sbp: Optional[str] = None

    meta_base = {
        "user_id": str(user_id),
        "plan_code": code,
        "months": str(plan["months"]),
        "v": "2",
        "trial_hours": str(plan.get("trial_hours", 72)),
        "plan_amount": plan["amount"],
    }
    first_amount = plan.get("trial_amount", "1.00")

    # 1) Карта: пробуем с сохранением (автопродление), иначе — без
    try:
        pay_url_card = youmoney.create_pay_ex(
            user_id=user_id,
            amount_rub=first_amount,
            description=f"{description} (пробный период)",
            metadata={**meta_base, "phase": "trial", "is_recurring": "1"},
            save_payment_method=True,
            payment_method_type="bank_card",
        )
    except Exception as e:
        logger.error("Card recurring not allowed, fallback to tokenless card trial: %s", e)
        try:
            pay_url_card = youmoney.create_pay_ex(
                user_id=user_id,
                amount_rub=first_amount,
                description=f"{description} (пробный период)",
                metadata={**meta_base, "phase": "trial_tokenless", "is_recurring": "0"},
                save_payment_method=False,
                payment_method_type="bank_card",
            )
        except Exception as e2:
            logger.error("Card tokenless also failed: %s", e2)
            pay_url_card = None

    # 2) СБП: сперва пытаемся с сохранением (если магазин/банк умеет), иначе — разовый СБП
    try:
        pay_url_sbp = youmoney.create_pay_ex(
            user_id=user_id,
            amount_rub=first_amount,
            description=f"{description} (пробный период, СБП)",
            metadata={**meta_base, "phase": "trial", "is_recurring": "1"},
            save_payment_method=True,
            payment_method_type="sbp",
        )
    except ForbiddenError as e:
        logger.error("SBP recurring not allowed, fallback to SBP tokenless trial: %s", e)
        try:
            pay_url_sbp = youmoney.create_pay_ex(
                user_id=user_id,
                amount_rub=first_amount,
                description=f"{description} (пробный период, СБП)",
                metadata={**meta_base, "phase": "trial_tokenless", "is_recurring": "0"},
                save_payment_method=False,
                payment_method_type="sbp",
            )
        except Exception as e2:
            logger.error("SBP tokenless also failed: %s", e2)
            pay_url_sbp = None
    except Exception as e:
        logger.error("SBP flow failed: %s", e)
        pay_url_sbp = None

    return (pay_url_card, pay_url_sbp)


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
    # Сохраняем выбор тарифа — ссылки пока не создаём
    _PENDING_SELECTION[user_id] = {"code": code, "description": description}

    # Инициализируем состояние чекбокса.
    # 1) Из памяти; 2) если нет — попытка гидратации из БД (если реализовано app_db.has_consent)
    consent = _CONSENT_FLAG.get(user_id, False)
    if not consent:
        try:
            if hasattr(app_db, "has_consent"):
                consent = bool(app_db.has_consent(user_id, kind="tos"))
        except Exception:
            pass
    _CONSENT_FLAG[user_id] = consent

    # Если согласие уже сохранено — ссылки генерим сразу, иначе — сбрасываем
    pay_url_card: Optional[str] = None
    pay_url_sbp: Optional[str] = None
    if consent:
        pay_url_card, pay_url_sbp = _create_links_for_selection(user_id)
        _LAST_PAY_URL_CARD[user_id] = pay_url_card or ""
        _LAST_PAY_URL_SBP[user_id]  = pay_url_sbp or ""
    else:
        _LAST_PAY_URL_CARD[user_id] = ""
        _LAST_PAY_URL_SBP[user_id]  = ""
    _LAST_PAY_HEADER[user_id] = description

    await _edit_safe(
        cb,
        f"{description}\n\n{PRE_PAY_TEXT}",
        kb_pay_with_consent(
            consent=consent,
            pay_url_card=(pay_url_card if consent else None),
            pay_url_sbp=(pay_url_sbp if consent else None),
        ),
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
    pay_url_card: Optional[str]
    pay_url_sbp: Optional[str]

    if new_state:
        # Создаём ссылки ТОЛЬКО сейчас — после согласия
        pay_url_card, pay_url_sbp = _create_links_for_selection(user_id)
        _LAST_PAY_URL_CARD[user_id] = pay_url_card or ""
        _LAST_PAY_URL_SBP[user_id]  = pay_url_sbp or ""
    else:
        # Снятие согласия — чистим ссылки и скрываем кнопки
        _LAST_PAY_URL_CARD[user_id] = ""
        _LAST_PAY_URL_SBP[user_id]  = ""
        pay_url_card, pay_url_sbp = None, None

    await _edit_safe(cb, text, kb_pay_with_consent(
        consent=new_state,
        pay_url_card=(pay_url_card if new_state else None),
        pay_url_sbp=(pay_url_sbp if new_state else None),
    ))


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

        # --- ИДЕМПОТЕНТНОСТЬ НА REDIS (быстрый путь) ---
        if not payment_id or not status:
            return 400, "missing payment_id/status"
        status_lc = str(status).lower()
        # waiting_for_capture: фиксируем и без побочек выходим
        if status_lc == "waiting_for_capture":
            # Запомним, но побочных эффектов не делаем
            await yookassa_dedup.should_process(payment_id, status_lc)  # просто зафиксирует, если надо
            return 200, "ack waiting_for_capture"

        # Для финальных статусов проверяем «надо ли обрабатывать?»
        ok = await yookassa_dedup.should_process(payment_id, status_lc)
        if not ok:
            return 200, f"duplicate/no-op status={status_lc}"

        # помечаем попытку списания в billing_db (статусы created -> succeeded/canceled/expired)
        try:
            if payment_id and status in ("succeeded", "canceled", "expired"):
                billing_db.mark_charge_attempt_status(payment_id=payment_id,
                                                      status=("succeeded" if status == "succeeded" else status))
        except Exception:
            pass

        # неуспех — просто уведомим
        if (event in ("payment.canceled", "payment.expired") or status in ("canceled", "expired")):
            try:
                user_id_raw = (payload.get("object") or {}).get("metadata", {}).get("user_id")
                user_id_fail = int(user_id_raw) if user_id_raw is not None else None
                sub_id_raw = (payload.get("object") or {}).get("metadata", {}).get("subscription_id")
                sub_id = int(sub_id_raw) if sub_id_raw is not None else None
            except Exception:
                user_id_fail, sub_id = None, None
            if user_id_fail:
                # ⚡ сбрасываем кэш «payment_ok» при любом финальном фейле
                try:
                    await invalidate_payment_ok_cache(user_id_fail)
                except Exception:
                    logger.warning("invalidate_payment_ok_cache failed (fail branch) for user %s", user_id_fail)
                try:
                    # троттлинг: не чаще 1 раза за 12ч
                    can_notice = True
                    if sub_id:
                        from bot.utils.billing_db import SessionLocal, Subscription, now_utc
                        from sqlalchemy import select
                        with SessionLocal() as s, s.begin():
                            rec = s.get(Subscription, sub_id)
                            if rec:
                                now = now_utc()
                                if rec.last_fail_notice_at and (now - rec.last_fail_notice_at) < timedelta(hours=12):
                                    can_notice = False
                                # инкремент фейлов (не более 6)
                                rec.consecutive_failures = min((rec.consecutive_failures or 0) + 1, 6)
                                if can_notice:
                                    rec.last_fail_notice_at = now
                                rec.updated_at = now
                    if can_notice:
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

        if event not in ("payment.succeeded",):
            return 200, f"skip event={event}"

        user_id = int(metadata.get("user_id") or 0)
        if not user_id:
            return 400, "missing user_id in metadata"

        # ⚡ на всякий случай инвалидируем кэш при успешном финальном событии
        try:
            await invalidate_payment_ok_cache(user_id)
        except Exception:
            logger.warning("invalidate_payment_ok_cache failed (success branch) for user %s", user_id)

        # --- аудит в БД (на случай рестартов/отладка) ---
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
            # Не полагаемся больше на БД для идемпотентности; Redis уже отфильтровал.
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
            # сброс счётчиков фейлов, обновление last_charge_at выполнено в repo; здесь — обнуление fail-серии на подписке
            try:
                from bot.utils.billing_db import SessionLocal, Subscription, now_utc
                with SessionLocal() as s, s.begin():
                    rec = (
                        s.query(Subscription)
                        .filter(Subscription.user_id == user_id, Subscription.status == "active")
                        .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                        .first()
                    )
                    if rec:
                        rec.consecutive_failures = 0
                        rec.updated_at = now_utc()
            except Exception:
                pass

        else:
            # Не рекуррентный кейс (включая trial_tokenless): только триал.
            trial_hours = int(str(metadata.get("trial_hours") or "72"))
            trial_until = app_db.set_trial(user_id, hours=trial_hours)
            await _notify_after_payment(bot, user_id, code, trial_until.date().isoformat())

        # помечаем как обработанный в БД (а в Redis уже зафиксирован финальный статус)
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
                "🎉 *Оплата прошла успешно!* Спасибо, что с нами.\n\n"
                f"🔖 Тариф: *{TARIFFS.get(code, {}).get('label', code)}*\n"
                f"📅 Доступ активен до: *{until_date_iso}*\n\n"
                "Что дальше:\n"
                "• Откройте главное меню и выберите нужный инструмент.\n\n"
                "Полезные инструменты:\n"
                "• 🛋️ Генератор дизайна интерьера — быстрые визуализации комнат.\n"
                "• 📐 Генератор планировок — скетч или реализм по вашему плану.\n"
                "• 🤖 Продвинутые ИИ-инструменты — тексты, ответы на возражения и др.\n\n"
                "Если что-то потребуется — напишите в поддержку, мы рядом."
            ),
            parse_mode="Markdown",
        )
        try:
            from bot.handlers.main_handler import send_menu_with_logo as _send_menu_with_logo
            await _send_menu_with_logo(bot, user_id)
        except Exception as e:
            logger.warning("Failed to send main menu after payment for user %s: %s", user_id, e)
        # Онбординг SMM (текст про 09:00 + 3 последних примера) теперь в smm_playbook
        try:
            from bot.handlers import smm_playbook as _smm
            await _smm.send_onboarding_after_payment(bot, user_id)
        except Exception as e:
            logger.warning("Failed to send SMM onboarding after payment for user %s: %s", user_id, e)
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


def format_access_text(user_id: int) -> str:
    """
    Короткий статус доступа для стартовых экранов инструментов.
    """
    try:
        hours = trial_remaining_hours(user_id)
    except Exception:
        hours = 0
    if is_trial_active(user_id):
        try:
            until_dt = app_db.get_trial_until(user_id)
        except Exception:
            until_dt = None
        if until_dt:
            return f"🆓 Бесплатный доступ активен до *{until_dt.date().isoformat()}* (~{hours} ч.)"
        return f"🆓 Бесплатный доступ активен ещё *~{hours} ч.*"
    if billing_db.has_saved_card(user_id):
        return "✅ Подписка активна (автопродление включено)"
    return "😢 Бесплатный период завершён. Оформи подписку, чтобы продолжить."


def has_access(user_id: int) -> bool:
    """
    Доступ есть, если активен триал или подписка.
    """
    try:
        return bool(is_trial_active(user_id) or billing_db.has_saved_card(user_id))
    except Exception:
        return False


async def ensure_access(evt: Message | CallbackQuery) -> bool:
    """
    Централизованная проверка доступа. Возвращает True, если доступ есть.
    Иначе — показывает экран с предложением оформить подписку и возвращает False,
    прерывая основной флоу.
    """
    user_id = evt.from_user.id if isinstance(evt, CallbackQuery) else evt.from_user.id
    if has_access(user_id):
        return True
    text = SUB_FREE if not billing_db.has_saved_card(user_id) else SUB_PAY
    try:
        if isinstance(evt, CallbackQuery):
            await _edit_safe(evt, text, SUBSCRIBE_KB)
        else:
            await evt.answer(text, reply_markup=SUBSCRIBE_KB, parse_mode="Markdown")
    except Exception:
        pass
    return False
