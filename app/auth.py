"""Authentication: default-deny. Every endpoint needs a login session or a Bearer token,
except the few listed in PUBLIC_ENDPOINTS. Forgetting a decorator can't open the proxy."""
from __future__ import annotations

import hmac
import secrets

from flask import (Blueprint, abort, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash

bp = Blueprint("auth", __name__)

PUBLIC_ENDPOINTS = {"auth.login", "main.healthz", "static"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _ctx():
    return current_app.extensions["mirror"]


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


def _csrf_ok() -> bool:
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
    if request.method not in SAFE_METHODS and not bearer and not _csrf_ok():
        abort(400, "Missing or invalid CSRF token")
    if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS or bearer or session.get("auth") is True:
        return None
    if request.path.startswith("/api/"):
        return jsonify(error="Authentication required", status=401), 401
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
            pw = request.form.get("password", "")
            if ctx.config.password_hash and check_password_hash(ctx.config.password_hash, pw):
                ctx.login_limiter.reset(key)
                session.clear()
                session["auth"] = True
                session.permanent = True
                return redirect(_safe_next(nxt))
            ctx.login_limiter.fail(key)
            error, status = "Wrong password.", 401
    return render_template("login.html", error=error, next=nxt), status


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
