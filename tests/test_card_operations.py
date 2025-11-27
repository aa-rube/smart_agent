"""
Tests for card_upsert_from_provider with real database.
"""
import pytest
from bot.utils.billing_db import PaymentMethod


def test_card_upsert_creates_new(in_memory_db):
    """Test creating new card with real database."""
    repo, SessionLocal = in_memory_db
    
    card_id = repo.card_upsert_from_provider(
        user_id=123,
        provider="yookassa",
        pm_token="pm_token_123",
        brand="Visa",
        first6="411111",
        last4="1111",
        exp_month=12,
        exp_year=2025
    )
    
    # Check real record
    with SessionLocal() as s:
        card = s.get(PaymentMethod, card_id)
        assert card is not None
        assert card.user_id == 123
        assert card.provider == "yookassa"
        assert card.provider_pm_token == "pm_token_123"
        assert card.brand == "Visa"
        assert card.first6 == "411111"
        assert card.last4 == "1111"
        assert card.exp_month == 12
        assert card.exp_year == 2025
        assert card.deleted_at is None  # Not deleted


def test_card_upsert_updates_existing(in_memory_db):
    """Test updating existing card."""
    repo, SessionLocal = in_memory_db
    
    # Create card
    card_id = repo.card_upsert_from_provider(
        user_id=123,
        provider="yookassa",
        pm_token="pm_token_123",
        brand="Visa",
        first6="411111",
        last4="1111",
        exp_month=12,
        exp_year=2025
    )
    
    # Update with new data
    updated_card_id = repo.card_upsert_from_provider(
        user_id=123,
        provider="yookassa",
        pm_token="pm_token_123",  # Same token
        brand="MasterCard",  # Changed
        first6="555555",  # Changed
        last4="5555",  # Changed
        exp_month=6,  # Changed
        exp_year=2026  # Changed
    )
    
    # Should be same card
    assert updated_card_id == card_id
    
    # Check updates
    with SessionLocal() as s:
        card = s.get(PaymentMethod, card_id)
        assert card.brand == "MasterCard"
        assert card.first6 == "555555"
        assert card.last4 == "5555"
        assert card.exp_month == 6
        assert card.exp_year == 2026
        assert card.deleted_at is None  # Reactivated if was deleted


def test_card_upsert_reactivates_deleted(in_memory_db):
    """Test reactivating deleted card."""
    repo, SessionLocal = in_memory_db
    
    # Create card
    card_id = repo.card_upsert_from_provider(
        user_id=123,
        provider="yookassa",
        pm_token="pm_token_123",
        brand="Visa",
        first6="411111",
        last4="1111",
        exp_month=12,
        exp_year=2025
    )
    
    # Delete it manually
    from bot.utils.time_helpers import now_msk, to_utc_for_db
    with SessionLocal() as s, s.begin():
        card = s.get(PaymentMethod, card_id)
        card.deleted_at = to_utc_for_db(now_msk())
        s.flush()
    
    # Check it's deleted
    with SessionLocal() as s:
        card = s.get(PaymentMethod, card_id)
        assert card.deleted_at is not None
    
    # Reactivate by upsert
    reactivated_id = repo.card_upsert_from_provider(
        user_id=123,
        provider="yookassa",
        pm_token="pm_token_123",  # Same token
        brand="Visa",
        first6="411111",
        last4="1111",
        exp_month=12,
        exp_year=2025
    )
    
    # Should be same card
    assert reactivated_id == card_id
    
    # Check it's reactivated
    with SessionLocal() as s:
        card = s.get(PaymentMethod, card_id)
        assert card.deleted_at is None  # Reactivated


def test_card_upsert_updates_user_id(in_memory_db):
    """Test that user_id can be updated when card is reused."""
    repo, SessionLocal = in_memory_db
    
    # Create card for user 123
    card_id = repo.card_upsert_from_provider(
        user_id=123,
        provider="yookassa",
        pm_token="pm_token_123",
        brand="Visa",
        first6="411111",
        last4="1111",
        exp_month=12,
        exp_year=2025
    )
    
    # Update with different user_id (card reused)
    updated_id = repo.card_upsert_from_provider(
        user_id=456,  # Different user
        provider="yookassa",
        pm_token="pm_token_123",  # Same token
        brand="Visa",
        first6="411111",
        last4="1111",
        exp_month=12,
        exp_year=2025
    )
    
    # Should be same card
    assert updated_id == card_id
    
    # Check user_id updated
    with SessionLocal() as s:
        card = s.get(PaymentMethod, card_id)
        assert card.user_id == 456


def test_card_upsert_handles_none_values(in_memory_db):
    """Test that None values are handled correctly."""
    repo, SessionLocal = in_memory_db
    
    # Create card with some None values
    card_id = repo.card_upsert_from_provider(
        user_id=123,
        provider="yookassa",
        pm_token="pm_token_123",
        brand=None,
        first6=None,
        last4=None,
        exp_month=None,
        exp_year=None
    )
    
    # Check record
    with SessionLocal() as s:
        card = s.get(PaymentMethod, card_id)
        assert card is not None
        assert card.user_id == 123
        assert card.provider == "yookassa"
        assert card.provider_pm_token == "pm_token_123"
        # None values are allowed
        assert card.brand is None or card.brand == ""
        assert card.first6 is None or card.first6 == ""
        assert card.last4 is None or card.last4 == ""
        assert card.exp_month is None
        assert card.exp_year is None

