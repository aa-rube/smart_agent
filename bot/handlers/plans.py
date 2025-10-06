# smart_agent/bot/handlers/plans.py
from __future__ import annotations
import logging

import os
import fitz
import aiohttp
from typing import Optional
import base64, re, uuid, tempfile

from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, ContentType
)
from aiogram.fsm.context import FSMContext
from aiogram.enums.chat_action import ChatAction
from aiogram.exceptions import TelegramBadRequest

from bot.config import get_file_path, EXECUTOR_BASE_URL
from bot.states.states import FloorPlanStates
from bot.utils.chat_actions import run_long_operation_with_action
from bot.utils.file_utils import safe_remove
from bot.utils.redis_repo import quota_repo

LOG = logging.getLogger(__name__)


def _save_data_url_to_file(data_url: str, user_id: int) -> str:
    """
    data:image/png;base64,... -> сохраняем во временный файл и возвращаем путь.
    """
    m = re.match(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", data_url)
    if not m:
        raise ValueError("Unsupported data URL")
    
    mime = (m.group("mime") or "image/png").lower()
    raw = base64.b64decode(m.group("data"))
    ext = "png"
    if mime.endswith(("jpeg", "jpg")):
        ext = "jpg"
    elif mime.endswith("webp"):
        ext = "webp"
    
    tmp = tempfile.NamedTemporaryFile(prefix=f"fp_{user_id}_", suffix=f".{ext}", delete=False)
    tmp.write(raw)
    tmp.flush(); tmp.close()
    return tmp.name


# ===========================
# Тексты
# ===========================

_TEXT_GET_FILE_PLAN_TPL = """
1️⃣ Загрузи план/чертёж помещения -  Получи макет за 1–2 минуты 💡

Готов? Кидай файл сюда 👇
""".strip()

def text_get_file_plan(user_id: int) -> str:
    # Подписка/триал не проверяются — возвращаем статичный текст
    return _TEXT_GET_FILE_PLAN_TPL

TEXT_GET_VIZ = "Выберите стиль визуализации плана:"
TEXT_GET_STYLE = "Отлично! Теперь выберите интерьерный стиль 🖼️"
TEXT_FINAL = "✅ Готово! Вот визуализация планировки."
ERROR_WRONG_INPUT = "❌ Пожалуйста, отправь изображение (jpg/png), PDF (1 страница) или прямую ссылку на картинку."
ERROR_PDF_PAGES = "❌ В PDF должно быть не больше одной страницы."
ERROR_LINK = "❌ Не удалось скачать изображение по ссылке. Нужна прямая ссылка на файл (jpg/png)."
SORRY_TRY_AGAIN = "😔 Не удалось сгенерировать изображение. Попробуйте ещё раз."
ERROR_RATE_LIMIT = "⏳ Превышен лимит запросов к Google API. Попробуйте через несколько минут."
ERROR_API_UNAVAILABLE = "🚫 Сервис генерации временно недоступен. Попробуйте позже."


# ===========================
# Клавиатуры
# ===========================

def kb_back_to_tools() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")]]
    )

def kb_visualization_style() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🖊️ Скетч-стиль", callback_data="viz_sketch")],
            [InlineKeyboardButton(text="📸 Реалистичный стиль", callback_data="viz_realistic")],
        ]
    )

