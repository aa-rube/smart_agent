#C:\Users\alexr\Desktop\dev\super_bot\smart_agent\executor\apps\description_generate.py
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
import bot.utils.logging_config as logging_config

log = logging_config.logger


try:
    from openai import OpenAI
except Exception:
    OpenAI = None

_FALLBACK_MODELS: List[str] = ["gpt-5", "gpt-4o", "gpt-4.1", "gpt-4o-mini", "gpt-4.1-mini"]

_client_default: Any = None


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
            req = dict(payload)
            req["model"] = model_name
            
            # Логируем промпт перед отправкой в OpenAI
            if "messages" in req:
                log.info("OpenAI prompt: %s", json.dumps(req["messages"], ensure_ascii=False, indent=2))
            resp = client.chat.completions.create(**req)
            text = _extract_text(resp)
            if text:
                if i > 1:
                    log.warning("Fallback model used: %s (requested %s)", model_name, first_model)
                return text, model_name
            last_err = RuntimeError("Empty completion text")
        except Exception as e:
            last_err = e
            log.warning("OpenAI call failed on model %s: %s", model_name, e)

    log.error("All OpenAI fallbacks failed. Last error: %s", last_err)
    raise last_err or RuntimeError("OpenAI request failed")


# =========================
# Конфиг / Логгер
# =========================
HTTP_DEBUG = os.getenv("HTTP_DEBUG", "0") == "1"
OPENAI_FALLBACK = os.getenv("OPENAI_FALLBACK", "1") == "1"

# Базовая модель
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


# ------------------ Специализированные USER-TEMPLATE по типам ------------------
# Квартира — ПРОДАЖА
DESCRIPTION_USER_TEMPLATE_FLAT_SALE_RU = """
Сгенерируй объявление о ПРОДАЖЕ КВАРТИРЫ. Если какое-либо поле равно «—» или пусто — просто пропусти соответствующую мысль/строку.

1) Короткий лид (1–2 предложения) — ощущение пространства/света/тишины по фактам.
2) Смысловые маркеры (в тексте, без списков): факты → выгоды.
3) Микро-блоки (только если есть данные):

— Планировка: общая {total_area} м², кухня {kitchen_area} м², комнаты {rooms}, этаж {floor_number}/{building_floors}, потолки {ceiling_height_m} м, планировка {layout}, балкон/лоджия {balcony}.

— Дом/ЖК/материал: {in_complex_label}, тип: {type_label} {apt_class_label}, материал {house_type}, лифты {lift}, окна {windows}, санузел {bathroom_type}, парковка {parking}, отделка {renovation}.

— Локация: {location}; (текст) адрес/ориентиры: {flat_location_text}; (текст) инфраструктура: {flat_infrastructure_text}; общая зона: {area_label}.
4) Условия сделки: сделка {deal_label}, способ продажи {sale_method}, срок передачи/сдачи {completion_term}, ипотека {mortgage_ok}, юр.особенности: {flat_legal_text}.
5) Комментарий: {comment}
"""

# Квартира — АРЕНДА
DESCRIPTION_USER_TEMPLATE_FLAT_RENT_RU = """
Сгенерируй готовое объявление (квартира, АРЕНДА) по данным ниже, соблюдая правила из системного промпта.
Если какое-либо поле равно «—» или пусто — просто пропусти соответствующую мысль/строку/блок.

Композиция:
1) Заголовок (до ~70 символов) — польза/сценарий жизни.
2) Лид-абзац — комфорт, тишина, быт, транспорт/инфраструктура.
3) 4–6 выгод-маркеров — «факт → польза жильцу».
4) Микро-блоки (только при наличии фактов):
— Планировка: {total_area} м², кухня {kitchen_area} м², комнаты {rooms}, этаж {floor_number}/{building_floors}, особенности {amenities}.
— Дом/ЖК: {in_complex_label}, класс/тип: {type_label} {apt_class_label}.
— Локация: {location} (и «{area_label}», если важно).
— Адрес/ориентиры (текст): {flat_location_text}
— Инфраструктура рядом (текст): {flat_infrastructure_text}
5) Условия аренды
— Коротко: срок/залог/коммунальные/правила. Не упоминай ипотеку.
6) CTA на просмотр/созвон.

Данные анкеты (используй только по смыслу, без вывода «—»):
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
— (Текст) Локация: {flat_location_text}
— (Текст) Инфраструктура: {flat_infrastructure_text}
"""

