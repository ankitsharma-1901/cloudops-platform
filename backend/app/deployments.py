"""Authenticated explicit deploy requests and at-most-once continuous-worker execution."""

from datetime import datetime, timezone
import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.artifact_store import ArtifactError
from app.aws_execution import apply_enabled
from app.auth import AuthenticatedPrincipal, get_authenticated_principal, require_role
from app.database import get_session
from app.deployment_policy import require_deployable, utc
from app.deployment_runner import DeploymentRunner
from app.deployment_diagnostics import DeploymentBlocked, failure_code
from app.models import ApprovalRequest, Deployment, DeploymentAuditEvent, ProvisioningJob, SavedPlanArtifact, ServerRequest, TerraformStateOperation
from app.schemas import DeployApprovedPlanRequest, DeploymentResponse, DeploymentView
from app.state_lock import STATE_KEY, owns_state, reserve_state, release_state

router = APIRouter(prefix="/api/v1", tags=["deployments"])
SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)]
DISABLED = "AWS deployment is disabled by the server safety switch."


def audit(session, deployment, event, actor):
    session.add(DeploymentAuditEvent(deployment_id=deployment.id, event_type=event, actor_subject=actor))


def view_job(session, principal, job_id):
    job = session.get(ProvisioningJob, job_id)
    if job is None:
        raise HTTPException(404, "Provisioning job not found.")
    resource = session.get(ServerRequest, job.server_request_id)
    if resource is None:
        raise HTTPException(404, "Server request not found.")
    if resource and resource.owner_subject == principal.subject and "engineer" in principal.roles:
        return job, resource
    if "approver" in principal.roles and session.exec(select(ApprovalRequest.id).where(ApprovalRequest.provisioning_job_id == job.id)).first():
        return job, resource
    raise HTTPException(403, "Deployment history access denied.")


@router.get("/jobs/{job_id}/deployment", response_model=DeploymentView)
def deployment_view(job_id: str, session: SessionDependency, principal: PrincipalDependency):
    job, resource = view_job(session, principal, job_id)
    deployment = session.exec(select(Deployment).where(Deployment.job_id == job_id)).first()
    artifact = session.exec(select(SavedPlanArtifact).where(SavedPlanArtifact.job_id == job_id)).first()
    reason = None
    if not apply_enabled():
        reason = DISABLED
    elif resource.owner_subject != principal.subject or "engineer" not in principal.roles:
        reason = "Only the engineer who owns this request may initiate deployment."
    elif deployment:
        reason = "This artifact already has a deployment attempt; it cannot be submitted again."
    elif session.get(TerraformStateOperation, STATE_KEY) is not None:
        reason = "The AWS development state is reserved by another operation or requires operator reconciliation."
    elif artifact is None:
        reason = "No executable saved AWS plan exists. Local and legacy results cannot be deployed."
    elif not artifact.create_only:
        reason = "Only create-only plans for the fixed AWS sandbox are deployable."
    elif artifact.consumed_at:
        reason = "The saved plan has been consumed."
    elif utc(artifact.expires_at) <= datetime.now(timezone.utc):
        reason = "The saved plan has expired. Generate and approve a new plan."
    else:
        try:
            require_deployable(session, job)
        except ArtifactError:
            reason = "A current, intact, unexpired AWS plan and separate explicit approval are required."
    events = session.exec(select(DeploymentAuditEvent).where(
        DeploymentAuditEvent.deployment_id == deployment.id
    ).order_by(DeploymentAuditEvent.occurred_at, DeploymentAuditEvent.id).limit(50)).all() if deployment else []
    return DeploymentView(enabled=apply_enabled(), eligible=reason is None, reason=reason,
                          artifact_digest=artifact.digest if artifact else None, plan_revision=job.plan_revision,
                          deployment=deployment, events=events)


@router.post("/jobs/{job_id}/deploy", response_model=DeploymentResponse, status_code=202)
def deploy_approved_plan(job_id: str, body: DeployApprovedPlanRequest,
                         session: SessionDependency, principal: PrincipalDependency):
    require_role(principal, "engineer")
    job, resource = view_job(session, principal, job_id)
    if resource.owner_subject != principal.subject:
        raise HTTPException(403, "Only the request owner may initiate deployment.")
    if not apply_enabled():
        raise HTTPException(503, DISABLED)
    try:
        # Flush the mutex first: eligibility reads and insertion share one write transaction.
        deployment = Deployment(request_id=resource.id, job_id=job.id, artifact_id="", approval_id="",
                                artifact_digest="", plan_revision="", requester=principal.subject,
                                approver="", initiator=principal.subject)
        reserve_state(session, deployment.id, "deploy")
        if session.exec(select(Deployment.id).where(Deployment.job_id == job.id)).first():
            raise HTTPException(409, "A deployment attempt already exists for this plan.")
        artifact, approval = require_deployable(session, job)
        if body.artifact_digest != artifact.digest or body.plan_revision != job.plan_revision:
            raise HTTPException(409, "The selected plan revision or artifact does not match.")
        deployment.artifact_id = artifact.id
        deployment.artifact_digest = artifact.digest
        deployment.plan_revision = job.plan_revision
        deployment.approval_id = approval.id
        deployment.approver = approval.decided_by
        deployment.phase = "queued"
        deployment.reason_code = "queued"
        session.add(deployment)
        audit(session, deployment, "queued", principal.subject)
        session.commit()
        session.refresh(deployment)
        return deployment
    except ArtifactError:
        session.rollback()
        raise HTTPException(409, "Deployment requires an intact, current, unexpired, separately approved create-only AWS plan.") from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "The plan already has an attempt or the AWS state is reserved; operator review may be required.") from None


