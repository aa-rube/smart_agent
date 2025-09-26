# C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\description.py
from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional, Tuple
from flask import jsonify, Request
from executor.config import OPENAI_API_KEY
import threading
import requests
import json
import re
from urllib.parse import urlparse

try:
    from openai import OpenAI
except Exception:
    # Для тайпченкера и рантайма: не валимся при отсутствии пакета.
    OpenAI = None  # type: ignore

_FALLBACK_MODELS: List[str] = ["gpt-5", "gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4.1-mini"]

# Храним общий клиент без жесткой типизации, чтобы IDE не ругалась.
_client_default: Any = None


def _log_request(payload: Dict[str, Any]) -> None:
    if HTTP_DEBUG:
        LOG.info(
            "OpenAI request: model=%s temp=%s max_tokens=%s messages=%d",
            payload.get("model"),
            # payload.get("temperature"),
            # payload.get("any+", "max_tokens"),
            len(payload.get("messages") or []),
        )


# =========================
# OpenAI helpers (client + send)
# =========================
def _extract_text(resp: Any) -> str:
    """
    Безопасно достаём текст из Chat Completions ответа.
    """
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def _client_or_init(api_key: Optional[str]) -> Any:
    """
    Возвращает OpenAI-клиент:
      - если ключ per-request совпадает с дефолтным — используем и кешируем общий клиент;
      - если пришёл иной ключ — создаём ephemeral клиент (без кеша).
    """
    if OpenAI is None:
        raise RuntimeError("openai package is not installed")

    default_key = _default_api_key()
    req_key = (api_key or default_key or "").strip()
    if not req_key:
        raise RuntimeError("OPENAI_API_KEY is missing (config/env or request header/body)")

    global _client_default
    if req_key == default_key:
        if _client_default is None:
            _client_default = OpenAI(api_key=req_key)
        return _client_default
    # per-request «чужой» ключ — отдельный клиент
    return OpenAI(api_key=req_key)


def _send_with_fallback(payload: Dict[str, Any],
                        default_model: str,
                        allow_fallback: bool,
                        api_key: Optional[str]) -> Tuple[str, str]:
    """
    Отправка Chat Completions с цепочкой fallback-моделей.
    Возвращает: (text, model_used).
    """
    client = _client_or_init(api_key)
    first_model = payload.get("model") or default_model
    chain = [first_model] + ([m for m in _FALLBACK_MODELS if m != first_model] if allow_fallback else [])
    last_err: Optional[Exception] = None

    for i, model_name in enumerate(chain, start=1):
        try:
            req = dict(payload);
            req["model"] = model_name
            _log_request(req)
            # type: ignore подавляет IDE-жалобу, когда пакет подхвачен как Any
            resp = client.chat.completions.create(**req)  # type: ignore[attr-defined]
            text = _extract_text(resp)
            if text:
                if i > 1:
                    LOG.warning("Fallback model used: %s (requested %s)", model_name, first_model)
                return text, model_name
            last_err = RuntimeError("Empty completion text")
        except Exception as e:
            last_err = e
            LOG.warning("OpenAI call failed on model %s: %s", model_name, e)

    LOG.error("All OpenAI fallbacks failed. Last error: %s", last_err)
    raise last_err or RuntimeError("OpenAI request failed")


# =========================
# Конфиг / Логгер
# =========================
LOG = logging.getLogger(__name__)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

HTTP_DEBUG = os.getenv("HTTP_DEBUG", "0") == "1"
OPENAI_FALLBACK = os.getenv("OPENAI_FALLBACK", "1") == "1"

# Базовая модель (можно переопределить переменной окружения)
DESCRIPTION_MODEL = os.getenv("DESCRIPTION_MODEL", "gpt-5")

# ------------------ Карты лейблов для select-полей ------------------
# NB: это единый источник «человекочитаемых» лейблов и для UI, и для сборки промпта
DESCRIPTION_TYPES = {
    "flat": "Квартира",
    "house": "Дом",
    "office": "Офис",
    "comm": "Коммерческая недвижимость",
    "commercial": "Коммерческая недвижимость",
    "land": "Земельный участок",
    "country": "Загородная недвижимость",
    "zagorod": "Загородная недвижимость",
}

