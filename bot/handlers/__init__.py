# smart_agent/bot/handlers/__init__.py
from aiogram import Router
from . import (
    handler_manager,
    design_planes,
    interior_redesign,
    admin,
    objection_playbook,
    description_playbook,
    feedback_playbook,
    summary_playbook
)

def register_routers(rt: Router):
    feedback_playbook.router(rt)
    handler_manager.router(rt)
    design_planes.router(rt)
    interior_redesign.router(rt)
    description_playbook.router(rt)
    objection_playbook.router(rt)
    summary_playbook.router(rt)
    rt.include_router(admin.router)