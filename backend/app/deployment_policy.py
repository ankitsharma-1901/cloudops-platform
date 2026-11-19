"""Pure/server-side deployment guards; no cloud commands, paths or credentials from clients."""

import json
import os
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.artifact_store import ArtifactError, ArtifactStore
from app.aws_execution import execution_identity
from app.models import ApprovalRequest, Deployment, ProvisioningJob, SavedPlanArtifact, ServerRequest
from app.plan_policy import is_current_plan
from app.auth import KNOWN_ROLES


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def require_deployable(session: Session, job: ProvisioningJob, *, deployment: Deployment | None = None,
                       revalidate_roles: bool = False) -> tuple[SavedPlanArtifact, ApprovalRequest]:
    resource = session.get(ServerRequest, job.server_request_id)
    artifact = session.exec(select(SavedPlanArtifact).where(SavedPlanArtifact.job_id == job.id)).first()
    approval = session.exec(select(ApprovalRequest).where(ApprovalRequest.provisioning_job_id == job.id)).first()
    now = datetime.now(timezone.utc)
    if (resource is None or job.plan_mode != "aws_development" or resource.environment != "development"
            or resource.region != "ca-central-1" or resource.instance_type != "t3.micro"
            or not is_current_plan(session, job) or artifact is None):
        raise ArtifactError()
    if (artifact.request_id != resource.id or artifact.plan_revision != job.plan_revision
            or artifact.region != "ca-central-1" or artifact.environment != "development"
            or artifact.summary != job.plan_summary or not artifact.create_only
            or utc(artifact.expires_at) <= now or (artifact.consumed_at is not None and deployment is None)):
        raise ArtifactError()
    if (approval is None or approval.status != "approved" or utc(approval.expires_at) <= now
            or approval.plan_revision != job.plan_revision or approval.artifact_digest != artifact.digest
            or approval.requested_by != resource.owner_subject or not approval.decided_by
            or approval.decided_by == resource.owner_subject or approval.decided_at is None):
        raise ArtifactError()
    if deployment is not None and (
        deployment.job_id != job.id or deployment.request_id != resource.id
        or deployment.artifact_id != artifact.id or deployment.artifact_digest != artifact.digest
        or deployment.plan_revision != job.plan_revision or deployment.approval_id != approval.id
        or deployment.requester != resource.owner_subject or deployment.initiator != resource.owner_subject
        or deployment.approver != approval.decided_by
    ):
        raise ArtifactError()
    if revalidate_roles:
        try:
            mapping = json.loads(os.getenv("CLOUDOPS_ROLE_ASSIGNMENTS", "{}"))
            owner_roles = mapping.get(resource.owner_subject, [])
            approver_roles = mapping.get(approval.decided_by, [])
            for roles in (owner_roles, approver_roles):
                if not isinstance(roles, list) or any(not isinstance(role, str) or role not in KNOWN_ROLES for role in roles):
                    raise ArtifactError()
            if "engineer" not in owner_roles or "approver" not in approver_roles:
                raise ArtifactError()
        except (ValueError, TypeError, AttributeError):
            raise ArtifactError() from None
    if execution_identity() != (artifact.account_id, artifact.backend_identity, artifact.configuration_identity):
        raise ArtifactError()
    ArtifactStore().read(artifact.id, artifact.digest)
    return artifact, approval
