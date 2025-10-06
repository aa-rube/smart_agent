#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\apps\design_generate.py
from __future__ import annotations

"""
Self-contained module for the /design/generate endpoint.

— Не тянет внешние конфиги (кроме ENV/ executor.config для фолбэка ключа)
— Все константы, билдеры, обработчики и утилиты — внутри
— Контроллер должен лишь делегировать сюда: design_generate(request)
— Двухпроходная схема: 1) черновик; 2) уточнение (истина + черновик + промпт)
"""

import base64
import hashlib
import logging
from typing import Any, Dict, Optional, List, Tuple

from flask import jsonify, Request
from executor.config import *  # BANANO_API_KEY_FALLBACK и т.п.

__all__ = ["design_generate", "build_design_prompt", "build_refine_prompt"]

LOG = logging.getLogger(__name__)

# =========================
# Constants: Gemini via google-genai
# =========================

# Модель генерации изображений
BANANO_MODEL = os.getenv("BANANO_MODEL_INTERIOR", "gemini-2.5-flash-image")

# По умолчанию — только изображения в ответе
BANANO_IMAGES_ONLY = os.getenv("BANANO_IMAGES_ONLY", "1") == "1"

# Необязательное соотношение сторон
BANANO_ASPECT_RATIO = os.getenv("BANANO_ASPECT_RATIO", "")

from google import genai
from google.genai import types
from PIL import Image
from io import BytesIO

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

REFINE_COMMON_EN = """
REFINE PASS (Image-to-Image with two inputs):
• Image #1 = ground-truth shell (immutable geometry & engineering).
• Image #2 = draft (colors, lighting, materials, movable furniture).
Hard constraints (apply always):
- Preserve room sizes/proportions exactly; do NOT move/resize/remove walls or partitions.
- Keep door/window openings at the same positions and dimensions.
- Preserve engineering: load-bearing elements, gas risers/pipes, water/plumbing lines, and wet areas.
- No text/numbers/labels/axis; remove any typography-like artifacts.
""".strip()

REFINE_ZERO_EXTRA_EN = """
Mode: ZERO-DESIGN
- You may fully redesign finishes and materials; wall coverings can be removed/changed.
- Propose the best functional layout and furniture placement for the chosen style.
- No structural changes; engineering stays intact.
Output: photo-realistic image with geometry identical to Image #1.
""".strip()

REFINE_REDESIGN_EXTRA_EN = """
Mode: REDESIGN (cosmetic refresh, no capital renovation)
- Keep existing finishes for walls/ceiling/floor; minimal repaint allowed if necessary.
- Replace loose furniture and cabinetry; update décor and textiles to the chosen style.
- No structural changes; engineering stays intact.
Output: photo-realistic image with geometry identical to Image #1.
""".strip()

# Режимные правила для 1-го прохода (усиление требований в base prompt)
ZERO_DESIGN_RULES_EN = """
ZERO-DESIGN MODE:
- Remove/replace all existing wall coverings and finishes; treat walls as a clean base.
- You may redesign finishes (walls/floor/ceiling), materials, palette; propose optimal furniture layout.
- Keep structural and engineering intact (load-bearing, gas/water lines, wet areas).
""".strip()

