from __future__ import annotations

import logging
from datetime import timedelta

import click
from flask import Flask, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from app import auth, views
from app.config import Config
from app.core.cache import MemoryCache
from app.core.context import AppContext
from app.core.errors import register_error_handlers
from app.core.http_client import HttpClient
from app.core.ratelimit import FailureLimiter
from app.database import init_app as init_database
from app.models import User
from app.registry import init_services


def create_app(config: Config | None = None) -> Flask:
    cfg = config or Config.from_env()
    cfg.validate()
    logging.basicConfig(level=logging.INFO)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=cfg.secret_key,
        DATABASE_URL=cfg.database_url,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=cfg.is_production,
        PERMANENT_SESSION_LIFETIME=timedelta(days=cfg.session_days),
        MAX_CONTENT_LENGTH=1_000_000,
    )
    app.json.ensure_ascii = False  # Persian text stays readable in JSON
    if cfg.trust_proxy:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    database = init_database(app)
    ctx = AppContext(
        config=cfg,
        http=HttpClient(cfg.http_timeout, cfg.http_max_bytes, cfg.user_agent),
        cache=MemoryCache(cfg.cache_max_items, cfg.cache_ttl),
        login_limiter=FailureLimiter(cfg.login_max_failures, cfg.login_window_seconds),
        database=database,
    )
    app.extensions["mirror"] = ctx

    register_error_handlers(app)
    app.before_request(auth.enforce_access)
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    init_services(app, ctx)

    @app.cli.command("create-user")
    def create_user_command() -> None:
        """Create a browser login account without exposing its password in argv."""
        try:
            username = auth.validate_username(click.prompt("Username"))
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        with database.session_scope() as db_session:
            existing = db_session.scalar(
                select(User.id).where(User.username == username)
            )
        if existing is not None:
            raise click.ClickException("That username is already registered.")

        password = click.prompt("Password", hide_input=True)
        confirmation = click.prompt("Confirm password", hide_input=True)
        if not password or len(password) > 1024:
            raise click.ClickException("Password must contain 1 to 1024 characters.")
        if password != confirmation:
            raise click.ClickException("Passwords do not match.")

        password_hash = generate_password_hash(password)
        try:
            with database.session_scope() as db_session:
                db_session.add(User(username=username, password_hash=password_hash))
                db_session.flush()
        except IntegrityError:
            with database.session_scope() as db_session:
                existing = db_session.scalar(
                    select(User.id).where(User.username == username)
                )
            if existing is None:
                raise
            raise click.ClickException("That username is already registered.")

        click.echo(f"Created user '{username}'.")

    @app.context_processor
    def _inject():
        return {
            "csrf_token": auth.csrf_token,
            "logged_in": auth.get_current_user() is not None,
        }

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.endpoint != "static":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    return app
