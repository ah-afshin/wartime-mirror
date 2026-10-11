"""Auto-discovery: every Service subclass in app/services/*.py is instantiated, and the
matching blueprint `bp` in app/routes/<service.name>.py is registered. Adding a feature
never requires editing this file or create_app()."""
from __future__ import annotations

import importlib
import logging
import pkgutil

from flask import Flask

import app.services as services_pkg
from app.core.context import AppContext
from app.services.base import Service

log = logging.getLogger(__name__)


def _discover() -> list[type[Service]]:
    found = []
    for mod in pkgutil.iter_modules(services_pkg.__path__):
        module = importlib.import_module(f"app.services.{mod.name}")
        for obj in vars(module).values():
            if (isinstance(obj, type) and issubclass(obj, Service) and obj is not Service
                    and obj.__module__ == module.__name__):
                found.append(obj)
    return found


def init_services(flask_app: Flask, ctx: AppContext) -> None:
    enabled = ctx.config.enabled_services
    for cls in _discover():
        if not cls.name:
            raise RuntimeError(f"{cls.__name__} must define a `name`")
        if enabled and cls.name not in enabled:
            continue
        ctx.services[cls.name] = cls(ctx)
        try:
            routes = importlib.import_module(f"app.routes.{cls.name}")
        except ModuleNotFoundError as e:
            if e.name != f"app.routes.{cls.name}":
                raise
            routes = None
        if routes is not None and hasattr(routes, "bp"):
            flask_app.register_blueprint(routes.bp)
        log.info("service enabled: %s", cls.name)
