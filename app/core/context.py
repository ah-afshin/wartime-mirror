from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Config
from app.core.cache import MemoryCache
from app.core.http_client import HttpClient
from app.core.ratelimit import FailureLimiter


@dataclass
class AppContext:
    """Shared objects handed to every service. Stored at app.extensions['mirror']."""
    config: Config
    http: HttpClient
    cache: MemoryCache
    login_limiter: FailureLimiter
    services: dict = field(default_factory=dict)
