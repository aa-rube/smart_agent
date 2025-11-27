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
    # Мокируем _repo напрямую, так как функция-обертка вызывает _repo.precharge_guard_and_attempt
    with patch('bot.utils.billing_db._repo') as mock_repo:
        # Настраиваем mock_subscription
        mock_subscription.status = "active"
        mock_subscription.payment_method_id = "pm_token_123"
        mock_subscription.consecutive_failures = 0
        mock_subscription.last_attempt_at = None
        mock_subscription.next_charge_at = datetime(2025, 2, 15, 12, 0, 0, tzinfo=TIMEZONE)
        
        # Мокируем метод precharge_guard_and_attempt
        mock_repo.precharge_guard_and_attempt.return_value = 123
        
        # Execute
        attempt_id = precharge_guard_and_attempt(
            subscription_id=1,
            now=mock_time,
            user_id=7833048230
        )
        
        # Should create attempt
        assert attempt_id == 123
        mock_repo.precharge_guard_and_attempt.assert_called_once_with(
            subscription_id=1,
            now=mock_time,
            user_id=7833048230
        )


def test_precharge_guard_and_attempt_blocked_by_12h_gap(mock_subscription, mock_time):
    """Test that guard blocks attempt if 12h gap not passed."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        mock_session.begin.return_value.__enter__.return_value = None
        
        # Setup subscription with recent last_attempt_at (2 hours ago)
        mock_subscription.last_attempt_at = datetime.now(TIMEZONE) - timedelta(hours=2)
        mock_sub_query = MagicMock()
        mock_sub_query.with_for_update.return_value.filter.return_value.one_or_none.return_value = mock_subscription
        
        from bot.utils.billing_db import Subscription
        mock_session.query.return_value = mock_sub_query
        
        # Execute
        attempt_id = precharge_guard_and_attempt(
            subscription_id=1,
            now=mock_time,
            user_id=7833048230
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
        mock_sub_query = MagicMock()
        mock_sub_query.with_for_update.return_value.filter.return_value.one_or_none.return_value = mock_subscription
        
        from bot.utils.billing_db import Subscription
        mock_session.query.return_value = mock_sub_query
        
        # Execute
        attempt_id = precharge_guard_and_attempt(
            subscription_id=1,
            now=mock_time,
            user_id=7833048230
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
        mock_sub_query = MagicMock()
        mock_sub_query.with_for_update.return_value.filter.return_value.one_or_none.return_value = mock_subscription
        
        # Setup charge attempt query - 2 attempts in last 24h
        mock_attempt_query = MagicMock()
        mock_attempt_query.filter.return_value.count.return_value = 2
        
        from bot.utils.billing_db import Subscription, ChargeAttempt
        mock_session.query.side_effect = [
            mock_sub_query,  # s.query(Subscription)
            mock_attempt_query,  # s.query(ChargeAttempt) для count
        ]
        
        # Execute
        attempt_id = precharge_guard_and_attempt(
            subscription_id=1,
            now=mock_time,
            user_id=7833048230
        )
        
        # Should be blocked
        assert attempt_id is None


def test_subscriptions_due_filters_correctly(mock_time):
    """Test that subscriptions_due filters subscriptions correctly."""
    # Мокируем _repo напрямую
    with patch('bot.utils.billing_db._repo') as mock_repo:
        # Настраиваем возвращаемое значение
        mock_repo.subscriptions_due.return_value = [
            {
                "id": 1,
                "user_id": 7833048230,
                "plan_code": "1m",
                "interval_months": 1,
                "amount_value": "2490.00",
                "amount_currency": "RUB",
                "payment_method_id": "pm_token_123",
                "consecutive_failures": 0,
                "last_attempt_at": None,
            }
        ]
        
        # Execute
        due = subscriptions_due(now=mock_time, limit=100)
        
        # Should return due subscription
        assert len(due) > 0
        assert due[0]["id"] == 1
        assert due[0]["user_id"] == 7833048230
        mock_repo.subscriptions_due.assert_called_once_with(now=mock_time, limit=100)


def test_subscriptions_due_filters_by_guard_rules(mock_time):
    """Test that subscriptions_due applies guard rules."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local, \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time):
        
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        
        # Setup subscription with 6 failures
        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.user_id = 7833048230
        mock_sub.plan_code = "1m"
        mock_sub.interval_months = 1
        mock_sub.amount_value = "2490.00"
        mock_sub.amount_currency = "RUB"
        mock_sub.payment_method_id = "pm_token_123"
        mock_sub.consecutive_failures = 6  # Blocked
        mock_sub.last_attempt_at = None
        mock_sub.next_charge_at = datetime.now(TIMEZONE) - timedelta(hours=1)
        
        # Настраиваем правильную цепочку SQLAlchemy запросов
        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.limit.return_value = [mock_sub]
        
        # Setup charge attempt queries
        mock_attempt_query1 = MagicMock()
        mock_attempt_query1.filter.return_value.group_by.return_value.having.return_value.all.return_value = []
        mock_attempt_query2 = MagicMock()
        mock_attempt_query2.filter.return_value.group_by.return_value.all.return_value = []
        mock_attempt_query3 = MagicMock()
        mock_attempt_query3.filter.return_value.group_by.return_value.all.return_value = []
        
        # Настраиваем последовательные вызовы query()
        from bot.utils.billing_db import Subscription, ChargeAttempt
        mock_session.query.side_effect = [
            mock_query,  # Первый запрос для subscriptions
            mock_attempt_query1,  # Для blocked_ids_day2
            mock_attempt_query2,  # Для blocked_ids_gap12h
            mock_attempt_query3,  # Для failed_counts
        ]
        
        # Execute
        due = subscriptions_due(now=mock_time, limit=100)
        
        # Should filter out subscription with 6 failures
        assert len(due) == 0


