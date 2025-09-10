#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\app.py

import os
import io
import hashlib
import logging
from flask import Flask, request, jsonify
import replicate
from replicate.exceptions import ReplicateError, ModelError
from PIL import Image

from executor.config import (
    REPLICATE_API_TOKEN,
    OPENAI_API_KEY,  # может понадобиться как pass-through для некоторых моделей Replicate
    EXECUTOR_HOST,
    EXECUTOR_PORT,
    MODEL_INTERIOR_DESIGN_REF,
    MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
    MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY,
    MODEL_FLOOR_PLAN_REF,
    MODEL_FLOOR_PLAN_IMAGE_PARAM,
    MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
    validate_config,
)

app = Flask(__name__)

# ----------------- ЛОГИ ----------------- #
root_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, root_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
HTTP_DEBUG = os.getenv("HTTP_DEBUG", "0") == "1"
logging.getLogger("httpx").setLevel(logging.INFO if HTTP_DEBUG else logging.WARNING)
logging.getLogger("werkzeug").setLevel(logging.INFO)

LOG_PAYLOAD = os.getenv("LOG_PAYLOAD", "1") == "1"

# ----------------- КОНФИГ ----------------- #
_config_issues = validate_config()
if _config_issues:
    app.logger.error("Config problems: %s", "; ".join(_config_issues))
else:
    os.environ["REPLICATE_API_TOKEN"] = REPLICATE_API_TOKEN

# ----------------- HELPERS ----------------- #
def _extract_url(output):
    """Нормализуем ответ Replicate в URL."""
    try:
        url = getattr(output, "url", None)
        if isinstance(url, str) and url.startswith("http"):
            return url
        if isinstance(output, list):
            if not output:
                return None
            first = output[0]
            if hasattr(first, "url") and isinstance(first.url, str) and first.url.startswith("http"):
                return first.url
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
        app.logger.warning("URL extract error: %s", e)
    return None


def _image_meta(img_bytes: bytes) -> dict:
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
        app.logger.warning("Pillow failed to read image meta: %s", e)
    return meta


