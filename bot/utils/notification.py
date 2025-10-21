# smart_agent/bot/utils/notification.py
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
from zoneinfo import ZoneInfo
from sqlalchemy import func

from bot.utils import database as app_db
from bot.utils import billing_db
from bot.utils.mailing import send_last_published_to_chat  # обёртка на "последний пост"
from bot.utils.redis_repo import set_nx_with_ttl
from bot.config import get_file_path

MSK = ZoneInfo("Europe/Moscow")
_ANTI_SPAM_TTL_SEC = 14 * 24 * 3600  # 14 дней
_BEFORE_AFTER_IMG_REL_DESIGN = "img/bot/before_after_design.jpg"  # универсальная заглушка «было-стало»
_BEFORE_AFTER_IMG_REL_PLANS = "img/bot/before_after_plans.jpg"

# Временное окно (часы), в которое разрешена отправка шага после достижения порога
_SEND_WINDOW_HOURS = 12.0
# ──────────────────────────────────────────────────────────────────────────────
# Тексты (обновлённые)
# ──────────────────────────────────────────────────────────────────────────────

# Сценарий: пользователь поработал с ботом, но не подписался
BEFORE_AFTER_PLANS = (
"Ребята, делимся с вами самым сокровенным! 🔥"
"Посмотрите — всё это создал наш бот."

"Такого в России ещё никто не делал!"
"Уже на следующей неделе каждый подписчик нашего канала получит бесплатный доступ к нашей технологии обрисовки планировок."

"Поддержите нас реакциями❤️🔥"
"Ваша поддержка вдохновляет нас делать ещё круче!"
)
TXT_UNSUB_D1 = (
    "👉 Ежедневный контент для твоих соцсетей уже готов — бери и публикуй. "
    "Зацени, вот это мы отправляли нашим риэлторам на прошлой неделе 👇"
)
TXT_UNSUB_D2 = (
    "👉 Всё ещё выкладываешь на Авито планировки из каменного века? "
    "Наш ИИ-обрисовщик сделает продающую планировку за 30 секунд. "
    "Удобно для презентаций клиентам."
)
TXT_UNSUB_D3 = (
    "👉 Генератор интерьеров: покажи клиенту, как может выглядеть квартира после ремонта. "
    "Это помогает быстрее закрывать сделки 😉\n"
)
TXT_UNSUB_D4 = (
    "👉 Дарим 3 дня бесплатного теста наших Инструментов. Только тссс, больше никому 😉 "
    "Не теряй время — активируй пробный доступ."
)

# Сценарий: пользователь оформил тестовую подписку
TXT_TRIAL_D1_ONBOARD = (
    "👉 Начни с простого: публикуй готовый контент для соцсетей. "
    "Сегодняшний пост уже ждёт тебя в канале с контентом 📲"
)
TXT_TRIAL_D1_2 = (
    "👉 Всё ещё выкладываешь на Авито планировки из каменного века? "
    "Наш ИИ-обрисовщик сделает продающую планировку за 30 секунд. "
    "Удобно для презентаций клиентам."
)
TXT_TRIAL_D2_1 = (
    "👉 Генератор интерьеров: покажи клиенту, как может выглядеть квартира после ремонта. "
    "Это помогает быстрее закрывать сделки 😉 Скорее протестируй!"
)
TXT_TRIAL_D2_2 = (
    "👉 Тратишь полдня, чтобы составить описание к новому объекту? "
    "Мы сделали для тебя бота, который делает продающее описание за 30 секунд! Убедись сам!"
)

# Сценарий: пользователь подписался
TXT_PAID_D3 = (
    "👉 Хочешь удивить клиентов? Покажи им дизайн интерьера их будущей квартиры. "
    "Генератор уже доступен в твоей подписке 😉"
)
TXT_PAID_D5 = (
    "👉 Закрытие возражений — ключ к сделкам. В боте есть инструмент, который поможет "
    "ответить на самые частые вопросы клиентов. Попробуй сегодня!"
)
TXT_PAID_D7 = (
    "👉 Всё ещё выкладываешь на Авито планировки из каменного века? "
    "Наш ИИ-обрисовщик сделает продающую планировку за 30 секунд. "
    "Удобно для презентаций клиентам. Уже попробовал?"
)
TXT_PAID_D10 = (
    "👉 Ты используешь только часть инструментов. Проверь: планировки, звонки, отзывы, описания — "
    "всё это поможет тебе продавать быстрее и дороже 🏡"
)
TXT_PAID_PRE_RENEW = (
    "👉 Мы провели потрясающий месяц вместе! Завтра подписка будет продлена — и у тебя по-прежнему будет "
    "доступ к контенту, планировкам, дизайну и другим инструментам."
)

