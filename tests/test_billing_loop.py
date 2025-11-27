"""
Tests for billing loop and charge attempts.
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta

from bot.utils.billing_db import precharge_guard_and_attempt, subscriptions_due
from bot.utils.time_helpers import now_msk, TIMEZONE


def test_precharge_guard_and_attempt_success(mock_subscription, mock_time):
    """Test successful charge attempt creation."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription query
        mock_session.query.return_value.with_for_update.return_value.filter.return_value.one_or_none.return_value = mock_subscription
        
        # Setup charge attempt query (no recent attempts)
        mock_session.query.return_value.filter.return_value.count.return_value = 0
        
        # Setup existing attempt query (no existing)
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        
        # Execute
        attempt_id = precharge_guard_and_attempt(
            subscription_id=1,
            now=mock_time,
            user_id=123456789
        )
        
        # Should create attempt
        assert attempt_id is not None
        mock_session.add.assert_called_once()


def test_precharge_guard_and_attempt_blocked_by_12h_gap(mock_subscription, mock_time):
    """Test that guard blocks attempt if 12h gap not passed."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription with recent last_attempt_at (2 hours ago)
        mock_subscription.last_attempt_at = datetime.now(TIMEZONE) - timedelta(hours=2)
        mock_session.query.return_value.with_for_update.return_value.filter.return_value.one_or_none.return_value = mock_subscription
        
        # Execute
        attempt_id = precharge_guard_and_attempt(
            subscription_id=1,
            now=mock_time,
            user_id=123456789
        )
        
        # Should be blocked
        assert attempt_id is None


def test_precharge_guard_and_attempt_blocked_by_6_failures(mock_subscription, mock_time):
    """Test that guard blocks attempt if consecutive_failures >= 6."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription with 6 failures
        mock_subscription.consecutive_failures = 6
        mock_subscription.last_attempt_at = None
        mock_session.query.return_value.with_for_update.return_value.filter.return_value.one_or_none.return_value = mock_subscription
        
        # Execute
        attempt_id = precharge_guard_and_attempt(
            subscription_id=1,
            now=mock_time,
            user_id=123456789
        )
        
        # Should be blocked
        assert attempt_id is None


def test_precharge_guard_and_attempt_blocked_by_2_attempts_24h(mock_subscription, mock_time):
    """Test that guard blocks attempt if 2 attempts in last 24h."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription
        mock_subscription.consecutive_failures = 0
        mock_subscription.last_attempt_at = None
        mock_session.query.return_value.with_for_update.return_value.filter.return_value.one_or_none.return_value = mock_subscription
        
        # Setup charge attempt query - 2 attempts in last 24h
        mock_session.query.return_value.filter.return_value.count.return_value = 2
        
        # Execute
        attempt_id = precharge_guard_and_attempt(
            subscription_id=1,
            now=mock_time,
            user_id=123456789
        )
        
        # Should be blocked
        assert attempt_id is None


def test_subscriptions_due_filters_correctly(mock_time):
    """Test that subscriptions_due filters subscriptions correctly."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        
        # Setup subscription query
        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.user_id = 123456789
        mock_sub.plan_code = "1m"
        mock_sub.interval_months = 1
        mock_sub.amount_value = "2490.00"
        mock_sub.amount_currency = "RUB"
        mock_sub.payment_method_id = "pm_token_123"
        mock_sub.consecutive_failures = 0
        mock_sub.last_attempt_at = None
        mock_sub.next_charge_at = datetime.now(TIMEZONE) - timedelta(hours=1)  # Due
        
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value = [mock_sub]
        
        # Setup charge attempt queries
        mock_session.query.return_value.filter.return_value.group_by.return_value.having.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.group_by.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.group_by.return_value.all.return_value = []
        
        # Execute
        due = subscriptions_due(now=mock_time, limit=100)
        
        # Should return due subscription
        assert len(due) > 0
        assert due[0]["id"] == 1
        assert due[0]["user_id"] == 123456789


def test_subscriptions_due_filters_by_guard_rules(mock_time):
    """Test that subscriptions_due applies guard rules."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        
        # Setup subscription with 6 failures
        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.user_id = 123456789
        mock_sub.plan_code = "1m"
        mock_sub.interval_months = 1
        mock_sub.amount_value = "2490.00"
        mock_sub.amount_currency = "RUB"
        mock_sub.payment_method_id = "pm_token_123"
        mock_sub.consecutive_failures = 6  # Blocked
        mock_sub.last_attempt_at = None
        mock_sub.next_charge_at = datetime.now(TIMEZONE) - timedelta(hours=1)
        
        mock_session.query.return_value.filter.return_value.order_by.return_value.limit.return_value = [mock_sub]
        
        # Setup charge attempt queries
        mock_session.query.return_value.filter.return_value.group_by.return_value.having.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.group_by.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.group_by.return_value.all.return_value = []
        
        # Execute
        due = subscriptions_due(now=mock_time, limit=100)
        
        # Should filter out subscription with 6 failures
        assert len(due) == 0

