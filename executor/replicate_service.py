# smart_agent/executor/replicate_service.py
import os
import logging
from typing import Any, Dict, Optional, Tuple

import replicate
from replicate.exceptions import ReplicateError, ModelError

from executor.config import (
    REPLICATE_API_TOKEN,
    OPENAI_API_KEY,
    MODEL_INTERIOR_DESIGN_REF,
    MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
    MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY,
    MODEL_FLOOR_PLAN_REF,
    MODEL_FLOOR_PLAN_IMAGE_PARAM,
    MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
)
from executor.helpers import (
    extract_url,
    build_replicate_payload,
    log_replicate_error,
)

# гарантируем, что токен Replicate в окружении
if REPLICATE_API_TOKEN and not os.getenv("REPLICATE_API_TOKEN"):
    os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN


def run_design(img_bytes: bytes, prompt: str) -> str:
    """
    Редизайн / дизайн с нуля — прямой вызов модели (без фолбэка имени поля).
    Возвращает URL результата или бросает исключение.
    """
    if not MODEL_INTERIOR_DESIGN_REF:
        raise RuntimeError("MODEL_INTERIOR_DESIGN_REF is empty")

    payload = build_replicate_payload(
        img_bytes=img_bytes,
        prompt=prompt,
        image_param=MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
        needs_openai_key=MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY,
        openai_api_key=OPENAI_API_KEY,
    )

    logging.getLogger(__name__).info(
        "Replicate run (design). model=%s, keys=%s",
        MODEL_INTERIOR_DESIGN_REF, list(payload.keys())
    )
    output = replicate.run(MODEL_INTERIOR_DESIGN_REF, input=payload)

    url = extract_url(output)
    if not url:
        raise RuntimeError("Unexpected output: no URL")
    return url


def _run_with_image_param_fallback(
    model_ref: str,
    img_bytes: bytes,
    prompt: str,
    image_param: str,
    needs_openai_key: bool,
    openai_api_key: Optional[str] = None,
) -> str:
    """
    Пробуем image → input_image → input_images, если модель не видит картинку.
    Возвращает URL результата или бросает исключение.
    """
    order = [image_param] + [p for p in ["image", "input_image", "input_images"] if p != image_param]
    last_err: Optional[Exception] = None

    for param in order:
        try:
            payload = build_replicate_payload(
                img_bytes=img_bytes,
                prompt=prompt,
                image_param=param,
                needs_openai_key=needs_openai_key,
                openai_api_key=openai_api_key,
            )
            logging.getLogger(__name__).info(
                "Replicate run (plan) try param=%s, model=%s, keys=%s",
                param, model_ref, list(payload.keys())
            )
            out = replicate.run(model_ref, input=payload)
            url = extract_url(out)
            if url:
                if param != image_param:
                    logging.getLogger(__name__).warning("Image param auto-switched: %s -> %s", image_param, param)
                return url

            last_err = RuntimeError("No URL in output")

        except (ModelError, ReplicateError) as e:
            log_replicate_error(f"Replicate error with param={param}", e)
            pred = getattr(e, "prediction", None)
            metrics = getattr(pred, "metrics", {}) if pred else {}
            image_count = (metrics or {}).get("image_count")
            # если модель не «увидела» изображение — пробуем дальше
            if image_count == 0:
                last_err = e
                continue
            # другая ошибка — отдаём сразу
            raise
        except Exception as e:
            last_err = e

    if last_err:
        raise last_err
    raise RuntimeError("Unknown replicate fallback failure")


def run_floor_plan(img_bytes: bytes, prompt: str) -> str:
    """
    Генерация планировок — с фолбэком имени поля изображения.
    Возвращает URL результата или бросает исключение.
    """
    if not MODEL_FLOOR_PLAN_REF:
        raise RuntimeError("MODEL_FLOOR_PLAN_REF is empty")

    return _run_with_image_param_fallback(
        model_ref=MODEL_FLOOR_PLAN_REF,
        img_bytes=img_bytes,
        prompt=prompt,
        image_param=MODEL_FLOOR_PLAN_IMAGE_PARAM,
        needs_openai_key=MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
        openai_api_key=OPENAI_API_KEY,
    )
