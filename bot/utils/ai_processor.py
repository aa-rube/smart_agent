#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\ai_processor.py
import os
import aiohttp
from bot.config import EXECUTOR_BASE_URL

async def _post_image(endpoint: str, image_path: str, prompt: str) -> str | None:
    url = f"{EXECUTOR_BASE_URL.rstrip('/')}{endpoint}"
    try:
        # Читаем в память и закрываем файл сразу (на Windows это критично)
        with open(image_path, "rb") as f:
            file_bytes = f.read()

        form = aiohttp.FormData()
        form.add_field(
            "image",
            file_bytes,  # <-- bytes вместо открытого файла
            filename=os.path.basename(image_path),
            content_type="image/png",
        )
        form.add_field("prompt", prompt)

        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=form, timeout=600) as resp:
                if resp.status == 200:
                    js = await resp.json()
                    return js.get("url")
                else:
                    txt = await resp.text()
                    print(f"Executor error {resp.status}: {txt}")
                    return None
    except Exception as e:
        print(f"HTTP client error: {e}")
        return None

async def generate_design(image_path: str, prompt: str) -> str | None:
    return await _post_image("/api/v1/design/generate", image_path, prompt)

async def generate_floor_plan(*, floor_plan_path: str, visualization_style: str, interior_style: str) -> str:
    """
    Отправляет изображение планировки и параметры визуализации на executor.
    Промпт строится на стороне executor/apps/plan_generate.py.
    Возвращает URL сгенерированного изображения или пустую строку.
    """
    import os
    from aiohttp import FormData, ClientSession
    
    url = f"{os.getenv('EXECUTOR_BASE_URL', 'http://localhost:8080')}/plan/generate"
    
    form = FormData()
    form.add_field(
        "image",
        open(floor_plan_path, "rb"),
        filename=os.path.basename(floor_plan_path),
        content_type="image/png",
    )
    # Вместо prompt передаём параметры
    if visualization_style:
        form.add_field("visualization_style", visualization_style)
    form.add_field("interior_style", interior_style or "Модерн")
    
    async with ClientSession() as session:
        async with session.post(url, data=form) as resp:
            if resp.status != 200:
                return ""
            data = await resp.json()
            return data.get("url") or ""

async def download_image_from_url(url: str) -> bytes | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
                else:
                    print(f"Download error {resp.status} for {url}")
                    return None
    except Exception as e:
        print(f"Download exception: {e}")
        return None