def kb_style_choices() -> InlineKeyboardMarkup:
    styles = [
        "Современный", "Скандинавский", "Классика", "Минимализм", "Хай-тек",
        "Лофт", "Эко-стиль", "Средиземноморский", "Барокко", "Неоклассика",
        "🔥 Случайный выбор ИИ",
    ]
    rows = [[InlineKeyboardButton(text=f"💎 {s}", callback_data=f"style_{s}")] for s in styles]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="start_retry")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_result_back() -> InlineKeyboardMarkup:
    """Клавиатура на экране результата, чтобы вернуться к загрузке нового плана."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="↩️ Загрузить другой план", callback_data="plan.back_to_upload")]]
    )


# ===========================
# Квоты
# ===========================
GEN_LIMIT_PER_DAY = 500          # попыток на пользователя
GEN_WINDOW_SEC    = 86400      # 24 часа


# ===========================
# Хелперы редактирования
# ===========================

async def _edit_text_or_caption(msg: Message, text: str, kb: Optional[InlineKeyboardMarkup] = None) -> None:
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode=None)
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_caption(caption=text, reply_markup=kb, parse_mode=None)
        return
    except TelegramBadRequest:
        pass
    try:
        await msg.edit_reply_markup(reply_markup=kb)
    except TelegramBadRequest:
        pass

async def _edit_or_replace_with_photo_file(
    bot: Bot, msg: Message, file_path: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    try:
        media = InputMediaPhoto(media=FSInputFile(file_path), caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=FSInputFile(file_path), caption=caption, reply_markup=kb)

async def _edit_or_replace_with_photo_url(
    bot: Bot, msg: Message, url: str, caption: str, kb: Optional[InlineKeyboardMarkup] = None
) -> None:
    try:
        media = InputMediaPhoto(media=url, caption=caption)
        await msg.edit_media(media=media, reply_markup=kb)
        return
    except TelegramBadRequest:
        try:
            await msg.delete()
        except TelegramBadRequest:
            pass
        await bot.send_photo(chat_id=msg.chat.id, photo=url, caption=caption, reply_markup=kb)


# ===========================
# Хендлеры: создание планировок
# ===========================

async def start_plans_flow(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Старт сценария «Планировки»: проверяем доступ → просим загрузить план/чертёж.
    Стартовый коллбек: "floor_plan"
    """
    user_id = callback.message.chat.id
    # Подписка/триал не проверяются — сразу переходим к загрузке
    await state.set_state(FloorPlanStates.waiting_for_file)
    await _edit_or_replace_with_photo_file(
        bot=bot,
        msg=callback.message,
        file_path=get_file_path('img/bot/plan.png'),
        caption=text_get_file_plan(user_id),
        kb=kb_back_to_tools(),
    )

    await callback.answer()


async def handle_plan_file(message: Message, state: FSMContext, bot: Bot):
    """
    Получаем файл/ссылку → конвертируем PDF (1 страница) в png → сохраняем → предлагаем стиль визуализации.
    """
    # Чат-статус
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    user_id = message.from_user.id
    image_bytes: bytes | None = None

    if message.photo:
        file_id = message.photo[-1].file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()

    elif message.document and message.document.mime_type and message.document.mime_type.startswith('image/'):
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        image_bytes = (await bot.download_file(file.file_path)).read()

    elif message.document and message.document.mime_type == 'application/pdf':
        file_id = message.document.file_id
        file = await bot.get_file(file_id)
        pdf_bytes = (await bot.download_file(file.file_path)).read()
        doc = fitz.open("pdf", pdf_bytes)
        if doc.page_count != 1:
            await message.answer(ERROR_PDF_PAGES)
            return
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=200)
        image_bytes = pix.tobytes("png")
        doc.close()

    elif message.text and (message.text.startswith('http://') or message.text.startswith('https://')):
        url = message.text.strip()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200 and 'image' in (resp.headers.get('Content-Type') or ''):
                        image_bytes = await resp.read()
                    else:
                        await message.answer(ERROR_LINK)
                        return
        except Exception:
            await message.answer(ERROR_LINK)
            return
    else:
        await message.answer(ERROR_WRONG_INPUT)
        return

    if image_bytes:
        # сохраняем временно на диск сами (без save_image_as_png), имя — по user_id
        plan_path = get_file_path(f"img/tmp/plan_{user_id}.png")
        os.makedirs(os.path.dirname(plan_path), exist_ok=True)
        with open(plan_path, "wb") as f:
            f.write(image_bytes)

        await state.update_data(plan_path=plan_path)
        await message.answer(TEXT_GET_VIZ, reply_markup=kb_visualization_style())
        await state.set_state(FloorPlanStates.waiting_for_visualization_style)


async def handle_visualization_style(callback: CallbackQuery, state: FSMContext):
    """
    Выбор «скетч/реализм» → показываем экран выбора интерьерного стиля как МЕДИЙНОЕ сообщение,
    чтобы затем это же сообщение заменить на результат через edit_media.
    """
    viz_style = "sketch" if callback.data == "viz_sketch" else "realistic"
    await state.update_data(visualization_style=viz_style)

    try:
        media = InputMediaPhoto(
            media=FSInputFile(get_file_path('img/bot/plan.png')),
            caption=TEXT_GET_STYLE
        )
        await callback.message.edit_media(media=media, reply_markup=kb_style_choices())
    except TelegramBadRequest:
        # если сообщение было текстом и заменить нельзя — удаляем и отправляем новое фото
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
        await callback.bot.send_photo(
            chat_id=callback.message.chat.id,
            photo=FSInputFile(get_file_path('img/bot/plan.png')),
            caption=TEXT_GET_STYLE,
            reply_markup=kb_style_choices(),
        )

    await state.set_state(FloorPlanStates.waiting_for_style)
    # важно: без popup-текста, чтобы не было всплывашки
    await callback.answer()


