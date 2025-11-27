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
    from bot.utils.billing_db import _repo
    
    # Создаем мок сессии
    mock_session = MagicMock()
    mock_session.begin.return_value.__enter__.return_value = None
    
    # Создаем мок session_factory, который возвращает нашу сессию
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    
    # Патчим _session_factory репозитория
    with patch.object(_repo, '_session_factory', mock_session_factory), \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        # Setup subscription query - создаем новый объект с правильным user_id
        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.user_id = 7833048230  # Должен совпадать с переданным user_id
        mock_sub.status = "active"  # Должен быть active
        mock_sub.consecutive_failures = 0
        
        # Настраиваем get() так, чтобы он возвращал наш объект при вызове с Subscription и subscription_id=1
        def get_side_effect(model_class, pk):
            if pk == 1:
                return mock_sub
            return None
        
        mock_session.get = MagicMock(side_effect=get_side_effect)
        
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
    from bot.utils.billing_db import _repo
    
    # Создаем мок сессии
    mock_session = MagicMock()
    mock_session.begin.return_value.__enter__.return_value = None
    
    # Создаем мок session_factory, который возвращает нашу сессию
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    
    # Патчим _session_factory репозитория
    with patch.object(_repo, '_session_factory', mock_session_factory), \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        # Setup subscription query by plan_code
        # Когда не передается subscription_id, код идет по ветке elif plan_code:
        mock_sub = MagicMock()
        mock_sub.id = 1  # Важно: должен быть id=1 для проверки
        mock_sub.user_id = 7833048230
        mock_sub.plan_code = "1m"
        mock_sub.status = "active"
        mock_sub.consecutive_failures = 0
        
        # Настраиваем query() для поиска по plan_code
        # Код делает два вызова query():
        # 1. Поиск по plan_code (должен найти подписку)
        # 2. Fallback поиск (не должен вызываться, т.к. первая подписка найдена)
        
        # Первый query - поиск по plan_code (находит подписку)
        mock_query1 = MagicMock()
        mock_filter1 = MagicMock()
        mock_filter1.first.return_value = mock_sub  # Находит подписку
        mock_query1.filter.return_value = mock_filter1
        
        # Fallback query не должен вызываться, но настраиваем на всякий случай
        mock_query2 = MagicMock()
        mock_filter2 = MagicMock()
        mock_filter2.order_by.return_value.first.return_value = None
        mock_query2.filter.return_value = mock_filter2
        
        mock_session.query.side_effect = [mock_query1, mock_query2]
        mock_session.get = MagicMock(return_value=None)  # Не используется в этом тесте
        
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
    from bot.utils.billing_db import _repo
    
    # Создаем мок сессии
    mock_session = MagicMock()
    mock_session.begin.return_value.__enter__.return_value = None
    
    # Создаем мок session_factory, который возвращает нашу сессию
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    
    # Патчим _session_factory репозитория
    with patch.object(_repo, '_session_factory', mock_session_factory), \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
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
    from bot.utils.billing_db import _repo
    
    # Создаем мок сессии
    mock_session = MagicMock()
    mock_session.begin.return_value.__enter__.return_value = None
    
    # Создаем мок session_factory, который возвращает нашу сессию
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    
    # Патчим _session_factory репозитория
    with patch.object(_repo, '_session_factory', mock_session_factory), \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        # Setup - subscription found but inactive
        mock_inactive_sub = MagicMock()
        mock_inactive_sub.id = 1
        mock_inactive_sub.status = "canceled"
        mock_inactive_sub.user_id = 7833048230  # Совпадает, но статус canceled
        
        # Настраиваем get() так, чтобы он возвращал наш объект при вызове с Subscription и subscription_id=1
        def get_side_effect(model_class, pk):
            if pk == 1:
                return mock_inactive_sub
            return None
        
        mock_session.get = MagicMock(side_effect=get_side_effect)
        
        # Fallback - find active subscription by plan_code
        mock_active_sub = MagicMock()
        mock_active_sub.id = 2
        mock_active_sub.user_id = 7833048230
        mock_active_sub.plan_code = "1m"
        mock_active_sub.status = "active"
        mock_active_sub.consecutive_failures = 0
        
        # Настраиваем последовательные вызовы query() для fallback
        # Код делает fallback поиск по plan_code (первый query после того как rec стал None)
        mock_query1 = MagicMock()
        mock_filter1 = MagicMock()
        mock_filter1.first.return_value = mock_active_sub  # Поиск по plan_code находит активную подписку
        mock_query1.filter.return_value = mock_filter1
        
        # Второй query (fallback по умолчанию) не должен вызываться, т.к. первый нашел подписку
        mock_query2 = MagicMock()
        mock_filter2 = MagicMock()
        mock_filter2.order_by.return_value.first.return_value = None
        mock_query2.filter.return_value = mock_filter2
        
        mock_session.query.side_effect = [mock_query1, mock_query2]
        
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

