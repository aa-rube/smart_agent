#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\apps\design_generate.py
from __future__ import annotations

"""
Self-contained module for the /design/generate endpoint.

— Не тянет внешние конфиги
— Все константы, билдеры, обработчики и утилиты — внутри
— Контроллер должен лишь делегировать сюда: design_generate(request)
"""

import io
import os
import hashlib
import logging
from typing import Any, Dict, Optional, List

from flask import jsonify, Request
import replicate
from replicate.exceptions import ReplicateError, ModelError

__all__ = ["design_generate", "build_design_prompt"]

LOG = logging.getLogger(__name__)

# =========================
# Constants: Models/Prompts
# =========================

# Базовая модель для интерьера (env-переопределяемая, с безопасным дефолтом)
MODEL_REF = os.getenv(
    "MODEL_INTERIOR_DESIGN_REF",
    "adirik/interior-design:76604baddc85b1b4616e1c6475eca080da339c8875bd4996705440484a6eac38",
)

# Имя поля с изображением для replicate.run; будет авто-фолбэк, если не подойдёт
MODEL_IMAGE_PARAM = os.getenv("MODEL_INTERIOR_DESIGN_IMAGE_PARAM", "image")

# Нужен ли прокид OPENAI_API_KEY внутрь Replicate модели
MODEL_NEEDS_OPENAI_KEY = os.getenv("MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY", "0") == "1"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Убедимся, что токен Replicate доступен SDK
if os.getenv("REPLICATE_API_TOKEN") and not os.environ.get("REPLICATE_API_TOKEN"):
    os.environ["REPLICATE_API_TOKEN"] = os.getenv("REPLICATE_API_TOKEN") or ""

# Prompt templates & dictionaries (самодостаточные)
PROMPT_INTERIOR_BASE = "photorealistic interior, hyperrealistic, 8k, highly detailed, professional photography"
PROMPT_REDESIGN = "{base_prompt} of a {room_type}, redesign in a {style_text}"
PROMPT_ZERO_DESIGN = "{base_prompt} of an empty {room_type}, redesigned as a {furniture_text} space in a {style_text}"

ROOM_TYPE_PROMPTS: Dict[str, str] = {
    "🍳 Кухня": "kitchen",
    "🛏 Спальня": "bedroom",
    "🛋 Гостиная": "living room",
    "🚿 Ванная": "bathroom",
    "🚪 Прихожая": "hallway",
}

FURNITURE_PROMPTS: Dict[str, str] = {
    "furniture_yes": "fully furnished with appropriate furniture",
    "furniture_no": "as an empty room, unfurnished",
}

STYLES_DETAIL: Dict[str, str] = {
    "Современный": "contemporary style, clean lines, neutral colors, functional design, use of glass and metal",
    "Скандинавский": "scandinavian style, hygge, light and airy, simple, functional furniture, natural materials",
    "Классика": "classic style, elegant, ornate details, rich materials, symmetrical balance",
    "Минимализм": "minimalist style, simplicity, clean lines, monochromatic palette, uncluttered space",
    "Хай-тек": "high-tech style, futuristic, metallic and plastic materials, advanced technology integration, sleek surfaces",
    "Лофт": "industrial loft style, exposed brick walls, high ceilings, open layout, metal and wood elements",
    "Эко-стиль": "eco-style, natural materials, sustainability, living plants, earthy tones, lots of light",
    "Средиземноморский": "mediterranean style, rustic, warm, earthy colors, terracotta, arches, natural wood",
    "Барокко": "baroque style, dramatic, opulent, grand scale, intricate details, gold accents",
    "Неоклассика": "neoclassical style, refined elegance, greek and roman motifs, clean lines, muted colors",
    "🔥 Случайный выбор ИИ": "random_style",
}

# ======================
# Helpers / Util methods
# ======================

def _image_meta(img_bytes: bytes) -> Dict[str, Any]:
    """Минимальная мета для отладки (без Pillow и сторонних зависимостей)."""
    return {
        "size_bytes": len(img_bytes),
        "sha256": hashlib.sha256(img_bytes).hexdigest(),
    }


def _extract_url(output: Any) -> Optional[str]:
    """Достаём URL из разных форматов ответа replicate.run."""
    try:
        # список строк/объектов
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str) and item.startswith("http"):
                    return item
            for item in output:
                url = getattr(item, "url", None)
                if isinstance(url, str) and url.startswith("http"):
                    return url

        # объект с .url
        url = getattr(output, "url", None)
        if isinstance(url, str) and url.startswith("http"):
            return url

        # словарь
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
        return None
    except Exception:
        return None


def _build_input_dict(
    *,
    prompt: str,
    image_param: str,
    img_bytes: bytes,
    needs_openai_key: bool,
    openai_api_key: Optional[str],
) -> Dict[str, Any]:
    """Сборка input-пэйлоада для replicate.run (используем file-like BytesIO)."""
    buffer = io.BytesIO(img_bytes)
    buffer.name = "upload.png"  # replicate любит имя файла
    input_dict: Dict[str, Any] = {"prompt": prompt}
    if needs_openai_key and openai_api_key:
        input_dict["openai_api_key"] = openai_api_key
    if image_param == "input_images":
        input_dict["input_images"] = [buffer]
    else:
        input_dict[image_param] = buffer
    return input_dict