async def handle_style_plan(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Финиш: собрали viz+style → генерируем картинку из плана.
    Логика:
      - без pop-up;
      - редактируем текущее сообщение на «⏳...»;
      - показываем chat action;
      - по готовности заменяем ЭТО ЖЕ сообщение на фото-результат (+кнопка «Загрузить другой план»).
    """
    # НЕ показываем pop-up
    await callback.answer()

    user_id = callback.from_user.id
    data = await state.get_data()
    plan_path = data.get("plan_path")
    viz = data.get("visualization_style")

    # --- Лимит 3 генерации за 24 часа (скользящее окно) ---
    ok, remaining, reset_at = await quota_repo.try_consume(
        user_id,
        scope="fp",            # floor plans
        limit=GEN_LIMIT_PER_DAY,
        window_sec=GEN_WINDOW_SEC,
    )
    if not ok:
        # посчитаем сколько часов/минут до сброса
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        reset_dt = datetime.fromtimestamp(reset_at, tz=timezone.utc)
        delta = reset_dt - now
        total_min = max(1, int(delta.total_seconds() // 60))
        hours, mins = divmod(total_min, 60)
        eta_text = (f"{hours} ч. {mins} мин." if hours else f"{mins} мин.")
        await _edit_text_or_caption(
            callback.message,
            f"⛔ Дневной лимит исчерпан.\nВы сможете запустить генерацию снова через ~{eta_text}.",
            kb=kb_back_to_tools()
        )
        await state.clear()
        return

    try:
        _, style_raw = (callback.data or "").split("_", 1)
        interior_style = style_raw
    except Exception:
        interior_style = "Модерн"

    # 1) «Часики» — редактируем текущее сообщение
    await _edit_text_or_caption(
        callback.message,
        "⏳ Генерирую визуализацию… Это может занять до 1–2 минут.",
        kb=None,
    )

    success = False
    try:
        # 2) чат-статус во время долгой операции — теперь передаём параметры,
        # а промпт собирается на стороне executor
        coro = generate_floor_plan(
            floor_plan_path=plan_path,
            visualization_style=viz,
            interior_style=interior_style,
        )
        image_url = await run_long_operation_with_action(
            bot=bot,
            chat_id=user_id,
            action=ChatAction.UPLOAD_PHOTO,
            coro=coro,
        )

        # 3) по готовности — ЗАМЕНЯЕМ это же сообщение на фото-результат
        if image_url:
            # Если пришёл data:URL — отправляем как файл, а не как URL
            if image_url.startswith("data:"):
                local_path = _save_data_url_to_file(image_url, user_id)
                try:
                    await _edit_or_replace_with_photo_file(
                        bot=bot,
                        msg=callback.message,
                        file_path=local_path,
                        caption=TEXT_FINAL,
                        kb=kb_result_back(),
                    )
                finally:
                    safe_remove(local_path)
                success = True
            else:
                try:
                    media = InputMediaPhoto(media=image_url, caption=TEXT_FINAL)
                    await callback.message.edit_media(media=media, reply_markup=kb_result_back())
                except TelegramBadRequest:
                    # фоллбэк — отправим отдельным сообщением
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=image_url,
                        caption=TEXT_FINAL,
                        reply_markup=kb_result_back(),
                    )
                success = True
        else:
            await _edit_text_or_caption(callback.message, SORRY_TRY_AGAIN, kb=kb_back_to_tools())

    finally:
        if not success and plan_path and os.path.exists(plan_path):
            safe_remove(plan_path)
        await state.clear()




#########################################################################################################
################################## HTTP CLIENT: GENERATE FLOOR PLAN #####################################
#########################################################################################################

async def generate_floor_plan(*, floor_plan_path: str, visualization_style: str, interior_style: str) -> str:
    """
    Отправляет изображение планировки и параметры визуализации на executor.
    Промпт строится на стороне executor/apps/plan_generate.py.
    Возвращает URL сгенерированного изображения или пустую строку.
    """
    import os, io, json, uuid
    from datetime import datetime
    from aiohttp import FormData, ClientSession

    # Основной путь через Blueprint с префиксом и фолбэк на «старый» путь без префикса
    base = os.getenv("EXECUTOR_BASE_URL", "http://localhost:8080").rstrip("/")
    api_prefix = os.getenv("EXECUTOR_API_PREFIX", "/api/v1").strip("/")
    primary_url = f"{base}/{api_prefix}/plan/generate" if api_prefix else f"{base}/plan/generate"
    fallback_url = f"{base}/plan/generate"

    # Читаем файл в память, чтобы можно было переиспользовать payload при фолбэке
    with open(floor_plan_path, "rb") as fh:
        img_bytes = fh.read()

    def _build_form() -> FormData:
        form = FormData()
        form.add_field(
            "image",
            io.BytesIO(img_bytes),
            filename=os.path.basename(floor_plan_path),
            content_type="image/png",
        )
        if visualization_style:
            form.add_field("visualization_style", visualization_style)
        form.add_field("interior_style", interior_style or "Модерн")
        return form

    req_id = f"fp-{uuid.uuid4().hex[:8]}-{int(datetime.utcnow().timestamp())}"
    try:
        async with ClientSession() as session:
            # 1) пробуем новый путь с префиксом (/api/v1/plan/generate)
            async with session.post(
                primary_url,
                params={"debug": "1"},
                data=_build_form(),
                headers={"X-Request-ID": req_id},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Проверяем разные форматы ответа
                    url = data.get("url") or ""
                    if not url and data.get("images"):
                        # Если есть массив изображений, берем первое
                        images = data.get("images", [])
                        if images:
                            url = images[0]
                    return url
                # 404 — пробуем фолбэк на старый путь
                if resp.status != 404:
                    body_text = await resp.text()
                    try:
                        body_json = json.loads(body_text)
                    except Exception:
                        body_json = {"raw": body_text}
                    LOG.error(
                        "FloorPlan primary failed [%s] %s status=%s details=%s",
                        req_id, primary_url, resp.status, body_json
                    )
                    return ""

            # 2) фолбэк на /plan/generate
            async with session.post(
                fallback_url,
                params={"debug": "1"},
                data=_build_form(),
                headers={"X-Request-ID": req_id},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Проверяем разные форматы ответа
                    url = data.get("url") or ""
                    if not url and data.get("images"):
                        # Если есть массив изображений, берем первое
                        images = data.get("images", [])
                        if images:
                            url = images[0]
                    return url
                else:
                    body_text = await resp.text()
                    try:
                        body_json = json.loads(body_text)
                    except Exception:
                        body_json = {"raw": body_text}
                    LOG.error(
                        "FloorPlan fallback failed [%s] %s status=%s details=%s",
                        req_id, fallback_url, resp.status, body_json
                    )
                    return ""
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "Too Many Requests" in error_msg:
            LOG.warning("Rate limit hit for generate_floor_plan [%s]: %s", req_id, e)
        elif "401" in error_msg or "403" in error_msg:
            LOG.error("Auth error in generate_floor_plan [%s]: %s", req_id, e)
        else:
            LOG.exception("Exception in generate_floor_plan [%s]: %s", req_id, e)
        return ""



async def handle_plan_back_to_upload(callback: CallbackQuery, state: FSMContext, bot: Bot):
    """
    Кнопка «Назад» с экрана результата:
    1) убираем клавиатуру у сообщения, где нажали кнопку (контент остаётся);
    2) отправляем новое сообщение с экраном «загрузите план»;
    3) переводим стейт в ожидание файла.
    """
    user_id = callback.from_user.id
    # 1) убрать клавиатуру у текущего сообщения
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass

    # 2) отправить новый экран загрузки
    await state.set_state(FloorPlanStates.waiting_for_file)
    await bot.send_photo(
        chat_id=callback.message.chat.id,
        photo=FSInputFile(get_file_path('img/bot/plan.png')),
        caption=text_get_file_plan(user_id),
        reply_markup=kb_back_to_tools(),
    )

    await callback.answer()

# ===========================
# Router
# ===========================
def router(rt: Router) -> None:
    # Старт сценария планировок
    rt.callback_query.register(start_plans_flow, F.data == "floor_plan")

    # Загрузка файла → выбор виз-стиля
    rt.message.register(
        handle_plan_file,
        FloorPlanStates.waiting_for_file,
        F.content_type.in_({ContentType.PHOTO, ContentType.DOCUMENT, ContentType.TEXT})
    )
    # Виз-стиль → интерьерный стиль
    rt.callback_query.register(handle_visualization_style, FloorPlanStates.waiting_for_visualization_style)
    # Запуск генерации
    rt.callback_query.register(handle_style_plan, FloorPlanStates.waiting_for_style)
    # Кнопка «назад к загрузке плана» с экрана результата
    rt.callback_query.register(handle_plan_back_to_upload, F.data == "plan.back_to_upload")
