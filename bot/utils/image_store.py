from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime

# Базовая директория для картинок: ~/super_bot/images
_BASE_DIR = Path.home() / "super_bot" / "images"
_HEALTHCHECK_NAME = "healthcheck.png"

# 1x1 PNG — минимальный валидный PNG, чтобы проверить право записи
_ONE_BY_ONE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0cIDATx\x9cc```"
    b"\x00\x00\x00\x04\x00\x01\x0b\xe7\x02\x9d\x00\x00\x00\x00IEND\xaeB`\x82"
)

def images_dir() -> Path:
    return _BASE_DIR

def init_image_store() -> None:
    """
    Создаёт директорию и проверяет «записью картинки».
    Вызывается на старте (импортирующим модулем).
    """
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_BASE_DIR / _HEALTHCHECK_NAME, "wb") as f:
        f.write(_ONE_BY_ONE_PNG)
    with open(_BASE_DIR / "healthcheck.txt", "w", encoding="utf-8") as f:
        f.write(f"init ok at {datetime.utcnow().isoformat()}Z\n")

def build_image_path_for_msg_id(msg_id: int, *, ext: str = "png") -> Path:
    ext = ext.lstrip(".")
    return _BASE_DIR / f"{int(msg_id)}.{ext}"

def save_bytes_as_png(data: bytes, msg_id: int) -> Path:
    """
    Сохраняет байты как PNG с именем <msg_id>.png.
    """
    p = build_image_path_for_msg_id(msg_id, ext="png")
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p

def save_bytes_with_ext(data: bytes, msg_id: int, *, ext: str) -> Path:
    p = build_image_path_for_msg_id(msg_id, ext=ext)
    _BASE_DIR.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p

def rename_for_new_msg_id(old_path: Path, new_msg_id: int) -> Path:
    """
    Фолбэк: если edit не удался и пришлось отправить НОВОЕ сообщение (другой msg_id),
    переименовываем файл под актуальный msg_id.
    """
    suffix = old_path.suffix or ".png"
    new_p = build_image_path_for_msg_id(new_msg_id, ext=suffix.lstrip("."))
    try:
        os.replace(old_path, new_p)
    except FileNotFoundError:
        new_p.parent.mkdir(parents=True, exist_ok=True)
    return new_p