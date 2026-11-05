"""Rate limit in memoria per chiave API, a token bucket (docs/contracts/api-v1.md §4).

Lo stato è per processo: valido solo con un worker uvicorn.
"""

import time
from collections.abc import Callable
from uuid import UUID


class RateLimiter:
    """Token bucket per `key_id`: capacità `per_minute`, ricarica `per_minute / 60` al secondo."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        if per_minute < 1:
            raise ValueError("per_minute deve essere >= 1")
        self._capacity = float(per_minute)
        self._rate = per_minute / 60.0
        self._clock = clock
        # key_id -> (gettoni disponibili, istante dell'ultimo aggiornamento)
        self._buckets: dict[UUID, tuple[float, float]] = {}

    def check(self, key: UUID) -> float | None:
        """Consuma un gettone: None se consentito, altrimenti i secondi da attendere."""
        now = self._clock()
        tokens, last = self._buckets.get(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + max(0.0, now - last) * self._rate)
        if tokens >= 1.0:
            self._buckets[key] = (tokens - 1.0, now)
            return None
        self._buckets[key] = (tokens, now)
        return (1.0 - tokens) / self._rate
