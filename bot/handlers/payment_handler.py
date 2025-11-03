# smart_agent/bot/handlers/payment_handler.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Optional, Tuple, List
import asyncio
import os
import httpx
from decimal import Decimal

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.filters import Command
from html import escape

from yookassa.domain.exceptions.forbidden_error import ForbiddenError
from bot.config import get_file_path
from bot.utils import youmoney
import bot.utils.database as app_db
import bot.utils.billing_db as billing_db
from bot.utils.redis_repo import yookassa_dedup, invalidate_payment_ok_cache, quota_repo

logger = logging.getLogger(__name__)

# Membership-service (FastAPI), по умолчанию локально на 6000
MEMBERSHIP_BASE_URL = os.getenv("MEMBERSHIP_BASE_URL", "http://127.0.0.1:6000")

# Локальная константа времени МСК для форматирования дат в UI
MSK = ZoneInfo("Europe/Moscow")

# ──────────────────────────────────────────────────────────────────────────────
# ТАРИФЫ
# ──────────────────────────────────────────────────────────────────────────────
TARIFFS: Dict[str, Dict] = {
    "1m": {"label": "1 месяц", "months": 1, "amount": "2490.00", "recurring": True, "trial_amount": "1.00", "trial_hours": 72},
    "3m": {"label": "3 месяца", "months": 3, "amount": "6490.00", "recurring": True},
    "6m": {"label": "6 месяцев", "months": 6, "amount": "11490.00", "recurring": True},
    "12m": {"label": "12 месяцев", "months": 12, "amount": "19900.00", "recurring": True},
}

def _to_decimal(x) -> Decimal:
    """Безопасное преобразование значений из TARIFFS к Decimal."""
    try:
        return Decimal(str(x))
    except Exception:
        return Decimal("0")

def _rub(x) -> str:
    """Форматирование рублей: 12 345.67 → '12 345.67', 19900 → '19 900'."""
    d = _to_decimal(x)
    s = f"{d:,.2f}".replace(",", " ")
    if s.endswith(".00"):
        s = s[:-3]
    return s

def _base_month_amount() -> Decimal:
    """Базовая помесячная цена (из плана '1m')."""
    return _to_decimal(TARIFFS.get("1m", {}).get("amount", "0"))

def _min_plan_amount() -> Decimal:
    """Минимальная конечная стоимость среди всех планов (без trial_amount)."""
    vals = []
    for p in TARIFFS.values():
        vals.append(_to_decimal(p.get("amount", "0")))
    return min(vals) if vals else Decimal("0")

# ──────────────────────────────────────────────────────────────────────────────
# КВОТЫ: бесплатные проходы
# ──────────────────────────────────────────────────────────────────────────────
# 5 проходов на 7*24 часа (скользящее окно)
WEEKLY_PASS_LIMIT = 5
WEEKLY_WINDOW_SEC = 7 * 24 * 60 * 60

def _build_sub_free_text() -> str:
    """SUB_FREE: динамическая минимальная цена и длительность пробного периода из TARIFFS."""
    trial_hours = int(str(TARIFFS.get("1m", {}).get("trial_hours", 72)))
    min_price = _rub(_min_plan_amount())
    return (
        "🎁 Бесплатный период завершён\n"
        f"Пробный доступ на {trial_hours} часа(ов) истёк — дальше только по подписке.\n\n"
        "📦 <b>Что даёт подписка:</b>\n"
        " — Полный доступ ко всем инструментам\n"
        " — Без ограничений по количеству запусков в период подписки*\n"
        f"Стоимость от {min_price} ₽"
    )

SUB_FREE = _build_sub_free_text()

def _build_pay_nothing_text() -> str:
    """PAY_NOTHING: фраза про trial собирается из trial_amount и trial_hours в '1m'."""
    plan = TARIFFS.get("1m", {})
    trial_amt = _rub(plan.get("trial_amount", "1"))
    trial_hours = int(str(plan.get("trial_hours", 72)))
    duration = f"{trial_hours // 24} дня" if trial_hours % 24 == 0 else f"{trial_hours} часов"
    return (
        "Упс… Кажется ваш лимит из 5 пробных генераций закончился.\n\n"
        "Мы видим, что вы активно пользуетесь нашими Инструментами.\n"
        f"Чтобы продолжать пользоваться ими дальше, дарим 🎁 безлимитную подписку на {duration} всего за {trial_amt} ₽!\n"
        "Оформляй и пользуйся без ограничений 👇"
    )

