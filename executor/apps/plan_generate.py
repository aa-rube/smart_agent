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
- Ключ берём из запроса, если нет — из ENV/конфига
"""

import json
import base64
import hashlib
import logging
import urllib.error
from executor.config import *
from typing import Any, Dict, Optional, List, Tuple

from flask import jsonify, Request

# =========================
#   Model / Runtime config
# =========================

LOG = logging.getLogger(__name__)



# Публичный REST endpoint Google (можно прокинуть свой прокси)
BANANO_ENDPOINT = os.getenv("BANANO_ENDPOINT", "https://generativelanguage.googleapis.com/v1beta")

# Модель для генерации изображений
BANANO_MODEL = os.getenv("BANANO_MODEL", "gemini-2.5-flash-image")

# По умолчанию — только изображение в ответе (без текстовых частей)
BANANO_IMAGES_ONLY = os.getenv("BANANO_IMAGES_ONLY", "1") == "1"

# Необязательное соотношение сторон по умолчанию: "", "1:1", "16:9", "9:16", ...
BANANO_ASPECT_RATIO = os.getenv("BANANO_ASPECT_RATIO", "")

__all__ = ["plan_generate", "build_plan_prompt"]


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
#    Banano HTTP client
# ======================

def _build_google_payload(
    *,
    prompt: str,
    images: List[bytes],
    aspect_ratio: Optional[str],
    images_only: bool,
) -> Dict[str, Any]:
    """
    Строим JSON под Google REST:
    POST /models/{model}:generateContent?key=API_KEY
    {
      "contents":[{"role":"user","parts":[{"text":"..."},{"inlineData":{"mimeType":"...","data":"...b64..."}}]}],
      "generationConfig":{"responseModalities":["IMAGE"],"imageConfig":{"aspectRatio":"16:9"}}
    }
    """
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for b in images:
        parts.append({
            "inlineData": {
                "mimeType": _detect_mime(b),
                "data": base64.b64encode(b).decode("ascii"),
            }
        })

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
    """
    Минимальный HTTP POST с использованием стандартной библиотеки (без сторонних зависимостей).
    Если в окружении установлен requests — можно раскомментировать альтернативу.
    """
    # --- Вариант на стандартной библиотеке ---
    import urllib.request
    import urllib.parse

    full_url = url
    if params:
        q = urllib.parse.urlencode(params)
        full_url = f"{url}?{q}"

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        full_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read()
            return json.loads(resp_body.decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        try:
            error_json = json.loads(error_body)
        except:
            error_json = {"error": {"message": error_body}}
        
        # Преобразуем HTTP ошибку в исключение с деталями
        if e.code == 429:
            raise Exception(f"HTTP Error 429: Too Many Requests - {error_json.get('error', {}).get('message', 'Rate limit exceeded')}")
        elif e.code >= 400:
            raise Exception(f"HTTP Error {e.code}: {error_json.get('error', {}).get('message', 'API request failed')}")
        else:
            raise Exception(f"HTTP Error {e.code}: {error_body}")

    # --- Вариант с requests (если предпочитаешь) ---
    # import requests
    # r = requests.post(url, params=params, json=body, timeout=timeout)
    # r.raise_for_status()
    # return r.json()


def _parse_google_response(js: Dict[str, Any]) -> Tuple[List[Tuple[bytes, str]], Optional[str]]:
    """
    Возвращает ([(bytes, mime)], optional_text).
    """
    images: List[Tuple[bytes, str]] = []
    text_out: Optional[str] = None
    try:
        cands = js.get("candidates") or []
        if not cands:
            return images, text_out
        parts = ((cands[0] or {}).get("content") or {}).get("parts") or []
        for p in parts:
            if "inlineData" in p:
                inline = p["inlineData"]
                mime = (inline.get("mimeType") or "image/png").lower()
                data_b64 = inline.get("data") or ""
                if data_b64:
                    images.append((base64.b64decode(data_b64), mime))
            elif "text" in p and not text_out:
                text_out = p["text"]
    except Exception:
        pass
    return images, text_out


def _banano_generate_image(
    *,
    api_key: str,
    model: str,
    endpoint: str,
    prompt: str,
    images: List[bytes],
    aspect_ratio: Optional[str],
    images_only: bool,
    timeout: int = 90,
    max_retries: int = 3,
) -> Dict[str, Any]:
    """
    Тонкий вызов REST: {endpoint}/models/{model}:generateContent?key=API_KEY
    С retry логикой для rate limits
    """
    import time
    
    url = endpoint.rstrip("/") + f"/models/{model}:generateContent"
    params = {"key": api_key}

    payload = _build_google_payload(
        prompt=prompt,
        images=images,
        aspect_ratio=aspect_ratio,
        images_only=images_only,
    )

    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            resp_json = _http_post_json(url, params, payload, timeout=timeout)
            out_images, out_text = _parse_google_response(resp_json)
            return {"images": out_images, "text": out_text, "raw": resp_json}
        except Exception as e:
            last_exception = e
            error_msg = str(e)
            
            # Если rate limit и есть еще попытки - ждем и повторяем
            if "429" in error_msg and attempt < max_retries:
                wait_time = (2 ** attempt) * 5  # Экспоненциальная задержка: 5, 10, 20 сек
                LOG.warning(f"Rate limit hit, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries + 1})")
                time.sleep(wait_time)
                continue
            else:
                # Не rate limit или кончились попытки
                raise e
    
    # Если дошли сюда, значит все попытки исчерпаны
    raise last_exception or Exception("All retry attempts failed")


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

        # 4) Ключ
        api_key = _read_api_key(req)
        if not api_key:
            return jsonify({"error": "auth_error", "detail": "API key is required (header or form, or ENV)"}), 401

        LOG.info("plan_generate (banano) start req_id=%s model=%s", request_id, BANANO_MODEL)

        # 5) Вызов Banano/Gemini
        nb_resp = _banano_generate_image(
            api_key=api_key,
            model=BANANO_MODEL,
            endpoint=BANANO_ENDPOINT,
            prompt=prompt,
            images=[img_bytes],
            aspect_ratio=aspect_ratio,
            images_only=images_only,
            max_retries=2,  # Максимум 3 попытки всего
        )

        # 6) Ответ
        out_imgs = [_to_data_url(b, mime=m) for b, m in nb_resp.get("images", [])]
        body: Dict[str, Any] = {"ok": True, "model": BANANO_MODEL, "images": out_imgs}
        # url публикуем только если это http(s), чтобы клиент не принимал data: как линк
        if out_imgs and isinstance(out_imgs[0], str) and out_imgs[0].startswith(("http://", "https://")):
            body["url"] = out_imgs[0]
        if not images_only and nb_resp.get("text"):
            body["text"] = nb_resp["text"]

        if debug_flag:
            body["debug"] = {
                "prompt": prompt,
                "image_meta": _image_meta(img_bytes),
                "request_id": request_id,
                "aspect_ratio": aspect_ratio,
                "response_mode": response_mode,
                "endpoint": BANANO_ENDPOINT,
            }

        return jsonify(body), 200

    except Exception as e:
        LOG.exception("Unhandled error in plan_generate (banano)")
        body = {"error": "internal_error", "detail": str(e)}
        try:
            if (req.args.get("debug") == "1"):
                body["debug"] = {
                    "endpoint": BANANO_ENDPOINT,
                    "model": BANANO_MODEL,
                }
        except Exception:
            pass
        return jsonify(body), 500