# Загородная недвижимость
DESCRIPTION_USER_TEMPLATE_COUNTRY_RU = """
Сгенерируй готовое объявление (загородная недвижимость) по данным ниже, соблюдая правила из системного промпта.
Если какое-либо поле равно «—» или пусто — просто пропусти соответствующую мысль/строку/блок.

Композиция:
1) Заголовок — образ жизни/пространство/возможности участка.
2) Лид — воздух/тишина/планировка/подъездность (по фактам).
3) 4–6 выгод-маркеров — дом, участок, коммуникации, окружение, транспорт.
4) Микро-блоки:
— Дом/Планировка: площадь {total_area} м², комнаты {rooms}, состояние {year_state}, особенности {amenities}.
— Участок/Коммуникации: {utilities}.
— Локация/Доступ: {location} ({area_label}, если важно).
5) Условия сделки — коротко, по делу.
6) CTA — просмотр/созвон.

Данные анкеты (используй только по смыслу, без вывода «—»):
— Тип: {type_label}
— Расположение (общее): {area_label}
— Локация (район/трасса/ориентиры): {location}
— Площадь дома (если есть): {total_area} м²
— Комнат: {rooms}
— Состояние/год: {year_state}
— Коммуникации: {utilities}
— Особенности: {amenities}
— Комментарий: {comment}
"""

# Коммерческая недвижимость
DESCRIPTION_USER_TEMPLATE_COMMERCIAL_RU = """
Сгенерируй готовое объявление (коммерческая недвижимость) по данным ниже, соблюдая правила из системного промпта.
Если какое-либо поле равно «—» или пусто — просто пропусти соответствующую мысль/строку/блок.

Композиция:
1) Заголовок — польза для бизнеса (трафик/витрины/доступ).
2) Лид — формат помещения и ключевые выгоды для арендатора/покупателя.
3) 4–6 маркеров — локация/поток, планировка, высота, входные группы, парковка, окружение (по фактам).
4) Микро-блоки:
— Помещение: ориентир на площадь {total_area} м², особенности {amenities}.
— Здание/БЦ: {in_complex_label} / {type_label}.
— Локация/Доступ: {location} ({area_label}, если важно), коммуникации {utilities}.
5) Условия сделки (аренда/продажа) — НДС/каникулы/формат обсуждения только если факты есть.
6) CTA — просмотр/бриф/контакт.

Данные анкеты (используй только по смыслу, без вывода «—»):
— Тип: {type_label}
— Расположение (общее): {area_label}
— Локация: {location}
— Площадь (если есть): {total_area} м²
— Состояние/особенности: {year_state}; {amenities}
— Коммуникации: {utilities}
— Комментарий: {comment}
"""

# Fallback
DESCRIPTION_USER_TEMPLATE_RU = """
Сгенерируй готовое объявление по данным ниже, соблюдая правила из системного промпта.
Если какое-либо поле равно «—» или пусто — просто пропусти соответствующую мысль/строку/блок.

Требуемая композиция (не как формальная анкета, а как продающий текст):

1) Заголовок (до ~70 символов)
— 1 ключевая выгода, без клише и без кавычек.

2) Лид-абзац (1–3 предложения)
— Сборка главных смыслов: метраж/планировка/этажность → ощущение пространства/света → для кого это идеально.

3) 4–6 выгоды-маркеров
— Каждый маркер = «факт → зачем это покупателю». Старайся варьировать формулировки.
— Диапазоны и коды превращай в смысл («1–5 этажность» → «невысокая этажность» и т.п.).

4) Микро-блоки (выводи только те, где есть факты)
— Планировка: общая площадь {total_area} м², кухня {kitchen_area} м², комнаты {rooms}, этаж {floor_number} / {building_floors}, особенности {amenities}.
— Дом/ЖК: {in_complex_label}, класс/тип: {type_label} {apt_class_label}.
— Локация: {location} (и «{area_label}», если это несёт смысл).

5) Условия сделки (коротко, по делу)
— Сделка: {deal_label}. Если есть ипотека/срок сдачи/способ продажи/иное в EXTRAS — упомяни, но без перегруза.
— Избегай перечислений «без обременений/перепланировок», если таких фактов нет.

6) CTA
— 1–2 предложения. Конкретика: созвон/просмотр/доп фото. Никакого давления.

Данные анкеты (используй только по смыслу, без вывода «—»):
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

Напоминание:
— Никаких пустых «шапок» и заглушек. Блок существует только если есть смысловые данные.
— Переводи вводные коды/ярлыки (например, «несколько балконов», «окна на солнечную сторону») в пользу для покупателя.
— Итог — цельный продающий текст, который можно сразу копировать в объявление.
"""


