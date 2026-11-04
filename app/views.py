from flask import Blueprint, current_app, jsonify, render_template

bp = Blueprint("main", __name__)


def _services():
    return current_app.extensions["mirror"].services


@bp.get("/")
def home():
    return render_template("home.html", services=list(_services().values()))


@bp.get("/chat")
def chat():
    return render_template("chat.html")


@bp.get("/api/services")
def api_services():
    return jsonify(services=[{"name": s.name, "title": s.title, "description": s.description}
                             for s in _services().values()])


@bp.get("/healthz")
def healthz():
    return jsonify(ok=True)
