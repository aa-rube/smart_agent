#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\run.py
import asyncio
import logging
import signal
from contextlib import suppress

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

from bot import setup
from bot.config import *
import bot.utils.database as db
import bot.utils.billing_db as billing_db
from bot.utils.mailing import run_mailing_scheduler

from bot.handlers.payment_handler import process_yookassa_webhook
from bot.utils import youmoney
from datetime import datetime
from zoneinfo import ZoneInfo
from bot.handlers.description_playbook import register_http_endpoints


bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher(storage=MemoryStorage())
setup(dp)

# Флаг для graceful shutdown
shutdown_event = asyncio.Event()


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

    #UTC+3
    msk = ZoneInfo("Europe/Moscow")

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

    async def billing_loop():
        """
        Простой фоновый цикл рекуррентного биллинга.
        Забирает «просроченные» подписки и создаёт платёж по сохранённому способу оплаты.
        """
        while not shutdown_event.is_set():
            try:
                # timezone-aware MSK:
                now_msk = datetime.now(msk)
                due = billing_db.subscriptions_due(now=now_msk, limit=100)
                for sub in due:
                    if shutdown_event.is_set():
                        break
                    
                    user_id = sub["user_id"]
                    pm_id = sub["payment_method_id"]
                    # если токена нет (триал был без сохранённой карты) — пропускаем
                    if not pm_id:
                        logging.info("Skip recurring: subscription %s for user %s has no payment_method_id", sub.get("id"), user_id)
                        continue
                    amount = sub["amount_value"]
                    plan_code = sub["plan_code"]

                    # Второй щит + атомарная фиксация попытки (created)
                    try:
                        attempt_id = billing_db.precharge_guard_and_attempt(
                            subscription_id=sub["id"],
                            now=now_msk.astimezone(ZoneInfo("UTC")),
                            user_id=user_id,
                        )
                        if not attempt_id:
                            logging.info("Skip recurring: guard blocked (sub=%s, user=%s)", sub.get("id"), user_id)
                            continue
                        pay_id = youmoney.charge_saved_method(
                            user_id=user_id,
                            payment_method_id=pm_id,
                            amount_rub=amount,
                            description=f"Подписка {plan_code}",
                            metadata={"is_recurring": "1", "plan_code": plan_code},
                            subscription_id=sub.get("id"),
                            record_attempt=False,
                        )
                        billing_db.link_payment_to_attempt(attempt_id=attempt_id, payment_id=pay_id)
                        logging.info("Recurring charge created: %s (user=%s, sub=%s)", pay_id, user_id, sub.get("id"))
                    except Exception as e:
                        logging.exception("Failed to create recurring charge for user %s: %s", user_id, e)
                        continue

                    # перенос next_charge_at и продление — только по вебхуку
            except Exception as e:
                logging.exception("billing_loop error: %s", e)
            
            # Прерываемый sleep
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=60)
                break
            except asyncio.TimeoutError:
                continue

    # ---Жёсткий стоп по сигналу---
    def _hard_stop(signum, frame):
        # максимально быстрый stop: ставим флаг и отменяем ВСЕ задачи event-loop
        logging.warning(f"Получен сигнал {signum}, выполняю немедленную остановку...")
        try:
            shutdown_event.set()
            loop = asyncio.get_event_loop()
            for task in asyncio.all_tasks(loop):
                if task is not asyncio.current_task(loop):
                    task.cancel()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, _hard_stop)
    signal.signal(signal.SIGINT, _hard_stop)
    
    try:
        logging.info("Бот запущен")
        # Запускаем задачи как отдельные таски, чтобы их можно было отменить мгновенно
        billing_task = asyncio.create_task(billing_loop(), name="billing_loop")
        mailing_task = asyncio.create_task(mailing_loop(), name="mailing_loop")
        polling_task = asyncio.create_task(dp.start_polling(bot), name="polling")

        # ждём, пока любая из задач завершится с исключением или по отмене
        done, pending = await asyncio.wait(
            {billing_task, mailing_task, polling_task},
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())