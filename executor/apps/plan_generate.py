# smart_agent/executor/apps/plan_generate.py
from __future__ import annotations

"""
Self-contained module for the /plan/generate endpoint.

- Не зависит от внешних конфигов проекта (кроме ENV)
- Все константы, HTTP-клиент, билдеры и обработчики находятся здесь
- Контроллер должен только делегировать: return plan_module.plan_generate(request)

Задача:
- Принять картинку и сгенерированный промпт
- Отправить в Banano (Gemini 2.5 Flash Image) без SDK — прямой HTTP
- Вернуть результат обработки (картинка(и) и опционально текст)
- Поддержать двухпроходную схему: 1) черновик; 2) уточнение c «картинка-истина + черновик + промпт»
- Ключ берём из запроса, если нет — из ENV/конфига
"""

import base64
import hashlib
import logging
from executor.config import *
from typing import Any, Dict, Optional, List, Tuple
import os

from flask import jsonify, Request

# =========================
#   Model / Runtime config
# =========================

LOG = logging.getLogger(__name__)



# Модель для генерации изображений
BANANO_MODEL = os.getenv("BANANO_MODEL", "gemini-2.5-flash-image")

# По умолчанию — только изображение в ответе (без текстовых частей)
BANANO_IMAGES_ONLY = os.getenv("BANANO_IMAGES_ONLY", "1") == "1"

# Необязательное соотношение сторон по умолчанию: "", "1:1", "16:9", "9:16", ...
BANANO_ASPECT_RATIO = os.getenv("BANANO_ASPECT_RATIO", "")

__all__ = ["plan_generate", "build_plan_prompt", "build_refine_prompt"]

from google import genai
from google.genai import types
from PIL import Image
from io import BytesIO


# ======================
#      Prompt blocks
# ======================

FLOOR_PLAN_BASE_INSTRUCTIONS = """
🧠 INSTRUCTION FOR AI: Generation of a 2D/3D real estate floor plan based on an image.
🎯 GOAL:
Create a visually appealing, accurate, and sellable property layout based on the user's uploaded image/drawing. The visualization must evoke a desire to purchase the property.
✅ MANDATORY RULES:
-❗ CRITICAL RULE — THE GEOMETRY OF THE ROOM CANNOT BE CHANGED.
    WALLS CANNOT BE:
* moved even by 1 mm;
* changed in thickness, shape, or length;
* removed, added, bent, or straightened;
* change the angle, curvature, or location.
 WALLS MUST:
* remain strictly within the coordinates specified in the source data;
* completely replicate the original shape down to the pixel/millimeter;
* maintain absolute consistency with the original layout.
* Any deviation, even minimal, is considered a gross error. If a wall is changed, the work is considered incorrect and unacceptable.
 🔴 Remember: the geometry and location of walls are unchangeable. Changing them is prohibited under any circumstances.

- Keep all “wet areas” (kitchen, bathroom, toilet) in their places. This is very important. If the layout shows a bathroom schematically or mentions it, it must appear strictly in the same place!!! The same applies to the bathroom.
- If you see a schematic drawing of a sink on the drawing, then you must draw a sink in that place. If you see a toilet on the drawing, then you must draw a toilet in that place. If you see a bathroom on the drawing, then you must draw a bathroom in that place. If you see a stove on the drawing, then you must depict a kitchen in that place. This is very important!!! If you move it, then no one will buy the apartment, we will have to close our business, and my child will be left without food.
- If the layout that was uploaded to you does not show a balcony, then you do not need to include it in the final image. This is very, very important!
- All doors in the images you create must look like doors!!! No semicircular doors are allowed!!! If you see a semicircular door on the diagram, you must show it as a regular door in the image; it must not be open! This is very important!!! If you show it, no one will buy the apartment, we will have to close our business, and my child will be left without food.
- You are strictly prohibited from showing the dimensions along the axes and the axes themselves. You can only show the areas inside the room itself. There should be no numbers outside the room!!! This is very important!!! If you show them, no one will buy our apartment, we will have to close our business, and my child will be left without food.
- You must have exactly the same number of rooms as in the diagram uploaded by the user. This is very important!!! If you show them, no one will buy the apartment, we will have to close our business, and my child will be left без еды.
- Generate a clean vector-style floor plan with flat fills and crisp lines.
- Absolutely no text: no letters, numbers, symbols, words, logos, watermarks, labels, signage, captions, legends, scales, north arrows, room names, dimensions, level marks.
If the source image contains text, completely remove it and replace with a uniform background/texture matching the surroundings.
Only geometric shapes for walls, doors, windows, furniture — with zero markings.
If any character appears, re-generate or inpaint until there is no text at all.
No typography-like textures or patterns.
Output: a text-free floor plan.
- All rooms must be fully displayed — no cropped parts are allowed. If they do not fit in the frame, zoom out, but show the entire layout.
- All wall lines shown on the floor plan must be reproduced on the image in their exact locations and dimensions!!!
- Add floor texture to the floor.
- Add furniture and decorative elements (paintings, green plants, soft textiles, stylish lamps, elegant mirrors, and decorative items) — only in places where it does not affect the walls, doors, windows, and geometry of the room. The main thing: first, you must keep the walls exactly where they are, and only then can you arrange the furniture and interior.
""".strip()

