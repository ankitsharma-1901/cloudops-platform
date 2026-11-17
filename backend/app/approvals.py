"""Authenticated, plan-bound infrastructure approval API."""

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.auth import (
    APPROVER_ROLE,
    ENGINEER_ROLE,
    AuthenticatedPrincipal,
    get_authenticated_principal,
    require_role,
)
from app.database import get_session
from app.plan_policy import is_current_plan, latest_job_query
from app.models import (
    ApprovalAuditEvent,
    ApprovalRequest,
    ProvisioningJob,
    ServerRequest,
)
from app.schemas import (
    ApprovalAuditEventResponse,
    ApprovalDecisionRequest,
    ApprovalRequestResponse,
)

SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[
    AuthenticatedPrincipal, Depends(get_authenticated_principal)
]
router = APIRouter(prefix="/api/v1", tags=["approvals"])
APPROVAL_LIFETIME = timedelta(minutes=30)


def _require_view_access(
    principal: AuthenticatedPrincipal,
    approval: ApprovalRequest,
) -> None:
    if principal.subject != approval.requested_by and APPROVER_ROLE not in principal.roles:
        raise HTTPException(status_code=403, detail="Approval request access denied.")


def _audit(
    approval: ApprovalRequest,
    event_type: str,
    actor: str,
) -> ApprovalAuditEvent:
    return ApprovalAuditEvent(
        approval_request_id=approval.id,
        event_type=event_type,
        actor_subject=actor,
    )


def _expire_pending(session: Session, approval_id: str) -> bool:
    """Atomically expire a stale pending request and append its audit event."""
    now = datetime.now(timezone.utc)
    result = session.exec(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.status == "pending",
            ApprovalRequest.expires_at <= now,
        )
        .values(status="expired")
        .returning(ApprovalRequest.id)
    )
    if result.scalar_one_or_none() is None:
        return False
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise RuntimeError("Expired approval request disappeared.")
    session.add(_audit(approval, "expired", "system"))
    session.commit()
    return True


