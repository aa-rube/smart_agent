# smart_agent/bot/handlers/__init__.py
from aiogram import Router
from . import (
    handler_manager,
    design,
    plans,
    admin,
    objection_playbook,
    description_playbook,
    feedback_playbook,
    summary_playbook,
    payment_handler
)

def register_routers(rt: Router):
    # Приоритет 1: Админские команды
    admin.router(rt)
    
    # Приоритет 2: Основные команды + универсальный обработчик команд
    handler_manager.router(rt)
    
    # Приоритет 3: Команды платежей
    payment_handler.router(rt)
    
    # Приоритет 4: Остальные обработчики (только callback и text с фильтрами состояний)
    description_playbook.router(rt)
    feedback_playbook.router(rt)
    design.router(rt)
    plans.router(rt)
    objection_playbook.router(rt)
    summary_playbook.router(rt)