def _run_with_fallbacks(img_bytes: bytes, prompt: str) -> str:
    """
    Запуск replicate с прогрессивными фолбэками имени поля изображения:
    MODEL_IMAGE_PARAM -> 'image' -> 'input_image' -> 'input_images'
    Возвращает URL или бросает исключение.
    """
    order: List[str] = [MODEL_IMAGE_PARAM] + [p for p in ["image", "input_image", "input_images"] if p != MODEL_IMAGE_PARAM]
    last_err: Optional[Exception] = None

    for param in order:
        try:
            payload = _build_input_dict(
                prompt=prompt,
                image_param=param,
                img_bytes=img_bytes,
                needs_openai_key=MODEL_NEEDS_OPENAI_KEY,
                openai_api_key=OPENAI_API_KEY,
            )
            LOG.info("Replicate.run model=%s try image_param=%s keys=%s", MODEL_REF, param, list(payload.keys()))
            out = replicate.run(MODEL_REF, input=payload)
            url = _extract_url(out)
            if url:
                if param != MODEL_IMAGE_PARAM:
                    LOG.warning("Image param auto-switched: %s -> %s", MODEL_IMAGE_PARAM, param)
                return url
            last_err = RuntimeError("No URL in output")
        except (ModelError, ReplicateError) as e:
            last_err = e
            # Если в метриках prediction явно 0 изображений — пробуем следующее имя поля
            try:
                pred = getattr(e, "prediction", None)
                metrics = getattr(pred, "metrics", {}) if pred else {}
                if (metrics or {}).get("image_count") == 0:
                    LOG.warning("Replicate image param suspected mismatch for %s: %s", param, e)
                    continue
            except Exception:
                pass
            raise
        except Exception as e:
            last_err = e

    if last_err:
        raise last_err
    raise RuntimeError("Unknown replicate fallback failure")

# =================
# Prompt Builders
# =================

def build_design_prompt(
    *,
    style: str,
    room_type: Optional[str] = None,
    furniture: Optional[str] = None,
) -> str:
    """
    Single, explicit builder. No legacy support, no extra modes.
    If room_type provided and furniture is None => redesign-by-photo
    If room_type and furniture provided => zero-design
    Else => generic base prompt + style
    """
    base_prompt = PROMPT_INTERIOR_BASE

    # style
    if style == "🔥 Случайный выбор ИИ":
        # choose from known styles except the random marker
        variants = [k for k, v in STYLES_DETAIL.items() if v != "random_style"]
        # deterministic-ish choice without importing random (so tests are stable)
        # hash user-visible style marker into an index
        idx = (sum(map(ord, "".join(variants))) % len(variants)) if variants else 0
        style_text = STYLES_DETAIL.get(variants[idx], "modern style") if variants else "modern style"
    else:
        style_text = STYLES_DETAIL.get(style, "modern style")

    if room_type and furniture is None:
        room_text = ROOM_TYPE_PROMPTS.get(room_type, "room")
        final_prompt = PROMPT_REDESIGN.format(base_prompt=base_prompt, room_type=room_text, style_text=style_text)
    elif room_type and furniture is not None:
        room_text = ROOM_TYPE_PROMPTS.get(room_type, "room")
        furniture_text = FURNITURE_PROMPTS.get(furniture, "")
        final_prompt = PROMPT_ZERO_DESIGN.format(
            base_prompt=base_prompt, room_type=room_text, furniture_text=furniture_text, style_text=style_text
        )
    else:
        final_prompt = f"{base_prompt}, {style_text}"

    # tidy redundant commas/spaces
    parts = [p.strip() for p in final_prompt.split(",") if p and p.strip()]
    return ", ".join(parts)

# ==============
# HTTP Handler
# ==============

def design_generate(req: Request):
    """
    Flask-совместимый обработчик.
    Ожидает multipart/form-data:
      - image: file (обязательно)
      - prompt: str (если нет — можно передать style/room_type/furniture, и мы соберём промпт сами)

    Ответ: JSON (url[, debug]) с корректными HTTP кодами.
    """
    try:
        files = getattr(req, "files", None)
        form = getattr(req, "form", None)
        if files is None or form is None:
            return jsonify({"error": "bad_request", "detail": "multipart form-data expected"}), 400

        debug_flag = (req.args.get("debug") == "1") if hasattr(req, "args") else False

        if "image" not in files:
            return jsonify({"error": "bad_request", "detail": "field 'image' is required"}), 400

        img_bytes = files["image"].read()
        if not img_bytes or len(img_bytes) < 64:
            return jsonify({"error": "bad_request", "detail": "image is empty or too small"}), 400

        # Либо берём готовый prompt, либо формируем из полей
        prompt = (form.get("prompt") or "").strip()
        if not prompt:
            style = (form.get("style") or "").strip()
            room_type = (form.get("room_type") or "").strip() or None
            furniture = (form.get("furniture") or "").strip() or None
            if not style:
                return jsonify({
                    "error": "bad_request",
                    "detail": "either 'prompt' or ('style' [+ room_type / furniture]) is required",
                }), 400
            prompt = build_design_prompt(style=style, room_type=room_type, furniture=furniture)

        meta = _image_meta(img_bytes)

        # Генерация изображения через Replicate (с умным фолбэком имени поля с изображением)
        url = _run_with_fallbacks(img_bytes, prompt)

        body: Dict[str, Any] = {"url": url}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": meta, "model_ref": MODEL_REF}
        return jsonify(body), 200

    except (ModelError, ReplicateError) as e:
        payload: Dict[str, Any] = {"error": "replicate_error", "detail": str(e)}
        try:
            pred = getattr(e, "prediction", None)
            payload.update({
                "prediction_id": getattr(pred, "id", None),
                "prediction_status": getattr(pred, "status", None),
                "prediction_error": getattr(pred, "error", None),
                "prediction_logs": getattr(pred, "logs", None),
                "metrics": getattr(pred, "metrics", None),
            })
        except Exception:
            pass
        return jsonify(payload), 502

    except Exception as e:
        LOG.exception("Unhandled error in design_generate")
        return jsonify({"error": "internal_error", "detail": str(e)}), 500
