#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\__init__.py

from aiogram import Router
from . import (handler_manager, design_planes, interior_redesign, zero_design, admin)

def register_routers(rt: Router):
    handler_manager.router(rt)
    design_planes.router(rt)
    interior_redesign.router(rt)
    zero_design.router(rt)
    rt.include_router(admin.router)
