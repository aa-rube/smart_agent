"""
Tests for notification idempotency.
"""
import pytest
from unittest.mock import AsyncMock, patch

from bot.handlers.payment_handler import _notify_after_payment
from bot.utils.redis_repo import set_nx_with_ttl


@pytest.mark.asyncio
async def test_notify_after_payment_idempotency(mock_bot):
    """Test that notification is sent only once for the same payment_id."""
    with patch('bot.utils.redis_repo.set_nx_with_ttl', new_callable=AsyncMock) as mock_set_nx:
        # First call - should send notification
        mock_set_nx.return_value = True
        await _notify_after_payment(
            mock_bot, 
            user_id=7833048230, 
            code="1m", 
            until_date_iso="2025-02-15",
            payment_id="test_payment_123"
        )
        
        assert mock_bot.send_message.called
        
        # Reset mock
        mock_bot.send_message.reset_mock()
        
        # Second call with same payment_id - should NOT send notification
        mock_set_nx.return_value = False
        await _notify_after_payment(
            mock_bot, 
            user_id=7833048230, 
            code="1m", 
            until_date_iso="2025-02-15",
            payment_id="test_payment_123"
        )
        
        # Should not send message again
        assert not mock_bot.send_message.called


@pytest.mark.asyncio
async def test_notify_after_payment_without_payment_id(mock_bot):
    """Test notification without payment_id (should still send)."""
    await _notify_after_payment(
        mock_bot, 
        user_id=7833048230, 
        code="1m", 
        until_date_iso="2025-02-15",
        payment_id=None
    )
    
    # Should send notification even without payment_id
    assert mock_bot.send_message.called


@pytest.mark.asyncio
async def test_notify_after_payment_redis_error(mock_bot):
    """Test notification when Redis check fails (should still send)."""
    with patch('bot.utils.redis_repo.set_nx_with_ttl', new_callable=AsyncMock) as mock_set_nx:
        mock_set_nx.side_effect = Exception("Redis error")
        
        await _notify_after_payment(
            mock_bot, 
            user_id=7833048230, 
            code="1m", 
            until_date_iso="2025-02-15",
            payment_id="test_payment_123"
        )
        
        # Should send notification even if Redis fails
        assert mock_bot.send_message.called

