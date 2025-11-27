"""
Tests for YooKassa webhook processing.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timedelta

from bot.handlers.payment_handler import process_yookassa_webhook
from bot.utils.time_helpers import now_msk


@pytest.mark.asyncio
async def test_webhook_succeeded_trial_payment(mock_bot, sample_payment_webhook_succeeded):
    """Test successful trial payment webhook processing."""
    with patch('bot.handlers.payment_handler.billing_db') as mock_billing, \
         patch('bot.handlers.payment_handler.app_db') as mock_app_db, \
         patch('bot.handlers.payment_handler.yookassa_dedup') as mock_dedup, \
         patch('bot.handlers.payment_handler._notify_after_payment') as mock_notify, \
         patch('bot.handlers.payment_handler.membership_invite') as mock_membership:
        
        # Setup mocks
        mock_dedup.should_process = AsyncMock(return_value=True)
        mock_billing.payment_log_is_processed.return_value = False
        mock_billing.payment_log_upsert = MagicMock()
        mock_billing.mark_charge_attempt_status = MagicMock()
        mock_billing.card_upsert_from_provider.return_value = 1
        mock_billing.subscription_upsert.return_value = 1
        mock_billing.payment_log_mark_processed = MagicMock()
        
        mock_app_db.set_trial.return_value = datetime(2025, 1, 18, 12, 0, 0)
        mock_app_db.user_exists.return_value = True
        
        # Execute
        status, message = await process_yookassa_webhook(mock_bot, sample_payment_webhook_succeeded)
        
        # Assertions
        assert status == 200
        assert "ok" in message.lower()
        mock_billing.subscription_upsert.assert_called_once()
        mock_notify.assert_called_once()
        mock_billing.payment_log_mark_processed.assert_called_once_with("test_payment_123")


@pytest.mark.asyncio
async def test_webhook_succeeded_renewal_payment(mock_bot, sample_payment_webhook_renewal):
    """Test successful renewal payment webhook processing."""
    with patch('bot.handlers.payment_handler.billing_db') as mock_billing, \
         patch('bot.handlers.payment_handler.yookassa_dedup') as mock_dedup, \
         patch('bot.handlers.payment_handler._notify_after_payment') as mock_notify, \
         patch('bot.handlers.payment_handler.membership_invite') as mock_membership:
        
        # Setup mocks
        mock_dedup.should_process = AsyncMock(return_value=True)
        mock_billing.payment_log_is_processed.return_value = False
        mock_billing.payment_log_upsert = MagicMock()
        mock_billing.mark_charge_attempt_status = MagicMock()
        mock_billing.card_upsert_from_provider.return_value = 1
        mock_billing.subscription_mark_charged_for_user.return_value = 2
        mock_billing.payment_log_mark_processed = MagicMock()
        
        # Execute
        status, message = await process_yookassa_webhook(mock_bot, sample_payment_webhook_renewal)
        
        # Assertions
        assert status == 200
        mock_billing.subscription_mark_charged_for_user.assert_called_once()
        mock_notify.assert_called_once()
        mock_billing.payment_log_mark_processed.assert_called_once_with("test_payment_456")


@pytest.mark.asyncio
async def test_webhook_canceled_payment(mock_bot, sample_payment_webhook_canceled):
    """Test canceled payment webhook processing."""
    with patch('bot.handlers.payment_handler.billing_db') as mock_billing, \
         patch('bot.handlers.payment_handler.app_db') as mock_app_db, \
         patch('bot.handlers.payment_handler.yookassa_dedup') as mock_dedup, \
         patch('bot.handlers.payment_handler.invalidate_payment_ok_cache') as mock_invalidate:
        
        # Setup mocks
        mock_dedup.should_process = AsyncMock(return_value=True)
        mock_billing.payment_log_is_processed.return_value = False
        mock_billing.payment_log_upsert = MagicMock()
        mock_billing.mark_charge_attempt_status = MagicMock()
        mock_invalidate = AsyncMock()
        
        # Mock subscription for consecutive_failures update
        mock_sub = MagicMock()
        mock_sub.consecutive_failures = 0
        mock_sub.last_fail_notice_at = None
        
        from bot.utils.billing_db import SessionLocal, Subscription
        with patch('bot.utils.billing_db.SessionLocal') as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            mock_session.get.return_value = mock_sub
            mock_session.begin.return_value.__enter__.return_value = None
            
            # Execute
            status, message = await process_yookassa_webhook(mock_bot, sample_payment_webhook_canceled)
        
        # Assertions
        assert status == 200
        assert "fail" in message.lower()
        mock_billing.mark_charge_attempt_status.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_duplicate_processing(mock_bot, sample_payment_webhook_succeeded):
    """Test that duplicate webhooks are not processed."""
    with patch('bot.handlers.payment_handler.billing_db') as mock_billing, \
         patch('bot.handlers.payment_handler.yookassa_dedup') as mock_dedup:
        
        # Setup mocks - webhook already processed
        mock_dedup.should_process = AsyncMock(return_value=False)
        mock_billing.payment_log_is_processed.return_value = True
        
        # Execute
        status, message = await process_yookassa_webhook(mock_bot, sample_payment_webhook_succeeded)
        
        # Assertions
        assert status == 200
        assert "duplicate" in message.lower() or "no-op" in message.lower()


@pytest.mark.asyncio
async def test_webhook_missing_payment_id(mock_bot):
    """Test webhook with missing payment_id."""
    payload = {"event": "payment.succeeded", "object": {"status": "succeeded"}}
    
    status, message = await process_yookassa_webhook(mock_bot, payload)
    
    assert status == 400
    assert "payment_id" in message.lower()


@pytest.mark.asyncio
async def test_webhook_renewal_subscription_not_found(mock_bot, sample_payment_webhook_renewal):
    """Test renewal payment when subscription is not found."""
    with patch('bot.handlers.payment_handler.billing_db') as mock_billing, \
         patch('bot.handlers.payment_handler.yookassa_dedup') as mock_dedup, \
         patch('bot.handlers.payment_handler._notify_after_payment') as mock_notify:
        
        # Setup mocks
        mock_dedup.should_process = AsyncMock(return_value=True)
        mock_billing.payment_log_is_processed.return_value = False
        mock_billing.payment_log_upsert = MagicMock()
        mock_billing.mark_charge_attempt_status = MagicMock()
        mock_billing.card_upsert_from_provider.return_value = 1
        mock_billing.subscription_mark_charged_for_user.return_value = None  # Subscription not found
        mock_billing.payment_log_mark_processed = MagicMock()
        
        # Mock subscription query for fallback logic
        from bot.utils.billing_db import SessionLocal, Subscription
        with patch('bot.utils.billing_db.SessionLocal') as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            mock_session.query.return_value.filter.return_value.first.return_value = None  # No canceled sub
            mock_session.commit = MagicMock()
            
            # Execute
            status, message = await process_yookassa_webhook(mock_bot, sample_payment_webhook_renewal)
        
        # Assertions
        assert status == 200
        # Should create new subscription or handle gracefully
        mock_billing.payment_log_mark_processed.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_waiting_for_capture(mock_bot):
    """Test webhook with waiting_for_capture status."""
    payload = {
        "event": "payment.waiting_for_capture",
        "object": {
            "id": "test_payment_999",
            "status": "waiting_for_capture",
            "amount": {"value": "2490.00", "currency": "RUB"},
            "metadata": {"user_id": "123456789"}
        }
    }
    
    with patch('bot.handlers.payment_handler.billing_db') as mock_billing, \
         patch('bot.handlers.payment_handler.yookassa_dedup') as mock_dedup:
        
        mock_dedup.should_process = AsyncMock(return_value=True)
        mock_billing.payment_log_upsert = MagicMock()
        
        status, message = await process_yookassa_webhook(mock_bot, payload)
        
        assert status == 200
        assert "waiting_for_capture" in message.lower()
        mock_billing.payment_log_upsert.assert_called_once()

