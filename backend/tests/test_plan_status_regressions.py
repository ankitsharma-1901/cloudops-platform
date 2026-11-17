"""Regressions for persisted job reads, JWT ownership and plan eligibility."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import auth
from app.main import create_app
from app.models import ProvisioningJob
from app.schemas import TerraformPlanSummary

SAMPLE = dict(name="poll-regression", environment="development", region="ca-central-1",
              instance_type="t3.micro", install_nginx=True, install_docker=True)
SUMMARY = dict(plan_mode="local", plan_revision="a" * 64, to_add=1, to_change=0, to_destroy=0,
               resource="terraform_data.server_configuration", resource_changes=[])


def save_job(client, summary=None, revision=None, status="plan_ready"):
    resource = client.post("/api/v1/resources", json=SAMPLE).json()
    with Session(client.app.state.engine) as session:
        job = ProvisioningJob(server_request_id=resource["id"], status=status,
                              plan_summary=summary, plan_revision=revision)
        session.add(job); session.commit(); session.refresh(job)
        return resource["id"], job.id


@pytest.mark.parametrize("summary", [
    dict(resource="terraform_data.server_configuration", to_add=1, to_change=0, to_destroy=0),
    dict(plan_mode="local", resource="terraform_data.server_configuration", resource_changes=[], to_add=1, to_change=0, to_destroy=0),
])
def test_legacy_completed_result_is_readable_but_never_approvable(client, summary):
    resource_id, job_id = save_job(client, summary)
    response = client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["plan_summary"]["plan_revision"] is None
    assert response.json()["plan_summary"]["resource_changes"] == []
    assert response.json()["plan_summary"]["to_add"] == 1
    assert client.get("/api/v1/history/requests").status_code == 200
    details = client.get(f"/api/v1/history/requests/{resource_id}").json()
    assert details["items"][0]["can_request_approval"] is False
    assert client.post(f"/api/v1/jobs/{job_id}/approvals").status_code == 409


@pytest.mark.parametrize("tamper", ["mismatched_revision", "missing_summary", "newer_job"])
def test_stale_or_invalid_plan_cannot_be_submitted_or_decided(client, tamper):
    resource_id, job_id = save_job(client, SUMMARY, "a" * 64)
    approval = client.post(f"/api/v1/jobs/{job_id}/approvals")
    assert approval.status_code == 201
    with Session(client.app.state.engine) as session:
        job = session.get(ProvisioningJob, job_id)
        if tamper == "newer_job":
            session.add(ProvisioningJob(server_request_id=resource_id))
        else:
            job.plan_summary = None if tamper == "missing_summary" else {**SUMMARY, "plan_revision": "b" * 64}
            session.add(job)
        session.commit()
    assert client.post(f"/api/v1/jobs/{job_id}/approvals").status_code == 409
    client.app.dependency_overrides[auth.get_authenticated_principal] = lambda: auth.AuthenticatedPrincipal("reviewer", frozenset({"approver"}))
    decision = client.post(f"/api/v1/approvals/{approval.json()['id']}/decision", json={"decision": "approved"})
    assert decision.status_code == 409
    history = client.get(f"/api/v1/history/requests/{resource_id}").json()
    assert history["items"][0]["can_request_approval"] is False


def test_real_signed_api_token_can_create_queue_and_poll_owned_job(database_url, monkeypatch):
    issuer = "https://identity.example/realms/cloudops"
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("OIDC_ISSUER", issuer)
    monkeypatch.setenv("OIDC_AUDIENCE", "cloudops-api")
    monkeypatch.setenv("OIDC_JWKS_URL", "https://identity.example/jwks")
    monkeypatch.setenv("OIDC_ACCESS_TOKEN_TYPES", "JWT")
    monkeypatch.setenv("OIDC_ALLOWED_ALGORITHMS", "RS256")
    monkeypatch.setenv("CLOUDOPS_ROLE_ASSIGNMENTS", json.dumps({f"{issuer}|engineer": ["engineer"], f"{issuer}|other": ["engineer"]}))
    monkeypatch.setattr(auth, "_jwk_client", lambda _: SimpleNamespace(get_signing_key_from_jwt=lambda _: SimpleNamespace(key=private.public_key())))

    def headers(subject="engineer", expired=False):
        now = datetime.now(timezone.utc)
        token = jwt.encode(dict(iss=issuer, aud="cloudops-api", sub=subject, iat=now,
                                exp=now + timedelta(minutes=-1 if expired else 5)), private, algorithm="RS256", headers={"typ": "JWT"})
        return {"Authorization": f"Bearer {token}"}

    with TestClient(create_app(database_url)) as client:
        resource = client.post("/api/v1/resources", json=SAMPLE, headers=headers()).json()
        queued = client.post(f"/api/v1/resources/{resource['id']}/plan", headers=headers())
        assert queued.status_code == 202
        job_id = queued.json()["id"]
        for status in ("queued", "planning", "plan_ready", "failed"):
            with Session(client.app.state.engine) as session:
                job = session.get(ProvisioningJob, job_id)
                job.status = status
                job.plan_summary = SUMMARY if status == "plan_ready" else None
                job.plan_revision = "a" * 64 if status == "plan_ready" else None
                session.add(job); session.commit()
            polled = client.get(f"/api/v1/jobs/{job_id}", headers=headers())
            assert polled.status_code == 200
            assert polled.json()["status"] == status
        assert client.get(f"/api/v1/jobs/{job_id}", headers=headers("other")).status_code == 403
        assert client.get(f"/api/v1/jobs/{job_id}", headers=headers(expired=True)).status_code == 401
