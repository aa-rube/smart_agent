import time
import asyncio
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl import functions, types

from config import settings


# ──────────────────────────────────────────────────────────────────────────────
# Конфиг
# ──────────────────────────────────────────────────────────────────────────────

# Bot API
BOT_API = f"https://api.telegram.org/bot{settings.BOT_TOKEN}"


# ──────────────────────────────────────────────────────────────────────────────
# FastAPI схемы
# ──────────────────────────────────────────────────────────────────────────────

class InviteRequest(BaseModel):
    user_id: int = Field(..., description="Telegram user_id")
    username: Optional[str] = Field(None, description="@username без @ тоже ок")
    full_name: Optional[str] = Field(None, description="ФИО/имя, если есть")
    invite_ttl_hours: int = Field(
        settings.INVITE_TTL_HOURS_DEFAULT,
        description=f"Срок жизни инвайта, часов (по умолчанию {settings.INVITE_TTL_HOURS_DEFAULT})",
    )


class RemoveRequest(BaseModel):
    user_id: int = Field(..., description="Telegram user_id")


# ──────────────────────────────────────────────────────────────────────────────
# Инициализация Telethon
# ──────────────────────────────────────────────────────────────────────────────

client: TelegramClient

def _make_client() -> TelegramClient:
    # Только StringSession из ENV. Без него запуск под systemd невозможен.
    return TelegramClient(StringSession(settings.SESSION), settings.API_ID, settings.API_HASH)

client = _make_client()
app = FastAPI(title="Membership Service (Telethon + Bot API)")

# ──────────────────────────────────────────────────────────────────────────────
# Утилиты Bot API
# ──────────────────────────────────────────────────────────────────────────────

async def bot_send_message(chat_id: int, text: str, reply_markup: Optional[dict] = None) -> bool:
    """
    Отправка через Bot API. Возвращает True, если ok==True.
    """
    if not settings.BOT_TOKEN:
        # Защитимся от случайного запуска без токена
        raise RuntimeError("BOT_TOKEN is not configured")

    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup

    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(f"{BOT_API}/sendMessage", json=payload)
        try:
            data = r.json()
        except Exception:
            return False
        return bool(data.get("ok"))


async def bot_send_invite_dm(user_id: int, invite_url: str) -> bool:
    kb = {"inline_keyboard": [[{"text": "Войти в чат", "url": invite_url}]]}
    text = (
        "👋 Привет! Тебя пригласили в закрытый чат.\n"
        "Нажми кнопку ниже, чтобы присоединиться."
    )
    return await bot_send_message(user_id, text, reply_markup=kb)


async def bot_notify_admin_incident(user_id: int, username: Optional[str], full_name: Optional[str]) -> None:
    u = username or "—"
    f = full_name or "—"
    txt = (
        "🚨 Инцидент приглашения в чат\n"
        f"• user_id: <code>{user_id}</code>\n"
        f"• username: {u}\n"
        f"• full name: {f}\n\n"
        "Боту не удалось отправить личное сообщение пользователю. "
        "Вероятно, пользователь не писал боту или ограничил ЛС."
    )
    # HTML парсинг тут не обязателен, можно plain text, но оставим так
    payload = {"chat_id": settings.ADMIN_ID, "text": txt, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15) as http:
        await http.post(f"{BOT_API}/sendMessage", json=payload)


# ──────────────────────────────────────────────────────────────────────────────
# Утилиты Telethon
# ──────────────────────────────────────────────────────────────────────────────

async def _get_entity_chat():
    return await client.get_entity(settings.TARGET_CHAT_ID)


async def _get_user_entity(user_id: int):
    return await client.get_entity(user_id)


