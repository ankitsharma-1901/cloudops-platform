import copy
import json

import pytest

from app.artifact_store import ArtifactError
from app.ec2_only_policy import AMI, SUBNET, VPC, require_one_instance, require_restricted_network

GROUP = "sg-0123456789abcdef0"


@pytest.fixture
def plan():
    return {"resource_changes": [{"mode": "managed", "address": "aws_instance.dev", "change": {
        "actions": ["create"], "after": {"ami": AMI, "instance_type": "t3.micro", "subnet_id": SUBNET,
        "vpc_security_group_ids": [GROUP], "associate_public_ip_address": False, "iam_instance_profile": None,
        "tags_all": {"Project": "CloudOps", "Environment": "development", "ManagedBy": "Terraform"},
        "root_block_device": [{"encrypted": True, "volume_type": "gp3", "volume_size": 8, "delete_on_termination": True}],
        "metadata_options": [{"http_tokens": "required"}]}}}]}


def test_one_hardened_instance_accepted(plan):
    require_one_instance(json.dumps(plan), GROUP)


@pytest.mark.parametrize("mutation", ["ami", "public", "profile", "extra", "delete", "update", "encrypted", "group"])
def test_unreviewed_or_destructive_changes_rejected(plan, mutation):
    plan = copy.deepcopy(plan)
    change = plan["resource_changes"][0]["change"]
    after = change["after"]
    if mutation == "ami": after["ami"] = "ami-00000000000000000"
    if mutation == "public": after["associate_public_ip_address"] = True
    if mutation == "profile": after["iam_instance_profile"] = "unexpected"
    if mutation == "extra": plan["resource_changes"].append({"mode": "managed", "address": "aws_vpc.dev", "change": {"actions": ["create"]}})
    if mutation == "delete": change["actions"] = ["delete"]
    if mutation == "update": change["actions"] = ["update"]
    if mutation == "encrypted": after["root_block_device"][0]["encrypted"] = False
    if mutation == "group": after["vpc_security_group_ids"] = ["sg-12345678"]
    with pytest.raises(ArtifactError):
        require_one_instance(json.dumps(plan), GROUP)


def test_network_preflight_fails_closed_for_ingress_or_missing_data():
    subnet = {"SubnetId": SUBNET, "VpcId": VPC, "OwnerId": "294665681966", "AvailabilityZone": "ca-central-1a", "State": "available"}
    group = {"GroupId": GROUP, "VpcId": VPC, "OwnerId": "294665681966", "GroupName": "cloudops", "IpPermissions": []}
    require_restricted_network(subnet, group, GROUP)
    for unsafe in ({**group, "IpPermissions": [{"IpProtocol": "tcp", "FromPort": 22}]}, {}, {**group, "VpcId": "vpc-other"}):
        with pytest.raises(ArtifactError):
            require_restricted_network(subnet, unsafe, GROUP)


def test_unknown_instance_profile_fails_closed(plan):
    plan["resource_changes"][0]["change"]["after_unknown"] = {"iam_instance_profile": True}
    with pytest.raises(ArtifactError):
        require_one_instance(json.dumps(plan), GROUP)


def test_stack_identity_rejects_legacy_backend_and_binds_group(tmp_path):
    from app.ec2_only_stack import ACCOUNT, BUCKET, FILES, STATE_KEY, stack_identity
    for name in FILES:
        (tmp_path / name).write_text("synthetic")
    metadata = tmp_path / ".terraform" / "terraform.tfstate"
    metadata.parent.mkdir()
    config = {"bucket": BUCKET, "key": STATE_KEY, "region": "ca-central-1", "allowed_account_ids": [ACCOUNT], "encrypt": True, "use_lockfile": True}
    def write():
        metadata.write_text(json.dumps({"backend": {"type": "s3", "config": config}}))
    write()
    first = stack_identity(GROUP, tmp_path)
    assert first != stack_identity("sg-11111111111111111", tmp_path)
    config["key"] = "cloudops/development/terraform.tfstate"
    write()
    with pytest.raises(ArtifactError):
        stack_identity(GROUP, tmp_path)


def test_network_preflight_fails_closed_when_aws_denies():
    from app.ec2_only_stack import verify_network
    from app.terraform_runner import TerraformRunnerError
    class DeniedRunner:
        credential_timeout_seconds = 20
        def _run(self, *args, **kwargs):
            raise TerraformRunnerError("preflight", "command_failed")
    with pytest.raises(TerraformRunnerError):
        verify_network(DeniedRunner(), "aws", {}, GROUP)


