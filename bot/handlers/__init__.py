# smart_agent/bot/handlers/__init__.py
from aiogram import Router

from . import (
    handler_manager,
    design_planes,
    interior_redesign,
    zero_design,
    admin,
    objection_playbook,   # ← добавили
)

def register_routers(rt: Router):
    handler_manager.router(rt)
    design_planes.router(rt)
    interior_redesign.router(rt)
    zero_design.router(rt)
    objection_playbook.router(rt)   # ← добавили

    # Оставь как есть: если в admin.router лежит именно Router-объект,
    # то include_router корректен; если там функция — зови admin.router(rt)
    rt.include_router(admin.router)