# ──────────────────────────────────────────────────────────────────────────────
# Утилиты
# ──────────────────────────────────────────────────────────────────────────────

def _format_amount(amount_value: str | None, amount_currency: str | None) -> str:
    """
    Нормализует цену из БД: '2490.00' -> '2490 ₽'; '990' -> '990 ₽'.
    Для не-RUB подставляем код валюты.
    """
    if not amount_value:
        return ""
    try:
        d = Decimal(str(amount_value))
        s = f"{d:.2f}".rstrip("0").rstrip(".")
    except (InvalidOperation, ValueError, TypeError):
        s = str(amount_value)
    cur = (amount_currency or "").upper()
    sym = "₽" if cur in ("RUB", "RUR") else (cur or "")
    return f"{s} {sym}".strip()


def _tariff_name(plan_code: str | None, interval_months: int | None) -> str:
    """
    Преобразует код плана в человекочитаемое имя.
    """
    code = (plan_code or "").lower()
    mapping = {
        "1m": "месячный",
        "3m": "3 месяца",
        "6m": "6 месяцев",
        "12m": "12 месяцев",
    }
    if code in mapping:
        return mapping[code]
    if interval_months and interval_months > 0:
        return f"{interval_months} мес."
    return plan_code or "тариф"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _hours_since(dt: Optional[datetime], now: Optional[datetime] = None) -> float:
    if dt is None:
        return -1.0
    now = now or _utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return (now - dt).total_seconds() / 3600.0

def _within_window(hours_since_baseline: float, threshold_h: float, window_h: float = _SEND_WINDOW_HOURS) -> bool:
    """
    True, если с момента baseline прошло >= threshold_h и < threshold_h + window_h.
    Используем как второй щит от «повторных» пушей при исчезновении Redis-ключа.
    """
    return (hours_since_baseline >= threshold_h) and (hours_since_baseline < (threshold_h + window_h))

async def _send_text_once(bot: Bot, user_id: int, key: str, text: str,
                          *, ttl: int = _ANTI_SPAM_TTL_SEC, disable_preview: bool = False) -> bool:
    """
    Идемпотентная отправка ОДНОГО текстового сообщения (антиспам через Redis).
    """
    try:
        need_send = await set_nx_with_ttl(key, "1", ttl)
    except Exception:
        logging.exception("[notif] redis setnx failed for key=%s", key)
        need_send = False

    if not need_send:
        return False

    try:
        # Явно используем HTML, чтобы корректно отображать <a href="...">...</a>
        await bot.send_message(
            user_id,
            text,
            disable_web_page_preview=disable_preview,
            parse_mode="HTML",
        )
        return True
    except Exception as e:
        logging.warning("[notif] send_message to %s failed: %s", user_id, e)
        return False

async def _send_unsub_d1_with_post(bot: Bot, user_id: int) -> bool:
    """
    D1 (unsub): шлём текст, затем — последний опубликованный пост из Mailings.
    Антидубль — один ключ на весь этап.
    """
    key = f"notif:unsub:{user_id}:d1"
    try:
        need_send = await set_nx_with_ttl(key, "1", _ANTI_SPAM_TTL_SEC)
    except Exception:
        logging.exception("[notif] redis setnx failed for key=%s", key)
        need_send = False

    if not need_send:
        return False

    ok = True
    try:
        await bot.send_message(user_id, TXT_UNSUB_D1)
    except Exception as e:
        ok = False
        logging.warning("[notif] unsub d1 text to %s failed: %s", user_id, e)

    # пробуем отправить «последний пост» даже если текст не удался — это лучше, чем ничего
    try:
        await send_last_published_to_chat(bot, user_id)
    except Exception as e:
        ok = False
        logging.warning("[notif] unsub d1 last-post to %s failed: %s", user_id, e)

    return ok





