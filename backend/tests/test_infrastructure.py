"""Inventory tests never invoke AWS or read a real Terraform state."""
import json
from types import SimpleNamespace

import pytest

from app import infrastructure
from app.auth import AuthenticatedPrincipal, get_authenticated_principal

INSTANCE = "i-0123456789abcdef0"


@pytest.fixture
def mocked_aws(monkeypatch):
    monkeypatch.setattr(infrastructure.shutil, "which", lambda _: "aws")
    monkeypatch.setattr(infrastructure, "stack_identity", lambda _: ("account", "backend", "config"))
    state = {"version": 4, "resources": [{"mode": "managed", "type": "aws_instance", "name": "dev",
        "instances": [{"attributes": {"id": INSTANCE, "secret": "never-return-this"}}]}]}
    responses = {
        "sts": {"Account": "294665681966", "Arn": "arn:aws:sts::294665681966:assumed-role/cloudops-terraform-deploy/test"},
        "ec2": {"Reservations": [{"Instances": [{"InstanceId": INSTANCE, "State": {"Name": "running"},
            "InstanceType": "t3.micro", "Tags": [{"Key": "Name", "Value": "dev"}], "SecurityGroups": []},
            {"InstanceId": "i-12345678", "State": {"Name": "stopped"}, "SecurityGroups": []}]}]},
        "s3api": {"Contents": [{"Key": infrastructure.STATE_KEY}]}, "s3": state,
    }
    def fake_run(self, executable, args, phase, cwd, env, timeout=None):
        assert args[0] in responses
        assert all(part not in args for part in ("apply", "destroy", "terminate-instances"))
        return SimpleNamespace(stdout=json.dumps(responses[args[0]]))
    monkeypatch.setattr(infrastructure.TerraformPlanRunner, "_run", fake_run)
    return responses


def test_inventory_classifies_only_verified_ec2_state(mocked_aws):
    result = infrastructure.read_inventory()
    assert result["aws_status"] == result["state_status"] == "available"
    assert [i["ownership"] for i in result["instances"]] == ["managed", "unmanaged"]
    assert result["instances"][0]["stack"] == "aws_ec2_only"
    assert "never-return-this" not in json.dumps(result)


def test_unknown_ownership_when_state_unavailable(mocked_aws):
    mocked_aws["s3api"] = {"Contents": []}
    result = infrastructure.read_inventory()
    assert result["state_status"] == "unknown"
    assert all(i["ownership"] == "unknown" for i in result["instances"])


def test_unexpected_state_resources_fail_closed(mocked_aws):
    mocked_aws["s3"]["resources"].append({"mode": "managed", "type": "aws_vpc", "name": "other"})
    assert infrastructure.read_inventory()["state_status"] == "unknown"


def test_inventory_requires_engineer_and_is_not_cached(client, mocked_aws):
    response = client.get("/api/v1/infrastructure")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal("reviewer", frozenset({"approver"}))
    assert client.get("/api/v1/infrastructure").status_code == 403


def test_aws_failure_is_sanitized(monkeypatch):
    monkeypatch.setattr(infrastructure.shutil, "which", lambda _: "aws")
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive diagnostic")
    monkeypatch.setattr(infrastructure.TerraformPlanRunner, "_run", fail)
    result = infrastructure.read_inventory()
    assert result == {"aws_status": "unavailable", "state_status": "unknown", "instances": []}
    assert "sensitive diagnostic" not in json.dumps(result)


def test_partial_deployment_and_instance_link_are_visible_to_owner(client, mocked_aws):
    from sqlmodel import Session
    from app.models import Deployment, ProvisioningJob
    body = {"name": "inventory-test", "environment": "development", "region": "ca-central-1",
            "instance_type": "t3.micro", "install_nginx": False, "install_docker": False}
    request_id = client.post("/api/v1/resources", json=body).json()["id"]
    with Session(client.app.state.engine) as session:
        job = ProvisioningJob(server_request_id=request_id, plan_mode="aws_ec2_only")
        session.add(job)
        session.commit()
        deployment = Deployment(request_id=request_id, job_id=job.id, artifact_id="artifact", approval_id="approval",
            artifact_digest="digest", plan_revision="revision", requester="https://issuer.example|engineer-user",
            approver="reviewer", initiator="https://issuer.example|engineer-user", status="needs_operator_review",
            result={"instance_id": INSTANCE})
        session.add(deployment)
        session.commit()
        deployment_id = deployment.id
    result = client.get("/api/v1/infrastructure").json()
    assert result["instances"][0]["latest_deployment"]["id"] == deployment_id
    assert result["recent_deployments"][0]["status"] == "needs_operator_review"