PAY_NOTHING = (_build_pay_nothing_text())

def _build_sub_pay_text() -> str:
    """SUB_PAY: динамическая минимальная цена из TARIFFS."""
    min_price = _rub(_min_plan_amount())
    return (
        "🪫 Подписка не активна\n"
        "Срок подписки истёк или не был оформлен.\n\n"
        "📦 <b>Что даёт подписка:</b>\n"
        " — Полный доступ ко всем инструментам\n"
        " — Без ограничений по количеству запусков в период подписки*\n"
        f"Стоимость от {min_price} ₽"
    )

SUB_PAY = _build_sub_pay_text()

def _build_rates_text() -> str:
    """Сборка блока тарифов на основе TARIFFS с автоматическим пересчётом цен/скидок."""
    # Заголовок с trial
    plan1 = TARIFFS.get("1m", {})
    trial_amt = plan1.get("trial_amount")
    trial_hours = int(str(plan1.get("trial_hours", 72)))
    if trial_amt is not None:
        trial_amt_s = _rub(trial_amt)
        duration = f"{trial_hours // 24} дня" if trial_hours % 24 == 0 else f"{trial_hours} часов"
        trial_part = f"Оформи пробную подписку на {duration} всего за {trial_amt_s} ₽,\nа далее выбери удобный абонемент:\n"
    else:
        trial_part = "Выбери удобный абонемент:\n"

    # Строки тарифов
    base_m = _base_month_amount()
    items = []
    discounts = {}
    for code, p in sorted(TARIFFS.items(), key=lambda kv: kv[1].get("months", 0)):
        label = p.get("label", code)
        months = int(p.get("months", 1))
        amount = _to_decimal(p.get("amount", "0"))
        base_total = (base_m * months).quantize(Decimal("0.01"))
        if months > 1 and amount < base_total:
            discounts[code] = (base_total - amount)
            line = f"{label} — <s>{_rub(base_total)} ₽</s> => {_rub(amount)} ₽"
        else:
            line = f"{label} — {_rub(amount)} ₽"
        items.append((code, line))

    # Пометим 🔥 самый выгодный дисконт
    if discounts:
        best_code = max(discounts.items(), key=lambda kv: kv[1])[0]
        items = [(c, (l + " 🔥") if c == best_code else l) for c, l in items]

    lines = "\n".join(l for _, l in items)
    return (
        "🎁 Хочешь получить полный доступ ко всем инструментам без ограничений?\n"
        f"{trial_part}\n{lines}\n"
    )

RATES_TEXT = _build_rates_text()

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

def _had_trial(user_id: int) -> bool:
    """True, если пробный период когда-либо выдавался (есть trial_until в БД)."""
    try:
        return app_db.get_trial_until(user_id) is not None
    except Exception:
        return False

def _had_subscription(user_id: int) -> bool:
    """True, если у пользователя когда-либо была запись подписки (любой статус)."""
    try:
        from bot.utils.billing_db import SessionLocal, Subscription
        with SessionLocal() as s:
            return s.query(Subscription).filter(Subscription.user_id == user_id).first() is not None
    except Exception:
        return False

def format_access_text(user_id: int) -> str:
    """
    Короткий статус доступа для стартовых экранов инструментов.
    """
    try:
        hours = app_db.trial_remaining_hours(user_id)
    except Exception:
        hours = 0
    if app_db.is_trial_active(user_id):
        try:
            until_dt = app_db.get_trial_until(user_id)
        except Exception:
            until_dt = None
        if until_dt:
            return f"🆓 Бесплатный доступ активен до <b>{until_dt.date().isoformat()}</b> (~{hours} ч.)"
        return f"🆓 Бесплатный доступ активен ещё <b>~{hours} ч.</b>"
    # Подписка/грейс
    try:
        from bot.utils.billing_db import SessionLocal, Subscription
        now = datetime.now(timezone.utc)
        with SessionLocal() as s:
            rec = (
                s.query(Subscription)
                .filter(Subscription.user_id == user_id, Subscription.status == "active")
                .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                .first()
            )
            if rec:
                if rec.next_charge_at and rec.next_charge_at > now:
                    return "✅ Подписка активна"
                fails = int(rec.consecutive_failures or 0)
                if fails < 3:
                    return f"🕊️ Грейс-период: ожидаем оплату (попыток: {fails}/6)"
    except Exception:
        pass
    # Не активен пробный период и нет активной карты.
    # Если пробный период ранее был — сообщаем, что он завершён.
    if _had_trial(user_id):
        return "😢 Бесплатный период завершён."
    # Если ранее была подписка — сообщаем, что она не активна.
    if _had_subscription(user_id):
        return "🪫 Подписка не активна."
    # Ничего не было — ничего «не завершилось»: возвращаем пустую строку.
    return ""


