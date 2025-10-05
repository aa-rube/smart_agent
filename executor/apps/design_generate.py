#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\apps\design_generate.py
from __future__ import annotations

"""
Self-contained module for the /design/generate endpoint.

— Не тянет внешние конфиги (кроме ENV/ executor.config для фолбэка ключа)
— Все константы, билдеры, обработчики и утилиты — внутри
— Контроллер должен лишь делегировать сюда: design_generate(request)
— Двухпроходная схема: 1) черновик; 2) уточнение (истина + черновик + промпт)
"""

import json
import base64
import hashlib
import logging
import urllib.error
from typing import Any, Dict, Optional, List, Tuple

from flask import jsonify, Request
from executor.config import *  # BANANO_API_KEY_FALLBACK и т.п.

__all__ = ["design_generate", "build_design_prompt", "build_refine_prompt"]

LOG = logging.getLogger(__name__)

# =========================
# Constants: Gemini/Banano
# =========================

# Публичный REST endpoint Google (можно прокинуть свой прокси)
BANANO_ENDPOINT = os.getenv("BANANO_ENDPOINT", "https://generativelanguage.googleapis.com/v1beta")

# Модель генерации изображений
BANANO_MODEL = os.getenv("BANANO_MODEL_INTERIOR", "gemini-2.5-flash-image")

# По умолчанию — только изображения в ответе
BANANO_IMAGES_ONLY = os.getenv("BANANO_IMAGES_ONLY", "1") == "1"

# Необязательное соотношение сторон
BANANO_ASPECT_RATIO = os.getenv("BANANO_ASPECT_RATIO", "")

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

# Дополнительный шаблон для 2-го прохода (укрепляет соблюдение геометрии)
REFINE_INTERIOR_INSTRUCTIONS_EN = """
REFINE PASS (Image-to-Image with two inputs):
• Image #1 is the ground-truth shell. Treat all structural / engineering elements as immutable:
  - load-bearing walls and columns;
  - gas risers / pipes and water/plumbing lines;
  - wet areas positions (kitchen, bathroom, toilet).
• Image #2 is a draft for color, lighting, materials and movable furniture only.
Hard constraints:
- Do NOT move/resize/remove walls or partitions; preserve room sizes and proportions exactly.
- Keep door/window openings at the same positions and dimensions.
- Remove any text, numbers, labels and axis marks completely.
- Apply finishes, décor and loose furniture only; no structural changes.
Output: final photo-realistic interior image with geometry identical to Image #1.
""".strip()

# ======================
# Helpers / Util methods
# ======================

def _image_meta(img_bytes: bytes) -> Dict[str, Any]:
    """Минимальная мета для отладки (без Pillow и сторонних зависимостей)."""
    return {
        "size_bytes": len(img_bytes),
        "sha256": hashlib.sha256(img_bytes).hexdigest(),
    }


def _read_api_key(req: Request) -> str:
    """
    Источник API-ключа (приоритет):
     1) из запроса: form['api_key'] либо Authorization: Bearer/ X-API-Key / X-Banano-Key
     2) из ENV (BANANO_API_KEY_FALLBACK / GOOGLE_API_KEY / GEMINI_API_KEY)
    """
    try:
        if hasattr(req, "form") and req.form and "api_key" in req.form:
            v = (req.form.get("api_key") or "").strip()
            if v:
                return v
    except Exception:
        pass

    auth = req.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()

    for h in ("X-API-Key", "X-Banano-Key", "X-Api-Key", "X-BANANO-KEY"):
        v = req.headers.get(h, "")
        if v:
            return v.strip()

    return BANANO_API_KEY_FALLBACK


def _detect_mime(b: bytes) -> str:
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _to_data_url(img_bytes: bytes, mime: str = "image/png") -> str:
    return f"data:{mime};base64,{base64.b64encode(img_bytes).decode('ascii')}"


