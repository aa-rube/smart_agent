#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\config.py

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = Path(__file__).parent

TOKEN = os.getenv("TOKEN")
DB_PATH = BASE_DIR / "settings.db"
YOUMONEY_SHOP_ID = os.getenv("YOUMONEY_SHOP_ID")
YOUMONEY_SECRET_KEY = os.getenv("YOUMONEY_SECRET_KEY")


EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://127.0.0.1:5001")


# DB для админки/подписок/постов (как Users.bd в старом боте)
ADMIN_DB_PATH = os.getenv("ADMIN_DB_PATH", "Users.bd")

# Телеграм-идентификаторы
ADMIN_ID = int(os.getenv("ADMIN_ID", "7833048230"))
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "7833048230"))     # куда слать нотификации админам
CONTENT_CHANNEL_ID = int(os.getenv("CONTENT_CHANNEL_ID", "7833048230"))  # канал с постами
CONTENT_GROUP_ID = int(os.getenv("CONTENT_GROUP_ID", "-1002899688608"))   # группа/супергруппа для доступа подписчиков

# Оплата
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "390540012:LIVE:75513")