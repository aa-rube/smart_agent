# smart_agent/executor/apps/review_generator.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import os
import logging
import re

from flask import jsonify, Request
from openai import OpenAI

from executor.config import OPENAI_API_KEY

LOG = logging.getLogger(__name__)

# --- поведение HTTP-логгера и фолбэка можно управлять через ENV ---
HTTP_DEBUG = os.getenv("HTTP_DEBUG", "0") == "1"
OPENAI_FALLBACK = os.getenv("OPENAI_FALLBACK", "1") == "1"

# Сначала полноразмерные, затем «мини». Первый элемент будет заменён на FEEDBACK_MODEL.
_FALLBACK_MODELS: List[str] = ["gpt-5", "gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4.1-mini"]

_client: Optional[OpenAI] = None

_DEAL_TITLES = {
    "sale": "Продажа",
    "buy": "Покупка",
    "rent": "Аренда",
    "mortgage": "Ипотека",
    "social_mortgage": "Гос. поддержка",
    "maternity_capital": "Материнский капитал",
    "custom": "Другое",
}

FEEDBACK_MODEL = os.getenv('FEEDBACK_MODEL', 'gpt-5')

# Модели для анализа транскриптов и транскрибации
SUMMARY_MODEL = os.getenv("SUMMARY_MODEL", "gpt-5")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "whisper-1")

# --- Мэппинги «тон оф войс» и «длина» (расширяемые) ---
FEEDBACK_TONES = {
    "friendly": "дружелюбный, тёплый, поддерживающий",
    "neutral":  "нейтральный, деловой, без эмоций",
    "formal":   "официальный, сухой, без эмоциональных оценок",
    "expert":   "экспертный, уверенный, с лёгкими пояснениями",
}

# Ключ -> подсказка для длины (в символах, ориентир для модели/редактора)
FEEDBACK_LENGTH_HINTS = {
    "short":  "От 100 до ≈250 знаков",
    "medium": "0т 400 до ≈450 знаков",
    "long":   "От 1000 до ≈1200 знаков",
}

# --- SYSTEM-промпт и шаблоны сообщений для генерации/мутаций ---
FEEDBACK_PROMPT_SYSTEM_RU = '''
Ты — помощник риэлтора. Твоя задача — писать короткие продающие черновики-отзывы о работе агента.
Требования к тексту:
- Без выдуманных фактов: опирайся только на переданные данные.
- Пиши просто и по делу, без канцелярита и клише «уютный/светлый».
- Структура (рекомендация): контекст → суть работы → сложности/как решили → результат/выгода для клиента → призыв к действию (CTA).
- Сохраняй заданный тон оф войс и целевую длину.
- Никакой разметки Markdown/HTML, только чистый текст.
'''

# Шаблон пользовательского сообщения для генерации
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
'''

# Промпты для мутаций
FEEDBACK_MUTATE_SYSTEM_RU = '''
Ты — редактор текста риэлтора. Правь текст максимально аккуратно:
- Не добавляй вымышленные факты.
- Сохраняй смысл, усиливай ясность и продающий фокус.
- Следуй инструкциям по тону/длине.
- Итог — только чистый текст, без разметки.
'''

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
'''

# =========================
#   OpenAI client helpers
# =========================
def _client_or_init() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is missing")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client


def _log_request(payload: Dict[str, Any]) -> None:
    if HTTP_DEBUG:
        LOG.info(
            "OpenAI request: model=%s n=%s messages=%d",
            payload.get("model"),
            payload.get("n"),
            len(payload.get("messages", [])),
        )


def _extract_text(resp) -> str:
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _extract_texts(resp) -> List[str]:
    out: List[str] = []
    try:
        for ch in getattr(resp, "choices", []) or []:
            txt = (ch.message.content or "").strip()
            if txt:
                out.append(txt)
    except Exception:
        pass
    return out


def _send_with_fallback(payload: Dict[str, Any], default_model: str, allow_fallback: bool) -> Tuple[str, str]:
    client = _client_or_init()
    _log_request(payload)

    first_model = payload.get("model") or default_model
    chain = [first_model] + ([m for m in _FALLBACK_MODELS if m != first_model] if allow_fallback else [])

    last_err: Optional[Exception] = None
    for i, model_name in enumerate(chain, start=1):
        try:
            req = dict(payload)
            req["model"] = model_name
            resp = client.chat.completions.create(**req)
            text = _extract_text(resp)
            if text:
                if i > 1:
                    LOG.warning("Fallback model used: %s (requested %s)", model_name, first_model)
                return _cleanup(text), model_name
            last_err = RuntimeError("Empty completion text")
        except Exception as e:
            last_err = e
            LOG.warning("OpenAI call failed on model %s: %s", model_name, e)

    LOG.error("All OpenAI fallbacks failed. Last error: %s", last_err)
    raise last_err or RuntimeError("OpenAI request failed")


