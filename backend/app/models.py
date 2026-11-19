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
    owner_subject: str | None = Field(default=None, max_length=255, index=True)
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
        Index(
            "uq_provisioning_job_active_aws",
            "plan_mode",
            unique=True,
            sqlite_where=text(
                "plan_mode = 'aws_development' "
                "AND status IN ('queued', 'planning')"
            ),
        ),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    server_request_id: str = Field(foreign_key="server_requests.id", index=True)
    plan_mode: str = Field(default="local")
    status: str = Field(default="queued")
    plan_summary: dict[str, object] | None = Field(default=None, sa_type=JSON)
    plan_revision: str | None = Field(default=None, max_length=64)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ApprovalRequest(SQLModel, table=True):
    """An approval bound to one immutable plan job and revision."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'expired')",
            name="ck_approval_request_status",
        ),
        UniqueConstraint("provisioning_job_id", name="uq_approval_plan_job"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    server_request_id: str = Field(foreign_key="server_requests.id", index=True)
    provisioning_job_id: str = Field(foreign_key="provisioning_jobs.id", index=True)
    plan_revision: str = Field(max_length=64)
    status: str = Field(default="pending")
    requested_by: str = Field(max_length=255)
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    decided_by: str | None = Field(default=None, max_length=255)
    decided_at: datetime | None = None
    decision_reason: str | None = Field(default=None, max_length=500)
    artifact_digest: str | None = Field(default=None, max_length=64)


class ApprovalAuditEvent(SQLModel, table=True):
    """Append-only evidence for an approval lifecycle event."""

    __tablename__ = "approval_audit_events"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    approval_request_id: str = Field(foreign_key="approval_requests.id", index=True)
    event_type: str = Field(max_length=32)
    actor_subject: str = Field(max_length=255)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SavedPlanArtifact(SQLModel, table=True):
    """Private execution metadata. No raw plan JSON or filesystem paths in APIs."""

    __tablename__ = "saved_plan_artifacts"
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    job_id: str = Field(foreign_key="provisioning_jobs.id", unique=True, index=True)
    request_id: str = Field(foreign_key="server_requests.id")
    digest: str = Field(max_length=64)
    plan_revision: str = Field(max_length=64)
    account_id: str
    region: str = "ca-central-1"
    environment: str = "development"
    backend_identity: str
    configuration_identity: str
    stack_id: str | None = None
    terraform_root: str | None = None
    state_key: str | None = None
    network_identity: str | None = None
    create_only: bool = False
    summary: dict[str, object] = Field(sa_type=JSON)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime
    consumed_at: datetime | None = None


class TerraformStateOperation(SQLModel, table=True):
    """Durable mutex: an uncertain operation retains the lock until reconciliation."""

    __tablename__ = "terraform_state_operations"
    key: str = Field(primary_key=True)
    owner_id: str
    operation: str


class Deployment(SQLModel, table=True):
    __tablename__ = "deployments"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed','needs_operator_review')"),
    )
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    request_id: str = Field(foreign_key="server_requests.id", index=True)
    job_id: str = Field(foreign_key="provisioning_jobs.id", unique=True)
    artifact_id: str = Field(foreign_key="saved_plan_artifacts.id", unique=True)
    approval_id: str = Field(foreign_key="approval_requests.id")
    artifact_digest: str
    plan_revision: str
    requester: str
    approver: str
    initiator: str
    status: str = "queued"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    apply_started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, str] = Field(default_factory=dict, sa_type=JSON)
    message: str | None = None
    phase: str | None = None
    reason_code: str | None = None


class DeploymentAuditEvent(SQLModel, table=True):
    __tablename__ = "deployment_audit_events"
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    deployment_id: str = Field(foreign_key="deployments.id", index=True)
    event_type: str
    actor_subject: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class OperatorRecoveryEvent(SQLModel, table=True):
    """Local operator attestation; evidence is a reference, never raw state/logs."""

    __tablename__ = "operator_recovery_events"
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    target_id: str
    action: str
    operator: str
    evidence_reference: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
