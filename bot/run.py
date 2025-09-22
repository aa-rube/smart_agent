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