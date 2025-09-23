# smart_agent/bot/handlers/admin.py
#Всегда пиши код без «поддержки старых версий». Если они есть в еодк - удаляй.
# секрет офигенного бота: тебе не нужен якорь.
# Пользуйся такой схемой:
# -если callback -> обновляем сообщение, msg_id берем из update
# -если обычный text_message, command -> отправляй новое сообщение.
# Используй fallback если изменить не удалось.
# Все, никаких anchors которые нужно настраивать, никаких залипаний, кучи сообщение и мисс-кликов.

from __future__ import annotations

from datetime import datetime, timedelta
import asyncio
from typing import List, Dict, Any

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    ChatInviteLink,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputMediaPhoto,
    InputMediaVideo,
)

import bot.config as cfg
import bot.utils.admin_db as adb
from bot.states.states import CreateMailing  # оставляем только рассылку
from bot.handlers.calendar_picker import open_calendar, router as calendar_router  # КАЛЕНДАРЬ


# =============================================================================
# UX тексты
# =============================================================================
ADMIN_MENU_TEXT = (
    "<b>Рассылка контента по подписке.</b>\n\n"
    "Выберите действие кнопкой ниже."
)
NO_ACCESS_TEXT = "У вас нет доступа к админ панели."

ASK_MAILING_CONTENT = (
    "Отправьте новое сообщение для рассылки.\n\n"
    "Поддерживается: текст, фото, <u>альбом (фото/видео)</u>, видео, аудио, GIF/анимация.\n"
    "Если отправляете <b>альбом</b>, загрузите все медиа одним пакетом (Telegram пометит их общим group_media_id). "
    "Я дождусь весь пакет и оформлю один черновик автоматически. Ничего дополнительно нажимать не нужно. "
    "Подпись (caption) возьмём с первого медиа с подписью."
)

ASK_MAILING_DATETIME = (
    "Укажите дату и время публикации (локальное время сервера) в одном из форматов:\n"
    "• <code>YYYY-MM-DD HH:MM</code>\n"
    "• <code>DD.MM.YYYY HH:MM</code>\n"
    "Пример: <code>2025-09-20 10:30</code> или <code>20.09.2025 10:30</code>\n\n"
    "🗓 По умолчанию: <b>{default_dt}</b>"
)

CONFIRM_MAILING_TEXT_TPL = (
    "Готово. Запланировать рассылку на: <b>{dt}</b>?\n"
    "Тип: <code>{ctype}</code>\n"
    "{extra}"
)
MAIL_SCHEDULED_OK = "Рассылка поставлена в планировщик."
MAILING_DONE = "Рассылка завершена!"
SUCCESS_PAYMENT_TPL = (
    "Оплата прошла успешно!\n"
    "Сумма: {amount:.2f} {currency}\n"
    "Тариф: {months} месяц(ев)"
)
PERSONAL_INVITE_TPL = "Ваша персональная ссылка для вступления:\n{}"
INVITE_ERROR_TPL = "Ошибка при создании ссылки: {}"
POSTS_HEADER = "Ниже посты этого месяца ↓"
SUB_EXPIRED_MSG = (
    "Ваша подписка истекла. Чтобы восстановить доступ, оформите новую подписку."
)


# =============================================================================
# Клавиатуры
# =============================================================================
def kb_admin_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📣 Новая рассылка", callback_data="admin.mailing")],
            [InlineKeyboardButton(text="🗂 Запланированные", callback_data="admin.mailing.list")],
        ]
    )


def kb_back_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ В админ-меню", callback_data="admin.home")]]
    )


def kb_use_default_dt(default_dt: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🗓 Использовать {default_dt}", callback_data="admin.mailing.use_default")],
            [InlineKeyboardButton(text="⬅️ В админ-меню", callback_data="admin.home")],
        ]
    )


BTN_MAILING_CONFIRM = InlineKeyboardMarkup(
    inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Запланировать", callback_data="go_mailing"),
            InlineKeyboardButton(text="✏️ Изменить", callback_data="stop_mailing"),
        ],
        [InlineKeyboardButton(text="⬅️ В админ-меню", callback_data="admin.home")],
    ]
)

# Альбом теперь собираем автоматически — кнопки «Далее» не нужны.


def kb_mailing_item_controls(mailing_id: int, origin: str = "list") -> InlineKeyboardMarkup:
    """
    origin:
      - "list"   -> назад в список рассылок
      - "create" -> назад на экран создания новой записи (отправка контента)
    """
    back_cb = "admin.mailing.list" if origin != "create" else "admin.mailing"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👁 Показать", callback_data=f"admin.mailing.show:{mailing_id}")],
            [InlineKeyboardButton(text="🗓 Изменить дату/время", callback_data=f"admin.mailing.edit_dt:{mailing_id}")],
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"admin.mailing.text:{mailing_id}")],
            [InlineKeyboardButton(text="🖼 Изменить контент", callback_data=f"admin.mailing.content:{mailing_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin.mailing.delete:{mailing_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_cb)],
        ]
    )


def kb_text_edit_prefilled(prefill: str) -> InlineKeyboardMarkup:
    """
    Клавиатура редактирования текста, где «Редактировать» подгружает текст
    в поле ввода через switch_inline_query_current_chat.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Редактировать", switch_inline_query_current_chat=prefill or "")],
            [InlineKeyboardButton(text="💾 Сохранить", callback_data="admin.mailing.text.save")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin.mailing.text.back")],
        ]
    )


def kb_content_edit_open(mailing_id: int, keep_origin: bool = False) -> InlineKeyboardMarkup:
    """
    Меню при открытии редактирования контента:
    • Удалить контент
    • Назад (без изменений)
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить контент", callback_data=f"admin.mailing.content.del:{mailing_id}")],
            [InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=(f"admin.mailing.open.keep:{mailing_id}" if keep_origin else f"admin.mailing.open:{mailing_id}")
            )],
        ]
    )

