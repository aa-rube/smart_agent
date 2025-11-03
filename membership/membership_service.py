#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\membership\membership_service.py
import asyncio
import logging
from typing import Optional, Any, Dict
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from telethon import TelegramClient, errors, events
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


class SendMessageRequest(BaseModel):
    chat_id: int = Field(..., description="ID чата или пользователя")
    text: str = Field(..., description="Текст сообщения")
    parse_mode: Optional[str] = Field(None, description="Режим парсинга (HTML, Markdown)")


class SendToTargetRequest(BaseModel):
    text: str = Field(..., description="Текст сообщения")
    parse_mode: Optional[str] = Field(None, description="Режим парсинга (HTML, Markdown)")


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
        authorized = await client.is_user_authorized()
        if not authorized:
            raise RuntimeError(
                "Telethon session is not authorized. "
                "Проверь TG_SESSION (длинная строка), она должна быть сгенерирована "
                "для текущих TG_API_ID/TG_API_HASH."
            )
            
        # Дополнительная проверка доступа к целевому чату
        chat_accessible = await _ensure_chat_access()
        
        # Запускаем прослушивание сообщений
        await start_message_listener()
        
    except Exception as e:
        logger.exception("Telethon startup failed: %s", e)
        raise
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
    parse_mode: Optional[str] = None,
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
    if parse_mode:
        payload["parse_mode"] = parse_mode

    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.post(f"{BOT_API}/sendMessage", json=payload)
        try:
            data = r.json()
        except Exception:
            return False
        return bool(data.get("ok"))


