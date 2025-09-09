#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\__init__.py

from aiogram import Router
from . import (start, design_planes, interior_redesign, zero_design)


def register_routers(rt: Router):
    start.router(rt)
    design_planes.router(rt)
    interior_redesign.router(rt)
    zero_design.router(rt)