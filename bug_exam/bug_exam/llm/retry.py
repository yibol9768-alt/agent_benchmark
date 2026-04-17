"""Exponential-backoff retry for LLM API calls.

Catches rate-limit (429) and transient server errors (500/502/503) from both
the OpenAI and Anthropic SDKs and retries with jitter.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception looks like a transient / rate-limit error."""
    # OpenAI SDK (used by GLM client)
    try:
        from openai import RateLimitError as OaiRL, APIStatusError as OaiStatus
        if isinstance(exc, OaiRL):
            return True
        if isinstance(exc, OaiStatus) and getattr(exc, "status_code", 0) in (429, 500, 502, 503, 529):
            return True
    except ImportError:
        pass

    # Anthropic SDK
    try:
        from anthropic import RateLimitError as AntRL, InternalServerError as AntISE
        if isinstance(exc, (AntRL, AntISE)):
            return True
    except ImportError:
        pass

    # Fallback heuristic: check error message for common patterns
    msg = str(exc).lower()
    if "429" in msg or "rate" in msg or "too many" in msg:
        return True
    # GLM Chinese error messages (网络错误 = network error)
    if "网络错误" in str(exc) or "code\":\"1234\"" in str(exc):
        return True

    return False


def retrying_call(
    fn: Callable[[], T],
    *,
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 120.0,
) -> T:
    """Call fn() with exponential backoff on retryable errors.

    Returns the result of fn() on success.
    Raises the last exception after max_retries exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:
            if not _is_retryable(e) or attempt == max_retries:
                raise
            last_exc = e
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, 0.5 * delay)
            total = delay + jitter
            log.warning(
                "retryable error (attempt %d/%d, wait %.1fs): %s",
                attempt + 1, max_retries, total, str(e)[:200],
            )
            time.sleep(total)
    raise last_exc  # unreachable, but keeps mypy happy
