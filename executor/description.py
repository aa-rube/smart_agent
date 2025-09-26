#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\description.py
from __future__ import annotations

import os
import logging
from typing import Any, Dict, List, Optional, Tuple
from flask import jsonify, Request
from executor.config import OPENAI_API_KEY

try:
    from openai import OpenAI
except Exception:  # пакет может быть не установлен в момент чтения файла
    OpenAI = None  # type: ignore

_FALLBACK_MODELS: List[str] = ["gpt-5", "gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4.1-mini"]

_client_default: Optional["OpenAI"] = None

def _log_request(payload: Dict[str, Any]) -> None:
    if HTTP_DEBUG:
        LOG.info(
            "OpenAI request: model=%s temp=%s max_tokens=%s messages=%d",
            payload.get("model"),
            payload.get("temperature"),
            payload.get("max_tokens"),
            len(payload.get("messages") or []),
        )

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
    "flat":   "Квартира",
    "house":  "Дом",
    "office": "Офис",
    "comm":   "Коммерческая недвижимость",
    "commercial": "Коммерческая недвижимость",
    "land":   "Земельный участок",
    "country":"Загородная недвижимость",
    "zagorod":"Загородная недвижимость",
}

DESCRIPTION_CLASSES = {
    "econom":   "Эконом",
    "comfort":  "Комфорт",
    "business": "Бизнес",
    "premium":  "Премиум",
}

DESCRIPTION_COMPLEX = {
    "yes": "Да (новостройка/ЖК)",
    "no":  "Нет",
}

DESCRIPTION_AREA = {
    "city": "В черте города",
    "out":  "За городом",
}

# ------------------ Системный промпт (укорочен — замените своим) ------------------
DESCRIPTION_PROMPT_DEFAULT_RU = """
Ты генерируешь продающие описания объектов недвижимости. Пиши фактами, «Х-П-В», без воды.
Сетка: заголовок, локация, дом/двор, планировка, состояние, юр. чистота, кому подходит, CTA.
"""  # <-- замените на полный вариант

# ------------------ Шаблон пользовательского сообщения (укорочен) ------------------
DESCRIPTION_USER_TEMPLATE_RU = """
Сгенерируй продающее описание по анкете. Соблюдай «Х-П-В», без воды, с явным CTA.

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

Если присутствуют дополнительные поля (квартира/загород/коммерция) — логично интегрируй их в текст.
"""  # <-- при необходимости дополните своими плейсхолдерами

# =====================================================================================
# ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ
# =====================================================================================
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
    return {
        # базовые
        "type":        raw.get("type"),
        "apt_class":   raw.get("apt_class"),
        "in_complex":  raw.get("in_complex"),
        "area":        raw.get("area"),
        "comment":     raw.get("comment"),

        # плоскость анкеты
        "total_area":      _first_nonempty(raw.get("total_area")),
        "kitchen_area":    _first_nonempty(raw.get("kitchen_area")),
        # этаж/этажность: принимаем оба варианта
        "building_floors": _first_nonempty(raw.get("floors_total"), raw.get("building_floors")),
        "floor_number":    _first_nonempty(raw.get("floor"), raw.get("floor_number")),
        "rooms":           _first_nonempty(raw.get("rooms")),
        "year_state":      _first_nonempty(raw.get("year_or_condition"), raw.get("year_state")),
        "utilities":       _first_nonempty(raw.get("utilities")),
        "location":        _first_nonempty(raw.get("location_exact"), raw.get("location")),
        "amenities":       _first_nonempty(raw.get("features"), raw.get("amenities")),

        # квартира (если есть)
        "market":            raw.get("market"),
        "completion_term":   raw.get("completion_term"),
        "sale_method":       raw.get("sale_method"),
        "mortgage_ok":       raw.get("mortgage_ok"),
        "bathroom_type":     raw.get("bathroom_type"),
        "windows":           raw.get("windows"),
        "house_type":        raw.get("house_type"),
        "lift":              raw.get("lift"),
        "parking":           raw.get("parking"),
        "renovation":        raw.get("renovation"),
        "layout":            raw.get("layout"),
        "balcony":           raw.get("balcony"),
        "ceiling_height_m":  raw.get("ceiling_height_m"),

        # загород (если есть)
        "country_object_type":        raw.get("country_object_type"),
        "country_house_area_m2":      raw.get("country_house_area_m2"),
        "country_plot_area_sotki":    raw.get("country_plot_area_sotki"),
        "country_distance_km":        raw.get("country_distance_km"),
        "country_floors":             raw.get("country_floors"),
        "country_rooms":              raw.get("country_rooms"),
        "country_land_category_house":raw.get("country_land_category_house"),
        "country_renovation":         raw.get("country_renovation"),
        "country_toilet":             raw.get("country_toilet"),
        "country_utilities":          raw.get("country_utilities"),
        "country_leisure":            raw.get("country_leisure"),
        "country_wall_material":      raw.get("country_wall_material"),
        "country_parking":            raw.get("country_parking"),
        "country_transport":          raw.get("country_transport"),
        "country_land_category_plot": raw.get("country_land_category_plot"),
        "country_communications_plot":raw.get("country_communications_plot"),

        # коммерция (если есть) — просто пропускаем в модель через comment-контекст
        "comm_object_type":  raw.get("comm_object_type"),
        "land_area":         raw.get("land_area"),
        "comm_building_type":raw.get("comm_building_type"),
        "comm_whole_object": raw.get("comm_whole_object"),
        "comm_finish":       raw.get("comm_finish"),
        "comm_entrance":     raw.get("comm_entrance"),
        "comm_parking":      raw.get("comm_parking"),
        "comm_layout":       raw.get("comm_layout"),
    }

