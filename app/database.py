"""Application-scoped SQLAlchemy engine and explicit session management."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from flask import Flask
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def ensure_database_directory(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return
    database = url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


class Database:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def _create_database(database_url: str) -> Database:
    ensure_database_directory(database_url)
    url = make_url(database_url)
    engine_options = {}
    if url.get_backend_name() == "sqlite":
        engine_options["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **engine_options)
    if url.get_backend_name() == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return Database(engine)


def init_app(app: Flask) -> Database:
    """Initialize the database configured on a Flask application."""
    database = _create_database(app.config["DATABASE_URL"])
    app.extensions["database"] = database
    return database
