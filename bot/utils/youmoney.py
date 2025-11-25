# smart_agent/bot/utils/youmoney.py
import uuid
import logging
from typing import Optional, Dict
from decimal import Decimal, InvalidOperation

from yookassa.domain.exceptions.bad_request_error import BadRequestError
from yookassa.domain.exceptions.forbidden_error import ForbiddenError
from yookassa import Configuration, Payment
from bot.config import YOUMONEY_SHOP_ID, YOUMONEY_SECRET_KEY
import bot.utils.billing_db as billing_db

logger = logging.getLogger(__name__)

Configuration.account_id = YOUMONEY_SHOP_ID
Configuration.secret_key = YOUMONEY_SECRET_KEY


def validate_amount(amount_rub: str) -> None:
    """
    Валидирует формат и значение суммы платежа.
    Выбрасывает ValueError при невалидных данных.
    """
    if not amount_rub or not isinstance(amount_rub, str):
        raise ValueError(f"Amount must be a non-empty string, got: {type(amount_rub)}")
    
    try:
        amount_decimal = Decimal(amount_rub)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Invalid amount format: {amount_rub}. Must be a decimal number (e.g., '2500.00')") from e
    
    # Проверка формата: должен быть в формате XX.XX (2 знака после запятой)
    parts = amount_rub.split('.')
    if len(parts) == 2 and len(parts[1]) > 2:
        raise ValueError(f"Amount must have at most 2 decimal places, got: {amount_rub}")
    
    # Проверка диапазона: минимум 0.01, максимум 1,000,000 (разумный лимит)
    if amount_decimal < Decimal("0.01"):
        raise ValueError(f"Amount must be at least 0.01, got: {amount_rub}")
    if amount_decimal > Decimal("1000000.00"):
        raise ValueError(f"Amount exceeds maximum limit of 1,000,000.00, got: {amount_rub}")


def validate_payment_method_id(payment_method_id: str) -> None:
    """
    Валидирует payment_method_id перед использованием.
    Выбрасывает ValueError при невалидных данных.
    """
    if not payment_method_id:
        raise ValueError("payment_method_id cannot be None or empty")
    
    if not isinstance(payment_method_id, str):
        raise ValueError(f"payment_method_id must be a string, got: {type(payment_method_id)}")
    
    # Минимальная длина токена (обычно токены YooKassa длиннее)
    if len(payment_method_id) < 10:
        raise ValueError(f"payment_method_id seems too short (length: {len(payment_method_id)})")


# НОВЫЙ УНИВЕРСАЛЬНЫЙ МЕТОД (разовые платежи и первичный платёж подписки)
def create_pay_ex(
    user_id: int,
    amount_rub: str,
    description: str,
    metadata: Optional[Dict[str, str]] = None,
    return_url: str = "https://t.me/realtornetworkai_bot",
    save_payment_method: bool = False,
    # Явно указать тип метода оплаты. Поддерживаем 'bank_card' и 'sbp'.
    # Если None — даём провайдеру предложить все доступные способы.
    payment_method_type: Optional[str] = None,
    customer_email: Optional[str] = None,
) -> str:
    """
    Создает платёж YooKassa и возвращает confirmation_url.
    amount_rub: строка с 2 знаками после запятой (например, "2500.00")
    payment_method_type: 'bank_card' | 'sbp' | None
    customer_email: опциональный email для чека (если None, используется значение по умолчанию)
    """
    # Валидация суммы перед созданием платежа
    validate_amount(amount_rub)
    
    md = {"user_id": str(user_id)}
    if metadata:
        md.update({k: str(v) for k, v in metadata.items()})

    # Используем переданный email или значение по умолчанию
    receipt_email = customer_email or "admin@google.com"
    
    body = {
        "amount": {"value": amount_rub, "currency": "RUB"},
        "description": description[:128],  # на всякий
        "confirmation": {"type": "redirect", "return_url": return_url},
        "metadata": md,
        "receipt": {
            "customer": {"email": receipt_email},
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
    # Принудительно ограничиваем способ оплаты, если явно попросили
    if payment_method_type in ("bank_card", "sbp"):
        body["payment_method_data"] = {
            "type": payment_method_type
        }

    try:
        payment = Payment.create(body, uuid.uuid4())
    except BadRequestError as e:
        logger.error("BadRequestError creating payment for user %s: %s", user_id, e)
        raise ValueError(f"Payment validation error: {str(e)}") from e
    except ForbiddenError as e:
        # пробрасываем как есть — наверху решим, нужен ли фолбэк без сохранения карты
        logger.warning("ForbiddenError creating payment for user %s: %s", user_id, e)
        raise
    except Exception as e:
        logger.exception("Unexpected error creating payment for user %s: %s", user_id, e)
        raise
    
    # Валидация confirmation_url перед возвратом
    if not payment.confirmation:
        raise ValueError("Payment created but confirmation is missing")
    
    confirmation_url = payment.confirmation.confirmation_url
    if not confirmation_url:
        raise ValueError("Payment created but confirmation_url is missing")
    
    if not isinstance(confirmation_url, str) or not (confirmation_url.startswith("http://") or confirmation_url.startswith("https://")):
        raise ValueError(f"Invalid confirmation_url format: {confirmation_url}")
    
    return confirmation_url


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
    attempt_id: Optional[int] = None,
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
    
    attempt_id: опциональный ID попытки для пометки как failed при ошибке
    """
    # Валидация входных данных
    validate_payment_method_id(payment_method_id)
    validate_amount(amount_rub)
    
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
    
    try:
        payment = Payment.create(body, uuid.uuid4())
        payment_id = payment.id
    except BadRequestError as e:
        logger.error(
            "BadRequestError creating recurring charge for user %s, subscription %s: %s",
            user_id, subscription_id, e
        )
        # Помечаем попытку как failed, если attempt_id передан
        if attempt_id is not None:
            try:
                billing_db.mark_charge_attempt_status(
                    subscription_id=subscription_id,
                    status="failed"
                )
            except Exception as mark_error:
                logger.warning("Failed to mark attempt %s as failed: %s", attempt_id, mark_error)
        raise ValueError(f"Payment validation error: {str(e)}") from e
    except ForbiddenError as e:
        logger.error(
            "ForbiddenError creating recurring charge for user %s, subscription %s: %s",
            user_id, subscription_id, e
        )
        # Помечаем попытку как failed, если attempt_id передан
        if attempt_id is not None:
            try:
                billing_db.mark_charge_attempt_status(
                    subscription_id=subscription_id,
                    status="failed"
                )
            except Exception as mark_error:
                logger.warning("Failed to mark attempt %s as failed: %s", attempt_id, mark_error)
        raise
    except Exception as e:
        logger.exception(
            "Unexpected error creating recurring charge for user %s, subscription %s: %s",
            user_id, subscription_id, e
        )
        # Помечаем попытку как failed, если attempt_id передан
        if attempt_id is not None:
            try:
                billing_db.mark_charge_attempt_status(
                    subscription_id=subscription_id,
                    status="failed"
                )
            except Exception as mark_error:
                logger.warning("Failed to mark attempt %s as failed: %s", attempt_id, mark_error)
        raise
    
    # запись попытки теперь опциональна (по умолчанию True для обратной совместимости)
    if record_attempt:
        try:
            if subscription_id is not None:
                billing_db.record_charge_attempt(
                    subscription_id=subscription_id,
                    user_id=user_id,
                    payment_id=payment_id,
                    status="created",
                )
        except Exception as e:
            logger.warning("Failed to record charge attempt for subscription %s: %s", subscription_id, e)
    
    return payment_id