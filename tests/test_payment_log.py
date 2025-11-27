"""
Tests for payment_log functions with real database.
"""
import pytest
import json
from bot.utils.billing_db import PaymentLog
from bot.utils.time_helpers import now_msk, from_db_naive


def test_payment_log_upsert_creates_new(in_memory_db):
    """Test creating new payment_log record."""
    repo, SessionLocal = in_memory_db
    
    metadata = {"plan_code": "1m", "user_id": "123"}
    raw_payload = {"event": "payment.succeeded", "test": "data"}
    
    repo.payment_log_upsert(
        payment_id="payment_123",
        user_id=123,
        amount_value="2490.00",
        amount_currency="RUB",
        event="payment.succeeded",
        status="succeeded",
        metadata=metadata,
        raw_payload=raw_payload
    )
    
    # Check real record
    with SessionLocal() as s:
        log = s.get(PaymentLog, "payment_123")
        assert log is not None
        assert log.user_id == 123
        assert log.amount_value == "2490.00"
        assert log.amount_currency == "RUB"
        assert log.event == "payment.succeeded"
        assert log.status == "succeeded"
        
        # Check JSON fields
        metadata_parsed = json.loads(log.metadata_json)
        assert metadata_parsed["plan_code"] == "1m"
        assert metadata_parsed["user_id"] == "123"
        
        raw_payload_parsed = json.loads(log.raw_payload_json)
        assert raw_payload_parsed["event"] == "payment.succeeded"
        assert raw_payload_parsed["test"] == "data"
        
        # Check timestamps
        assert log.created_at is not None
        assert log.processed_at is None  # Not processed yet


def test_payment_log_upsert_updates_existing(in_memory_db):
    """Test updating existing payment_log record."""
    repo, SessionLocal = in_memory_db
    
    # Create record
    repo.payment_log_upsert(
        payment_id="payment_123",
        user_id=123,
        amount_value="2490.00",
        amount_currency="RUB",
        event="payment.waiting_for_capture",
        status="waiting_for_capture",
        metadata={"plan_code": "1m"},
        raw_payload={"event": "waiting_for_capture"}
    )
    
    # Update with new data
    new_metadata = {"plan_code": "3m", "user_id": "123"}
    new_payload = {"event": "payment.succeeded", "status": "succeeded"}
    
    repo.payment_log_upsert(
        payment_id="payment_123",  # Same payment_id
        user_id=123,
        amount_value="6490.00",  # Changed
        amount_currency="RUB",
        event="payment.succeeded",  # Changed
        status="succeeded",  # Changed
        metadata=new_metadata,
        raw_payload=new_payload
    )
    
    # Check update
    with SessionLocal() as s:
        log = s.get(PaymentLog, "payment_123")
        assert log.event == "payment.succeeded"
        assert log.status == "succeeded"
        assert log.amount_value == "6490.00"
        
        # Check JSON updated
        metadata_parsed = json.loads(log.metadata_json)
        assert metadata_parsed["plan_code"] == "3m"


def test_payment_log_is_processed(in_memory_db):
    """Test checking if payment is processed."""
    repo, SessionLocal = in_memory_db
    
    # Create unprocessed record
    repo.payment_log_upsert(
        payment_id="payment_123",
        user_id=123,
        amount_value="2490.00",
        amount_currency="RUB",
        event="payment.succeeded",
        status="succeeded",
        metadata={},
        raw_payload={}
    )
    
    # Check not processed
    assert repo.payment_log_is_processed("payment_123") is False
    
    # Mark as processed
    repo.payment_log_mark_processed("payment_123")
    
    # Check processed
    assert repo.payment_log_is_processed("payment_123") is True
    
    # Check in DB
    with SessionLocal() as s:
        log = s.get(PaymentLog, "payment_123")
        assert log.processed_at is not None


def test_payment_log_mark_processed(in_memory_db):
    """Test marking payment as processed."""
    repo, SessionLocal = in_memory_db
    
    # Create record
    repo.payment_log_upsert(
        payment_id="payment_123",
        user_id=123,
        amount_value="2490.00",
        amount_currency="RUB",
        event="payment.succeeded",
        status="succeeded",
        metadata={},
        raw_payload={}
    )
    
    # Check not processed
    with SessionLocal() as s:
        log = s.get(PaymentLog, "payment_123")
        assert log.processed_at is None
    
    # Mark as processed
    repo.payment_log_mark_processed("payment_123")
    
    # Check processed
    with SessionLocal() as s:
        log = s.get(PaymentLog, "payment_123")
        assert log.processed_at is not None
        processed_at_msk = from_db_naive(log.processed_at)
        now = now_msk()
        # Should be recent
        assert abs((processed_at_msk - now).total_seconds()) < 60


def test_payment_log_mark_processed_creates_if_not_exists(in_memory_db):
    """Test that mark_processed creates record if not exists."""
    repo, SessionLocal = in_memory_db
    
    # Mark non-existent payment as processed
    repo.payment_log_mark_processed("payment_999")
    
    # Check record created
    with SessionLocal() as s:
        log = s.get(PaymentLog, "payment_999")
        assert log is not None
        assert log.processed_at is not None


def test_payment_log_handles_none_values(in_memory_db):
    """Test that None values are handled correctly."""
    repo, SessionLocal = in_memory_db
    
    # Create record with None values
    repo.payment_log_upsert(
        payment_id="payment_123",
        user_id=None,
        amount_value=None,
        amount_currency=None,
        event=None,
        status=None,
        metadata=None,
        raw_payload=None
    )
    
    # Check record
    with SessionLocal() as s:
        log = s.get(PaymentLog, "payment_123")
        assert log is not None
        assert log.payment_id == "payment_123"
        # None values are allowed
        assert log.user_id is None or log.user_id == 0
        assert log.amount_value is None or log.amount_value == ""
        assert log.event is None or log.event == ""
        assert log.status is None or log.status == ""


def test_payment_log_json_encoding(in_memory_db):
    """Test JSON encoding with special characters."""
    repo, SessionLocal = in_memory_db
    
    # Create record with special characters
    metadata = {"plan_code": "1m", "description": "Тест с кириллицей"}
    raw_payload = {"event": "payment.succeeded", "data": {"key": "value"}}
    
    repo.payment_log_upsert(
        payment_id="payment_123",
        user_id=123,
        amount_value="2490.00",
        amount_currency="RUB",
        event="payment.succeeded",
        status="succeeded",
        metadata=metadata,
        raw_payload=raw_payload
    )
    
    # Check JSON encoding
    with SessionLocal() as s:
        log = s.get(PaymentLog, "payment_123")
        metadata_parsed = json.loads(log.metadata_json)
        assert metadata_parsed["description"] == "Тест с кириллицей"
        
        raw_payload_parsed = json.loads(log.raw_payload_json)
        assert raw_payload_parsed["data"]["key"] == "value"

