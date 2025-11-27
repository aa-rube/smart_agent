"""
Integration tests for billing_loop with real database.
"""
import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
from bot.utils.billing_db import ChargeAttempt, Subscription
from bot.utils.time_helpers import now_msk


@pytest.mark.asyncio
async def test_billing_loop_full_cycle(in_memory_db, mock_bot):
    """Test full billing_loop cycle with real database."""
    repo, SessionLocal = in_memory_db
    
    # 1. Create real subscription in DB
    now = now_msk()
    next_charge = now - timedelta(days=1)  # Overdue
    
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
    
    # 2. Check subscription is created
    with SessionLocal() as s:
        sub = s.get(Subscription, sub_id)
        assert sub is not None
        assert sub.status == "active"
    
    # 3. Mock only external dependencies
    shutdown_event = asyncio.Event()
    
    with patch('bot.run.billing_db._repo', repo), \
         patch('bot.run.billing_db.subscriptions_due', repo.subscriptions_due), \
         patch('bot.run.billing_db.precharge_guard_and_attempt', repo.precharge_guard_and_attempt), \
         patch('bot.run.billing_db.link_payment_to_attempt', repo.link_payment_to_attempt), \
         patch('bot.run.youmoney.charge_saved_method') as mock_charge, \
         patch('bot.run.shutdown_event', shutdown_event):
        
        mock_charge.return_value = "payment_123"
        
        # 4. Call one iteration of billing_loop
        from bot.run import billing_loop
        
        # Create task
        task = asyncio.create_task(billing_loop())
        await asyncio.sleep(0.2)  # Give time for one iteration
        shutdown_event.set()
        
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # 5. Check real results in DB
        with SessionLocal() as s:
            # Check that charge attempt was created
            attempt = s.query(ChargeAttempt).filter(
                ChargeAttempt.subscription_id == sub_id
            ).first()
            assert attempt is not None
            assert attempt.status == "created"
            assert attempt.payment_id == "payment_123"  # Linked to payment
            
            # Check that call was correct
            mock_charge.assert_called_once()
            call_kwargs = mock_charge.call_args[1]
            assert call_kwargs['user_id'] == 123
            assert call_kwargs['payment_method_id'] == "pm_token_123"
            assert call_kwargs['amount_rub'] == "2490.00"


@pytest.mark.asyncio
async def test_billing_loop_handles_errors(in_memory_db, mock_bot):
    """Test error handling in billing_loop."""
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
        next_charge_at=now - timedelta(days=1),
        status="active"
    )
    
    shutdown_event = asyncio.Event()
    
    with patch('bot.run.billing_db._repo', repo), \
         patch('bot.run.billing_db.subscriptions_due', repo.subscriptions_due), \
         patch('bot.run.billing_db.precharge_guard_and_attempt', repo.precharge_guard_and_attempt), \
         patch('bot.run.youmoney.charge_saved_method') as mock_charge, \
         patch('bot.run.shutdown_event', shutdown_event):
        
        # Simulate error
        mock_charge.side_effect = ValueError("Invalid payment method")
        
        # Call billing_loop
        from bot.run import billing_loop
        
        task = asyncio.create_task(billing_loop())
        await asyncio.sleep(0.2)
        shutdown_event.set()
        
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Check that:
        # 1. Attempt was created
        with SessionLocal() as s:
            attempt = s.query(ChargeAttempt).filter(
                ChargeAttempt.subscription_id == sub_id
            ).first()
            assert attempt is not None
            
            # 2. Attempt status = "failed"
            assert attempt.status == "failed"


@pytest.mark.asyncio
async def test_billing_loop_skips_duplicates(in_memory_db, mock_bot):
    """Test that billing_loop skips duplicate attempts."""
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
        next_charge_at=now - timedelta(days=1),
        status="active"
    )
    
    # Create existing attempt with payment_id
    attempt_id = repo.record_charge_attempt(
        subscription_id=sub_id,
        user_id=123,
        payment_id="existing_payment_123",
        status="created",
        due_at=now - timedelta(days=1)
    )
    
    shutdown_event = asyncio.Event()
    
    with patch('bot.run.billing_db._repo', repo), \
         patch('bot.run.billing_db.subscriptions_due', repo.subscriptions_due), \
         patch('bot.run.youmoney.charge_saved_method') as mock_charge, \
         patch('bot.run.shutdown_event', shutdown_event):
        
        # Call billing_loop
        from bot.run import billing_loop
        
        task = asyncio.create_task(billing_loop())
        await asyncio.sleep(0.2)
        shutdown_event.set()
        
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Check that charge_saved_method was NOT called (duplicate skipped)
        mock_charge.assert_not_called()
        
        # Check that no new attempt was created
        with SessionLocal() as s:
            attempts = s.query(ChargeAttempt).filter(
                ChargeAttempt.subscription_id == sub_id
            ).all()
            assert len(attempts) == 1  # Only the existing one
            assert attempts[0].id == attempt_id