BTN_CONTENT_SAVE_BACK = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="💾 Сохранить", callback_data="admin.mailing.content.save")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin.mailing.content.back")],
    ]
)


# =============================================================================
# Вспомогательные
# =============================================================================
def _parse_dt(s: str) -> datetime | None:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    return None


def _extract_single_content(msg: Message) -> Dict[str, Any] | None:
    """
    Возвращает структуру:
    {
      "content_type": "...",
      "caption": str|None,
      "payload": dict   # для single: {"file_id": "..."} / {"text": "..."}
    }
    """
    if msg.photo:
        return {
            "content_type": "photo",
            "caption": msg.caption,
            "payload": {"file_id": msg.photo[-1].file_id},
        }
    if msg.video:
        return {
            "content_type": "video",
            "caption": msg.caption,
            "payload": {"file_id": msg.video.file_id},
        }
    if msg.audio:
        return {
            "content_type": "audio",
            "caption": msg.caption,
            "payload": {"file_id": msg.audio.file_id},
        }
    if msg.animation:  # GIF
        return {
            "content_type": "animation",
            "caption": msg.caption,
            "payload": {"file_id": msg.animation.file_id},
        }
    if msg.text and msg.text.strip():
        return {
            "content_type": "text",
            "caption": None,
            "payload": {"text": msg.text},
        }
    return None


def _chunk(lst: List[Any], n: int) -> List[List[Any]]:
    return [lst[i:i + n] for i in range(0, len(lst), n)]


def _clean_leading_at(text: str) -> str:
    """
    Если текст начинается с '@...' — обрезаем от '@' до первого пробела включительно.
    """
    if not text:
        return text
    if text.startswith("@"):
        sp = text.find(" ")
        if sp != -1:
            return text[sp + 1 :].lstrip()
        else:
            return ""
    return text


# =========================
# АЛЬБОМ: дебаунс-сборка
# =========================
ALBUM_DEBOUNCE_SEC = 1.2  # время ожидания «хвоста» альбома от Telegram
_album_tasks: dict[int, asyncio.Task] = {}

def _cancel_album_task(chat_id: int) -> None:
    t = _album_tasks.pop(chat_id, None)
    if t:
        t.cancel()

def _schedule_album_task(chat_id: int, task: asyncio.Task) -> None:
    _cancel_album_task(chat_id)
    _album_tasks[chat_id] = task

async def _finalize_album_create(message: Message, state: FSMContext) -> None:
    """Создание новой записи после того, как все части альбома получены."""
    data = await state.get_data()
    items = data.get("album_items") or []
    if not items:
        return
    # дефолтная дата — как в обычном потоке
    last = adb.get_last_publish_at()
    if last:
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(last, fmt)
                break
            except Exception:
                pass
        def_dt = parsed or datetime.now()
    else:
        def_dt = datetime.now()
    def_dt = def_dt + timedelta(days=1)
    publish_at_iso = def_dt.isoformat(timespec="minutes")
    caption = data.get("caption")

    mailing_id = adb.create_scheduled_mailing(
        content_type="media_group",
        caption=caption,
        payload={"items": items},
        publish_at=publish_at_iso,
        mailing_on=True,
    )
    await state.clear()
    await state.update_data(view_mailing_id=mailing_id, view_origin="create")
    # Отрисуем один раз карточку без промежуточных служебных сообщений
    await _render_mailing_item(message, mailing_id, origin="create")

async def _finalize_album_edit(message: Message, state: FSMContext) -> None:
    """Обновление контента существующей записи (редактирование) после сборки альбома."""
    data = await state.get_data()
    items = data.get("album_items") or []
    if not items:
        return
    caption = data.get("caption")
    mid = int(data.get("edit_mailing_id"))
    adb.update_mailing_payload(
        mailing_id=mid,
        content_type="media_group",
        payload={"items": items},
        caption=caption,
    )
    await state.update_data(step=None, album_gid=None, album_items=[], caption=None, new_content=None, view_mailing_id=mid)
    origin = (await state.get_data()).get("view_origin", "list")
    await _render_mailing_item(message, mid, origin=origin)


async def _preview_mailing_to_chat(m: Dict[str, Any], chat_id: int, bot: Bot):
    ctype = m["content_type"]
    caption = m.get("caption")
    payload = m.get("payload") or {}
    if ctype == "text":
        await bot.send_message(chat_id, payload.get("text", ""))
    elif ctype == "photo":
        await bot.send_photo(chat_id, payload["file_id"], caption=caption or None)
    elif ctype == "video":
        await bot.send_video(chat_id, payload["file_id"], caption=caption or None)
    elif ctype == "audio":
        await bot.send_audio(chat_id, payload["file_id"], caption=caption or None)
    elif ctype == "animation":
        await bot.send_animation(chat_id, payload["file_id"], caption=caption or None)
    elif ctype == "media_group":
        # Новая схема: payload.items = [{"type":"photo|video","file_id":"..."}]
        items = payload.get("items")
        if not items:
            # back-compat: старая схема file_ids = [..] → трактуем как фото
            file_ids: List[str] = payload.get("file_ids", [])
            items = [{"type": "photo", "file_id": fid} for fid in file_ids]
        for chunk in _chunk(items, 10):
            media = []
            for i, it in enumerate(chunk):
                t = (it.get("type") or "photo").lower()
                fid = it.get("file_id")
                cap = caption if (i == 0 and caption) else None
                if t == "video":
                    media.append(InputMediaVideo(media=fid, caption=cap))
                else:
                    media.append(InputMediaPhoto(media=fid, caption=cap))
            await bot.send_media_group(chat_id, media)

