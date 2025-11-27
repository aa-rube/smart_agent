"""
Tests for subscription_mark_charged_for_user.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from bot.utils.billing_db import subscription_mark_charged_for_user
from bot.utils.time_helpers import now_msk, TIMEZONE


def test_subscription_mark_charged_by_subscription_id(mock_subscription, mock_time):
    """Test updating subscription by subscription_id."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription query
        mock_session.get.return_value = mock_subscription
        
        # Execute
        result = subscription_mark_charged_for_user(
            user_id=123456789,
            next_charge_at=mock_time + timedelta(days=30),
            subscription_id=1
        )
        
        # Should update subscription
        assert result == 1
        assert mock_subscription.consecutive_failures == 0
        mock_session.flush.assert_called_once()


def test_subscription_mark_charged_by_plan_code(mock_subscription, mock_time):
    """Test updating subscription by plan_code."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription query by plan_code
        mock_session.get.return_value = None  # Not found by subscription_id
        mock_session.query.return_value.filter.return_value.first.return_value = mock_subscription
        
        # Execute
        result = subscription_mark_charged_for_user(
            user_id=123456789,
            next_charge_at=mock_time + timedelta(days=30),
            plan_code="1m"
        )
        
        # Should update subscription
        assert result == 1
        assert mock_subscription.consecutive_failures == 0


def test_subscription_mark_charged_subscription_not_found(mock_time):
    """Test when subscription is not found."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup - no subscription found
        mock_session.get.return_value = None
        mock_session.query.return_value.filter.return_value.first.return_value = None
        
        # Execute
        result = subscription_mark_charged_for_user(
            user_id=123456789,
            next_charge_at=mock_time + timedelta(days=30),
            subscription_id=999
        )
        
        # Should return None
        assert result is None


def test_subscription_mark_charged_inactive_subscription_fallback(mock_subscription, mock_time):
    """Test fallback when subscription_id points to inactive subscription."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup - subscription found but inactive
        mock_inactive_sub = MagicMock()
        mock_inactive_sub.status = "canceled"
        mock_session.get.return_value = mock_inactive_sub
        
        # Fallback - find active subscription by plan_code
        mock_active_sub = MagicMock()
        mock_active_sub.id = 2
        mock_active_sub.user_id = 123456789
        mock_active_sub.consecutive_failures = 0
        mock_session.query.return_value.filter.return_value.first.return_value = mock_active_sub
        
        # Execute
        result = subscription_mark_charged_for_user(
            user_id=123456789,
            next_charge_at=mock_time + timedelta(days=30),
            subscription_id=1,
            plan_code="1m"
        )
        
        # Should find and update active subscription
        assert result == 2
        assert mock_active_sub.consecutive_failures == 0