# =====================================================================================
# ФАБРИКА ПРОМПТА (Description)
# =====================================================================================
def compose_description_user_message(fields: Dict[str, Any]) -> str:
    """
    Собирает пользовательское сообщение из полей анкеты (с нормализацией).
    """
    t_key = fields.get("type")
    c_key = fields.get("apt_class") if (t_key == "flat") else None
    x_key = fields.get("in_complex")
    a_key = fields.get("area")

    user_payload = {
        "type_label":       _label(DESCRIPTION_TYPES,   t_key),
        "apt_class_label":  _label(DESCRIPTION_CLASSES, c_key) if c_key else "—",
        "in_complex_label": _label(DESCRIPTION_COMPLEX, x_key),
        "area_label":       _label(DESCRIPTION_AREA,    a_key),

        "location":         _safe(fields.get("location")),
        "total_area":       _safe(fields.get("total_area")),
        "kitchen_area":     _safe(fields.get("kitchen_area")),
        "floor_number":     _safe(fields.get("floor_number")),
        "building_floors":  _safe(fields.get("building_floors")),
        "rooms":            _safe(fields.get("rooms")),
        "year_state":       _safe(fields.get("year_state")),
        "utilities":        _safe(fields.get("utilities")),
        "amenities":        _safe(fields.get("amenities")),
        "comment":          _safe(fields.get("comment")),
    }

    # Бонус: если есть спец-поля (квартира/загород/коммерция) — добавим их в конец комментария,
    # что позволит модели их учесть
    return DESCRIPTION_USER_TEMPLATE_RU.format(**user_payload)

# =====================================================================================
# OPENAI CLIENT & SEND
# =====================================================================================
def _send_with_fallback(payload: Dict[str, Any], default_model: str, 
                       allow_fallback: bool = True, api_key: Optional[str] = None) -> Tuple[str, str]:
    """
    Заглушка для отправки запроса в OpenAI. В реальной реализации здесь будет
    клиент OpenAI с обработкой fallback моделей.
    """
    # Здесь должна быть реальная логика отправки в OpenAI
    return "Generated description text", default_model

