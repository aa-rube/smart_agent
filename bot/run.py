#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\run.py
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

from bot import setup
from bot.config import TOKEN
import bot.utils.database as db

from bot.handlers.payment_handler import process_yookassa_webhook
from bot.utils import youmoney
from datetime import datetime
import bot.utils.database as db
from dateutil.relativedelta import relativedelta



bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher(storage=MemoryStorage())
setup(dp)



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
    logging.info("DB initialized")

    app = web.Application()
    app.router.add_post("/payment", yookassa_webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()
    logging.info("Webhook server started on http://0.0.0.0:8000")

    async def billing_loop():
        """
        Простой фоновый цикл рекуррентного биллинга.
        Забирает «просроченные» подписки и создаёт платёж по сохранённому способу оплаты.
        """
        while True:
            try:
                due = db.subscriptions_due(now=datetime.utcnow(), limit=100)
                for sub in due:
                    user_id = sub["user_id"]
                    pm_id = sub["payment_method_id"]
                    amount = sub["amount_value"]
                    plan_code = sub["plan_code"]
                    interval_m = int(sub["interval_months"] or 1)

                    # Создаём платёж (без участия пользователя)
                    try:
                        pay_id = youmoney.charge_saved_method(
                            user_id=user_id,
                            payment_method_id=pm_id,
                            amount_rub=amount,
                            description=f"Подписка {plan_code}",
                            metadata={"is_recurring": "1", "plan_code": plan_code},
                        )
                        logging.info("Recurring charge created: %s (user=%s)", pay_id, user_id)
                    except Exception as e:
                        logging.exception("Failed to create recurring charge for user %s: %s", user_id, e)
                        continue

                    # продлеваем дату следующего списания (факт успеха подтвердит вебхук)
                    next_charge_at = datetime.utcnow() + relativedelta(months=+interval_m)
                    db.subscription_mark_charged(sub["id"], next_charge_at=next_charge_at)

                    # сразу продлим доступ (консервативно можно ждать вебхука; оставим как есть, доступ продлеваем по вебхуку)
            except Exception as e:
                logging.exception("billing_loop error: %s", e)
            finally:
                await asyncio.sleep(60)  # раз в минуту

    try:
        logging.info("Бот запущен")
        # Запускаем фоновый биллинг-процесс и поллинг бота параллельно
        await asyncio.gather(
            billing_loop(),
            dp.start_polling(bot),
        )
    except Exception as e:
        logging.error(f"Ошибка при запуске бота: {e}")
    finally:
        await runner.cleanup()
        await bot.session.close()
        logging.info("Бот и веб-сервер остановлены")


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(main())