def has_access(user_id: int) -> bool:
    try:
        if app_db.is_trial_active(user_id):
            return True
        return _has_paid_or_grace_access(user_id)
    except Exception:
        return False


async def _try_free_pass(user_id: int) -> bool:
    """
    Пытаемся списать один бесплатный «проход» из недельной квоты.
    Возвращает True, если квота ещё не исчерпана (проход засчитан).
    """
    try:
        ok, _, _ = await quota_repo.try_consume(
            user_id,
            scope="access",               # общий скоуп доступа к инструментам
            limit=WEEKLY_PASS_LIMIT,      # 5 проходов
            window_sec=WEEKLY_WINDOW_SEC  # 7 дней
        )
        return ok
    except Exception:
        logger.exception("Free pass quota check failed for user %s", user_id)
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
    # Бесплатные проходы: если квота не исчерпана — пропускаем пользователя
    if await _try_free_pass(user_id):
        return True
    # Приоритет: если когда-либо была подписка (и сейчас нет) — показываем про подписку.
    # Иначе, если был пробный период — показываем про завершённый пробный период.
    # Иначе — общий экран подписки.
    if _had_subscription(user_id):
        text = SUB_PAY
    elif _had_trial(user_id):
        text = SUB_FREE
    else:
        text = PAY_NOTHING
    try:
        if isinstance(evt, CallbackQuery):
            await _edit_safe(evt, text, SUBSCRIBE_KB)
        else:
            await evt.answer(text, reply_markup=SUBSCRIBE_KB, parse_mode="HTML")
    except Exception:
        pass
    return False


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

def _build_settings_text(user_id: int) -> str:
    """
    Единая сборка текста для экрана /settings:
    - Статус (пробный период/активна/грейс/неактивна)
    - Платёжные данные (карта, СБП) или «не привязаны»
    Формат: HTML (совместим с _edit_safe и .answer(parse_mode="HTML")).
    """
    # 1) Статус
    now = datetime.now(timezone.utc)
    try:
        if app_db.is_trial_active(user_id):
            until = app_db.get_trial_until(user_id)
            if until:
                status_line = f"пробный период до {until.date().isoformat()}"
            else:
                status_line = "пробный период активен"
        else:
            from bot.utils.billing_db import SessionLocal, Subscription
            with SessionLocal() as s:
                rec = (
                    s.query(Subscription)
                    .filter(Subscription.user_id == user_id, Subscription.status == "active")
                    .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                    .first()
                )
                if rec and rec.next_charge_at and rec.next_charge_at > now:
                    status_line = "активна"
                elif rec and int(rec.consecutive_failures or 0) < 3:
                    fails = int(rec.consecutive_failures or 0)
                    status_line = f"грейс-период (попыток {fails}/6)"
                else:
                    status_line = "неактивна"
    except Exception:
        status_line = "неактивна"

    # 2) Платёжные методы
    try:
        methods = billing_db.list_user_payment_methods(user_id)
    except Exception:
        methods = []
    has_card = any((m.get("provider") == "bank_card") for m in methods)
    has_sbp  = any((m.get("provider") == "sbp") for m in methods)
    pm_lines: list[str] = []
    if has_card:
        card = billing_db.get_user_card(user_id) or {}
        suffix = f"{(card.get('brand') or '').upper()} ••••{card.get('last4', '')}"
        pm_lines.append(f"Карта: {escape(suffix)}")
    if has_sbp:
        pm_lines.append("СБП: привязана")
    if not pm_lines:
        pm_lines.append("не привязаны")

    text = (
        "⚙️ <b>Настройки подписки</b>\n"
        "Здесь можно управлять подпиской и удалять привязанные платёжные методы.\n\n"
        f"<b>Статус:</b> {escape(status_line)}\n"
        f"<b>Платёжные данные:</b> " + "; ".join(pm_lines)
    )
    return text