async def bot_send_invite_dm(user_id: int, invite_url: str) -> bool:
    kb = {"inline_keyboard": [[{"text": "Войти в чат", "url": invite_url}]]}
    text = ('''
Привет! 👋
Мы хотели добавить тебя в закрытый канал с готовым контентом для соцсетей,
но Telegram не разрешил сделать это автоматически 😅

👉 Подключись самостоятельно - (нажми на кнопку) и попади в пространство, где риэлторы уже качают свой личный бренд!
Первые посты уже вышли - не пропусти и начни привлекать клиентов уже  сегодня 💪
'''
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
# Утилиты Telethon для работы с сообщениями
# ──────────────────────────────────────────────────────────────────────────────

async def start_message_listener():
    """
    Запускает прослушивание входящих сообщений и выводит их в консоль.
    """
    @client.on(events.NewMessage)
    async def handler(event):
        try:
            # Получаем информацию о чате/пользователе
            chat = await event.get_chat()
            sender = await event.get_sender()
            
            chat_info = f"'{getattr(chat, 'title', '')}' ({chat.id})" if hasattr(chat, 'title') else f"ID: {chat.id}"
            sender_info = f"@{sender.username}" if sender.username else f"{getattr(sender, 'first_name', '')} {getattr(sender, 'last_name', '')}".strip()
            
            print(f"📨 Новое сообщение:")
            print(f"   Чат: {chat_info}")
            print(f"   От: {sender_info} (ID: {sender.id})")
            print(f"   Текст: {event.text}")
            print(f"   Время: {event.date}")
            print("-" * 50)
        except Exception as e:
            print(f"Ошибка при обработке сообщения: {e}")

    print("🔄 Прослушивание сообщений запущено...")
    # Не нужно запускать client.run() так как мы уже управляем клиентом через lifespan


async def send_telethon_message(chat_id: int, text: str) -> bool:
    """
    Отправляет сообщение через Telethon клиент (user-bot) в указанный чат/пользователю.
    """
    try:
        await client.send_message(chat_id, text)
        logger.info(f"Сообщение отправлено через Telethon в {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения через Telethon в {chat_id}: {e}")
        return False


async def send_message_to_target_chat(text: str, parse_mode: Optional[str] = None) -> bool:
    """
    Отправляет сообщение в целевой чат через Bot API.
    """
    try:
        return await bot_send_message(
            chat_id=settings.TARGET_CHAT_ID,
            text=text,
            parse_mode=parse_mode
        )
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения в целевой чат: {e}")
        return False


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
        logger.warning(f"Бот не имеет доступа к целевому чату {settings.TARGET_CHAT_ID}. Некоторые функции будут недоступны.")
        return None


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
        logger.warning(f"Бот не имеет доступа к целевому чату {settings.TARGET_CHAT_ID}")
        return None


async def _ensure_chat_access():
    """
    Проверяет доступ к целевому чату при старте приложения.
    """
    try:
        entity = await _get_entity_chat()
        if entity:
            logger.info(f"Успешный доступ к чату: {getattr(entity, 'title', 'Unknown')}")
            return True
        else:
            logger.warning("Доступ к целевому чату отсутствует")
            return False
    except Exception as e:
        logger.warning(f"Ошибка доступа к целевому чату: {e}")
        return False


async def _get_input_chat() -> tuple[str, InputPeerChat | InputChannel]:
    """
    Универсально получаем Input-* пира и сразу помечаем тип:
      - ('channel', InputChannel)  — супергруппа/канал
      - ('chat',    InputPeerChat) — обычная группа
    """
    inp = await _get_input_peer_for_chat()
    if inp is None:
        raise RuntimeError(f"Нет доступа к целевому чату {settings.TARGET_CHAT_ID}")
        
    if isinstance(inp, InputChannel):
        return "channel", inp
    if isinstance(inp, InputPeerChat):
        return "chat", inp
    # Если пришёл не тот тип — попробуем дорезолвить через high-level
    ent = await _get_entity_chat()
    if ent is None:
        raise RuntimeError(f"Нет доступа к целевому чату {settings.TARGET_CHAT_ID}")
        
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
    res = await client(functions.messages.ExportChatInviteRequest(
        peer=peer,
        expire_date=expire_date,
        usage_limit=1,
        request_needed=False,
        title=None
    ))
    # Telethon возвращает types.messages.ExportedChatInvite (обёртка),
    # внутри которой .invite = types.ChatInviteExported
    if isinstance(res, types.messages.ExportedChatInvite):
        inv = getattr(res, "invite", None)
        if isinstance(inv, types.ChatInviteExported) and getattr(inv, "link", None):
            return inv.link
        # иногда линк дублируется прямо в обёртке
        link = getattr(res, "link", None)
        if link:
            return link
    # Некоторые версии могут вернуть напрямую ChatInviteExported
    if isinstance(res, types.ChatInviteExported):
        return res.link
    raise RuntimeError(f"Не удалось получить ссылку-приглашение (тип ответа: {type(res).__name__})")


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
    except ValueError as e:
        # Пользователь не найден (удалён аккаунт, заблокировал бота и т.д.)
        logger.warning("Could not find user entity for user_id=%s: %s. User may have deleted account or blocked bot.", user_id, e)
        # Если пользователь не найден, считаем операцию успешной (пользователь уже не в чате)
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
    chat_accessible = await _ensure_chat_access()
    if not chat_accessible:
        raise HTTPException(status_code=500, detail="Нет доступа к целевому чату")
    
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
    # Проверяем доступ к чату перед выполнением операций
    chat_accessible = await _ensure_chat_access()
    if not chat_accessible:
        raise HTTPException(status_code=500, detail="Нет доступа к целевому чату")
    
    ok = await kick_then_unban(req.user_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Не удалось удалить пользователя (бан/анбан)")
    return {"status": "removed"}


# ──────────────────────────────────────────────────────────────────────────────
# Контроллеры для работы с сообщениями
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/message/send")
async def send_message(req: SendMessageRequest):
    """
    Отправляет сообщение в любой чат или пользователю через Bot API.
    """
    success = await bot_send_message(
        chat_id=req.chat_id,
        text=req.text,
        parse_mode=req.parse_mode
    )
    
    if success:
        return {"status": "message_sent", "chat_id": req.chat_id}
    else:
        raise HTTPException(
            status_code=500, 
            detail=f"Не удалось отправить сообщение в {req.chat_id}"
        )


@app.post("/message/send_target")
async def send_message_to_target(req: SendToTargetRequest):
    """
    Отправляет сообщение в целевой чат через Bot API.
    """
    # Для Bot API не нужен доступ через Telethon, только правильный chat_id и права бота
    success = await send_message_to_target_chat(
        text=req.text,
        parse_mode=req.parse_mode
    )
    
    if success:
        return {"status": "message_sent", "chat_id": settings.TARGET_CHAT_ID}
    else:
        raise HTTPException(
            status_code=500, 
            detail=f"Не удалось отправить сообщение в целевой чат {settings.TARGET_CHAT_ID}. Проверьте что бот добавлен в чат и имеет права на отправку сообщений."
        )


@app.post("/message/send_telethon")
async def send_message_telethon(req: SendMessageRequest):
    """
    Отправляет сообщение через Telethon клиент (user-bot).
    Полезно когда нужно отправить от имени user-бота.
    """
    success = await send_telethon_message(
        chat_id=req.chat_id,
        text=req.text
    )
    
    if success:
        return {"status": "message_sent_telethon", "chat_id": req.chat_id}
    else:
        raise HTTPException(
            status_code=500, 
            detail=f"Не удалось отправить сообщение через Telethon в {req.chat_id}"
        )


@app.get("/debug/session")
async def debug_session():
    """
    Информация о текущей Telethon сессии.
    """
    try:
        me = await client.get_me()
        return {
            "session_authorized": True,
            "user": {
                "id": me.id,
                "username": me.username,
                "first_name": me.first_name,
                "last_name": me.last_name,
                "phone": me.phone
            }
        }
    except Exception as e:
        return {
            "session_authorized": False,
            "error": str(e)
        }


# ──────────────────────────────────────────────────────────────────────────────
# Запуск локально
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=6000)