async def try_direct_invite(user_id: int) -> bool:
    """
    Пробуем добавить напрямую (как админ-пользователь).
    Для мегагруппы: channels.InviteToChannel.
    """
    try:
        chat = await _get_entity_chat()
        user = await _get_user_entity(user_id)
        await client(functions.channels.InviteToChannelRequest(channel=chat, users=[user]))
        return True
    except (
        errors.UserPrivacyRestrictedError,
        errors.UserNotMutualContactError,
        errors.UserChannelsTooMuchError,
        errors.ChatAdminRequiredError,
        errors.PeerFloodError,
        errors.UserAlreadyParticipantError,
        errors.FloodWaitError,
        errors.ChatWriteForbiddenError,
        errors.RPCError,
    ):
        # Любая из этих ошибок означает, что мы не смогли напрямую добавить — переходим к инвайту
        return False


async def create_single_use_invite(ttl_hours: int) -> str:
    """
    Создаёт одноразовую ссылку с ограничением по использованию (1) и TTL (часов).
    """
    chat = await _get_entity_chat()
    expire_date = int(time.time() + max(60, ttl_hours * 3600))
    exported = await client(functions.messages.ExportChatInviteRequest(
        peer=chat,
        expire_date=expire_date,
        usage_limit=1,
        request_needed=False,
        title=None
    ))
    # Ответ — ExportedChatInvite || ExportedChatInviteReplaced; берём актуальный инвайт
    if isinstance(exported, types.messages.ExportedChatInvite):
        if exported.new_invite:  # replaced-кейс
            return exported.new_invite.link
        if exported.invite:
            return exported.invite.link
    if isinstance(exported, types.ExportedChatInvite):
        return exported.link
    raise RuntimeError("Не удалось получить ссылку-приглашение")


async def kick_then_unban(user_id: int) -> bool:
    """
    «Полное удаление»: кик → мгновенный анбан.
    После этого пользователя можно снова звать без проблем.
    """
    try:
        chat = await _get_entity_chat()
        user = await _get_user_entity(user_id)

        # Баним (kick)
        rights_ban = types.ChatBannedRights(
            until_date=None,  # бессрочно
            view_messages=True,  # запрет просматривать = исключение
        )
        await client(functions.channels.EditBannedRequest(channel=chat, participant=user, banned_rights=rights_ban))

        # Небольшая пауза и снимаем бан
        await asyncio.sleep(0.2)
        rights_unban = types.ChatBannedRights(
            until_date=0,  # явный unban
            view_messages=False,
        )
        await client(functions.channels.EditBannedRequest(channel=chat, participant=user, banned_rights=rights_unban))
        return True
    except errors.RPCError as e:
        # Сообщим админу, но не завалим весь запрос
        try:
            await bot_send_message(settings.ADMIN_ID, f"⚠️ Не удалось удалить пользователя {user_id} из чата. Ошибка: {e}")
        except Exception:
            pass
        return False


# ──────────────────────────────────────────────────────────────────────────────
# Контроллеры
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/members/invite")
async def invite_member(req: InviteRequest):
    # 1) Пробуем прямое добавление
    added = await try_direct_invite(req.user_id)
    if added:
        return {"status": "added"}

    # 2) Создаём одноразовую ссылку
    try:
        invite_url = await create_single_use_invite(req.invite_ttl_hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось создать пригласительную ссылку: {e}")

    # 3) Пытаемся отправить её пользователю через Bot API
    dm_ok = await bot_send_invite_dm(req.user_id, invite_url)
    if dm_ok:
        return {"status": "invited_link_sent"}

    # 4) Если не смогли отправить личку — уведомляем админа
    await bot_notify_admin_incident(req.user_id, req.username, req.full_name)
    return {"status": "incident_reported_to_admin"}


@app.post("/members/remove")
async def remove_member(req: RemoveRequest):
    ok = await kick_then_unban(req.user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Не удалось удалить пользователя (бан/анбан)")
    return {"status": "removed"}


# ──────────────────────────────────────────────────────────────────────────────
# Запуск
# ──────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _on_start():
    # Стартуем сессию Telethon. Если TG_SESSION битый/пустой — свалимся с понятной ошибкой.
    await client.start()

if __name__ == "__main__":
    import uvicorn
    # Запуск контроллера на 0.0.0.0:6000
    uvicorn.run(app, host="0.0.0.0", port=6000)

