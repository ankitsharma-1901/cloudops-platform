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
    """Create tables and apply additive SQLite migrations."""
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
        if "plan_revision" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE provisioning_jobs "
                        "ADD COLUMN plan_revision VARCHAR(64)"
                    )
                )

    inspector = inspect(engine)
    if engine.dialect.name == "sqlite" and inspector.has_table("server_requests"):
        resource_columns = {
            column["name"] for column in inspector.get_columns("server_requests")
        }
        if "owner_subject" not in resource_columns:
            with engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE server_requests ADD COLUMN owner_subject VARCHAR(255)")
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
            connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS approval_audit_no_update "
                    "BEFORE UPDATE ON approval_audit_events "
                    "BEGIN SELECT RAISE(ABORT, 'approval audit events are immutable'); END"
                )
            )
            connection.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS approval_audit_no_delete "
                    "BEFORE DELETE ON approval_audit_events "
                    "BEGIN SELECT RAISE(ABORT, 'approval audit events are immutable'); END"
                )
            )


def get_session(request: Request) -> Iterator[Session]:
    """Yield a session and always close it after the request completes."""
    with Session(request.app.state.engine) as session:
        yield session