async def _send_text_with_image_once(
    bot: Bot,
    user_id: int,
    key: str,
    text: str,
    image_rel_path: str = _BEFORE_AFTER_IMG_REL_DESIGN,
    *,
    ttl: int = _ANTI_SPAM_TTL_SEC,
) -> bool:
    """
    Идемпотентно шлёт ОДНО сообщение: фото + подпись (caption) с текстом.
    Плейсхолдер "/пример контента было-стало/" из текста вырезается.
    Если файл изображения недоступен — шлём один текст как fallback.
    """
    try:
        need_send = await set_nx_with_ttl(key, "1", ttl)
    except Exception:
        logging.exception("[notif] redis setnx failed for key=%s", key)
        need_send = False
    if not need_send:
        return False

    clean = (text or "").replace("/пример контента было-стало/", "").strip()

    # Резолвим путь к изображению
    try:
        abs_path = get_file_path(image_rel_path)
    except Exception:
        abs_path = image_rel_path

    # Если картинка есть — отправляем единым сообщением (photo + caption)
    if abs_path and Path(abs_path).exists():
        try:
            await bot.send_photo(
                user_id,
                FSInputFile(abs_path),
                caption=clean if clean else None,
                parse_mode="HTML",
            )
            return True
        except Exception as e:
            logging.warning("[notif] send_photo (caption) to %s failed: %s", user_id, e)

    # Фолбэк: нет изображения — отправляем только текст, чтобы не молчать
    try:
        if clean:
            await bot.send_message(user_id, clean, parse_mode="HTML")
            return True
    except Exception as e:
        logging.warning("[notif] fallback send_message to %s failed: %s", user_id, e)
    return False


def _compose_trial_d3_text(
    *,
    plan_code: str | None,
    interval_months: int | None,
    amount_value: str | None,
    amount_currency: str | None,
    next_charge_at: datetime | None,
    now: Optional[datetime] = None,
) -> str:
    """
    Собирает текст для шага Trial D3 (дожим к оплате) с реальным тарифом и ценой.
    Если next_charge_at попадает на «завтра» по МСК — используем слово «Завтра», иначе указываем дату.
    """
    now = now or _utcnow()
    nca = billing_db.to_aware_utc(next_charge_at) if next_charge_at else None
    plan = _tariff_name(plan_code, interval_months)
    price = _format_amount(amount_value, amount_currency)

    if nca:
        msk_now = now.astimezone(MSK)
        msk_nca = nca.astimezone(MSK)
        is_tomorrow = (msk_nca.date() == (msk_now + timedelta(days=1)).date())
        when = "Завтра" if is_tomorrow else msk_nca.strftime("%d.%m")
        return (
            f"👉 Спасибо, что активно тестировал «Инструменты Риэлтора». "
            f"{when} ты перейдёшь на тариф {plan} за {price} и сможешь пользоваться всеми инструментами без ограничений!"
        )
    # Фоллбэк, если даты нет — без «завтра», но с планом и ценой (если они есть)
    if price:
        return (
            f"👉 Спасибо, что активно тестировал «Инструменты Риэлтора». "
            f"Скоро ты перейдёшь на тариф {plan} за {price} и сможешь пользоваться всеми инструментами без ограничений!"
        )
    return (
        "👉 Спасибо, что активно тестировал «Инструменты Риэлтора». "
        "Скоро ты перейдёшь на оплачиваемый тариф и сможешь пользоваться всеми инструментами без ограничений!"
    )


async def _send_trial_d3_pay_once(bot: Bot, user_id: int) -> bool:
    """
    Идемпотентный отправитель D3-pay для триала с реальными параметрами подписки.
    Берём ближайшую (по next_charge_at) активную подписку пользователя.
    """
    key = f"notif:trial:{user_id}:d3:pay"
    try:
        need_send = await set_nx_with_ttl(key, "1", _ANTI_SPAM_TTL_SEC)
    except Exception:
        logging.exception("[notif] redis setnx failed for key=%s", key)
        need_send = False
    if not need_send:
        return False

    now = _utcnow()
    # ищем ближайшую активную подписку с будущим next_charge_at
    Session = billing_db.SessionLocal
    plan_code = None
    interval_months = None
    amount_value = None
    amount_currency = None
    next_charge_at = None
    with Session() as s:
        rec = (
            s.query(
                billing_db.Subscription.plan_code,
                billing_db.Subscription.interval_months,
                billing_db.Subscription.amount_value,
                billing_db.Subscription.amount_currency,
                billing_db.Subscription.next_charge_at,
            )
            .filter(
                billing_db.Subscription.user_id == user_id,
                billing_db.Subscription.status == "active",
                billing_db.Subscription.next_charge_at != None,  # noqa: E711
                billing_db.Subscription.next_charge_at > now,
            )
            .order_by(billing_db.Subscription.next_charge_at.asc())
            .first()
        )
        if rec:
            (plan_code, interval_months, amount_value, amount_currency, next_charge_at) = rec

    text = _compose_trial_d3_text(
        plan_code=plan_code,
        interval_months=interval_months,
        amount_value=amount_value,
        amount_currency=amount_currency,
        next_charge_at=next_charge_at,
        now=now,
    )
    try:
        await bot.send_message(user_id, text)
        return True
    except Exception as e:
        logging.warning("[notif] trial d3 pay send to %s failed: %s", user_id, e)
        return False

