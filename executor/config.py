#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\config.py

import os
from dotenv import load_dotenv, find_dotenv

# Надёжная загрузка .env (ищем вверх по дереву от текущей рабочей директории)
load_dotenv(find_dotenv(usecwd=True))


# Базовые секреты
# Приоритет ключа: запрос -> ENV
BANANO_API_KEY_FALLBACK = (
    os.getenv("BANANO_API_KEY", "")
    or os.getenv("GOOGLE_API_KEY", "")
    or os.getenv("GEMINI_API_KEY", "")
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Хост/порт локального API
EXECUTOR_HOST = os.getenv("EXECUTOR_HOST", "127.0.0.1")
EXECUTOR_PORT = int(os.getenv("EXECUTOR_PORT"))