REDESIGN_RULES_EN = """
REDESIGN MODE (no capital renovation):
- Preserve existing finishes; minimal repaint only if really needed.
- Completely replace loose furniture and casework with better options for this room in the chosen style.
- Do not move doors/windows/radiators/plumbing/gas lines; keep geometry/openings identical.
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

def _genai_generate_image(*, api_key: str, model: str,
                          prompt: str, images: List[bytes],
                          aspect_ratio: Optional[str],
                          images_only: bool) -> Dict[str, Any]:
    """
    Обертка над google-genai: generate_content(model, contents=[prompt, *images], config=...)
    Возвращает {"images": List[(bytes, mime)], "text": Optional[str]} — как раньше.
    """
    client = genai.Client(api_key=api_key)

    # contents: сначала текст, далее PIL-изображения
    contents: List[Any] = [prompt]
    for b in images:
        # подсовываем PIL.Image, как в официальных примерах
        contents.append(Image.open(BytesIO(b)))

    cfg_kwargs: Dict[str, Any] = {}
    # response_modalities: по умолчанию Text+Image; для "только картинки" сузим
    if images_only:
        cfg_kwargs["response_modalities"] = ["Image"]
    if aspect_ratio:
        cfg_kwargs["image_config"] = types.ImageConfig(aspect_ratio=aspect_ratio)

    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(**cfg_kwargs) if cfg_kwargs else None,
    )

    out_images: List[Tuple[bytes, str]] = []
    out_text: Optional[str] = None
    try:
        cand = resp.candidates[0]
        for part in cand.content.parts:
            if getattr(part, "text", None):
                # копим только первый осмысленный текст
                if not out_text:
                    out_text = part.text
            elif getattr(part, "inline_data", None):
                data = part.inline_data.data
                mime = getattr(part.inline_data, "mime_type", None) or "image/png"
                out_images.append((data, mime))
    except Exception:
        pass

    return {"images": out_images, "text": out_text}




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

    # режим определяем строго по присутствию furniture (совпадает с твоими хендлерами)
    is_zero = bool(room_type and (furniture is not None))

    if room_type and not is_zero:
        room_text = ROOM_TYPE_PROMPTS.get(room_type, "room")
        core = PROMPT_REDESIGN.format(base_prompt=base_prompt, room_type=room_text, style_text=style_text)
        final_prompt = f"{core}. {REDESIGN_RULES_EN}"
    elif room_type and is_zero:
        room_text = ROOM_TYPE_PROMPTS.get(room_type, "room")
        furniture_text = FURNITURE_PROMPTS.get(furniture, "")
        core = PROMPT_ZERO_DESIGN.format(
            base_prompt=base_prompt, room_type=room_text, furniture_text=furniture_text, style_text=style_text
        )
        final_prompt = f"{core}. {ZERO_DESIGN_RULES_EN}"
    else:
        final_prompt = f"{base_prompt}, {style_text}"

    # tidy redundant commas/spaces
    parts = [p.strip() for p in final_prompt.split(",") if p and p.strip()]
    return ", ".join(parts)


def build_refine_prompt(*, base_prompt: str, is_zero: bool, extra: Optional[str] = None) -> str:
    """
    Промпт для 2-го прохода: общий блок (геометрия/инженерия) + режимозависимая часть.
    """
    blocks: List[str] = []
    if base_prompt.strip():
        blocks.append("Context from pass #1:\n" + base_prompt.strip())
    blocks.append(REFINE_COMMON_EN)
    blocks.append(REFINE_ZERO_EXTRA_EN if is_zero else REFINE_REDESIGN_EXTRA_EN)
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
        else:
            # если prompt пришёл готовый, всё равно определим режим для 2-го прохода
            style = (form.get("style") or "").strip()
            room_type = (form.get("room_type") or "").strip() or None
            furniture = (form.get("furniture") or "").strip() or None

        # Параметры ответа
        aspect_ratio = (form.get("aspect_ratio") or BANANO_ASPECT_RATIO or "").strip() or None
        response_mode = (form.get("response") or ("image" if BANANO_IMAGES_ONLY else "image+text")).strip().lower()
        images_only = response_mode == "image"
        second_pass_flag = (form.get("second_pass") or "1").strip() != "0"
        refine_extra = (form.get("refine_prompt") or "").strip()
        is_zero = bool(room_type and (furniture is not None))

        # Ключ
        api_key = _read_api_key(req)
        if not api_key:
            return jsonify({"error": "auth_error", "detail": "API key is required (header/form or ENV)"}), 401

        LOG.info("design_generate (genai) pass1 start req_id=%s model=%s", request_id, BANANO_MODEL)

        # 1-й проход — черновик
        p1 = _genai_generate_image(
            api_key=api_key,
            model=BANANO_MODEL,
            prompt=prompt,
            images=[img_bytes],
            aspect_ratio=aspect_ratio,
            images_only=images_only,
        )

        # 2-й проход — истина (исходник) + черновик, режимозависимые уточнения
        final_resp = p1
        if second_pass_flag and p1.get("images"):
            try:
                draft_bytes, _mime = p1["images"][0]
                refine_prompt = build_refine_prompt(base_prompt=prompt, is_zero=is_zero, extra=refine_extra)
                LOG.info("design_generate (genai) pass2 start req_id=%s mode=%s", request_id, ("zero" if is_zero else "redesign"))
                final_resp = _genai_generate_image(
                    api_key=api_key,
                    model=BANANO_MODEL,
                    prompt=refine_prompt,
                    images=[img_bytes, draft_bytes],
                    aspect_ratio=aspect_ratio,
                    images_only=True,   # во 2-м проходе нам нужна только финальная картинка
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
                "prompt_pass2": (build_refine_prompt(base_prompt=prompt, is_zero=is_zero, extra=refine_extra) if second_pass_flag else ""),
                "image_meta": _image_meta(img_bytes),
                "lib": "google.genai",
                "aspect_ratio": aspect_ratio,
                "response_mode": response_mode,
                "second_pass": bool(second_pass_flag),
                "mode": ("zero" if is_zero else "redesign") if room_type else "generic",
                "pass1_images_count": len(p1.get("images", [])),
                "pass2_images_count": len(final_resp.get("images", [])) if second_pass_flag else 0,
            }
        return jsonify(body), 200

    except Exception as e:
        LOG.exception("Unhandled error in design_generate (genai)")
        return jsonify({"error": "internal_error", "detail": str(e)}), 500
