"""Persistent history must enforce the same boundaries as individual operations."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.main import create_app
from app.models import ApprovalRequest, ProvisioningJob, ServerRequest

OWNER = "https://issuer.example|engineer-user"
BASE = "/api/v1/history/requests"
SAMPLE = dict(name="history-server", environment="development", region="ca-central-1",
              instance_type="t3.micro", install_nginx=True, install_docker=True)


def identity(client, subject, *roles):
    client.app.dependency_overrides[get_authenticated_principal] = lambda: (
        AuthenticatedPrincipal(subject, frozenset(roles))
    )


def seed(client, newer_unsubmitted=True):
    now = datetime.now(timezone.utc)
    with Session(client.app.state.engine) as session:
        resource = ServerRequest(**SAMPLE, owner_subject=OWNER)
        other = ServerRequest(**{**SAMPLE, "name": "other-server"}, owner_subject="other")
        legacy = ServerRequest(**{**SAMPLE, "name": "legacy-server"})
        session.add_all([resource, other, legacy]); session.commit()
        job = ProvisioningJob(server_request_id=resource.id, status="plan_ready",
                              plan_revision="a" * 64, created_at=now - timedelta(minutes=1),
                              plan_summary={"plan_revision": "a" * 64, "plan_mode": "local",
                                            "resource": "terraform_data.server_configuration",
                                            "resource_changes": [], "to_add": 1,
                                            "to_change": 0, "to_destroy": 0,
                                            "raw_state": "must-not-leak"})
        unsubmitted = ProvisioningJob(server_request_id=resource.id, status="failed",
                                     created_at=now if newer_unsubmitted else now - timedelta(minutes=2))
        session.add_all([job, unsubmitted]); session.commit()
        return resource.id, job.id, unsubmitted.id


def test_history_is_owned_bounded_and_newest_first(client):
    resource_id, submitted, newest = seed(client)
    page = client.get(BASE).json()
    assert [item["request"]["id"] for item in page["items"]] == [resource_id]
    assert page["items"][0]["latest_job"]["id"] == newest
    assert page["has_more"] is False
    detail = client.get(f"{BASE}/{resource_id}?limit=1").json()
    assert detail["items"][0]["job"]["id"] == newest
    assert detail["has_more"] is True
    older = client.get(f"{BASE}/{resource_id}?limit=1&offset=1").json()
    assert older["items"][0]["job"]["id"] == submitted
    assert "must-not-leak" not in str(older)
    assert "owner_subject" not in str(page)
    assert client.get(f"{BASE}/{resource_id}?offset=2").json()["items"] == []
    identity(client, "other", "engineer")
    assert client.get(f"{BASE}/{resource_id}").status_code == 403
    identity(client, OWNER, "administrator")
    assert client.get(BASE).status_code == 403
    assert client.get(f"{BASE}/{resource_id}").status_code == 403


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "offset=-1", "offset=100001", "limit=bad"])
def test_history_rejects_invalid_pagination(client, query):
    assert client.get(f"{BASE}?{query}").status_code == 422
    assert client.get(f"{BASE}/unknown?{query}").status_code == 422
    identity(client, "reviewer", "approver")
    assert client.get(f"/api/v1/approvals?{query}").status_code == 422


def test_request_pagination_and_persistence(client, database_url):
    first = client.post("/api/v1/resources", json=SAMPLE).json()
    second = client.post("/api/v1/resources", json={**SAMPLE, "name": "newer-server"}).json()
    application = create_app(database_url)
    application.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(OWNER, frozenset({"engineer"}))
    with TestClient(application) as restarted:
        page = restarted.get(f"{BASE}?limit=1").json()
        assert page["items"][0]["request"]["id"] == second["id"]
        assert page["has_more"] is True
        assert restarted.get(f"{BASE}?limit=1&offset=1").json()["items"][0]["request"]["id"] == first["id"]


def test_reviewer_only_sees_submitted_jobs_and_own_decisions(client):
    resource_id, submitted, unsubmitted = seed(client, newer_unsubmitted=False)
    identity(client, "reviewer", "approver")
    assert client.get(BASE).status_code == 403
    assert client.get(f"{BASE}/{resource_id}").status_code == 403
    identity(client, OWNER, "engineer", "approver")
    approval = client.post(f"/api/v1/jobs/{submitted}/approvals").json()
    assert client.post(f"/api/v1/approvals/{approval['id']}/decision", json={"decision": "approved"}).status_code == 403
    identity(client, "reviewer", "approver")
    details = client.get(f"{BASE}/{resource_id}").json()
    assert [item["job"]["id"] for item in details["items"]] == [submitted]
    assert unsubmitted not in str(details)
    assert client.post(f"/api/v1/resources/{resource_id}/plan").status_code == 403
    assert client.post(f"/api/v1/approvals/{approval['id']}/decision", json={"decision": "approved"}).status_code == 200
    decisions = client.get("/api/v1/approvals?status=decided&mine=true").json()
    assert len(decisions) == 1
    assert datetime.fromisoformat(decisions[0]["decided_at"]).utcoffset() == timedelta(0)
    identity(client, "another-reviewer", "approver")
    assert client.get("/api/v1/approvals?status=decided&mine=true").json() == []
    identity(client, "unrelated", "engineer")
    assert client.get(f"/api/v1/approvals/{approval['id']}/audit").status_code == 403
    assert client.get("/api/v1/approvals?status=decided&mine=true").status_code == 403


def test_expired_approvals_do_not_leave_holes_in_queue(client):
    resource_id, job_id, _ = seed(client, newer_unsubmitted=False)
    approval = client.post(f"/api/v1/jobs/{job_id}/approvals").json()
    with Session(client.app.state.engine) as session:
        row = session.get(ApprovalRequest, approval["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.add(row); session.commit()
    identity(client, "reviewer", "approver")
    assert client.get("/api/v1/approvals?status=pending&limit=1").json() == []
    assert client.get("/api/v1/approvals?status=expired&limit=1").json()[0]["status"] == "expired"
    assert client.get(f"{BASE}/{resource_id}").json()["items"][0]["approval"]["status"] == "expired"
    assert client.post(f"/api/v1/approvals/{approval['id']}/decision", json={"decision": "approved"}).status_code == 409
    audit = client.get(f"/api/v1/approvals/{approval['id']}/audit").json()
    assert [event["event_type"] for event in audit].count("expired") == 1


def test_history_requires_authentication(database_url):
    with TestClient(create_app(database_url)) as anonymous:
        assert anonymous.get(BASE).status_code == 401
        assert anonymous.get(f"{BASE}/unknown").status_code == 401
        assert anonymous.get("/api/v1/approvals?status=decided&mine=true").status_code == 401


def test_empty_and_missing_history(client):
    assert client.get(BASE).json() == {"items": [], "has_more": False}
    assert client.get(f"{BASE}/unknown").status_code == 404
    saved = client.post("/api/v1/resources", json=SAMPLE).json()
    assert client.get(f"{BASE}/{saved['id']}").json()["items"] == []


def test_review_pagination_is_newest_first_and_filters_before_paging(client):
    resource_id, job_id, _ = seed(client, newer_unsubmitted=False)
    first = client.post(f"/api/v1/jobs/{job_id}/approvals").json()
    with Session(client.app.state.engine) as session:
        for index in range(3):
            job = ProvisioningJob(server_request_id=resource_id, status="plan_ready", plan_revision=str(index) * 64)
            session.add(job); session.commit()
            session.add(ApprovalRequest(
                server_request_id=resource_id, provisioning_job_id=job.id,
                plan_revision=job.plan_revision, requested_by=OWNER,
                requested_at=datetime.now(timezone.utc) + timedelta(seconds=index + 1),
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
            ))
        session.commit()
    identity(client, "reviewer", "approver")
    all_items = client.get("/api/v1/approvals").json()
    assert len(all_items) == 4
    assert all_items[-1]["id"] == first["id"]
    assert client.get("/api/v1/approvals?limit=1&offset=1").json() == all_items[1:2]
    assert client.get("/api/v1/approvals?status=invalid").status_code == 422
