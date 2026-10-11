from __future__ import annotations

import logging
from datetime import timedelta

from flask import Flask, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from app import auth, views
from app.config import Config
from app.core.cache import MemoryCache
from app.core.context import AppContext
from app.core.errors import register_error_handlers
from app.core.http_client import HttpClient
from app.core.ratelimit import FailureLimiter
from app.registry import init_services


def create_app(config: Config | None = None) -> Flask:
    cfg = config or Config.from_env()
    cfg.validate()
    logging.basicConfig(level=logging.INFO)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.is_production,
        PERMANENT_SESSION_LIFETIME=timedelta(days=cfg.session_days),
        MAX_CONTENT_LENGTH=1_000_000,
    )
    app.json.ensure_ascii = False  # Persian text stays readable in JSON
    if cfg.trust_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    ctx = AppContext(
        config=cfg,
        http=HttpClient(cfg.http_timeout, cfg.http_max_bytes, cfg.user_agent),
        cache=MemoryCache(cfg.cache_max_items, cfg.cache_ttl),
        login_limiter=FailureLimiter(cfg.login_max_failures, cfg.login_window_seconds),
    )
    app.extensions["mirror"] = ctx

    register_error_handlers(app)
    app.before_request(auth.enforce_access)
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    init_services(app, ctx)

    @app.context_processor
    def _inject():
        return {"csrf_token": auth.csrf_token, "logged_in": session.get("auth") is True}

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.endpoint != "static":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    return app
