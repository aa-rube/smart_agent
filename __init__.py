from bot.handlers import register_routers
from aiogram import Router, Dispatcher


def setup(dp: Dispatcher):
    main_router = Router()
    register_routers(main_router)

    dp.include_router(main_router)