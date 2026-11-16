"""Configurable database engine and one session per API request."""

import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, inspect, text
from sqlmodel import Session, SQLModel, create_engine

DEFAULT_DATABASE_URL = "sqlite:///" + str(
    Path(__file__).resolve().parents[1] / "cloudops.db"
)


def create_database_engine(database_url: str | None = None) -> Engine:
    """Explicit URLs override the environment and local development default."""
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite:") else {}
    return create_engine(url, connect_args=connect_args)


def initialize_database(engine: Engine) -> None:
    """Create tables and apply the one additive Day 10 SQLite migration."""
    inspector = inspect(engine)
    if engine.dialect.name == "sqlite" and inspector.has_table("provisioning_jobs"):
        columns = {
            column["name"]
            for column in inspector.get_columns("provisioning_jobs")
        }
        if "plan_mode" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE provisioning_jobs "
                        "ADD COLUMN plan_mode VARCHAR NOT NULL DEFAULT 'local'"
                    )
                )

    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_provisioning_job_active_aws ON provisioning_jobs (plan_mode) "
                    "WHERE plan_mode = 'aws_development' "
                    "AND status IN ('queued', 'planning')"
                )
            )


def get_session(request: Request) -> Iterator[Session]:
    """Yield a session and always close it after the request completes."""
    with Session(request.app.state.engine) as session:
        yield session