DESCRIPTION_CLASSES = {
    "econom": "Эконом",
    "comfort": "Комфорт",
    "business": "Бизнес",
    "premium": "Премиум",
}

DESCRIPTION_COMPLEX = {
    "yes": "Да (новостройка/ЖК)",
    "no": "Нет",
}

DESCRIPTION_AREA = {
    "city": "В черте города",
    "out": "За городом",
}

# ------------------ Системный промпт (укорочен — замените своим) ------------------
DESCRIPTION_PROMPT_DEFAULT_RU = """
Ты — ассистент-листинг-райтер. Пишешь продающие описания для Авито/ЦИАН/соцсетей.
Требования:
— Фактура и выгоды, без воды; язык — лёгкий, уверенный, уважительный.
— Структура (по разделам, каждый с коротким подзаголовком):
  1) Заголовок (короткий, с ключевой выгодой)
  2) Локация и окружение (район/метро/транспорт, плюсы района)
  3) Дом/территория (если применимо)
  4) Планировка и метраж (точные факты)
  5) Состояние/ремонт/коммуникации (аккуратно, по делу)
  6) Кому подойдёт (2–3 сценария)
  7) Юридические/условия сделки (если уместно)
  8) CTA (призыв к действию)
— Форматирование: короткие абзацы, маркеры там, где это повышает читаемость.
— Не выдумывай факты; если чего-то нет — не придумывай.
— Если сделка = аренда, формулируй CTA и акценты под аренду.
"""

# ------------------ Шаблон пользовательского сообщения (укорочен) ------------------
DESCRIPTION_USER_TEMPLATE_RU = """
Сгенерируй продающее описание по анкете. Соблюдай «Х-П-В», без воды, фактами и выгодами. 
Формат и стиль ВАЖЕН — выведи строго по блокам ниже, с профессиональными маркерами.

🏷 Заголовок
— Коротко (до 70 символов) и по сути, с 1 ключевой выгодой.

⭐ Ключевые преимущества (3–5 пунктов)
• Метраж/планировка/этаж или этажность (по ситуации)
• Локация/транспортная доступность/инфраструктура
• Состояние/ремонт/высота потолков/вид из окон (если уместно)
• Коммуникации/паркинг/лифты/двор/охрана (по данным)
• Доп. выгоды из анкеты (если есть)

📍 Локация и транспорт
— Район/окружение: {location}
— Зона расположения: {area_label}

🏢 Дом и территория (если применимо)
— Новостройка/ЖК: {in_complex_label}
— Тип/класс: {type_label}{apt_class_label:+, класс {apt_class_label}}
— Двор/территория/инфраструктура: используй факты из анкеты/EXTRAS.

📐 Планировка и метраж
— Общая площадь: {total_area} м²{rooms:+, комнат: {rooms}}
— Кухня: {kitchen_area} м²
— Этаж / этажность: {floor_number} / {building_floors}
— Особенности планировки/окна/балкон/кладовые: {amenities}

🛠 Состояние и коммуникации
— Год/состояние: {year_state}
— Коммуникации: {utilities}
— Ремонт/материалы (если есть в данных): из анкеты/EXTRAS.

👥 Кому подойдёт
— 2–3 сценария целевой аудитории (семья/аренда под сдачу/офис и т.д.) по типу объекта.

📑 Условия сделки
— Формат сделки: учитывай «Сделка: {deal_label}»
— Ипотека/перепланировки/обременения/свободная продажа/документы — ТОЛЬКО если в данных/EXTRAS.
— Доп. условия: срок сдачи, способ продажи (если указаны).

✅ Что важно знать
— Без вымысла. Если данных нет — не упоминай.
— Числа с единицами измерения, аккуратные диапазоны.
— Короткие абзацы, маркеры там, где улучшают читаемость.

📲 Призыв к действию
— 1–2 уверенных предложения: пригласи на просмотр/позвонить/написать. Никакого капса.

Данные анкеты:
— Тип: {type_label}
— Класс: {apt_class_label}
— Новостройка/ЖК: {in_complex_label}
— Расположение (общее): {area_label}
— Локация (район/метро/транспорт): {location}
— Общая площадь: {total_area} м²
— Кухня: {kitchen_area} м²
— Этаж / Этажность: {floor_number} / {building_floors}
— Комнат: {rooms}
— Год / Состояние: {year_state}
— Коммуникации: {utilities}
— Особенности/удобства: {amenities}
— Комментарий: {comment}

Если присутствуют дополнительные поля (квартира/загород/коммерция) — логично используй их в соответствующих блоках; не дублируй и не выдумывай.
"""


