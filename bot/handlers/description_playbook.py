# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\bot\handlers\description_playbook.py
# Всегда пиши код без «поддержки старых версий». Если они есть в коде - удаляй.

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

# ====== Доступ / подписка ======
import bot.utils.database as db
from bot.utils.database import is_trial_active, trial_remaining_hours

# ====== Визард (мастер параметров недвижимости) ======
# Ожидается, что визард экспортирует router() -> Router
from bot.handlers.property_wizard import router as wizard_router


# ──────────────────────────────────────────────────────────────────────────────
# Доступ / подписка
# ──────────────────────────────────────────────────────────────────────────────

def _is_sub_active(user_id: int) -> bool:
    raw = db.get_variable(user_id, "sub_until") or ""
    if not raw:
        return False
    try:
        from datetime import datetime
        today = datetime.utcnow().date()
        return today <= datetime.fromisoformat(raw).date()
    except Exception:
        return False

def _format_access_text(user_id: int) -> str:
    trial_hours = trial_remaining_hours(user_id)
    if _is_sub_active(user_id):
        sub_until = db.get_variable(user_id, "sub_until")
        return f'✅ Подписка активна до *{sub_until}*'
    if trial_hours > 0:
        return f'🆓 Бесплатный доступ активен ещё *~{trial_hours} ч.*'
    return '😢 Бесплатный период завершён. Оформи подписку, чтобы продолжить.'

def _has_access(user_id: int) -> bool:
    return is_trial_active(user_id) or _is_sub_active(user_id)

SUB_FREE = (
    "🎁 Бесплатный период завершён\n"
    "Пробный доступ на 72 часа истёк — дальше только по подписке.\n\n"
    "📦 *Что даёт подписка:*\n"
    " — Полный доступ ко всем инструментам\n"
    " — Без ограничений по количеству запусков в период подписки*\n"
    "Стоимость пакета всего 2500 рублей!"
)

SUB_PAY = (
    "🪫 Подписка не активна\n"
    "Срок подписки истёк или не был оформлен.\n\n"
    "📦 *Что даёт подписка:*\n"
    " — Полный доступ ко всем инструментам\n"
    " — Без ограничений по количеству запусков в период подписки*\n"
    "Стоимость пакета всего 2500 рублей!"
)

SUBSCRIBE_KB = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Оформить подписку", callback_data="show_rates")]]
)


# ──────────────────────────────────────────────────────────────────────────────
# Стартовый экран раздела «Описание объекта»
# ──────────────────────────────────────────────────────────────────────────────

INTRO = (
    "Заполните короткую анкету и получите продающее описание объекта для Авито/ЦИАН/соцсетей.\n"
    "Мастер задаст вопросы и сформирует структурированные параметры.\n\n"
    "Нажмите «Заполнить анкету», чтобы начать."
)

def _kb_intro() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧩 Заполнить анкету", callback_data="nav.description")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="nav.ai_tools")],
        ]
    )

async def _edit_or_send_intro(cb: CallbackQuery) -> None:
    text = f"{INTRO}\n\n{_format_access_text(cb.message.chat.id)}"
    try:
        await cb.message.edit_text(text, reply_markup=_kb_intro(), parse_mode="Markdown")
    except TelegramBadRequest:
        await cb.message.answer(text, reply_markup=_kb_intro(), parse_mode="Markdown")


# ──────────────────────────────────────────────────────────────────────────────
# Entry-point хендлер
# ──────────────────────────────────────────────────────────────────────────────

async def start_description_entry(cb: CallbackQuery, state: FSMContext):
    """Единая точка входа в раздел; дальше управление отдаём визарду."""
    await state.clear()
    user_id = cb.message.chat.id

    if not _has_access(user_id):
        text = SUB_FREE if not _is_sub_active(user_id) else SUB_PAY
        try:
            await cb.message.edit_text(text, reply_markup=SUBSCRIBE_KB, parse_mode="Markdown")
        except TelegramBadRequest:
            await cb.message.answer(text, reply_markup=SUBSCRIBE_KB, parse_mode="Markdown")
        await cb.answer()
        return

    await _edit_or_send_intro(cb)
    await cb.answer()


# ──────────────────────────────────────────────────────────────────────────────
# Регистрация роутеров (важно: сигнатура принимает rt: Router)
# ──────────────────────────────────────────────────────────────────────────────

def router(rt: Router):
    """
    Регистрирует стартовые хендлеры и подкладывает визард.
    Совместимо с handlers/__init__.py → register_routers(rt).
    """
    # Точки входа из текущего меню
    rt.callback_query.register(start_description_entry, F.data == "nav.descr_home")
    rt.callback_query.register(start_description_entry, F.data == "desc_start")

    # Подключаем визард (он обрабатывает 'nav.description' и всю дальнейшую логику)
    rt.include_router(wizard_router())
