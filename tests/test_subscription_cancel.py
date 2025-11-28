"""
Tests for subscription_cancel_for_user with real database.
"""
import time
import pytest
from datetime import datetime, timedelta
from bot.utils.billing_db import Subscription
from bot.utils.time_helpers import now_msk, from_db_naive


def test_subscription_cancel_for_user_single(in_memory_db):
    """Test canceling single subscription with real field checks."""
    repo, SessionLocal = in_memory_db
    
    # Create active subscription
    now = now_msk()
    sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        status="active",
        next_charge_at=now + timedelta(days=30)
    )
    
    # Remember original values
    with SessionLocal() as s:
        original_sub = s.get(Subscription, sub_id)
        original_pm_id = original_sub.payment_method_id
        original_next_charge = original_sub.next_charge_at
    
    # Small delay to ensure timestamps differ
    time.sleep(0.1)
    
    # Cancel
    count = repo.subscription_cancel_for_user(user_id=123)
    assert count == 1
    
    # Check real changes in DB
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub.status == "canceled"
        assert sub.cancel_at is not None
        assert sub.payment_method_id is None  # Cleared
        assert sub.next_charge_at is None  # Cleared
        assert sub.updated_at is not None
        # Compare in same timezone (MSK)
        updated_at_msk = from_db_naive(sub.updated_at)
        original_updated_at_msk = from_db_naive(original_sub.updated_at)
        assert updated_at_msk > original_updated_at_msk


def test_subscription_cancel_for_user_multiple(in_memory_db):
    """Test canceling multiple subscriptions for one user."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create multiple active subscriptions
    sub_id1 = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        status="active",
        next_charge_at=now + timedelta(days=30)
    )
    
    sub_id2 = repo.subscription_upsert(
        user_id=123,
        plan_code="3m",
        interval_months=3,
        amount_value="6490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_456",
        status="active",
        next_charge_at=now + timedelta(days=90)
    )
    
    # Cancel all
    count = repo.subscription_cancel_for_user(user_id=123)
    assert count == 2
    
    # Check both are canceled
    with SessionLocal() as s:
        sub1 = s.get(Subscription, sub_id1)
        sub2 = s.get(Subscription, sub_id2)
        
        assert sub1.status == "canceled"
        assert sub2.status == "canceled"
        assert sub1.payment_method_id is None
        assert sub2.payment_method_id is None
        assert sub1.next_charge_at is None
        assert sub2.next_charge_at is None
        assert sub1.cancel_at is not None
        assert sub2.cancel_at is not None


def test_subscription_cancel_for_user_updates_fields(in_memory_db):
    """Test that all required fields are updated correctly."""
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
        status="active",
        next_charge_at=now + timedelta(days=30)
    )
    
    # Get original updated_at
    with SessionLocal() as s:
        original_sub = s.get(Subscription, sub_id)
        original_updated_at = original_sub.updated_at
    
    # Small delay to ensure timestamps differ
    time.sleep(0.1)
    
    # Cancel
    repo.subscription_cancel_for_user(user_id=123)
    
    # Check all fields
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        
        # Status changed
        assert sub.status == "canceled"
        
        # cancel_at set
        assert sub.cancel_at is not None
        cancel_at_msk = from_db_naive(sub.cancel_at)
        assert abs((cancel_at_msk - now).total_seconds()) < 60
        
        # payment_method_id cleared
        assert sub.payment_method_id is None
        
        # next_charge_at cleared
        assert sub.next_charge_at is None
        
        # updated_at updated (compare in same timezone)
        updated_at_msk = from_db_naive(sub.updated_at)
        original_updated_at_msk = from_db_naive(original_updated_at)
        assert updated_at_msk > original_updated_at_msk


def test_subscription_cancel_for_user_only_active(in_memory_db):
    """Test that only active subscriptions are canceled."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create active subscription
    sub_id1 = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        status="active",
        payment_method_id="pm_token_123",
        next_charge_at=now + timedelta(days=30),
        amount_value="2490.00",
        amount_currency="RUB",
        interval_months=1
    )
    
    # Create canceled subscription
    sub_id2 = repo.subscription_upsert(
        user_id=123,
        plan_code="3m",
        status="canceled",
        payment_method_id="pm_token_456",
        next_charge_at=None,
        amount_value="6490.00",
        amount_currency="RUB",
        interval_months=3
    )
    
    # Cancel (should only affect active)
    count = repo.subscription_cancel_for_user(user_id=123)
    assert count == 1  # Only one active subscription
    
    # Check
    with SessionLocal() as s:
        sub1 = s.get(Subscription, sub_id1)
        sub2 = s.get(Subscription, sub_id2)
        
        # Active one is now canceled
        assert sub1.status == "canceled"
        
        # Already canceled one stays canceled
        assert sub2.status == "canceled"


def test_subscription_cancel_for_user_different_users(in_memory_db):
    """Test that canceling for one user doesn't affect others."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create subscriptions for different users
    sub_id1 = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        status="active",
        payment_method_id="pm_token_123",
        next_charge_at=now + timedelta(days=30),
        amount_value="2490.00",
        amount_currency="RUB",
        interval_months=1
    )
    
    sub_id2 = repo.subscription_upsert(
        user_id=456,
        plan_code="1m",
        status="active",
        payment_method_id="pm_token_456",
        next_charge_at=now + timedelta(days=30),
        amount_value="2490.00",
        amount_currency="RUB",
        interval_months=1
    )
    
    # Cancel for user 123 only
    count = repo.subscription_cancel_for_user(user_id=123)
    assert count == 1
    
    # Check
    with SessionLocal() as s:
        sub1 = s.get(Subscription, sub_id1)
        sub2 = s.get(Subscription, sub_id2)
        
        # User 123's subscription is canceled
        assert sub1.status == "canceled"
        
        # User 456's subscription is still active
        assert sub2.status == "active"
        assert sub2.payment_method_id == "pm_token_456"

