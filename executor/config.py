#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\config.py

import os
from dotenv import load_dotenv, find_dotenv

# Надёжная загрузка .env (ищем вверх по дереву от текущей рабочей директории)
load_dotenv(find_dotenv(usecwd=True))

# Базовые секреты
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Хост/порт локального API
EXECUTOR_HOST = os.getenv("EXECUTOR_HOST", "127.0.0.1")
EXECUTOR_PORT = int(os.getenv("EXECUTOR_PORT", "5001"))

# --- Конфиг моделей из окружения ---
# Формат REF: "owner/model:version_hash"
MODEL_INTERIOR_DESIGN_REF = (os.getenv("MODEL_INTERIOR_DESIGN_REF") or "").strip()
MODEL_INTERIOR_DESIGN_IMAGE_PARAM = (os.getenv("MODEL_INTERIOR_DESIGN_IMAGE_PARAM") or "").strip()
MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY = (os.getenv("MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY", "false").lower() == "true")

MODEL_FLOOR_PLAN_REF = (os.getenv("MODEL_FLOOR_PLAN_REF") or "").strip()
MODEL_FLOOR_PLAN_IMAGE_PARAM = (os.getenv("MODEL_FLOOR_PLAN_IMAGE_PARAM") or "").strip()
MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY = (os.getenv("MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY", "false").lower() == "true")

def validate_config() -> list[str]:
    """
    Проверяем, что обязательные переменные заданы корректно.
    Возвращаем список проблем (пустой — всё ок).
    """
    problems: list[str] = []

    if not REPLICATE_API_TOKEN:
        problems.append("REPLICATE_API_TOKEN is missing")

    allowed_img_params = {"image", "input_image", "input_images"}

    # Interior
    if not MODEL_INTERIOR_DESIGN_REF:
        problems.append("MODEL_INTERIOR_DESIGN_REF is missing")
    if MODEL_INTERIOR_DESIGN_IMAGE_PARAM not in allowed_img_params:
        problems.append("MODEL_INTERIOR_DESIGN_IMAGE_PARAM must be one of: image|input_image|input_images")

    # Floor plan
    if not MODEL_FLOOR_PLAN_REF:
        problems.append("MODEL_FLOOR_PLAN_REF is missing")
    if MODEL_FLOOR_PLAN_IMAGE_PARAM not in allowed_img_params:
        problems.append("MODEL_FLOOR_PLAN_IMAGE_PARAM must be one of: image|input_image|input_images")

    # Если для какой-то модели требуется openai key — он должен быть задан
    if MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY and not OPENAI_API_KEY:
        problems.append("OPENAI_API_KEY is required for interior design model (flag is true)")
    if MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY and not OPENAI_API_KEY:
        problems.append("OPENAI_API_KEY is required for floor plan model (flag is true)")

    return problems