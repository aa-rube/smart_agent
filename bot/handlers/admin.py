# smart_agent/bot/handlers/admin.py
from __future__ import annotations

from datetime import datetime, timedelta
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
)

import bot.config as cfg
import bot.utils.admin_db as adb
from bot.states.states import CreateMailing  # оставляем только рассылку


# =============================================================================
# UX тексты
# =============================================================================
ADMIN_MENU_TEXT = (
    "<b>Админ-меню</b>\n\n"
    "Выберите действие кнопкой ниже."
)
NO_ACCESS_TEXT = "У вас нет доступа к админ панели."

ASK_MAILING_CONTENT = (
    "Отправьте сообщение для рассылки.\n\n"
    "Поддерживается: текст, фото, <u>альбом фото</u>, видео, аудио, GIF/анимация.\n"
    "Если отправляете <b>альбом</b>, загрузите все фото одним пакетом, затем нажмите «Далее». "
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

BTN_ALBUM_FLOW = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Далее", callback_data="admin.mailing.album_done")],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data="stop_mailing")],
        [InlineKeyboardButton(text="⬅️ В админ-меню", callback_data="admin.home")],
    ]
)

BTN_ALBUM_FLOW_EDIT = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="➡️ Далее", callback_data="admin.mailing.album_done_edit")],
        [InlineKeyboardButton(text="↩️ Отмена", callback_data="stop_mailing")],
        [InlineKeyboardButton(text="⬅️ В админ-меню", callback_data="admin.home")],
    ]
)


def kb_mailing_item_controls(mailing_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👁 Показать", callback_data=f"admin.mailing.show:{mailing_id}")],
            [InlineKeyboardButton(text="🗓 Изменить дату/время", callback_data=f"admin.mailing.edit_dt:{mailing_id}")],
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data=f"admin.mailing.text:{mailing_id}")],
            [InlineKeyboardButton(text="🖼 Изменить контент", callback_data=f"admin.mailing.content:{mailing_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin.mailing.delete:{mailing_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin.mailing.list")],
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
        file_ids: List[str] = payload.get("file_ids", [])
        for chunk in _chunk(file_ids, 10):
            media = []
            for i, fid in enumerate(chunk):
                if i == 0 and caption:
                    media.append(InputMediaPhoto(media=fid, caption=caption))
                else:
                    media.append(InputMediaPhoto(media=fid))
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