FLOOR_PLAN_VISUALIZATION_SKETCH = """
🖊️ SKETCH-STYLE VISUALIZATION:
- The visualization must be in color.
- The sketch style should look as if drawn by a professional artist by hand, but with:
  - Colored fills for rooms.
  - Shadows and details.
  - A vibrant, pleasant palette.
  - A visual atmosphere of coziness, light, and textures.
- Absolutely no black-and-white schemes or CAD graphics! It must be a colorful, artistic sketch, perfect for a real estate presentation.
""".strip()

FLOOR_PLAN_VISUALIZATION_REALISTIC = """
📸 REALISTIC-STYLE VISUALIZATION:
- Focus on photorealism, accurate materials, and lifelike lighting.
- The final image should be indistinguishable from a high-quality 3D render.
""".strip()

FLOOR_PLAN_FINAL_INSTRUCTIONS = """
🎨 DESIGN AND STYLE:
- User-selected Format: 2D
- User-selected Interior Style: {interior_style}
💡 FINAL RESULT:
The floor plan must be:
- Complete (the entire plan fits in the frame).
- Accurate (everything from the source image is preserved).
- Beautiful and stylish (in accordance with the chosen style).
- As cozy and desirable as possible for the buyer.
The buyer should see the layout, fall in love with it, and want to buy this home from the realtor immediately. Imagine that your fate depends on this specific outcome.
""".strip()

REFINE_PASS_INSTRUCTIONS_EN = """
REFINE PASS (Image-to-Image with two inputs):
Use image #1 as the ground-truth geometry (walls, doors, windows). It is immutable.
Use image #2 as a draft for colors/furniture/textures only.
Hard rules:
- Do NOT move, bend, resize or remove any walls/partitions from image #1.
- Keep the room count identical to image #1. Keep all wet areas in the same places.
- Remove ALL text, numbers, labels and axis marks completely.
- Doors must be simple closed rectangles; no swing arcs or semicircles.
- If any text appears, regenerate/clean until there is none.
Output only the final cleaned image.
""".strip()

def build_refine_prompt(*, base_prompt: str, extra: Optional[str] = None) -> str:
    """
    Готовит промпт для 2-го прохода: поясняем роли изображений и жёсткие ограничения.
    base_prompt — исходный промпт первого прохода (встраиваем как контекст).
    extra — необязательное уточнение от клиента.
    """
    parts: List[str] = []
    if base_prompt.strip():
        parts.append("Context from the initial prompt:\n" + base_prompt.strip())
    parts.append(REFINE_PASS_INSTRUCTIONS_EN)
    if extra and extra.strip():
        parts.append(extra.strip())
    # компактная склейка
    return "\n\n".join(parts)


# ======================
#          Utils
# ======================

