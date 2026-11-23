"""Offline claim experiment; deliberately not connected to the running worker.

This module cannot execute AWS or reserve an EC2 lifecycle. A successful claim
alone is never authorization to invoke an infrastructure executor.
"""

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import update
from sqlalchemy.engine import Engine
from sqlmodel import Session, select

from app.ec2_requests import event, intact
from app.models import EC2ProvisionAudit, EC2ProvisionRequest


def claim_for_offline_test(engine: Engine, request_id: str,
                           roles_for_subject: Callable[[str], frozenset[str]]) -> bool:
    """Claim once in a test database using an injected, simulated role source.

    The conditional write checks the full decision snapshot so that cancellation,
    expiry or a competing claim cannot win between inspection and the write.
    """
    with Session(engine) as session:
        item = session.get(EC2ProvisionRequest, request_id)
        if not item or not item.approver or item.approver == item.requester or not intact(item):
            return False
        try:
            authorized = ("engineer" in roles_for_subject(item.requester)
                          and "approver" in roles_for_subject(item.approver))
        except Exception:
            return False
        if not authorized:
            return False
        decision = session.exec(select(EC2ProvisionAudit.id).where(
            EC2ProvisionAudit.request_id == item.id,
            EC2ProvisionAudit.actor == item.approver,
            EC2ProvisionAudit.event == "approved")).first()
        if not decision or item.decided_at is None:
            return False
        now = datetime.now(timezone.utc)
        result = session.exec(update(EC2ProvisionRequest).where(
            EC2ProvisionRequest.id == item.id,
            EC2ProvisionRequest.requester == item.requester,
            EC2ProvisionRequest.approver == item.approver,
            EC2ProvisionRequest.configuration == item.configuration,
            EC2ProvisionRequest.digest == item.digest,
            EC2ProvisionRequest.decided_at == item.decided_at,
            EC2ProvisionRequest.status == "approved",
            EC2ProvisionRequest.execution_status == "not_started",
            EC2ProvisionRequest.expires_at > now,
        ).values(execution_status="claimed").execution_options(synchronize_session=False))
        if result.rowcount != 1:
            session.rollback()
            return False
        event(session, request_id, "offline-test-worker", "claimed")
        session.commit()
        return True
