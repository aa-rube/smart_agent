# smart_agent/executor/helpers.py
import os
import io
import hashlib
import logging
from typing import Any, Dict, Optional, Tuple, Union, List
from PIL import Image

LOG_PAYLOAD = os.getenv("LOG_PAYLOAD", "1") == "1"

def image_meta(img_bytes: bytes) -> Dict[str, Any]:
    """Метаданные для логов: размер, sha256, формат/размеры через Pillow."""
    meta = {
        "size_bytes": len(img_bytes),
        "sha256": hashlib.sha256(img_bytes).hexdigest(),
        "format": None,
        "width": None,
        "height": None,
        "mode": None,
    }
    try:
        with Image.open(io.BytesIO(img_bytes)) as im:
            meta["width"], meta["height"] = im.size
            meta["mode"] = im.mode
            if im.format:
                meta["format"] = im.format.lower()
    except Exception as e:
        logging.getLogger(__name__).warning("Pillow read meta failed: %s", e)
    return meta


def extract_url(output: Any) -> Optional[str]:
    """Попытаться вытащить URL из разных форм ответов Replicate."""
    try:
        url = getattr(output, "url", None)
        if isinstance(url, str) and url.startswith("http"):
            return url

        if isinstance(output, list):
            if not output:
                return None
            first = output[0]
            url = getattr(first, "url", None)
            if isinstance(url, str) and url.startswith("http"):
                return url
            for item in output:
                if isinstance(item, str) and item.startswith("http"):
                    return item

        if isinstance(output, dict):
            if isinstance(output.get("url"), str) and output["url"].startswith("http"):
                return output["url"]
            res = output.get("output")
            if isinstance(res, str) and res.startswith("http"):
                return res
            if isinstance(res, list):
                for item in res:
                    if isinstance(item, str) and item.startswith("http"):
                        return item
    except Exception as e:
        logging.getLogger(__name__).warning("extract_url error: %s", e)
    return None


def log_payload(kind: str, model_ref: str, image_param: str,
                prompt: str, img_meta: Dict[str, Any], needs_openai_key: bool) -> None:
    """Подробный лог отправляемых данных (промпт полностью)."""
    if not LOG_PAYLOAD:
        return
    logging.getLogger(__name__).info(
        "\n===== OUTGOING PAYLOAD [%s] =====\n"
        "model: %s\n"
        "image_param: %s\n"
        "needs_openai_key: %s\n"
        "prompt_len: %d\n"
        "prompt:\n%s\n"
        "image_meta: %s\n"
        "=================================\n",
        kind, model_ref, image_param, needs_openai_key, len(prompt), prompt, img_meta
    )


def serialize_prediction_error(e: Exception) -> Dict[str, Any]:
    """Достаём максимум из Replicate/ModelError для ответа."""
    try:
        pred = getattr(e, "prediction", None)
        return {
            "detail": str(e),
            "prediction_id": getattr(pred, "id", None),
            "prediction_status": getattr(pred, "status", None),
            "prediction_error": getattr(pred, "error", None),
            "prediction_logs": getattr(pred, "logs", None),
            "metrics": getattr(pred, "metrics", None),
        }
    except Exception:
        return {"detail": str(e)}


def log_replicate_error(prefix: str, e: Exception) -> None:
    """Подробный лог ошибки от Replicate, если prediction присутствует."""
    try:
        pred = getattr(e, "prediction", None)
        logging.getLogger(__name__).error(
            "%s: %s | id=%s status=%s error=%s metrics=%s logs=%s",
            prefix, e,
            getattr(pred, "id", None),
            getattr(pred, "status", None),
            getattr(pred, "error", None),
            getattr(pred, "metrics", None),
            getattr(pred, "logs", None),
        )
    except Exception:
        logging.getLogger(__name__).error("%s: %s", prefix, e)


def build_replicate_payload(
    img_bytes: bytes,
    prompt: str,
    image_param: str,
    needs_openai_key: bool,
    openai_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Собрать payload для Replicate с корректным именем поля для изображения."""
    buf = io.BytesIO(img_bytes)
    buf.name = "upload.png"
    if image_param == "input_images":
        payload = {"prompt": prompt, "input_images": [buf]}
    else:
        payload = {"prompt": prompt, image_param: buf}
    if needs_openai_key:
        if not openai_api_key:
            raise RuntimeError("OPENAI_API_KEY required by model, but missing")
        payload["openai_api_key"] = openai_api_key
    return payload
