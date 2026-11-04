"""Authentication: default-deny. Every endpoint needs a login session or a Bearer token,
except the few listed in PUBLIC_ENDPOINTS. Forgetting a decorator can't open the proxy."""
from __future__ import annotations

import hmac
import secrets
import unicodedata

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from sqlalchemy import select
from werkzeug.security import check_password_hash, generate_password_hash

from app.models import User

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"auth.login", "main.healthz", "static"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))


def _ctx():
    return current_app.extensions["mirror"]


def normalize_username(username: str) -> str:
    return unicodedata.normalize("NFKC", username).strip().casefold()


def validate_username(username: str) -> str:
    normalized = normalize_username(username)
    if (not normalized or len(normalized) > 255
            or any(unicodedata.category(char) == "Cc" for char in normalized)):
        raise ValueError("Username must contain 1 to 255 non-control characters.")
    return normalized


def get_current_user() -> User | None:
    """Return the active database user identified by this browser session."""
    user_id = session.get("user_id")
    if type(user_id) is not int or user_id < 1:
        session.pop("user_id", None)
        session.pop("auth", None)
        return None

    with _ctx().database.session_scope() as db_session:
        user = db_session.get(User, user_id)
        if user is not None and not user.is_active:
            user = None

    if user is None:
        session.pop("user_id", None)
        session.pop("auth", None)
    return user


def csrf_token() -> str:
    tok = session.get("csrf")
    if not tok:
        tok = session["csrf"] = secrets.token_urlsafe(32)
    return tok


def _bearer_ok() -> bool:
    token = _ctx().config.api_token
    header = request.headers.get("Authorization", "")
    if not token or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header[7:].strip().encode(), token.encode())


def csrf_valid() -> bool:
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("csrf", "")
    return bool(sent and expected and hmac.compare_digest(sent, expected))


def _safe_next(nxt: str | None) -> str:
    if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt:
        return nxt
    return url_for("main.home")


def enforce_access():
    """before_request hook: CSRF for browser sessions, then default-deny auth."""
    bearer = _bearer_ok()
    session.pop("auth", None)
    user = get_current_user()
    authenticated = bearer or user is not None
    if (request.path.startswith("/api/") and request.endpoint is not None
            and request.endpoint not in PUBLIC_ENDPOINTS
            and not authenticated):
        return jsonify(error="Authentication required", status=401), 401
    if request.method not in SAFE_METHODS and not bearer and not csrf_valid():
        abort(400, "Missing or invalid CSRF token")
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS or authenticated:
        return None
    target = request.path + ("?" + request.query_string.decode() if request.query_string else "")
    return redirect(url_for("auth.login", next=target))


@bp.route("/login", methods=["GET", "POST"])
def login():
    ctx = _ctx()
    error, status = None, 200
    nxt = request.values.get("next", "")
    if request.method == "POST":
        key = request.remote_addr or "?"
        if ctx.login_limiter.blocked(key):
            error, status = "Too many failed attempts. Try again later.", 429
        else:
            username = normalize_username(request.form.get("username", ""))
            password = request.form.get("password", "")
            user = None
            if username and len(username) <= 255:
                with ctx.database.session_scope() as db_session:
                    user = db_session.scalar(
                        select(User).where(User.username == username)
                    )
            checked_password = password if len(password) <= 1024 else ""
            password_matches = check_password_hash(
                user.password_hash if user is not None else _DUMMY_PASSWORD_HASH,
                checked_password,
            )
            if user is not None and user.is_active and password_matches:
                ctx.login_limiter.reset(key)
                session.clear()
                session["user_id"] = user.id
                session.permanent = True
                return redirect(_safe_next(nxt))
            ctx.login_limiter.fail(key)
            error, status = "Invalid username or password.", 401
    return render_template("login.html", error=error, next=nxt), status


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
