#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\config.py

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# === Директории ===
CURRENT_FILE = Path(__file__).resolve()         # .../smart_agent/bot/config.py
BOT_DIR      = CURRENT_FILE.parent              # .../smart_agent/bot
PROJECT_DIR  = CURRENT_FILE.parents[1]          # .../smart_agent
WORKSPACE    = PROJECT_DIR.parent               # .../super_bot

# Целевая папка для БД — соседняя к smart_agent: .../super_bot/smart_agent_bd
DEFAULT_DB_DIR = WORKSPACE / f"{PROJECT_DIR.name}_bd"
DATA_DIR = Path(os.getenv("DATA_DIR", str(PROJECT_DIR / "data"))).resolve()

# Важно: чтобы записи в data/ не падали
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Можно переопределить через переменную окружения DB_DIR
DB_DIR = Path(os.getenv("DB_DIR", str(DEFAULT_DB_DIR))).resolve()
DB_DIR.mkdir(parents=True, exist_ok=True)

# === Токен бота ===
TOKEN = os.getenv("TOKEN")

# === Пути к БД (строки для sqlite3.connect) ===
DB_PATH        = os.getenv("DB_PATH",        str(DB_DIR / "settings.db"))
ADMIN_DB_PATH  = os.getenv("ADMIN_DB_PATH",  str(DB_DIR / "admins.bd"))

# === Интеграции / сервисы ===
EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL", "http://127.0.0.1:5001")

PARTNER_CHANNELS=[{"chat_id":"-1002969803274","url":"https://t.me/setrealtora","label":"Сеть Риэлтора"}]

# YooMoney
YOUMONEY_SHOP_ID     = os.getenv("YOUMONEY_SHOP_ID")
YOUMONEY_SECRET_KEY  = os.getenv("YOUMONEY_SECRET_KEY")

# === Telegram IDs ===
ADMIN_ID            = int(os.getenv("ADMIN_ID", "7833048230"))
ADMIN_GROUP_ID      = int(os.getenv("ADMIN_GROUP_ID", "7833048230"))
CONTENT_CHANNEL_ID  = int(os.getenv("CONTENT_CHANNEL_ID", "7833048230"))
CONTENT_GROUP_ID    = int(os.getenv("CONTENT_GROUP_ID", "-1002899688608"))

# === Оплата ===
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "390540012:LIVE:75513")


def get_file_path(relative_path: str) -> str:

    if not relative_path:
        raise ValueError("relative_path must be a non-empty string")

    # Если передали абсолютный путь и он существует — уважаем его.
    # Если абсолютный, но файла нет — трактуем как путь внутри DATA_DIR.
    p = Path(relative_path)
    if p.is_absolute() and p.exists():
        return str(p.resolve())

    # normalize separators and strip leading slashes
    rel = str(relative_path).replace("\\", "/").lstrip("/")

    # if someone passed a path that starts with "data/" — remove that part
    if rel.lower().startswith("data/"):
        rel = rel[5:]  # cut "data/"

    full = (DATA_DIR / rel).resolve()
    return str(full)