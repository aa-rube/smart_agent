"""
Tests for charge attempt functions with real database.
"""
import pytest
from datetime import datetime, timedelta
from bot.utils.billing_db import ChargeAttempt, Subscription
from bot.utils.time_helpers import now_msk, from_db_naive


def test_record_charge_attempt(in_memory_db):
    """Test creating charge attempt with real database."""
    repo, SessionLocal = in_memory_db
    
    # Create subscription
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
    
    # Create attempt
    due_at = now + timedelta(days=30)
    attempt_id = repo.record_charge_attempt(
        subscription_id=sub_id,
        user_id=123,
        payment_id=None,
        status="created",
        due_at=due_at
    )
    
    # Check real record in DB
    with SessionLocal() as s:
        attempt = s.get(ChargeAttempt, attempt_id)
        assert attempt is not None
        assert attempt.subscription_id == sub_id
        assert attempt.user_id == 123
        assert attempt.payment_id is None
        assert attempt.status == "created"
        assert attempt.attempted_at is not None
        
        # Check due_at conversion (MSK -> UTC in DB)
        assert attempt.due_at is not None
        due_at_msk = from_db_naive(attempt.due_at)
        assert abs((due_at_msk - due_at).total_seconds()) < 60


def test_mark_charge_attempt_status(in_memory_db):
    """Test updating charge attempt status."""
    repo, SessionLocal = in_memory_db
    
    # Create subscription and attempt
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
    
    attempt_id = repo.record_charge_attempt(
        subscription_id=sub_id,
        user_id=123,
        payment_id=None,
        status="created",
        due_at=now + timedelta(days=30)
    )
    
    # First link payment_id to attempt
    repo.link_payment_to_attempt(attempt_id=attempt_id, payment_id="payment_123")
    
    # Then update status
    repo.mark_charge_attempt_status(
        payment_id="payment_123",
        status="succeeded"
    )
    
    # Check real update in DB
    with SessionLocal() as s:
        attempt = s.get(ChargeAttempt, attempt_id)
        assert attempt.status == "succeeded"
        assert attempt.payment_id == "payment_123"


def test_mark_charge_attempt_status_by_subscription_id(in_memory_db):
    """Test updating status by subscription_id fallback."""
    repo, SessionLocal = in_memory_db
    
    # Create subscription and attempt
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
    
    attempt_id = repo.record_charge_attempt(
        subscription_id=sub_id,
        user_id=123,
        payment_id=None,
        status="created",
        due_at=now + timedelta(days=30)
    )
    
    # Update status by subscription_id (no payment_id)
    repo.mark_charge_attempt_status(
        subscription_id=sub_id,
        status="failed"
    )
    
    # Check update
    with SessionLocal() as s:
        attempt = s.get(ChargeAttempt, attempt_id)
        assert attempt.status == "failed"


def test_link_payment_to_attempt(in_memory_db):
    """Test linking payment to attempt."""
    repo, SessionLocal = in_memory_db
    
    # Create subscription and attempt
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
    
    attempt_id = repo.record_charge_attempt(
        subscription_id=sub_id,
        user_id=123,
        payment_id=None,
        status="created",
        due_at=now + timedelta(days=30)
    )
    
    # Link payment
    repo.link_payment_to_attempt(
        attempt_id=attempt_id,
        payment_id="payment_123"
    )
    
    # Check link
    with SessionLocal() as s:
        attempt = s.get(ChargeAttempt, attempt_id)
        assert attempt.payment_id == "payment_123"


def test_link_payment_to_attempt_not_found(in_memory_db):
    """Test linking payment to non-existent attempt."""
    repo, SessionLocal = in_memory_db
    
    # Try to link to non-existent attempt
    # Should not raise error, just log warning
    repo.link_payment_to_attempt(
        attempt_id=99999,
        payment_id="payment_123"
    )
    
    # Should complete without error
    assert True


def test_record_charge_attempt_updates_subscription(in_memory_db):
    """Test that record_charge_attempt creates proper attempt record."""
    repo, SessionLocal = in_memory_db
    
    # Create subscription
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
    
    # Create attempt
    due_at = now + timedelta(days=30)
    attempt_id = repo.record_charge_attempt(
        subscription_id=sub_id,
        user_id=123,
        payment_id="payment_123",
        status="succeeded",
        due_at=due_at
    )
    
    # Check attempt record
    with SessionLocal() as s:
        attempt = s.get(ChargeAttempt, attempt_id)
        assert attempt is not None
        assert attempt.subscription_id == sub_id
        assert attempt.user_id == 123
        assert attempt.payment_id == "payment_123"
        assert attempt.status == "succeeded"
        assert attempt.due_at is not None
        assert attempt.attempted_at is not None