# ------------------ Специализированные системные промпты ------------------
# Квартира — ПРОДАЖА
DESCRIPTION_PROMPT_FLAT_SALE_RU = """
Ты — профессиональный копирайтер и маркетолог в сфере недвижимости.
Твоя задача - по предоставленным характеристикам объекта составить уникальное, художественное и продающее описание, которое вызывает эмоции и помогает покупателю представить себя владельцем этого объекта.

Правила генерации описания:
 1. Твоя цель - превратить характеристики в преимущества и выгоды.
 • Характеристики - это факты: площадь, этаж, планировка, вид из окна.
 • Преимущества- это, чем этот объект лучше других.
 • Выгоды - это, что получит покупатель (комфорт, статус, удобство, безопасность и т. д.).
 2. Пиши единым связным текстом, без списков, нумерации и буллетов.
Текст должен звучать естественно, как описание из рекламного каталога премиум-класса или storytelling в журнале о недвижимости.
 3. Избегай клише и шаблонных фраз вроде «дом вашей мечты» или «идеальное место для жизни».
Вместо этого описывай ощущения, атмосферу, образ жизни и реальную ценность объекта.
 4. Пиши в позитивном ключе, с лёгким эмоциональным оттенком, но без пафоса.
Основная цель - заинтересовать и вызвать желание узнать подробнее.
 5. В описании не должно быть технических данных в виде списка- только плавное вплетение характеристик в контекст.
Например, вместо:
«Квартира площадью 75 кв.м., 3 этаж, вид на парк»
напиши:
«Просторная квартира с окнами, из которых открывается вид на зелень городского парка. Тёплый свет заливает гостиную, создавая атмосферу уюта и спокойствия».
 6. Если пользователь добавил дополнительный комментарий в свободной форме — используй его, чтобы усилить индивидуальность описания.
 7. Формат вывода:
Один связный абзац художественного текста длиной 700–1000 символов."""

# Квартира — АРЕНДА
DESCRIPTION_PROMPT_FLAT_RENT_RU = """
Ты — копирайтер по недвижимости. Пишем объявление об аренде квартиры.

Правила:
1) «Факт → комфорт/решение задачи». Подчёркивай быт, тишину, удобства, транспорт.
2) Тон: дружелюбный, практичный. Без клише, без давления.
3) Структура: заголовок → лид → 4–6 маркеров выгод → микро-блоки (Планировка/Дом/Локация) → Условия аренды (срок, залог, коммунальные, правила) → CTA на просмотр.
4) Не упоминай ипотеку/инвестиции. Ничего не выдумывай; пустые поля не выводи.
5) Числа: м², этаж/этажность. Диапазоны переосмысливай.
6) Объём ~900–1400 знаков.
Выводи сразу готовый текст.
"""

# Загородная недвижимость (дом/дача/участок)
DESCRIPTION_PROMPT_COUNTRY_RU = """
Ты — копирайтер по загородной недвижимости. Покажи образ жизни: пространство, воздух, участок, коммуникации.

Правила:
1) Факты переводим в выгоды: площадь дома/участка, категория земли, коммуникации, подъезд, расстояние, окружение.
2) Тон: вдохновляющий, но конкретный. Без штампов.
3) Структура: заголовок → лид → 4–6 выгод-маркеров → микро-блоки (Дом/Планировка/Участок/Локация — только при наличии данных) → Условия сделки → CTA.
4) Ничего не выдумывай; пустые блоки не выводи.
5) Объём ~900–1400 знаков.
Сразу выводи готовый текст.
"""

# Коммерческая недвижимость (офис/street retail/склад и пр.)
DESCRIPTION_PROMPT_COMMERCIAL_RU = """
Ты — копирайтер по коммерческой недвижимости. Фокус на бизнес-задачи: трафик, доступность, планировка, мощности, витрины.

Правила:
1) Выгоды через факты: локация/поток, транспорт/парковка, высота потолков, входные группы, планировка, нагрузка/мощность, витринность.
2) Тон: деловой и предметный, без маркетинговых штампов.
3) Структура: заголовок → лид → 4–6 маркеров → микро-блоки (Помещение/Здание/Локация — если есть данные) → Условия сделки (аренда/продажа, НДС, каникулы, e.t.c.) → CTA (просмотр/бриф).
4) Не выдумывай; пустые поля не выводи.
5) Объём ~900–1400 знаков.
Выводи готовый текст без служебных пояснений.
"""

