from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import pytest

from app.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.ec2_configuration import catalog


def principal(client, subject, *roles):
    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(subject, frozenset(roles))


def submitted(client):
    review = client.post("/api/v1/ec2-demo/review", json={**catalog()["defaults"], "instance_name": "test-instance"})
    assert review.status_code == 200
    response = client.post("/api/v1/ec2-requests", json={"review_id": review.json()["review_id"]})
    assert response.status_code == 201
    return response.json()


def test_engineer_approver_and_cancel_workflow(client):
    principal(client, "engineer-1", "engineer")
    item = submitted(client)
    assert item["status"] == "pending" and item["execution_status"] == "not_started"
    assert client.post(f"/api/v1/ec2-requests/{item['id']}/decision", json={"decision": "approved"}).status_code == 403
    principal(client, "engineer-1", "engineer", "approver")
    assert client.post(f"/api/v1/ec2-requests/{item['id']}/decision", json={"decision": "approved"}).status_code == 403
    principal(client, "approver-1", "approver")
    assert client.post(f"/api/v1/ec2-requests/{item['id']}/decision", json={"decision": "approved"}).status_code == 200
    assert client.post(f"/api/v1/ec2-requests/{item['id']}/decision", json={"decision": "approved"}).status_code == 409
    principal(client, "engineer-1", "engineer")
    assert client.post(f"/api/v1/ec2-requests/{item['id']}/cancel").status_code == 200
    assert client.get("/api/v1/ec2-requests").json()[0]["status"] == "cancelled"
    assert [entry["event"] for entry in client.get(f"/api/v1/ec2-requests/{item['id']}/audit").json()] == ["submitted", "approved", "cancelled"]
    with client.app.state.engine.connect() as connection:
        assert list(connection.execute(text("SELECT event FROM ec2_provision_audit ORDER BY occurred_at")).scalars()) == ["submitted", "approved", "cancelled"]


def test_tampering_rejection_and_expiry_fail_closed(client):
    principal(client, "engineer-1", "engineer")
    first = submitted(client)
    second = submitted(client)
    third = submitted(client)
    with pytest.raises(IntegrityError), client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE ec2_provision_requests SET configuration=:configuration WHERE id=:id"), {"configuration": "{}", "id": first["id"]})
    with client.app.state.engine.begin() as connection:
        connection.execute(text("UPDATE ec2_provision_requests SET expires_at='2000-01-01 00:00:00' WHERE id=:id"), {"id": third["id"]})
    principal(client, "approver-1", "approver")
    assert client.post(f"/api/v1/ec2-requests/{first['id']}/decision", json={"decision": "approved"}).status_code == 200
    assert client.post(f"/api/v1/ec2-requests/{second['id']}/decision", json={"decision": "rejected"}).status_code == 200
    assert client.post(f"/api/v1/ec2-requests/{second['id']}/decision", json={"decision": "approved"}).status_code == 409
    assert client.post(f"/api/v1/ec2-requests/{third['id']}/decision", json={"decision": "approved"}).status_code == 409
    assert {x["id"]: x["status"] for x in client.get("/api/v1/ec2-requests").json()}[third["id"]] == "expired"


def test_review_single_use_and_concurrent_decisions(client):
    principal(client, "engineer-1", "engineer")
    review = client.post("/api/v1/ec2-demo/review", json=catalog()["defaults"]).json()
    response = client.post("/api/v1/ec2-requests", json={"review_id": review["review_id"]})
    assert response.status_code == 201
    assert client.post("/api/v1/ec2-requests", json={"review_id": review["review_id"]}).status_code == 409
    principal(client, "approver-1", "approver")
    barrier = Barrier(2, timeout=10)
    def decide(_):
        barrier.wait()
        return client.post(f"/api/v1/ec2-requests/{response.json()['id']}/decision", json={"decision": "approved"}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide, range(2))) == [200, 409]


def test_only_owner_can_cancel_and_cancelled_request_cannot_be_approved(client):
    principal(client, "engineer-1", "engineer")
    item = submitted(client)
    principal(client, "engineer-2", "engineer")
    assert client.get("/api/v1/ec2-requests").json() == []
    assert client.post(f"/api/v1/ec2-requests/{item['id']}/cancel").status_code == 409
    principal(client, "engineer-1", "engineer")
    assert client.post(f"/api/v1/ec2-requests/{item['id']}/cancel").status_code == 200
    principal(client, "approver-1", "approver")
    assert client.post(f"/api/v1/ec2-requests/{item['id']}/decision", json={"decision": "approved"}).status_code == 409


def test_requests_require_authentication(client):
    client.app.dependency_overrides.pop(get_authenticated_principal)
    assert client.get("/api/v1/ec2-requests").status_code == 401
    assert client.post("/api/v1/ec2-requests", json={"review_id": "invalid"}).status_code == 401
