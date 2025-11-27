"""
Pytest configuration and fixtures for payment module tests.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import User, Chat

from bot.config import TIMEZONE
from bot.utils.time_helpers import now_msk


@pytest.fixture
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_bot():
    """Mock Telegram Bot."""
    bot = AsyncMock(spec=Bot)
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    return bot


@pytest.fixture
def mock_user():
    """Mock Telegram User."""
    user = User(
        id=123456789,
        is_bot=False,
        first_name="Test",
        username="testuser"
    )
    return user


@pytest.fixture
def mock_chat():
    """Mock Telegram Chat."""
    chat = Chat(id=123456789, type="private")
    return chat


@pytest.fixture
def sample_payment_webhook_succeeded():
    """Sample YooKassa webhook payload for succeeded payment."""
    return {
        "event": "payment.succeeded",
        "object": {
            "id": "test_payment_123",
            "status": "succeeded",
            "amount": {
                "value": "2490.00",
                "currency": "RUB"
            },
            "created_at": "2025-01-15T10:00:00.000Z",
            "payment_method": {
                "id": "pm_token_123",
                "type": "bank_card",
                "card": {
                    "card_type": "Visa",
                    "first6": "411111",
                    "last4": "1111",
                    "expiry_month": 12,
                    "expiry_year": 2025
                }
            },
            "metadata": {
                "user_id": "123456789",
                "plan_code": "1m",
                "months": "1",
                "is_recurring": "1",
                "phase": "trial",
                "trial_hours": "72",
                "plan_amount": "2490.00",
                "subscription_id": "1"
            }
        }
    }


@pytest.fixture
def sample_payment_webhook_renewal():
    """Sample YooKassa webhook payload for renewal payment."""
    return {
        "event": "payment.succeeded",
        "object": {
            "id": "test_payment_456",
            "status": "succeeded",
            "amount": {
                "value": "2490.00",
                "currency": "RUB"
            },
            "created_at": "2025-01-15T10:00:00.000Z",
            "payment_method": {
                "id": "pm_token_456",
                "type": "bank_card",
                "card": {
                    "card_type": "MasterCard",
                    "first6": "555555",
                    "last4": "5555",
                    "expiry_month": 6,
                    "expiry_year": 2026
                }
            },
            "metadata": {
                "user_id": "123456789",
                "plan_code": "1m",
                "months": "1",
                "is_recurring": "1",
                "phase": "renewal",
                "plan_amount": "2490.00",
                "subscription_id": "2"
            }
        }
    }


@pytest.fixture
def sample_payment_webhook_canceled():
    """Sample YooKassa webhook payload for canceled payment."""
    return {
        "event": "payment.canceled",
        "object": {
            "id": "test_payment_789",
            "status": "canceled",
            "amount": {
                "value": "2490.00",
                "currency": "RUB"
            },
            "metadata": {
                "user_id": "123456789",
                "subscription_id": "2"
            }
        }
    }


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=True)
    redis.exists = AsyncMock(return_value=0)
    return redis


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    session = MagicMock()
    session.query = MagicMock()
    session.get = MagicMock()
    session.add = MagicMock()
    session.commit = MagicMock()
    session.begin = MagicMock()
    session.flush = MagicMock()
    session.execute = MagicMock()
    return session


@pytest.fixture
def mock_time():
    """Mock current time in MSK."""
    return datetime(2025, 1, 15, 12, 0, 0, tzinfo=TIMEZONE)


@pytest.fixture
def mock_subscription():
    """Mock subscription object."""
    sub = MagicMock()
    sub.id = 1
    sub.user_id = 123456789
    sub.plan_code = "1m"
    sub.status = "active"
    sub.payment_method_id = "pm_token_123"
    sub.next_charge_at = datetime(2025, 2, 15, 12, 0, 0, tzinfo=TIMEZONE)
    sub.last_charge_at = datetime(2025, 1, 15, 12, 0, 0, tzinfo=TIMEZONE)
    sub.consecutive_failures = 0
    sub.last_attempt_at = None
    sub.created_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=TIMEZONE)
    sub.updated_at = datetime(2025, 1, 15, 12, 0, 0, tzinfo=TIMEZONE)
    return sub


@pytest.fixture
def mock_charge_attempt():
    """Mock charge attempt object."""
    attempt = MagicMock()
    attempt.id = 1
    attempt.subscription_id = 1
    attempt.user_id = 123456789
    attempt.payment_id = "test_payment_123"
    attempt.status = "created"
    attempt.attempted_at = datetime(2025, 1, 15, 12, 0, 0, tzinfo=TIMEZONE)
    attempt.due_at = datetime(2025, 2, 15, 12, 0, 0, tzinfo=TIMEZONE)
    return attempt

