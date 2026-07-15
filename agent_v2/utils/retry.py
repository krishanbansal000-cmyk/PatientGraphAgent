"""Retry logic with exponential backoff and jitter.

Ported from medical-terminologies-mcp src/utils/retry.ts
"""

import random
import time
from typing import Callable, List, Optional, TypeVar


T = TypeVar("T")


RETRYABLE_STATUS_CODES = [408, 429, 500, 502, 503, 504]
RETRYABLE_NETWORK_ERRORS = (
    "ConnectionResetError",
    "ConnectionRefusedError",
    "TimeoutError",
    "ConnectionError",
)


class RetryOptions:
    def __init__(
        self,
        max_retries: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 10.0,
        backoff_multiplier: float = 2.0,
        retryable_status_codes: Optional[List[int]] = None,
        jitter: bool = True,
    ):
        self.max_retries = max_retries
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
        self.retryable_status_codes = retryable_status_codes or list(RETRYABLE_STATUS_CODES)
        self.jitter = jitter


def _is_retryable_error(error: Exception) -> bool:
    name = type(error).__name__
    if name in RETRYABLE_NETWORK_ERRORS:
        return True
    if hasattr(error, "status_code") and getattr(error, "status_code") in RETRYABLE_STATUS_CODES:
        return True
    if hasattr(error, "response") and hasattr(error.response, "status_code"):
        code = getattr(error.response, "status_code", None)
        if code in RETRYABLE_STATUS_CODES:
            return True
    return False


def _calculate_delay(attempt: int, options: RetryOptions) -> float:
    delay = options.initial_delay * (options.backoff_multiplier ** attempt)
    delay = min(delay, options.max_delay)
    if options.jitter:
        # ±25% jitter
        delay = delay * (0.75 + random.random() * 0.5)
    return delay


def retry(
    fn: Callable[[], T],
    options: Optional[RetryOptions] = None,
    on_retry: Optional[Callable[[Exception, int, float], None]] = None,
) -> T:
    """Synchronous retry wrapper."""
    options = options or RetryOptions()
    last_error: Optional[Exception] = None
    for attempt in range(options.max_retries + 1):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt >= options.max_retries or not _is_retryable_error(e):
                raise
            delay = _calculate_delay(attempt, options)
            if on_retry:
                on_retry(e, attempt + 1, delay)
            time.sleep(delay)
    if last_error:
        raise last_error
    raise RuntimeError("Retry loop exited without result or error")
