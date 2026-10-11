from flask import Blueprint, current_app, jsonify, render_template

bp = Blueprint("diagnostics", __name__)


def _svc():
    return current_app.extensions["mirror"].services["diagnostics"]


@bp.get("/diagnostics")
def page():
    return render_template("diagnostics.html", results=_svc().check())


@bp.get("/api/diagnostics/outbound")
def outbound():
    return jsonify(results=_svc().check())
