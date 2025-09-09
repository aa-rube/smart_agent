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
