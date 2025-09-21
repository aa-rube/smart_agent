# smart_agent/executor/controller.py
from __future__ import annotations
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
    """
    ТОНКИЙ контроллер: принимает сырые поля анкеты (JSON или form),
    передаёт в сервис, возвращает готовый текст.
    Вся сборка промпта/валидация формата перенесена в фабрику/сервис.
    """
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    # Принимаем JSON в приоритете, фолбэк на form-data
    data = request.get_json(silent=True) or {}
    form = request.form
    debug_flag = request.args.get("debug") == "1"

    # Единый набор поддерживаемых полей (новая анкета + старые поля для back-compat)
    fields = {
        # базовые
        "type":        data.get("type")        or form.get("type"),
        "apt_class":   data.get("apt_class")   or form.get("apt_class"),
        "in_complex":  data.get("in_complex")  or form.get("in_complex"),
        "area":        data.get("area")        or form.get("area"),
        "comment":     data.get("comment")     or form.get("comment"),
        # новая анкета (обязательные на стороне бота)
        "total_area":      data.get("total_area")      or form.get("total_area"),
        "building_floors": data.get("building_floors") or form.get("building_floors"),
        "floor_number":    data.get("floor_number")    or form.get("floor_number"),
        "kitchen_area":    data.get("kitchen_area")    or form.get("kitchen_area"),
        "rooms":           data.get("rooms")           or form.get("rooms"),
        "year_state":      data.get("year_state")      or form.get("year_state"),
        "utilities":       data.get("utilities")       or form.get("utilities"),
        "location":        data.get("location")        or form.get("location"),
        "amenities":       data.get("amenities")       or form.get("amenities"),
    }

    # Минимальная проверка наличия типа (вся остальная логика — в фабрике/боте)
    if not (fields.get("type") or "").strip():
        return jsonify({"error": "bad_request", "detail": "field 'type' is required"}), 400

    try:
        text, used_model = send_description_generate_request_from_fields(fields, allow_fallback=True)
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


