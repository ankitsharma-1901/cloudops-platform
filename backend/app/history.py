"""Bounded views over existing persisted requests and sanitized plan jobs."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.approvals import _expire_pending
from app.auth import (
    APPROVER_ROLE, ENGINEER_ROLE, AuthenticatedPrincipal,
    get_authenticated_principal, require_role,
)
from app.database import get_session
from app.models import ApprovalRequest, ProvisioningJob, ServerRequest
from app.plan_policy import is_current_plan
from app.schemas import (
    JobHistoryItem, JobHistoryPage, RequestHistoryItem, RequestHistoryPage,
)

router = APIRouter(prefix="/api/v1/history", tags=["history"])
SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[AuthenticatedPrincipal, Depends(get_authenticated_principal)]
PageLimit = Annotated[int, Query(ge=1, le=50)]
PageOffset = Annotated[int, Query(ge=0, le=100000)]


def _approval(session: Session, job: ProvisioningJob | None) -> ApprovalRequest | None:
    if job is None:
        return None
    approval = session.exec(select(ApprovalRequest).where(
        ApprovalRequest.provisioning_job_id == job.id
    )).first()
    if approval is not None:
        _expire_pending(session, approval.id)
        session.refresh(approval)
    return approval


@router.get("/requests", response_model=RequestHistoryPage)
def list_request_history(
    session: SessionDependency, principal: PrincipalDependency,
    limit: PageLimit = 20, offset: PageOffset = 0,
):
    require_role(principal, ENGINEER_ROLE)
    resources = session.exec(
        select(ServerRequest).where(ServerRequest.owner_subject == principal.subject)
        .order_by(ServerRequest.created_at.desc(), ServerRequest.id.desc())
        .offset(offset).limit(limit + 1)
    ).all()
    items = []
    for resource in resources[:limit]:
        job = session.exec(select(ProvisioningJob).where(
            ProvisioningJob.server_request_id == resource.id
        ).order_by(ProvisioningJob.created_at.desc(), ProvisioningJob.id.desc()).limit(1)).first()
        items.append(RequestHistoryItem(
            request=resource, latest_job=job, approval=_approval(session, job),
        ))
    return RequestHistoryPage(items=items, has_more=len(resources) > limit)


@router.get("/requests/{resource_id}", response_model=JobHistoryPage)
def get_request_history(
    resource_id: str, session: SessionDependency, principal: PrincipalDependency,
    limit: PageLimit = 20, offset: PageOffset = 0,
):
    resource = session.get(ServerRequest, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Server request not found.")
    owns_request = (
        resource.owner_subject == principal.subject and ENGINEER_ROLE in principal.roles
    )
    query = select(ProvisioningJob).where(ProvisioningJob.server_request_id == resource_id)
    if not owns_request:
        require_role(principal, APPROVER_ROLE)
        # A reviewer may inspect submitted plans, never another unsubmitted job.
        query = query.join(ApprovalRequest)
        if session.exec(query.limit(1)).first() is None:
            raise HTTPException(status_code=403, detail="Server request access denied.")
    jobs = session.exec(query.order_by(
        ProvisioningJob.created_at.desc(), ProvisioningJob.id.desc()
    ).offset(offset).limit(limit + 1)).all()
    items = []
    for job in jobs[:limit]:
        current = is_current_plan(session, job)
        items.append(JobHistoryItem(
            job=job, approval=_approval(session, job), is_current_plan=current,
            can_request_approval=owns_request and current,
        ))
    return JobHistoryPage(
        request=resource,
        items=items,
        has_more=len(jobs) > limit,
    )
