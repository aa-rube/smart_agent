#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\prompt_factory.py

import random
from executor import ai_config

ROOM_TYPE_PROMPTS = {
    "🍳 Кухня": "kitchen",
    "🛏 Спальня": "bedroom",
    "🛋 Гостиная": "living room",
    "🚿 Ванная": "bathroom",
    "🚪 Прихожая": "hallway"
}

FURNITURE_PROMPTS = {
    "furniture_yes": "fully furnished with appropriate furniture",
    "furniture_no": "as an empty room, unfurnished"
}

PLAN_TYPE_PROMPTS = {
    "plan_2d": "a stylish 2d floor plan",
    "plan_3d": "a 3d floor plan with furniture"
}


def create_prompt(
        style: str,
        room_type: str | None = None,
        furniture: str | None = None,
        plan_type: str | None = None
) -> str:
    base_prompt = ai_config.PROMPT_INTERIOR_BASE

    # Обработка случайного стиля
    if style == "🔥 Случайный выбор ИИ":
        available_styles = {k: v for k, v in ai_config.STYLES_DETAIL.items() if v != "random_style"}
        random_style_name = random.choice(list(available_styles.keys()))
        style_text = available_styles[random_style_name]
    else:
        # Если стиль не случайный, получаем его детализацию из конфига
        style_text = ai_config.STYLES_DETAIL.get(style, "modern style")

    # Сценарий "Дизайн планировок"
    if plan_type:
        plan_text = PLAN_TYPE_PROMPTS.get(plan_type, "")
        # Используем шаблон из конфига
        final_prompt = ai_config.PROMPT_PLAN_DESIGN.format(
            plan_type=plan_text,
            style_text=style_text
        )

    # Сценарий "Редизайн интерьера"
    elif room_type and furniture is None:
        room_text = ROOM_TYPE_PROMPTS.get(room_type, "room")
        # Используем шаблон из конфига
        final_prompt = ai_config.PROMPT_REDESIGN.format(
            base_prompt=base_prompt,
            room_type=room_text,
            style_text=style_text
        )

    # Сценарий "Дизайн с нуля"
    elif room_type and furniture:
        room_text = ROOM_TYPE_PROMPTS.get(room_type, "room")
        furniture_text = FURNITURE_PROMPTS.get(furniture, "")
        # Используем шаблон из конфига
        final_prompt = ai_config.PROMPT_ZERO_DESIGN.format(
            base_prompt=base_prompt,
            room_type=room_text,
            furniture_text=furniture_text,
            style_text=style_text
        )
    else:
        # Запасной вариант
        final_prompt = f"{base_prompt}, {style_text}"

    # Очищаем от лишних пробелов и запятых
    return ", ".join(part.strip() for part in final_prompt.split(',') if part.strip())


def create_floor_plan_prompt(visualization_style: str, interior_style: str) -> str:
    base_instructions = ai_config.FLOOR_PLAN_BASE_INSTRUCTIONS

    if visualization_style == 'sketch':
        visualization_block = ai_config.FLOOR_PLAN_VISUALIZATION_SKETCH
    else:
        visualization_block = ai_config.FLOOR_PLAN_VISUALIZATION_REALISTIC

    # ⬇️ здесь была ошибка: блок с {interior_style} не форматировался
    final_instructions = ai_config.FLOOR_PLAN_FINAL_INSTRUCTIONS.format(
        interior_style=interior_style
    )

    full_prompt = f"{base_instructions.strip()}\n\n{visualization_block.strip()}\n\n{final_instructions.strip()}"
    return full_prompt