# ──────────────────────────────────────────────────────────────────────────────
# 1) «Взаимодействовал, но не подписался»
# baseline = минимальный app_db.EventLog.created_at; исключаем активные trial/paid
# пороги: D1=24h, D2=48h, D3=72h, D4=96h
# ──────────────────────────────────────────────────────────────────────────────

async def run_unsubscribed_nurture(bot: Bot) -> None:
    now = _utcnow()

    active_trial_ids = set(app_db.list_trial_active_user_ids(now))
    active_paid_ids = set(billing_db.list_active_subscription_user_ids(now))

    Session = app_db.SessionLocal
    with Session() as s:
        rows: List[Tuple[int, datetime]] = (
            s.query(app_db.EventLog.user_id, func.min(app_db.EventLog.created_at))
             .group_by(app_db.EventLog.user_id)
             .all()
        )

    sent = dict(d1=0, d2=0, d3=0, d4=0)
    for uid, first_at in rows:
        if uid in active_trial_ids or uid in active_paid_ids:
            continue
        h = _hours_since(first_at, now)
        if h < 0:
            continue

        if _within_window(h, 24):
            if await _send_unsub_d1_with_post(bot, uid):
                sent["d1"] += 1
        if _within_window(h, 48):
            if await _send_text_once(bot, uid, f"notif:unsub:{uid}:d2", TXT_UNSUB_D2):
                sent["d2"] += 1
        if _within_window(h, 72):
            # D3: «Генератор интерьеров» → отправляем «design»
            if await _send_text_with_image_once(
                bot, uid, f"notif:unsub:{uid}:d3", TXT_UNSUB_D3, image_rel_path=_BEFORE_AFTER_IMG_REL_DESIGN
            ):
                sent["d3"] += 1
        if _within_window(h, 96):
            if await _send_text_once(bot, uid, f"notif:unsub:{uid}:d4", TXT_UNSUB_D4):
                sent["d4"] += 1

    logging.info("[notif][unsub] done: %s", sent)

# ──────────────────────────────────────────────────────────────────────────────
# 2) «Оформил тестовую подписку» (trial)
# baseline = app_db.Trial.created_at; пороги: D1_onboard>=1h, D1_2>=24h, D2_1>=48h, D2_2>=48h, D3_pay>=72h
# ──────────────────────────────────────────────────────────────────────────────

async def run_trial_onboarding(bot: Bot) -> None:
    now = _utcnow()
    trial_ids = app_db.list_trial_active_user_ids(now)
    if not trial_ids:
        logging.info("[notif][trial] no active trial users")
        return

    Session = app_db.SessionLocal
    with Session() as s:
        rows = (
            s.query(app_db.Trial.user_id, app_db.Trial.created_at)
             .filter(app_db.Trial.user_id.in_(trial_ids))
             .all()
        )

    sent = dict(d1_onboard=0, d1_2=0, d2_1=0, d2_2=0, d3_pay=0)
    for uid, created_at in rows:
        h = _hours_since(created_at, now)
        if h < 0:
            continue

        if _within_window(h, 1):
            if await _send_text_once(bot, uid, f"notif:trial:{uid}:d1:onboard", TXT_TRIAL_D1_ONBOARD):
                sent["d1_onboard"] += 1
        if _within_window(h, 24):
            # D1_2: «планировки» → отправляем «plan»
            if await _send_text_with_image_once(
                bot, uid, f"notif:trial:{uid}:d1:2", TXT_TRIAL_D1_2, image_rel_path=_BEFORE_AFTER_IMG_REL_PLANS
            ):
                sent["d1_2"] += 1
        if _within_window(h, 48):
            # D2_1: «генератор интерьеров» → отправляем «design»
            if await _send_text_with_image_once(
                bot, uid, f"notif:trial:{uid}:d2:1", TXT_TRIAL_D2_1, image_rel_path=_BEFORE_AFTER_IMG_REL_DESIGN
            ):
                sent["d2_1"] += 1
            if await _send_text_once(bot, uid, f"notif:trial:{uid}:d2:2", TXT_TRIAL_D2_2):
                sent["d2_2"] += 1
        if _within_window(h, 72):
            if await _send_trial_d3_pay_once(bot, uid):
                sent["d3_pay"] += 1

    logging.info("[notif][trial] done: %s", sent)