# ==============================
#   NEW: FEEDBACK / REVIEW API
# ==============================
@api.post("/review/generate")
def review_generate():
    """
    Генерация черновиков/отзывов.
    Ожидает JSON:
      {
        client_name, agent_name, company, city, address,
        deal_type,   # CSV кодов: "sale,buy,custom" (опционально)
        deal_custom, # если был выбор "Другое"
        situation,   # >= 50 символов
        style,       # строка (исторически — общий стиль)
        tone,        # новый "тон оф войс" (optional)
        length,      # новый: short|medium|long (optional)
        num_variants # 1..5 (по умолчанию 3)
      }
    """
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    data = request.get_json(silent=True) or {}
    debug_flag = request.args.get("debug") == "1"

    # Валидация
    situation = (data.get("situation") or "").strip()
    if len(situation) < 50:
        return jsonify({"error": "bad_request", "detail": "field 'situation' must be >= 50 chars"}), 400
    try:
        num_variants = int(data.get("num_variants") or 3)
    except Exception:
        return jsonify({"error": "bad_request", "detail": "field 'num_variants' must be integer"}), 400
    if not (1 <= num_variants <= 5):
        return jsonify({"error": "bad_request", "detail": "num_variants must be in range 1..5"}), 400

    # Собираем "payload" как есть — сервис сам разрулит старый/новый формат
    payload = {
        "client_name": data.get("client_name"),
        "agent_name":  data.get("agent_name"),
        "company":     data.get("company"),
        "city":        data.get("city"),
        "address":     data.get("address"),
        "deal_type":   data.get("deal_type"),
        "deal_custom": data.get("deal_custom"),
        "situation":   situation,
        "style":       data.get("style"),
        "tone":        data.get("tone"),
        "length":      data.get("length"),
    }

    try:
        variants, used_model, debug_msg = send_feedback_generate_request(payload, num_variants=num_variants, allow_fallback=True)
        body = {"variants": variants}
        if debug_flag:
            body["debug"] = {"model_used": used_model, "prompt": debug_msg}
        return jsonify(body), 200
    except Exception as e:
        LOG.exception("OpenAI error (review_generate)")
        body = {"error": "openai_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"model": FEEDBACK_MODEL}
        return jsonify(body), 502


@api.post("/review/mutate")
def review_mutate():
    """
    Мутации текста (укоротить/удлинить/поменять тон).
    Ожидает JSON:
      {
        base_text: str,
        operation: 'short' | 'long' | 'style',
        style:     str | None,
        tone:      str | None,   # если используем новый раздельный тон
        length:    str | None,   # short|medium|long — целевая длина (для 'short'/'long' опционально)
        context:   { те же поля, что и в generate }  # опционально, для контекста
      }
    """
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    data = request.get_json(silent=True) or {}
    base_text = (data.get("base_text") or "").strip()
    operation = (data.get("operation") or "").strip()
    style     = data.get("style")
    tone      = data.get("tone")
    length    = data.get("length")
    context   = data.get("context") or {}
    debug_flag = request.args.get("debug") == "1"

    if not base_text:
        return jsonify({"error": "bad_request", "detail": "field 'base_text' is required"}), 400
    if operation not in ("short", "long", "style"):
        return jsonify({"error": "bad_request", "detail": "field 'operation' must be one of short|long|style"}), 400

    try:
        text, used_model, debug_msg = send_feedback_mutate_request(
            base_text=base_text,
            operation=operation,
            style=style,
            tone=tone,
            length=length,
            context=context,
            allow_fallback=True,
        )
        body = {"text": text}
        if debug_flag:
            body["debug"] = {"model_used": used_model, "prompt": debug_msg}
        return jsonify(body), 200
    except Exception as e:
        LOG.exception("OpenAI error (review_mutate)")
        body = {"error": "openai_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"model": FEEDBACK_MODEL}
        return jsonify(body), 502


@api.post("/summary/analyze")
def summary_analyze():
    """
    Принимает payload:
      { "user_id": ..., "source": {...}, "created_at": "...",
        "input": { "type": "text", "text": "..."} | { "type": "audio", "local_path": "..." } }
    Возвращает:
      { "summary": "...", "strengths": [...], "mistakes": [...], "decisions": [...] }
    """
    if _config_issues:
        return jsonify({"error": "config", "detail": "; ".join(_config_issues)}), 500

    if not request.is_json:
        return jsonify({"error": "bad_request", "detail": "JSON body required"}), 400
    data = request.get_json(silent=True) or {}
    debug_flag = request.args.get("debug") == "1"

    input_obj = data.get("input") or {}
    in_type = (input_obj.get("type") or "").strip().lower()
    if in_type not in ("text", "audio"):
        return jsonify({"error": "bad_request", "detail": "input.type must be 'text' or 'audio'"}), 400

    try:
        result_dict, used_model, debug_meta = summarize_from_input(input_obj, allow_fallback=True)

        body = {
            "summary":   result_dict.get("summary", "") or "",
            "strengths": result_dict.get("strengths", []) or [],
            "mistakes":  result_dict.get("mistakes", []) or [],
            "decisions": result_dict.get("decisions", []) or [],
        }
        if debug_flag:
            body["debug"] = {"model_used": used_model, **debug_meta}
        return jsonify(body), 200

    except FileNotFoundError as e:
        return jsonify({"error": "bad_request", "detail": str(e)}), 400
    except ValueError as e:
        return jsonify({"error": "bad_request", "detail": str(e)}), 400
    except Exception as e:
        LOG.exception("Unhandled error (summary_analyze)")
        body = {"error": "internal_error", "detail": str(e)}
        if debug_flag:
            try:
                from executor.ai_config import SUMMARY_MODEL as _SUMMARY_MODEL
                body["debug"] = {"model": _SUMMARY_MODEL}
            except Exception:
                body["debug"] = {"model": "summary"}
        return jsonify(body), 500