# =====================================================================================
# ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ
# =====================================================================================
def _strip_format_specifiers(s: str) -> str:
    """
    Удаляет формат-спецификаторы внутри плейсхолдеров str.format.
    Пример: '{apt_class:+, класс —}' -> '{apt_class}'
    Работает только для имён вида [a-zA-Z_][a-zA-Z0-9_]*.
    """
    return re.sub(r'{([a-zA-Z_]\w*):[^}]*}', r'{\1}', s)

def _default_api_key() -> str:
    """
    Ключ по умолчанию берём из config (приоритет) либо из окружения как бэкап.
    """
    return (OPENAI_API_KEY or os.getenv("OPENAI_API_KEY", "")).strip()


def validate_config() -> List[str]:
    """
    Проверяем только базовые вещи. Тонкий контроллер:
    — отсутствие ключа в окружении не считается ошибкой, если придёт per-request ключ.
    """
    issues: List[str] = []
    # soft check
    if not _default_api_key():
        issues.append("OPENAI_API_KEY not set (pass per-request key or set in config)")
    return issues


def _safe(val: Any) -> str:
    if val is None:
        return "—"
    if isinstance(val, bool):
        return "Да" if val else "Нет"
    if isinstance(val, (int, float)):
        try:
            return f"{val:.15g}"
        except Exception:
            return str(val)
    if isinstance(val, (list, tuple, set)):
        parts = [_safe(x) for x in val]
        parts = [p for p in parts if p and p != "—"]
        return ", ".join(parts) if parts else "—"
    s = str(val).strip()
    return s or "—"


def _label(m: Dict[str, str], key: Optional[str], default: str = "—") -> str:
    return m.get((key or "").strip(), default) if key else default


def _first_nonempty(*xs: Any) -> Any:
    for x in xs:
        if x not in (None, "", []):
            return x
    return None