def process_next_deployment(engine, runner=None) -> bool:
    """Claim once. Nothing automatically retries running/failed/uncertain deployments."""
    runner = runner or DeploymentRunner()
    with Session(engine) as session:
        queued = select(Deployment.id).where(Deployment.status == "queued").order_by(Deployment.created_at, Deployment.id).limit(1).scalar_subquery()
        result = session.exec(update(Deployment).where(Deployment.id == queued, Deployment.status == "queued")
                              .values(status="running", started_at=datetime.now(timezone.utc)).returning(Deployment.id))
        deployment_id = result.scalar_one_or_none()
        if deployment_id is None:
            session.rollback()
            return False
        deployment = session.get(Deployment, deployment_id)
        deployment.phase = "eligibility"
        deployment.reason_code = "running"
        session.add(deployment)
        artifact = session.get(SavedPlanArtifact, deployment.artifact_id)
        previously_consumed = artifact is None or artifact.consumed_at is not None
        if artifact and not previously_consumed:
            artifact.consumed_at = datetime.now(timezone.utc)
            session.add(artifact)
        audit(session, deployment, "running", "worker")
        session.commit()  # Durable claim + single-use burn before any execution boundary.

    crossed_boundary = False
    try:
        with Session(engine) as session:
            deployment = session.get(Deployment, deployment_id)
            job = session.get(ProvisioningJob, deployment.job_id)
            if not apply_enabled():
                raise DeploymentBlocked("execution_disabled")
            if previously_consumed or deployment.status != "running" or not owns_state(session, deployment.id):
                raise ArtifactError()
            artifact, _ = require_deployable(session, job, deployment=deployment, revalidate_roles=True)
            deployment.phase = "preflight"
            session.add(deployment)
            session.commit()
            session.refresh(artifact)
        # Do not hold a SQLite read transaction across credential/CLI timeouts.
        prepared = runner.prepare(artifact, deployment_id)
        with Session(engine) as session:
            deployment = session.get(Deployment, deployment_id)
            job = session.get(ProvisioningJob, deployment.job_id)
            # Revalidate fresh rows after credential/plan inspection, before the durable marker.
            if deployment.status != "running" or not owns_state(session, deployment.id):
                raise ArtifactError()
            require_deployable(session, job, deployment=deployment, revalidate_roles=True)
            if not apply_enabled():
                raise DeploymentBlocked("execution_disabled")
            deployment.apply_started_at = datetime.now(timezone.utc)
            deployment.phase = "apply_started"
            deployment.reason_code = "apply_started"
            session.add(deployment)
            audit(session, deployment, "apply_started", "worker")
            session.commit()
        crossed_boundary = True
        if getattr(prepared, "stack", None) == "aws_ec2_only":
            def final_check():
                with Session(engine) as session:
                    current = session.get(Deployment, deployment_id)
                    if current is None or current.status != "running" or not owns_state(session, deployment_id) or not apply_enabled():
                        raise ArtifactError()
                    job = session.get(ProvisioningJob, current.job_id)
                    if job is None:
                        raise ArtifactError()
                    require_deployable(session, job, deployment=current, revalidate_roles=True)
            prepared.pre_apply_check = final_check
        result = runner.execute(prepared)
        with Session(engine) as session:
            deployment = session.get(Deployment, deployment_id)
            # An operator may have marked an interrupted/uncertain execution while we ran.
            if deployment.status != "running":
                return True
            deployment.status = "succeeded"
            deployment.phase = "completed"
            deployment.reason_code = "succeeded"
            deployment.result = DeploymentRunner.safe_ids(json.dumps({
                name: {"value": value, "sensitive": False} for name, value in result.items()
            }))
            deployment.finished_at = datetime.now(timezone.utc)
            session.add(deployment)
            audit(session, deployment, "succeeded", "worker")
            release_state(session, deployment.id)
            session.commit()
    except Exception as error:
        # An apply exit error/timeout can mean partial creation. Never log the exception text.
        with Session(engine) as session:
            deployment = session.get(Deployment, deployment_id)
            if deployment.status != "running":
                return True  # Never overwrite an operator's offline recovery decision.
            deployment.status = "needs_operator_review" if crossed_boundary else "failed"
            deployment.phase, deployment.reason_code = failure_code(error, crossed_boundary)
            deployment.message = ("Terraform may have changed AWS. Operator reconciliation is required; automatic retry is blocked."
                                  if crossed_boundary else "Deployment was blocked before apply. Check trusted server configuration and generate a new plan.")
            deployment.finished_at = datetime.now(timezone.utc)
            session.add(deployment)
            audit(session, deployment, deployment.status, "worker")
            audit(session, deployment, deployment.reason_code, "worker")
            if not crossed_boundary:
                release_state(session, deployment.id)
            session.commit()
    return True


