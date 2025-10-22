#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\membership\membership_service.py
import asyncio
import logging
from typing import Optional, Any, Dict
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from telethon.tl import functions, types

from config import settings
from telethon.tl.types import InputPeerChat, InputChannel

logger = logging.getLogger(__name__)


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
    """
    Создаём TelegramClient только из StringSession (длинной строки).
    Если TG_SESSION невалиден/пуст — дальше на старте намеренно упадём.
    """
    return TelegramClient(StringSession(settings.SESSION), settings.API_ID, settings.API_HASH)


client = _make_client()


# ──────────────────────────────────────────────────────────────────────────────
# Lifespan (startup/shutdown)
# ──────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_: FastAPI):
    # Безинтерактивный старт: подключаемся и убеждаемся, что сессия авторизована.
    await client.connect()
    try:
        authorized = client.is_user_authorized()
        if authorized:
            # Дополнительная проверка доступа к целевому чату
            chat_accessible = await _ensure_chat_access()
            if not chat_accessible:
                raise RuntimeError("Нет доступа к целевому чату. Проверьте что бот добавлен в чат")
    except Exception as e:
        logger.exception("Telethon is_user_authorized() failed: %s", e)
        raise
    if not authorized:
        raise RuntimeError(
            "Telethon session is not authorized. "
            "Проверь TG_SESSION (длинная строка), она должна быть сгенерирована "
            "для текущих TG_API_ID/TG_API_HASH."
        )
    yield
    # Ничего особого при завершении
    try:
        await client.disconnect()
    except Exception:
        pass


