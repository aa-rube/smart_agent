# smart_agent/bot/utils/youmoney.py
import uuid
from typing import Optional, Dict

from yookassa.domain.exceptions.bad_request_error import BadRequestError
from yookassa.domain.exceptions.forbidden_error import ForbiddenError
from yookassa import Configuration, Payment
from bot.config import YOUMONEY_SHOP_ID, YOUMONEY_SECRET_KEY
import bot.utils.billing_db as billing_db

Configuration.account_id = YOUMONEY_SHOP_ID
Configuration.secret_key = YOUMONEY_SECRET_KEY


# НОВЫЙ УНИВЕРСАЛЬНЫЙ МЕТОД (разовые платежи и первичный платёж подписки)
def create_pay_ex(
    user_id: int,
    amount_rub: str,
    description: str,
    metadata: Optional[Dict[str, str]] = None,
    return_url: str = "https://t.me/realtornetworkai_bot",
    save_payment_method: bool = False,
    payment_method_type: Optional[str] = None,  # "bank_card" | "sbp" | None (умный выбор провайдера)
) -> str:
    """
    Создает платёж YooKassa и возвращает confirmation_url.
    amount_rub: строка с 2 знаками после запятой (например, "2500.00")
    payment_method_type: если указан, в тело добавляется payment_method_data = {"type": ...}
      - "bank_card" — для оплаты картой (с привязкой при save_payment_method=True)
      - "sbp"       — для оплаты через СБП (возможность привязки/автосписаний зависит от банка и настроек магазина)
    """
    md = {"user_id": str(user_id)}
    if metadata:
        md.update({k: str(v) for k, v in metadata.items()})

    body = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "description": description[:128],  # на всякий
        "confirmation": {"type": "redirect", "return_url": return_url},
        "metadata": md,
        "receipt": {
            "customer": {"email": "admin@google.com"},
            "items": [{
                "description": description[:128],
                "quantity": "1",
                "amount": {"value": amount_rub, "currency": "RUB"},
                "vat_code": "1",
                "payment_mode": "full_prepayment",
                "payment_subject": "service",
            }]
        },
        "capture": True,
        "save_payment_method": bool(save_payment_method),
    }
    if payment_method_type:
        # Явно фиксируем тип способа оплаты (например, "sbp")
        body["payment_method_data"] = {"type": payment_method_type}

    try:
        payment = Payment.create(body, uuid.uuid4())
    except ForbiddenError as e:
        # пробрасываем как есть — наверху решим, нужен ли фолбэк без сохранения карты
        raise
    except Exception:
        # любые другие ошибки — тоже наверх
        raise
    return payment.confirmation.confirmation_url


# Повторные списания по сохранённому способу оплаты (подписка)
def charge_saved_method(
    *,
    user_id: int,
    payment_method_id: str,
    amount_rub: str,
    description: str,
    metadata: Optional[Dict[str, str]] = None,
    subscription_id: Optional[int] = None,
    record_attempt: bool = True,
) -> str:
    """
    Создаёт повторное списание по сохранённой карте.
    В metadata принудительно добавляются поля:
      - kind=recurring
      - is_recurring=1
      - phase=renewal
      - subscription_id (если передан)

    ВАЖНО (политика ретраев обеспечивается на уровне billing_db.subscriptions_due,
    а запись «created» — ТОЛЬКО в billing_db):
      - не более 2 автосписаний в сутки с минимальным интервалом 12 часов,
      - не более 6 НЕуспешных попыток (canceled/expired) за всё время по подписке.
    Запись о попытке создаётся сразу (status='created'); финальный статус
    помечается по вебхуку (succeeded/canceled/expired).
    """
    md = {
        "user_id": str(user_id),
        "kind": "recurring",
        "is_recurring": "1",
        "phase": "renewal",
    }
    if metadata:
        md.update({k: str(v) for k, v in metadata.items()})
    if subscription_id is not None:
        md["subscription_id"] = str(subscription_id)

    body = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "capture": True,
        "payment_method_id": payment_method_id,
        "description": description[:128],
        "metadata": md,
    }
    payment = Payment.create(body, uuid.uuid4())
    # запись попытки теперь опциональна (по умолчанию True для обратной совместимости)
    if record_attempt:
        try:
            if subscription_id is not None:
                billing_db.record_charge_attempt(
                    subscription_id=subscription_id,
                    user_id=user_id,
                    payment_id=payment.id,
                    status="created",
                )
        except Exception:
            pass
    return payment.id