def _log_payload(kind: str, model_ref: str, image_param: str, prompt: str, img_meta: dict, needs_openai_key: bool):
    """Подробный лог отправляемых данных (промпт полностью)."""
    if not LOG_PAYLOAD:
        return
    app.logger.info(
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


def _build_replicate_payload(img_bytes: bytes, prompt: str, image_param: str, needs_openai_key: bool) -> dict:
    """Payload для Replicate строго по конфигу."""
    buf = io.BytesIO(img_bytes)
    buf.name = "upload.png"
    if image_param == "input_images":
        payload = {"prompt": prompt, "input_images": [buf]}
    else:
        payload = {"prompt": prompt, image_param: buf}
    if needs_openai_key:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY required by model, but missing")
        payload["openai_api_key"] = OPENAI_API_KEY
    return payload


def _serialize_prediction_error(e: Exception) -> dict:
    """Достаём максимум из Replicate/ModelError."""
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

# ----------------- SERVICE ----------------- #
@app.get("/health")
def health():
    ok = not _config_issues
    return jsonify({"status": "ok" if ok else "misconfigured", "issues": _config_issues}), (200 if ok else 500)

@app.get("/debug/env")
def debug_env():
    return jsonify({
        "replicate_token_loaded": bool(REPLICATE_API_TOKEN),
        "openai_key_loaded": bool(OPENAI_API_KEY),  # просто факт наличия (некоторым моделям Replicate нужен pass-through)
        "interior_ref": MODEL_INTERIOR_DESIGN_REF,
        "interior_image_param": MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
        "interior_needs_openai_key": MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY,
        "plan_ref": MODEL_FLOOR_PLAN_REF,
        "plan_image_param": MODEL_FLOOR_PLAN_IMAGE_PARAM,
        "plan_needs_openai_key": MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
        "config_issues": _config_issues,
        "http_debug": HTTP_DEBUG,
        "log_payload": LOG_PAYLOAD,
    }), 200

# ----------------- ENDPOINTS ----------------- #
@app.post("/api/v1/design/generate")
def design_generate():
    """
    Редизайн/дизайн с нуля — ТОЛЬКО через Replicate (sync).
    form-data:
      - image: file
      - prompt: text
    """
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    files = request.files
    form = request.form
    if "image" not in files or "prompt" not in form:
        return jsonify({"error": "bad_request", "detail": "image and prompt are required"}), 400
    if not MODEL_INTERIOR_DESIGN_REF:
        return jsonify({"error": "config", "detail": "MODEL_INTERIOR_DESIGN_REF is empty"}), 500

    img_bytes = files["image"].read()
    prompt = form["prompt"]
    img_meta = _image_meta(img_bytes)

    _log_payload(
        "design", MODEL_INTERIOR_DESIGN_REF, MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
        prompt, img_meta, MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY
    )
    debug_flag = request.args.get("debug") == "1"

    try:
        payload = _build_replicate_payload(
            img_bytes, prompt,
            MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
            MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY
        )
        app.logger.info("Replicate run (design). model=%s, keys=%s",
                        MODEL_INTERIOR_DESIGN_REF, list(payload.keys()))
        output = replicate.run(MODEL_INTERIOR_DESIGN_REF, input=payload)

        url = _extract_url(output)
        if not url:
            app.logger.error("Unexpected output (design): %r", output)
            body = {"error": "unexpected_output", "detail": "no url in output"}
            if debug_flag:
                body["debug"] = {"prompt": prompt, "image_meta": img_meta}
            return jsonify(body), 502

        resp = {"url": url}
        if debug_flag:
            resp["debug"] = {"prompt": prompt, "image_meta": img_meta}
        return jsonify(resp), 200

    except (ModelError, ReplicateError) as e:
        body = {"error": "replicate_error", **_serialize_prediction_error(e)}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": img_meta}
        return jsonify(body), 502
    except Exception as e:
        app.logger.exception("Unhandled error (design)")
        body = {"error": "internal_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": img_meta}
        return jsonify(body), 500


# --- helpers for fallback (put near other helpers) ---
def _log_replicate_error(prefix: str, e: Exception):
    try:
        pred = getattr(e, "prediction", None)
        app.logger.error(
            "%s: %s | id=%s status=%s error=%s metrics=%s logs=%s",
            prefix, e, getattr(pred, "id", None), getattr(pred, "status", None),
            getattr(pred, "error", None), getattr(pred, "metrics", None),
            getattr(pred, "logs", None),
        )
    except Exception:
        app.logger.error("%s: %s", prefix, e)


def _run_with_image_param_fallback(model_ref, img_bytes, prompt, image_param, needs_openai_key):
    """
    Пробуем image → input_image → input_images, если модель не видит картинку.
    Возвращает (url, raw_output, err)
    """
    tried = []
    order = [image_param] + [p for p in ["image", "input_image", "input_images"] if p != image_param]
    last_err = None

    for param in order:
        tried.append(param)
        try:
            payload = _build_replicate_payload(img_bytes, prompt, param, needs_openai_key)
            app.logger.info("Replicate run (plan) try param=%s, model=%s, keys=%s",
                            param, model_ref, list(payload.keys()))
            out = replicate.run(model_ref, input=payload)
            url = _extract_url(out)
            if url:
                if param != image_param:
                    app.logger.warning("Image param auto-switched: %s -> %s", image_param, param)
                return url, out, None
            last_err = RuntimeError("No URL in output")
        except (ModelError, ReplicateError) as e:
            _log_replicate_error(f"Replicate error with param={param}", e)
            pred = getattr(e, "prediction", None)
            metrics = getattr(pred, "metrics", {}) if pred else {}
            image_count = (metrics or {}).get("image_count")
            # Если модель не «увидела» изображение — пробуем следующую вариацию
            if image_count == 0:
                last_err = e
                continue
            # Иная ошибка — прекращаем попытки и возвращаем её сразу
            return None, None, e
        except Exception as e:
            last_err = e

    app.logger.error("All image params tried and failed: %s", tried)
    return None, None, last_err


# ----------------- ENDPOINT ----------------- #
@app.post("/api/v1/plan/generate")
def plan_generate():
    """
    Планировки — ТОЛЬКО через Replicate (sync).
    form-data:
      - image: file
      - prompt: text
    ?debug=1  → вернуть в ответе промпт и мета изображения
    """
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    files = request.files
    form = request.form
    if "image" not in files or "prompt" not in form:
        return jsonify({"error": "bad_request", "detail": "image and prompt are required"}), 400
    if not MODEL_FLOOR_PLAN_REF:
        return jsonify({"error": "config", "detail": "MODEL_FLOOR_PLAN_REF is empty"}), 500

    img_bytes = files["image"].read()
    prompt = form["prompt"]
    img_meta = _image_meta(img_bytes)
    debug_flag = request.args.get("debug") == "1"

    _log_payload(
        "plan-replicate",
        MODEL_FLOOR_PLAN_REF,
        MODEL_FLOOR_PLAN_IMAGE_PARAM,
        prompt,
        img_meta,
        MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
    )

    # Запускаем с авто-фолбэком имени поля изображения
    url, output, err = _run_with_image_param_fallback(
        MODEL_FLOOR_PLAN_REF,
        img_bytes,
        prompt,
        MODEL_FLOOR_PLAN_IMAGE_PARAM,
        MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
    )

    if err:
        if isinstance(err, (ModelError, ReplicateError)):
            body = {"error": "replicate_error", **_serialize_prediction_error(err)}
            if debug_flag:
                body["debug"] = {"prompt": prompt, "image_meta": img_meta}
            return jsonify(body), 502
        app.logger.exception("Unhandled error (plan)")
        body = {"error": "internal_error", "detail": str(err)}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": img_meta}
        return jsonify(body), 500

    if not url:
        app.logger.error("Unexpected output (plan): %r", output)
        body = {"error": "unexpected_output", "detail": "no url in output"}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": img_meta}
        return jsonify(body), 502

    resp = {"url": url}
    if debug_flag:
        resp["debug"] = {"prompt": prompt, "image_meta": img_meta}
    return jsonify(resp), 200



if __name__ == "__main__":
    app.run(host=EXECUTOR_HOST, port=EXECUTOR_PORT)