def test_missing_security_group_fails_before_any_cli(client, monkeypatch):
    import subprocess
    from app.terraform_runner import TerraformPlanRunner
    from app.worker import process_next_job
    monkeypatch.setenv("CLOUDOPS_EC2_SECURITY_GROUP_ID", "")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("CLI must not run"))
    body = {"name": "missing-group", "environment": "development", "region": "ca-central-1", "instance_type": "t3.micro", "install_nginx": False, "install_docker": False}
    resource = client.post("/api/v1/resources", json=body).json()
    job = client.post(f"/api/v1/resources/{resource['id']}/plan", json={"plan_mode": "aws_ec2_only"}).json()
    assert process_next_job(client.app.state.engine, TerraformPlanRunner())
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "failed"


def test_ec2_only_routes_to_isolated_worker_and_apply_is_blocked(client, tmp_path, monkeypatch, plan):
    import types
    from app import artifact_store, ec2_only_stack
    from app.models import SavedPlanArtifact
    from app.terraform_runner import TerraformPlanRunner
    from app.worker import process_next_job
    from sqlmodel import Session, select
    store = artifact_store.ArtifactStore(tmp_path / "plans")
    monkeypatch.setattr(artifact_store, "ArtifactStore", lambda: store)
    monkeypatch.setattr(ec2_only_stack, "stack_identity", lambda group: ("294665681966", "backend-fingerprint", "configuration-fingerprint"))
    network_calls = []
    monkeypatch.setattr(ec2_only_stack, "verify_network", lambda *args: network_calls.append(True))
    monkeypatch.setenv("CLOUDOPS_EC2_SECURITY_GROUP_ID", GROUP)
    monkeypatch.setattr("shutil.which", lambda name: name)
    commands = []
    def fake_run(self, executable, args, phase, cwd, environment, timeout=None):
        commands.append((phase, cwd))
        if phase == "credentials":
            return types.SimpleNamespace(stdout=json.dumps({"Account": "294665681966", "Arn": "arn:aws:sts::294665681966:assumed-role/cloudops-terraform-deploy/test"}))
        if phase == "plan":
            next(x for x in args if x.startswith("-out="))
            from pathlib import Path
            Path(next(x[5:] for x in args if x.startswith("-out="))).write_bytes(b"isolated synthetic plan")
        return types.SimpleNamespace(stdout=json.dumps(plan) if phase == "show" else "{}")
    monkeypatch.setattr(TerraformPlanRunner, "_run", fake_run)
    body = {"name": "ec2-only-test", "environment": "development", "region": "ca-central-1", "instance_type": "t3.micro", "install_nginx": False, "install_docker": False}
    resource = client.post("/api/v1/resources", json=body).json()
    response = client.post(f"/api/v1/resources/{resource['id']}/plan", json={"plan_mode": "aws_ec2_only"})
    assert response.status_code == 202
    job = response.json()["id"]
    assert process_next_job(client.app.state.engine, TerraformPlanRunner())
    result = client.get(f"/api/v1/jobs/{job}").json()
    assert result["status"] == "plan_ready" and result["plan_mode"] == "aws_ec2_only"
    assert result["plan_summary"]["to_add"] == 1
    assert network_calls and all(cwd == ec2_only_stack.ROOT for _, cwd in commands)
    with Session(client.app.state.engine) as session:
        artifact = session.exec(select(SavedPlanArtifact).where(SavedPlanArtifact.job_id == job)).one()
        assert artifact.backend_identity == "backend-fingerprint"
        assert artifact.summary["plan_mode"] == "aws_ec2_only"
        assert artifact.stack_id == "aws_ec2_only"
        assert artifact.terraform_root == "terraform/aws/ec2-only"
        assert artifact.state_key == "cloudops/development/ec2-only/terraform.tfstate"
        assert artifact.network_identity == ec2_only_stack.network_identity(GROUP)
    approval = client.post(f"/api/v1/jobs/{job}/approvals")
    assert approval.status_code == 201
    with Session(client.app.state.engine) as session:
        artifact = session.exec(select(SavedPlanArtifact).where(SavedPlanArtifact.job_id == job)).one()
        artifact.backend_identity = "legacy-state"
        session.add(artifact)
        session.commit()
    from app.auth import AuthenticatedPrincipal, get_authenticated_principal
    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal("reviewer", frozenset({"approver"}))
    decision = client.post(f"/api/v1/approvals/{approval.json()['id']}/decision", json={"decision": "approved"})
    assert decision.status_code == 409
    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal("https://issuer.example|engineer-user", frozenset({"engineer"}))
    monkeypatch.setenv("CLOUDOPS_ENABLE_AWS_APPLY", "true")
    deployment = client.post(f"/api/v1/jobs/{job}/deploy", json={"confirmation": "DEPLOY APPROVED AWS PLAN", "artifact_digest": "a" * 64, "plan_revision": "b" * 64})
    assert deployment.status_code == 503