# Fallback
DESCRIPTION_PROMPT_DEFAULT_RU = """
Ты — сильный копирайтер по недвижимости. Твоя задача — быстро влюблять читателя в объект
и продавать выгоды владения (не просто перечислять факты).

Жёсткие правила:
1) Пиши живо, простым человеческим языком. Короткие фразы, «воздух», без канцелярита.
2) Каждый факт → формулируй выгоду. Не «потолки 3.2 м», а «высокие потолки — больше света и воздуха».
3) Не выдумывай. Если данных нет или стоит «—», просто опусти это место без заглушек.
4) Никаких общих фраз «дом хороший/квартира уютная». Никаких штампов «лучшее предложение рынка».
5) Структура компактная: цепляющий заголовок → сильный лид-абзац (1–3 предложения) → 4–6 выгод-маркеров →
   1–3 микро-блока по теме (Планировка, Дом/ЖК, Локация — только если есть данные) → Условия сделки → мощный CTA.
6) Пиши «вы», не «вы с большой буквы». Без капса, без множества восклицательных знаков.
7) Ничего лишнего: если значение «—» или пусто — не выводи строку/маркер/блок.
8) Числа с единицами: м², этаж / этажность. Диапазоны переосмысливай по смыслу (например, «невысокая этажность»).
9) Тон на продажу для «Продажа» и на решение задачи/комфорта для «Аренда».
10) Длина целевого текста: ~900–1400 знаков (не обрезай мысли, но держи темп).

Памятка о стиле:
— Дай читателю картинку: свет, воздух, тишина, планировка — через пользу. 
— Баланс: эмоция + конкретика. Ни грамма фантазий сверх данных.
— Меньше разделителей и подзаголовков-«шапок»; вместо этого — микро-блоки с короткими подзаголовками без эмодзи.

Выводи сразу готовый текст объявления, без технических пояснений и без заглушек типа «если применимо».
"""


# =====================================================================================
# ВСПОМОГАТЕЛЬНЫЕ УТИЛИТЫ
# =====================================================================================
def _sanitize_format_template(s: str) -> str:
    """
    Делает шаблон безопасным для str.format:
      — для плейсхолдера {name:...} удаляет формат-часть (включая вложенные скобки) -> {name}
      — экранирует одиночные '{' и '}' вне валидных плейсхолдеров -> '{{' / '}}'
    Допустимое имя: [a-zA-Z_][a-zA-Z0-9_]*
    """
    out: list[str] = []
    i = 0
    n = len(s)
    ident_re = re.compile(r'[a-zA-Z_][a-zA-Z0-9_]*')
    while i < n:
        ch = s[i]
        if ch == '{':
            # Экранированные "{{"
            if i + 1 < n and s[i + 1] == '{':
                out.append('{{'); i += 2; continue
            # Пытаемся разобрать {identifier ...}
            j = i + 1
            m = ident_re.match(s, j)
            if not m:
                # Не валидное начало плейсхолдера — экранируем
                out.append('{{'); i += 1; continue
            name_end = m.end()
            name = s[j:name_end]
            if name_end < n and s[name_end] == '}':
                # Простой {name}
                out.append('{'); out.append(name); out.append('}')
                i = name_end + 1; continue
            if name_end < n and s[name_end] == ':':
                # {name: ...} — нужно пропустить формат-часть с учётом вложенных скобок
                k = name_end + 1
                depth = 0
                while k < n:
                    if s[k] == '{':
                        depth += 1
                    elif s[k] == '}':
                        if depth == 0:
                            # конец формат-части
                            out.append('{'); out.append(name); out.append('}')
                            k += 1
                            break
                        depth -= 1
                    k += 1
                else:
                    # Не нашли закрывающую — экранируем исходную '{'
                    out.append('{{'); i += 1; continue
                i = k; continue
            # Иной символ после имени — не формат, экранируем '{'
            out.append('{{'); i += 1; continue
        elif ch == '}':
            # Экранированные "}}"
            if i + 1 < n and s[i + 1] == '}':
                out.append('}}'); i += 2; continue
            # Одиночная закрывающая — экранируем
            out.append('}}'); i += 1; continue
        else:
            out.append(ch); i += 1
    return ''.join(out)

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

        # новые текстовые поля (квартира)
        "flat_location_text": raw.get("flat_location_text"),
        "flat_infrastructure_text": raw.get("flat_infrastructure_text"),
        "flat_legal_text": raw.get("flat_legal_text"),

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

    # 4) Деривации по умолчанию для неполных анкет
    # 4.1) Если это квартира и указан «market=Новостройка», но не задано in_complex — подставим "yes"
    if (norm.get("type") or "").strip().lower() == "flat" and not norm.get("in_complex"):
        if str(raw.get("market") or "").strip().lower() in {"новостройка", "new", "newbuild", "новая"}:
            norm["in_complex"] = "yes"

    # 4.2) Если нет «location», но есть развернутая «flat_location_text» — используем её как локацию
    if not norm.get("location") and raw.get("flat_location_text"):
        loc = str(raw.get("flat_location_text")).strip()
        if loc:
            norm["location"] = loc

    # защита от «ипотека/право» при аренде: юр.текст имеет смысл только для продажи
    if (norm.get("deal_type") or "").strip().lower() != "sale":
        # если прилетело по ошибке — вычистим, чтобы не попадало в шаблон
        norm["flat_legal_text"] = None
    return norm


