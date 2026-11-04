"""All configuration comes from environment variables (see .env.example)."""
from __future__ import annotations

import math
import os
import secrets
from dataclasses import dataclass, replace


def _csv(value: str) -> tuple[str, ...]:
    return tuple(x.strip().lower() for x in value.split(",") if x.strip())


def _flag(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    env: str = "development"
    secret_key: str = ""
    password_hash: str = ""
    api_token: str = ""
    enabled_services: tuple[str, ...] = ()  # empty = all discovered services
    trust_proxy: bool = True
    session_days: int = 14
    http_timeout: float = 10.0
    http_max_bytes: int = 5_000_000
    ai_timeout: float = 60.0
    ai_max_response_bytes: int = 5_000_000
    cache_ttl: int = 300
    cache_max_items: int = 500
    user_agent: str = "MirrorBot/0.1"
    login_max_failures: int = 5
    login_window_seconds: int = 600

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        e = os.environ.get
        env = e("APP_ENV", "development").strip().lower()
        secret = e("SECRET_KEY", "")
        if not secret and env != "production":
            secret = secrets.token_hex(32)  # dev only: sessions reset on restart
        cfg = cls(
            env=env,
            secret_key=secret,
            password_hash=e("ACCESS_PASSWORD_HASH", ""),
            api_token=e("API_TOKEN", ""),
            enabled_services=_csv(e("ENABLED_SERVICES", "")),
            trust_proxy=_flag(e("TRUST_PROXY"), True),
            session_days=int(e("SESSION_DAYS", "14")),
            http_timeout=float(e("HTTP_TIMEOUT", "10")),
            http_max_bytes=int(e("HTTP_MAX_BYTES", "5000000")),
            ai_timeout=float(e("AI_TIMEOUT", "60")),
            ai_max_response_bytes=int(e("AI_MAX_RESPONSE_BYTES", "5000000")),
            cache_ttl=int(e("CACHE_TTL", "300")),
        )
        return replace(cfg, **overrides)

    def secret(self, name: str) -> str:
        """Read a provider key (GROQ_API_KEY, ...) from the environment."""
        return os.environ.get(name, "")

    def validate(self) -> None:
        problems = []
        if not self.password_hash and not self.api_token:
            problems.append("set ACCESS_PASSWORD_HASH and/or API_TOKEN (otherwise the app would be open to everyone)")
        if self.is_production and len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be at least 32 characters in production")
        if not math.isfinite(self.ai_timeout) or self.ai_timeout <= 0 or self.ai_max_response_bytes <= 0:
            problems.append("AI_TIMEOUT and AI_MAX_RESPONSE_BYTES must be positive")
        if problems:
            raise RuntimeError("Invalid configuration: " + "; ".join(problems))
