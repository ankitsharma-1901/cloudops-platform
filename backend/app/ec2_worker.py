"""Execute an independently approved EC2 request or reconcile its result."""

import os
import re
from time import monotonic
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlmodel import Session, select

from app import ec2_demo
from app.deployment_policy import utc
from app.ec2_demo_catalog import ACCOUNT
from app.ec2_execution_state import approval_binding, authorized, claim, eligible
from app.ec2_requests import event, intact
from app.models import EC2Demo, EC2Execution, EC2ProvisionRequest

STALE_AFTER = timedelta(minutes=5)
LAUNCH_WINDOW_SECONDS = 15
INSTANCE_ID = re.compile(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})")


def enabled():
    return os.getenv("CLOUDOPS_ENABLE_APPROVED_EC2") == "true" and ec2_demo.enabled()


def load_execution(session, execution_id):
    execution = session.get(EC2Execution, execution_id)
    if execution is None:
        return None
    request = session.get(EC2ProvisionRequest, execution.request_id)
    reservation = session.get(EC2Demo, 1)
    if (request is None or reservation is None or reservation.operation_token != execution.idempotency_token
            or reservation.owner_subject != request.requester or not intact(request)
            or reservation.instance_name != request.configuration["instance_name"]):
        return None
    return execution, request, reservation


def begin_execution(engine, execution_id, worker_id, binding):
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        rows = load_execution(session, execution_id)
        if rows is None:
            return False
        execution, request, reservation = rows
        now = datetime.now(timezone.utc)
        if (execution.claim_owner != worker_id or execution.state != "claimed"
                or reservation.status != "creating" or reservation.instance_id is not None
                or approval_binding(request) != binding or not eligible(session, request, "claimed", now)):
            return False
        execution.state = "execution_started"
        execution.started_at = now
        request.execution_status = "execution_started"
        event(session, request.id, worker_id, "execution_started")
        ec2_demo.audit(session, request.requester, "create_started")
        session.commit()
        return True


def stop_before_launch(engine, execution_id, actor, reason, *, stale=False):
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        rows = load_execution(session, execution_id)
        if rows is None:
            return False
        execution, request, reservation = rows
        if (execution.state != "claimed" or request.execution_status != "claimed"
                or execution.started_at is not None or reservation.status != "creating"
                or reservation.instance_id is not None):
            return False
        if stale:
            if datetime.now(timezone.utc) - utc(execution.claimed_at) < STALE_AFTER:
                return False
        elif execution.claim_owner != actor:
            return False
        # Persist the fence before releasing the slot. A paused worker must win
        # begin_execution before making a mutating call, and it can no longer win.
        execution.state = "needs_operator_review"
        request.execution_status = "needs_operator_review"
        session.delete(reservation)
        event(session, request.id, actor, reason)
        session.commit()
        return True


def mark_unknown(engine, execution_id, actor):
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        rows = load_execution(session, execution_id)
        if rows is None:
            return
        execution, request, reservation = rows
        if (execution.state != "execution_started" or request.execution_status != "execution_started"
                or reservation.status != "creating" or reservation.instance_id is not None):
            return
        execution.state = "outcome_unknown"
        request.execution_status = "outcome_unknown"
        reservation.status = "needs_operator_review"
        reservation.message = "AWS result is uncertain. The reservation is retained pending reconciliation."
        event(session, request.id, actor, "outcome_unknown")
        ec2_demo.audit(session, request.requester, "create_uncertain")
        session.commit()


def finish_execution(engine, execution_id, actor, instance_id, *, reconciled=False):
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        rows = load_execution(session, execution_id)
        if rows is None:
            return False
        execution, request, reservation = rows
        if (execution.state not in {"execution_started", "outcome_unknown"}
                or request.execution_status != execution.state
                or reservation.status not in {"creating", "needs_operator_review"}
                or reservation.instance_id is not None
                or (not reconciled and execution.claim_owner != actor)):
            return False
        # The execution row records the launch boundary. The request records
        # completion so existing installations can retain the Phase 1C.1 schema.
        execution.state = "execution_started"
        request.execution_status = "succeeded"
        reservation.instance_id = instance_id
        reservation.status = "created"
        reservation.message = None
        event(session, request.id, actor, "execution_reconciled" if reconciled else "execution_succeeded")
        ec2_demo.audit(session, request.requester, "create_reconciled" if reconciled else "create_returned")
        session.commit()
        return True