def _read_api_key(req: Request) -> str:
    """
    Источник API-ключа (приоритет):
      1) из запроса: form['api_key'] либо Authorization: Bearer/ X-API-Key / X-Banano-Key
      2) из ENV (BANANO_API_KEY / GOOGLE_API_KEY / GEMINI_API_KEY)
    """
    # multipart/form-data
    try:
        if hasattr(req, "form") and req.form and "api_key" in req.form:
            v = (req.form.get("api_key") or "").strip()
            if v:
                return v
    except Exception:
        pass

    # Authorization: Bearer <token>
    auth = req.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()

    # X-API-Key / X-Banano-Key
    for h in ("X-API-Key", "X-Banano-Key", "X-Api-Key", "X-BANANO-KEY"):
        v = req.headers.get(h, "")
        if v:
            return v.strip()

    # ENV fallback
    return BANANO_API_KEY_FALLBACK


def _image_meta(img_bytes: bytes) -> Dict[str, Any]:
    """Простая мета для отладки."""
    return {"size_bytes": len(img_bytes), "sha256": hashlib.sha256(img_bytes).hexdigest()}


def _detect_mime(b: bytes) -> str:
    """Мини-сниффер по сигнатурам."""
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _to_data_url(img_bytes: bytes, mime: str = "image/png") -> str:
    """Возвращаем data:URL, чтобы удобно отдавать через JSON."""
    return f"data:{mime};base64,{base64.b64encode(img_bytes).decode('ascii')}"


# ======================
#    google-genai client
# ======================

def _genai_generate_image(
    *,
    api_key: str,
    model: str,
    prompt: str,
    images: List[bytes],
    aspect_ratio: Optional[str],
    images_only: bool,
) -> Dict[str, Any]:
    """
    Вызов через официальную библиотеку google-genai.
    Возвращает {"images": [(bytes, mime)], "text": Optional[str]}.
    """
    client = genai.Client(api_key=api_key)

    contents: List[Any] = [prompt]
    for b in images:
        contents.append(Image.open(BytesIO(b)))  # как в оф. примерах

    cfg_kwargs: Dict[str, Any] = {}
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
#   Prompt builder
# =================

def build_plan_prompt(*, visualization_style: str, interior_style: str) -> str:
    """
    Собирает единый промпт для генерации планировки.
    visualization_style: 'sketch' | 'realistic' (любой иной — трактуем как 'realistic')
    interior_style: произвольная строка
    """
    vis = (visualization_style or "").strip().lower()
    vis_block = FLOOR_PLAN_VISUALIZATION_SKETCH if vis == "sketch" else FLOOR_PLAN_VISUALIZATION_REALISTIC
    final_block = FLOOR_PLAN_FINAL_INSTRUCTIONS.format(
        interior_style=(interior_style or "Modern").strip() or "Modern"
    )

    parts = [FLOOR_PLAN_BASE_INSTRUCTIONS, vis_block, final_block]
    prompt = "\n\n".join([p.strip() for p in parts if p and p.strip()])
    return "\n".join([line.rstrip() for line in prompt.splitlines() if line.strip()])


# ==============
#  HTTP handler
# ==============