async def _render_mailing_item(message: Message, mailing_id: int) -> None:
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
        extra = f"Альбом • фото: {len((m.get('payload') or {}).get('file_ids', []))} • caption: {cap}"
    else:
        extra = f"Caption: {cap}"
    await _edit_or_send(
        message,
        text=f"<b>ID:</b> {mailing_id}\n<b>Когда:</b> {dt}\n<b>Тип:</b> <code>{ctype}</code>\n{extra}",
        kb=kb_mailing_item_controls(mailing_id),
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


async def album_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    items: List[str] = data.get("album_items") or []
    if not items:
        await callback.answer("Альбом пуст — пришлите фото.", show_alert=True)
        return
    # Сохраняем как контент и просим дату
    await state.update_data(
        step="await_datetime",
        content_type="media_group",
        payload={"file_ids": items},
    )
    # default_dt = (max publish_at from DB) + 1 day
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
    def_str = def_dt.strftime("%Y-%m-%d %H:%M")
    await state.update_data(default_publish_at=def_str)
    await _edit_or_send(
        callback.message,
        text=ASK_MAILING_DATETIME.format(default_dt=def_str),
        kb=kb_use_default_dt(def_str),
        parse_mode="HTML",
    )
    await callback.answer()


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
        await _render_mailing_item(message, mid)
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
        # Альбом для редактирования?
        if message.media_group_id:
            gid = message.media_group_id
            st_gid = data.get("album_gid")
            items: List[str] = data.get("album_items") or []
            caption = data.get("caption")
            if message.photo:
                fid = message.photo[-1].file_id
                items.append(fid)
                if (message.caption or "") and not caption:
                    caption = message.caption
            else:
                await message.answer(
                    "В альбоме допустимы только фото. Повторите отправку.",
                    reply_markup=BTN_ALBUM_FLOW_EDIT,
                )
                return
            if st_gid is None:
                await state.update_data(album_gid=gid)
            elif st_gid != gid:
                await message.answer(
                    "Получен другой альбом — завершите текущий кнопкой «Далее».",
                    reply_markup=BTN_ALBUM_FLOW_EDIT,
                )
                return
            await state.update_data(album_items=items, caption=caption)
            await message.answer(
                f"Принято фото: {len(items)}. Нажмите «Далее», когда закончите.",
                reply_markup=BTN_ALBUM_FLOW_EDIT,
            )
            return

        # Одиночный контент
        single = _extract_single_content(message)
        if not single:
            await message.answer("Пришлите новый контент или альбом.", reply_markup=kb_back_admin())
            return
        mid = int(data.get("edit_mailing_id"))
        adb.update_mailing_payload(
            mailing_id=mid,
            content_type=single["content_type"],
            payload=single["payload"],
            caption=single.get("caption"),
        )
        await state.update_data(step=None, album_gid=None, album_items=[], caption=None)
        await message.answer("Контент обновлён.")
        await _render_mailing_item(message, mid)
        return

    # 1) Сбор контента (создание)
    if step in (None, "await_content"):
        # Альбом фото?
        if message.media_group_id:
            gid = message.media_group_id
            st_gid = data.get("album_gid")
            items: List[str] = data.get("album_items") or []
            caption = data.get("caption")

            if message.photo:
                file_id = message.photo[-1].file_id
                items.append(file_id)
                if (message.caption or "") and not caption:
                    caption = message.caption
            else:
                await message.answer(
                    "В альбоме допустимы только фотографии. Повторите отправку.",
                    reply_markup=BTN_ALBUM_FLOW,
                )
                return

            if st_gid is None:
                await state.update_data(album_gid=gid)
            elif st_gid != gid:
                await message.answer(
                    "Получен другой альбом — завершите текущий кнопкой «Далее» или нажмите «Отмена».",
                    reply_markup=BTN_ALBUM_FLOW,
                )
                return

            await state.update_data(album_items=items, caption=caption, step="await_content")
            await message.answer(
                f"Принято фото в альбом: {len(items)}. Когда отправите все — нажмите «Далее».",
                reply_markup=BTN_ALBUM_FLOW,
            )
            return

        # Одиночный контент (текст/фото/видео/аудио/GIF)
        single = _extract_single_content(message)
        if not single:
            await message.answer("Пришлите текст/медиа для рассылки.", reply_markup=kb_back_admin())
            return

        await state.update_data(
            step="await_datetime",
            content_type=single["content_type"],
            caption=single.get("caption"),
            payload=single["payload"],
        )
        # default_dt = (max publish_at from DB) + 1 day
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
        def_str = def_dt.strftime("%Y-%m-%d %H:%M")
        await state.update_data(default_publish_at=def_str)
        await message.answer(
            ASK_MAILING_DATETIME.format(default_dt=def_str),
            reply_markup=kb_use_default_dt(def_str),
            parse_mode="HTML",
        )
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
            extra = f"Фотографий в альбоме: {len(payload.get('file_ids', []))}"
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

    await callback.message.answer(f"{MAIL_SCHEDULED_OK}\nID: {mailing_id}", reply_markup=kb_back_admin())
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
        await callback.message.answer("Нет запланированных рассылок.", reply_markup=kb_back_admin())
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
    await state.update_data(view_mailing_id=mailing_id)
    await _render_mailing_item(callback.message, mailing_id)
    await callback.answer()


async def preview_mailing(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    m = adb.get_mailing_by_id(mailing_id)
    if not m:
        await callback.answer("Не найдено", show_alert=True)
        return
    await _preview_mailing_to_chat(m, callback.message.chat.id, bot)
    # После превью оставляем на экране карточку с кнопками
    await _render_mailing_item(callback.message, mailing_id)
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
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin.mailing.open:{mailing_id}")]
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


async def start_edit_mailing_text(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    m = adb.get_mailing_by_id(mailing_id)
    if not m:
        await callback.answer("Не найдено", show_alert=True)
        return
    await state.update_data(step="edit_text", edit_mailing_id=mailing_id, edit_text_buffer=None, view_mailing_id=mailing_id)
    # Сообщение и комментарий
    if m["content_type"] == "text":
        cur_text = (m.get("payload") or {}).get("text", "") or "—"
        msg_text = (
            f"<b>Сообщение:</b>\n{cur_text}\n\n"
            f"<b>Комментарий:</b>\nВведите новый текст, если хотите его изменить.\n"
            f"Или нажмите «Редактировать», чтобы изменить существующий."
        )
    else:
        cur_text = m.get("caption") or "—"
        msg_text = (
            f"<b>Подпись:</b>\n{cur_text}\n\n"
            f"<b>Комментарий:</b>\nВведите новый текст, если хотите его изменить.\n"
            f"Или нажмите «Редактировать», чтобы изменить существующий."
        )
    # Кнопка «Редактировать» подгружает текущий текст/подпись в поле ввода
    prefill = (m.get("payload") or {}).get("text", "") if m["content_type"] == "text" else (m.get("caption") or "")
    await _edit_or_send(callback.message, text=msg_text, kb=kb_text_edit_prefilled(prefill), parse_mode="HTML")
    await callback.answer()





async def text_edit_save(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or data.get("step") not in ("edit_text", "edit_text_wait"):
        await callback.answer("Нет активного редактирования.", show_alert=True)
        return
    mid = int(data.get("edit_mailing_id"))
    buf = data.get("edit_text_buffer")
    if buf is None:
        await callback.answer("Сначала нажмите «Редактировать» и пришлите новый текст.", show_alert=True)
        return
    # Обрезаем ведущий '@... '
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
    await _render_mailing_item(callback.message, mid)
    await callback.answer()


async def text_edit_back(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    mid = int((data or {}).get("view_mailing_id", 0)) or int((data or {}).get("edit_mailing_id", 0)) or 0
    await state.update_data(step=None, edit_text_buffer=None)
    if mid:
        await _render_mailing_item(callback.message, mid)
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
    )
    await _edit_or_send(
        callback.message,
        text=("Пришлите новый контент (текст/фото/видео/аудио/GIF) или альбом фото. "
              "Для альбома загрузите все фото и нажмите «Далее»."),
        kb=BTN_ALBUM_FLOW_EDIT,
    )
    await state.set_state(CreateMailing.GetText)
    await callback.answer()


async def album_done_edit(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data or data.get("step") != "edit_content_wait":
        await callback.answer("Нет активного редактирования альбома.", show_alert=True)
        return
    items: List[str] = data.get("album_items") or []
    caption = data.get("caption")
    if not items:
        await callback.answer("Альбом пуст — пришлите фото.", show_alert=True)
        return
    mid = int(data.get("edit_mailing_id"))
    adb.update_mailing_payload(
        mailing_id=mid,
        content_type="media_group",
        payload={"file_ids": items},
        caption=caption,
    )
    await state.update_data(step=None, album_gid=None, album_items=[], caption=None)
    await _edit_or_send(callback.message, text="Контент (альбом) обновлён.", kb=None)
    await _render_mailing_item(callback.message, mid)
    await callback.answer()


async def delete_mailing(callback: CallbackQuery):
    if callback.from_user.id != cfg.ADMIN_ID:
        await callback.answer("Нет доступа", show_alert=True)
        return
    mailing_id = int(callback.data.split(":")[1])
    ok = adb.delete_mailing(mailing_id)
    await _edit_or_send(
        callback.message,
        text="Удалено." if ok else "Не удалось удалить (возможно, не найдено).",
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
                    file_ids: List[str] = payload.get("file_ids", [])
                    # Telegram ограничивает медиа-группу 10 элементами. Режем на чанки.
                    for chunk in _chunk(file_ids, 10):
                        media = []
                        for i, fid in enumerate(chunk):
                            if i == 0 and caption:
                                media.append(InputMediaPhoto(media=fid, caption=caption))
                            else:
                                media.append(InputMediaPhoto(media=fid))
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
    rt.callback_query.register(album_done, F.data == "admin.mailing.album_done")
    rt.message.register(mailing_accept, CreateMailing.GetText)  # принимает и контент, и дату
    rt.callback_query.register(go_mailing, F.data == "go_mailing")
    rt.callback_query.register(use_default_datetime, F.data == "admin.mailing.use_default")

    # Управление запланированными
    rt.callback_query.register(open_mailing_list, F.data == "admin.mailing.list")
    rt.callback_query.register(open_mailing_item, F.data.startswith("admin.mailing.open:"))
    rt.callback_query.register(preview_mailing, F.data.startswith("admin.mailing.show:"))
    rt.callback_query.register(start_edit_mailing_datetime, F.data.startswith("admin.mailing.edit_dt:"))
    rt.callback_query.register(start_edit_mailing_text, F.data.startswith("admin.mailing.text:"))
    rt.callback_query.register(text_edit_save, F.data == "admin.mailing.text.save")
    rt.callback_query.register(text_edit_back, F.data == "admin.mailing.text.back")
    rt.callback_query.register(start_edit_mailing_content, F.data.startswith("admin.mailing.content:"))
    rt.callback_query.register(album_done_edit, F.data == "admin.mailing.album_done_edit")

    # Оплата (если используется в проекте)
    rt.pre_checkout_query.register(pre_checkout)
    rt.message.register(successful_payment, F.successful_payment)