def _build_google_payload(*, prompt: str, images: List[bytes],
                         aspect_ratio: Optional[str], images_only: bool) -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for b in images:
        parts.append({"inlineData": {"mimeType": _detect_mime(b),
                                     "data": base64.b64encode(b).decode("ascii")}})

    payload: Dict[str, Any] = {"contents": [{"role": "user", "parts": parts}]}
    gen_cfg: Dict[str, Any] = {}
    if images_only:
        gen_cfg["responseModalities"] = ["IMAGE"]
    if aspect_ratio:
        gen_cfg["imageConfig"] = {"aspectRatio": aspect_ratio}
    if gen_cfg:
        payload["generationConfig"] = gen_cfg
    return payload


def _http_post_json(url: str, params: Dict[str, str], body: Dict[str, Any], timeout: int = 90) -> Dict[str, Any]:
    import urllib.request, urllib.parse
    full_url = f"{url}?{urllib.parse.urlencode(params)}" if params else url
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(full_url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            as_json = json.loads(error_body)
        except Exception:
            as_json = {"error": {"message": error_body}}
        if e.code == 429:
            raise Exception(f"HTTP 429 Too Many Requests: {as_json.get('error',{}).get('message','rate limit')}")
        raise Exception(f"HTTP {e.code}: {as_json.get('error',{}).get('message','request failed')}")


def _parse_google_response(js: Dict[str, Any]) -> Tuple[List[Tuple[bytes, str]], Optional[str]]:
    images: List[Tuple[bytes, str]] = []
    text_out: Optional[str] = None
    try:
        cands = js.get("candidates") or []
        parts = ((cands[0] or {}).get("content") or {}).get("parts") or []
        for p in parts:
            if "inlineData" in p:
                mime = (p["inlineData"].get("mimeType") or "image/png").lower()
                b64 = p["inlineData"].get("data") or ""
                if b64:
                    images.append((base64.b64decode(b64), mime))
            elif "text" in p and not text_out:
                text_out = p["text"]
    except Exception:
        pass
    return images, text_out


def _banano_generate_image(*, api_key: str, model: str, endpoint: str,
                          prompt: str, images: List[bytes], aspect_ratio: Optional[str],
                          images_only: bool, timeout: int = 90, max_retries: int = 3) -> Dict[str, Any]:
    url = endpoint.rstrip("/") + f"/models/{model}:generateContent"
    params = {"key": api_key}
    payload = _build_google_payload(prompt=prompt, images=images,
                                   aspect_ratio=aspect_ratio, images_only=images_only)

    last = None
    for attempt in range(max_retries + 1):
        try:
            js = _http_post_json(url, params, payload, timeout=timeout)
            imgs, txt = _parse_google_response(js)
            return {"images": imgs, "text": txt, "raw": js}
        except Exception as e:
            last = e
            if "429" in str(e) and attempt < max_retries:
                wait = (2 ** attempt) * 5
                LOG.warning("rate limit, retry in %ss (%s/%s)", wait, attempt + 1, max_retries + 1)
                import time; time.sleep(wait); continue
            raise
    raise last or Exception("all retries failed")




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


def build_refine_prompt(*, base_prompt: str, extra: Optional[str] = None) -> str:
    """
    Промпт для 2-го прохода (интерьер): фиксируем размеры помещений и инженерные конструкции.
    """
    blocks: List[str] = []
    if base_prompt.strip():
        blocks.append("Context from the initial prompt:\n" + base_prompt.strip())
    blocks.append(REFINE_INTERIOR_INSTRUCTIONS_EN)
    if extra and extra.strip():
        blocks.append(extra.strip())
    return "\n\n".join(blocks)

# ==============
# HTTP Handler
# ==============

def design_generate(req: Request):
    """
    Flask-совместимый обработчик.
    Ожидает multipart/form-data:
      - image: file (обязательно)
      - prompt: str (если нет — можно передать style/room_type/furniture, и мы соберём промпт сами)
      - aspect_ratio: str (опц.; напр. '16:9')
      - response: 'image' | 'image+text' (опц.; по умолчанию env BANANO_IMAGES_ONLY)
      - api_key: str (опц.; приоритетный источник ключа)
      - second_pass: '0' | '1' (опц.; default '1')
      - refine_prompt: str (опц.; добавка к промпту для 2-го прохода)

    Ответ: JSON { images: [dataUrl,...], url?: http(s) } + debug при ?debug=1.
    """
    try:
        files = getattr(req, "files", None)
        form = getattr(req, "form", None)
        if files is None or form is None:
            return jsonify({"error": "bad_request", "detail": "multipart form-data expected"}), 400

        debug_flag = (req.args.get("debug") == "1") if hasattr(req, "args") else False
        request_id = req.headers.get("X-Request-ID", "")

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

        # Параметры ответа
        aspect_ratio = (form.get("aspect_ratio") or BANANO_ASPECT_RATIO or "").strip() or None
        response_mode = (form.get("response") or ("image" if BANANO_IMAGES_ONLY else "image+text")).strip().lower()
        images_only = response_mode == "image"
        second_pass_flag = (form.get("second_pass") or "1").strip() != "0"
        refine_extra = (form.get("refine_prompt") or "").strip()

        # Ключ
        api_key = _read_api_key(req)
        if not api_key:
            return jsonify({"error": "auth_error", "detail": "API key is required (header/form or ENV)"}), 401

        LOG.info("design_generate (banano) pass1 start req_id=%s model=%s", request_id, BANANO_MODEL)

        # 1-й проход — черновик
        p1 = _banano_generate_image(
            api_key=api_key,
            model=BANANO_MODEL,
            endpoint=BANANO_ENDPOINT,
            prompt=prompt,
            images=[img_bytes],
            aspect_ratio=aspect_ratio,
            images_only=images_only,
            max_retries=2,
        )

        # 2-й проход — истина (исходник) + черновик, акцент на геометрию/инженерию
        final_resp = p1
        if second_pass_flag and p1.get("images"):
            try:
                draft_bytes, _mime = p1["images"][0]
                refine_prompt = build_refine_prompt(base_prompt=prompt, extra=refine_extra)
                LOG.info("design_generate (banano) pass2 start req_id=%s", request_id)
                final_resp = _banano_generate_image(
                    api_key=api_key,
                    model=BANANO_MODEL,
                    endpoint=BANANO_ENDPOINT,
                    prompt=refine_prompt,
                    images=[img_bytes, draft_bytes],
                    aspect_ratio=aspect_ratio,
                    images_only=True,
                    max_retries=2,
                )
            except Exception as _e:
                LOG.warning("design_generate second pass skipped: %s", _e)
                final_resp = p1

        # Ответ
        out_imgs = [_to_data_url(b, mime=m) for b, m in final_resp.get("images", [])]
        body: Dict[str, Any] = {"ok": True, "model": BANANO_MODEL, "images": out_imgs}
        if out_imgs and isinstance(out_imgs[0], str) and out_imgs[0].startswith(("http://", "https://")):
            body["url"] = out_imgs[0]
        if not images_only and final_resp.get("text"):
            body["text"] = final_resp["text"]
        if debug_flag:
            body["debug"] = {
                "prompt_pass1": prompt,
                "prompt_pass2": (build_refine_prompt(base_prompt=prompt, extra=refine_extra) if second_pass_flag else ""),
                "image_meta": _image_meta(img_bytes),
                "endpoint": BANANO_ENDPOINT,
                "aspect_ratio": aspect_ratio,
                "response_mode": response_mode,
                "second_pass": bool(second_pass_flag),
                "pass1_images_count": len(p1.get("images", [])),
                "pass2_images_count": len(final_resp.get("images", [])) if second_pass_flag else 0,
            }
        return jsonify(body), 200

    except Exception as e:
        LOG.exception("Unhandled error in design_generate (banano)")
        return jsonify({"error": "internal_error", "detail": str(e)}), 500
