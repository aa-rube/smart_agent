import os
from dataclasses import dataclass

@dataclass(frozen=True)
class Settings:
    # Чаты / админы
    TARGET_CHAT_ID: int = int(os.getenv("TARGET_CHAT_ID"))
    ADMIN_ID: int = int(os.getenv("ADMIN_ID"))

    # Bot API
    BOT_TOKEN: str = os.getenv("BOT_TOKEN")

    # Telethon (user-bot)
    API_ID: int = int(os.getenv("TG_API_ID"))
    API_HASH: str = os.getenv("TG_API_HASH")
    SESSION: str | None = os.getenv("TG_SESSION")

    # Прочие настройки
    INVITE_TTL_HOURS_DEFAULT: int = int(os.getenv("INVITE_TTL_HOURS_DEFAULT"))


settings = Settings()