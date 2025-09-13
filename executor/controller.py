# smart_agent/executor/controller.py
from __future__ import annotations

import logging
from flask import Blueprint, request, jsonify

from replicate.exceptions import ReplicateError, ModelError

from executor.config import *
from executor.helpers import *

from executor.openai_service import *
from executor import replicate_service as svc

api = Blueprint("api", __name__, url_prefix="/api/v1")

_config_issues = validate_config()
LOG = logging.getLogger(__name__)


@api.get("/../health")  # доступно и как /health
def compat_health():
    ok = not _config_issues
    return jsonify({"status": "ok" if ok else "misconfigured", "issues": _config_issues}), (200 if ok else 500)


@api.get("/../debug/env")  # доступно и как /debug/env
def compat_debug_env():
    return jsonify({
        "openai_key_loaded": bool(OPENAI_API_KEY),
        "interior_ref": MODEL_INTERIOR_DESIGN_REF,
        "interior_image_param": MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
        "interior_needs_openai_key": MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY,
        "plan_ref": MODEL_FLOOR_PLAN_REF,
        "plan_image_param": MODEL_FLOOR_PLAN_IMAGE_PARAM,
        "plan_needs_openai_key": MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
        "config_issues": _config_issues,
    }), 200


@api.get("/health")
def health():
    ok = not _config_issues
    return jsonify({"status": "ok" if ok else "misconfigured", "issues": _config_issues}), (200 if ok else 500)


@api.get("/debug/env")
def debug_env():
    return jsonify({
        "openai_key_loaded": bool(OPENAI_API_KEY),
        "interior_ref": MODEL_INTERIOR_DESIGN_REF,
        "interior_image_param": MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
        "interior_needs_openai_key": MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY,
        "plan_ref": MODEL_FLOOR_PLAN_REF,
        "plan_image_param": MODEL_FLOOR_PLAN_IMAGE_PARAM,
        "plan_needs_openai_key": MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
        "config_issues": _config_issues,
    }), 200


@api.post("/design/generate")
def design_generate():
    """Редизайн / дизайн с нуля: form-data { image:file, prompt:text }"""
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    files = request.files
    form = request.form
    if "image" not in files or "prompt" not in form:
        return jsonify({"error": "bad_request", "detail": "image and prompt are required"}), 400

    img_bytes = files["image"].read()
    prompt = form["prompt"]
    meta = image_meta(img_bytes)
    debug_flag = request.args.get("debug") == "1"

    log_payload(
        kind="design",
        model_ref=MODEL_INTERIOR_DESIGN_REF,
        image_param=MODEL_INTERIOR_DESIGN_IMAGE_PARAM,
        prompt=prompt,
        img_meta=meta,
        needs_openai_key=MODEL_INTERIOR_DESIGN_NEEDS_OPENAI_KEY,
    )

    try:
        url = svc.run_design(img_bytes, prompt)
        body = {"url": url}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": meta}
        return jsonify(body), 200

    except (ModelError, ReplicateError) as e:
        body = {"error": "replicate_error", **serialize_prediction_error(e)}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": meta}
        return jsonify(body), 502
    except Exception as e:
        LOG.exception("Unhandled error (design)")
        body = {"error": "internal_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": meta}
        return jsonify(body), 500


@api.post("/plan/generate")
def plan_generate():
    """Планировки: form-data { image:file, prompt:text } (с фолбэком имени поля изображения)."""
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    files = request.files
    form = request.form
    if "image" not in files or "prompt" not in form:
        return jsonify({"error": "bad_request", "detail": "image and prompt are required"}), 400

    img_bytes = files["image"].read()
    prompt = form["prompt"]
    meta = image_meta(img_bytes)
    debug_flag = request.args.get("debug") == "1"

    log_payload(
        kind="plan-replicate",
        model_ref=MODEL_FLOOR_PLAN_REF,
        image_param=MODEL_FLOOR_PLAN_IMAGE_PARAM,
        prompt=prompt,
        img_meta=meta,
        needs_openai_key=MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY,
    )

    try:
        url = svc.run_floor_plan(img_bytes, prompt)
        body = {"url": url}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": meta}
        return jsonify(body), 200

    except (ModelError, ReplicateError) as e:
        body = {"error": "replicate_error", **serialize_prediction_error(e)}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": meta}
        return jsonify(body), 502
    except Exception as e:
        LOG.exception("Unhandled error (plan)")
        body = {"error": "internal_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"prompt": prompt, "image_meta": meta}
        return jsonify(body), 500


@api.post("/objection/generate")
def objection_generate():
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or request.form.get("question") or "").strip()
    if not question:
        return jsonify({"error": "bad_request", "detail": "field 'question' is required"}), 400

    debug_flag = request.args.get("debug") == "1"

    try:
        text, used_model = send_objection_generate_request(question, True)
        body = {"text": text}
        if debug_flag:
            body["debug"] = {"model_used": used_model}
        return jsonify(body), 200

    except Exception as e:
        LOG.exception("OpenAI error (objection)")
        body = {"error": "openai_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"model": OBJECTION_MODEL}
        return jsonify(body), 502


@api.post("/description/generate")
def description_generate():
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    data = request.get_json(silent=True) or {}
    form = request.form

    # BACK-COMPAT: если прислали уже собранное "question" — работаем по-старому
    question = (data.get("question") or form.get("question") or "").strip()
    debug_flag = request.args.get("debug") == "1"

    try:
        if question:
            text, used_model = send_description_generate_request(question, True)
        else:
            # НОВЫЙ ПУТЬ: сырые поля
            fields = {
                "type":       data.get("type")       or form.get("type"),
                "apt_class":  data.get("apt_class")  or form.get("apt_class"),
                "in_complex": data.get("in_complex") or form.get("in_complex"),
                "area":       data.get("area")       or form.get("area"),
                "comment":    data.get("comment")    or form.get("comment"),
            }
            # минимальная валидация
            if not fields["type"]:
                return jsonify({"error": "bad_request", "detail": "field 'type' is required"}), 400

            text, used_model = send_description_generate_request_from_fields(fields, True)

        body = {"text": text}
        if debug_flag:
            body["debug"] = {"model_used": used_model}
        return jsonify(body), 200

    except Exception as e:
        LOG.exception("OpenAI error (description)")
        body = {"error": "openai_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"model": DESCRIPTION_MODEL}
        return jsonify(body), 502
