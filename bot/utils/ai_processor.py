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

async def generate_floor_plan(floor_plan_path: str, prompt: str) -> str | None:
    return await _post_image("/api/v1/plan/generate", floor_plan_path, prompt)

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
