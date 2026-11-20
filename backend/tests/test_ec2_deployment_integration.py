"""All AWS and Terraform calls are mocked; no real apply can run."""
import io
import json
import zipfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from app import artifact_store, deployment_policy, deployment_runner, deployments, ec2_only_stack
from app.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.models import ApprovalRequest, Deployment, ProvisioningJob, SavedPlanArtifact, ServerRequest
from app.terraform_runner import TerraformPlanRunner, TerraformRunnerError
from app.ec2_only_policy import AMI, SUBNET, VPC

OWNER = "https://issuer.example|engineer-user"
REVIEWER = "reviewer"
GROUP = "sg-0123456789abcdef0"
INSTANCE = "i-0123456789abcdef0"


@pytest.fixture
def scenario(client, tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Real subprocess forbidden"))
    store = artifact_store.ArtifactStore(tmp_path / "plans")
    for module in (artifact_store, deployment_policy, deployment_runner):
        monkeypatch.setattr(module, "ArtifactStore", lambda: store)
    identity = ("294665681966", "ec2-backend", "ec2-config")
    monkeypatch.setattr(ec2_only_stack, "stack_identity", lambda group: identity)
    monkeypatch.setattr("shutil.which", lambda name: name)
    monkeypatch.setenv("CLOUDOPS_EC2_SECURITY_GROUP_ID", GROUP)
    monkeypatch.setenv("CLOUDOPS_ROLE_ASSIGNMENTS", json.dumps({OWNER: ["engineer"], REVIEWER: ["approver"]}))
    monkeypatch.setattr(deployments, "apply_enabled", lambda: True)
    monkeypatch.setattr(deployment_runner, "apply_enabled", lambda: True)
    after = {"ami": AMI, "instance_type": "t3.micro", "subnet_id": SUBNET, "vpc_security_group_ids": [GROUP],
        "associate_public_ip_address": False, "iam_instance_profile": None,
        "tags_all": {"Project": "CloudOps", "Environment": "development", "ManagedBy": "Terraform"},
        "root_block_device": [{"volume_type": "gp3", "volume_size": 8, "encrypted": True, "delete_on_termination": True}],
        "metadata_options": [{"http_tokens": "required"}]}
    plan = json.dumps({"resource_changes": [{"mode": "managed", "address": "aws_instance.dev", "change": {"actions": ["create"], "after": after}}]})
    summary = TerraformPlanRunner._summarize_aws_plan(plan, plan_mode="aws_ec2_only").model_dump()
    binary = io.BytesIO()
    with zipfile.ZipFile(binary, "w") as archive:
        archive.writestr("tfstate", json.dumps({"version": 4, "serial": 0, "lineage": "", "resources": []}))
    with Session(client.app.state.engine) as session:
        resource = ServerRequest(name="isolated-instance", environment="development", region="ca-central-1", instance_type="t3.micro", install_nginx=False, install_docker=False, owner_subject=OWNER)
        session.add(resource); session.commit()
        job = ProvisioningJob(server_request_id=resource.id, plan_mode="aws_ec2_only", status="plan_ready", plan_revision=summary["plan_revision"], plan_summary=summary)
        session.add(job); session.commit()
        artifact = SavedPlanArtifact(job_id=job.id, request_id=resource.id, digest="", plan_revision=job.plan_revision, account_id=identity[0], backend_identity=identity[1], configuration_identity=identity[2], stack_id=ec2_only_stack.STACK, terraform_root=ec2_only_stack.ROOT_ID, state_key=ec2_only_stack.STATE_KEY, network_identity=ec2_only_stack.network_identity(GROUP), summary=summary, create_only=True, expires_at=datetime.now(timezone.utc)+timedelta(minutes=20))
        artifact.digest = store.write(artifact.id, binary.getvalue()); session.add(artifact); session.commit()
        approval = ApprovalRequest(server_request_id=resource.id, provisioning_job_id=job.id, plan_revision=job.plan_revision, status="approved", requested_by=OWNER, decided_by=REVIEWER, decided_at=datetime.now(timezone.utc), expires_at=datetime.now(timezone.utc)+timedelta(minutes=20), artifact_digest=artifact.digest)
        session.add(approval); session.commit()
        job_id, artifact_id = job.id, artifact.id
    applied = {"value": False, "fault": None, "network_checks": 0}
    def network(*args):
        applied["network_checks"] += 1
        if applied["fault"] == "network_denied":
            raise TerraformRunnerError("preflight", "command_failed")
        if applied["fault"] == "expire_before_apply" and applied["network_checks"] == 2:
            from sqlmodel import select
            with Session(client.app.state.engine) as session:
                approval = session.exec(select(ApprovalRequest).where(ApprovalRequest.provisioning_job_id == job_id)).one()
                approval.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                session.add(approval); session.commit()
    monkeypatch.setattr(ec2_only_stack, "verify_network", network)
    state = json.dumps({"version": 4, "serial": 1, "lineage": "lineage", "resources": [{"mode": "managed", "type": "aws_instance", "name": "dev", "instances": [{"attributes": {"id": INSTANCE}}]}]})
    def current(self, aws, environment):
        if applied["fault"] == "stale": return json.dumps({"version": 4, "lineage": "unexpected", "serial": 1})
        return state if applied["value"] else None
    monkeypatch.setattr(deployment_runner.DeploymentRunner, "_ec2_current_state", current)
    def run(self, executable, args, phase, cwd, environment, timeout=None):
        assert cwd == ec2_only_stack.ROOT
        if phase == "credentials": return SimpleNamespace(stdout=json.dumps({"Account": "294665681966", "Arn": "arn:aws:sts::294665681966:assumed-role/cloudops-ec2-only-deploy/test"}))
        if phase == "show": return SimpleNamespace(stdout=plan)
        if phase == "apply":
            if applied["fault"] == "denied": raise TerraformRunnerError("apply", "command_failed")
            applied["value"] = True
        if args[:2] == ("ec2", "describe-instances"):
            instance = {"InstanceId": INSTANCE, "ImageId": AMI, "InstanceType": "t3.micro", "SubnetId": SUBNET, "VpcId": VPC, "SecurityGroups": [{"GroupId": GROUP}], "State": {"Name": "running"}, "Tags": [{"Key": "Project", "Value": "CloudOps"}, {"Key": "Environment", "Value": "development"}, {"Key": "ManagedBy", "Value": "Terraform"}]}
            if applied["fault"] == "partial": instance["InstanceId"] = "i-12345678"
            return SimpleNamespace(stdout=json.dumps({"Reservations": [{"OwnerId": "294665681966", "Instances": [instance]}]}))
        return SimpleNamespace(stdout="{}")
    monkeypatch.setattr(deployment_runner.DeploymentRunner, "_run", run)
    return client, job_id, artifact_id, summary, store, applied


def queue(scenario):
    client, job_id, artifact_id, summary, store, fault = scenario
    with Session(client.app.state.engine) as session:
        artifact = session.get(SavedPlanArtifact, artifact_id)
        body = {"confirmation": "DEPLOY APPROVED AWS PLAN", "artifact_digest": artifact.digest, "plan_revision": summary["plan_revision"]}
    return client.post(f"/api/v1/jobs/{job_id}/deploy", json=body)


def test_exact_approved_ec2_plan_executes_and_verifies(scenario):
    client, job, _, _, _, state = scenario
    queued = queue(scenario)
    assert queued.status_code == 202
    assert deployments.process_next_deployment(client.app.state.engine)
    deployment = client.get(f"/api/v1/jobs/{job}/deployment").json()["deployment"]
    assert deployment["status"] == "succeeded" and deployment["result"]["instance_id"] == INSTANCE
    assert state["value"] is True
    assert queue(scenario).status_code == 409


@pytest.mark.parametrize("fault,expected", [("stale", "failed"), ("network_denied", "failed"), ("denied", "needs_operator_review"), ("partial", "needs_operator_review")])
def test_failures_never_become_success(scenario, fault, expected):
    client, job, _, _, _, state = scenario
    state["fault"] = fault
    assert queue(scenario).status_code == 202
    assert deployments.process_next_deployment(client.app.state.engine)
    deployment = client.get(f"/api/v1/jobs/{job}/deployment").json()["deployment"]
    assert deployment["status"] == expected
    from app.models import TerraformStateOperation
    from app.state_lock import STATE_KEY
    with Session(client.app.state.engine) as session:
        reservation = session.get(TerraformStateOperation, STATE_KEY)
        assert (reservation is not None) == (expected == "needs_operator_review")


def test_queued_deployment_prevents_concurrent_attempt(scenario):
    assert queue(scenario).status_code == 202
    assert queue(scenario).status_code == 409


def test_expired_approval_fails_before_queue(scenario):
    client, job, _, _, _, _ = scenario
    from sqlmodel import select
    with Session(client.app.state.engine) as session:
        approval = session.exec(select(ApprovalRequest).where(ApprovalRequest.provisioning_job_id == job)).one()
        approval.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(approval); session.commit()
    assert queue(scenario).status_code == 409


def test_approval_expiring_at_apply_boundary_never_executes(scenario):
    client, job, _, _, _, state = scenario
    state["fault"] = "expire_before_apply"
    assert queue(scenario).status_code == 202
    assert deployments.process_next_deployment(client.app.state.engine)
    result = client.get(f"/api/v1/jobs/{job}/deployment").json()["deployment"]
    assert result["status"] == "needs_operator_review"
    assert state["value"] is False


def test_wrong_artifact_digest_fails_before_queue(scenario):
    client, job, _, summary, _, _ = scenario
    response = client.post(f"/api/v1/jobs/{job}/deploy", json={"confirmation": "DEPLOY APPROVED AWS PLAN",
        "artifact_digest": "f" * 64, "plan_revision": summary["plan_revision"]})
    assert response.status_code == 409


def test_legacy_artifact_identity_cannot_authorize_ec2_only(scenario):
    client, _, artifact_id, _, _, _ = scenario
    with Session(client.app.state.engine) as session:
        artifact = session.get(SavedPlanArtifact, artifact_id)
        artifact.stack_id = None
        session.add(artifact); session.commit()
    assert queue(scenario).status_code == 409


def test_ec2_artifact_cannot_authorize_legacy_stack(scenario):
    client, job_id, artifact_id, _, _, _ = scenario
    with Session(client.app.state.engine) as session:
        job = session.get(ProvisioningJob, job_id)
        artifact = session.get(SavedPlanArtifact, artifact_id)
        summary = {**job.plan_summary, "plan_mode": "aws_development"}
        job.plan_mode = "aws_development"
        job.plan_summary = summary
        artifact.summary = summary
        session.add(job); session.add(artifact); session.commit()
    assert queue(scenario).status_code == 409


def test_interrupted_ec2_deployment_retains_reservation_and_artifact(scenario):
    from app.deployment_recovery import recover_operation
    from app.models import TerraformStateOperation
    from app.state_lock import STATE_KEY
    client, _, artifact_id, _, _, _ = scenario
    deployment_id = queue(scenario).json()["id"]
    with Session(client.app.state.engine) as session:
        deployment = session.get(Deployment, deployment_id)
        deployment.status = "running"
        deployment.apply_started_at = datetime.now(timezone.utc)
        session.add(deployment); session.commit()
    recover_operation(client.app.state.engine, "deployment", deployment_id, "mark",
                      workers_stopped=True, terraform_stopped=True, evidence="test-incident")
    with Session(client.app.state.engine) as session:
        assert session.get(Deployment, deployment_id).status == "needs_operator_review"
        assert session.get(TerraformStateOperation, STATE_KEY).owner_id == deployment_id
        assert session.get(SavedPlanArtifact, artifact_id).consumed_at is not None
