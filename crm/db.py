"""Engine and session helpers for the local SQLite database."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Base


def create_db_engine(db_path=None) -> Engine:
    config.ensure_dirs()
    path = db_path or config.DB_PATH
    engine = create_engine(f"sqlite:///{path.as_posix() if hasattr(path, 'as_posix') else path}",
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def initialize(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    _ensure_columns(engine)


def _ensure_columns(engine: Engine) -> None:
    """Add columns that newer versions introduced to an existing database.

    SQLite can't add a FK constraint via ALTER, so upgraded databases get a
    plain column; new databases get the real constraint from the model.
    """
    additions = {
        "import_batches": {"request_id": "INTEGER"},
        "prospects": {"city": "VARCHAR(160)", "profiles": "JSON NOT NULL DEFAULT '[]'"},
        "research_requests": {
            "kind": "VARCHAR(16) NOT NULL DEFAULT 'discover'",
            "target_ids": "JSON NOT NULL DEFAULT '[]'",
        },
    }
    inspector = inspect(engine)
    for table, columns in additions.items():
        if table not in inspector.get_table_names():
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl_type in columns.items():
            if name not in existing:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
