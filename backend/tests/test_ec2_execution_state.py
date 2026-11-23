from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.ec2_authority import AuthorityResult, CurrentAuthority
from app.ec2_execution_state import claim, record_interrupted
from app.models import EC2Demo, EC2Execution, EC2ProvisionAudit, EC2ProvisionRequest
from app.ec2_configuration import catalog
from datetime import datetime, timedelta, timezone
from test_ec2_claim_prototype import make_request


def allow(monkeypatch):
    monkeypatch.setattr(CurrentAuthority, "check", lambda self, requester, approver: AuthorityResult(True, "test"))


def test_real_authority_is_blocked_and_audited(client):
    engine = client.app.state.engine
    request_id = make_request(engine)
    assert claim(engine, request_id, "worker") == "current_authority_unavailable_or_denied"
    with Session(engine) as session:
        assert session.get(EC2Demo, 1) is None
        assert session.get(EC2ProvisionRequest, request_id).execution_status == "not_started"
        assert "authorization_blocked" in session.exec(select(EC2ProvisionAudit.event).where(EC2ProvisionAudit.request_id == request_id)).all()


@pytest.mark.parametrize("options", [{"status": "pending"}, {"status": "rejected"},
    {"status": "cancelled"}, {"expired": True}, {"approver": "engineer"}, {"audit": False}])
def test_ineligible_never_claimed(client, monkeypatch, options):
    allow(monkeypatch)
    engine = client.app.state.engine
    assert claim(engine, make_request(engine, **options), "worker") == "ineligible"


@pytest.mark.parametrize("reason", ["engineer_revoked", "approver_revoked", "provider_unavailable"])
def test_authority_denial_fails_closed(client, monkeypatch, reason):
    monkeypatch.setattr(CurrentAuthority, "check", lambda *args: AuthorityResult(False, reason))
    assert claim(client.app.state.engine, make_request(client.app.state.engine), "worker") == "current_authority_unavailable_or_denied"


def test_authority_exception_and_digest_mismatch_fail_closed(client, monkeypatch):
    engine = client.app.state.engine
    def unavailable(*args):
        raise RuntimeError("unavailable")
    monkeypatch.setattr(CurrentAuthority, "check", unavailable)
    assert claim(engine, make_request(engine), "worker") == "current_authority_unavailable_or_denied"
    allow(monkeypatch)
    item = EC2ProvisionRequest(requester="engineer", configuration=catalog()["defaults"], digest="wrong",
        status="approved", approver="approver", decided_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
    request_id = item.id
    with Session(engine) as session:
        session.add(item)
        session.add(EC2ProvisionAudit(request_id=request_id, actor="approver", event="approved"))
        session.commit()
    assert claim(engine, request_id, "worker") == "ineligible"


def test_atomic_claim_reserves_singleton_and_prevents_replay(client, monkeypatch):
    allow(monkeypatch)
    engine = client.app.state.engine
    first, second = make_request(engine), make_request(engine)
    assert claim(engine, first, "worker-a") == "claimed"
    assert claim(engine, first, "worker-b") == "ineligible"
    assert claim(engine, second, "worker-b") == "reservation_or_execution_conflict"
    with Session(engine) as session:
        execution = session.exec(select(EC2Execution)).one()
        reservation = session.get(EC2Demo, 1)
        assert execution.claim_owner == "worker-a"
        assert execution.idempotency_token == reservation.operation_token
        assert reservation.owner_subject == "engineer"
        assert session.get(EC2ProvisionRequest, second).execution_status == "not_started"


def test_concurrent_claims_one_winner(client, monkeypatch):
    allow(monkeypatch)
    engine = client.app.state.engine
    request_id = make_request(engine)
    barrier = Barrier(2, timeout=10)
    def run(worker):
        barrier.wait()
        return claim(engine, request_id, worker)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run, ["a", "b"])) == ["claimed", "ineligible"]


@pytest.mark.parametrize("started,expected", [(False, "needs_operator_review"), (True, "outcome_unknown")])
def test_interrupted_claim_retains_reservation(client, monkeypatch, started, expected):
    allow(monkeypatch)
    engine = client.app.state.engine
    request_id = make_request(engine)
    assert claim(engine, request_id, "worker") == "claimed"
    with Session(engine) as session:
        execution = session.exec(select(EC2Execution)).one()
        execution_id = execution.id
        if started:
            execution.state = "execution_started"
            session.add(execution)
            session.commit()
    assert record_interrupted(engine, execution_id, "operator")
    assert not record_interrupted(engine, execution_id, "operator")
    assert claim(engine, request_id, "other") == "ineligible"
    with Session(engine) as session:
        assert session.get(EC2Execution, execution_id).state == expected
        assert session.get(EC2Demo, 1).status == "needs_operator_review"
        assert session.get(EC2ProvisionRequest, request_id).execution_status == expected
        assert "recovery_" + expected in session.exec(select(EC2ProvisionAudit.event).where(EC2ProvisionAudit.request_id == request_id)).all()


def test_duplicate_token_rejected(client, monkeypatch):
    allow(monkeypatch)
    engine = client.app.state.engine
    first, second = make_request(engine), make_request(engine)
    assert claim(engine, first, "worker") == "claimed"
    with Session(engine) as session:
        existing = session.exec(select(EC2Execution)).one()
        session.add(EC2Execution(request_id=second, claim_owner="other", idempotency_token=existing.idempotency_token))
        with pytest.raises(IntegrityError):
            session.commit()
