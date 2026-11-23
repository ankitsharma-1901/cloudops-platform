"""Exercise API approval, current authority, worker and recovery with offline providers."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text
from sqlmodel import Session, select

from app import ec2_authority, ec2_demo, ec2_worker, worker
from app.ec2_execution_state import approval_binding, claim
from app.models import EC2Demo, EC2Execution, EC2ProvisionRequest
from test_ec2_demo import FakeEC2
from test_ec2_requests import principal, submitted

ISSUER = "https://keycloak.example/auth/realms/cloudops"
ENGINEER = ISSUER + "|engineer-id"
APPROVER = ISSUER + "|approver-id"
INSTANCE = "i-0123456789abcdef0"


class OfflineEC2(FakeEC2):
    visible = True
    duplicate = False
    wrong_token = False
    crash = False

    def run_instances(self, **kwargs):
        result = super().run_instances(**kwargs)
        if self.crash:
            raise KeyboardInterrupt
        return result

    def get_paginator(self, operation):
        assert operation == "describe_instances"
        return SimpleNamespace(paginate=self.pages)

    def pages(self, **kwargs):
        assert kwargs["Filters"] == [
            {"Name": "client-token", "Values": [self.token]},
            {"Name": "tag:CloudOpsDemoId", "Values": [self.token]},
        ]
        return iter([self.describe_instances(), self.describe_instances()] if self.duplicate
                    else [self.describe_instances()])

    def describe_instances(self, **kwargs):
        if not self.visible:
            return {"Reservations": []}
        result = super().describe_instances(**kwargs)
        instance = result["Reservations"][0]["Instances"][0]
        instance["ClientToken"] = "wrong-token" if self.wrong_token else self.token
        instance["Tags"].append({"Key": "Name", "Value": "test-instance"})
        return result


@pytest.fixture
def providers(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDOPS_ENABLE_APPROVED_EC2", "true")
    monkeypatch.setenv("CLOUDOPS_EC2_KEYCLOAK_CLIENT_ID", "test-client")
    monkeypatch.setenv("CLOUDOPS_EC2_KEYCLOAK_CLIENT_SECRET", "test-only")
    monkeypatch.setattr(ec2_demo, "enabled", lambda: True)
    provider = SimpleNamespace(
        assignments={ENGINEER: ["engineer"], APPROVER: ["approver"]},
        checks=0, revoke_at=None, disable_at=None, wrong_user=False, status=200, ec2=OfflineEC2(),
        arn=f"arn:aws:sts::{ec2_demo.ACCOUNT}:assumed-role/{ec2_demo.ROLE}/offline",
    )

    provider.role_response = None

    def manifest():
        return {"OIDC_ISSUER": ISSUER, "CLOUDOPS_ROLE_ASSIGNMENTS": json.dumps(provider.assignments)}

    def keycloak(request):
        if request.method == "POST":
            assert str(request.url) == ISSUER + "/protocol/openid-connect/token"
            assert request.content == b"grant_type=client_credentials"
            assert request.headers["Authorization"].startswith("Basic ")
            provider.checks += 1
            if provider.checks == provider.revoke_at:
                provider.assignments.pop(APPROVER)
            return httpx.Response(provider.status, json={"access_token": "offline-token"})
        prefix = "/auth/admin/realms/cloudops/users/"
        assert request.url.path.startswith(prefix)
        assert request.headers["Authorization"] == "Bearer offline-token"
        user_id, _, endpoint = request.url.path.removeprefix(prefix).partition("/")
        assert user_id in {"approver-id", "engineer-id"}
        if endpoint:
            assert endpoint == "role-mappings/realm/composite"
            if provider.role_response is not None:
                return provider.role_response(user_id)
            return httpx.Response(200, json=[{"name": user_id.removesuffix("-id"), "clientRole": False}])
        return httpx.Response(200, json={"id": "other" if provider.wrong_user else user_id,
            "enabled": not (user_id == "approver-id" and provider.checks == provider.disable_at)})

    client = httpx.Client
    monkeypatch.setattr(ec2_authority, "load_local_config", manifest)
    monkeypatch.setattr(ec2_authority.httpx, "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(keycloak), **kwargs))

    # Keep the real clients() identity checks, but never consult host profiles.
    # Mocking only boto3.Session leaves Botocore's credential resolver live.
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "absent-aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "absent-aws-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    process_provider = SimpleNamespace(_popen=None)

    def set_profile(name, value):
        assert (name, value) == ("profile", ec2_demo.PROFILE)

    def get_component(name):
        assert name == "credential_provider"

        def get_provider(provider_name):
            assert provider_name == "custom-process"
            return process_provider

        return SimpleNamespace(get_provider=get_provider)

    core = SimpleNamespace(set_config_variable=set_profile, get_component=get_component)
    monkeypatch.setattr(ec2_demo.botocore.session, "get_session", lambda: core)

    def aws_session(**kwargs):
        assert kwargs["botocore_session"] is core
        assert process_provider._popen is ec2_demo._NoninteractiveProcess
        assert kwargs["profile_name"] == ec2_demo.PROFILE
        assert kwargs["region_name"] == ec2_demo.REGION

        def aws_client(service, *, config):
            assert config.retries["total_max_attempts"] == 1
            if service == "sts":
                return SimpleNamespace(get_caller_identity=lambda: {"Account": ec2_demo.ACCOUNT, "Arn": provider.arn})
            assert service == "ec2"
            return provider.ec2

        return SimpleNamespace(client=aws_client)

    monkeypatch.setattr(ec2_demo.boto3, "Session", aws_session)
    return provider


def approve(client):
    principal(client, ENGINEER, "engineer")
    request_id = submitted(client)["id"]
    principal(client, APPROVER, "approver")
    assert client.post(f"/api/v1/ec2-requests/{request_id}/decision", json={"decision": "approved"}).status_code == 200
    principal(client, ENGINEER, "engineer")
    return request_id


def state(engine, request_id):
    with Session(engine) as session:
        return session.get(EC2ProvisionRequest, request_id).execution_status, session.get(EC2Demo, 1)


@pytest.mark.parametrize(("status", "roles", "reason"), [
    (200, [], "current_role_denied"),
    (200, {"name": "engineer"}, "current_identity_authority_unavailable"),
    (200, [{"name": "engineer", "clientRole": True}], "current_identity_authority_unavailable"),
    (403, {}, "current_identity_authority_unavailable"),
    (307, {}, "current_identity_authority_unavailable"),
])
def test_current_authority_fails_closed_on_realm_role_lookup(providers, status, roles, reason):
    providers.role_response = lambda user_id: httpx.Response(status, json=roles)
    result = ec2_authority.CurrentAuthority().check(ENGINEER, APPROVER)
    assert not result.allowed
    assert result.reason == reason


def test_normal_worker_approval_through_termination_and_history(client, database_url, monkeypatch, providers):
    request_id = approve(client)
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert client.post("/api/v1/ec2-demo/create", json={"confirmation": "CREATE ONE EC2 DEMO INSTANCE"}).status_code == 503
    worker.run_worker(once=True)
    engine = client.app.state.engine
    status, reservation = state(engine, request_id)
    assert status == "succeeded" and reservation.instance_id == INSTANCE
    launch, = providers.ec2.created
    assert launch["ClientToken"] == reservation.operation_token
    assert launch["MinCount"] == launch["MaxCount"] == 1
    assert launch["MetadataOptions"]["HttpTokens"] == "required"
    assert launch["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is False
    assert launch["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] is True
    assert {"Key": "Name", "Value": "test-instance"} in launch["TagSpecifications"][0]["Tags"]
    assert providers.checks == 2
    assert client.post("/api/v1/ec2-demo/terminate", json={"confirmation": "TERMINATE " + INSTANCE}).status_code == 200
    providers.ec2.state = "terminated"
    assert client.get("/api/v1/ec2-demo").json()["instance"]["status"] == "terminated"
    assert client.post("/api/v1/ec2-demo/complete", json={"confirmation": "COMPLETE " + INSTANCE}).status_code == 200
    assert client.get("/api/v1/ec2-demo").json()["history"][0]["name"] == "test-instance"
    assert not ec2_worker.process_next_ec2(engine, "replay")
    assert len(providers.ec2.created) == 1
    events = client.get(f"/api/v1/ec2-requests/{request_id}/audit").json()
    assert [entry["event"] for entry in events] == [
        "submitted", "approved", "authorization_verified", "claimed", "execution_started", "execution_succeeded",
    ]


@pytest.mark.parametrize("scenario,expected", [
    ("disabled", "not_started"), ("role_removed_before_claim", "not_started"),
    ("role_revoked", "needs_operator_review"), ("user_disabled", "needs_operator_review"),
    ("wrong_user", "not_started"), ("redirect", "not_started"), ("unavailable", "not_started"),
    ("wrong_role", "needs_operator_review"), ("expiry", "needs_operator_review"),
    ("stalled_at_boundary", "outcome_unknown"),
])
def test_launch_fails_closed(client, monkeypatch, providers, scenario, expected):
    engine = client.app.state.engine
    request_id = approve(client)
    if scenario == "disabled":
        monkeypatch.delenv("CLOUDOPS_ENABLE_APPROVED_EC2")
    elif scenario == "role_removed_before_claim":
        providers.assignments[ENGINEER] = []
    elif scenario == "role_revoked":
        providers.revoke_at = 2
    elif scenario == "user_disabled":
        providers.disable_at = 2
    elif scenario == "redirect":
        providers.status = 307
    elif scenario == "unavailable":
        providers.status = 503
    elif scenario == "wrong_user":
        providers.wrong_user = True
    elif scenario == "wrong_role":
        providers.arn = providers.arn.replace(ec2_demo.ROLE, "terraform-role")
    elif scenario == "stalled_at_boundary":
        monkeypatch.setattr(ec2_worker, "monotonic", iter([0, 20]).__next__)
    elif scenario == "expiry":
        describe = providers.ec2.describe_images

        def expire(**kwargs):
            with engine.begin() as connection:
                connection.execute(text("UPDATE ec2_provision_requests SET expires_at='2000-01-01'"))
            return describe(**kwargs)

        monkeypatch.setattr(providers.ec2, "describe_images", expire)
    ec2_worker.process_next_ec2(engine, "worker")
    status, reservation = state(engine, request_id)
    assert status == expected
    if expected == "outcome_unknown":
        assert reservation.status == "needs_operator_review"
    else:
        assert reservation is None
    assert not providers.ec2.created


def test_concurrent_workers_launch_once(client, providers):
    request_id = approve(client)
    engine = client.app.state.engine
    barrier = Barrier(2, timeout=5)

    def poll(actor):
        barrier.wait()
        return ec2_worker.process_next_ec2(engine, actor)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(poll, ["first", "second"])) == [False, True]
    assert state(engine, request_id)[0] == "succeeded"
    assert len(providers.ec2.created) == 1


def test_stale_claim_is_fenced_before_slot_is_released(client, providers, monkeypatch):
    request_id = approve(client)
    engine = client.app.state.engine
    assert claim(engine, request_id, "stalled-worker") == "claimed"
    with Session(engine) as session:
        execution_id = session.exec(select(EC2Execution.id)).one()
        binding = approval_binding(session.get(EC2ProvisionRequest, request_id))
    monkeypatch.setattr(ec2_worker, "STALE_AFTER", timedelta(0))
    assert ec2_worker.process_next_ec2(engine, "recovery")
    assert state(engine, request_id) == ("needs_operator_review", None)
    assert not ec2_worker.begin_execution(engine, execution_id, "stalled-worker", binding)
    assert not providers.ec2.created


@pytest.mark.parametrize("crash", [False, True])
def test_uncertain_or_crashed_launch_requires_positive_reconciliation(client, providers, monkeypatch, crash):
    request_id = approve(client)
    engine = client.app.state.engine
    providers.ec2.visible = False
    if crash:
        providers.ec2.crash = True
        with pytest.raises(KeyboardInterrupt):
            ec2_worker.process_next_ec2(engine, "crashed")
        assert state(engine, request_id)[0] == "execution_started"
        monkeypatch.setattr(ec2_worker, "STALE_AFTER", timedelta(0))
    else:
        providers.ec2.fail = True
        assert ec2_worker.process_next_ec2(engine, "timed-out")
    assert not ec2_worker.process_next_ec2(engine, "recovery")
    status, reservation = state(engine, request_id)
    assert status == "outcome_unknown" and reservation.status == "needs_operator_review"
    providers.ec2.visible = True
    providers.ec2.duplicate = True
    assert not ec2_worker.process_next_ec2(engine, "recovery")
    providers.ec2.duplicate = False
    providers.ec2.wrong_token = True
    assert not ec2_worker.process_next_ec2(engine, "recovery")
    providers.ec2.wrong_token = False
    assert ec2_worker.process_next_ec2(engine, "recovery")
    assert state(engine, request_id)[0] == "succeeded"
    assert len(providers.ec2.created) == 1
