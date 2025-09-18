#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\prompt_factory.py

import random
from typing import Optional, Dict, Any, List, Tuple

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


#OPEN AI - ОТРАБОТКА ВОЗРАЖЕНИЙ КЛИЕНТОВ
def build_objection_request(
    question: str,
    model: Optional[str] = None,
    temperature: float = 0.3,
    max_tokens: int = 700) -> Dict[str, Any]:
    """
    Единственное место, где формируется payload для OpenAI Chat Completion.
    """
    system_prompt = ai_config.OBJECTION_PROMPT_DEFAULT_RU
    use_model = model or ai_config.OBJECTION_MODEL
    return {
        "model": use_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    }


# --- было: build_description_request(question) — оставляем для бэкапа
def build_description_request(*, question: str, model: Optional[str] = None,
                              temperature: float = 0.7, max_tokens: int = 1200) -> Dict[str, Any]:
    system_prompt = ai_config.DESCRIPTION_PROMPT_DEFAULT_RU
    use_model = model or ai_config.DESCRIPTION_MODEL
    return {
        "model": use_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    }

# --------- НОВОЕ: сборка текста из сырых полей ----------
def _label(m: Dict[str, str], key: Optional[str], default: str = "—") -> str:
    return m.get(key, default) if key else default

def compose_description_user_message(fields: Dict[str, Optional[str]]) -> str:
    """
    Сборка пользовательского сообщения для описания на основе сырых полей.
    Ожидаемые ключи:
      type, apt_class (только для flat), in_complex, area, comment
    """
    t_key  = fields.get("type")
    c_key  = fields.get("apt_class") if t_key == "flat" else None
    x_key  = fields.get("in_complex")
    a_key  = fields.get("area")
    cmt    = (fields.get("comment") or "").strip()

    t_label  = _label(ai_config.DESCRIPTION_TYPES,   t_key)
    cls_lbl  = _label(ai_config.DESCRIPTION_CLASSES, c_key) if c_key else "—"
    cx_label = _label(ai_config.DESCRIPTION_COMPLEX, x_key)
    ar_label = _label(ai_config.DESCRIPTION_AREA,    a_key)
    comment  = cmt or "—"

    return (
        "Сгенерируй продающее, информативное описание объекта недвижимости "
        "для объявления и презентации. Соблюдай гайд Х–П–В и заверши явным CTA.\n\n"
        f"Тип: {t_label}\n"
        f"Класс (если квартира): {cls_lbl}\n"
        f"Новостройка/ЖК: {cx_label}\n"
        f"Расположение: {ar_label}\n"
        f"Комментарий риелтора: {comment}"
    )

def build_description_request_from_fields(
    *,
    fields: Dict[str, Optional[str]],
    model: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 1200,
) -> Dict[str, Any]:
    """
    ЕДИНОЕ место сборки payload из сырых полей.
    """
    user_message = compose_description_user_message(fields)
    system_prompt = ai_config.DESCRIPTION_PROMPT_DEFAULT_RU
    use_model = model or ai_config.DESCRIPTION_MODEL
    return {
        "model": use_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }


# ===================== NEW: FEEDBACK / REVIEW =====================
_DEAL_TITLES = {
    "sale":              "Продажа",
    "buy":               "Покупка",
    "rent":              "Аренда",
    "mortgage":          "Ипотека",
    "social_mortgage":   "Гос. поддержка",
    "maternity_capital": "Материнский капитал",
    "custom":            "Другое",
}

def _humanize_deal(deal_csv: Optional[str], custom: Optional[str]) -> str:
    codes = [c.strip() for c in (deal_csv or "").split(",") if c and c.strip()]
    names: List[str] = []
    for c in codes:
        if c == "custom":
            continue
        names.append(_DEAL_TITLES.get(c, c))
    if custom:
        names.append(f"Другое: {custom}")
    return ", ".join(names) if names else "—"

def _tone_label(key: Optional[str]) -> str:
    return ai_config.FEEDBACK_TONES.get(key or "", "нейтральный")

def _length_hint(key: Optional[str]) -> str:
    return ai_config.FEEDBACK_LENGTH_HINTS.get(key or "", "до ~450 знаков")

