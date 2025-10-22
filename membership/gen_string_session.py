#!/usr/bin/env python3
"""
Генератор StringSession для Telethon (user-бота).

Приоритет источников api_id / api_hash:
1) Общий конфиг проекта (пытаемся импортировать):
   - bot.config: settings.TG_API_ID / settings.TG_API_HASH
   - bot.config: TG_API_ID / TG_API_HASH (как модульные константы)
   - config:     settings.TG_API_ID / settings.TG_API_HASH
   - config:     TG_API_ID / TG_API_HASH
2) Переменные окружения / .env: TG_API_ID, TG_API_HASH
3) Интерактивный ввод.

Результат: печатает StringSession (его кладём в .env как TG_SESSION=...).
Запуск из IDE: просто ▶️ (main()).
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Tuple

# 1) аккуратно подхватим .env, если есть
try:
    from dotenv import load_dotenv  # type: ignore
except Exception:
    load_dotenv = None

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
except Exception as e:
    print("❌ Не установлены зависимости. Установите:\n   pip install telethon python-dotenv", file=sys.stderr)
    raise

# ──────────────────────────────────────────────────────────────────────────────
# helpers: поиск общих конфигов
# ──────────────────────────────────────────────────────────────────────────────

def _safe_import(module_name: str):
    try:
        return __import__(module_name, fromlist=["*"])
    except Exception:
        return None


def _read_from_settings_obj(settings: object, *names: str) -> Optional[str]:
    for n in names:
        if hasattr(settings, n):
            v = getattr(settings, n)
            if v is not None and str(v).strip():
                return str(v).strip()
    return None


def _probe_common_config() -> Tuple[Optional[str], Optional[str]]:
    """
    Пытаемся достать api_id / api_hash из общего конфига проекта.
    Поддерживаем распространённые варианты:
      - bot.config (Pydantic settings или модульные константы)
      - config (settings или модульные константы)
    Вернём (api_id, api_hash) как строки (или None/None).
    """
    candidates = [
        "bot.config",
        "config",
    ]

    for mod_name in candidates:
        mod = _safe_import(mod_name)
        if not mod:
            continue

        # 1) settings-объект (pydantic BaseSettings и пр.)
        settings = getattr(mod, "settings", None)
        if settings:
            api_id = _read_from_settings_obj(settings, "TG_API_ID", "API_ID", "TELEGRAM_API_ID", "api_id")
            api_hash = _read_from_settings_obj(settings, "TG_API_HASH", "API_HASH", "TELEGRAM_API_HASH", "api_hash")
            if api_id or api_hash:
                return api_id, api_hash

        # 2) модульные константы
        for a_name in ("TG_API_ID", "API_ID", "TELEGRAM_API_ID", "api_id"):
            if hasattr(mod, a_name):
                api_id = str(getattr(mod, a_name)).strip()
                # подберём hash
                for h_name in ("TG_API_HASH", "API_HASH", "TELEGRAM_API_HASH", "api_hash"):
                    if hasattr(mod, h_name):
                        api_hash = str(getattr(mod, h_name)).strip()
                        return api_id, api_hash

    return None, None


def _load_env_defaults() -> Tuple[Optional[str], Optional[str]]:
    if load_dotenv:
        load_dotenv()  # не мешает, просто подхватит .env при наличии
    api_id = os.getenv("TG_API_ID", "").strip() or None
    api_hash = os.getenv("TG_API_HASH", "").strip() or None
    return api_id, api_hash


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


def _resolve_api_creds() -> Tuple[int, str]:
    # 1) общий конфиг
    api_id_from_cfg, api_hash_from_cfg = _probe_common_config()

    # 2) .env / окружение
    api_id_from_env, api_hash_from_env = _load_env_defaults()

    # выберем приоритет: общий конфиг > окружение
    api_id_str = api_id_from_cfg or api_id_from_env or ""
    api_hash = api_hash_from_cfg or api_hash_from_env or ""

    # 3) спросим интерактивно, если нужно
    api_id_str = _ask("api_id (my.telegram.org)", api_id_str or None)
    if not api_id_str.isdigit():
        print("Ошибка: api_id должен быть целым числом.", file=sys.stderr)
        raise SystemExit(1)

    api_id = int(api_id_str)
    api_hash = _ask("api_hash", api_hash or None)
    if not api_hash:
        print("Ошибка: api_hash обязателен.", file=sys.stderr)
        raise SystemExit(1)

    return api_id, api_hash


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    print("=== Генератор StringSession (Telethon) — запуск из IDE готов ===")

    api_id, api_hash = _resolve_api_creds()

    print("\nЛогинимся. Скрипт спросит телефон, код из Telegram и (если включено) пароль 2FA.\n")

    # используем временную строковую сессию; после логина сохраним строку
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        client.start()  # интерактивно: телефон → код → 2FA
        session_str = client.session.save()

        print("\n=== ВАШ StringSession ===")
        print(session_str)
        print("=========================\n")

        print("Подсказка для .env:\n")
        print(f"TG_API_ID={api_id}")
        print(f"TG_API_HASH={api_hash}")
        print(f"TG_SESSION={session_str}\n")

        print("⚠️  Держите строку в секрете. При утечке — выйдите из всех устройств в Telegram.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
