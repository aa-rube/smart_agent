# smart_agent/bot/__init__.py
from aiogram import Router, Dispatcher
from bot.handlers import register_routers  # убедись, что handlers/__init__.py экспортирует register_routers

def setup(dp: Dispatcher) -> None:
    main_router = Router()
    register_routers(main_router)
    dp.include_router(main_router)

__all__ = ["setup"]
