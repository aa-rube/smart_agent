#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\config.py

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ==== Директории ====
# .../super_bot/smart_agent/bot/config.py -> bot_dir
BOT_DIR: Path = Path(__file__).resolve().parent
# корень приложения (папка, содержащая smart_agent)
APP_ROOT: Path = BOT_DIR.parents[2] if len(BOT_DIR.parents) >= 3 else BOT_DIR
# целевая папка для БД: соседняя к проекту, "<project_name>_bds"
DEFAULT_DB_DIR: Path = APP_ROOT.parent / f"{APP_ROOT.name}_bds"

# можно переопределить через переменную окружения DB_DIR
DB_DIR: Path = Path(os.getenv("DB_DIR", str(DEFAULT_DB_DIR))).resolve()
DB_DIR.mkdir(parents=True, exist_ok=True)

# ==== Токен бота ====
TOKEN = os.getenv("TOKEN")

# ==== Пути к БД ====
# Основная БД (settings) — по умолчанию в DB_DIR/settings.db
DB_PATH = os.getenv("DB_PATH", str(DB_DIR / "settings.db"))
# Админская БД (подписки/посты/уведомления) — в DB_DIR/admins.bd
ADMIN_DB_PATH = os.getenv("ADMIN_DB_PATH", str(DB_DIR / "admins.bd"))

# ==== Интеграции / сервисы ====
EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://127.0.0.1:5001")

# YooMoney (если используешь)
YOUMONEY_SHOP_ID = os.getenv("YOUMONEY_SHOP_ID")
YOUMONEY_SECRET_KEY = os.getenv("YOUMONEY_SECRET_KEY")

# ==== Telegram IDs ====
ADMIN_ID = int(os.getenv("ADMIN_ID", "7833048230"))
ADMIN_GROUP_ID = int(os.getenv("ADMIN_GROUP_ID", "7833048230"))          # куда слать нотификации админам
CONTENT_CHANNEL_ID = int(os.getenv("CONTENT_CHANNEL_ID", "7833048230"))  # канал с постами
CONTENT_GROUP_ID = int(os.getenv("CONTENT_GROUP_ID", "-1002899688608"))  # группа подписчиков

# ==== Оплата ====
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "390540012:LIVE:75513")