@pytest.mark.asyncio
async def test_billing_loop_respects_guard_rules(in_memory_db, mock_bot):
    """Test that billing_loop respects guard rules."""
    repo, SessionLocal = in_memory_db
    
    # Create subscription with 6 failures (should be blocked)
    now = now_msk()
    sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_123",
        next_charge_at=now - timedelta(days=1),
        status="active"
    )
    
    # Set consecutive_failures to 6
    with SessionLocal() as s, s.begin():
        sub = s.get(Subscription, sub_id)
        sub.consecutive_failures = 6
        s.flush()
    
    shutdown_event = asyncio.Event()
    
    with patch('bot.run.billing_db._repo', repo), \
         patch('bot.run.billing_db.subscriptions_due', repo.subscriptions_due), \
         patch('bot.run.youmoney.charge_saved_method') as mock_charge, \
         patch('bot.run.shutdown_event', shutdown_event):
        
        # Call billing_loop
        from bot.run import billing_loop
        
        task = asyncio.create_task(billing_loop())
        await asyncio.sleep(0.2)
        shutdown_event.set()
        
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Check that charge_saved_method was NOT called (blocked by guard)
        mock_charge.assert_not_called()
        
        # Check that no attempt was created
        with SessionLocal() as s:
            attempts = s.query(ChargeAttempt).filter(
                ChargeAttempt.subscription_id == sub_id
            ).all()
            assert len(attempts) == 0


@pytest.mark.asyncio
async def test_billing_loop_skips_no_payment_method(in_memory_db, mock_bot):
    """Test that billing_loop skips subscriptions without payment_method_id."""
    repo, SessionLocal = in_memory_db
    
    # Create subscription without payment_method_id
    now = now_msk()
    sub_id = repo.subscription_upsert(
        user_id=123,
        plan_code="1m",
        interval_months=1,
        amount_value="2490.00",
        amount_currency="RUB",
        payment_method_id=None,  # No payment method
        next_charge_at=now - timedelta(days=1),
        status="active"
    )
    
    shutdown_event = asyncio.Event()
    
    with patch('bot.run.billing_db._repo', repo), \
         patch('bot.run.billing_db.subscriptions_due', repo.subscriptions_due), \
         patch('bot.run.youmoney.charge_saved_method') as mock_charge, \
         patch('bot.run.shutdown_event', shutdown_event):
        
        # Call billing_loop
        from bot.run import billing_loop
        
        task = asyncio.create_task(billing_loop())
        await asyncio.sleep(0.2)
        shutdown_event.set()
        
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Check that charge_saved_method was NOT called
        mock_charge.assert_not_called()


@pytest.mark.asyncio
async def test_billing_loop_handles_multiple_subscriptions(in_memory_db, mock_bot):
    """Test that billing_loop processes multiple subscriptions."""
    repo, SessionLocal = in_memory_db
    
    now = now_msk()
    
    # Create multiple subscriptions
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
    
    sub_id2 = repo.subscription_upsert(
        user_id=456,
        plan_code="3m",
        interval_months=3,
        amount_value="6490.00",
        amount_currency="RUB",
        payment_method_id="pm_token_456",
        next_charge_at=now - timedelta(days=1),
        status="active"
    )
    
    shutdown_event = asyncio.Event()
    
    with patch('bot.run.billing_db._repo', repo), \
         patch('bot.run.billing_db.subscriptions_due', repo.subscriptions_due), \
         patch('bot.run.billing_db.precharge_guard_and_attempt', repo.precharge_guard_and_attempt), \
         patch('bot.run.billing_db.link_payment_to_attempt', repo.link_payment_to_attempt), \
         patch('bot.run.youmoney.charge_saved_method') as mock_charge, \
         patch('bot.run.shutdown_event', shutdown_event):
        
        mock_charge.side_effect = ["payment_123", "payment_456"]
        
        # Call billing_loop
        from bot.run import billing_loop
        
        task = asyncio.create_task(billing_loop())
        await asyncio.sleep(0.3)
        shutdown_event.set()
        
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except asyncio.TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Check that both subscriptions were processed
        assert mock_charge.call_count == 2
        
        # Check that both attempts were created
        with SessionLocal() as s:
            attempts = s.query(ChargeAttempt).filter(
                ChargeAttempt.subscription_id.in_([sub_id1, sub_id2])
            ).all()
            assert len(attempts) == 2

