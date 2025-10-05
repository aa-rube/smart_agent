# smart_agent/executor/apps/plan_generate.py
from __future__ import annotations

"""
Self-contained module for the /plan/generate endpoint.

- Не зависит от внешних конфигов
- Все константы, клиенты, билдеры и обработчики находятся здесь
- Контроллер должен только делегировать: return plan_module.plan_generate(request)
"""

import io
import os
import hashlib
import logging
from typing import Any, Dict, Optional, List

from flask import jsonify, Request
import replicate
from replicate.exceptions import ReplicateError, ModelError

__all__ = ["plan_generate", "build_plan_prompt"]

LOG = logging.getLogger(__name__)

# =========================
#   Model / Runtime config
# =========================

# Реплицируем практику: если токен в окружении, убедимся, что SDK его увидит
if os.getenv("REPLICATE_API_TOKEN") and not os.environ.get("REPLICATE_API_TOKEN"):
    os.environ["REPLICATE_API_TOKEN"] = os.getenv("REPLICATE_API_TOKEN") or ""

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Модель планировок (env-overridable с безопасным дефолтом)
# По умолчанию ориентируемся на openai/gpt-image-1 у Replicate-обёрток.
MODEL_REF = os.getenv("MODEL_FLOOR_PLAN_REF", "openai/gpt-image-1")

# Какое имя поля ожидает модель для изображения.
# Для gpt-image-1 чаще всего — "input_images".
MODEL_IMAGE_PARAM = os.getenv("MODEL_FLOOR_PLAN_IMAGE_PARAM", "input_images")

# Нужно ли прокидывать OPENAI_API_KEY в input (для openai/* это обычно обязательно).
MODEL_NEEDS_OPENAI_KEY = os.getenv("MODEL_FLOOR_PLAN_NEEDS_OPENAI_KEY", "1") == "1"

# =========================
#        Prompt blocks
# =========================

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
- You must have exactly the same number of rooms as in the diagram uploaded by the user. This is very important!!! If you show them, no one will buy the apartment, we will have to close our business, and my child will be left without food.
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
- Add furniture and decorative elements (paintings, green plants, soft textiles, stylish lamps, elegant mirrors, and decorative items) — only in places where it does not affect the walls, doors, windows, and geometry of the room. The main thing: first, you must keep the walls exactly where they are, and only then can you arrange the furniture and interior. This is very important!!! If you show it, no one will buy the apartment, we will have to close our business, and my child will be left without food.
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

# ======================
#      Helper utils
# ======================

def _image_meta(img_bytes: bytes) -> Dict[str, Any]:
    """Минимальная мета для отладки — без внешних зависимостей."""
    return {
        "size_bytes": len(img_bytes),
        "sha256": hashlib.sha256(img_bytes).hexdigest(),
    }

def _extract_url(output: Any) -> Optional[str]:
    """Попытка вытащить URL результата из разных форматов ответа Replicate."""
    try:
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str) and item.startswith("http"):
                    return item
            for item in output:
                url = getattr(item, "url", None)
                if isinstance(url, str) and url.startswith("http"):
                    return url
        url = getattr(output, "url", None)
        if isinstance(url, str) and url.startswith("http"):
            return url
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
    """Формируем input для replicate.run. Картинку подаём как BytesIO (с именем файла)."""
    buf = io.BytesIO(img_bytes)
    buf.name = "upload.png"
    payload: Dict[str, Any] = {"prompt": prompt}
    if needs_openai_key and openai_api_key:
        payload["openai_api_key"] = openai_api_key
    if image_param == "input_images":
        payload["input_images"] = [buf]
    else:
        payload[image_param] = buf
    return payload

