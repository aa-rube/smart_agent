#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\run.py
import asyncio
import logging
import os
import signal
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
import httpx

from bot import setup
from bot.config import *
import bot.utils.database as db
import bot.utils.billing_db as billing_db
from bot.utils.mailing import run_mailing_scheduler
from bot.utils.notification import run_notification_scheduler

from bot.handlers.payment_handler import process_yookassa_webhook
from bot.utils import youmoney
from bot.utils.time_helpers import now_msk
from bot.handlers.description_playbook import register_http_endpoints


bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher(storage=MemoryStorage())
setup(dp)

# Флаг для graceful shutdown
shutdown_event = asyncio.Event()

# Приведение дат к UTC-aware (на случай, если из БД пришёл naive)
def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

# ──────────────────────────────────────────────────────────────────────────────
# Membership Enforcer: настройки
# ──────────────────────────────────────────────────────────────────────────────
# Базовый URL membership-сервиса (по умолчанию локально)
MEMBERSHIP_BASE_URL = os.getenv("MEMBERSHIP_BASE_URL", "http://127.0.0.1:6000")
# Как часто сканировать просроченные подписки, сек
ENFORCER_INTERVAL_SEC = int(os.getenv("MEMBERSHIP_ENFORCER_INTERVAL_SEC", "900"))  # 15 минут
# Кулдаун между повторными попытками remove одного и того же пользователя, часы
REMOVAL_COOLDOWN_HOURS = int(os.getenv("MEMBERSHIP_REMOVAL_COOLDOWN_HOURS", "24"))
# Память последних попыток удаления (анти-спам при неудачах/повторах)
_last_removal_attempt: dict[int, datetime] = {}


async def yookassa_webhook_handler(request: web.Request):
    try:
        data = await request.json()
        logging.info(f"YooKassa webhook received: {data}")

        # Централизованный вызов
        status, msg = await process_yookassa_webhook(bot, data)
        if status != 200:
            logging.warning("Webhook not OK: %s", msg)
        return web.Response(status=status)

    except Exception as e:
        logging.error(f"Error processing YooKassa webhook: {e}")
        return web.Response(status=500)


def _has_active_paid_period_strict(user_id: int) -> bool:
    """
    Строгое состояние «оплачено»: есть запись подписки со статусом 'active'
    и next_charge_at > сейчас (то есть оплаченный период ещё идёт).
    Грейс-периоды сюда не входят.
    """
    try:
        from bot.utils.billing_db import SessionLocal, Subscription
        now_utc = datetime.now(ZoneInfo("UTC"))
        with SessionLocal() as s:
            rec = (
                s.query(Subscription)
                .filter(Subscription.user_id == user_id, Subscription.status == "active")
                .order_by(Subscription.next_charge_at.desc(), Subscription.updated_at.desc())
                .first()
            )
            if not rec:
                return False
            # next_charge_at может быть naive → сравниваем только UTC-aware
            next_at_utc = _as_utc(rec.next_charge_at)
            return bool(next_at_utc and next_at_utc > now_utc)
    except Exception:
        return False


def _should_attempt_remove(user_id: int) -> bool:
    """
    Не даём «дубасить» Telegram remove слишком часто.
    Возвращает True, если последняя попытка была достаточно давно.
    """
    now = datetime.now(ZoneInfo("UTC"))
    last = _last_removal_attempt.get(user_id)
    if last and (now - last) < timedelta(hours=REMOVAL_COOLDOWN_HOURS):
        return False
    _last_removal_attempt[user_id] = now
    return True


async def _membership_remove_http(user_id: int) -> bool:
    """
    Аккуратный HTTP-вызов membership-сервиса на удаление пользователя из чата.
    Возвращает True при HTTP 2xx.
    """
    url = f"{MEMBERSHIP_BASE_URL}/members/remove"
    payload = {"user_id": int(user_id)}
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            r = await http.post(url, json=payload)
            if 200 <= r.status_code < 300:
                return True
            logging.warning("membership remove failed: user_id=%s status=%s body=%s",
                            user_id, r.status_code, r.text)
            return False
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
        # Сетевые ошибки - логируем только предупреждение без полного traceback
        logging.warning("membership remove connection error for user_id=%s: %s (service may be unavailable)", 
                       user_id, str(e))
        return False
    except Exception as e:
        # Другие ошибки - логируем с полным traceback для отладки
        logging.exception("membership remove unexpected error for user_id=%s: %s", user_id, e)
        return False


