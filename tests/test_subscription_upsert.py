"""
Tests for subscription_upsert with real database.
"""
import pytest
from datetime import datetime, timedelta
from bot.utils.billing_db import Subscription
from bot.utils.time_helpers import now_msk, from_db_naive


def test_subscription_upsert_creates_new(in_memory_db):
    """Test creating new subscription with real database check."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    next_charge = now + timedelta(days=30)
    
    # Call function
    sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=next_charge,
        status="active"
    )
    
    # Check real record in DB (not mock!)
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub is not None
        assert sub.user_id == 123
        assert sub.plan_code == "1m"
        assert sub.interval_months == 1
        assert sub.amount_value == "2490.00"
        assert sub.amount_currency == "RUB"
        assert sub.payment_method_id == "pm_token_123"
        assert sub.status == "active"
        
        # Check time conversion (MSK -> UTC in DB)
        assert sub.next_charge_at is not None
        # next_charge_at in DB should be in UTC
        next_charge_msk = from_db_naive(sub.next_charge_at)
        # Check that time is correct (with conversion tolerance)
        assert abs((next_charge_msk - next_charge).total_seconds()) < 60
        
        # Check created_at and updated_at
        assert sub.created_at is not None
        assert sub.updated_at is not None


def test_subscription_upsert_updates_existing(in_memory_db):
    """Test updating existing subscription."""
    repo, SessionLocal = in_memory_db
    
    # 1. Create subscription
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
    
    # Remember created_at (should not change)
    with SessionLocal() as s:
        original_sub = s.get(Subscription, sub_id)
        original_created_at = original_sub.created_at
    
    # 2. Update with new data
    new_next_charge = now + timedelta(days=60)
    updated_sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=3,  # Changed
        amount_value="6490.00",  # Changed
        amount_currency="RUB",
        payment_method_id="pm_token_456",  # Changed
        next_charge_at=new_next_charge,
        status="active"
    )
    
    # 3. Check that same record was updated (not created new)
    assert updated_sub_id == sub_id  # Same ID
    
    with SessionLocal() as s:
        subs = s.query(Subscription).filter(
            Subscription.user_id == 123,
            Subscription.plan_code == "1m"
        ).all()
        assert len(subs) == 1  # Only one record
        
        updated_sub = s.get(Subscription, sub_id)
        assert updated_sub.interval_months == 3
        assert updated_sub.amount_value == "6490.00"
        assert updated_sub.payment_method_id == "pm_token_456"
        
        # created_at should not change
        assert updated_sub.created_at == original_created_at
        # updated_at should be updated
        assert updated_sub.updated_at > original_sub.updated_at


def test_subscription_upsert_prevents_duplicates(in_memory_db):
    """Test duplicate prevention through SELECT FOR UPDATE."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create first subscription
    sub_id1 = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=now + timedelta(days=30),
        status="active"
    )
    
    # Try to create second with same parameters
    # In reality SELECT FOR UPDATE blocks second call
    # But in test we just check that new one was not created
    sub_id2 = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=now + timedelta(days=30),
        status="active"
    )
    
    # Check that only one record
    with SessionLocal() as s:
        subs = s.query(Subscription).filter(
            Subscription.user_id == 123,
            Subscription.plan_code == "1m",
            Subscription.status == "active"
        ).all()
        assert len(subs) == 1
        assert sub_id1 == sub_id2  # Same ID


def test_subscription_upsert_reactivates_canceled(in_memory_db):
    """Test reactivating canceled subscription."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create and cancel subscription
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
    
    # Cancel it
    repo.subscription_cancel_for_user(user_id=123)
    
    # Check it's canceled
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub.status == "canceled"
    
    # Reactivate by upsert
    reactivated_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_456",
        next_charge_at=now + timedelta(days=30),
        status="active"
    )
    
    # Should be same subscription, reactivated
    assert reactivated_id == sub_id
    
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub.status == "active"
        assert sub.payment_method_id == "pm_token_456"


def test_subscription_upsert_update_payment_method_flag(in_memory_db):
    """Test update_payment_method flag behavior."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create subscription with payment method
    sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=now + timedelta(days=30),
        status="active",
        update_payment_method=True
    )
    
    # Update with update_payment_method=False and payment_method_id=None
    # Should not update payment_method_id
    repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id=None,  # None with update_payment_method=False
        next_charge_at=now + timedelta(days=60),
        status="active",
        update_payment_method=False
    )
    
    # Check that payment_method_id was not changed
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub.payment_method_id == "pm_token_123"  # Still original
    
    # Update with update_payment_method=True and payment_method_id=None
    # Should update to None
    repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id=None,
        next_charge_at=now + timedelta(days=60),
        status="active",
        update_payment_method=True
    )
    
    # Check that payment_method_id was updated to None
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub.payment_method_id is None

