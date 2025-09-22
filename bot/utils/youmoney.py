# smart_agent/bot/utils/youmoney.py
import uuid
from typing import Optional, Dict

from yookassa import Configuration, Payment
from bot.config import YOUMONEY_SHOP_ID, YOUMONEY_SECRET_KEY

Configuration.account_id = YOUMONEY_SHOP_ID
Configuration.secret_key = YOUMONEY_SECRET_KEY


# НОВЫЙ УНИВЕРСАЛЬНЫЙ МЕТОД
def create_pay_ex(
    user_id: int,
    amount_rub: str,
    description: str,
    metadata: Optional[Dict[str, str]] = None,
    return_url: str = "https://t.me/",
) -> str:
    """
    Создает платёж YooKassa и возвращает confirmation_url.
    amount_rub: строка с 2 знаками после запятой (например, "2500.00")
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
    }

    payment = Payment.create(body, uuid.uuid4())
    return payment.confirmation.confirmation_url