def _run_with_fallbacks(img_bytes: bytes, prompt: str) -> str:
    """
    Запускаем replicate.run c прогрессивными фолбэками имени поля изображения:
    MODEL_IMAGE_PARAM -> 'image' -> 'input_image' -> 'input_images'.
    Возвращаем URL результата или выбрасываем исключение.
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
            LOG.info("Replicate.run (plan) model=%s try image_param=%s keys=%s", MODEL_REF, param, list(payload.keys()))
            out = replicate.run(MODEL_REF, input=payload)
            url = _extract_url(out)
            if url:
                if param != MODEL_IMAGE_PARAM:
                    LOG.warning("Image param auto-switched: %s -> %s", MODEL_IMAGE_PARAM, param)
                return url
            last_err = RuntimeError("No URL in output")
        except (ModelError, ReplicateError) as e:
            last_err = e
            # Если по метрикам видно, что изображение не распозналось — пробуем дальше
            try:
                pred = getattr(e, "prediction", None)
                metrics = getattr(pred, "metrics", {}) if pred else {}
                if (metrics or {}).get("image_count") == 0:
                    LOG.warning("Replicate image param suspected mismatch for %s: %s", param, e)
                    continue
            except Exception:
                pass
            # Иные ошибки — пробрасываем наружу
            raise
        except Exception as e:
            last_err = e

    if last_err:
        raise last_err
    raise RuntimeError("Unknown replicate fallback failure")

# =================
#   Prompt builder
# =================

def build_plan_prompt(*, visualization_style: str, interior_style: str) -> str:
    """
    Собирает единый промпт для генерации планировки.
    visualization_style: 'sketch' | 'realistic' (любой иной — трактуем как 'realistic')
    interior_style: произвольная строка (UI-перечень стилей может меняться)
    """
    vis = (visualization_style or "").strip().lower()
    vis_block = FLOOR_PLAN_VISUALIZATION_SKETCH if vis == "sketch" else FLOOR_PLAN_VISUALIZATION_REALISTIC

    final_block = FLOOR_PLAN_FINAL_INSTRUCTIONS.format(interior_style=(interior_style or "Modern").strip() or "Modern")

    # Склеиваем блоки с аккуратной нормализацией
    parts = [FLOOR_PLAN_BASE_INSTRUCTIONS, vis_block, final_block]
    prompt = "\n\n".join([p.strip() for p in parts if p and p.strip()])

    # На всякий — подчистим возможные дабл-пробелы после конкатенации
    return "\n".join([line.rstrip() for line in prompt.splitlines() if line.strip()])

# ==============
#  HTTP handler
# ==============

def plan_generate(req: Request):
    """
    Flask-совместимый обработчик планировок.
    Ожидает multipart/form-data с полями:
      - image: file (обязательно)
      - prompt: str (опционально) — готовая подсказка
        ИЛИ (если prompt не передан)
      - visualization_style: 'sketch' | 'realistic' (опционально; по умолчанию realistic)
      - interior_style: str (обязательно при отсутствии prompt)

    Query-параметр:
      - ?debug=1 — вернёт prompt, meta и model_ref
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

        # Берём готовый prompt, либо собираем из стилей
        prompt = (form.get("prompt") or "").strip()
        if not prompt:
            visualization_style = (form.get("visualization_style") or "realistic").strip()
            interior_style = (form.get("interior_style") or "").strip()
            if not interior_style:
                return jsonify({
                    "error": "bad_request",
                    "detail": "either 'prompt' or ('interior_style' [+ visualization_style]) is required",
                }), 400
            prompt = build_plan_prompt(visualization_style=visualization_style, interior_style=interior_style)

        meta = _image_meta(img_bytes)

        # Генерация изображения на Replicate (+ умный фолбэк имени поля)
        url = _run_with_fallbacks(img_bytes, prompt)

        body: Dict[str, Any] = {"url": url}
        if debug_flag:
            body["debug"] = {
                "prompt": prompt,
                "image_meta": meta,
                "model_ref": MODEL_REF,
            }
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
        LOG.exception("Unhandled error in plan_generate")
        return jsonify({"error": "internal_error", "detail": str(e)}), 500