def kb_rates(user_id: Optional[int] = None) -> InlineKeyboardMarkup:
    """
    Строит клавиатуру тарифов.
    Кнопка "🎁 3 дня за 1₽" показывается только если доступен пробный период (кулдаун 90 дней прошёл).
    """
    rows: List[List[InlineKeyboardButton]] = []
    
    # Кнопка "🎁 3 дня за 1₽" показывается только если доступен пробный период
    if user_id is not None and app_db.is_trial_allowed(user_id, cooldown_days=90):
        rows.append([InlineKeyboardButton(text="🎁 3 дня за 1₽", callback_data="sub:choose:1m")])
    
    rows.extend([
        [
            InlineKeyboardButton(text="1 месяц", callback_data="sub:choose:1m"),
            InlineKeyboardButton(text="3 месяца", callback_data="sub:choose:3m"),
            InlineKeyboardButton(text="6 месяцев", callback_data="sub:choose:6m"),
        ],
        [InlineKeyboardButton(text="12 месяцев", callback_data="sub:choose:12m")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _trial_status_line(user_id: int) -> Optional[str]:
    """Возвращает строку статуса, если активен пробный период."""
    try:

        until = app_db.get_trial_until(user_id)
        if until and app_db.is_trial_active(user_id):
            return f"Статус: до {until.date().isoformat()} (пробный период)"
    except Exception:
        pass
    return None


def kb_settings_main(user_id: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []

    # Платёжные методы (карта/СБП) — только действия (никаких noop-информеров)
    try:
        methods = billing_db.list_user_payment_methods(user_id)
    except Exception:
        methods = []
    has_card = any((m.get("provider") == "bank_card") for m in methods)
    has_sbp  = any((m.get("provider") == "sbp") for m in methods)

    if has_card:
        card = billing_db.get_user_card(user_id) or {}
        suffix = f"{(card.get('brand') or '').upper()} ••••{card.get('last4', '')}"
        rows.append([InlineKeyboardButton(text=f"🗑️ Удалить карту ({suffix})", callback_data="sub:cancel_all")])
    if has_sbp:
        rows.append([InlineKeyboardButton(text="🗑️ Удалить СБП-привязку", callback_data="sub:cancel_sbp")])

    rows.append([InlineKeyboardButton(text="⬅️ К тарифам", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_cancel_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить карту", callback_data="sub:cancel_yes")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sub:cancel_no")],
    ])


def kb_cancel_sbp_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить СБП", callback_data="sub:cancel_sbp_yes")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="sub:cancel_sbp_no")],
    ])


def kb_pay_with_consent(*, consent: bool, pay_url_card: Optional[str], pay_url_sbp: Optional[str]) -> InlineKeyboardMarkup:
    check = "✅ Я ознакомлен и согласен" if consent else "⬜️ Я ознакомлен и согласен"
    rows: List[List[InlineKeyboardButton]] = [[InlineKeyboardButton(text=check, callback_data="tos:toggle")]]
    if consent:
        if pay_url_sbp:
            rows.append([InlineKeyboardButton(text="🌫 СБП", url=pay_url_sbp)])
        if pay_url_card:
            rows.append([InlineKeyboardButton(text="💳 Карта ", url=pay_url_card)])

    rows.append([InlineKeyboardButton(text="⬅️ Выбрать другой тариф", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────────────────────────────────────────────────────────────────────────
# HELPER: оффер пробного периода 3 дня за 1 ₽ с прямой кнопкой оплаты (без чекбокса)
# ──────────────────────────────────────────────────────────────────────────────
def build_trial_offer(user_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """
    Строит текст и клавиатуру с кнопкой «💳 Активировать за 1 ₽» (пробный доступ 3 дня),
    как первый тариф 1m с пробным периодом. Ссылка — результат youmoney.create_pay_ex.
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

    # Кулдаун 90 дней на повторный пробный период/покупку за 1 рубль (учитывает покупки подписки)
    if not app_db.is_trial_allowed(user_id, cooldown_days=90):
        pay_url = None
    else:
        try:
            pay_url = youmoney.create_pay_ex(
                user_id=user_id,
                amount_rub=plan.get("trial_amount", "1.00"),
                description=f"{description} (пробный период)",
                metadata=meta,
                save_payment_method=True,
            )
        except Exception as e:
            logger.error("Trial recurring not available for user %s: %s", user_id, e)
            pay_url = None

    # Динамическая фраза про trial и базовую помесячную цену
    trial_hours = int(str(plan.get("trial_hours", 72)))
    duration = f"{trial_hours // 24} дня" if trial_hours % 24 == 0 else f"{trial_hours} часов"
    text = (
        f"🎁 Спасибо за подписку, наш подарок для тебя — все инструменты на {duration} за {_rub(plan.get('trial_amount', '1'))} ₽.\n\n"
        f"После этого подписка автоматически продлевается — {_rub(_base_month_amount())} ₽/мес."
    )
    kb_rows: List[List[InlineKeyboardButton]] = []

    if pay_url:
        kb_rows.append([InlineKeyboardButton(text="💳 Активировать за 1 ₽", url=pay_url)])
        kb_rows.append([InlineKeyboardButton(text="❌ Отказаться", callback_data="main")])

    else:
        # пробный период недоступен или рекуррент недоступен — отправляем к тарифам
        kb_rows.append([InlineKeyboardButton(text="⬅️ Выбрать тариф", callback_data="show_rates")])
        text = (
            "❗ Пробный доступ уже активировался ранее. "
            "Выберите тариф и продолжите пользоваться инструментами."
        )

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    return text, kb


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

async def _edit_safe(cb: CallbackQuery, text: str, kb: InlineKeyboardMarkup | None = None) -> Optional[int]:
    msg_id: Optional[int] = None
    # Telegram HTML не поддерживает <br>. Нормализуем в перевод строки.
    def _norm_html(s: str) -> str:
        if not isinstance(s, str):
            return s
        s = s.replace("<br/>", "\n").replace("<br />", "\n").replace("<br>", "\n")
        return s
    text = _norm_html(text)
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


async def membership_invite(user_id: int) -> None:
    """
    Запрос в membership-service: попытка добавить/пригласить пользователя в чат.
    """
    url = f"{MEMBERSHIP_BASE_URL}/members/invite"
    payload = {"user_id": int(user_id)}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post(url, json=payload)
            if r.status_code >= 400:
                logging.warning("membership invite failed: user_id=%s status=%s body=%s",
                                user_id, r.status_code, r.text)
    except Exception as e:
        logging.exception("membership invite exception for user_id=%s: %s", user_id, e)


async def _membership_remove(user_id: int) -> None:
    """
    Запрос в membership-service: полное удаление пользователя из чата.
    """
    url = f"{MEMBERSHIP_BASE_URL}/members/remove"
    payload = {"user_id": int(user_id)}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post(url, json=payload)
            if r.status_code >= 400:
                logging.warning("membership remove failed: user_id=%s status=%s body=%s",
                                user_id, r.status_code, r.text)
    except Exception as e:
        logging.exception("membership remove exception for user_id=%s: %s", user_id, e)


def _compute_next_time_from_months(months: int) -> datetime:
    try:
        from dateutil.relativedelta import relativedelta
        return datetime.now(timezone.utc) + relativedelta(months=+months)
    except Exception:
        return datetime.now(timezone.utc) + timedelta(days=30 * months)

def _has_paid_or_grace_access(user_id: int) -> bool:
    """
    Доступ считается активным, если:
      • следующий платёж ещё не наступил (next_charge_at > now), ИЛИ
      • оплаченный период закончился, но не завершены 3 неудачные попытки списания
        (consecutive_failures < 3) — «грейс-период».
    """
    try:
        from bot.utils.billing_db import SessionLocal, Subscription
        now = datetime.now(timezone.utc)
        with SessionLocal() as s:
            rec = (
                s.query(Subscription)
                .filter(Subscription.user_id == user_id, Subscription.status == "active")
                .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                .first()
            )
            if not rec:
                return False
            if rec.next_charge_at and rec.next_charge_at > now:
                return True  # оплаченный период ещё идёт
            # оплаченный период закончился — смотрим кол-во попыток
            fails = int(rec.consecutive_failures or 0)
            return fails < 3
    except Exception:
        return False


def _create_links_for_selection(user_id: int) -> tuple[Optional[str], Optional[str]]:
    """
    Генерирует ссылки оплаты (карта/СБП) для ранее выбранного тарифа в _PENDING_SELECTION[user_id].
    Возвращает (pay_url_card, pay_url_sbp).
    ВАЖНО: для пробного периода «3 дня за 1 ₽» разрешаем ТОЛЬКО рекуррентные платежи (с сохранением метода).
    Никаких фолбэков на безтокенные/разовые сценарии — если рекуррент недоступен, ссылка = None.
    """
    sel = _PENDING_SELECTION.get(user_id) or {}
    code = sel.get("code")
    description = sel.get("description") or "Оплата подписки"
    plan = _plan_by_code(code) if code else None

    if not plan:
        return (None, None)

    meta_base = {
        "user_id": str(user_id),
        "plan_code": code,
        "months": str(plan["months"]),
        "v": "2",
        "trial_hours": str(plan.get("trial_hours", 72)),
        "plan_amount": plan["amount"],
    }
    # Решаем: пробный период или полный платеж
    has_trial = bool(plan.get("trial_amount"))
    is_recurring = "1" if plan.get("recurring") else "0"
    if has_trial:
        first_amount = plan["trial_amount"]
        phase = "trial"
        desc_suffix = " (пробный период)"
        # Проверяем кулдаун 90 дней для пробного периода за 1 рубль
        if not app_db.is_trial_allowed(user_id, cooldown_days=90):
            return (None, None)  # Кулдаун не прошёл - не создаём ссылки
    else:
        first_amount = plan["amount"]
        phase = "renewal"  # первый полный платёж трактуем как период подписки
        desc_suffix = ""

    # 1) Карта (РЕКУРРЕНТ ТОЛЬКО): без фолбэков на разовую оплату.
    try:
        pay_url_card = youmoney.create_pay_ex(
            user_id=user_id,
            amount_rub=first_amount,
            description=f"{description}{desc_suffix}",
            metadata={**meta_base, "phase": phase, "is_recurring": is_recurring},
            save_payment_method=bool(plan.get("recurring")),
            payment_method_type="bank_card",
        )
    except Exception as e:
        logger.error("Card recurring not allowed — declining tokenless trial: %s", e)
        pay_url_card = None

    # 2) СБП (РЕКУРРЕНТ ТОЛЬКО): без фолбэков на разовую оплату.
    try:
        pay_url_sbp = youmoney.create_pay_ex(
            user_id=user_id,
            amount_rub=first_amount,
            description=f"{description}{desc_suffix if desc_suffix else ''}",
            metadata={**meta_base, "phase": phase, "is_recurring": is_recurring},
            save_payment_method=bool(plan.get("recurring")),
            payment_method_type="sbp",
        )
    except ForbiddenError as e:
        logger.error("SBP recurring not allowed — declining tokenless trial: %s", e)
        pay_url_sbp = None
    except Exception as e:
        logger.error("SBP recurring flow failed: %s", e)
        pay_url_sbp = None

    return (pay_url_card, pay_url_sbp)


# ──────────────────────────────────────────────────────────────────────────────
# PUBLIC: Показ тарифов / выбор тарифа / ссылка на оплату
# ──────────────────────────────────────────────────────────────────────────────

async def show_rates(evt: Message | CallbackQuery) -> None:
    user_id = evt.from_user.id if isinstance(evt, CallbackQuery) else evt.from_user.id
    if isinstance(evt, CallbackQuery):
        await _edit_safe(evt, RATES_TEXT, kb_rates(user_id))
    else:
        await evt.answer(RATES_TEXT, reply_markup=kb_rates(user_id), parse_mode="HTML")


async def choose_rate(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    try:
        _, _, code = cb.data.split(":", 2)  # sub:choose:<code>
    except Exception:
        await _edit_safe(cb, "Не удалось определить тариф. Попробуйте ещё раз.", kb_rates(user_id))
        return

    plan = _plan_by_code(code)
    if not plan:
        await _edit_safe(cb, "Такого тарифа нет. Выберите из списка.", kb_rates(user_id))
        return

    # Проверяем кулдаун для пробного периода за 1 рубль (только для тарифа 1m с trial_amount)
    has_trial = bool(plan.get("trial_amount"))
    if has_trial and code == "1m":
        if not app_db.is_trial_allowed(user_id, cooldown_days=90):
            await _edit_safe(
                cb,
                "❗ Повторный пробный доступ доступен раз в 90 дней после последней покупки. Сейчас оформить пробный период нельзя. Выберите тариф и оформите подписку.",
                kb_rates(user_id)
            )
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
        # Если пробный период запрещён (кулдаун) — _create_links_for_selection() вернёт (None, None)
        # В этом случае просто не показываем кнопки оплаты
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
                # Записываем событие неуспешного платежа в БД
                try:
                    app_db.event_add(user_id_fail, f"PAYMENT:FAIL status={status} payment_id={payment_id}")
                except Exception:
                    logger.warning("Failed to log payment fail event for user %s", user_id_fail)
                # ⚡ сбрасываем кэш «payment_ok» при любом финальном фейле
                try:
                    await invalidate_payment_ok_cache(user_id_fail)
                except Exception:
                    logger.warning("invalidate_payment_ok_cache failed (fail branch) for user %s", user_id_fail)
                try:
                    # троттлинг: не чаще 1 раза за 12ч
                    can_notice = True
                    should_remove = False  # сигнал «достигли 3-й подряд неудачи»
                    if sub_id:
                        from bot.utils.billing_db import SessionLocal, Subscription, now_utc
                        from sqlalchemy import select
                        with SessionLocal() as s, s.begin():
                            rec = s.get(Subscription, sub_id)
                            if rec:
                                now = now_utc()
                                if rec.last_fail_notice_at and (now - rec.last_fail_notice_at) < timedelta(hours=12):
                                    can_notice = False
                                # инкремент фейлов (не более 6) + детекция порога 3
                                prev_fails = int(rec.consecutive_failures or 0)
                                new_fails = min(prev_fails + 1, 6)
                                rec.consecutive_failures = new_fails
                                if new_fails >= 3 and prev_fails < 3:
                                    should_remove = True
                                if can_notice:
                                    rec.last_fail_notice_at = now
                                rec.updated_at = now
                    # если достигли 3-й неудачи — инициируем полное удаление из чата
                    if should_remove:
                        try:
                            asyncio.create_task(_membership_remove(user_id_fail))
                        except Exception:
                            pass
                    if can_notice:
                        cover_path = get_file_path("data/img/bot/no_pay.png")
                        photo = FSInputFile(cover_path)
                        caption = (
                            "❌ *Оплата не прошла*\n\n"
                            "Платёж был отменён или не завершён.\n"
                            "Если списания не было — попробуйте оплатить снова из раздела тарифов."
                        )
                        ##await bot.send_photo(chat_id=user_id_fail, photo=photo, caption=caption, parse_mode="Markdown")
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

        # Убедимся, что пользователь есть в app DB (для пробного периода/истории)
        app_db.check_and_add_user(user_id)

        # Записываем событие успешного платежа в БД
        try:
            app_db.event_add(user_id, f"PAYMENT:SUCCESS status={status} payment_id={payment_id} phase={phase} plan={code}")
        except Exception:
            logger.warning("Failed to log payment success event for user %s", user_id)

        # Успешные сценарии
        if is_recurring and phase == "trial":
            # 1) сохраняем карту в справочник (id не нужен в подписке; храним токен провайдера)
            if pm_token:
                billing_db.card_upsert_from_provider(
                    user_id=user_id, provider=pmethod.get("type", "yookassa"),
                    pm_token=pm_token, brand=brand, first6=first6, last4=last4,
                    exp_month=exp_month, exp_year=exp_year,
                )
            # 2) включаем пробный период доступа
            trial_hours = int(str(metadata.get("trial_hours") or "72"))
            trial_until = app_db.set_trial(user_id, hours=trial_hours)  # datetime (UTC)
            # 3) создаём/обновляем подписку с next_charge_at после пробный периода
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
            # попытка инициализировать добавление/приглашение в чат
            try:
                asyncio.create_task(membership_invite(user_id))
            except Exception:
                pass

        elif is_recurring and phase == "renewal":
            # Сохраняем платёжный метод (чтобы было автопродление)
            if pm_token:
                billing_db.card_upsert_from_provider(
                    user_id=user_id, provider=pmethod.get("type", "yookassa"),
                    pm_token=pm_token, brand=brand, first6=first6, last4=last4,
                    exp_month=exp_month, exp_year=exp_year,
                )
            # переносим next_charge_at вперёд на период тарифа
            next_at = _compute_next_time_from_months(months)
            updated_sub_id = billing_db.subscription_mark_charged_for_user(user_id=user_id, next_charge_at=next_at)
            if not updated_sub_id:
                # если нет подписки (крайний случай) — создадим
                billing_db.subscription_upsert(
                    user_id=user_id, plan_code=code, interval_months=months,
                    amount_value=TARIFFS.get(code, {}).get("amount", "0.00"),
                    amount_currency=str(obj.get("amount", {}).get("currency") or "RUB"),
                    payment_method_id=pm_token,  # знаем токен из текущего события
                    next_charge_at=next_at, status="active",
                )
            else:
                # если подписка есть, но карта ещё не привязана — привяжем
                try:
                    from bot.utils.billing_db import SessionLocal, Subscription, now_utc
                    with SessionLocal() as s, s.begin():
                        rec = (
                            s.query(Subscription)
                            .filter(Subscription.user_id == user_id, Subscription.status == "active")
                            .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                            .first()
                        )
                        if rec and not rec.payment_method_id and pm_token:
                            rec.payment_method_id = pm_token
                            rec.updated_at = now_utc()
                except Exception:
                    pass
            # уведомление с «до …» брать из next_at
            await _notify_after_payment(bot, user_id, code, next_at.date().isoformat())
            # попытка инициализировать добавление/приглашение в чат
            try:
                asyncio.create_task(membership_invite(user_id))
            except Exception:
                pass
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
            # Не рекуррентный кейс (включая trial_tokenless): только пробный период.
            trial_hours = int(str(metadata.get("trial_hours") or "72"))
            trial_until = app_db.set_trial(user_id, hours=trial_hours)
            await _notify_after_payment(bot, user_id, code, trial_until.date().isoformat())
            # попытка инициализировать добавление/приглашение в чат
            try:
                asyncio.create_task(membership_invite(user_id))
            except Exception:
                pass

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
                "• Добавили Вас в канал с контентом для Ваших соц.сетей!\n"
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
    text = _build_settings_text(user_id)
    await msg.answer(text, reply_markup=kb_settings_main(user_id), parse_mode="HTML")


async def cancel_request(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    card = billing_db.get_user_card(user_id) or {}
    suffix = f"{(card.get('brand') or '').upper()} ••••{card.get('last4', '')}"
    # HTML: жирный заголовок + экранирование
    text = (
        f"Удалить карту <b>{escape(suffix)}</b>?<br><br>"
        "• Автосписания прекратятся.<br>"
        "• Подписка НЕ отменяется, доступ останется до оплаченной даты.<br>"
        "• Данные карты будут удалены."
    )
    await _edit_safe(cb, text, kb_cancel_confirm())


async def cancel_no(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    await _edit_safe(cb, _build_settings_text(uid), kb_settings_main(uid))


async def cancel_yes(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    try:
        affected = billing_db.delete_user_card_and_detach_subscriptions(user_id=user_id)
        logger.info("Card deleted for user %s; detached from %s subscriptions", user_id, affected)
    except Exception:
        logger.exception("Failed to delete card for user %s", user_id)
        await _edit_safe(cb, "Не удалось удалить карту. Попробуйте позже.", kb_settings_main(user_id))
        return
    success = "✅ Карта удалена. Автосписания остановлены. Подписка не отменена.\n\n"
    await _edit_safe(cb, success + _build_settings_text(user_id), kb_settings_main(user_id))


async def cancel_sbp_request(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    # Текст подтверждения без маски номера (для СБП нет last4/brand)
    text = (
        "Удалить привязку <b>СБП</b>?\n\n"
        "• Автосписания по СБП прекратятся.\n"
        "• Подписка НЕ отменяется, доступ останется до оплаченной даты.\n"
        "• Привязка СБП будет удалена."
    )
    await _edit_safe(cb, text, kb_cancel_sbp_confirm())


async def cancel_sbp_no(cb: CallbackQuery) -> None:
    uid = cb.from_user.id
    await _edit_safe(cb, _build_settings_text(uid), kb_settings_main(uid))


async def cancel_sbp_yes(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    try:
        affected = billing_db.delete_user_sbp_and_detach_subscriptions(user_id=user_id)
        logger.info("SBP deleted for user %s; detached from %s subscriptions", user_id, affected)
    except Exception:
        logger.exception("Failed to delete SBP for user %s", user_id)
        await _edit_safe(cb, "Не удалось удалить СБП. Попробуйте позже.", kb_settings_main(user_id))
        return
    success = "✅ СБП-привязка удалена. Автосписания по СБП остановлены. Подписка не отменена.\n\n"
    await _edit_safe(cb, success + _build_settings_text(user_id), kb_settings_main(user_id))


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
    rows: List[List[InlineKeyboardButton]] = [[]]
    for code, label in _upgrade_options_from(cur_code):
        rows.append([InlineKeyboardButton(text=f"Повысить до: {label}", callback_data=f"sub:upgrade:{code}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад к тарифам", callback_data="show_rates")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def open_manage(cb: CallbackQuery) -> None:
    user_id = cb.from_user.id
    # Управление доступно, если есть активный пробный период или оплаченный/грейс-доступ
    if not (app_db.is_trial_active(user_id) or _has_paid_or_grace_access(user_id)):
        await _edit_safe(cb, "Подписка не активна. Выберите тариф для оформления:", kb_rates(user_id))
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

    # удаление СБП
    rt.callback_query.register(cancel_sbp_request, F.data == "sub:cancel_sbp")
    rt.callback_query.register(cancel_sbp_yes, F.data == "sub:cancel_sbp_yes")
    rt.callback_query.register(cancel_sbp_no, F.data == "sub:cancel_sbp_no")