def test_subscriptions_due_excludes_paid_period(mock_time):
    """Test that subscriptions with next_charge_at in future (paid period) are excluded."""
    from bot.utils.billing_db import _repo
    
    # Создаем мок сессии
    mock_session = MagicMock()
    
    # Создаем мок session_factory
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    
    # Патчим _session_factory репозитория
    with patch.object(_repo, '_session_factory', mock_session_factory), \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time), \
         patch('bot.utils.billing_db.to_utc_for_db') as mock_to_utc, \
         patch('bot.utils.billing_db.to_aware_msk') as mock_to_aware:
        
        # Настраиваем конвертацию времени
        mock_to_aware.return_value = mock_time
        mock_time_utc = datetime(2025, 1, 15, 9, 0, 0)  # UTC время
        mock_to_utc.return_value = mock_time_utc
        
        # Setup subscription with next_charge_at in future (paid period)
        # Сценарий: пользователь подписался, через 3 дня списалось 2500р
        # next_charge_at установлен на 30 дней вперед - период оплачен
        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.user_id = 7833048230
        mock_sub.plan_code = "1m"
        mock_sub.interval_months = 1
        mock_sub.amount_value = "2500.00"
        mock_sub.amount_currency = "RUB"
        mock_sub.payment_method_id = "pm_token_123"
        mock_sub.consecutive_failures = 0
        mock_sub.last_attempt_at = None
        # next_charge_at в будущем - период оплачен
        mock_sub.next_charge_at = mock_time + timedelta(days=30)
        
        # Настраиваем query для subscriptions
        # Важно: фильтр next_charge_at <= now_utc должен исключить эту подписку
        mock_query = MagicMock()
        # Пустой список, т.к. next_charge_at > now
        mock_query.filter.return_value.order_by.return_value.limit.return_value = []
        
        # Setup charge attempt queries (не должны вызываться, т.к. подписок нет)
        mock_attempt_query1 = MagicMock()
        mock_attempt_query1.filter.return_value.group_by.return_value.having.return_value.all.return_value = []
        mock_attempt_query2 = MagicMock()
        mock_attempt_query2.filter.return_value.group_by.return_value.all.return_value = []
        mock_attempt_query3 = MagicMock()
        mock_attempt_query3.filter.return_value.group_by.return_value.all.return_value = []
        
        from bot.utils.billing_db import Subscription, ChargeAttempt
        mock_session.query.side_effect = [
            mock_query,  # Первый запрос для subscriptions
            mock_attempt_query1,  # Для blocked_ids_day2
            mock_attempt_query2,  # Для blocked_ids_gap12h
            mock_attempt_query3,  # Для failed_counts
        ]
        
        # Execute
        due = subscriptions_due(now=mock_time, limit=100)
        
        # Should NOT include subscription with paid period (next_charge_at > now)
        assert len(due) == 0