async def _edit_or_send(msg: Message, *, text: str, kb: InlineKeyboardMarkup | None = None, parse_mode: str | None = "HTML") -> None:
    """
    Для callback: пытаемся отредактировать текущее сообщение.
    Если не вышло — отправляем новое.
    Для обычных сообщений используйте msg.answer() напрямую.
    """
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        low = str(e).lower()
        # если текст не изменился — попробуем хотя бы клавиатуру
        if "message is not modified" in low:
            try:
                await msg.edit_reply_markup(reply_markup=kb)
                return
            except TelegramBadRequest:
                pass
        # если редактировать нельзя — шлём новое
        await msg.answer(text, reply_markup=kb, parse_mode=parse_mode)

async def _render_mailing_item(message: Message, mailing_id: int, origin: str = "list") -> None:
    """
    Единая отрисовка карточки рассылки (чтобы возвращаться в то же место после любых правок).
    """
    m = adb.get_mailing_by_id(mailing_id)
    if not m:
        await message.answer("Запись не найдена.", reply_markup=kb_back_admin())
        return
    dt = m["publish_at"].replace("T", " ")
    ctype = m["content_type"]
    cap = m.get("caption") or "—"
    if ctype == "text":
        extra = f"Текст: {(m.get('payload', {}) or {}).get('text','')[:160]}"
    elif ctype == "media_group":
        pl = (m.get("payload") or {})
        items = pl.get("items")
        if items:
            photos = sum(1 for it in items if (it.get("type") or "photo").lower() == "photo")
            videos = sum(1 for it in items if (it.get("type") or "photo").lower() == "video")
            extra = f"Альбом • фото: {photos} • видео: {videos} • caption: {cap}"
        else:
            # back-compat
            extra = f"Медиа в альбоме: {len(pl.get('file_ids', []))} • caption: {cap}"
    else:
        extra = f"Caption: {cap}"
    await _edit_or_send(
        message,
        text=f"<b>ID:</b> {mailing_id}\n<b>Когда:</b> {dt}\n<b>Тип:</b> <code>{ctype}</code>\n{extra}",
        kb=kb_mailing_item_controls(mailing_id, origin=origin),
        parse_mode="HTML",
    )


# =============================================================================
# ХЕНДЛЕРЫ МЕНЮ
# =============================================================================
async def admin_menu(message: Message):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return
    await message.answer(ADMIN_MENU_TEXT, reply_markup=kb_admin_home(), parse_mode="HTML")


