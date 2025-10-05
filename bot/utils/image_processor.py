# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\utils\image_processor.py
import os
import uuid
import aiohttp
from PIL import Image
from io import BytesIO


async def save_image_as_png(image_bytes: bytes, user_id: int) -> str | None:
    try:
        save_dir = os.path.join("images", "tmp")
        os.makedirs(save_dir, exist_ok=True)

        filename = f"{user_id}_{uuid.uuid4()}.png"
        filepath = os.path.join(save_dir, filename)

        with Image.open(BytesIO(image_bytes)) as img:
            img.convert("RGB").save(filepath, "PNG")
        return filepath
    except Exception as e:
        print(f"Ошибка сохранения файла: {e}")
        return None


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
