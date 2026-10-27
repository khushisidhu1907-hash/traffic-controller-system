"""Database configuration and request-scoped SQLAlchemy sessions."""
import os
from typing import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session


def _build_engine(url: str):
    options = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=options, future=True)


DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./traffic.db")
engine = _build_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def configure_database(url: str) -> None:
    """Reconfigure database connection (also used by isolated tests)."""
    global engine, SessionLocal, DATABASE_URL
    engine.dispose()
    DATABASE_URL = url
    engine = _build_engine(url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def create_tables() -> None:
    from backend import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # create_all does not alter existing tables. These nullable additions keep
    # existing SQLite project databases usable without discarding their data.
    additions = {
        "metric_snapshots": {"controller_mode": "VARCHAR(16)"},
        "control_decisions": {"observed_metrics": "JSON"},
    }
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table, columns in additions.items():
            if table not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspect(connection).get_columns(table)}
            for name, sql_type in columns.items():
                if name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
