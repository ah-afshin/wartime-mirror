from flask import Blueprint, current_app, jsonify, request

from app.core.errors import ServiceError
from app.core.ratelimit import RequestLimiter

bp = Blueprint("wikipedia", __name__, url_prefix="/api/wikipedia/v1")
_limiter = RequestLimiter(max_requests=60, window=60)


def _service():
    return current_app.extensions["mirror"].services["wikipedia"]


def _check_rate_limit():
    key = request.remote_addr or "unknown"
    if not _limiter.allow(key):
        return jsonify(error="Too many Wikipedia requests; try again later", status=429), 429
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
        language=request.args.get("lang", "en"),
        limit=limit,
    ))


@bp.get("/page")
def page():
    limited = _check_rate_limit()
    if limited is not None:
        return limited
    return jsonify(_service().page(
        request.args.get("title", ""),
        language=request.args.get("lang", "en"),
    ))


@bp.get("/diagnostics")
def diagnostics():
    limited = _check_rate_limit()
    if limited is not None:
        return limited
    return jsonify(_service().diagnostics())