@router.post(
    "/jobs/{job_id}/approvals",
    response_model=ApprovalRequestResponse,
    status_code=201,
)
def create_approval_request(
    job_id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ApprovalRequest:
    """Request approval for exactly one completed plan revision."""
    require_role(principal, ENGINEER_ROLE)
    job = session.get(ProvisioningJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Provisioning job not found.")
    if job.status != "plan_ready" or job.plan_revision is None:
        raise HTTPException(status_code=409, detail="Only a completed plan can be approved.")
    resource = session.get(ServerRequest, job.server_request_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Server request not found.")
    if resource.owner_subject != principal.subject:
        raise HTTPException(status_code=403, detail="Server request access denied.")

    if not is_current_plan(session, job):
        raise HTTPException(status_code=409, detail="Only the current intact completed plan can be submitted for approval. Generate a new plan if this result is legacy or superseded.")

    existing = session.exec(
        select(ApprovalRequest).where(ApprovalRequest.provisioning_job_id == job_id)
    ).first()
    if existing is not None:
        _expire_pending(session, existing.id)
        session.refresh(existing)
        return existing

    now = datetime.now(timezone.utc)
    approval = ApprovalRequest(
        server_request_id=job.server_request_id,
        provisioning_job_id=job.id,
        plan_revision=job.plan_revision,
        requested_by=principal.subject,
        requested_at=now,
        expires_at=now + APPROVAL_LIFETIME,
    )
    session.add(approval)
    session.add(_audit(approval, "requested", principal.subject))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.exec(
            select(ApprovalRequest).where(ApprovalRequest.provisioning_job_id == job_id)
        ).first()
        if existing is None:
            raise
        return existing
    session.refresh(approval)
    return approval


@router.get(
    "/jobs/{job_id}/approval",
    response_model=ApprovalRequestResponse,
)
def get_job_approval(
    job_id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ApprovalRequest:
    approval = session.exec(
        select(ApprovalRequest).where(ApprovalRequest.provisioning_job_id == job_id)
    ).first()
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    _require_view_access(principal, approval)
    _expire_pending(session, approval.id)
    session.refresh(approval)
    return approval


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalRequestResponse,
)
def decide_approval(
    approval_id: str,
    decision: ApprovalDecisionRequest,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ApprovalRequest:
    """Apply one authorized decision with an atomic pending-state guard."""
    require_role(principal, APPROVER_ROLE)
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    if approval.requested_by == principal.subject:
        raise HTTPException(status_code=403, detail="Requesters cannot approve their own plans.")
    if _expire_pending(session, approval_id):
        raise HTTPException(status_code=409, detail="Approval request has expired.")

    job = session.get(ProvisioningJob, approval.provisioning_job_id)
    if job is None or not is_current_plan(session, job) or job.plan_revision != approval.plan_revision:
        raise HTTPException(status_code=409, detail="This approval is bound to a superseded or invalid plan. Review the current plan instead.")

    now = datetime.now(timezone.utc)
    result = session.exec(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.status == "pending",
            ApprovalRequest.expires_at > now,
            ApprovalRequest.plan_revision == job.plan_revision,
            ApprovalRequest.provisioning_job_id == latest_job_query(
                ApprovalRequest.server_request_id
            ).correlate(ApprovalRequest).scalar_subquery(),
        )
        .values(
            status=decision.decision,
            decided_by=principal.subject,
            decided_at=now,
            decision_reason=decision.reason,
        )
        .returning(ApprovalRequest.id)
    )
    if result.scalar_one_or_none() is None:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Approval request has already been decided or expired.",
        )
    updated = session.get(ApprovalRequest, approval_id)
    if updated is None:
        raise RuntimeError("Decided approval request disappeared.")
    session.add(_audit(updated, decision.decision, principal.subject))
    session.commit()
    session.refresh(updated)
    return updated


@router.get("/approvals", response_model=list[ApprovalRequestResponse])
def list_approvals(
    session: SessionDependency,
    principal: PrincipalDependency,
    status: str = "pending",
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> list[ApprovalRequest]:
    """Return the review queue to authenticated approvers only."""
    require_role(principal, APPROVER_ROLE)
    if status not in {"pending", "approved", "rejected", "expired", "decided"}:
        raise HTTPException(status_code=422, detail="Invalid approval status.")
    now = datetime.now(timezone.utc)
    query = select(ApprovalRequest)
    if status == "pending":
        query = query.where(ApprovalRequest.status == "pending", ApprovalRequest.expires_at > now)
    elif status == "expired":
        query = query.where(or_(
            ApprovalRequest.status == "expired",
            (ApprovalRequest.status == "pending") & (ApprovalRequest.expires_at <= now),
        ))
    elif status == "decided":
        query = query.where(ApprovalRequest.status.in_(("approved", "rejected")))
    else:
        query = query.where(ApprovalRequest.status == status)
    if mine:
        query = query.where(ApprovalRequest.decided_by == principal.subject)
    sort_time = ApprovalRequest.decided_at if status == "decided" else ApprovalRequest.requested_at
    approvals = list(
        session.exec(
            query.order_by(sort_time.desc(), ApprovalRequest.id.desc())
            .offset(offset).limit(limit)
        ).all()
    )
    for approval in approvals:
        if _expire_pending(session, approval.id):
            session.refresh(approval)
    return approvals


@router.get(
    "/approvals/{approval_id}/audit",
    response_model=list[ApprovalAuditEventResponse],
)
def list_approval_audit(
    approval_id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> list[ApprovalAuditEvent]:
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    _require_view_access(principal, approval)
    _expire_pending(session, approval.id)
    return list(
        session.exec(
            select(ApprovalAuditEvent)
            .where(ApprovalAuditEvent.approval_request_id == approval_id)
            .order_by(ApprovalAuditEvent.occurred_at, ApprovalAuditEvent.id)
        ).all()
    )