# =====================================================================================
# ФАБРИКА ПРОМПТА (Description)
# =====================================================================================

def _select_description_user_template(fields: Dict[str, Any]) -> str:
    """
    Выбор пользовательского TEMPLATE по типу/сделке:
     - flat + sale  -> DESCRIPTION_USER_TEMPLATE_FLAT_SALE_RU
     - flat + rent  -> DESCRIPTION_USER_TEMPLATE_FLAT_RENT_RU
     - country/zagorod -> DESCRIPTION_USER_TEMPLATE_COUNTRY_RU
     - commercial/comm/office/land -> DESCRIPTION_USER_TEMPLATE_COMMERCIAL_RU
     - иначе -> DESCRIPTION_USER_TEMPLATE_RU (универсальный)
    """
    t = str(fields.get("type") or "").strip().lower()
    deal = str(fields.get("deal_type") or "").strip().lower()
    
    if t == "flat":
        return DESCRIPTION_USER_TEMPLATE_FLAT_RENT_RU if deal == "rent" else DESCRIPTION_USER_TEMPLATE_FLAT_SALE_RU
    
    if t in {"country", "zagorod"}:
        return DESCRIPTION_USER_TEMPLATE_COUNTRY_RU
    
    if t in {"commercial", "comm", "office", "land"}:
        return DESCRIPTION_USER_TEMPLATE_COMMERCIAL_RU
    
    return DESCRIPTION_USER_TEMPLATE_RU
def _select_description_prompt(fields: Dict[str, Any]) -> str:
    """
    Выбор системного промпта по типу/сделке:
     - flat + sale  -> DESCRIPTION_PROMPT_FLAT_SALE_RU
     - flat + rent  -> DESCRIPTION_PROMPT_FLAT_RENT_RU
     - country/zagorod -> DESCRIPTION_PROMPT_COUNTRY_RU
     - commercial/comm/office/land -> DESCRIPTION_PROMPT_COMMERCIAL_RU
     - иначе -> DESCRIPTION_PROMPT_DEFAULT_RU
    """
    t = str(fields.get("type") or "").strip().lower()
    deal = str(fields.get("deal_type") or "").strip().lower()
    
    if t == "flat":
        if deal == "rent":
            return DESCRIPTION_PROMPT_FLAT_RENT_RU
        # по умолчанию для квартиры — продажа
        return DESCRIPTION_PROMPT_FLAT_SALE_RU
    
    if t in {"country", "zagorod"}:
        return DESCRIPTION_PROMPT_COUNTRY_RU
    
    if t in {"commercial", "comm", "office", "land"}:
        # land: по умолчанию трактуем как коммерческий сценарий (участок под бизнес),
        # при необходимости можно выделить отдельный промпт.
        return DESCRIPTION_PROMPT_COMMERCIAL_RU
    
    return DESCRIPTION_PROMPT_DEFAULT_RU

