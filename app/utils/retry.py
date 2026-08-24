"""
retry.py — Async Retry with Exponential Backoff
=================================================
Reusable decorator for retrying transient failures
on external API calls (Groq, Azure, etc.).
"""

import asyncio
import random
import logging
import functools
from typing import Tuple, Type

logger = logging.getLogger(__name__)

# Default transient exceptions to retry on
_DEFAULT_RETRYABLE: Tuple[Type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

# Try to include httpx exceptions if available (used by LangChain/Groq)
try:
    import httpx
    _DEFAULT_RETRYABLE = _DEFAULT_RETRYABLE + (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.RemoteProtocolError,
    )
except ImportError:
    pass


def async_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[BaseException], ...] = _DEFAULT_RETRYABLE,
):
    """
    Decorator that retries an async function on transient failures.

    Uses exponential backoff: delay = base_delay * (backoff_factor ** attempt)
    With optional random jitter to prevent thundering herd.

    Args:
        max_retries:          Maximum number of retry attempts (0 = no retries)
        base_delay:           Initial delay in seconds before first retry
        max_delay:            Maximum delay cap in seconds
        backoff_factor:       Multiplier applied to delay on each retry
        jitter:               Add random jitter (0–50% of delay) to prevent stampede
        retryable_exceptions: Tuple of exception types that trigger a retry.
                              All other exceptions propagate immediately.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exception = exc

                    if attempt >= max_retries:
                        logger.error(
                            "Retry exhausted for %s after %d attempts. Last error: %s",
                            func.__name__, max_retries + 1, exc,
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    if jitter:
                        delay += random.uniform(0, delay * 0.5)

                    logger.warning(
                        "Retry %d/%d for %s in %.1fs — %s: %s",
                        attempt + 1, max_retries,
                        func.__name__, delay,
                        type(exc).__name__, exc,
                    )
                    await asyncio.sleep(delay)

            # Should not reach here, but just in case
            raise last_exception  # type: ignore

        return wrapper
    return decorator


def sync_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[BaseException], ...] = _DEFAULT_RETRYABLE,
):
    """
    Decorator that retries a synchronous function on transient failures.
    Same backoff logic as async_retry, but uses time.sleep.
    """
    import time

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exception = exc

                    if attempt >= max_retries:
                        logger.error(
                            "Retry exhausted for %s after %d attempts. Last error: %s",
                            func.__name__, max_retries + 1, exc,
                        )
                        raise

                    delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                    if jitter:
                        delay += random.uniform(0, delay * 0.5)

                    logger.warning(
                        "Retry %d/%d for %s in %.1fs — %s: %s",
                        attempt + 1, max_retries,
                        func.__name__, delay,
                        type(exc).__name__, exc,
                    )
                    time.sleep(delay)

            raise last_exception  # type: ignore

        return wrapper
    return decorator
