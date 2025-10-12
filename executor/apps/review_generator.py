# smart_agent/executor/apps/review_generator.py
from __future__ import annotations

import os
import re
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import jsonify, Request

try:
    # Если есть общий конфиг – используем его ключ; иначе падаем на ENV
    from executor.config import OPENAI_API_KEY  # type: ignore
except Exception:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ==== OpenAI client (локальный, без внешних зависимостей на сервисы) ====
try:
    from openai import OpenAI
except Exception as e:
    raise RuntimeError("OpenAI package is required: pip install openai") from e

LOG = logging.getLogger(__name__)

# ==== Параметры/дефолты окружения ====
FEEDBACK_MODEL = os.getenv("FEEDBACK_MODEL", "gpt-5")
HTTP_DEBUG = os.getenv("HTTP_DEBUG", "0") == "1"
OPENAI_FALLBACK = os.getenv("OPENAI_FALLBACK", "1") == "1"

# сначала полноразмерные, затем мини — порядок важен
_FALLBACK_MODELS: List[str] = ["gpt-5", "gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4.1-mini"]

_client: Optional[OpenAI] = None


def _client_or_init() -> OpenAI:
    """Ленивое создание клиента OpenAI."""
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is missing")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _log_request(payload: Dict[str, Any]) -> None:
    if HTTP_DEBUG:
        LOG.info(
            "OpenAI request: model=%s temp=%s max_tokens=%s messages=%d n=%s",
            payload.get("model"),
            payload.get("temperature"),
            payload.get("max_tokens"),
            len(payload.get("messages", [])),
            payload.get("n"),
        )


def _extract_text(resp) -> str:
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _extract_texts(resp) -> List[str]:
    try:
        out: List[str] = []
        for ch in getattr(resp, "choices", []) or []:
            txt = (ch.message.content or "").strip()
            if txt:
                out.append(txt)
        return out
    except Exception:
        return []


def _send_with_fallback(payload: Dict[str, Any], default_model: str, allow_fallback: bool) -> Tuple[str, str]:
    client = _client_or_init()
    _log_request(payload)
    first = payload.get("model") or default_model
    chain = [first] + ([m for m in _FALLBACK_MODELS if m != first] if allow_fallback else [])

    last_err: Optional[Exception] = None
    for i, model_name in enumerate(chain, start=1):
        try:
            req = dict(payload); req["model"] = model_name
            resp = client.chat.completions.create(**req)
            text = _extract_text(resp)
            if text:
                if i > 1:
                    LOG.warning("Fallback model used: %s (requested %s)", model_name, first)
                return text, model_name
            last_err = RuntimeError("Empty completion text")
        except Exception as e:
            last_err = e
            LOG.warning("OpenAI call failed on model %s: %s", model_name, e)

    LOG.error("All OpenAI fallbacks failed. Last error: %s", last_err)
    raise last_err or RuntimeError("OpenAI request failed")


def _send_with_fallback_list(payload: Dict[str, Any], default_model: str, allow_fallback: bool) -> Tuple[List[str], str]:
    client = _client_or_init()
    _log_request(payload)
    first = payload.get("model") or default_model
    chain = [first] + ([m for m in _FALLBACK_MODELS if m != first] if allow_fallback else [])

    last_err: Optional[Exception] = None
    for i, model_name in enumerate(chain, start=1):
        try:
            req = dict(payload); req["model"] = model_name
            resp = client.chat.completions.create(**req)
            texts = _extract_texts(resp)
            if texts:
                if i > 1:
                    LOG.warning("Fallback model used: %s (requested %s)", model_name, first)
                return texts, model_name
            last_err = RuntimeError("Empty completion list")
        except Exception as e:
            last_err = e
            LOG.warning("OpenAI call failed on model %s: %s", model_name, e)

    LOG.error("All OpenAI fallbacks failed. Last error: %s", last_err)
    raise last_err or RuntimeError("OpenAI request failed")


# ===================== Доменная логика отзывов (всё в одном файле) =====================

# Мэппинги «тон оф войс» и «длина»
FEEDBACK_TONES: Dict[str, str] = {
    "friendly": "дружелюбный, тёплый, поддерживающий",
    "neutral":  "нейтральный, деловой, без эмоций",
    "formal":   "официальный, сухой, без эмоциональных оценок",
    "expert":   "экспертный, уверенный, с лёгкими пояснениями",
}

FEEDBACK_LENGTH_HINTS: Dict[str, str] = {
    "short":  "≈250 знаков",
    "medium": "до ≈450 знаков",
    "long":   "до ≈1200 знаков",
}

