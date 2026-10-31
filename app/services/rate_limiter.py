"""
rate_limiter.py — Per-User Token Rate Limiter
================================================
Sliding-window token budget enforcement.
Each user (identified by email or IP) gets a fixed token
allowance per rolling hour. Async-compatible for FastAPI.

Timestamps are stored as epoch seconds so the same SQL runs on
MySQL and SQLite. The limiter fails OPEN: a database hiccup must
never block a live demo or a paying user.
"""

import time
import logging
from typing import Tuple

from app.config import RATE_LIMIT_TOKENS_PER_HOUR, RATE_LIMIT_WINDOW_SECONDS
from app.services.database import async_execute, async_fetchone

logger = logging.getLogger(__name__)


class TokenRateLimiter:
    """Sliding-window rate limiter that tracks token consumption per user."""

    def __init__(
        self,
        max_tokens: int = RATE_LIMIT_TOKENS_PER_HOUR,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
    ):
        self.max_tokens = max_tokens
        self.window_seconds = window_seconds

    def _cutoff(self) -> float:
        return time.time() - self.window_seconds

    async def _evict(self) -> None:
        """Remove entries older than the sliding window to keep the table small."""
        await async_execute("DELETE FROM token_ledger WHERE consumed_ts < %s", (self._cutoff(),))

    async def _current_usage(self, user_key: str) -> int:
        """Sum of tokens consumed within the current window."""
        result = await async_fetchone(
            "SELECT COALESCE(SUM(tokens), 0) FROM token_ledger WHERE user_key = %s AND consumed_ts > %s",
            (user_key, self._cutoff()),
        )
        return int(result[0]) if result and result[0] is not None else 0

    async def _resets_in(self, user_key: str) -> float:
        """Seconds until the oldest in-window entry expires (soonest budget freed)."""
        result = await async_fetchone(
            "SELECT MIN(consumed_ts) FROM token_ledger WHERE user_key = %s AND consumed_ts > %s",
            (user_key, self._cutoff()),
        )
        if result and result[0] is not None:
            return max(0.0, (float(result[0]) + self.window_seconds) - time.time())
        return 0.0

    async def check(self, user_key: str) -> Tuple[bool, int, float]:
        """
        Check whether the user has token budget remaining.

        Returns:
            (allowed, remaining_tokens, resets_in_seconds)
        """
        try:
            await self._evict()
            used = await self._current_usage(user_key)
            remaining = max(0, self.max_tokens - used)
            allowed = remaining > 0
            resets_in = await self._resets_in(user_key) if not allowed else 0.0
            return allowed, remaining, resets_in
        except Exception as err:
            logger.warning("Rate limiter check failed (failing open): %s", err)
            return True, self.max_tokens, 0.0

    async def consume(self, user_key: str, tokens: int) -> None:
        """Record token consumption for a user."""
        try:
            await async_execute(
                "INSERT INTO token_ledger (user_key, tokens, consumed_ts) VALUES (%s, %s, %s)",
                (user_key, int(tokens), time.time()),
            )
            logger.debug("Rate limiter: user=%s consumed=%d", user_key, tokens)
        except Exception as err:
            logger.warning("Rate limiter consume failed: %s", err)

    async def get_usage(self, user_key: str) -> dict:
        """Return current usage stats for a user (for the status endpoint)."""
        try:
            used = await self._current_usage(user_key)
            resets_in = await self._resets_in(user_key)
        except Exception as err:
            logger.warning("Rate limiter usage lookup failed: %s", err)
            used, resets_in = 0, 0.0

        return {
            "tokens_used": used,
            "tokens_remaining": max(0, self.max_tokens - used),
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
    Works for both HTTP requests and WebSocket connections.
    """
    # Check scope directly — hasattr won't catch AssertionError from Starlette's session property
    user = request.session.get("user") if "session" in getattr(request, "scope", {}) else None
    if user and user.get("email"):
        return f"user:{user['email']}"
    client_host = getattr(request.client, "host", None) if request.client else None
    return f"ip:{client_host or 'unknown'}"
