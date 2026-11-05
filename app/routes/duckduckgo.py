from flask import Blueprint, current_app, jsonify, request

from app.core.errors import ServiceError
from app.core.ratelimit import RequestLimiter

bp = Blueprint("duckduckgo", __name__, url_prefix="/api/duckduckgo/v1")
_limiter = RequestLimiter(max_requests=30, window=60)


def _service():
    return current_app.extensions["mirror"].services["duckduckgo"]


def _check_rate_limit():
    key = request.remote_addr or "unknown"
    if not _limiter.allow(key):
        return jsonify(error="Too many DuckDuckGo requests; try again later", status=429), 429
    return None


@bp.get("/search")
def search():
    limited = _check_rate_limit()
    if limited is not None:
        return limited

    raw_limit = request.args.get("limit", "5")
    try:
        limit = int(raw_limit)
    except ValueError:
        raise ServiceError("Limit must be an integer", 400)
    return jsonify(_service().search(
        request.args.get("q", ""),
        limit=limit,
        region=request.args.get("region", "us-en"),
        language=request.args.get("lang"),
        safesearch=request.args.get("safesearch", "moderate"),
    ))


@bp.get("/diagnostics")
def diagnostics():
    limited = _check_rate_limit()
    if limited is not None:
        return limited
    return jsonify(_service().diagnostics())