def reconcile(engine, execution_id, actor, token, owner, instance_name):
    try:
        ec2 = ec2_demo.clients()
        paginator = ec2.get_paginator("describe_instances")
        pages = paginator.paginate(Filters=[
            {"Name": "client-token", "Values": [token]},
            {"Name": "tag:CloudOpsDemoId", "Values": [token]},
        ], PaginationConfig={"MaxItems": 2})
        instances = [(reservation.get("OwnerId"), instance) for page in pages
                     for reservation in page.get("Reservations", [])
                     for instance in reservation.get("Instances", [])]
        if len(instances) != 1:
            return False
        account, instance = instances[0]
        instance_id = instance.get("InstanceId", "")
        if account != ACCOUNT or not INSTANCE_ID.fullmatch(instance_id):
            return False
        record = EC2Demo(owner_subject=owner, instance_name=instance_name,
                         operation_token=token, status="needs_operator_review", instance_id=instance_id)
        live = ec2_demo.live_instance(ec2, record, allow_terminated_network_absence=True)
        if (live.get("ClientToken") != token
                or not any(tag.get("Key") == "Name" and tag.get("Value") == instance_name
                           for tag in live.get("Tags", []))):
            return False
    except Exception:
        # Failed or empty observations do not prove that a launch never happened.
        return False
    return finish_execution(engine, execution_id, actor, instance_id, reconciled=True)


def execute_claim(engine, execution_id, worker_id):
    with Session(engine) as session:
        rows = load_execution(session, execution_id)
        if rows is None:
            return
        execution, request, _ = rows
        if execution.claim_owner != worker_id or execution.state != "claimed":
            return
        binding = approval_binding(request)
        token = execution.idempotency_token
        instance_name = request.configuration["instance_name"]
        session.expunge(request)
    try:
        if not enabled():
            raise ValueError("disabled")
        ec2 = ec2_demo.clients()
        parameters = ec2_demo.launch_parameters(ec2, token, instance_name)
    except Exception:
        stop_before_launch(engine, execution_id, worker_id, "execution_preflight_blocked")
        return
    if not authorized(request) or not enabled():
        stop_before_launch(engine, execution_id, worker_id, "execution_authorization_blocked")
        return
    deadline = monotonic() + LAUNCH_WINDOW_SECONDS
    if not begin_execution(engine, execution_id, worker_id, binding):
        stop_before_launch(engine, execution_id, worker_id, "execution_revalidation_blocked")
        return
    if monotonic() >= deadline or utc(request.expires_at) <= datetime.now(timezone.utc):
        mark_unknown(engine, execution_id, worker_id)
        return
    try:
        instance_id = ec2_demo.launch_instance(ec2, parameters)
    except Exception:
        mark_unknown(engine, execution_id, worker_id)
        return
    finish_execution(engine, execution_id, worker_id, instance_id)


def process_next_ec2(engine, worker_id):
    if not enabled() or engine.dialect.name != "sqlite":
        return False
    with Session(engine) as session:
        reservation = session.get(EC2Demo, 1)
        if reservation is None:
            candidates = session.exec(select(EC2ProvisionRequest.id).where(
                EC2ProvisionRequest.status == "approved", EC2ProvisionRequest.execution_status == "not_started",
                EC2ProvisionRequest.expires_at > datetime.now(timezone.utc),
            ).order_by(EC2ProvisionRequest.created_at, EC2ProvisionRequest.id)).all()
        else:
            execution_id = session.exec(select(EC2Execution.id).where(
                EC2Execution.idempotency_token == reservation.operation_token)).first()
            rows = load_execution(session, execution_id) if execution_id else None
            if rows is None:
                return False
            execution, request, _ = rows
            state = request.execution_status
            start = utc(execution.started_at or execution.claimed_at)
            token, owner, name = execution.idempotency_token, request.requester, reservation.instance_name
    if reservation is not None:
        if state == "outcome_unknown":
            return reconcile(engine, execution_id, worker_id, token, owner, name)
        if datetime.now(timezone.utc) - start < STALE_AFTER:
            return False
        if state == "claimed":
            return stop_before_launch(engine, execution_id, worker_id, "interrupted_before_launch", stale=True)
        if state == "execution_started":
            mark_unknown(engine, execution_id, worker_id)
            return reconcile(engine, execution_id, worker_id, token, owner, name)
        return False
    for request_id in candidates:
        result = claim(engine, request_id, worker_id)
        if result == "reservation_or_execution_conflict":
            break
        if result == "claimed":
            with Session(engine) as session:
                execution_id = session.exec(select(EC2Execution.id).where(
                    EC2Execution.request_id == request_id)).one()
            execute_claim(engine, execution_id, worker_id)
            return True
    return False
