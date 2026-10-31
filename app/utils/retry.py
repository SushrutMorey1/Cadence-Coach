"""
retry.py — Retry with Exponential Backoff
===========================================
Reusable decorator for retrying transient failures on the blocking
Azure Speech and Groq Whisper calls (they run in worker threads).
"""

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
    Exponential backoff: delay = base_delay * (backoff_factor ** attempt), plus
    optional jitter. Only `retryable_exceptions` trigger a retry.
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
