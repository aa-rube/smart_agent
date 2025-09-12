# smart_agent/executor/openai_service.py

from __future__ import annotations
from typing import Optional, Dict, Any, List, Tuple
import os
import logging
from openai import OpenAI

from executor.config import OPENAI_API_KEY
from executor.ai_config import OBJECTION_MODEL
from executor.prompt_factory import build_objection_request

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

_FALLBACK_MODELS: List[str] = [
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4.1-mini",
]

def _log_request(payload: Dict[str, Any]) -> None:
    if not HTTP_DEBUG:
        return
    LOG.info(
        "OpenAI request: model=%s temp=%s max_tokens=%s messages=%d",
        payload.get("model"),
        payload.get("temperature"),
        payload.get("max_tokens"),
        len(payload.get("messages", [])),
    )

def _extract_text(resp) -> str:
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""

def send_chat_request(question: str, allow_fallback: bool = OPENAI_FALLBACK) -> Tuple[str, str]:
    """
    Принимает ГОТОВЫЙ payload и отправляет его в OpenAI.
    Возвращает кортеж: (text, model_used).
    """
    payload = build_objection_request(
        question,
        model=OBJECTION_MODEL,
    )

    client = _client_or_init()
    _log_request(payload)

    if not allow_fallback:
        resp = client.chat.completions.create(**payload)
        text = _extract_text(resp)
        if not text:
            raise RuntimeError("Empty completion text")
        return text, (payload.get("model") or OBJECTION_MODEL)

    first_model = payload.get("model") or OBJECTION_MODEL
    models_chain: List[str] = [first_model] + [m for m in _FALLBACK_MODELS if m != first_model]

    last_err: Optional[Exception] = None
    for i, model_name in enumerate(models_chain, start=1):
        try:
            req = dict(payload)
            req["model"] = model_name
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