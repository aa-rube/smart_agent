#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\prompt_factory.py
from typing import Optional, Dict, Any, List, Tuple

from executor.ai_config import *



#OPEN AI - ОТРАБОТКА ВОЗРАЖЕНИЙ КЛИЕНТОВ
def build_objection_request(
    question: str,
    model: Optional[str] = None) -> Dict[str, Any]:
    """
    Единственное место, где формируется payload для OpenAI Chat Completion.
    """
    system_prompt = OBJECTION_PROMPT_DEFAULT_RU
    use_model = model or OBJECTION_MODEL
    return {
        "model": use_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": question},
        ],
    }


def _safe(val: Any) -> str:
    """
    Робастный safe-cast в строку для любых типов:
      - None  -> "—"
      - int/float -> компактная форма без хвоста .0
      - bool -> "Да"/"Нет"
      - list/tuple/set -> через запятую (каждый элемент прогоняется через _safe)
      - иное -> str().strip() с фолбэком "—"
    """
    if val is None:
        return "—"
    if isinstance(val, (int, float)):
        try:
            return f"{val:.15g}"
        except Exception:
            return str(val)
    if isinstance(val, bool):
        return "Да" if val else "Нет"
    if isinstance(val, (list, tuple, set)):
        parts = [ _safe(x) for x in val ]
        parts = [ p for p in parts if p and p != "—" ]
        return ", ".join(parts) if parts else "—"
    s = str(val).strip()
    return s or "—"

# вспомогательный лимитер длины
def _cut(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1]



# ------------------------------------------------------------------
# Билдер для саммари переговоров (используется в /summary/analyze)
# ------------------------------------------------------------------
def build_summary_analyze_request(
    *,
    transcript_text: str,
    model: str,
    prefer_language: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Собирает payload для Chat Completions в JSON-формате.
    Использует шаблоны из ai_config.py и конкатенирует полученный текст диалога.
    Возвращает (payload, debug_system_prompt).
    """
    lang = prefer_language or "the language of the conversation"

    # system: задача + чек-лист + схема JSON
    sys_prompt = REALTY_SUMMARY_TASK_TMPL.format(
        CHECKLIST=REALTY_CHECKLIST,
        SCHEMA=REALTY_SUMMARY_JSON_SCHEMA,
        LANGUAGE=lang,
    )

    # user: сам текст диалога (обрезаем, чтобы не улететь в лимиты)
    user_prompt = SUMMARY_ANALYZE_USER_TMPL.format(
        TEXT=_cut(transcript_text, 16000)
    )

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }

    return payload, sys_prompt