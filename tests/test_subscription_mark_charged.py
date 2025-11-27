"""
Tests for subscription_mark_charged_for_user.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from bot.utils.billing_db import subscription_mark_charged_for_user
from bot.utils.time_helpers import now_msk, TIMEZONE


def test_subscription_mark_charged_by_subscription_id(mock_time):
    """Test updating subscription by subscription_id."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription query - создаем новый объект с правильным user_id
        # НЕ используем mock_subscription из фикстуры, чтобы избежать проблем с user_id
        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.user_id = 7833048230  # Должен совпадать с переданным user_id
        mock_sub.status = "active"  # Должен быть active
        mock_sub.consecutive_failures = 0
        # Настраиваем get() так, чтобы он возвращал наш объект при любом вызове
        mock_session.get = MagicMock(return_value=mock_sub)
        
        # Execute
        result = subscription_mark_charged_for_user(
            user_id=7833048230,
            next_charge_at=mock_time + timedelta(days=30),
            subscription_id=1
        )
        
        # Should update subscription
        assert result == 1
        assert mock_sub.consecutive_failures == 0
        mock_session.flush.assert_called_once()


def test_subscription_mark_charged_by_plan_code(mock_time):
    """Test updating subscription by plan_code."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription query by plan_code
        # Когда не передается subscription_id, код идет по ветке elif plan_code:
        # Нужно правильно настроить моки для query()
        # Создаем новый объект с правильным user_id
        mock_sub = MagicMock()
        mock_sub.id = 1  # Важно: должен быть id=1 для проверки
        mock_sub.user_id = 7833048230
        mock_sub.plan_code = "1m"
        mock_sub.status = "active"
        mock_sub.consecutive_failures = 0
        
        # Настраиваем query() для поиска по plan_code
        # Когда не передается subscription_id, get() не вызывается, только query()
        mock_session.get = MagicMock(return_value=None)  # Не используется в этом тесте
        mock_query = MagicMock()
        mock_query.filter.return_value.first.return_value = mock_sub
        mock_session.query.return_value = mock_query
        
        # Execute
        result = subscription_mark_charged_for_user(
            user_id=7833048230,
            next_charge_at=mock_time + timedelta(days=30),
            plan_code="1m"
        )
        
        # Should update subscription
        assert result == 1
        assert mock_sub.consecutive_failures == 0


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
            user_id=7833048230,
            next_charge_at=mock_time + timedelta(days=30),
            subscription_id=999
        )
        
        # Should return None
        assert result is None


def test_subscription_mark_charged_inactive_subscription_fallback(mock_time):
    """Test fallback when subscription_id points to inactive subscription."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup - subscription found but inactive
        # Создаем новый объект с правильным user_id
        mock_inactive_sub = MagicMock()
        mock_inactive_sub.id = 1
        mock_inactive_sub.status = "canceled"
        mock_inactive_sub.user_id = 7833048230  # Совпадает, но статус canceled
        # Настраиваем get() так, чтобы он возвращал наш объект
        mock_session.get = MagicMock(return_value=mock_inactive_sub)
        
        # Fallback - find active subscription by plan_code
        mock_active_sub = MagicMock()
        mock_active_sub.id = 2
        mock_active_sub.user_id = 7833048230
        mock_active_sub.plan_code = "1m"
        mock_active_sub.status = "active"
        mock_active_sub.consecutive_failures = 0
        
        # Настраиваем последовательные вызовы query() для fallback
        mock_query1 = MagicMock()
        mock_query1.filter.return_value.first.return_value = mock_active_sub
        mock_query2 = MagicMock()
        mock_query2.filter.return_value.order_by.return_value.first.return_value = None
        
        mock_session.query.side_effect = [
            mock_query1,  # Поиск по plan_code
            mock_query2,  # Fallback поиск по умолчанию (не используется в этом случае)
        ]
        
        # Execute
        result = subscription_mark_charged_for_user(
            user_id=7833048230,
            next_charge_at=mock_time + timedelta(days=30),
            subscription_id=1,
            plan_code="1m"
        )
        
        # Should find and update active subscription
        assert result == 2
        assert mock_active_sub.consecutive_failures == 0

