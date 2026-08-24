"""
rate_limiter.py — Per-User Token Rate Limiter
================================================
Sliding-window token budget enforcement.
Each user (identified by email or IP) gets a fixed token
allowance per rolling hour. Async-compatible for FastAPI.
"""

import time
import logging
from typing import Tuple

from app.config import RATE_LIMIT_TOKENS_PER_HOUR, RATE_LIMIT_WINDOW_SECONDS
from app.services.database import async_execute, async_fetchone

logger = logging.getLogger(__name__)


class TokenRateLimiter:
    """Sliding-window rate limiter that tracks token consumption per user in MySQL."""

    def __init__(
        self,
        max_tokens: int = RATE_LIMIT_TOKENS_PER_HOUR,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
    ):
        self.max_tokens = max_tokens
        self.window_seconds = window_seconds

    async def _evict(self) -> None:
        """Remove entries older than the sliding window to keep the table small."""
        query = "DELETE FROM token_usage WHERE consumed_at < DATE_SUB(NOW(), INTERVAL %s SECOND)"
        await async_execute(query, (self.window_seconds,))

    async def _current_usage(self, user_key: str) -> int:
        """Sum of tokens consumed within the current window."""
        query = """
            SELECT COALESCE(SUM(tokens), 0) FROM token_usage 
            WHERE user_key = %s AND consumed_at > DATE_SUB(NOW(), INTERVAL %s SECOND)
        """
        result = await async_fetchone(query, (user_key, self.window_seconds))
        return int(result[0]) if result else 0

    async def check(self, user_key: str) -> Tuple[bool, int, float]:
        """
        Check whether the user has token budget remaining.

        Returns:
            (allowed, remaining_tokens, resets_in_seconds)
        """
        await self._evict()
        used = await self._current_usage(user_key)
        remaining = max(0, self.max_tokens - used)
        allowed = remaining > 0

        # Calculate when the oldest entry expires (soonest budget freed)
        query = """
            SELECT UNIX_TIMESTAMP(consumed_at) FROM token_usage 
            WHERE user_key = %s AND consumed_at > DATE_SUB(NOW(), INTERVAL %s SECOND)
            ORDER BY consumed_at ASC LIMIT 1
        """
        result = await async_fetchone(query, (user_key, self.window_seconds))
            
        if result:
            oldest_ts = result[0]
            resets_in = max(0.0, (oldest_ts + self.window_seconds) - time.time())
        else:
            resets_in = 0.0

        return allowed, remaining, resets_in

    async def consume(self, user_key: str, tokens: int) -> None:
        """Record token consumption for a user."""
        query = "INSERT INTO token_usage (user_key, tokens, consumed_at) VALUES (%s, %s, NOW())"
        await async_execute(query, (user_key, tokens))
            
        logger.debug(
            "Rate limiter: user=%s consumed=%d",
            user_key,
            tokens,
        )

    async def get_usage(self, user_key: str) -> dict:
        """Return current usage stats for a user (for the status endpoint)."""
        used = await self._current_usage(user_key)
        remaining = max(0, self.max_tokens - used)

        query = """
            SELECT UNIX_TIMESTAMP(consumed_at) FROM token_usage 
            WHERE user_key = %s AND consumed_at > DATE_SUB(NOW(), INTERVAL %s SECOND)
            ORDER BY consumed_at ASC LIMIT 1
        """
        result = await async_fetchone(query, (user_key, self.window_seconds))
            
        if result:
            oldest_ts = result[0]
            resets_in = max(0.0, (oldest_ts + self.window_seconds) - time.time())
        else:
            resets_in = 0.0

        return {
            "tokens_used": used,
            "tokens_remaining": remaining,
            "tokens_limit": self.max_tokens,
            "window_seconds": self.window_seconds,
            "resets_in_seconds": round(resets_in, 1),
        }


# ── Singleton instance ──
rate_limiter = TokenRateLimiter()


def get_user_key(request) -> str:
    """
    Extract a rate-limiting key from the request.
    Uses authenticated email if available, otherwise falls back to client IP.
    """
    # Check scope directly — hasattr won't catch AssertionError from Starlette's session property
    user = request.session.get("user") if "session" in getattr(request, "scope", {}) else None
    if user and user.get("email"):
        return f"user:{user['email']}"
    # Fallback to IP
    client_host = getattr(request.client, "host", None) if request.client else None
    return f"ip:{client_host or 'unknown'}"
