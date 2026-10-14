"""In-process rate limiting.

**What this is, and what it is not.** A fixed-window counter held in this
process's memory. With N API processes the effective limit is N times the
configured one, and a restart forgets every counter. That is stated here rather
than discovered later, because a rate limiter whose real behaviour is
undocumented is worse than none: somebody will size a threat model around it.

It is deliberately not Redis. Architecture.md §5.2 and the Phase 5 non-goals
both say no second datastore, and the property actually needed is bounding the
damage from a runaway client or a credential-stuffing loop — which an
approximate, per-process limit does. A precise global limit would need shared
state, and the cost of that (another service to run, secure and keep available,
and a new failure mode in the request path) is not worth buying precision the
threat does not require.

Two consequences follow from it being approximate, and both are handled:

* The limiter **fails open**. An error inside it must never turn a working
  request into an error; the worst outcome of a bug here should be that a
  request is allowed.
* It is applied per *route class* with different budgets, so a burst of
  dashboard reads cannot exhaust the budget that protects sign-in.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from app.core.logging import get_logger

logger = get_logger(__name__)

_WINDOW_SECONDS = 60.0

# Beyond this many distinct keys the table is swept. Bounded so that a flood of
# unique keys — which is what an attacker sending one request per forged
# address produces — cannot itself become the memory exhaustion it was meant to
# prevent.
_MAX_TRACKED_KEYS = 50_000


@dataclass
class _Window:
    started_at: float
    count: int = 0


@dataclass
class RateLimiter:
    """Fixed-window counters, keyed by an arbitrary string."""

    limit_per_minute: int
    _windows: dict[str, _Window] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, key: str) -> tuple[bool, int]:
        """Record a hit. Returns ``(allowed, retry_after_seconds)``.

        ``retry_after`` is the time until the current window closes, so a
        well-behaved client waits exactly as long as it needs to rather than
        guessing.
        """
        try:
            now = time.monotonic()
            with self._lock:
                if len(self._windows) > _MAX_TRACKED_KEYS:
                    self._sweep(now)
                window = self._windows.get(key)
                if window is None or now - window.started_at >= _WINDOW_SECONDS:
                    self._windows[key] = _Window(started_at=now, count=1)
                    return True, 0
                window.count += 1
                if window.count > self.limit_per_minute:
                    remaining = _WINDOW_SECONDS - (now - window.started_at)
                    return False, max(1, int(remaining) + 1)
                return True, 0
        # Deliberately broad, and it FAILS OPEN. A bug in the limiter must
        # never turn a working request into an error; the worst outcome of a
        # fault here should be that a request is allowed. See the module
        # docstring.
        except Exception:
            logger.exception("ratelimit.internal_error")
            return True, 0

    def _sweep(self, now: float) -> None:
        stale = [
            key
            for key, window in self._windows.items()
            if now - window.started_at >= _WINDOW_SECONDS
        ]
        for key in stale:
            self._windows.pop(key, None)
        if len(self._windows) > _MAX_TRACKED_KEYS:
            # Still over after sweeping: drop everything rather than grow
            # without bound. Forgetting counters allows requests, which is the
            # fail-open direction.
            logger.warning("ratelimit.table_reset", tracked=len(self._windows))
            self._windows.clear()

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()


__all__ = ["RateLimiter"]