async def membership_enforcer_loop():
    """
    Периодически проверяет пользователей с истёкшим доступом
    (нет активного триала и нет активного оплаченного периода)
    и аккуратно инициирует удаление из чата через membership-service.
    Анти-спам: один и тот же user_id удаляем не чаще, чем раз в REMOVAL_COOLDOWN_HOURS.
    """
    while not shutdown_event.is_set():
        try:
            # 1) Кандидаты: подписки со статусом 'active', у которых оплаченный период уже закончился
            from bot.utils.billing_db import SessionLocal, Subscription
            now_utc = datetime.now(ZoneInfo("UTC"))
            user_ids: set[int] = set()
            with SessionLocal() as s:
                rows = (
                    s.query(Subscription.user_id, Subscription.next_charge_at)
                    .filter(Subscription.status == "active")
                    .all()
                )
            for uid, next_at in rows:
                # Если триал активен — не трогаем
                try:
                    if db.is_trial_active(uid):
                        continue
                except Exception:
                    # если не смогли проверить триал — подстрахуемся дальнейшей строгой проверкой
                    pass
                # Строго: активный оплаченный период?
                if _has_active_paid_period_strict(uid):
                    continue  # всё ещё в оплаченной зоне
                # next_at в прошлом → оплаченный период закончился (нормализуем к UTC-aware)
                next_at_utc = _as_utc(next_at)
                if next_at_utc is None or next_at_utc <= now_utc:
                    user_ids.add(int(uid))
            # 2) Попробуем удалить каждого кандидата с анти-спамом
            for uid in user_ids:
                if shutdown_event.is_set():
                    break
                if not _should_attempt_remove(uid):
                    continue
                ok = await _membership_remove_http(uid)
                if ok:
                    logging.info("membership_enforcer: removed user %s from chat", uid)
                else:
                    logging.warning("membership_enforcer: failed to remove user %s", uid)
        except Exception:
            logging.exception("membership_enforcer_loop error")
        # Прерываемый sleep
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=ENFORCER_INTERVAL_SEC)
            break
        except asyncio.TimeoutError:
            continue


async def main():
    # Инициализация БД перед любыми обработками
    db.init_db()
    billing_db.init_billing_db()
    logging.info("DB initialized")

    app = web.Application()
    app.router.add_post("/payment", yookassa_webhook_handler)
    # колбэк от executor'а: заменяет "⏳ Генерирую..." итогом
    register_http_endpoints(app, bot)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", YOUMONEY_PORT)
    await site.start()

    async def mailing_loop():
        """
        Фоновый цикл рассылок.
        Раз в 120 секунд проверяет «созревшие» записи и отправляет подписчикам.
        """
        # Опционально: на старте «прожечь» всё, что просрочено
        try:
            await run_mailing_scheduler(bot)
        except Exception:
            logging.exception("mailing_loop initial tick failed")

        while not shutdown_event.is_set():
            try:
                await run_mailing_scheduler(bot)
            except Exception:
                # Любая ошибка внутри — логируем и продолжаем цикл
                logging.exception("mailing_loop tick failed")
            
            # Прерываемый sleep
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=120)
                break
            except asyncio.TimeoutError:
                continue

