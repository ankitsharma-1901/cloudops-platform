"""Database tables, separate from the public request validation schema."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, Index, JSON, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class ServerRequest(SQLModel, table=True):
    """A saved server request; queued means recorded, not provisioned."""

    __tablename__ = "server_requests"
    __table_args__ = (
        UniqueConstraint("environment", "name", name="uq_server_environment_name"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    environment: str
    region: str
    instance_type: str
    install_nginx: bool
    install_docker: bool
    status: str = Field(default="queued")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ProvisioningJob(SQLModel, table=True):
    """A local plan-only job associated with a saved server request."""

    __tablename__ = "provisioning_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'planning', 'plan_ready', 'failed')",
            name="ck_provisioning_job_status",
        ),
        Index(
            "uq_provisioning_job_active_resource",
            "server_request_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'planning')"),
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    server_request_id: str = Field(foreign_key="server_requests.id", index=True)
    status: str = Field(default="queued")
    plan_summary: dict[str, int | str] | None = Field(default=None, sa_type=JSON)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
