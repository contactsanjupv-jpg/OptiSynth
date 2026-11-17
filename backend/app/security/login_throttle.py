"""Slows online password guessing against /api/auth/login.

Before this, an attacker could try unlimited passwords (measured: 60 wrong guesses in a row were all processed and the
right password still worked afterwards), and each guess cost the server about 0.2 s of CPU.

Three sliding windows over WINDOW_SECONDS, so that one person's mistakes do not lock others out:
  * the same address trying the same email   -> LIMIT_ADDRESS_EMAIL failures      (the everyday typo case)
  * one address trying anything              -> LIMIT_ADDRESS failures            (stops one machine guessing many accounts)
  * one email from anywhere                   -> LIMIT_EMAIL failures              (a high backstop against guessing spread
                                                                                    over many addresses)
State is held in this process's memory. That suits a single server process (the pilot deployment); with several
worker processes each keeps its own counts, so the effective limits are multiplied by the worker count. Behind a
reverse proxy run uvicorn with --proxy-headers and --forwarded-allow-ips so the address is the client's, not the proxy's.
"""
import threading
import time
from collections import deque

WINDOW_SECONDS = 15 * 60
LIMIT_ADDRESS_EMAIL = 5
LIMIT_ADDRESS = 30
LIMIT_EMAIL = 100
MAX_TRACKED_KEYS = 20000


class LoginThrottle:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._events = {}  # key -> deque of failure times

    @staticmethod
    def _keys(address: str, email: str):
        email = (email or "").lower().strip()
        return (
            (("address+email", address, email), LIMIT_ADDRESS_EMAIL),
            (("address", address), LIMIT_ADDRESS),
            (("email", email), LIMIT_EMAIL),
        )

    def _recent(self, key, now):
        q = self._events.get(key)
        if q is None:
            return None
        while q and now - q[0] >= WINDOW_SECONDS:
            q.popleft()
        if not q:
            del self._events[key]
            return None
        return q

    def retry_after(self, address: str, email: str) -> int:
        """Seconds the caller must wait before another attempt is allowed; 0 when it may try now."""
        now = self._clock()
        wait = 0.0
        with self._lock:
            for key, limit in self._keys(address, email):
                q = self._recent(key, now)
                if q is not None and len(q) >= limit:
                    wait = max(wait, q[0] + WINDOW_SECONDS - now)
        return int(wait) + 1 if wait > 0 else 0

    def record_failure(self, address: str, email: str) -> None:
        now = self._clock()
        with self._lock:
            if len(self._events) > MAX_TRACKED_KEYS:
                self._purge(now)
            for key, _ in self._keys(address, email):
                self._events.setdefault(key, deque()).append(now)

    def record_success(self, address: str, email: str) -> None:
        """A correct login clears that address+email pair (the wider counters keep their history)."""
        with self._lock:
            self._events.pop(("address+email", address, (email or "").lower().strip()), None)

    def _purge(self, now) -> None:
        for key in list(self._events):
            self._recent(key, now)
        if len(self._events) > MAX_TRACKED_KEYS:  # still too many live keys: drop the oldest half
            for key in sorted(self._events, key=lambda k: self._events[k][-1])[: len(self._events) // 2]:
                del self._events[key]

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


throttle = LoginThrottle()
