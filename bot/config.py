# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\config.py
import os
from pathlib import Path

from dotenv import load_dotenv
from urllib.parse import quote_plus

load_dotenv()

EXECUTOR_CALLBACK_TOKEN = os.getenv("EXECUTOR_CALLBACK_TOKEN")

# === Директории ===
CURRENT_FILE = Path(__file__).resolve()  # .../smart_agent/bot/config.py
BOT_DIR = CURRENT_FILE.parent  # .../smart_agent/bot
PROJECT_DIR = CURRENT_FILE.parents[1]  # .../smart_agent
WORKSPACE = PROJECT_DIR.parent  # .../super_bot


# === MySQL Database ===
MYSQL_HOST = os.getenv("MYSQL_HOST", "null")
MYSQL_PORT = os.getenv("MYSQL_PORT", "0")
MYSQL_USER = os.getenv("MYSQL_USER", "null")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "null")

# Админская база данных
MYSQL_ADMIN_DB = os.getenv("MYSQL_ADMIN_DB", "null")
ADMIN_DB_URL = f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_ADMIN_DB}"

# === Токен бота ===
TOKEN = os.getenv("TOKEN")


# === Telegram IDs ===

def _parse_int_list(s: str) -> list[int]:
    if not s:
        return []
    import re
    ids = []
    for token in re.split(r"[,\s;]+", s.strip()):
        if not token:
            continue
        ids.append(int(token))
    return ids


ADMIN_IDS = _parse_int_list(os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID", ""))
CONTENT_GROUP_ID = int(os.getenv("CONTENT_GROUP_ID", "0"))

# Дополнительные настройки
PARTNER_CHANNEL = int(os.getenv("PARTNER_CHANNEL", "0"))
PARTNER_URL = os.getenv("PARTNER_URL", "http://t.me")
PARTNER_CHANNELS = [{"chat_id": PARTNER_CHANNEL, "url": PARTNER_URL, "label": "Сеть Риэлтора"}]

# --- Новые конфиги для callback от executor ---
BOT_PUBLIC_BASE_URL = os.getenv("BOT_CALLBACK_BASE_URL", "").rstrip("/")
EXECUTOR_BASE_URL = os.getenv("EXECUTOR_BASE_URL")
REDIS_PREFIX=os.getenv("REDIS_PREFIX", "sa")

# Основная база данных
MYSQL_DB = os.getenv("MYSQL_DB", "null")
DB_URL = f"mysql+pymysql://{MYSQL_USER}:{quote_plus(MYSQL_PASSWORD)}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"

YOUMONEY_SHOP_ID = os.getenv("YOUMONEY_SHOP_ID")
YOUMONEY_SECRET_KEY = os.getenv("YOUMONEY_SECRET_KEY")
YOUMONEY_PORT = int(os.getenv("YOUMONEY_PORT"))


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