def _send_with_fallback_list(payload: Dict[str, Any], default_model: str, allow_fallback: bool) -> Tuple[List[str], str]:
    client = _client_or_init()
    _log_request(payload)

    first_model = payload.get("model") or default_model
    chain = [first_model] + ([m for m in _FALLBACK_MODELS if m != first_model] if allow_fallback else [])

    last_err: Optional[Exception] = None
    for i, model_name in enumerate(chain, start=1):
        try:
            req = dict(payload)
            req["model"] = model_name
            resp = client.chat.completions.create(**req)
            texts = [_cleanup(t) for t in _extract_texts(resp)]
            if texts:
                if i > 1:
                    LOG.warning("Fallback model used: %s (requested %s)", model_name, first_model)
                return texts, model_name
            last_err = RuntimeError("Empty completion list")
        except Exception as e:
            last_err = e
            LOG.warning("OpenAI call failed on model %s: %s", model_name, e)

    LOG.error("All OpenAI fallbacks failed. Last error: %s", last_err)
    raise last_err or RuntimeError("OpenAI request failed")


# =========================
#   Formatting helpers
# =========================
def _cleanup(s: str) -> str:
    """
    Снимаем возможные ограждения ```...```, <code>...</code> и лишние кавычки.
    """
    if not s:
        return ""
    s = s.strip()

    # ```...``` блоки
    m = re.match(r"^```(?:\w+)?\s*(.*?)\s*```$", s, flags=re.S)
    if m:
        s = m.group(1).strip()

    # <code>...</code>
    m2 = re.match(r"^<code>(.*?)</code>$", s, flags=re.S | re.I)
    if m2:
        s = m2.group(1).strip()

    # ведущие/замыкающие кавычки
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()

    return s


def _safe(val: Any) -> str:
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
    return FEEDBACK_TONES.get((key or "").strip(), FEEDBACK_TONES.get("neutral", "нейтральный"))


def _length_hint(key: Optional[str]) -> str:
    return FEEDBACK_LENGTH_HINTS.get((key or "").strip(), "до ≈450 знаков")


def _length_target_tokens(key: Optional[str]) -> int:
    # Примерная прикидка для RU (1 токен ~ 3–4 знака). Даем запас.
    k = (key or "").strip()
    if k == "short":
        return 256
    if k == "long":
        return 900
    return 512  # medium