# ──────────────────────────────────────────────────────────────────────────────
# 3) «Подписался» (оплаченная подписка)
# baseline = billing_db.Subscription.created_at (последняя активная); пороги: D3=72h, D5=120h, D7=168h, D10=240h
# pre_renew: 0 < (next_charge_at - now) <= 24h
# ──────────────────────────────────────────────────────────────────────────────

async def run_paid_lifecycle(bot: Bot) -> None:
    now = _utcnow()
    active_ids = billing_db.list_active_subscription_user_ids(now)
    if not active_ids:
        logging.info("[notif][paid] no active subscribers")
        return

    Session = billing_db.SessionLocal
    with Session() as s:
        subs: List[Tuple[int, datetime, datetime]] = (
            s.query(
                billing_db.Subscription.user_id,
                billing_db.Subscription.created_at,
                billing_db.Subscription.next_charge_at,
            )
            .filter(
                billing_db.Subscription.user_id.in_(active_ids),
                billing_db.Subscription.status == "active",
                billing_db.Subscription.next_charge_at != None,  # noqa: E711
                billing_db.Subscription.next_charge_at > now,
            )
            .order_by(
                billing_db.Subscription.user_id.asc(),
                billing_db.Subscription.created_at.desc(),
            )
            .all()
        )

    by_user: Dict[int, Tuple[datetime, datetime]] = {}
    for uid, created_at, next_charge_at in subs:
        if uid not in by_user:
            by_user[uid] = (created_at, next_charge_at)

    sent = dict(d3=0, d5=0, d7=0, d10=0, pre=0)
    for uid, (created_at, next_charge_at) in by_user.items():
        h = _hours_since(created_at, now)
        if h < 0:
            continue

        if _within_window(h, 72):
            # D3: «дизайн интерьера» → отправляем «design»
            if await _send_text_with_image_once(
                bot, uid, f"notif:paid:{uid}:d3", TXT_PAID_D3, image_rel_path=_BEFORE_AFTER_IMG_REL_DESIGN
            ):
                sent["d3"] += 1
        if _within_window(h, 120):
            if await _send_text_once(bot, uid, f"notif:paid:{uid}:d5", TXT_PAID_D5):
                sent["d5"] += 1
        if _within_window(h, 168):
            # D7: «планировки» → отправляем «plan»
            if await _send_text_with_image_once(
                bot, uid, f"notif:paid:{uid}:d7", TXT_PAID_D7, image_rel_path=_BEFORE_AFTER_IMG_REL_PLANS
            ):
                sent["d7"] += 1
        if _within_window(h, 240):
            if await _send_text_once(bot, uid, f"notif:paid:{uid}:d10", TXT_PAID_D10):
                sent["d10"] += 1

        if next_charge_at is not None:
            nca = billing_db.to_aware_utc(next_charge_at) or next_charge_at
            delta_s = (nca - now).total_seconds()
            if 0 < delta_s <= 24 * 3600:
                epoch_key = int(nca.timestamp())
                if await _send_text_once(bot, uid, f"notif:paid:{uid}:pre:{epoch_key}", TXT_PAID_PRE_RENEW):
                    sent["pre"] += 1

    logging.info("[notif][paid] done: %s", sent)

# ──────────────────────────────────────────────────────────────────────────────
# Единый шедулер
# ──────────────────────────────────────────────────────────────────────────────

async def run_notification_scheduler(bot: Bot) -> None:
    """
    Запускайте по cron/APScheduler каждые 10–30 минут.
    """
    try:
        await run_unsubscribed_nurture(bot)
    except Exception:
        logging.exception("[notif] unsubscribed_nurture failed")

    try:
        await run_trial_onboarding(bot)
    except Exception:
        logging.exception("[notif] trial_onboarding failed")

    try:
        await run_paid_lifecycle(bot)
    except Exception:
        logging.exception("[notif] paid_lifecycle failed")
