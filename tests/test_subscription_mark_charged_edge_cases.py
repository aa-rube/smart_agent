"""
Edge cases tests for subscription_mark_charged_for_user with real database.
"""
import pytest
from datetime import datetime, timedelta
from bot.utils.billing_db import Subscription
from bot.utils.time_helpers import now_msk, from_db_naive


def test_subscription_mark_charged_wrong_user_id(in_memory_db):
    """Test security: cannot update another user's subscription."""
    repo, SessionLocal = in_memory_db
    
    # Create subscription for user_id=123
    now = now_msk()
    sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=now + timedelta(days=30),
        status="active"
    )
    
    # Remember original next_charge_at
    with SessionLocal() as s:
        original_sub = s.get(Subscription, sub_id)
        original_next_charge = original_sub.next_charge_at
    
    # Try to update from user_id=999 (different user)
    result = repo.subscription_mark_charged_for_user(
        user_id=999,  # Different user_id
        subscription_id=sub_id,
        next_charge_at=now + timedelta(days=60)
    )
    
    # Should return None (not updated)
    assert result is None
    
    # Check that subscription did NOT change
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub.next_charge_at == original_next_charge  # Not changed
        assert sub.user_id == 123  # Still correct user_id


def test_subscriptions_due_excludes_none_next_charge_at(in_memory_db):
    """Test filtering subscriptions with next_charge_at = None through real SQL."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create subscription with next_charge_at = None
    repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=None,  # None value
        status="active"
    )
    
    # Create subscription with valid next_charge_at (for comparison)
    repo.subscription_upsert(
        user_id=456,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_456",
        next_charge_at=now - timedelta(days=1),  # Overdue
        status="active"
    )
    
    # Call subscriptions_due
    due = repo.subscriptions_due(now=now, limit=100)
    
    # Check that:
    # 1. Subscription with None did NOT get into selection
    # 2. Subscription with valid next_charge_at got in
    user_ids = [sub["user_id"] for sub in due]
    assert 123 not in user_ids  # None - excluded
    assert 456 in user_ids  # Valid - included
    
    # Check real SQL query: filter next_charge_at != None
    # This is checked through result, not through mock query


def test_subscriptions_due_excludes_no_payment_method(in_memory_db):
    """Test filtering subscriptions without payment_method_id."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create subscription without payment_method_id
    repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id=None,  # No payment method
        next_charge_at=now - timedelta(days=1),  # Overdue
        status="active"
    )
    
    # Create subscription with payment_method_id (for comparison)
    repo.subscription_upsert(
        user_id=456,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_456",  # Has payment method
        next_charge_at=now - timedelta(days=1),  # Overdue
        status="active"
    )
    
    # Call subscriptions_due
    due = repo.subscriptions_due(now=now, limit=100)
    
    # Check that subscription without payment_method_id is excluded
    user_ids = [sub["user_id"] for sub in due]
    assert 123 not in user_ids  # No payment method - excluded
    assert 456 in user_ids  # Has payment method - included


def test_subscriptions_due_excludes_6_failed_in_cycle(in_memory_db):
    """Test filtering subscriptions with 6 failed attempts in current cycle."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create subscription
    sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=now - timedelta(days=1),  # Overdue
        status="active"
    )
    
    # Create 6 failed attempts for this cycle
    due_at = now - timedelta(days=1)
    for i in range(6):
        repo.record_charge_attempt(
            subscription_id=sub_id,
            user_id=123,
            payment_id=None,
            status="canceled",  # Failed status
            due_at=due_at
        )
    
    # Call subscriptions_due
    due = repo.subscriptions_due(now=now, limit=100)
    
    # Check that subscription is excluded (6 failures in cycle)
    user_ids = [sub["user_id"] for sub in due]
    assert 123 not in user_ids  # Excluded due to 6 failures


def test_precharge_guard_edge_cases(in_memory_db):
    """Test edge cases for precharge_guard_and_attempt."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Test 1: consecutive_failures = 5 (boundary value, should pass)
    sub_id1 = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=now - timedelta(days=1),
        status="active"
    )
    
    # Set consecutive_failures to 5
    with SessionLocal() as s, s.begin():
        sub = s.get(Subscription, sub_id1)
        sub.consecutive_failures = 5
        s.flush()
    
    # Should pass (5 < 6)
    attempt_id = repo.precharge_guard_and_attempt(
        subscription_id=sub_id1,
        now=now,
        user_id=123
    )
    assert attempt_id is not None
    
    # Test 2: last_attempt_at = None (should pass)
    sub_id2 = repo.subscription_upsert(
        user_id=456,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_456",
        next_charge_at=now - timedelta(days=1),
        status="active"
    )
    
    # last_attempt_at is None by default
    attempt_id = repo.precharge_guard_and_attempt(
        subscription_id=sub_id2,
        now=now,
        user_id=456
    )
    assert attempt_id is not None
    
    # Test 3: last_attempt_at exactly 12 hours ago (boundary value)
    sub_id3 = repo.subscription_upsert(
        user_id=789,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_789",
        next_charge_at=now - timedelta(days=1),
        status="active"
    )
    
    # Set last_attempt_at to exactly 12 hours ago
    from bot.utils.time_helpers import to_utc_for_db
    with SessionLocal() as s, s.begin():
        sub = s.get(Subscription, sub_id3)
        sub.last_attempt_at = to_utc_for_db(now - timedelta(hours=12))
        s.flush()
    
    # Should pass (exactly 12 hours = pass, blocked only if < 12 hours)
    # Logic uses strict <, so exactly 12 hours should pass
    attempt_id = repo.precharge_guard_and_attempt(
        subscription_id=sub_id3,
        now=now,
        user_id=789
    )
    assert attempt_id is not None  # Should pass (exactly 12 hours = pass)


def test_subscription_mark_charged_consecutive_failures_reset(in_memory_db):
    """Test that consecutive_failures is reset to 0 on successful charge."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create subscription with failures
    sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=now + timedelta(days=30),
        status="active"
    )
    
    # Set consecutive_failures to 3
    with SessionLocal() as s, s.begin():
        sub = s.get(Subscription, sub_id)
        sub.consecutive_failures = 3
        s.flush()
    
    # Mark as charged
    result = repo.subscription_mark_charged_for_user(
        user_id=123,
        subscription_id=sub_id,
        next_charge_at=now + timedelta(days=60)
    )
    
    assert result == sub_id
    
    # Check that consecutive_failures is reset to 0
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub.consecutive_failures == 0

