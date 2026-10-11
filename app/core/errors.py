from __future__ import annotations

import logging

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

log = logging.getLogger(__name__)


class ServiceError(Exception):
    """Raise from services/core code; turned into a clean JSON or HTML error."""

    def __init__(self, message: str, status: int = 502, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = detail


def _wants_json() -> bool:
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def _respond(status: int, message: str):
    if _wants_json():
        return jsonify(error=message, status=status), status
    return render_template("error.html", status=status, message=message), status


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ServiceError)
    def _service_error(e: ServiceError):
        return _respond(e.status, e.message)

    @app.errorhandler(HTTPException)
    def _http_error(e: HTTPException):
        return _respond(e.code or 500, e.description or e.name)

    @app.errorhandler(Exception)
    def _unexpected(e: Exception):
        log.exception("Unhandled error")
        return _respond(500, "Internal server error")
