"""Synchronous token bucket rate limiter.

Ported from medical-terminologies-mcp src/utils/rate-limiter.ts
"""

import time
from typing import Dict, Optional


class TokenBucketRateLimiter:
    """Token bucket rate limiter for the synchronous medical API clients."""

    def __init__(self, rate: float, capacity: Optional[int] = None):
        """Initialize with tokens per second.

        Args:
            rate: tokens per second
            capacity: maximum bucket size (defaults to rate)
        """
        self.rate = float(rate)
        self.capacity = float(capacity if capacity is not None else rate)
        self.tokens = float(self.capacity)
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_refill = now

    def acquire_sync(self) -> None:
        """Synchronous token acquisition (best effort, no waiting)."""
        import time as _time

        self._refill()
        if self.tokens >= 1:
            self.tokens -= 1
            return
        # If no tokens, sleep just enough for one token
        wait = max(0.0, (1 - self.tokens) / self.rate)
        _time.sleep(wait)
        self._refill()
        self.tokens = max(0, self.tokens - 1)

class RateLimiterRegistry:
    """Registry of named rate limiters for common medical APIs."""

    def __init__(self):
        self._limiters: Dict[str, TokenBucketRateLimiter] = {}

    def get(self, name: str, rate: Optional[float] = None) -> TokenBucketRateLimiter:
        if name not in self._limiters:
            default_rates = {
                "who": 5.0,
                "nlm": 10.0,
                "rxnorm": 20.0,
                "snomed": 10.0,
                "healthcare": 100.0,  # Google Healthcare API
            }
            r = rate if rate is not None else default_rates.get(name, 10.0)
            self._limiters[name] = TokenBucketRateLimiter(r)
        return self._limiters[name]


_global_rate_limiters = RateLimiterRegistry()


def get_rate_limiter(name: str, rate: Optional[float] = None) -> TokenBucketRateLimiter:
    return _global_rate_limiters.get(name, rate)