# Нормализация бота-алиасов: поддерживаем и новые и старые ключи
def _normalize_fields(raw: Dict[str, Any]) -> Dict[str, Any]:
    norm: Dict[str, Any] = {
        # базовые
        "type": raw.get("type"),
        "deal_type": raw.get("deal_type"),  # sale | rent (из playbook)
        "apt_class": raw.get("apt_class"),
        "in_complex": raw.get("in_complex"),
        "area": raw.get("area"),
        "comment": raw.get("comment"),

        # плоскость анкеты
        "total_area": _first_nonempty(raw.get("total_area")),
        "kitchen_area": _first_nonempty(raw.get("kitchen_area")),
        # этаж/этажность: принимаем оба варианта
        "building_floors": _first_nonempty(raw.get("floors_total"), raw.get("building_floors")),
        "floor_number": _first_nonempty(raw.get("floor"), raw.get("floor_number")),
        "rooms": _first_nonempty(raw.get("rooms")),
        "year_state": _first_nonempty(raw.get("year_or_condition"), raw.get("year_state")),
        "utilities": _first_nonempty(raw.get("utilities")),
        "location": _first_nonempty(raw.get("location_exact"), raw.get("location")),
        "amenities": _first_nonempty(raw.get("features"), raw.get("amenities")),

        # квартира (если есть)
        "market": raw.get("market"),
        "completion_term": raw.get("completion_term"),
        "sale_method": raw.get("sale_method"),
        "mortgage_ok": raw.get("mortgage_ok"),
        "bathroom_type": raw.get("bathroom_type"),
        "windows": raw.get("windows"),
        "house_type": raw.get("house_type"),
        "lift": raw.get("lift"),
        "parking": raw.get("parking"),
        "renovation": raw.get("renovation"),
        "layout": raw.get("layout"),
        "balcony": raw.get("balcony"),
        "ceiling_height_m": raw.get("ceiling_height_m"),

        # загород (если есть)
        "country_object_type": raw.get("country_object_type"),
        "country_house_area_m2": raw.get("country_house_area_m2"),
        "country_plot_area_sotki": raw.get("country_plot_area_sotki"),
        "country_distance_km": raw.get("country_distance_km"),
        "country_floors": raw.get("country_floors"),
        "country_rooms": raw.get("country_rooms"),
        "country_land_category_house": raw.get("country_land_category_house"),
        "country_renovation": raw.get("country_renovation"),
        "country_toilet": raw.get("country_toilet"),
        "country_utilities": raw.get("country_utilities"),
        "country_leisure": raw.get("country_leisure"),
        "country_wall_material": raw.get("country_wall_material"),
        "country_parking": raw.get("country_parking"),
        "country_transport": raw.get("country_transport"),
        "country_land_category_plot": raw.get("country_land_category_plot"),
        "country_communications_plot": raw.get("country_communications_plot"),

        # коммерция (если есть) — просто пропускаем в модель через comment-контекст
        "comm_object_type": raw.get("comm_object_type"),
        "land_area": raw.get("land_area"),
        "comm_building_type": raw.get("comm_building_type"),
        "comm_whole_object": raw.get("comm_whole_object"),
        "comm_finish": raw.get("comm_finish"),
        "comm_entrance": raw.get("comm_entrance"),
        "comm_parking": raw.get("comm_parking"),
        "comm_layout": raw.get("comm_layout"),
    }
    # 1) Если для квартиры пришёл «состояние квартиры» (человеческая метка),
    #    а явного year_state нет — используем именно его.
    if not norm.get("year_state") and raw.get("apt_condition"):
        norm["year_state"] = raw.get("apt_condition")

    # 2) Нормализация мультивыборов из playbook:
    #    там мы сохраняем МЕТКИ либо коды (в country_*). На уровне описания нам
    #    удобнее иметь «человеческую строку» (для шаблона user).
    def _join_labels(v: Any) -> Optional[str]:
        if isinstance(v, (list, tuple, set)):
            parts = [str(x).strip() for x in v if str(x).strip()]
            return ", ".join(parts) if parts else None
        return str(v).strip() if v else None

    for multi_key in (
            "country_utilities", "country_leisure", "country_communications_plot"
    ):
        if multi_key in raw:
            j = _join_labels(raw.get(multi_key))
            if j:
                # встраиваем мультивыборы как часть «коммуникаций/удобств» через comment
                # (или оставим как отдельные поля — ниже шаблон их учитывает как amenities/utilities)
                norm[multi_key] = j

    # 3) Удалим пустые строки и "—" (для чистоты payload)
    for k, v in list(norm.items()):
        if v in ("", "—"):
            norm[k] = None
    return norm


# =====================================================================================
# ФАБРИКА ПРОМПТА (Description)
# =====================================================================================
def build_description_request_from_fields(*, fields: Dict[str, Any], model: Optional[str] = None) -> Dict[str, Any]:
    """
    Создает payload для OpenAI запроса из полей анкеты.
    """
    normalized = _normalize_fields(fields)
    user_message = compose_description_user_message(normalized)
    use_model = model or DESCRIPTION_MODEL

    payload = {
        "model": use_model,
        "messages": [
            {"role": "system", "content": DESCRIPTION_PROMPT_DEFAULT_RU},
            {"role": "user", "content": user_message},
        ]
    }
    return payload


