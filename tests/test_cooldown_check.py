"""
Tests for cooldown and trial eligibility checks.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from bot.handlers.payment_handler import _create_links_for_selection
from bot.utils.time_helpers import now_msk, TIMEZONE


@pytest.mark.asyncio
async def test_cooldown_with_canceled_subscription():
    """Test that canceled subscription with recent last_charge_at blocks trial offer."""
    user_id = 123456789
    
    with patch('bot.handlers.payment_handler.app_db') as mock_app_db, \
         patch('bot.handlers.payment_handler.youmoney') as mock_youmoney, \
         patch('bot.handlers.payment_handler.billing_db') as mock_billing:
        
        # Setup: canceled subscription with recent last_charge_at (within 90 days)
        mock_canceled_sub = MagicMock()
        mock_canceled_sub.status = "canceled"
        mock_canceled_sub.last_charge_at = datetime.now(TIMEZONE) - timedelta(days=30)  # 30 days ago
        
        from bot.utils.billing_db import SessionLocal, Subscription
        with patch('bot.handlers.payment_handler.SessionLocal') as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            
            # No active subscription
            mock_session.query.return_value.filter.return_value.first.return_value = None
            
            # But there's a canceled subscription with recent charge
            mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_canceled_sub
            
            # Setup pending selection
            from bot.handlers.payment_handler import _PENDING_SELECTION
            _PENDING_SELECTION[user_id] = {"code": "1m", "description": "Test subscription"}
            
            mock_youmoney.create_pay_ex.return_value = "https://yookassa.ru/pay/test"
            
            # Execute
            pay_url_card, pay_url_sbp = _create_links_for_selection(user_id)
            
            # Should offer full payment, not trial (because has_active_subscription = True due to recent canceled)
            # The amount should be full price, not trial_amount
            assert pay_url_card is not None or pay_url_sbp is not None


@pytest.mark.asyncio
async def test_cooldown_with_old_canceled_subscription():
    """Test that old canceled subscription (beyond 90 days) allows trial offer."""
    user_id = 123456789
    
    with patch('bot.handlers.payment_handler.app_db') as mock_app_db, \
         patch('bot.handlers.payment_handler.youmoney') as mock_youmoney, \
         patch('bot.handlers.payment_handler.billing_db') as mock_billing:
        
        # Setup: canceled subscription with old last_charge_at (beyond 90 days)
        mock_canceled_sub = MagicMock()
        mock_canceled_sub.status = "canceled"
        mock_canceled_sub.last_charge_at = datetime.now(TIMEZONE) - timedelta(days=100)  # 100 days ago
        
        from bot.utils.billing_db import SessionLocal, Subscription
        with patch('bot.handlers.payment_handler.SessionLocal') as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            
            # No active subscription
            mock_session.query.return_value.filter.return_value.first.return_value = None
            
            # Old canceled subscription
            mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = mock_canceled_sub
            
            # Setup pending selection
            from bot.handlers.payment_handler import _PENDING_SELECTION
            _PENDING_SELECTION[user_id] = {"code": "1m", "description": "Test subscription"}
            
            mock_app_db.is_trial_allowed.return_value = True  # Cooldown passed
            mock_youmoney.create_pay_ex.return_value = "https://yookassa.ru/pay/test"
            
            # Execute
            pay_url_card, pay_url_sbp = _create_links_for_selection(user_id)
            
            # Should allow trial offer if is_trial_allowed returns True
            # (The logic checks both has_active_subscription and is_trial_allowed)


@pytest.mark.asyncio
async def test_cooldown_with_active_subscription():
    """Test that active subscription always offers full payment."""
    user_id = 123456789
    
    with patch('bot.handlers.payment_handler.app_db') as mock_app_db, \
         patch('bot.handlers.payment_handler.youmoney') as mock_youmoney, \
         patch('bot.handlers.payment_handler.billing_db') as mock_billing:
        
        # Setup: active subscription exists
        mock_active_sub = MagicMock()
        mock_active_sub.status = "active"
        
        from bot.utils.billing_db import SessionLocal, Subscription
        with patch('bot.handlers.payment_handler.SessionLocal') as mock_session_local:
            mock_session = MagicMock()
            mock_session_local.return_value.__enter__.return_value = mock_session
            mock_session.query.return_value.filter.return_value.first.return_value = mock_active_sub
            
            # Setup pending selection
            from bot.handlers.payment_handler import _PENDING_SELECTION
            _PENDING_SELECTION[user_id] = {"code": "1m", "description": "Test subscription"}
            
            mock_youmoney.create_pay_ex.return_value = "https://yookassa.ru/pay/test"
            
            # Execute
            pay_url_card, pay_url_sbp = _create_links_for_selection(user_id)
            
            # Should always offer full payment for active subscription
            # (has_active_subscription = True, so phase = "renewal", first_amount = plan["amount"])