async def billing_loop(shutdown_event_param=None):
    """
    Простой фоновый цикл рекуррентного биллинга.
    Забирает «просроченные» подписки и создаёт платёж по сохранённому способу оплаты.
    Поддерживает корректное завершение по сигналам (SIGTERM/SIGINT) для systemd.
    """
    # Используем переданный shutdown_event или глобальный
    event = shutdown_event_param if shutdown_event_param is not None else shutdown_event
    logging.info("billing_loop started")
    while not event.is_set():
            try:
                # Используем МСК везде
                now_msk_val = now_msk()
                due = billing_db.subscriptions_due(now=now_msk_val, limit=100)
                
                # Проверяем shutdown_event перед началом обработки
                if event.is_set():
                    logging.info("billing_loop: shutdown signal received, exiting immediately")
                    break
                
                for sub in due:
                    # Частая проверка shutdown_event для быстрого завершения
                    if event.is_set():
                        logging.info("billing_loop: shutdown signal received during processing, breaking")
                        break
                    
                    user_id = sub["user_id"]
                    pm_id = sub["payment_method_id"]
                    subscription_id = sub.get("id")
                    # если токена нет (триал был без сохранённой карты) — пропускаем
                    if not pm_id:
                        # Это нормальная ситуация для trial без сохранённой карты - логируем только на debug
                        logging.debug("Skip recurring: subscription %s for user %s has no payment_method_id", subscription_id, user_id)
                        continue
                    amount = sub["amount_value"]
                    plan_code = sub["plan_code"]

                    # Проверка на дубликаты: есть ли активная попытка с payment_id или недавно созданная попытка без payment_id
                    # Это предотвращает создание нескольких платежей при race condition между созданием платежа и получением webhook
                    try:
                        from bot.utils.billing_db import SessionLocal, ChargeAttempt
                        from bot.utils.time_helpers import to_utc_for_db
                        from datetime import timedelta
                        with SessionLocal() as s:
                            # Проверяем попытки с payment_id (уже создан платёж)
                            existing_attempt_with_payment = (
                                s.query(ChargeAttempt)
                                .filter(
                                    ChargeAttempt.subscription_id == subscription_id,
                                    ChargeAttempt.status == "created",
                                    ChargeAttempt.payment_id.isnot(None)
                                )
                                .first()
                            )
                            if existing_attempt_with_payment:
                                logging.warning(
                                    "Skip recurring: active attempt with payment_id exists (sub=%s, user=%s, payment_id=%s, attempt_id=%s)", 
                                    subscription_id, user_id, existing_attempt_with_payment.payment_id, existing_attempt_with_payment.id
                                )
                                continue
                            
                            # Проверяем недавно созданные попытки без payment_id (за последние 5 минут)
                            # Это предотвращает создание нескольких платежей при race condition
                            recent_threshold = now_msk_val - timedelta(minutes=5)
                            recent_threshold_utc = to_utc_for_db(recent_threshold)
                            recent_attempt = (
                                s.query(ChargeAttempt)
                                .filter(
                                    ChargeAttempt.subscription_id == subscription_id,
                                    ChargeAttempt.status == "created",
                                    ChargeAttempt.payment_id.is_(None),
                                    ChargeAttempt.attempted_at >= recent_threshold_utc
                                )
                                .first()
                            )
                            if recent_attempt:
                                logging.warning(
                                    "Skip recurring: recent attempt without payment_id exists (sub=%s, user=%s, attempt_id=%s, attempted_at=%s)", 
                                    subscription_id, user_id, recent_attempt.id, recent_attempt.attempted_at
                                )
                                continue
                    except Exception as e:
                        logging.warning("Failed to check for duplicate attempts: %s", e)

                    # Валидация payment_method_id перед созданием платежа
                    if not pm_id or not isinstance(pm_id, str) or len(pm_id.strip()) == 0:
                        logging.warning(
                            "Invalid payment_method_id for subscription %s, user %s: %s",
                            subscription_id, user_id, pm_id
                        )
                        continue
                    
                    # Второй щит + атомарная фиксация попытки (created)
                    attempt_id = None
                    try:
                        attempt_id = billing_db.precharge_guard_and_attempt(
                            subscription_id=subscription_id,
                            now=now_msk_val,  # Передаём МСК
                            user_id=user_id,
                        )
                        if not attempt_id:
                            # Guard заблокировал - это нормально (лимиты ретраев, паузы и т.д.)
                            # Логируем только на debug уровне, чтобы не засорять логи
                            logging.debug("Skip recurring: guard blocked (sub=%s, user=%s)", subscription_id, user_id)
                            continue
                        
                        # Создаём платёж с передачей attempt_id для обработки ошибок
                        pay_id = youmoney.charge_saved_method(
                            user_id=user_id,
                            payment_method_id=pm_id,
                            amount_rub=amount,
                            description=f"Подписка {plan_code}",
                            metadata={"is_recurring": "1", "plan_code": plan_code},
                            subscription_id=subscription_id,
                            record_attempt=False,
                            attempt_id=attempt_id,  # Передаём для пометки как failed при ошибке
                        )
                        billing_db.link_payment_to_attempt(attempt_id=attempt_id, payment_id=pay_id)
                        logging.info("Recurring charge created: %s (user=%s, sub=%s, attempt=%s)", 
                                   pay_id, user_id, subscription_id, attempt_id)
                    except ValueError as e:
                        # Ошибки валидации - логируем и помечаем попытку
                        logging.error(
                            "Validation error creating recurring charge for user %s, subscription %s: %s",
                            user_id, subscription_id, e
                        )
                        if attempt_id:
                            try:
                                from bot.utils.billing_db import SessionLocal, ChargeAttempt
                                with SessionLocal() as s, s.begin():
                                    attempt_rec = s.get(ChargeAttempt, attempt_id)
                                    if attempt_rec:
                                        attempt_rec.status = "failed"
                                        s.flush()
                            except Exception as mark_error:
                                logging.warning("Failed to mark attempt %s as failed: %s", attempt_id, mark_error)
                        continue
                    except Exception as e:
                        # Другие ошибки - логируем с полным контекстом
                        logging.exception(
                            "Failed to create recurring charge for user %s, subscription %s, attempt %s: %s",
                            user_id, subscription_id, attempt_id, e
                        )
                        # Помечаем попытку как failed при ошибке (charge_saved_method уже должен был это сделать,
                        # но делаем дополнительную проверку на всякий случай)
                        if attempt_id:
                            try:
                                from bot.utils.billing_db import SessionLocal, ChargeAttempt
                                with SessionLocal() as s, s.begin():
                                    attempt_rec = s.get(ChargeAttempt, attempt_id)
                                    if attempt_rec and attempt_rec.status == "created":
                                        attempt_rec.status = "failed"
                                        s.flush()
                            except Exception as mark_error:
                                logging.warning("Failed to mark attempt %s as failed: %s", attempt_id, mark_error)
                        continue

                    # перенос next_charge_at и продление — только по вебхуку
            except Exception as e:
                logging.exception("billing_loop error: %s", e)
            
            # Проверяем shutdown_event перед sleep
            if event.is_set():
                logging.info("billing_loop: shutdown signal received, exiting")
                break
            
            # Прерываемый sleep с коротким таймаутом для быстрой реакции на сигналы
            try:
                # Используем короткий таймаут (5 секунд) для быстрой реакции на shutdown_event
                await asyncio.wait_for(event.wait(), timeout=5.0)
                # Если shutdown_event установлен, выходим немедленно
                if event.is_set():
                    logging.info("billing_loop: shutdown signal received during sleep, exiting")
                    break
            except asyncio.TimeoutError:
                # Таймаут истёк, продолжаем цикл
                continue
    
    logging.info("billing_loop stopped")

    async def notification_loop():
        """
        Фоновый цикл сценарных уведомлений (unsub/trial/paid).
        Раз в 10 минут проверяет, кому пришло время отправить сообщения.
        Антиспам — на уровне notification.* через Redis.
        """
        # На старте один «тик» (можно словить хвосты после рестарта)
        try:
            await run_notification_scheduler(bot)
        except Exception:
            logging.exception("notification_loop initial tick failed")

        while not shutdown_event.is_set():
            try:
                await run_notification_scheduler(bot)
            except Exception:
                logging.exception("notification_loop tick failed")
            # Прерываемый sleep
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=600)
                break
            except asyncio.TimeoutError:
                continue

    # ---Жёсткий стоп по сигналу---
    def _hard_stop(signum, frame):
        # максимально быстрый stop для systemd: устанавливаем shutdown_event и отменяем таски
        signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logging.warning(f"Получен сигнал {signal_name} ({signum}), выполняю немедленную остановку...")
        
        # Устанавливаем shutdown_event - все циклы должны немедленно завершиться
        shutdown_event.set()
        
        try:
            # Отменяем все задачи, кроме текущей
            loop = asyncio.get_event_loop()
            if loop.is_running():
                current_task = asyncio.current_task(loop)
                for task in asyncio.all_tasks(loop):
                    if task is not current_task and not task.done():
                        task.cancel()
                        logging.debug(f"Отменена задача: {task.get_name()}")
        except Exception as e:
            logging.warning(f"Ошибка при отмене задач: {e}")
        
        # Даём немного времени на корректное завершение (но не ждём долго)
        try:
            # Небольшая задержка для завершения текущих операций в циклах
            # но не более 2 секунд
            time.sleep(0.5)  # 500ms на завершение текущих операций
        except Exception:
            pass
        
        try:
            logging.shutdown()
        except Exception:
            pass
        
        # Жёсткий выход без ожидания сборки/cleanup — гарантирует моментальный рестарт
        logging.warning("Принудительное завершение процесса")
        os._exit(0)

    signal.signal(signal.SIGTERM, _hard_stop)
    signal.signal(signal.SIGINT, _hard_stop)
    
    try:
        logging.info("Бот запущен")
        # Запускаем задачи как отдельные таски, чтобы их можно было отменить мгновенно
        billing_task = asyncio.create_task(billing_loop(shutdown_event), name="billing_loop")
        mailing_task = asyncio.create_task(mailing_loop(), name="mailing_loop")
        notification_task = asyncio.create_task(notification_loop(), name="notification_loop")
        enforcer_task = asyncio.create_task(membership_enforcer_loop(), name="membership_enforcer_loop")
        # Важно: отключаем встроенную обработку сигналов, чтобы не было «грейсфул» задержек
        polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False), name="polling")

        # ждём, пока любая из задач завершится с исключением или по отмене
        done, pending = await asyncio.wait(
            {billing_task, mailing_task, notification_task, enforcer_task, polling_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )

        for t in done:
            with suppress(asyncio.CancelledError):
                exc = t.exception()
                if exc:
                    logging.error("Задача %s завершилась с ошибкой: %s", t.get_name() or t, exc)
                
    except Exception as e:
        logging.error(f"Ошибка при запуске бота: {e}")


if __name__ == '__main__':
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    # чуть приглушим шум сетевых библиотек
    logging.getLogger("aiohttp.client").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    asyncio.run(main())