def compose_description_user_message(fields: Dict[str, Any]) -> str:
    """
    Собирает пользовательское сообщение из полей анкеты (с нормализацией).
    """
    t_key = fields.get("type")
    c_key = fields.get("apt_class") if (t_key == "flat") else None
    x_key = fields.get("in_complex")
    a_key = fields.get("area")

    deal_label = {"sale": "Продажа", "rent": "Аренда"}.get(str(fields.get("deal_type") or "").strip(), "—")

    user_payload = {
        "deal_label": deal_label,
        "type_label": _label(DESCRIPTION_TYPES, t_key),
        "apt_class_label": _label(DESCRIPTION_CLASSES, c_key) if c_key else "—",
        "in_complex_label": _label(DESCRIPTION_COMPLEX, x_key),
        "area_label": _label(DESCRIPTION_AREA, a_key),

        "location": _safe(fields.get("location")),
        "total_area": _safe(fields.get("total_area")),
        "kitchen_area": _safe(fields.get("kitchen_area")),
        "floor_number": _safe(fields.get("floor_number")),
        "building_floors": _safe(fields.get("building_floors")),
        "rooms": _safe(fields.get("rooms")),
        "year_state": _safe(fields.get("year_state")),
        "utilities": _safe(fields.get("utilities")),
        "amenities": _safe(fields.get("amenities")),
        "comment": _safe(fields.get("comment")),
    }

    # Добираем дополнительные поля (квартира/загород/коммерция) — в EXTRAS,
    # чтобы ассистент мог использовать их, не перегружая основную сетку.
    extras: Dict[str, Any] = {}
    for k in (
            # квартира
            "market", "completion_term", "sale_method", "mortgage_ok", "bathroom_type", "windows",
            "house_type", "lift", "parking", "renovation", "layout", "balcony", "ceiling_height_m",
            # загород
            "country_object_type", "country_house_area_m2", "country_plot_area_sotki", "country_distance_km",
            "country_floors", "country_rooms", "country_land_category_house", "country_renovation", "country_toilet",
            "country_utilities", "country_leisure", "country_wall_material", "country_parking", "country_transport",
            "country_land_category_plot", "country_communications_plot",
            # коммерция
            "comm_object_type", "land_area", "comm_building_type", "comm_whole_object", "comm_finish", "comm_entrance",
            "comm_parking", "comm_layout"
    ):
        v = fields.get(k, None)
        if v not in (None, "", [], "—"):
            extras[k] = v

    if extras:
        extras_str = ", ".join(f"{kk}={_safe(vv)}" for kk, vv in extras.items() if _safe(vv) != "—")
        user_payload["comment"] = (
                    user_payload["comment"] + ((" | EXTRAS: " + extras_str) if extras_str else "")).strip()

    # Добавим сделку в верхнюю часть анкеты для контекста
    msg = "Сгенерируй продающее описание по анкете. Соблюдай «Х-П-В», без воды, с явным CTA.\n\n"
    msg += f"— Сделка: {user_payload['deal_label']}\n"
    msg += DESCRIPTION_USER_TEMPLATE_RU
    # В шаблоне могли оказаться двоеточия после имени плейсхолдера,
    # что воспринимается как формат-спецификатор → чистим их.
    tmpl = _strip_format_specifiers(msg)
    try:
        return tmpl.format(**user_payload)
    except Exception as e:
        # Логируем и делаем максимально безопасную замену без format(),
        # чтобы не ронять обработчик из-за шаблона.
        logging.exception("compose_description_user_message: format failed, fallback is used: %s", e)
        out = tmpl
        for k, v in user_payload.items():
            out = out.replace("{" + k + "}", "" if v is None else str(v))
        return out