def plan_generate(req: Request):
    """
    Flask-совместимый обработчик.
    Ожидает multipart/form-data:
      - image: file (обязательно)
      - prompt: str (обязательно в рамках ТЗ; но если не передали — попробуем собрать из стилей)
      - visualization_style: 'sketch' | 'realistic' (опц.; default=realistic)
      - interior_style: str (опц.; используется, если prompt не передан)
      - aspect_ratio: str (опц.; например '16:9')
      - response: 'image' | 'image+text' (опц.; default=env BANANO_IMAGES_ONLY)
      - second_pass: '0' | '1' (опц.; default='1') — запускать ли уточняющий 2-й проход
      - refine_prompt: str (опц.) — добавка к промпту для 2-го прохода
      - api_key: str (опц.; приоритетный источник ключа)
    Query:
      - ?debug=1 — вернуть отладочные поля
    """
    try:
        files = getattr(req, "files", None)
        form = getattr(req, "form", None)
        if files is None or form is None:
            return jsonify({"error": "bad_request", "detail": "multipart form-data expected"}), 400

        debug_flag = (req.args.get("debug") == "1") if hasattr(req, "args") else False
        request_id = req.headers.get("X-Request-ID", "")

        # 1) Изображение
        if "image" not in files:
            return jsonify({"error": "bad_request", "detail": "field 'image' is required"}), 400
        img_bytes = files["image"].read()
        if not img_bytes or len(img_bytes) < 64:
            return jsonify({"error": "bad_request", "detail": "image is empty or too small"}), 400

        # 2) Промпт
        prompt = (form.get("prompt") or "").strip()
        if not prompt:
            # Допускаем автосборку (чтобы не падать, если фронт пока шлёт стили)
            visualization_style = (form.get("visualization_style") or "realistic").strip()
            interior_style = (form.get("interior_style") or "").strip()
            if not interior_style:
                return jsonify({
                    "error": "bad_request",
                    "detail": "either 'prompt' or ('interior_style' [+ visualization_style]) is required",
                }), 400
            prompt = build_plan_prompt(
                visualization_style=visualization_style,
                interior_style=interior_style
            )

        # 3) Режим ответа и параметры
        aspect_ratio = (form.get("aspect_ratio") or BANANO_ASPECT_RATIO or "").strip() or None
        response_mode = (form.get("response") or ("image" if BANANO_IMAGES_ONLY else "image+text")).strip().lower()
        images_only = response_mode == "image"
        second_pass_flag = (form.get("second_pass") or "1").strip() != "0"
        refine_prompt_extra = (form.get("refine_prompt") or "").strip()

        # 4) Ключ
        api_key = _read_api_key(req)
        if not api_key:
            return jsonify({"error": "auth_error", "detail": "API key is required (header or form, or ENV)"}), 401

        LOG.info("plan_generate (genai) start req_id=%s model=%s", request_id, BANANO_MODEL)

        # 5) 1-й проход: черновик
        nb_resp = _genai_generate_image(
            api_key=api_key,
            model=BANANO_MODEL,
            prompt=prompt,
            images=[img_bytes],
            aspect_ratio=aspect_ratio,
            images_only=images_only,
        )

        # 6) 2-й проход (опционально): картинка-истина + черновик
        final_resp = nb_resp
        if second_pass_flag and nb_resp.get("images"):
            try:
                draft_img_bytes, draft_mime = nb_resp["images"][0]  # берём первое изображение черновика
                refine_prompt = build_refine_prompt(base_prompt=prompt, extra=refine_prompt_extra)
                LOG.info("plan_generate (genai) second pass start req_id=%s model=%s", request_id, BANANO_MODEL)
                final_resp = _genai_generate_image(
                    api_key=api_key,
                    model=BANANO_MODEL,
                    prompt=refine_prompt,
                    images=[img_bytes, draft_img_bytes],  # истина + черновик
                    aspect_ratio=aspect_ratio,
                    images_only=True,  # финал — только картинка
                )
            except Exception as _e:
                LOG.warning("Second pass skipped due to error: %s", _e)
                final_resp = nb_resp

        # 7) Ответ
        out_imgs = [_to_data_url(b, mime=m) for b, m in final_resp.get("images", [])]
        body: Dict[str, Any] = {"ok": True, "model": BANANO_MODEL, "images": out_imgs}
        # url публикуем только если это http(s), чтобы клиент не принимал data: как линк
        if out_imgs and isinstance(out_imgs[0], str) and out_imgs[0].startswith(("http://", "https://")):
            body["url"] = out_imgs[0]
        if not images_only and final_resp.get("text"):
            body["text"] = final_resp["text"]

        if debug_flag:
            body["debug"] = {
                "prompt_pass1": prompt,
                "prompt_pass2": (build_refine_prompt(base_prompt=prompt, extra=refine_prompt_extra) if second_pass_flag else ""),
                "image_meta": _image_meta(img_bytes),
                "request_id": request_id,
                "aspect_ratio": aspect_ratio,
                "response_mode": response_mode,
                "lib": "google.genai",
                "second_pass": bool(second_pass_flag),
                "pass1_images_count": len(nb_resp.get("images", [])),
                "pass2_images_count": len(final_resp.get("images", [])) if second_pass_flag else 0,
            }

        return jsonify(body), 200

    except Exception as e:
        LOG.exception("Unhandled error in plan_generate (genai)")
        body = {"error": "internal_error", "detail": str(e)}
        try:
            if (req.args.get("debug") == "1"):
                body["debug"] = {
                    "model": BANANO_MODEL,
                    "lib": "google.genai",
                }
        except Exception:
            pass
        return jsonify(body), 500
