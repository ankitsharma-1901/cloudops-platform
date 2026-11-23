"""Transactional claim and recovery for the approved EC2 worker."""

from datetime import datetime, timezone

from sqlalchemy import text, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.deployment_policy import utc
from app.ec2_authority import CurrentAuthority
from app.ec2_requests import event, intact
from app.models import EC2Demo, EC2Execution, EC2ProvisionAudit, EC2ProvisionRequest


def eligible(session, item, state, now):
    if (not item or item.status != "approved" or item.execution_status != state
            or not item.approver or item.approver == item.requester or not item.decided_at
            or utc(item.expires_at) <= now or not intact(item)):
        return False
    return session.exec(select(EC2ProvisionAudit.id).where(
        EC2ProvisionAudit.request_id == item.id, EC2ProvisionAudit.actor == item.approver,
        EC2ProvisionAudit.event == "approved")).first() is not None


def approval_binding(item):
    return item.requester, item.approver, item.digest, item.decided_at, item.expires_at


def authorized(item):
    try:
        return CurrentAuthority().check(item.requester, item.approver).allowed
    except Exception:
        return False


def claim(engine: Engine, request_id: str, worker_id: str) -> str:
    """Check authority outside the transaction, then atomically reserve the singleton."""
    if engine.dialect.name != "sqlite" or not worker_id.strip():
        return "blocked"
    with Session(engine) as session:
        item = session.get(EC2ProvisionRequest, request_id)
        if not item:
            return "not_found"
        binding = approval_binding(item)
        valid = eligible(session, item, "not_started", datetime.now(timezone.utc))
        session.expunge(item)
    allowed = valid and authorized(item)
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        item = session.get(EC2ProvisionRequest, request_id)
        now = datetime.now(timezone.utc)
        if not valid or not eligible(session, item, "not_started", now) or approval_binding(item) != binding:
            event(session, request_id, "worker", "execution_blocked_ineligible")
            session.commit()
            return "ineligible"
        if not allowed:
            event(session, request_id, "worker", "authorization_blocked")
            session.commit()
            return "current_authority_unavailable_or_denied"
        event(session, request_id, "worker", "authorization_verified")
        execution = EC2Execution(request_id=item.id, claim_owner=worker_id)
        reservation = EC2Demo(id=1, owner_subject=item.requester, operation_token=execution.idempotency_token,
                              status="creating", instance_name=item.configuration["instance_name"])
        changed = session.exec(update(EC2ProvisionRequest).where(
            EC2ProvisionRequest.id == item.id, EC2ProvisionRequest.status == "approved",
            EC2ProvisionRequest.execution_status == "not_started", EC2ProvisionRequest.expires_at > now,
            EC2ProvisionRequest.requester == item.requester, EC2ProvisionRequest.approver == item.approver,
            EC2ProvisionRequest.digest == item.digest, EC2ProvisionRequest.configuration == item.configuration,
            EC2ProvisionRequest.decided_at == item.decided_at,
        ).values(execution_status="claimed").execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            session.rollback()
            return "ineligible"
        session.add(execution)
        session.add(reservation)
        event(session, request_id, "worker", "claimed")
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            # Do not modify or release the existing singleton reservation.
            event(session, request_id, "worker", "execution_blocked_reservation_or_replay")
            session.commit()
            return "reservation_or_execution_conflict"
        return "claimed"


def record_interrupted(engine: Engine, execution_id: str, operator: str) -> bool:
    """Mark an independently confirmed stopped worker for review; never retry.

    Caller must prove the worker cannot still run. No API or worker calls this
    function; a future operator workflow must authenticate and enforce that proof.
    """
    if not operator.strip():
        return False
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        execution = session.get(EC2Execution, execution_id)
        if not execution or execution.state not in {"claimed", "execution_started"}:
            session.rollback()
            return False
        state = "outcome_unknown" if execution.state == "execution_started" else "needs_operator_review"
        reservation = session.get(EC2Demo, 1)
        if not reservation or reservation.operation_token != execution.idempotency_token:
            session.rollback()
            return False
        execution.state = state
        reservation.status = "needs_operator_review"
        reservation.message = "Interrupted execution requires operator review; automatic retry is blocked."
        request = session.get(EC2ProvisionRequest, execution.request_id)
        if not request or request.execution_status != "claimed":
            session.rollback()
            return False
        request.execution_status = state
        event(session, request.id, operator, "recovery_" + state)
        session.add(execution)
        session.add(reservation)
        session.add(request)
        session.commit()
        return True
