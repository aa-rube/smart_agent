# smart_agent/executor/openai_service.py
from __future__ import annotations
from typing import Optional, Dict, Any, List, Tuple
import os, logging
from openai import OpenAI

from executor.config import OPENAI_API_KEY
from executor.ai_config import OBJECTION_MODEL, DESCRIPTION_MODEL, FEEDBACK_MODEL
from executor.prompt_factory import (
    build_objection_request,
    build_description_request,
    build_description_request_from_fields,
    build_feedback_generate_request,
    build_feedback_mutate_request,
)

LOG = logging.getLogger(__name__)
HTTP_DEBUG = os.getenv("HTTP_DEBUG", "0") == "1"
OPENAI_FALLBACK = os.getenv("OPENAI_FALLBACK", "1") == "1"

_client: Optional[OpenAI] = None

def _client_or_init() -> OpenAI:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is missing")
        _client = OpenAI(api_key=OPENAI_API_KEY)
    return _client

# сначала полноразмерные, потом мини
_FALLBACK_MODELS: List[str] = ["gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4.1-mini"]

def _log_request(payload: Dict[str, Any]) -> None:
    if HTTP_DEBUG:
        LOG.info(
            "OpenAI request: model=%s temp=%s max_tokens=%s messages=%d",
            payload.get("model"), payload.get("temperature"),
            payload.get("max_tokens"), len(payload.get("messages", [])),
        )

def _extract_text(resp) -> str:
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""

def _extract_texts(resp) -> List[str]:
    try:
        texts: List[str] = []
        for ch in getattr(resp, "choices", []) or []:
            txt = (ch.message.content or "").strip()
            if txt:
                texts.append(txt)
        return texts
    except Exception:
        return []

def _send_with_fallback(payload: Dict[str, Any], default_model: str, allow_fallback: bool) -> Tuple[str, str]:
    client = _client_or_init()
    _log_request(payload)

    first_model = payload.get("model") or default_model
    chain = [first_model] + ([m for m in _FALLBACK_MODELS if m != first_model] if allow_fallback else [])

    last_err: Optional[Exception] = None
    for i, model_name in enumerate(chain, start=1):
        try:
            req = dict(payload); req["model"] = model_name
            resp = client.chat.completions.create(**req)
            text = _extract_text(resp)
            if text:
                if i > 1:
                    LOG.warning("Fallback model used: %s (requested %s)", model_name, first_model)
                return text, model_name
            last_err = RuntimeError("Empty completion text")
        except Exception as e:
            last_err = e
            LOG.warning("OpenAI call failed on model %s: %s", model_name, e)

    LOG.error("All OpenAI fallbacks failed. Last error: %s", last_err)
    raise last_err or RuntimeError("OpenAI request failed")

def _send_with_fallback_list(payload: Dict[str, Any], default_model: str, allow_fallback: bool) -> Tuple[List[str], str]:
    """
    То же, что _send_with_fallback, но возвращает список вариантов (использует параметр n в Chat Completions).
    """
    client = _client_or_init()
    _log_request(payload)

    first_model = payload.get("model") or default_model
    chain = [first_model] + ([m for m in _FALLBACK_MODELS if m != first_model] if allow_fallback else [])

    last_err: Optional[Exception] = None
    for i, model_name in enumerate(chain, start=1):
        try:
            req = dict(payload); req["model"] = model_name
            resp = client.chat.completions.create(**req)
            texts = _extract_texts(resp)
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

# ---- public ----

def send_objection_generate_request(question: str, allow_fallback: bool = OPENAI_FALLBACK) -> Tuple[str, str]:
    payload = build_objection_request(
        question=question,
        model=OBJECTION_MODEL
    )
    return _send_with_fallback(
        payload,
        default_model=OBJECTION_MODEL,
        allow_fallback=allow_fallback
    )

def send_description_generate_request_from_fields(fields: Dict[str, Optional[str]],
                                                  allow_fallback: bool = OPENAI_FALLBACK) -> Tuple[str, str]:
    payload = build_description_request_from_fields(
        fields=fields,
        model=DESCRIPTION_MODEL
    )
    return _send_with_fallback(
        payload,
        default_model=DESCRIPTION_MODEL,
        allow_fallback=allow_fallback
    )

# (оставляем для совместимости путь через 'question', если когда-то прилетит)
def send_description_generate_request(question: str, allow_fallback: bool = OPENAI_FALLBACK) -> Tuple[str, str]:
    payload = build_description_request(
        question=question,
        model=DESCRIPTION_MODEL)
    return _send_with_fallback(payload,
                               default_model=DESCRIPTION_MODEL,
                               allow_fallback=allow_fallback)

# -------- NEW: FEEDBACK / REVIEW --------
def send_feedback_generate_request(payload_fields: Dict[str, Any],
                                   num_variants: int = 3,
                                   allow_fallback: bool = OPENAI_FALLBACK) -> Tuple[List[str], str, Dict[str, Any]]:
    """
    Возвращает (variants, model_used, debug_message_dict).
    """
    payload, debug_msg = build_feedback_generate_request(
        fields=payload_fields,
        num_variants=num_variants,
        model=FEEDBACK_MODEL
    )
    texts, used_model = _send_with_fallback_list(
        payload,
        default_model=FEEDBACK_MODEL,
        allow_fallback=allow_fallback
    )
    return texts, used_model, debug_msg


def send_feedback_mutate_request(*,
                                 base_text: str,
                                 operation: str,
                                 style: Optional[str],
                                 tone: Optional[str],
                                 length: Optional[str],
                                 context: Dict[str, Any] | None = None,
                                 allow_fallback: bool = OPENAI_FALLBACK) -> Tuple[str, str, Dict[str, Any]]:
    """
    Возвращает (new_text, model_used, debug_message_dict).
    """
    payload, debug_msg = build_feedback_mutate_request(
        base_text=base_text,
        operation=operation,
        style=style,
        tone=tone,
        length=length,
        context=context or {},
        model=FEEDBACK_MODEL,
    )
    text, used_model = _send_with_fallback(
        payload,
        default_model=FEEDBACK_MODEL,
        allow_fallback=allow_fallback
    )
    return text, used_model, debug_msg
