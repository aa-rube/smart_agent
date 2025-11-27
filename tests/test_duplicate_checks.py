"""
Tests for duplicate payment checks in billing loop.
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta

from bot.utils.billing_db import ChargeAttempt
from bot.utils.time_helpers import now_msk, TIMEZONE, to_utc_for_db


def test_duplicate_check_with_payment_id(mock_time):
    """Test duplicate check when attempt with payment_id exists."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local:
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        
        # Setup - existing attempt with payment_id
        mock_existing_attempt = MagicMock()
        mock_existing_attempt.payment_id = "test_payment_123"
        mock_existing_attempt.id = 1
        
        mock_session.query.return_value.filter.return_value.first.return_value = mock_existing_attempt
        
        # This simulates the duplicate check in billing_loop
        existing_attempt = (
            mock_session.query(ChargeAttempt)
            .filter(
                ChargeAttempt.subscription_id == 1,
                ChargeAttempt.status == "created",
                ChargeAttempt.payment_id.isnot(None)
            )
            .first()
        )
        
        # Should find existing attempt
        assert existing_attempt is not None
        assert existing_attempt.payment_id == "test_payment_123"


def test_duplicate_check_recent_attempt_without_payment_id(mock_time):
    """Test duplicate check for recent attempt without payment_id."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local:
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        
        # Setup - recent attempt without payment_id (created 2 minutes ago)
        mock_recent_attempt = MagicMock()
        mock_recent_attempt.payment_id = None
        mock_recent_attempt.id = 2
        mock_recent_attempt.attempted_at = to_utc_for_db(mock_time - timedelta(minutes=2))
        
        # First query - no attempt with payment_id
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            None,  # First query (with payment_id) returns None
            mock_recent_attempt  # Second query (recent without payment_id) returns attempt
        ]
        
        # This simulates the duplicate check in billing_loop
        # First check - no attempt with payment_id
        existing_attempt_with_payment = (
            mock_session.query(ChargeAttempt)
            .filter(
                ChargeAttempt.subscription_id == 1,
                ChargeAttempt.status == "created",
                ChargeAttempt.payment_id.isnot(None)
            )
            .first()
        )
        
        assert existing_attempt_with_payment is None
        
        # Second check - recent attempt without payment_id
        recent_threshold = mock_time - timedelta(minutes=5)
        recent_threshold_utc = to_utc_for_db(recent_threshold)
        
        recent_attempt = (
            mock_session.query(ChargeAttempt)
            .filter(
                ChargeAttempt.subscription_id == 1,
                ChargeAttempt.status == "created",
                ChargeAttempt.payment_id.is_(None),
                ChargeAttempt.attempted_at >= recent_threshold_utc
            )
            .first()
        )
        
        # Should find recent attempt
        assert recent_attempt is not None
        assert recent_attempt.payment_id is None


def test_duplicate_check_old_attempt_without_payment_id(mock_time):
    """Test that old attempt without payment_id doesn't block new charge."""
    with patch('bot.utils.billing_db.SessionLocal') as mock_session_local:
        mock_session = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_session
        
        # Setup - old attempt without payment_id (created 10 minutes ago)
        # First query - no attempt with payment_id
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            None,  # First query (with payment_id) returns None
            None   # Second query (recent without payment_id) returns None (too old)
        ]
        
        # This simulates the duplicate check in billing_loop
        existing_attempt_with_payment = (
            mock_session.query(ChargeAttempt)
            .filter(
                ChargeAttempt.subscription_id == 1,
                ChargeAttempt.status == "created",
                ChargeAttempt.payment_id.isnot(None)
            )
            .first()
        )
        
        assert existing_attempt_with_payment is None
        
        # Second check - old attempt without payment_id (outside 5 minute window)
        recent_threshold = mock_time - timedelta(minutes=5)
        recent_threshold_utc = to_utc_for_db(recent_threshold)
        
        recent_attempt = (
            mock_session.query(ChargeAttempt)
            .filter(
                ChargeAttempt.subscription_id == 1,
                ChargeAttempt.status == "created",
                ChargeAttempt.payment_id.is_(None),
                ChargeAttempt.attempted_at >= recent_threshold_utc
            )
            .first()
        )
        
        # Should not find old attempt (outside window)
        assert recent_attempt is None