def test_subscriptions_due_excludes_after_successful_charge(mock_time):
    """Test that after successful charge, subscription is excluded until next_charge_at."""
    from bot.utils.billing_db import _repo
    
    # Создаем мок сессии
    mock_session = MagicMock()
    
    # Создаем мок session_factory
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    
    # Патчим _session_factory репозитория
    with patch.object(_repo, '_session_factory', mock_session_factory), \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time), \
         patch('bot.utils.billing_db.to_utc_for_db') as mock_to_utc, \
         patch('bot.utils.billing_db.to_aware_msk') as mock_to_aware:
        
        # Настраиваем конвертацию времени
        mock_to_aware.return_value = mock_time
        mock_time_utc = datetime(2025, 1, 15, 9, 0, 0)  # UTC время
        mock_to_utc.return_value = mock_time_utc
        
        # Сценарий: 
        # 1. Пользователь подписался в марте за 1 рубль (триал)
        # 2. Через 3 дня списалось 2500р - next_charge_at обновлен на +30 дней
        # 3. Через 12 часов проверяем - подписка НЕ должна попасть в выборку
        
        # Подписка после успешного списания
        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.user_id = 7833048230
        mock_sub.plan_code = "1m"
        mock_sub.interval_months = 1
        mock_sub.amount_value = "2500.00"
        mock_sub.amount_currency = "RUB"
        mock_sub.payment_method_id = "pm_token_123"
        mock_sub.consecutive_failures = 0
        mock_sub.last_attempt_at = mock_time - timedelta(hours=12)  # Списание было 12 часов назад
        # next_charge_at обновлен после успешного списания на +30 дней
        mock_sub.next_charge_at = mock_time + timedelta(days=30)
        
        # Настраиваем query для subscriptions
        # Фильтр next_charge_at <= now_utc должен исключить эту подписку
        mock_query = MagicMock()
        # Пустой список, т.к. next_charge_at > now
        mock_query.filter.return_value.order_by.return_value.limit.return_value = []
        
        # Setup charge attempt queries
        mock_attempt_query1 = MagicMock()
        mock_attempt_query1.filter.return_value.group_by.return_value.having.return_value.all.return_value = []
        mock_attempt_query2 = MagicMock()
        mock_attempt_query2.filter.return_value.group_by.return_value.all.return_value = []
        mock_attempt_query3 = MagicMock()
        mock_attempt_query3.filter.return_value.group_by.return_value.all.return_value = []
        
        from bot.utils.billing_db import Subscription, ChargeAttempt
        mock_session.query.side_effect = [
            mock_query,  # Первый запрос для subscriptions
            mock_attempt_query1,  # Для blocked_ids_day2
            mock_attempt_query2,  # Для blocked_ids_gap12h
            mock_attempt_query3,  # Для failed_counts
        ]
        
        # Execute - проверяем через 12 часов после успешного списания
        due = subscriptions_due(now=mock_time, limit=100)
        
        # Should NOT include subscription - период оплачен, next_charge_at в будущем
        assert len(due) == 0


def test_subscriptions_due_only_includes_due_subscriptions(mock_time):
    """Test that subscriptions_due only includes subscriptions where next_charge_at <= now."""
    from bot.utils.billing_db import _repo
    
    # Создаем мок сессии
    mock_session = MagicMock()
    
    # Создаем мок session_factory
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    
    # Патчим _session_factory репозитория
    with patch.object(_repo, '_session_factory', mock_session_factory), \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time), \
         patch('bot.utils.billing_db.to_utc_for_db') as mock_to_utc, \
         patch('bot.utils.billing_db.to_aware_msk') as mock_to_aware, \
         patch('bot.utils.billing_db.from_db_naive') as mock_from_db:
        
        # Настраиваем конвертацию времени
        mock_to_aware.return_value = mock_time
        mock_time_utc = datetime(2025, 1, 15, 9, 0, 0)  # UTC время
        mock_to_utc.return_value = mock_time_utc
        
        # Сценарий: у пользователя несколько активных подписок
        # 1. Подписка с next_charge_at в прошлом (должна попасть)
        # 2. Подписка с next_charge_at в будущем (НЕ должна попасть)
        
        # Подписка 1: срок наступил (должна попасть)
        mock_sub1 = MagicMock()
        mock_sub1.id = 1
        mock_sub1.user_id = 7833048230
        mock_sub1.plan_code = "1m"
        mock_sub1.interval_months = 1
        mock_sub1.amount_value = "2500.00"
        mock_sub1.amount_currency = "RUB"
        mock_sub1.payment_method_id = "pm_token_123"
        mock_sub1.consecutive_failures = 0
        mock_sub1.last_attempt_at = None
        mock_sub1.next_charge_at = mock_time - timedelta(days=1)  # Срок наступил
        
        # Подписка 2: период оплачен (НЕ должна попасть)
        mock_sub2 = MagicMock()
        mock_sub2.id = 2
        mock_sub2.user_id = 7833048230
        mock_sub2.plan_code = "3m"
        mock_sub2.interval_months = 3
        mock_sub2.amount_value = "6000.00"
        mock_sub2.amount_currency = "RUB"
        mock_sub2.payment_method_id = "pm_token_456"
        mock_sub2.consecutive_failures = 0
        mock_sub2.last_attempt_at = None
        mock_sub2.next_charge_at = mock_time + timedelta(days=60)  # Период оплачен
        
        # Настраиваем query для subscriptions
        # Фильтр next_charge_at <= now_utc должен включить только mock_sub1
        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.limit.return_value = [mock_sub1]
        
        # Настраиваем from_db_naive для конвертации next_charge_at
        def from_db_naive_side_effect(dt):
            if dt == mock_sub1.next_charge_at:
                return mock_time - timedelta(days=1)
            elif dt == mock_sub2.next_charge_at:
                return mock_time + timedelta(days=60)
            return dt
        
        mock_from_db.side_effect = from_db_naive_side_effect
        
        # Setup charge attempt queries
        mock_attempt_query1 = MagicMock()
        mock_attempt_query1.filter.return_value.group_by.return_value.having.return_value.all.return_value = []
        mock_attempt_query2 = MagicMock()
        mock_attempt_query2.filter.return_value.group_by.return_value.all.return_value = []
        mock_attempt_query3 = MagicMock()
        mock_attempt_query3.filter.return_value.group_by.return_value.all.return_value = []
        
        from bot.utils.billing_db import Subscription, ChargeAttempt
        mock_session.query.side_effect = [
            mock_query,  # Первый запрос для subscriptions
            mock_attempt_query1,  # Для blocked_ids_day2
            mock_attempt_query2,  # Для blocked_ids_gap12h
            mock_attempt_query3,  # Для failed_counts
        ]
        
        # Execute
        due = subscriptions_due(now=mock_time, limit=100)
        
        # Should only include subscription 1 (next_charge_at <= now)
        # Should NOT include subscription 2 (next_charge_at > now)
        assert len(due) == 1
        assert due[0]["id"] == 1


