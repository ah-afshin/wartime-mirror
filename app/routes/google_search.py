from flask import Blueprint, current_app, jsonify, render_template, request

from app.core.errors import ServiceError
from app.core.ratelimit import RequestLimiter

bp = Blueprint("google_search", __name__)
_limiter = RequestLimiter(max_requests=30, window=60)


def _service():
    return current_app.extensions["mirror"].services["google_search"]


def _check_rate_limit():
    key = request.remote_addr or "unknown"
    if not _limiter.allow(key):
        return jsonify(error="Too many Google Search requests; try again later", status=429), 429
    return None


def _get_limit() -> int:
    raw_limit = request.args.get("limit", "5")
    try:
        return int(raw_limit)
    except ValueError as error:
        raise ServiceError("Limit must be an integer", 400) from error


@bp.get("/api/google-search/v1/search")
def search():
    limited = _check_rate_limit()
    if limited is not None:
        return limited
    return jsonify(_service().search(
        request.args.get("q", ""),
        limit=_get_limit(),
        gl=request.args.get("gl", "us"),
        hl=request.args.get("hl", "en"),
    ))


@bp.get("/api/google-search/v1/diagnostics")
def diagnostics():
    limited = _check_rate_limit()
    if limited is not None:
        return limited
    return jsonify(_service().diagnostics())


@bp.get("/google-search")
def page():
    return render_template("google_search.html")
