"""Security and state-transition tests for plan approvals."""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, select

from app.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.main import create_app
from app.models import ApprovalAuditEvent, ProvisioningJob, ServerRequest


def _ready_job(
    client: TestClient,
    revision: str = "a" * 64,
    owner: str = "user:requester",
) -> ProvisioningJob:
    with Session(client.app.state.engine) as session:
        resource = ServerRequest(
            name=f"approval-{revision[0]}",
            environment="development",
            region="ca-central-1",
            instance_type="t3.micro",
            install_nginx=True,
            install_docker=True,
            owner_subject=owner,
        )
        session.add(resource)
        session.commit()
        job = ProvisioningJob(
            server_request_id=resource.id,
            status="plan_ready",
            plan_mode="local",
            plan_revision=revision,
            plan_summary={
                "plan_mode": "local",
                "plan_revision": revision,
                "resource": "terraform_data.server_configuration",
                "resource_changes": [],
                "to_add": 0,
                "to_change": 0,
                "to_destroy": 0,
            },
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def _principal(subject: str, *roles: str):
    return lambda: AuthenticatedPrincipal(subject, frozenset(roles))


def test_approvals_are_disabled_without_authentication(database_url):
    with TestClient(create_app(database_url)) as client:
        job = _ready_job(client)
        response = client.post(f"/api/v1/jobs/{job.id}/approvals")
    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}


def test_request_decision_and_audit_use_server_principals(database_url):
    app = create_app(database_url)
    with TestClient(app) as client:
        job = _ready_job(client)
        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:requester", "engineer"
        )
        created = client.post(f"/api/v1/jobs/{job.id}/approvals")
        assert created.status_code == 201
        approval = created.json()
        assert approval["requested_by"] == "user:requester"
        assert approval["plan_revision"] == "a" * 64

        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:approver", "engineer", "approver"
        )
        decided = client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={"decision": "approved", "reason": "Reviewed plan revision."},
        )
        assert decided.status_code == 200
        assert decided.json()["decided_by"] == "user:approver"
        events = client.get(f"/api/v1/approvals/{approval['id']}/audit")
        assert [event["event_type"] for event in events.json()] == [
            "requested",
            "approved",
        ]


def test_self_approval_and_repeated_transition_are_rejected(database_url):
    app = create_app(database_url)
    with TestClient(app) as client:
        job = _ready_job(client, owner="user:same")
        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:same", "engineer", "approver"
        )
        approval = client.post(f"/api/v1/jobs/{job.id}/approvals").json()
        self_decision = client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={"decision": "approved"},
        )
        assert self_decision.status_code == 403

        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:other", "approver"
        )
        first = client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={"decision": "rejected"},
        )
        repeated = client.post(
            f"/api/v1/approvals/{approval['id']}/decision",
            json={"decision": "approved"},
        )
        assert first.status_code == 200
        assert repeated.status_code == 409


def test_unrelated_authenticated_user_cannot_read_an_approval(database_url):
    app = create_app(database_url)
    with TestClient(app) as client:
        job = _ready_job(client)
        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:requester", "engineer"
        )
        approval = client.post(f"/api/v1/jobs/{job.id}/approvals").json()
        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:unrelated", "engineer"
        )
        assert client.get(f"/api/v1/jobs/{job.id}/approval").status_code == 403
        assert client.get(f"/api/v1/approvals/{approval['id']}/audit").status_code == 403


def test_concurrent_decisions_allow_only_one_transition(database_url):
    app = create_app(database_url)
    with TestClient(app) as client:
        job = _ready_job(client)
        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:requester", "engineer"
        )
        approval_id = client.post(f"/api/v1/jobs/{job.id}/approvals").json()["id"]
        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:approver", "approver"
        )

        def decide(value: str):
            return client.post(
                f"/api/v1/approvals/{approval_id}/decision",
                json={"decision": value},
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(decide, ("approved", "rejected")))
        assert sorted(statuses) == [200, 409]


def test_new_plan_job_requires_a_new_approval(database_url):
    app = create_app(database_url)
    with TestClient(app) as client:
        first_job = _ready_job(client, "a" * 64)
        second_job = _ready_job(client, "b" * 64)
        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:requester", "engineer"
        )
        first = client.post(f"/api/v1/jobs/{first_job.id}/approvals").json()
        second = client.post(f"/api/v1/jobs/{second_job.id}/approvals").json()
        assert first["id"] != second["id"]
        assert first["plan_revision"] != second["plan_revision"]


def test_audit_rows_are_database_immutable(database_url):
    app = create_app(database_url)
    with TestClient(app) as client:
        job = _ready_job(client)
        app.dependency_overrides[get_authenticated_principal] = _principal(
            "user:requester", "engineer"
        )
        approval = client.post(f"/api/v1/jobs/{job.id}/approvals").json()
        with Session(client.app.state.engine) as session:
            event = session.exec(
                select(ApprovalAuditEvent).where(
                    ApprovalAuditEvent.approval_request_id == approval["id"]
                )
            ).one()
            try:
                session.exec(
                    text("UPDATE approval_audit_events SET event_type='changed' WHERE id=:id"),
                    params={"id": event.id},
                )
                session.commit()
            except Exception:
                session.rollback()
            else:
                raise AssertionError("Audit event update unexpectedly succeeded.")