# =========================
#   Payload builders
# =========================
def _build_generate_payload(*, fields: Dict[str, Optional[str]], num_variants: int, model: Optional[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    # Back-compat: допускаем старое поле style для тона/длины
    tone_key = fields.get("tone") or fields.get("style")
    # если кто-то прокинул style="long"/"brief" — маппим в целевую длину
    if fields.get("length"):
        length_key = fields.get("length")
    else:
        st = (fields.get("style") or "").strip().lower()
        length_key = "long" if st == "long" else ("short" if st in ("brief", "short") else "medium")

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

    use_model = (model or FEEDBACK_MODEL).strip()
    payload: Dict[str, Any] = {
        "model": use_model,
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
        "system": system_prompt,
        "user": user_message,
    }
    return payload, debug


def _build_mutate_payload(
    *,
    base_text: str,
    operation: str,
    style: Optional[str],
    tone: Optional[str],
    length: Optional[str],
    context: Dict[str, Optional[str]],
    model: Optional[str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    tone_key = tone or style
    tone_label = _tone_label(tone_key)
    length_hint = _length_hint(length)
    deal_human = _humanize_deal(context.get("deal_type"), context.get("deal_custom"))

    if operation == "short":
        instruction = f"Сократи текст до {length_hint} без потери смысла, сохранив структуру и читабельность."
    elif operation == "long":
        instruction = f"Раскрой и расширь текст (но без «воды») до {length_hint}, усилив доказательность и CTA."
    elif operation == "style":
        instruction = f"Перепиши текст в тоне: {tone_label}. Длина: {length_hint}."
    else:
        instruction = "Отредактируй текст, сохранив факты и усилив убедительность."

    system_prompt = FEEDBACK_MUTATE_SYSTEM_RU
    user_message = FEEDBACK_MUTATE_USER_TEMPLATE_RU.format(
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

    use_model = (model or FEEDBACK_MODEL).strip()
    target_tokens = _length_target_tokens(length)
    payload: Dict[str, Any] = {
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
        "system": system_prompt,
        "user": user_message,
    }
    return payload, debug


# =========================
#   Public Flask handlers
# =========================
def review_generate(req: Request):
    """
    POST /review/generate

    Ожидает JSON:
      {
        client_name, agent_name, company, city, address,
        deal_type,   # CSV кодов: "sale,buy,custom" (опционально)
        deal_custom, # если был выбор "Другое"
        situation,   # >= 50 символов
        style,       # (legacy) общий стиль
        tone,        # новый отдельный «тон»
        length,      # short|medium|long
        num_variants # 1..5 (default=3)
      }
    """
    data = req.get_json(silent=True) or {}
    debug_flag = req.args.get("debug") == "1"

    situation = (data.get("situation") or "").strip()
    if len(situation) < 50:
        return jsonify({"error": "bad_request", "detail": "field 'situation' must be >= 50 chars"}), 400

    try:
        num_variants = int(data.get("num_variants") or 3)
    except Exception:
        return jsonify({"error": "bad_request", "detail": "field 'num_variants' must be integer"}), 400
    if not (1 <= num_variants <= 5):
        return jsonify({"error": "bad_request", "detail": "num_variants must be in range 1..5"}), 400

    fields = {
        "client_name": data.get("client_name"),
        "agent_name": data.get("agent_name"),
        "company": data.get("company"),
        "city": data.get("city"),
        "address": data.get("address"),
        "deal_type": data.get("deal_type"),
        "deal_custom": data.get("deal_custom"),
        "situation": situation,
        "style": data.get("style"),
        "tone": data.get("tone"),
        "length": data.get("length"),
    }

    try:
        payload, debug_info = _build_generate_payload(fields=fields, num_variants=num_variants, model=FEEDBACK_MODEL)
        texts, used_model = _send_with_fallback_list(payload, default_model=FEEDBACK_MODEL, allow_fallback=OPENAI_FALLBACK)

        body: Dict[str, Any] = {"variants": texts}
        if debug_flag:
            body["debug"] = {"model_used": used_model, **debug_info}
        return jsonify(body), 200

    except Exception as e:
        LOG.exception("OpenAI error (review_generate)")
        body = {"error": "openai_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"model": FEEDBACK_MODEL}
        return jsonify(body), 502


def review_mutate(req: Request):
    """
    POST /review/mutate
    Ожидает JSON:
      {
        base_text: str,
        operation: 'short' | 'long' | 'style',
        style:     str | None,
        tone:      str | None,
        length:    str | None,   # target length (short|medium|long)
        context:   { опционально тот же набор полей, что и в generate }
      }
    """
    data = req.get_json(silent=True) or {}

    base_text = (data.get("base_text") or "").strip()
    operation = (data.get("operation") or "").strip()
    style = data.get("style")
    tone = data.get("tone")
    length = data.get("length")
    context = data.get("context") or {}
    debug_flag = req.args.get("debug") == "1"

    if not base_text:
        return jsonify({"error": "bad_request", "detail": "field 'base_text' is required"}), 400
    if operation not in ("short", "long", "style"):
        return jsonify({"error": "bad_request", "detail": "field 'operation' must be one of short|long|style"}), 400

    try:
        payload, debug_info = _build_mutate_payload(
            base_text=base_text,
            operation=operation,
            style=style,
            tone=tone,
            length=length,
            context=context,
            model=FEEDBACK_MODEL,
        )
        text, used_model = _send_with_fallback(payload, default_model=FEEDBACK_MODEL, allow_fallback=OPENAI_FALLBACK)

        body: Dict[str, Any] = {"text": text}
        if debug_flag:
            body["debug"] = {"model_used": used_model, **debug_info}
        return jsonify(body), 200

    except Exception as e:
        LOG.exception("OpenAI error (review_mutate)")
        body = {"error": "openai_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"model": FEEDBACK_MODEL}
        return jsonify(body), 502