# Типы сделок (человеческие подписи)
_DEAL_TITLES: Dict[str, str] = {
    "sale":              "Продажа",
    "buy":               "Покупка",
    "rent":              "Аренда",
    "mortgage":          "Ипотека",
    "social_mortgage":   "Гос. поддержка",
    "maternity_capital": "Материнский капитал",
    "custom":            "Другое",
}

# SYSTEM / USER промпты
FEEDBACK_PROMPT_SYSTEM_RU = '''
Ты — помощник риэлтора. Твоя задача — писать короткие продающие черновики-отзывы о работе агента.
Требования к тексту:
- Без выдуманных фактов: опирайся только на переданные данные.
- Пиши просто и по делу, без канцелярита и клише «уютный/светлый».
- Структура (рекомендация): контекст → суть работы → сложности/как решили → результат/выгода для клиента → призыв к действию (CTA).
- Сохраняй заданный тон оф войс и целевую длину.
- Никакой разметки Markdown/HTML, только чистый текст.
'''.strip()

FEEDBACK_USER_TEMPLATE_RU = '''
Сгенерируй 1 вариант черновика-отзыва на основе данных. Учитывай тон и целевую длину.

Клиент: {client_name}
Агент: {agent_name}
Компания: {company}
Город/адрес: {city}, {address}
Тип сделки: {deal_human}
Ситуация (что делали, сроки, сложность, итог): {situation}

Тон оф войс: {tone}
Стиль/регист: {style}
Целевая длина: {length_hint}

Верни только сам текст отзыва, без заголовков, списков и разметки.
'''.strip()

FEEDBACK_MUTATE_SYSTEM_RU = '''
Ты — редактор текста риэлтора. Правь текст максимально аккуратно:
- Не добавляй вымышленные факты.
- Сохраняй смысл, усиливай ясность и продающий фокус.
- Следуй инструкциям по тону/длине.
- Итог — только чистый текст, без разметки.
'''.strip()

FEEDBACK_MUTATE_USER_TEMPLATE_RU = '''
Инструкция: {instruction}

Исходный текст:
---
{base_text}
---

Контекст (для точности формулировок):
Клиент: {client_name}
Агент: {agent_name}
Компания: {company}
Город/адрес: {city}, {address}
Тип сделки: {deal_human}
Ситуация: {situation}
Тон: {tone}
Целевая длина: {length_hint}

Верни только исправленный текст, без пояснений.
'''.strip()


# ===================== Утилиты форматирования =====================

def _safe(val: Any) -> str:
    """Робастный safe-cast к строке для любых типов."""
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
        parts = [_safe(x) for x in val]
        parts = [p for p in parts if p and p != "—"]
        return ", ".join(parts) if parts else "—"
    s = str(val).strip()
    return s or "—"


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
    return FEEDBACK_TONES.get((key or "").strip().lower(), "нейтральный")


def _length_hint(key: Optional[str]) -> str:
    return FEEDBACK_LENGTH_HINTS.get((key or "").strip().lower(), "до ~450 знаков")


def _normalize_length_for_legacy(style: Optional[str], length: Optional[str]) -> str:
    """
    Back-compat: если раньше передавали style == 'long'|'brief', конвертируем.
    """
    if length:
        return length
    s = (style or "").strip().lower()
    if s == "long":
        return "long"
    if s in ("brief", "short"):
        return "short"
    return "medium"


# ===================== Сборка payload-ов =====================

def _build_generate_payload(fields: Dict[str, Optional[str]],
                            num_variants: int = 3,
                            model: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    tone_key = fields.get("tone") or fields.get("style")
    length_key = _normalize_length_for_legacy(fields.get("style"), fields.get("length"))

    tone_label = _tone_label(tone_key)
    length_hint = _length_hint(length_key)
    deal_human = _humanize_deal(fields.get("deal_type"), fields.get("deal_custom"))

    system_prompt = FEEDBACK_PROMPT_SYSTEM_RU
    user_message = FEEDBACK_USER_TEMPLATE_RU.format(
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

    use_model = model or FEEDBACK_MODEL
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


def _build_mutate_payload(*,
                          base_text: str,
                          operation: str,            # 'short' | 'long' | 'style'
                          style: Optional[str],
                          tone: Optional[str],
                          length: Optional[str],
                          context: Dict[str, Optional[str]],
                          model: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    tone_key = tone or style
    # если length не указан, подставим из операции разумный таргет
    if not length:
        if operation == "short":
            length = "short"
        elif operation == "long":
            length = "long"
        else:
            length = _