def build_description_request_from_fields(*, fields: Dict[str, Any], model: Optional[str] = None) -> Dict[str, Any]:
    """
    Создает payload для OpenAI запроса из полей анкеты.
    """
    normalized = _normalize_fields(fields)
    user_message = compose_description_user_message(normalized)
    use_model = model or DESCRIPTION_MODEL
    system_prompt = _select_description_prompt(normalized)

    payload = {
        "model": use_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
    }
    return payload


def compose_description_user_message(fields: Dict[str, Any]) -> str:
    """
    Собирает пользовательское сообщение из полей анкеты (с нормализацией).
    """
    t_key = fields.get("type")
    deal_type_raw = str(fields.get("deal_type") or "").strip().lower()
    c_key = fields.get("apt_class") if (t_key == "flat") else None
    x_key = fields.get("in_complex")
    a_key = fields.get("area")

    deal_label = {"sale": "Продажа", "rent": "Аренда"}.get(str(fields.get("deal_type") or "").strip(), "—")

    # Выбираем USER-TEMPLATE согласно типу/сделке
    user_template = _select_description_user_template(fields)

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
        # нов. текстовые поля (квартира)
        "flat_location_text": _safe(fields.get("flat_location_text")),
        "flat_infrastructure_text": _safe(fields.get("flat_infrastructure_text")),
        "flat_legal_text": _safe(fields.get("flat_legal_text")),

        # ПРОДАЖА/квартира — явно передаём в шаблон, а не только через EXTRAS
        "market": _safe(fields.get("market")),
        "completion_term": _safe(fields.get("completion_term")),
        "sale_method": _safe(fields.get("sale_method")),
        "mortgage_ok": (_safe(fields.get("mortgage_ok")) if deal_type_raw == "sale" else "—"),

        # дополнительные «понятные» параметры квартиры, чтобы шаблон мог их встроить
        "bathroom_type": _safe(fields.get("bathroom_type")),
        "windows": _safe(fields.get("windows")),
        "house_type": _safe(fields.get("house_type")),
        "lift": _safe(fields.get("lift")),
        "parking": _safe(fields.get("parking")),
        "renovation": _safe(fields.get("renovation")),
        "layout": _safe(fields.get("layout")),
        "balcony": _safe(fields.get("balcony")),
        "ceiling_height_m": _safe(fields.get("ceiling_height_m")),
    }

    # Добираем дополнительные поля (квартира/загород/коммерция) — в EXTRAS,
    # чтобы ассистент мог использовать их, не перегружая основную сетку.
    extras: Dict[str, Any] = {}
    # Базовый набор ключей для EXTRAS
    extras_keys = [
        # квартира
        "market", "completion_term", "sale_method",
        # "mortgage_ok" — ниже добавляем только для продажи
        "bathroom_type", "windows", "house_type", "lift", "parking",
        "renovation", "layout", "balcony", "ceiling_height_m",
        # загород
        "country_object_type", "country_house_area_m2", "country_plot_area_sotki", "country_distance_km",
        "country_floors", "country_rooms", "country_land_category_house", "country_renovation", "country_toilet",
        "country_utilities", "country_leisure", "country_wall_material", "country_parking", "country_transport",
        "country_land_category_plot", "country_communications_plot",
        # коммерция
        "comm_object_type", "land_area", "comm_building_type", "comm_whole_object", "comm_finish", "comm_entrance",
        "comm_parking", "comm_layout",
    ]
    if deal_type_raw != "rent":
        extras_keys.insert(3, "mortgage_ok")  # разрешаем ипотеку только для продажи

    for k in extras_keys:
        v = fields.get(k, None)
        if v not in (None, "", [], "—"):
            extras[k] = v

    if extras:
        extras_str = ", ".join(f"{kk}={_safe(vv)}" for kk, vv in extras.items() if _safe(vv) != "—")
        user_payload["comment"] = (
                    user_payload["comment"] + ((" | EXTRAS: " + extras_str) if extras_str else "")).strip()

    # NB: Текстовые поля локации/инфраструктуры/юридических особенностей уже включены
    # в основной шаблон через плейсхолдеры; не дублируем их в EXTRAS.
    # (Если потребуется, можно добавлять в EXTRAS отдельным флагом/настройкой.)

    # Добавим сделку и выбранный TEMPLATE
    msg = ""
    msg += f"— Сделка: {user_payload['deal_label']}\n"
    msg += user_template
    # Санитизируем шаблон: убираем формат-спеки и экранируем одиночные скобки
    tmpl = _sanitize_format_template(msg)
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
    log.info("Sending callback to URL: %s", callback_url)
    log.info("Callback payload: %s", json.dumps(payload, ensure_ascii=False, indent=2))
    try:
        # небольшая валидация URL
        pr = urlparse(callback_url)
        if pr.scheme not in {"http", "https"}:
            raise ValueError("callback_url must be http/https")
        headers = {"Content-Type": "application/json"}
        response = requests.post(callback_url, data=json.dumps(payload), headers=headers, timeout=30)
        log.info("Callback sent successfully, status: %s, response: %s", response.status_code, response.text)
    except Exception as e:
        log.warning("Callback POST failed: %s", e)


