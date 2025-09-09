#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\chat_actions.py

import asyncio
from typing import Coroutine, Any, Callable
from aiogram import Bot
from aiogram.enums.chat_action import ChatAction


async def run_long_operation_with_action(
        bot: Bot,
        chat_id: int,
        action: str | ChatAction,
        coro: Callable[..., Coroutine[Any, Any, Any]],
) -> Any:

    async def send_action_periodically():
        while True:
            await bot.send_chat_action(chat_id=chat_id, action=action)
            await asyncio.sleep(4.5)

    typing_task = asyncio.create_task(send_action_periodically())

    result = None
    try:
        result = await coro
    finally:
        typing_task.cancel()

    return result