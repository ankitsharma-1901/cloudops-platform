"""Direct EC2 request and decision records. This module does not execute AWS."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import update
from sqlmodel import Session, select

from app.auth import AuthenticatedPrincipal, get_authenticated_principal, require_role
from app.database import get_session
from app.ec2_configuration import EC2Configuration
from app.models import EC2DemoReview, EC2ProvisionAudit, EC2ProvisionRequest

router = APIRouter(prefix="/api/v1/ec2-requests", tags=["ec2-requests"])


class Submit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_id: str


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approved", "rejected"]


def digest(request_id, requester, configuration):
    payload = {"request_id": request_id, "requester": requester, "configuration": configuration}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def event(session, request_id, actor, name):
    session.add(EC2ProvisionAudit(request_id=request_id, actor=actor, event=name))


def intact(item):
    try:
        configuration = EC2Configuration.model_validate(item.configuration).model_dump()
        return configuration == item.configuration and digest(item.id, item.requester, configuration) == item.digest
    except (ValidationError, ValueError, TypeError):
        return False


def expire(session, item):
    changed = session.exec(update(EC2ProvisionRequest).where(EC2ProvisionRequest.id == item.id,
        EC2ProvisionRequest.status.in_(["pending", "approved"]), EC2ProvisionRequest.execution_status == "not_started",
        EC2ProvisionRequest.expires_at <= datetime.now(timezone.utc)).values(status="expired")
        .execution_options(synchronize_session=False))
    if changed.rowcount == 1:
        event(session, item.id, "system", "expired")
        session.commit()
        session.refresh(item)


@router.post("", status_code=201)
def submit(body: Submit, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    review = session.get(EC2DemoReview, body.review_id)
    if not review or review.owner_subject != principal.subject:
        raise HTTPException(404, "Reviewed configuration not found.")
    try:
        configuration = EC2Configuration.model_validate(review.configuration).model_dump()
    except ValidationError:
        raise HTTPException(409, "Reviewed configuration is no longer supported.") from None
    now = datetime.now(timezone.utc)
    consumed = session.exec(update(EC2DemoReview).where(EC2DemoReview.id == body.review_id,
        EC2DemoReview.owner_subject == principal.subject, EC2DemoReview.consumed_at.is_(None),
        EC2DemoReview.expires_at > now).values(consumed_at=now).execution_options(synchronize_session=False))
    if consumed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "Review expired or was already submitted.")
    item = EC2ProvisionRequest(requester=principal.subject, configuration=configuration, digest="", expires_at=now + timedelta(minutes=30))
    item.digest = digest(item.id, item.requester, configuration)
    session.add(item)
    event(session, item.id, principal.subject, "submitted")
    session.commit()
    session.refresh(item)
    return item


@router.get("")
def list_requests(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    if "approver" in principal.roles:
        query = select(EC2ProvisionRequest)
    else:
        require_role(principal, "engineer")
        query = select(EC2ProvisionRequest).where(EC2ProvisionRequest.requester == principal.subject)
    items = session.exec(query.order_by(EC2ProvisionRequest.created_at.desc()).limit(100)).all()
    for item in items:
        expire(session, item)
    for item in items:
        session.refresh(item)
    return items


@router.post("/{request_id}/decision")
def decide(request_id: str, body: Decision, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "approver")
    item = session.get(EC2ProvisionRequest, request_id)
    if not item:
        raise HTTPException(404, "Request not found.")
    if item.requester == principal.subject:
        raise HTTPException(403, "Self-approval is prohibited.")
    expire(session, item)
    if not intact(item):
        raise HTTPException(409, "Request configuration is invalid or changed.")
    now = datetime.now(timezone.utc)
    changed = session.exec(update(EC2ProvisionRequest).where(EC2ProvisionRequest.id == request_id,
        EC2ProvisionRequest.requester == item.requester, EC2ProvisionRequest.digest == item.digest,
        EC2ProvisionRequest.configuration == item.configuration,
        EC2ProvisionRequest.status == "pending", EC2ProvisionRequest.execution_status == "not_started",
        EC2ProvisionRequest.expires_at > now).values(status=body.decision, approver=principal.subject, decided_at=now)
        .execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "Request expired or already decided.")
    event(session, request_id, principal.subject, body.decision)
    session.commit()
    return {"id": request_id, "status": body.decision}


@router.post("/{request_id}/cancel")
def cancel(request_id: str, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    changed = session.exec(update(EC2ProvisionRequest).where(EC2ProvisionRequest.id == request_id,
        EC2ProvisionRequest.requester == principal.subject, EC2ProvisionRequest.status.in_(["pending", "approved"]),
        EC2ProvisionRequest.execution_status == "not_started").values(status="cancelled").execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "Request cannot be cancelled.")
    event(session, request_id, principal.subject, "cancelled")
    session.commit()
    return {"id": request_id, "status": "cancelled"}


@router.get("/{request_id}/audit")
def audit_history(request_id: str, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    item = session.get(EC2ProvisionRequest, request_id)
    if not item or not (("engineer" in principal.roles and item.requester == principal.subject) or "approver" in principal.roles):
        raise HTTPException(404, "Request not found.")
    return session.exec(select(EC2ProvisionAudit).where(EC2ProvisionAudit.request_id == request_id)
        .order_by(EC2ProvisionAudit.occurred_at, EC2ProvisionAudit.id)).all()
