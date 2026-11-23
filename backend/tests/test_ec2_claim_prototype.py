from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlmodel import Session, select

from app.ec2_claim_prototype import claim_for_offline_test
from app.ec2_configuration import catalog
from app.ec2_requests import digest
from app.models import EC2ProvisionAudit, EC2ProvisionRequest


def roles(subject):
    return {"engineer": frozenset({"engineer"}), "approver": frozenset({"approver"})}.get(subject, frozenset())


def make_request(engine, status="approved", approver="approver", expired=False, audit=True):
    now = datetime.now(timezone.utc)
    item = EC2ProvisionRequest(requester="engineer", configuration=catalog()["defaults"], digest="",
        status=status, approver=approver, decided_at=now,
        expires_at=now + timedelta(seconds=-1 if expired else 300))
    item.digest = digest(item.id, item.requester, item.configuration)
    request_id = item.id
    with Session(engine) as session:
        session.add(item)
        if audit:
            session.add(EC2ProvisionAudit(request_id=item.id, actor=approver, event="approved"))
        session.commit()
    return request_id


def test_one_claim_and_audit(client):
    engine = client.app.state.engine
    request_id = make_request(engine)
    assert claim_for_offline_test(engine, request_id, roles)
    assert not claim_for_offline_test(engine, request_id, roles)
    with Session(engine) as session:
        assert session.get(EC2ProvisionRequest, request_id).execution_status == "claimed"
        assert len(session.exec(select(EC2ProvisionAudit).where(
            EC2ProvisionAudit.request_id == request_id, EC2ProvisionAudit.event == "claimed")).all()) == 1


@pytest.mark.parametrize("options", [
    {"status": "pending"}, {"status": "rejected"}, {"status": "cancelled"},
    {"expired": True}, {"approver": "engineer"}, {"approver": "unknown"}, {"audit": False},
])
def test_ineligible_request_is_not_claimed(client, options):
    engine = client.app.state.engine
    request_id = make_request(engine, **options)
    assert not claim_for_offline_test(engine, request_id, roles)
    with Session(engine) as session:
        assert session.get(EC2ProvisionRequest, request_id).execution_status == "not_started"


def test_role_source_failure_and_revocation_fail_closed(client):
    engine = client.app.state.engine
    request_id = make_request(engine)
    def unavailable(_subject):
        raise RuntimeError("unavailable")
    assert not claim_for_offline_test(engine, request_id, unavailable)
    assert not claim_for_offline_test(engine, request_id, lambda _: frozenset())


def test_configuration_digest_tampering_is_rejected(client):
    engine = client.app.state.engine
    request_id = make_request(engine)
    # Existing trigger blocks changes to the saved configuration itself. Simulate
    # corrupt data before insertion through a separate intentionally invalid row.
    with Session(engine) as session:
        item = EC2ProvisionRequest(requester="engineer", configuration=catalog()["defaults"],
            digest="invalid", status="approved", approver="approver",
            decided_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
        session.add(item)
        session.add(EC2ProvisionAudit(request_id=item.id, actor="approver", event="approved"))
        session.commit()
        invalid_id = item.id
    assert not claim_for_offline_test(engine, invalid_id, roles)
    assert claim_for_offline_test(engine, request_id, roles)


def test_concurrent_claims_have_one_winner(client):
    engine = client.app.state.engine
    request_id = make_request(engine)
    barrier = Barrier(2, timeout=10)
    def attempt(_):
        barrier.wait()
        return claim_for_offline_test(engine, request_id, roles)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [False, True]
