import uuid

from yookassa import Configuration, Payment
from bot.config import YOUMONEY_SHOP_ID, YOUMONEY_SECRET_KEY

Configuration.account_id = YOUMONEY_SHOP_ID
Configuration.secret_key = YOUMONEY_SECRET_KEY


def create_pay(user_id, amount=2500):
    payment = Payment.create({
        "amount": {
            "value": amount,
            "currency": "RUB"
        },
        "confirmation": {
            'type': 'redirect',
            'return_url': 'https://t.me/'
        },
        'metadata': {
            'user_id': user_id
        },
        "receipt": {
            "customer": {
                "email": 'admin@google.com',
            },
            "items": [
                {

                    "description": "Пакет из 100 генераций",
                    "quantity": "1",
                    "amount": {
                        "value": amount,
                        "currency": "RUB",
                    },
                    "vat_code": "1",
                    "payment_mode": "full_prepayment",
                    "payment_subject": "payment"

                },
            ]
        },
        "capture": True,
    }, uuid.uuid4())
    print(payment.status)
    return payment.confirmation.confirmation_url