app = FastAPI(
    title="Membership Service (Telethon + Bot API)",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """
    Проверка здоровья сервиса и доступа к чату.
    """
    try:
        chat_info = await debug_chat()
        return {
            "status": "healthy",
            "chat_accessible": True,
            "chat_info": chat_info
        }
    except Exception as e:
        return {
            "status": "unhealthy", 
            "chat_accessible": False,
            "error": str(e)
        }


@app.get("/debug/chat")
async def debug_chat():
    """
    Быстрый способ понять, что именно резолвится:
      - тип (chat/channel)
      - внутренние id/access_hash
      - заголовок/username (если есть)
    """
    ent = await _get_entity_chat()
    kind, inp = await _get_input_chat()
    out = {
        "kind": kind,
        "entity_type": type(ent).__name__,
        "input_type": type(inp).__name__,
        "entity": {},
    }
    if isinstance(ent, types.Channel):
        out["entity"] = {"id": ent.id, "access_hash": getattr(ent, "access_hash", None),
                         "title": ent.title, "username": ent.username, "megagroup": ent.megagroup}
    elif isinstance(ent, types.Chat):
        out["entity"] = {"id": ent.id, "title": ent.title}
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Утилиты Bot API
# ──────────────────────────────────────────────────────────────────────────────

async def bot_send_message(
    chat_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Отправка через Bot API. Возвращает True, если ok==True.
    """
    if not settings.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not configured")

    # Важно: payload типизирован шире, чтобы положить reply_markup (dict)
    payload: Dict[str, Any] = {"chat_id": chat_id, "text": text}
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
    payload = {"chat_id": settings.ADMIN_ID, "text": txt, "parse_mode": "HTML"}
    async with httpx.AsyncClient(timeout=15) as http:
        await http.post(f"{BOT_API}/sendMessage", json=payload)


# ──────────────────────────────────────────────────────────────────────────────
# Утилиты Telethon
# ──────────────────────────────────────────────────────────────────────────────

async def _get_entity_chat():
    """
    Возвращает high-level entity (Chat|Channel) по TARGET_CHAT_ID.
    Требует, чтобы аккаунт из TG_SESSION состоял в группе/канале.
    """
    try:
        return await client.get_entity(settings.TARGET_CHAT_ID)
    except (ValueError, errors.ChannelInvalidError) as e:
        logger.error(f"Не удалось получить entity чата {settings.TARGET_CHAT_ID}: {e}")
        raise RuntimeError(f"Бот не имеет доступа к чату {settings.TARGET_CHAT_ID} или чат не существует")


async def _get_user_entity(user_id: int):
    return await client.get_entity(user_id)


async def _get_input_peer_for_chat():
    """
    InputPeer* для таргет-чата (InputPeerChannel или InputPeerChat).
    Нужен, например, для messages.ExportChatInviteRequest(peer=...).
    """
    try:
        return await client.get_input_entity(settings.TARGET_CHAT_ID)
    except (ValueError, errors.ChannelInvalidError) as e:
        logger.error(f"Не удалось получить input entity чата {settings.TARGET_CHAT_ID}: {e}")
        raise RuntimeError(f"Бот не имеет доступа к чату {settings.TARGET_CHAT_ID}")


async def _ensure_chat_access():
    """
    Проверяет доступ к целевому чату при старте приложения.
    """
    try:
        entity = await _get_entity_chat()
        logger.info(f"Успешный доступ к чату: {getattr(entity, 'title', 'Unknown')}")
        return True
    except Exception as e:
        logger.error(f"Критическая ошибка доступа к чату: {e}")
        return False


async def _get_input_chat() -> tuple[str, InputPeerChat | InputChannel]:
    """
    Универсально получаем Input-* пира и сразу помечаем тип:
      - ('channel', InputChannel)  — супергруппа/канал
      - ('chat',    InputPeerChat) — обычная группа
    """
    inp = await client.get_input_entity(settings.TARGET_CHAT_ID)
    if isinstance(inp, InputChannel):
        return "channel", inp
    if isinstance(inp, InputPeerChat):
        return "chat", inp
    # Если пришёл не тот тип — попробуем дорезолвить через high-level
    ent = await _get_entity_chat()
    if isinstance(ent, types.Chat):
        return "chat", InputPeerChat(ent.id)
    if isinstance(ent, types.Channel):
        return "channel", InputChannel(ent.id, ent.access_hash)
    raise RuntimeError("TARGET_CHAT_ID не резолвится ни в Chat, ни в Channel")


async def _get_input_user(user_id: int) -> types.InputUser:
    """
    Гарантировано возвращает InputUser (а не InputPeerUser).
    """
    ent = await client.get_entity(user_id)
    if isinstance(ent, types.User):
        return types.InputUser(ent.id, ent.access_hash)
    # На всякий случай fallback
    ipeer = await client.get_input_entity(user_id)
    if isinstance(ipeer, types.InputPeerUser):
        return types.InputUser(ipeer.user_id, ipeer.access_hash)
    raise RuntimeError("Не удалось получить InputUser для user_id=%s" % user_id)


async def try_direct_invite(user_id: int) -> bool:
    """
    Пробуем добавить напрямую (как админ-пользователь).
    Для мегагруппы/канала: channels.InviteToChannel(users=[InputUser])
    Для обычных чатов: messages.AddChatUser(user_id=InputUser)
    """
    try:
        kind, ichat = await _get_input_chat()
        iuser = await _get_input_user(user_id)

        if kind == "channel":  # супергруппа
            await client(functions.channels.InviteToChannelRequest(
                channel=ichat,  # InputChannel
                users=[iuser],  # list[InputUser]
            ))
        else:                  # обычный чат
            await client(functions.messages.AddChatUserRequest(
                chat_id=ichat.chat_id,  # int
                user_id=iuser,          # InputUser
                fwd_limit=0
            ))
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
        ValueError,
    ) as e:
        # Любая из этих ошибок означает, что мы не смогли напрямую добавить — переходим к инвайту
        logger.warning("Direct invite failed: %s", e)
        return False


async def create_single_use_invite(ttl_hours: int) -> str:
    """
    Создаёт одноразовую ссылку с ограничением по использованию (1) и TTL (часов).
    """
    peer = await _get_input_peer_for_chat()  # InputPeerChannel | InputPeerChat
    expire_date = datetime.utcnow() + timedelta(seconds=max(60, ttl_hours * 3600))
    exported = await client(functions.messages.ExportChatInviteRequest(
        peer=peer,
        expire_date=expire_date,
        usage_limit=1,
        request_needed=False,
        title=None
    ))
    # Вариант 1: messages.ExportedChatInvite (обёртка со списками)
    if isinstance(exported, types.messages.ExportedChatInvite):
        if exported.invite and isinstance(exported.invite, types.ChatInviteExported):
            return exported.invite.link
        # иногда ссылка приходит в .link
        if getattr(exported, "link", None):
            return exported.link
    # Вариант 2: простой ExportedChatInvite
    if isinstance(exported, types.ExportedChatInvite):
        return exported.link
    raise RuntimeError("Не удалось получить ссылку-приглашение")


async def kick_then_unban(user_id: int) -> bool:
    """
    «Полное удаление»: для каналов — ban→unban (EditBanned),
    для обычных чатов — DeleteChatUser. После этого пользователя можно снова звать.
    """
    try:
        kind, ichat = await _get_input_chat()
        iuser = await _get_input_user(user_id)

        if kind == "channel":
            # Супергруппа: бан → анбан
            rights_ban = types.ChatBannedRights(
                until_date=None,      # бессрочно
                view_messages=True,   # исключение
            )
            await client(functions.channels.EditBannedRequest(
                channel=ichat, participant=iuser, banned_rights=rights_ban
            ))
            await asyncio.sleep(0.2)
            rights_unban = types.ChatBannedRights(
                until_date=0,         # явный unban
                view_messages=False,
            )
            await client(functions.channels.EditBannedRequest(
                channel=ichat, participant=iuser, banned_rights=rights_unban
            ))
        else:
            # Обычная группа: DeleteChatUser — удаляет без помещения в бан-лист.
            await client(functions.messages.DeleteChatUserRequest(
                chat_id=ichat.chat_id,
                user_id=iuser,
                revoke_history=False,  # историю не трогаем
            ))
        return True
    except errors.RPCError as e:
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
    # Проверяем доступ к чату перед выполнением операций
    try:
        await _ensure_chat_access()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Нет доступа к целевому чату: {e}")
    
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
# Запуск локально
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    # Запуск контроллера на 0.0.0.0:6000
    uvicorn.run(app, host="0.0.0.0", port=6000)