def _length_target_tokens(key: Optional[str]) -> int:
    # приблизительно: 1 токен ~ 3–4 символа для RU; делаем с запасом
    if key == "short":
        return 256
    if key == "long":
        return 900
    return 512  # medium


def _safe(s: Optional[str]) -> str:
    return (s or "").strip() or "—"


def build_feedback_generate_request(*,
                                    fields: Dict[str, Optional[str]],
                                    num_variants: int = 3,
                                    model: Optional[str] = None,
                                    temperature: float = 0.6) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Сборка payload для генерации черновиков.
    Возвращает (payload для OpenAI, debug_info).
    """
    # Поддержка старого стиля (style) и нового разделения (tone/length)
    tone_key   = fields.get("tone") or fields.get("style")  # back-compat
    length_key = fields.get("length") or ("long" if (fields.get("style") == "long") else ("short" if fields.get("style") == "brief" else "medium"))

    tone_label   = _tone_label(tone_key)
    length_hint  = _length_hint(length_key)
    max_tokens   = _length_target_tokens(length_key)
    deal_human   = _humanize_deal(fields.get("deal_type"), fields.get("deal_custom"))

    system_prompt = ai_config.FEEDBACK_PROMPT_SYSTEM_RU
    user_message = ai_config.FEEDBACK_USER_TEMPLATE_RU.format(
        client_name=_safe(fields.get("client_name")),
        agent_name=_safe(fields.get("agent_name")),
        company=_safe(fields.get("company")),
        city=_safe(fields.get("city")),
        address=_safe(fields.get("address")),
        deal_human=deal_human,
        situation=_safe(fields.get("situation")),
        tone=tone_label,
        style=_safe(fields.get("style")),
        length_hint=length_hint,
    )

    use_model = model or ai_config.FEEDBACK_MODEL
    payload = {
        "model": use_model,
        "n": max(1, int(num_variants)),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    debug = {
        "tone": tone_label,
        "length": length_key,
        "length_hint": length_hint,
        "deal_human": deal_human,
    }
    return payload, debug


def build_feedback_mutate_request(*,
                                  base_text: str,
                                  operation: str,            # 'short' | 'long' | 'style'
                                  style: Optional[str],
                                  tone: Optional[str],
                                  length: Optional[str],
                                  context: Dict[str, Optional[str]],
                                  model: Optional[str] = None,
                                  temperature: float = 0.5) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Сборка payload для мутации текста.
    """
    tone_key   = tone or style  # back-compat
    length_key = length

    tone_label  = _tone_label(tone_key)
    length_hint = _length_hint(length_key)
    max_tokens  = _length_target_tokens(length_key)
    deal_human  = _humanize_deal(context.get("deal_type"), context.get("deal_custom"))

    system_prompt = ai_config.FEEDBACK_MUTATE_SYSTEM_RU

    instruction = ""
    if operation == "short":
        instruction = f"Сократи текст до {length_hint} без потери смысла, сохранив структуру и читабельность."
    elif operation == "long":
        instruction = f"Раскрой и расширь текст (но без «воды») до {length_hint}, усилив доказательность и CTA."
    elif operation == "style":
        instruction = f"Перепиши текст в тоне: {tone_label}. Длина: {length_hint}."
    else:
        instruction = "Отредактируй текст, сохранив факты и усилив убедительность."

    # user message
    user_message = ai_config.FEEDBACK_MUTATE_USER_TEMPLATE_RU.format(
        instruction=instruction,
        base_text=base_text,
        client_name=_safe(context.get("client_name")),
        agent_name=_safe(context.get("agent_name")),
        company=_safe(context.get("company")),
        city=_safe(context.get("city")),
        address=_safe(context.get("address")),
        deal_human=deal_human,
        situation=_safe(context.get("situation")),
        tone=tone_label,
        length_hint=length_hint,
    )

    use_model = model or ai_config.FEEDBACK_MODEL
    payload = {
        "model": use_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    debug = {
        "operation": operation,
        "tone": tone_label,
        "length_hint": length_hint,
        "deal_human": deal_human,
    }
    return payload, debug