async def admin_home(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await _edit_or_send(callback.message, text=ADMIN_MENU_TEXT, kb=kb_admin_home(), parse_mode="HTML")
    await callback.answer()


# =============================================================================
# РАССЫЛКА (создание и планирование)
# =============================================================================
async def start_mailing(callback: CallbackQuery, state: FSMContext):
    if callback.message.chat.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.update_data(step="await_content", album_gid=None, album_items=[], caption=None)
    await _edit_or_send(callback.message, text=ASK_MAILING_CONTENT, kb=kb_back_admin(), parse_mode="HTML")
    await state.set_state(CreateMailing.GetText)
    await callback.answer()


async def mailing_stop(callback: CallbackQuery, state: FSMContext):
    # Сброс сценария и возврат к вводу контента
    if callback.message.chat.id == cfg.ADMIN_ID:
        await state.clear()
        await state.update_data(step="await_content", album_gid=None, album_items=[], caption=None)
        await _edit_or_send(callback.message, text=ASK_MAILING_CONTENT, kb=kb_back_admin(), parse_mode="HTML")
        await state.set_state(CreateMailing.GetText)
    await callback.answer()


# Кнопка album_done больше не используется (сборка идёт автоматически).


async def mailing_accept(message: Message, state: FSMContext):
    if message.from_user.id != cfg.ADMIN_ID:
        await message.answer(NO_ACCESS_TEXT)
        return

    data = await state.get_data()
    step = data.get("step")

    # Редактирование: дата/время
    if step == "edit_datetime":
        dt = _parse_dt(message.text or "")
        if not dt:
            await message.answer(
                "Не удалось распознать дату/время. Формат: YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM.",
                reply_markup=kb_back_admin(),
            )
            return
        mid = int(data.get("edit_mailing_id"))
        adb.update_mailing_publish_at(mid, dt.isoformat(timespec="minutes"))
        await message.answer("Дата/время обновлены.")
        data2 = await state.get_data()
        origin = (data2 or {}).get("view_origin", "list")
        await _render_mailing_item(message, mid, origin=origin)
        await state.update_data(step=None)
        return

    # Редактирование: ввод нового текста (в буфер)
    if step == "edit_text_wait":
        txt = (message.text or "").strip()
        await state.update_data(edit_text_buffer=txt)
        await message.answer(
            "Текст обновлён в черновике. Нажмите «Сохранить», чтобы применить.",
            reply_markup=kb_text_edit_prefilled(txt),
        )
        return

    # Редактирование: замена контента
    if step == "edit_content_wait":
        # Альбом для редактирования? Копим фото/видео, НИЧЕГО не отвечаем на каждую часть,
        # и по таймауту одного пакета обновляем запись один раз.
        if message.media_group_id:
            gid = message.media_group_id
            st_gid = data.get("album_gid")
            items: List[Dict[str, str]] = data.get("album_items") or []
            caption = data.get("caption")
            if message.photo:
                fid = message.photo[-1].file_id
                items.append({"type": "photo", "file_id": fid})
                if (message.caption or "") and not caption:
                    caption = message.caption
            elif message.video:
                fid = message.video.file_id
                items.append({"type": "video", "file_id": fid})
                if (message.caption or "") and not caption:
                    caption = message.caption
            else:
                # игнорируем неподдерживаемые элементы в медиагруппе
                return
            if st_gid is None:
                await state.update_data(album_gid=gid)
            elif st_gid != gid:
                # Поступил другой альбом — перезапускаем набор с новой группой
                await state.update_data(album_gid=gid, album_items=[], caption=None)
                items = [{"type": it["type"], "file_id": it["file_id"]} for it in items[-1:]]  # начнём с текущего
            await state.update_data(album_items=items, caption=caption, new_content=None)
            # Запланируем одноразовую финализацию через небольшой таймаут
            async def _debounced():
                await asyncio.sleep(ALBUM_DEBOUNCE_SEC)
                await _finalize_album_edit(message, state)
            _schedule_album_task(message.chat.id, asyncio.create_task(_debounced()))
            return

        # Одиночный контент — кладём в буфер, ждём «Сохранить/Назад»
        single = _extract_single_content(message)
        if not single:
            await message.answer("Пришлите новый контент или используйте «Назад».", reply_markup=BTN_CONTENT_SAVE_BACK)
            return
        await state.update_data(
            new_content=single,
            album_gid=None,
            album_items=[],
            caption=single.get("caption"),
        )
        await message.answer("Новый контент загружен. Нажмите «Сохранить», чтобы применить, или «Назад».",
                             reply_markup=BTN_CONTENT_SAVE_BACK)
        return

    # 1) Сбор контента (создание) — создаём запись сразу и открываем карточку
    if step in (None, "await_content"):
        # Альбом? (Telegram ставит media_group_id / group_media_id)
        if message.media_group_id:
            gid = message.media_group_id
            st_gid = data.get("album_gid")
            items: List[Dict[str, str]] = data.get("album_items") or []
            caption = data.get("caption")

            if message.photo:
                file_id = message.photo[-1].file_id
                items.append({"type": "photo", "file_id": file_id})
                if (message.caption or "") and not caption:
                    caption = message.caption
            elif message.video:
                file_id = message.video.file_id
                items.append({"type": "video", "file_id": file_id})
                if (message.caption or "") and not caption:
                    caption = message.caption
            else:
                # игнорируем неподдерживаемые элементы в медиагруппе
                return

            if st_gid is None:
                await state.update_data(album_gid=gid)
            elif st_gid != gid:
                # Поступил другой альбом — перезапускаем набор с новой группой
                await state.update_data(album_gid=gid, album_items=[], caption=None)

            await state.update_data(album_items=items, caption=caption, step="await_content")
            # Ничего не отвечаем на каждую часть; финализируем альбом 1 раз по таймауту
            async def _debounced():
                await asyncio.sleep(ALBUM_DEBOUNCE_SEC)
                await _finalize_album_create(message, state)
            _schedule_album_task(message.chat.id, asyncio.create_task(_debounced()))
            return

        # Одиночный контент (текст/фото/видео/аудио/GIF)
        single = _extract_single_content(message)
        if not single:
            await message.answer("Пришлите текст/медиа для рассылки.", reply_markup=kb_back_admin())
            return

        # Создаём запись сразу с дефолтной датой и открываем карточку
        last = adb.get_last_publish_at()
        def_dt: datetime
        if last:
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(last, fmt)
                    break
                except Exception:
                    pass
            def_dt = parsed or datetime.now()
        else:
            def_dt = datetime.now()
        def_dt = def_dt + timedelta(days=1)
        publish_at_iso = def_dt.isoformat(timespec="minutes")
        mailing_id = adb.create_scheduled_mailing(
            content_type=single["content_type"],
            caption=single.get("caption"),
            payload=single["payload"],
            publish_at=publish_at_iso,
            mailing_on=True,
        )
        await state.clear()
        await state.update_data(view_mailing_id=mailing_id, view_origin="create")
        await _render_mailing_item(message, mailing_id, origin="create")
        return

    # 2) Ожидаем дату/время
    if step == "await_datetime":
        dt = _parse_dt(message.text or "")
        if not dt:
            data = await state.get_data()
            def_str = data.get("default_publish_at")
            hint_kb = kb_use_default_dt(def_str) if def_str else kb_back_admin()
            await message.answer(
                "Не удалось распознать дату/время. Укажите в формате YYYY-MM-DD HH:MM или DD.MM.YYYY HH:MM.\n"
                + (f"🗓 По умолчанию: <b>{def_str}</b>" if def_str else ""),
                reply_markup=hint_kb,
                parse_mode="HTML",
            )
            return

        ctype = data.get("content_type")
        caption = data.get("caption")
        payload = data.get("payload") or {}
        if ctype == "text":
            t = payload.get('text', '') or ''
            extra = f"Текст: {t[:120]}{'…' if t and len(t) > 120 else ''}"
        elif ctype in ("photo", "video", "audio", "animation"):
            extra = f"Caption: {caption or '—'}"
        elif ctype == "media_group":
            items = payload.get("items")
            cnt = len(items or payload.get("file_ids", []))
            extra = f"Медиа в альбоме: {cnt}"
        else:
            extra = "—"

        await state.update_data(step="confirm", publish_at=dt.isoformat(timespec="minutes"))
        await message.answer(
            CONFIRM_MAILING_TEXT_TPL.format(dt=dt.strftime("%Y-%m-%d %H:%M"), ctype=ctype, extra=extra),
            reply_markup=BTN_MAILING_CONFIRM,
            parse_mode="HTML",
        )
        return

    # 3) Любые иные сообщения в иных шагах — подскажем
    await message.answer("Используйте кнопки ниже.", reply_markup=kb_back_admin())


async def go_mailing(callback: CallbackQuery, state: FSMContext):
    if callback.message.chat.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return

    data = await state.get_data()
    if data.get("step") != "confirm":
        await callback.answer("Сначала пришлите контент и дату.", show_alert=True)
        return

    # Сохраняем запись в БД с флагом mailing_on=1
    ctype: str = data["content_type"]
    caption = data.get("caption")
    payload: Dict[str, Any] = data.get("payload") or {}
    publish_at_iso: str = data["publish_at"]

    mailing_id = adb.create_scheduled_mailing(
        content_type=ctype,
        caption=caption,
        payload=payload,
        publish_at=publish_at_iso,
        mailing_on=True,
    )

    await _edit_or_send(callback.message, text=f"{MAIL_SCHEDULED_OK}\nID: {mailing_id}", kb=kb_back_admin())
    await state.clear()
    await callback.answer()


async def use_default_datetime(callback: CallbackQuery, state: FSMContext):
    """Callback на кнопку '🗓 Использовать {default_dt}' — сразу переходим к подтверждению."""
    if callback.message.chat.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    if not data or data.get("step") != "await_datetime":
        await callback.answer("Сначала загрузите контент.", show_alert=True)
        return
    def_str = data.get("default_publish_at")
    if not def_str:
        await callback.answer("Нет даты по умолчанию.", show_alert=True)
        return
    # формируем подтверждение так же, как при ручном вводе
    ctype = data.get("content_type")
    caption = data.get("caption")
    payload = data.get("payload") or {}
    if ctype == "text":
        t = payload.get('text', '') or ''
        extra = f"Текст: {t[:120]}{'…' if t and len(t) > 120 else ''}"
    elif ctype in ("photo", "video", "audio", "animation"):
        extra = f"Caption: {caption or '—'}"
    elif ctype == "media_group":
        extra = f"Фотографий в альбоме: {len(payload.get('file_ids', []))}"
    else:
        extra = "—"
    await state.update_data(step="confirm", publish_at=def_str)
    await _edit_or_send(
        callback.message,
        text=CONFIRM_MAILING_TEXT_TPL.format(dt=def_str, ctype=ctype, extra=extra),
        kb=BTN_MAILING_CONFIRM,
        parse_mode="HTML",
    )
    await callback.answer()


# =============================
# УПРАВЛЕНИЕ ЗАПЛАНИРОВАННЫМИ
# =============================
async def open_mailing_list(callback: CallbackQuery):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    items = adb.get_scheduled_mailings(limit=10)  # невыполненные
    if not items:
        await _edit_or_send(callback.message, text="Нет запланированных рассылок.", kb=kb_back_admin())
        await callback.answer()
        return
    kb_rows = []
    for m in items:
        dt = m["publish_at"].replace("T", " ")
        kb_rows.append([
            InlineKeyboardButton(
                text=f"{m['id']} • {dt} • {m['content_type']}",
                callback_data=f"admin.mailing.open:{m['id']}"
            )
        ])
    kb_rows.append([InlineKeyboardButton(text="⬅️ В админ-меню", callback_data="admin.home")])
    await _edit_or_send(callback.message, text="Запланированные рассылки:", kb=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await callback.answer()


async def open_mailing_item(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    # сохраняем текущий id в состоянии для возвращений "Назад"
    mailing_id = int(callback.data.split(":")[1])
    await state.update_data(view_mailing_id=mailing_id, view_origin="list")
    await _render_mailing_item(callback.message, mailing_id, origin="list")
    await callback.answer()

async def open_mailing_item_keep(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    # не трогаем view_origin, только обновляем текущий id
    await state.update_data(view_mailing_id=mailing_id)
    data = await state.get_data()
    origin = (data or {}).get("view_origin", "list")
    await _render_mailing_item(callback.message, mailing_id, origin=origin)
    await callback.answer()


async def preview_mailing(callback: CallbackQuery, bot: Bot, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    m = adb.get_mailing_by_id(mailing_id)
    if not m:
        await callback.answer("Не найдено", show_alert=True)
        return
    await _preview_mailing_to_chat(m, callback.message.chat.id, bot)
    # После превью оставляем карточку с нужной кнопкой «Назад»
    data = await state.get_data()
    origin = (data or {}).get("view_origin", "list")
    await _render_mailing_item(callback.message, mailing_id, origin=origin)
    await callback.answer("Показано.")


async def start_edit_mailing_datetime(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    m = adb.get_mailing_by_id(mailing_id)
    if not m:
        await callback.answer("Не найдено", show_alert=True)
        return

    # помечаем шаг редактирования и запоминаем id
    await state.update_data(step="edit_datetime", edit_mailing_id=mailing_id, view_mailing_id=mailing_id)

    # клавиатура "Назад" к карточке редактирования этого сообщения
    back_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Календарь", callback_data=f"admin.mailing.edit_dt.cal:{mailing_id}")
            ],
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin.mailing.open.keep:{mailing_id}")
            ]
        ]
    )

    await _edit_or_send(
        callback.message,
        text="Введите новую дату/время публикации:\n<code>YYYY-MM-DD HH:MM</code> или <code>DD.MM.YYYY HH:MM</code>",
        kb=back_kb,
        parse_mode="HTML",
    )
    await state.set_state(CreateMailing.GetText)
    await callback.answer()

async def start_edit_mailing_datetime_calendar(callback: CallbackQuery, state: FSMContext):
    """Открыть календарик на дате текущей публикации."""
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    m = adb.get_mailing_by_id(mailing_id)
    if not m:
        await callback.answer("Не найдено", show_alert=True)
        return
    # парсим текущую дату публикации и открываем календарь на ней
    pub = m["publish_at"].replace("T", " ")
    base_dt = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            from datetime import datetime as _dt
            base_dt = _dt.strptime(pub, fmt)
            break
        except Exception:
            pass
    from datetime import datetime as _dt
    base_dt = base_dt or _dt.now()
    # помним, что редактируем именно этот пост
    await state.update_data(step="edit_datetime", edit_mailing_id=mailing_id, view_mailing_id=mailing_id)
    # открыть календарь (он сам обработает навигацию; выбор дня вернётся как cal.date:YYYY-MM-DD)
    await open_calendar(callback.message, base_dt.date())
    await callback.answer()

# ─────────────────────────────────────────────────────────────────────────────
# Календарь: финальные действия времени для админ-редактирования
# ─────────────────────────────────────────────────────────────────────────────
async def calendar_time_done(callback: CallbackQuery, state: FSMContext):
    """
    cal.done:YYYY-MM-DDTHH:MM  -> применяем новую дату+время к записи и возвращаем карточку.
    """
    data = await state.get_data()
    if (data or {}).get("step") != "edit_datetime":
        # Не в контексте редактирования — молча пропустим
        await callback.answer()
        return
    mid = int((data or {}).get("edit_mailing_id", 0) or 0)
    if not mid:
        await callback.answer("Нет ID записи.", show_alert=True)
        return
    iso = callback.data.split(":", 1)[1]  # YYYY-MM-DDTHH:MM
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M")
    except Exception:
        await callback.answer("Некорректная дата/время.", show_alert=True)
        return
    adb.update_mailing_publish_at(mid, dt.isoformat(timespec="minutes"))
    await state.update_data(step=None)
    origin = (data or {}).get("view_origin", "list")
    await _render_mailing_item(callback.message, mid, origin=origin)
    await callback.answer("Дата и время обновлены.")

async def calendar_time_keep(callback: CallbackQuery, state: FSMContext):
    """
    cal.keep:YYYY-MM-DD -> оставить текущее время записи, заменить только дату.
    """
    data = await state.get_data()
    if (data or {}).get("step") != "edit_datetime":
        await callback.answer()
        return
    mid = int((data or {}).get("edit_mailing_id", 0) or 0)
    if not mid:
        await callback.answer("Нет ID записи.", show_alert=True)
        return
    dstr = callback.data.split(":", 1)[1]  # YYYY-MM-DD
    m = adb.get_mailing_by_id(mid)
    if not m:
        await callback.answer("Запись не найдена.", show_alert=True)
        return
    old = m["publish_at"].replace("T", " ")
    old_dt = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            old_dt = datetime.strptime(old, fmt)
            break
        except Exception:
            pass
    if not old_dt:
        old_dt = datetime.now()
    hhmm = old_dt.strftime("%H:%M")
    new_dt = datetime.strptime(f"{dstr} {hhmm}", "%Y-%m-%d %H:%M")
    adb.update_mailing_publish_at(mid, new_dt.isoformat(timespec="minutes"))
    await state.update_data(step=None)
    origin = (data or {}).get("view_origin", "list")
    await _render_mailing_item(callback.message, mid, origin=origin)
    await callback.answer("Дата обновлена (время оставлено прежним).")


async def start_edit_mailing_text(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    m = adb.get_mailing_by_id(mailing_id)
    if not m:
        await callback.answer("Не найдено", show_alert=True)
        return
    # сразу ждём новый текст (входящие сообщения ловит mailing_accept с шагом edit_text_wait)
    await state.update_data(
        step="edit_text_wait",
        edit_mailing_id=mailing_id,
        edit_text_buffer=None,
        view_mailing_id=mailing_id
    )
    # Сообщение и комментарий
    if m["content_type"] == "text":
        cur_text = (m.get("payload") or {}).get("text", "") or "—"
        msg_text = (
            f"<b>Сообщение:</b>\n{cur_text}\n\n"
            f"\n\nВведите новый текст, если хотите его изменить.\n"
            f"Или нажмите «Редактировать», чтобы изменить существующий."
        )
    else:
        cur_text = m.get("caption") or "—"
        msg_text = (
            f"<b>Подпись:</b>\n{cur_text}\n\n"
            f"\n\nВведите новый текст, если хотите его изменить.\n"
            f"Или нажмите «Редактировать», чтобы изменить существующий."
        )
    # Кнопка «Редактировать» подгружает текущий текст/подпись в поле ввода
    prefill = (m.get("payload") or {}).get("text", "") if m["content_type"] == "text" else (m.get("caption") or "")
    await _edit_or_send(callback.message, text=msg_text, kb=kb_text_edit_prefilled(prefill), parse_mode="HTML")
    # чтобы текстовый хэндлер принимал сообщение от админа
    await state.set_state(CreateMailing.GetText)
    await callback.answer()


async def text_edit_save(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    # сохраняем только из режима ожидания нового текста
    if not data or data.get("step") != "edit_text_wait":
        await callback.answer("Нет активного редактирования.", show_alert=True)
        return
    mid = int(data.get("edit_mailing_id"))
    buf = data.get("edit_text_buffer")
    if buf is None:
        await callback.answer("Сначала нажмите «Редактировать» и пришлите новый текст.", show_alert=True)
        return
    # Обрезаем ведущий '@...' (особенность switch_inline_query_current_chat)
    cleaned = _clean_leading_at(buf)
    m = adb.get_mailing_by_id(mid)
    if not m:
        await callback.answer("Не найдено", show_alert=True)
        return
    if m["content_type"] == "text":
        adb.update_mailing_text_or_caption(mid, text=cleaned)
    else:
        adb.update_mailing_text_or_caption(mid, caption=cleaned)
    await state.update_data(step=None, edit_text_buffer=None)
    await _edit_or_send(callback.message, text="Текст сохранён.", kb=None)
    data2 = await state.get_data()
    origin = (data2 or {}).get("view_origin", "list")
    await _render_mailing_item(callback.message, mid, origin=origin)
    await callback.answer()


async def text_edit_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mid = int((data or {}).get("view_mailing_id", 0)) or int((data or {}).get("edit_mailing_id", 0)) or 0
    await state.update_data(step=None, edit_text_buffer=None)
    if mid:
        data2 = await state.get_data()
        origin = (data2 or {}).get("view_origin", "list")
        await _render_mailing_item(callback.message, mid, origin=origin)
    else:
        await open_mailing_list(callback)
    await callback.answer()


async def start_edit_mailing_content(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    await state.update_data(
        step="edit_content_wait",
        edit_mailing_id=mailing_id,
        view_mailing_id=mailing_id,
        album_gid=None,
        album_items=[],
        caption=None,
        new_content=None,  # буфер для одиночного контента
    )
    await _edit_or_send(
        callback.message,
        text=("Пришлите новый контент (текст/фото/видео/аудио/GIF) или медиа-группу (альбом) одним пакетом.\n"
              "• Для <b>альбома</b> ничего нажимать не нужно — я дождусь все элементы и сохраню автоматически одним файлом.\n"
              "• Для одиночного контента используйте «Сохранить» или «Назад»."),
        kb=kb_content_edit_open(mailing_id, keep_origin=True),
        parse_mode="HTML",
    )
    await state.set_state(CreateMailing.GetText)
    await callback.answer()


# Кнопка album_done_edit больше не используется (редактирование альбома завершается автоматически).

async def content_edit_save(callback: CallbackQuery, state: FSMContext):
    """Сохранить новые медиа/текст и вернуться к карточке рассылки."""
    data = await state.get_data()
    if not data or data.get("step") != "edit_content_wait":
        await callback.answer("Нет изменений для сохранения.", show_alert=True)
        return
    mid = int(data.get("edit_mailing_id"))
    items: List[Dict[str, str]] = data.get("album_items") or []
    caption = data.get("caption")
    single: Dict[str, Any] | None = data.get("new_content")

    if items:
        content_type = "media_group"
        payload = {"items": items}
    elif single:
        content_type = single["content_type"]
        payload = single["payload"]
        caption = single.get("caption")
    else:
        await callback.answer("Вы ничего не загрузили.", show_alert=True)
        return

    adb.update_mailing_payload(
        mailing_id=mid,
        content_type=content_type,
        payload=payload,
        caption=caption,
    )
    # очистим буферы и вернёмся к карточке
    await state.update_data(step=None, album_gid=None, album_items=[], caption=None, new_content=None)
    data2 = await state.get_data()
    origin = (data2 or {}).get("view_origin", "list")
    await _render_mailing_item(callback.message, mid, origin=origin)
    await callback.answer("Сохранено.")

async def content_edit_back(callback: CallbackQuery, state: FSMContext):
    """Назад без сохранения — просто вернуться к карточке."""
    data = await state.get_data()
    mid = int((data or {}).get("view_mailing_id", 0)) or int((data or {}).get("edit_mailing_id", 0)) or 0
    await state.update_data(step=None, album_gid=None, album_items=[], caption=None, new_content=None)
    if mid:
        data2 = await state.get_data()
        origin = (data2 or {}).get("view_origin", "list")
        await _render_mailing_item(callback.message, mid, origin=origin)
    await callback.answer()

async def content_edit_delete(callback: CallbackQuery, state: FSMContext):
    """Удалить текущий контент у рассылки и вернуться к карточке."""
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    # «Удалить контент»: обнуляем — сохраняем пустой текст.
    adb.update_mailing_payload(
        mailing_id=mailing_id,
        content_type="text",
        payload={"text": ""},
        caption=None,
    )
    await state.update_data(step=None, album_gid=None, album_items=[], caption=None, new_content=None, edit_mailing_id=mailing_id, view_mailing_id=mailing_id)
    data2 = await state.get_data()
    origin = (data2 or {}).get("view_origin", "list")
    await _render_mailing_item(callback.message, mailing_id, origin=origin)
    await callback.answer("Контент удалён.")


async def delete_mailing(callback: CallbackQuery):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    ok = adb.delete_mailing(mailing_id)
    if ok:
        # вернуться к списку запланированных
        await open_mailing_list(callback)
        return  # open_mailing_list сам делает callback.answer()
    else:
        await _edit_or_send(
            callback.message,
            text="Не удалось удалить (возможно, не найдено).",
            kb=kb_back_admin(),
        )
        await callback.answer()


# =============================================================================
# ОПЛАТА (пользовательский поток из общей логики)
# =============================================================================
async def pre_checkout(pre_checkout_q, bot: Bot):
    await bot.answer_pre_checkout_query(pre_checkout_q.id, ok=True)


async def successful_payment(message: Message, bot: Bot):
    payment_info = message.successful_payment
    months = int(payment_info.invoice_payload)
    amount_rub = payment_info.total_amount / 100
    await message.answer(
        SUCCESS_PAYMENT_TPL.format(amount=amount_rub, currency=payment_info.currency, months=months)
    )
    adb.add_sub_user(message.from_user.id, months)
    await create_invite(message, message.bot)
    await message.answer(POSTS_HEADER)
    await notify_admin_about_new_sub(message.from_user.id, bot)


# =============================================================================
# ВСПОМОГАТЕЛЬНЫЕ
# =============================================================================
async def create_invite(message: Message, bot: Bot):
    try:
        invite_link: ChatInviteLink = await bot.create_chat_invite_link(
            chat_id=cfg.CONTENT_GROUP_ID,
            expire_date=None,
            member_limit=1,
            creates_join_request=False,
        )
        await message.answer(PERSONAL_INVITE_TPL.format(invite_link.invite_link), parse_mode=None)
    except Exception as e:
        await message.answer(INVITE_ERROR_TPL.format(e))


async def notify_admin_about_new_sub(user_id: int, bot: Bot):
    user_info = adb.check_user(user_id)
    if not user_info:
        return
    text = f"<a href='https://t.me/{user_info[2]}'>Пользователь</a> оплатил подписку на: {user_info[0]} месяц(ев)."
    await bot.send_message(chat_id=cfg.ADMIN_GROUP_ID, text=text, parse_mode="HTML")


# =============================================================================
# ПЛАНОВЫЕ ЗАДАЧИ (scheduler)
# =============================================================================
async def check_user_sub(bot: Bot):
    expired_users = adb.remove_expired_subscriptions()
    for user_id in expired_users:
        try:
            await bot.send_message(chat_id=int(user_id), text=SUB_EXPIRED_MSG, parse_mode="HTML")
        except Exception as e:
            print(f"Не удалось уведомить {user_id}: {e}")


async def notify_expiring_users(bot: Bot):
    for days in [10, 7, 3, 1]:
        users = adb.get_users_with_expiring_subscription(days_before=days)
        msg = adb.get_notification_message(days)
        if not msg or not users:
            continue
        for uid in users:
            try:
                await bot.send_message(chat_id=int(uid), text=msg, parse_mode="HTML")
            except Exception as e:
                print(f"Не удалось отправить {uid}: {e}")


async def run_mailing_scheduler(bot: Bot):
    """
    Вызывать из внешнего планировщика (APScheduler/cron).
    Берёт все Mailings, у которых:
      - mailing_on = 1
      - mailing_completed = 0
      - publish_at <= now
    Шлёт подписчикам контент и помечает как выполненную.
    """
    pending = adb.get_pending_mailings()
    if not pending:
        return

    user_ids = adb.get_active_user_ids()
    if not user_ids:
        # Некому отправлять — сразу пометим как completed, чтобы не висело (опционально)
        for m in pending:
            adb.mark_mailing_completed(m["id"])
        return

    for m in pending:
        ctype = m["content_type"]
        caption = m.get("caption")
        payload = m.get("payload") or {}

        for uid in user_ids:
            try:
                if ctype == "text":
                    await bot.send_message(int(uid), payload.get("text", ""))
                elif ctype == "photo":
                    await bot.send_photo(int(uid), payload["file_id"], caption=caption or None)
                elif ctype == "video":
                    await bot.send_video(int(uid), payload["file_id"], caption=caption or None)
                elif ctype == "audio":
                    await bot.send_audio(int(uid), payload["file_id"], caption=caption or None)
                elif ctype == "animation":
                    await bot.send_animation(int(uid), payload["file_id"], caption=caption or None)
                elif ctype == "media_group":
                    items = payload.get("items")
                    if not items:
                        # back-compat: старая схема — только фото
                        file_ids: List[str] = payload.get("file_ids", [])
                        items = [{"type": "photo", "file_id": fid} for fid in file_ids]
                    # Telegram ограничивает медиа-группу 10 элементами. Режем на чанки.
                    for chunk in _chunk(items, 10):
                        media = []
                        for i, it in enumerate(chunk):
                            t = (it.get("type") or "photo").lower()
                            fid = it.get("file_id")
                            cap = caption if (i == 0 and caption) else None
                            if t == "video":
                                media.append(InputMediaVideo(media=fid, caption=cap))
                            else:
                                media.append(InputMediaPhoto(media=fid, caption=cap))
                        await bot.send_media_group(int(uid), media)
                else:
                    # неизвестный тип — пропускаем
                    pass
            except Exception as e:
                print(f"Mailing send error to {uid}: {e}")

        adb.mark_mailing_completed(m["id"])


# =============================================================================
# РОУТЕР
# =============================================================================
def router(rt: Router):
    # Вход только командой; дальше — кнопками
    rt.message.register(admin_menu, Command("admin_menu"))
    rt.callback_query.register(admin_home, F.data == "admin.home")

    # Рассылка (контент -> дата -> подтверждение)
    rt.callback_query.register(start_mailing, F.data == "admin.mailing")
    rt.callback_query.register(mailing_stop, F.data == "stop_mailing")
    # Кнопка «Далее» для альбома больше не используется
    rt.message.register(mailing_accept, CreateMailing.GetText)  # принимает и контент, и дату
    rt.callback_query.register(go_mailing, F.data == "go_mailing")
    rt.callback_query.register(use_default_datetime, F.data == "admin.mailing.use_default")

    # Управление запланированными
    rt.callback_query.register(open_mailing_list, F.data == "admin.mailing.list")
    rt.callback_query.register(open_mailing_item, F.data.startswith("admin.mailing.open:"))
    rt.callback_query.register(open_mailing_item_keep, F.data.startswith("admin.mailing.open.keep:"))
    rt.callback_query.register(preview_mailing, F.data.startswith("admin.mailing.show:"))
    rt.callback_query.register(start_edit_mailing_datetime, F.data.startswith("admin.mailing.edit_dt:"))
    rt.callback_query.register(start_edit_mailing_datetime_calendar, F.data.startswith("admin.mailing.edit_dt.cal:"))
    rt.callback_query.register(start_edit_mailing_text, F.data.startswith("admin.mailing.text:"))
    rt.callback_query.register(text_edit_save, F.data == "admin.mailing.text.save")
    rt.callback_query.register(text_edit_back, F.data == "admin.mailing.text.back")
    rt.callback_query.register(start_edit_mailing_content, F.data.startswith("admin.mailing.content:"))
    # Новое меню редактирования контента
    rt.callback_query.register(content_edit_delete, F.data.startswith("admin.mailing.content.del:"))
    rt.callback_query.register(content_edit_save, F.data == "admin.mailing.content.save")
    rt.callback_query.register(content_edit_back, F.data == "admin.mailing.content.back")
    # album_done_edit не используется — финализация альбома идёт автоматически
    # Удаление самой рассылки (кнопка 🗑 Удалить в карточке)
    rt.callback_query.register(delete_mailing, F.data.startswith("admin.mailing.delete:"))
    # Сначала подключаем календарный виджет (дата → выбор времени)
    calendar_router(rt)
    # Финальные действия выбора времени (применение результата)
    rt.callback_query.register(calendar_time_done, F.data.startswith("cal.done:"))
    rt.callback_query.register(calendar_time_keep, F.data.startswith("cal.keep:"))

    # Оплата (если используется в проекте)
    rt.pre_checkout_query.register(pre_checkout)
    rt.message.register(successful_payment, F.successful_payment)