def send_description_generate_request_from_fields(
    fields: Dict[str, Any],
    model: Optional[str] = None,
    allow_fallback: bool = True,
    api_key: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Основная функция генерации описания из полей анкеты.
    """
    normalized = _normalize_fields(fields)
    user_message = compose_description_user_message(normalized)
    
    payload = {
        "model": model or DESCRIPTION_MODEL,
        "messages": [
            {"role": "system", "content": DESCRIPTION_PROMPT_DEFAULT_RU},
            {"role": "user", "content": user_message}
        ],
        "temperature": 0.7,
        "max_tokens": 1000
    }
    
    return _send_with_fallback(payload, default_model=model or DESCRIPTION_MODEL,
                               allow_fallback=allow_fallback, api_key=api_key)

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

    debug_flag = req.args.get("debug") == "1"
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
    extras: Dict[str, Any] = {}
    for k in (
        "market","completion_term","sale_method","mortgage_ok","bathroom_type","windows",
        "house_type","lift","parking","renovation","layout","balcony","ceiling_height_m",
        "country_object_type","country_house_area_m2","country_plot_area_sotki","country_distance_km",
        "country_floors","country_rooms","country_land_category_house","country_renovation","country_toilet",
        "country_utilities","country_leisure","country_wall_material","country_parking","country_transport",
        "country_land_category_plot","country_communications_plot",
        "comm_object_type","land_area","comm_building_type","comm_whole_object","comm_finish","comm_entrance",
        "comm_parking","comm_layout"
    ):
        v = fields.get(k, None)
        if v not in (None, "", []):
            extras[k] = v

    if extras:
        user_payload["comment"] = (user_payload["comment"] + " | EXTRAS: " +
                                   ", ".join(f"{kk}={_safe(vv)}" for kk, vv in extras.items() if _safe(vv) != "—")).strip()

    return DESCRIPTION_USER_TEMPLATE_RU.format(**user_payload)

def build_description_request_from_fields(*, fields: Dict[str, Any], model: Optional[str] = None) -> Dict[str, Any]:
    """
    ЕДИНОЕ место сборки payload для Chat Completions из полей анкеты.
    """
    normalized = _normalize_fields(fields)
    user_message = compose_description_user_message(normalized)
    use_model = model or DESCRIPTION_MODEL
    payload = {
        "model": use_model,
        "messages": [
            {"role": "system", "content": DESCRIPTION_PROMPT_DEFAULT_RU},
            {"role": "user", "content": user_message},
        ],
        # Температуру/макс. токены можно пробросить при необходимости:
        # "temperature": 0.6,
        # "max_tokens": 1200,
    }
    if HTTP_DEBUG:
        LOG.info("DESCRIPTION payload built (model=%s)", use_model)
    return payload

# =====================================================================================
# OPENAI СЕРВИС (минимальная обёртка с fallback)
# =====================================================================================
try:
    from openai import OpenAI
except Exception:  # пакет может быть не установлен в момент чтения файла
    OpenAI = None  # type: ignore

_FALLBACK_MODELS: List[str] = ["gpt-5", "gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4.1-mini"]

def _extract_text(resp) -> str:
    try:
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""

def _client_or_init(api_key: Optional[str]) -> OpenAI:
    """
    - Если api_key не передан или равен config-ключу — используем и кэшируем общий клиент.
    - Если пришёл свой ключ на запрос — создаём ephemeral-клиент без кэша.
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

def _send_with_fallback(payload: Dict[str, Any], default_model: str, allow_fallback: bool,
                        api_key: Optional[str]) -> Tuple[str, str]:
    client = _client_or_init(api_key)
    first_model = payload.get("model") or default_model
    chain = [first_model] + ([m for m in _FALLBACK_MODELS if m != first_model] if allow_fallback else [])
    last_err: Optional[Exception] = None

    for i, model_name in enumerate(chain, start=1):
        try:
            req = dict(payload); req["model"] = model_name
            _log_request(req)
            resp = client.chat.completions.create(**req)
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

def send_description_generate_request_from_fields(
    fields: Dict[str, Any],
    *,
    model: Optional[str] = None,
    allow_fallback: bool = OPENAI_FALLBACK,
    api_key: Optional[str] = None,
) -> Tuple[str, str]:
    use_model = model or DESCRIPTION_MODEL
    payload = build_description_request_from_fields(fields=fields, model=use_model)
    return _send_with_fallback(payload, default_model=use_model,
                               allow_fallback=allow_fallback, api_key=api_key)