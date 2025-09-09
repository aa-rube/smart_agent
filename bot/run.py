import asyncio
import logging

import utils.database as db
import utils.tokens as tk

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiohttp import web

from bot import setup
from bot.config import TOKEN

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
dp = Dispatcher(storage=MemoryStorage())
setup(dp)


async def yookassa_webhook_handler(request: web.Request):
    try:
        data = await request.json()
        logging.info(f"YooKassa webhook received: {data}")

        if data.get("object", {}).get("status") == "succeeded":
            payment_info = data["object"]

            user_id = int(payment_info["metadata"]["user_id"])
            amount = float(payment_info["amount"]["value"])

            logging.info(f"Successful payment from user_id: {user_id} for amount: {amount}")

            # гарантируем существование пользователя и дефолтов
            db.check_and_add_user(user_id)

            tk.add_tokens(user_id, 100)
            db.set_variable(user_id, "have_sub", "1")

            await bot.send_message(
                chat_id=user_id,
                text=f"✅ Ваша оплата на сумму {amount} руб. прошла успешно!"
            )
        return web.Response(status=200)

    except Exception as e:
        logging.error(f"Error processing YooKassa webhook: {e}")
        return web.Response(status=500)


async def main():
    # Инициализация БД перед любыми обработками
    db.init_db()
    logging.info("DB initialized")

    app = web.Application()
    app.router.add_post("/yookassa_webhook", yookassa_webhook_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logging.info("Webhook server started on http://0.0.0.0:8080")

    try:
        logging.info("Бот запущен")
        await dp.start_polling(bot)
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
