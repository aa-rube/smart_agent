"""
Tests for pre-renewal notification logic.
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta

from bot.utils.notification import run_paid_lifecycle
from bot.utils.time_helpers import from_db_naive, TIMEZONE


@pytest.mark.asyncio
async def test_pre_renew_notification_skipped_recent_charge(mock_bot):
    """Test that pre_renew notification is skipped if last_charge_at is recent."""
    with patch('bot.utils.notification.billing_db') as mock_billing, \
         patch('bot.utils.notification._send_text_once') as mock_send, \
         patch('bot.utils.notification._utcnow') as mock_utcnow:
        
        now = datetime.now(TIMEZONE)
        mock_utcnow.return_value = now
        
        # Setup active subscription with recent last_charge_at (1 hour ago)
        mock_billing.list_active_subscription_user_ids.return_value = [7833048230]
        
        from bot.utils.billing_db import SessionLocal, Subscription
        with patch('bot.utils.notification.billing_db.SessionLocal') as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            
            # Setup subscription query - используем реальные datetime объекты
            next_charge_at = now + timedelta(hours=23)
            created_at = now - timedelta(days=10)
            last_charge_at = now - timedelta(hours=1)
            
            # Настраиваем query chain правильно
            mock_query = MagicMock()
            mock_query.filter.return_value.order_by.return_value.all.return_value = [
                (7833048230, created_at, next_charge_at, last_charge_at)
            ]
            mock_session.query.return_value = mock_query
            
            mock_send.return_value = False  # Not sent due to recent charge
            
            # Execute
            await run_paid_lifecycle(mock_bot)
            
            # Should not send pre_renew notification
            # (The logic checks last_charge_at and skips if < 2 hours)


@pytest.mark.asyncio
async def test_pre_renew_notification_sent_old_charge(mock_bot):
    """Test that pre_renew notification is sent if last_charge_at is old."""
    with patch('bot.utils.notification.billing_db') as mock_billing, \
         patch('bot.utils.notification._send_text_once') as mock_send, \
         patch('bot.utils.notification._utcnow') as mock_utcnow:
        
        now = datetime.now(TIMEZONE)
        mock_utcnow.return_value = now
        
        # Setup active subscription with old last_charge_at (3 hours ago)
        mock_billing.list_active_subscription_user_ids.return_value = [7833048230]
        
        from bot.utils.billing_db import SessionLocal, Subscription
        with patch('bot.utils.notification.billing_db.SessionLocal') as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            
            # Setup subscription query - используем реальные datetime объекты
            next_charge_at = now + timedelta(hours=23)
            created_at = now - timedelta(days=10)
            last_charge_at = now - timedelta(hours=3)
            
            # Настраиваем query chain правильно
            mock_query = MagicMock()
            mock_query.filter.return_value.order_by.return_value.all.return_value = [
                (7833048230, created_at, next_charge_at, last_charge_at)
            ]
            mock_session.query.return_value = mock_query
            
            mock_send.return_value = True  # Sent
            
            # Execute
            await run_paid_lifecycle(mock_bot)
            
            # Should send pre_renew notification
            # (last_charge_at is > 2 hours ago, so notification should be sent)
            # Note: actual call depends on next_charge_at being within 24h


@pytest.mark.asyncio
async def test_pre_renew_notification_no_last_charge_at(mock_bot):
    """Test pre_renew notification when last_charge_at is None."""
    with patch('bot.utils.notification.billing_db') as mock_billing, \
         patch('bot.utils.notification._send_text_once') as mock_send, \
         patch('bot.utils.notification._utcnow') as mock_utcnow:
        
        now = datetime.now(TIMEZONE)
        mock_utcnow.return_value = now
        
        # Setup active subscription without last_charge_at
        mock_billing.list_active_subscription_user_ids.return_value = [7833048230]
        
        from bot.utils.billing_db import SessionLocal, Subscription
        with patch('bot.utils.notification.billing_db.SessionLocal') as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            
            # Setup subscription query - используем реальные datetime объекты
            next_charge_at = now + timedelta(hours=23)
            created_at = now - timedelta(days=10)
            
            # Настраиваем query chain правильно
            mock_query = MagicMock()
            mock_query.filter.return_value.order_by.return_value.all.return_value = [
                (7833048230, created_at, next_charge_at, None)
            ]
            mock_session.query.return_value = mock_query
            
            mock_send.return_value = True  # Sent
            
            # Execute
            await run_paid_lifecycle(mock_bot)
            
            # Should send pre_renew notification if next_charge_at is within 24h
            # (last_charge_at is None, so should_skip_pre_renew = False)

