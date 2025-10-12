# smart_agent/executor/controller.py
from __future__ import annotations
from flask import Blueprint, request, jsonify

from executor.openai_service import *
import executor.apps.plan_generate as plan_module
import executor.apps.design_generate as design_module
import executor.apps.review_generator as review_module


import executor.apps.description_generate as description_module

api = Blueprint("api", __name__, url_prefix="/api/v1")
LOG = logging.getLogger(__name__)


@api.post("/review/generate")
def review_generate():
    return review_module.review_generate(request)

@api.post("/review/mutate")
def review_mutate():
    return review_module.review_mutate(request)


@api.post("/description/generate")
def description_generate():
    return description_module.description_generate(request)


@api.post("/design/generate")
def design_generate():
    return design_module.design_generate(request)


@api.post("/plan/generate")
def plan_generate():
    return plan_module.plan_generate(request)



@api.post("/objection/generate")
def objection_generate():
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


@api.post("/summary/analyze")
def summary_analyze():
    """
    Принимает payload:
      { "user_id": ..., "source": {...}, "created_at": "...",
        "input": { "type": "text", "text": "..."} | { "type": "audio", "local_path": "..." } }
    Возвращает:
      { "summary": "...", "strengths": [...], "mistakes": [...], "decisions": [...] }
    """

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