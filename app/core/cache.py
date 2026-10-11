"""Tiny thread-safe TTL cache (per process). Swap for a file/Redis cache later
by keeping the same get/set/get_or_set interface."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable


class MemoryCache:
    def __init__(self, max_items: int = 500, default_ttl: int = 300):
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.max_items = max_items
        self.default_ttl = default_ttl

    def get(self, key: str, default=None):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            expires, value = item
            if expires < time.monotonic():
                del self._data[key]
                return default
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value, ttl: int | None = None) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + (ttl or self.default_ttl), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def get_or_set(self, key: str, factory: Callable[[], Any], ttl: int | None = None):
        sentinel = object()
        value = self.get(key, sentinel)
        if value is sentinel:
            value = factory()
            self.set(key, value, ttl)
        return value
