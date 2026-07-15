"""In-memory cache with lazy TTL expiration.

Ported from medical-terminologies-mcp src/utils/cache.ts
"""

import time
from typing import Any, Callable, Dict, Optional


class InMemoryCache:
    """Map-based cache with lazy TTL expiration.

    Entries are not actively removed; they expire on next access.
    """

    def __init__(self):
        self._store: Dict[str, Any] = {}
        self._expires: Dict[str, float] = {}

    def get(self, prefix: str, key: str) -> Optional[Any]:
        full_key = f"{prefix}:{key}"
        now = time.time()
        if full_key in self._expires and self._expires[full_key] < now:
            self.delete(prefix, key)
            return None
        return self._store.get(full_key)

    def set(self, prefix: str, key: str, value: Any, ttl: int) -> None:
        full_key = f"{prefix}:{key}"
        self._store[full_key] = value
        self._expires[full_key] = time.time() + ttl

    def delete(self, prefix: str, key: str) -> None:
        full_key = f"{prefix}:{key}"
        self._store.pop(full_key, None)
        self._expires.pop(full_key, None)

    def get_or_set(
        self, prefix: str, key: str, factory: Callable[[], Any], ttl: int = 3600
    ) -> Any:
        cached = self.get(prefix, key)
        if cached is not None:
            return cached
        value = factory()
        self.set(prefix, key, value, ttl)
        return value

    def clear_prefix(self, prefix: str) -> None:
        prefix_key = f"{prefix}:"
        for key in list(self._store.keys()):
            if key.startswith(prefix_key):
                self._store.pop(key, None)
                self._expires.pop(key, None)


# Default TTLs (seconds) similar to medical-terminologies-mcp
DEFAULT_TTL = {
    "STATIC": 86400,  # 24h - terminology chapters
    "LOOKUP": 3600,   # 1h - code lookups
    "SEARCH": 600,    # 10min - search results
    "TOKEN": 3000,    # 50min - OAuth tokens
}


# Global process cache
_global_cache = InMemoryCache()


def get_cache() -> InMemoryCache:
    return _global_cache