def send_description_generate_request_from_fields(
        fields: Dict[str, Any],
        *,
        model: Optional[str] = None,
        allow_fallback: bool = OPENAI_FALLBACK,
        api_key: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Основная функция генерации описания из полей анкеты.
    """
    use_model = model or DESCRIPTION_MODEL
    payload = build_description_request_from_fields(fields=fields, model=use_model)
    return _send_with_fallback(
        payload,
        default_model=use_model,
        allow_fallback=allow_fallback,
        api_key=api_key
    )


def _post_callback(callback_url: str, payload: Dict[str, Any]) -> None:
    """
    Безопасно шлём результат на callback_url. Не бросаем исключения наружу.
    """
    try:
        # небольшая валидация URL
        pr = urlparse(callback_url)
        if pr.scheme not in {"http", "https"}:
            raise ValueError("callback_url must be http/https")
        headers = {"Content-Type": "application/json"}
        requests.post(callback_url, data=json.dumps(payload), headers=headers, timeout=30)
    except Exception as e:
        LOG.warning("Callback POST failed: %s", e)


# =====================================================================================
# PUBLIC ENTRYPOINT for thin controller
# =====================================================================================
def description_generate(req: Request):
    """
    Тонкий вход: разбираем запрос (JSON/form), берём per-request API ключ (если есть),
    вызываем локальный OpenAI-сервис и возвращаем Flask-совместимый ответ.
    Контроллер просто делегирует сюда: return description_module.description_generate(request).
    """
    # мягкая проверка конфигурации: если нет env-ключа и не передан per-request ключ — 500
    issues = validate_config()
    data = req.get_json(silent=True) or {}
    form = req.form or {}

    api_key = (
            req.headers.get("X-OpenAI-Api-Key")
            or (data.get("api_key") if isinstance(data, dict) else None)
            or req.args.get("api_key")
    )
    # если валидация ругается и ключ явно не пришёл — попробуем взять из конфигурации
    if issues and not api_key:
        fallback = _default_api_key()
        if not fallback:
            return jsonify({"error": "config", "detail": "; ".join(issues)}), 500
        api_key = fallback

    # Собираем поля анкеты «как есть»: фабрика сама нормализует алиасы
    fields: Dict[str, Any] = {}
    if isinstance(data, dict):
        fields.update(data)
    for k in form.keys():
        fields[k] = form.get(k)

    # Минимальная валидация
    t = (fields.get("type") or "").strip()
    if not t:
        return jsonify({"error": "bad_request", "detail": "field 'type' is required"}), 400

    # Параметры для обратного вызова
    callback_url   = (data.get("callback_url") if isinstance(data, dict) else None) or req.args.get("callback_url")
    callback_token = (data.get("callback_token") if isinstance(data, dict) else None) or req.args.get("callback_token")
    cb_chat_id     = (data.get("chat_id") if isinstance(data, dict) else None) or req.args.get("chat_id")
    cb_msg_id      = (data.get("msg_id") if isinstance(data, dict) else None) or req.args.get("msg_id")

    debug_flag = req.args.get("debug") == "1"

    # Режим async callback
    if callback_url and cb_chat_id and cb_msg_id:
        try:
            chat_id = int(cb_chat_id)
            msg_id  = int(cb_msg_id)
        except Exception:
            return jsonify({"error": "bad_request", "detail": "chat_id and msg_id must be integers"}), 400

        def _bg():
            """Фоновая генерация и POST результата на callback_url."""
            try:
                text, used_model = send_description_generate_request_from_fields(
                    fields=fields,
                    allow_fallback=True,
                    api_key=api_key,
                )
                payload = {
                    "chat_id": chat_id,
                    "msg_id": msg_id,
                    "text": text,
                    "error": "",
                    "token": callback_token or "",
                }
                _post_callback(callback_url, payload)
            except Exception as e:
                LOG.exception("OpenAI error (description, async)")
                payload = {
                    "chat_id": chat_id,
                    "msg_id": msg_id,
                    "text": "",
                    "error": str(e),
                    "token": callback_token or "",
                }
                _post_callback(callback_url, payload)

        threading.Thread(target=_bg, daemon=True).start()
        # Быстрый ACK, чтобы бот не «ждал»
        return jsonify({"accepted": True}), 202

    # Обычный синхронный режим (совместимость)
    try:
        text, used_model = send_description_generate_request_from_fields(
            fields=fields,
            allow_fallback=True,
            api_key=api_key,
        )
        body: Dict[str, Any] = {"text": text}
        if debug_flag:
            body["debug"] = {"model_used": used_model}
        return jsonify(body), 200
    except Exception as e:
        LOG.exception("OpenAI error (description)")
        body: Dict[str, Any] = {"error": "openai_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"model": DESCRIPTION_MODEL}
        return jsonify(body), 502