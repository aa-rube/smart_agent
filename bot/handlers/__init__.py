# smart_agent/bot/handlers/__init__.py
from aiogram import Router
from . import (
    handler_manager,
    # подключаем миддлвары логирования
    design,
    plans_playbook,
    admin,
    subscribe_partner_manager,
    objection_playbook,
    description_playbook,
    feedback_playbook,
    summary_playbook,
    payment_handler,
    smm_playbook,
)

from .clicklog_mw import CallbackClickLogger, MessageLogger


# Порядок роутеров оставляем как есть:
# subscribe_partner_manager подключается после handler_manager,
# что позволяет переопределить обработку partners.check
def register_routers(rt: Router):
    # Глобально вешаем миддлвары логирования для ВСЕХ хендлеров:
    # - TEXT/COMMAND сообщений
    rt.message.outer_middleware(MessageLogger())
    # - CallbackQuery
    rt.callback_query.outer_middleware(CallbackClickLogger())

    # Приоритет 1: Админские команды
    admin.router(rt)

    # Приоритет 2: Основные команды + универсальный обработчик команд
    handler_manager.router(rt)

    # Приоритет 3: SMM функциональность
    smm_playbook.router(rt)

    # Приоритет 4: Команды платежей
    payment_handler.router(rt)

    # Приоритет 5: Остальные обработчики
    # Проверка подписки (кнопка «✅ Проверить подписку») - partners.check
    subscribe_partner_manager.router(rt)
    description_playbook.router(rt)
    feedback_playbook.router(rt)
    design.router(rt)
    plans_playbook.router(rt)
    objection_playbook.router(rt)
    summary_playbook.router(rt)