def test_subscriptions_due_no_repeat_charge_within_12h(mock_time):
    """Test that subscription is not charged again within 12 hours after successful charge."""
    from bot.utils.billing_db import _repo
    
    # Создаем мок сессии
    mock_session = MagicMock()
    
    # Создаем мок session_factory
    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__enter__.return_value = mock_session
    
    # Патчим _session_factory репозитория
    with patch.object(_repo, '_session_factory', mock_session_factory), \
         patch('bot.utils.billing_db.now_msk', return_value=mock_time), \
         patch('bot.utils.billing_db.to_utc_for_db') as mock_to_utc, \
         patch('bot.utils.billing_db.to_aware_msk') as mock_to_aware, \
         patch('bot.utils.billing_db.from_db_naive') as mock_from_db:
        
        # Настраиваем конвертацию времени
        mock_to_aware.return_value = mock_time
        mock_time_utc = datetime(2025, 1, 15, 9, 0, 0)  # UTC время
        mock_to_utc.return_value = mock_time_utc
        
        # Сценарий: 
        # 1. Подписка со сроком наступившим (next_charge_at <= now)
        # 2. Но была успешная попытка списания 6 часов назад
        # 3. Проверяем, что подписка блокируется правилом 12h gap
        
        # Подписка со сроком наступившим
        mock_sub = MagicMock()
        mock_sub.id = 1
        mock_sub.user_id = 7833048230
        mock_sub.plan_code = "1m"
        mock_sub.interval_months = 1
        mock_sub.amount_value = "2500.00"
        mock_sub.amount_currency = "RUB"
        mock_sub.payment_method_id = "pm_token_123"
        mock_sub.consecutive_failures = 0
        mock_sub.next_charge_at = mock_time - timedelta(days=1)  # Срок наступил
        
        # Настраиваем from_db_naive
        mock_from_db.return_value = mock_time - timedelta(days=1)
        
        # Настраиваем query для subscriptions
        mock_query = MagicMock()
        mock_query.filter.return_value.order_by.return_value.limit.return_value = [mock_sub]
        
        # Setup charge attempt queries
        # blocked_ids_day2: нет 2 попыток за 24 часа
        mock_attempt_query1 = MagicMock()
        mock_attempt_query1.filter.return_value.group_by.return_value.having.return_value.all.return_value = []
        
        # blocked_ids_gap12h: есть попытка 6 часов назад (в пределах 12h окна)
        mock_attempt_query2 = MagicMock()
        # Возвращаем subscription_id=1, т.к. есть попытка в пределах 12 часов
        mock_attempt_query2.filter.return_value.group_by.return_value.all.return_value = [(1,)]
        
        # failed_counts: нет неуспешных попыток
        mock_attempt_query3 = MagicMock()
        mock_attempt_query3.filter.return_value.group_by.return_value.all.return_value = []
        
        from bot.utils.billing_db import Subscription, ChargeAttempt
        mock_session.query.side_effect = [
            mock_query,  # Первый запрос для subscriptions
            mock_attempt_query1,  # Для blocked_ids_day2
            mock_attempt_query2,  # Для blocked_ids_gap12h (блокирует!)
            mock_attempt_query3,  # Для failed_counts
        ]
        
        # Execute
        due = subscriptions_due(now=mock_time, limit=100)
        
        # Should be blocked by 12h gap rule - не должно быть повторного списания
        assert len(due) == 0
