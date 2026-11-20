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
    if os.getenv("CLOUDOPS_LOCAL_CONFIG"):
        from app.local_config import load_local_config
        settings = load_local_config()
        if database_url is not None and database_url != settings["DATABASE_URL"]:
            raise ValueError("Explicit database conflicts with pinned local configuration.")
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
    inspector = inspect(engine)
    if engine.dialect.name == "sqlite" and inspector.has_table("approval_requests"):
        if "artifact_digest" not in {column["name"] for column in inspector.get_columns("approval_requests")}:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE approval_requests ADD COLUMN artifact_digest VARCHAR(64)"))
    inspector = inspect(engine)
    if engine.dialect.name == "sqlite" and inspector.has_table("saved_plan_artifacts"):
        columns = {column["name"] for column in inspector.get_columns("saved_plan_artifacts")}
        for name in ("stack_id", "terraform_root", "state_key", "network_identity"):
            if name not in columns:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE saved_plan_artifacts ADD COLUMN {name} VARCHAR"))
    inspector = inspect(engine)
    if engine.dialect.name == "sqlite" and inspector.has_table("deployments"):
        columns = {column["name"] for column in inspector.get_columns("deployments")}
        for name in ("phase", "reason_code"):
            if name not in columns:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE deployments ADD COLUMN {name} VARCHAR"))
    SQLModel.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        with engine.begin() as connection:
            connection.execute(text("CREATE TRIGGER IF NOT EXISTS ec2_demo_audit_no_update BEFORE UPDATE ON ec2_demo_audit BEGIN SELECT RAISE(ABORT, 'demo audit is immutable'); END"))
            connection.execute(text("CREATE TRIGGER IF NOT EXISTS ec2_demo_audit_no_delete BEFORE DELETE ON ec2_demo_audit BEGIN SELECT RAISE(ABORT, 'demo audit is immutable'); END"))
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
            for operation in ("UPDATE", "DELETE"):
                connection.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS recovery_audit_no_{operation.lower()} "
                    f"BEFORE {operation} ON operator_recovery_events "
                    "BEGIN SELECT RAISE(ABORT, 'recovery audit events are immutable'); END"
                ))
                connection.execute(text(
                    f"CREATE TRIGGER IF NOT EXISTS deployment_audit_no_{operation.lower()} "
                    f"BEFORE {operation} ON deployment_audit_events "
                    "BEGIN SELECT RAISE(ABORT, 'deployment audit events are immutable'); END"
                ))
            connection.execute(text(
                "CREATE TRIGGER IF NOT EXISTS no_replan_during_deployment "
                "BEFORE INSERT ON provisioning_jobs WHEN EXISTS "
                "(SELECT 1 FROM deployments WHERE request_id = NEW.server_request_id "
                "AND status IN ('queued','running','needs_operator_review')) "
                "BEGIN SELECT RAISE(ABORT, 'request has an active or uncertain deployment'); END"
            ))


def get_session(request: Request) -> Iterator[Session]:
    """Yield a session and always close it after the request completes."""
    with Session(request.app.state.engine) as session:
        yield session