# =====================================================================================
# PUBLIC ENTRYPOINT for thin controller
# =====================================================================================
def description_generate(req: Request):
    """
    Тонкий вход: разбираем запрос (JSON/form), берём per-request API ключ (если есть),
    вызываем локальный OpenAI-сервис и возвращаем Flask-совместимый ответ.
    Контроллер просто делегирует сюда: return description_module.description_generate(request).
    """
    # Логируем входящие данные по API
    log.info("API request received - Method: %s, URL: %s", req.method, req.url)
    log.info("Request headers: %s", dict(req.headers))
    
    # мягкая проверка конфигурации: если нет env-ключа и не передан per-request ключ — 500
    issues = validate_config()
    data = req.get_json(silent=True) or {}
    form = req.form or {}
    
    # Логируем данные запроса
    log.info("Request JSON data: %s", json.dumps(data, ensure_ascii=False, indent=2))
    log.info("Request form data: %s", dict(form))
    log.info("Request args: %s", dict(req.args))

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

    # Собираем поля анкеты. Поддерживаем оба формата:
    # 1) плоский: {"type": "...", "deal_type": "...", ...}
    # 2) вложенный: {"fields": {...}, "callback_url": "...", ...}  ← именно так шлёт бот
    fields: Dict[str, Any] = {}
    if isinstance(data, dict):
        if isinstance(data.get("fields"), dict):   # ← новый корректный путь
            fields.update(data.get("fields"))
        else:
            fields.update(data)
    for k in form.keys():
        fields[k] = form.get(k)

    # Логируем именно распакованные поля анкеты (без служебных ключей)
    log.info("Collected fields (normalized): %s", json.dumps(fields, ensure_ascii=False, indent=2))

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
            log.info("Starting async description generation for chat_id=%s, msg_id=%s", chat_id, msg_id)
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
                    # полезно для истории в боте (он умеет принять fields)
                    "fields": fields,
                }
                log.info("Async generation completed successfully, sending callback to: %s", callback_url)
                log.info("Callback payload: %s", json.dumps(payload, ensure_ascii=False, indent=2))
                _post_callback(callback_url, payload)
            except Exception as e:
                log.exception("OpenAI error (description, async)")
                payload = {
                    "chat_id": chat_id,
                    "msg_id": msg_id,
                    "text": "",
                    "error": str(e),
                    "token": callback_token or "",
                    "fields": fields,
                }
                log.info("Async generation failed, sending error callback: %s", json.dumps(payload, ensure_ascii=False, indent=2))
                _post_callback(callback_url, payload)

        threading.Thread(target=_bg, daemon=True).start()
        # Быстрый ACK, чтобы бот не «ждал»
        log.info("Async request accepted, returning 202")
        return jsonify({"accepted": True}), 202

    # Обычный синхронный режим (совместимость)
    log.info("Starting sync description generation")
    try:
        text, used_model = send_description_generate_request_from_fields(
            fields=fields,
            allow_fallback=True,
            api_key=api_key,
        )
        body: Dict[str, Any] = {"text": text}
        if debug_flag:
            body["debug"] = {"model_used": used_model}
        log.info("Sync generation completed successfully, response: %s", json.dumps(body, ensure_ascii=False, indent=2))
        return jsonify(body), 200
    except Exception as e:
        log.exception("OpenAI error (description)")
        body: Dict[str, Any] = {"error": "openai_error", "detail": str(e)}
        if debug_flag:
            body["debug"] = {"model": DESCRIPTION_MODEL}
        log.info("Sync generation failed, error response: %s", json.dumps(body, ensure_ascii=False, indent=2))
        return jsonify(body), 502