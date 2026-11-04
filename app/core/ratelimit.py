"""Per-process in-memory limiters for login failures and API requests."""
from __future__ import annotations

import threading
import time


class FailureLimiter:
    def __init__(self, max_failures: int, window: int):
        self.max_failures = max_failures
        self.window = window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._fails.get(key, []) if now - t < self.window]
        if recent:
            self._fails[key] = recent
        else:
            self._fails.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._prune(key, time.monotonic())) >= self.max_failures

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._prune(key, now)
            self._fails.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


class RequestLimiter:
    """In-memory per-key sliding-window limit for API requests."""

    def __init__(self, max_requests: int, window: int, max_keys: int = 10000):
        self.max_requests = max_requests
        self.window = window
        self.max_keys = max_keys
        self._requests: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            now = time.monotonic()
            recent = [t for t in self._requests.get(key, []) if now - t < self.window]
            if len(recent) >= self.max_requests:
                self._requests[key] = recent
                return False
            recent.append(now)
            self._requests[key] = recent
            if len(self._requests) > self.max_keys:
                oldest = min(self._requests, key=lambda item: self._requests[item][-1])
                del self._requests[oldest]
            return True
