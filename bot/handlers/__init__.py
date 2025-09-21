# smart_agent/bot/handlers/__init__.py
from aiogram import Router
from . import (
    handler_manager,
    design,
    # plans,
    admin,
    objection_playbook,
    description_playbook,
    feedback_playbook,
    summary_playbook,
    subscribe_handler
)

def register_routers(rt: Router):
    feedback_playbook.router(rt)
    handler_manager.router(rt)
    design.router(rt)
    # plans.router(rt)
    description_playbook.router(rt)
    objection_playbook.router(rt)
    summary_playbook.router(rt)
    admin.router(rt)
    subscribe_handler.router(rt)
