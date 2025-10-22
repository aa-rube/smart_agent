#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\membership\config.py

import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    # Чаты / админы
    TARGET_CHAT_ID: int = int(os.getenv("TARGET_CHAT_ID", "0"))
    ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))

    # Bot API
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # Telethon (user-bot)
    API_ID: int = int(os.getenv("TG_API_ID", "0"))
    API_HASH: str = os.getenv("TG_API_HASH", "")
    SESSION: str | None = os.getenv("TG_SESSION")

    # Прочие настройки
    INVITE_TTL_HOURS_DEFAULT: int = int(os.getenv("INVITE_TTL_HOURS_DEFAULT", "24"))

    def validate(self):
        """Проверка обязательных настроек"""
        if not self.TARGET_CHAT_ID:
            raise ValueError("TARGET_CHAT_ID не установлен")
        if not self.ADMIN_ID:
            raise ValueError("ADMIN_ID не установлен")
        if not self.BOT_TOKEN:
            raise ValueError("BOT_TOKEN не установлен")
        if not self.SESSION:
            raise ValueError("TG_SESSION не установлен")


settings = Settings()
settings.validate()