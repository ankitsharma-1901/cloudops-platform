"""Disabled-by-default deployment tests: all cloud/CLI boundaries are mocked."""

import hashlib
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app import artifact_store, aws_execution, deployment_policy, deployment_runner, deployments, terraform_runner as tf
from app.artifact_store import ArtifactError, ArtifactStore
from app.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.models import ApprovalRequest, Deployment, DeploymentAuditEvent, ProvisioningJob, SavedPlanArtifact, ServerRequest, TerraformStateOperation
from app.state_lock import STATE_KEY
from app.worker import process_next_job

OWNER = "https://issuer.example|engineer-user"
REVIEWER = "https://issuer.example|reviewer"
RAW_PLAN = json.dumps({"resource_changes": [{"address": "aws_vpc.dev", "mode": "managed", "change": {"actions": ["create"]}}]})
SAMPLE = dict(name="deploy-sandbox", environment="development", region="ca-central-1", instance_type="t3.micro", install_nginx=True, install_docker=True)


@pytest.fixture
def sandbox(client, tmp_path, monkeypatch):
    # Even a regression cannot reach an actual CLI command from this test module.
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Unmocked CLI invocation is forbidden"))
    monkeypatch.setenv("CLOUDOPS_ENABLE_AWS_APPLY", "false")
    monkeypatch.setenv("CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("CLOUDOPS_AWS_STATE_BUCKET", "cloudops-test-state")
    monkeypatch.setenv("CLOUDOPS_ROLE_ASSIGNMENTS", json.dumps({OWNER: ["engineer"], REVIEWER: ["approver"]}))
    directory = tmp_path / "dev"
    directory.mkdir()
    for name in aws_execution.CONFIG_FILES:
        shutil.copyfile(tf.AWS_DIRECTORY / name, directory / name)
    metadata = directory / ".terraform" / "terraform.tfstate"
    metadata.parent.mkdir()
    metadata.write_text(json.dumps({"backend": {"type": "s3", "config": {
        "bucket": "cloudops-test-state", "key": "cloudops/development/terraform.tfstate",
        "region": "ca-central-1", "allowed_account_ids": ["123456789012"],
        "encrypt": True, "use_lockfile": True,
    }}}))
    monkeypatch.setattr(tf, "AWS_DIRECTORY", directory)
    monkeypatch.setattr(tf, "AWS_BACKEND_METADATA", metadata)
    store = ArtifactStore(tmp_path / "private")
    for module in (artifact_store, deployment_policy, deployment_runner):
        monkeypatch.setattr(module, "ArtifactStore", lambda: store)
    return store


def principal(client, subject, *roles):
    client.app.dependency_overrides[get_authenticated_principal] = lambda: AuthenticatedPrincipal(subject, frozenset(roles))


def saved(client, store):
    resource = client.post("/api/v1/resources", json=SAMPLE).json()
    summary = tf.TerraformPlanRunner._summarize_aws_plan(RAW_PLAN).model_dump()
    identity = aws_execution.execution_identity()
    with Session(client.app.state.engine) as session:
        job = ProvisioningJob(server_request_id=resource["id"], plan_mode="aws_development", status="plan_ready", plan_revision=summary["plan_revision"], plan_summary=summary)
        session.add(job); session.commit(); session.refresh(job)
        artifact = SavedPlanArtifact(job_id=job.id, request_id=resource["id"], digest="", plan_revision=job.plan_revision,
            account_id=identity[0], backend_identity=identity[1], configuration_identity=identity[2], create_only=True,
            summary=summary, expires_at=datetime.now(timezone.utc) + timedelta(minutes=20))
        artifact.digest = store.write(artifact.id, b"synthetic executable plan; never real Terraform")
        session.add(artifact); session.commit(); session.refresh(artifact)
        return resource["id"], job.id, artifact.id


def approved(client, store):
    request_id, job_id, artifact_id = saved(client, store)
    response = client.post(f"/api/v1/jobs/{job_id}/approvals")
    assert response.status_code == 201
    approval = response.json()
    principal(client, REVIEWER, "approver")
    assert client.post(f"/api/v1/approvals/{approval['id']}/decision", json={"decision": "approved"}).status_code == 200
    principal(client, OWNER, "engineer")
    return request_id, job_id, artifact_id, approval


def enable_mock_policy(monkeypatch):
    # Override a function only in this isolated test, never turn on the actual env switch.
    monkeypatch.setattr(deployments, "apply_enabled", lambda: True)
    monkeypatch.setattr(deployment_runner, "apply_enabled", lambda: True)


def body(approval):
    return {"confirmation": "DEPLOY APPROVED AWS PLAN", "artifact_digest": approval["artifact_digest"], "plan_revision": approval["plan_revision"]}


def test_disabled_api_and_direct_runner_cannot_apply(client, sandbox):
    _, job, _, approval = approved(client, sandbox)
    assert client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).status_code == 503
    view = client.get(f"/api/v1/jobs/{job}/deployment").json()
    assert view["enabled"] is False and view["eligible"] is False
    assert "disabled" in view["reason"]
    with pytest.raises(ArtifactError):
        deployment_runner.DeploymentRunner().execute(None)
    with Session(client.app.state.engine) as session:
        assert session.exec(select(Deployment)).all() == []


@pytest.mark.parametrize("phase,code", [("credentials", "credential_preflight_failed"),
    ("identity", "credential_preflight_failed"), ("preflight", "backend_preflight_denied"),
    ("show", "plan_integrity_failed")])
def test_safe_failure_codes_never_persist_exception_text(client, sandbox, monkeypatch, phase, code):
    _, job, _, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval))
    class FailingRunner:
        def prepare(self, *args):
            raise tf.TerraformRunnerError(phase, "SECRET-must-not-survive")
    deployments.process_next_deployment(client.app.state.engine, FailingRunner())
    view = client.get(f"/api/v1/jobs/{job}/deployment").json()
    assert view["deployment"]["reason_code"] == code
    assert code in [event["event_type"] for event in view["events"]]
    assert "SECRET" not in json.dumps(view)


@pytest.mark.parametrize("apply_marker", [False, True])
def test_targeted_recovery_retains_then_explicitly_releases_without_replay(client, sandbox, monkeypatch, apply_marker):
    from app.deployment_recovery import recover_operation, inspect_operations
    from app.models import OperatorRecoveryEvent
    _, job, artifact_id, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    response = client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).json()
    engine = client.app.state.engine
    with Session(engine) as session:
        row = session.get(Deployment, response["id"])
        row.status = "running"
        row.apply_started_at = datetime.now(timezone.utc) if apply_marker else None
        session.add(row); session.commit()
    args = dict(workers_stopped=True, terraform_stopped=True, evidence="INC-14")
    with pytest.raises(ValueError):
        recover_operation(engine, "deployment", response["id"], "mark", **{**args, "terraform_stopped": False})
    recover_operation(engine, "deployment", response["id"], "mark", **args)
    assert inspect_operations(engine)["reservation"]["owner_id"] == response["id"]
    with Session(engine) as session:
        row = session.get(Deployment, response["id"])
        assert row.status == ("needs_operator_review" if apply_marker else "failed")
        assert session.get(SavedPlanArtifact, artifact_id).consumed_at is not None
    with pytest.raises(ValueError):
        recover_operation(engine, "deployment", response["id"], "release", **args)
    recover_operation(engine, "deployment", response["id"], "release", reconciled=True, **args)
    assert inspect_operations(engine)["reservation"] is None
    assert not deployments.process_next_deployment(engine, MockRunner())
    assert client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).status_code == 409
    with Session(engine) as session:
        assert session.get(SavedPlanArtifact, artifact_id).consumed_at is not None
        assert len(session.exec(select(OperatorRecoveryEvent)).all()) == 2


def test_stranded_plan_recovery_never_releases_another_operations_lock(client, sandbox):
    from app.deployment_recovery import recover_operation
    resource = client.post("/api/v1/resources", json=SAMPLE).json()
    engine = client.app.state.engine
    with Session(engine) as session:
        job = ProvisioningJob(server_request_id=resource["id"], plan_mode="aws_development", status="planning")
        session.add(job); session.flush()
        job_id = job.id
        session.add(TerraformStateOperation(key=STATE_KEY, owner_id=job_id, operation="plan")); session.commit()
    args = dict(workers_stopped=True, terraform_stopped=True, evidence="INC-15")
    with pytest.raises(ValueError):
        recover_operation(engine, "plan", job_id, "release", reconciled=True, **args)
    with Session(engine) as session:
        lock = session.get(TerraformStateOperation, STATE_KEY)
        lock.owner_id = "different-operation"
        session.add(lock); session.commit()
    with pytest.raises(ValueError):
        recover_operation(engine, "plan", job_id, "mark", **args)
    with Session(engine) as session:
        lock = session.get(TerraformStateOperation, STATE_KEY)
        assert lock.owner_id == "different-operation"
        lock.owner_id = job_id
        session.add(lock); session.commit()
    recover_operation(engine, "plan", job_id, "mark", **args)
    with Session(engine) as session:
        assert session.get(ProvisioningJob, job_id).status == "failed"
        assert session.get(TerraformStateOperation, STATE_KEY).owner_id == job_id
    with pytest.raises(ValueError):
        recover_operation(engine, "plan", "another-job", "release", reconciled=True, **args)
    recover_operation(engine, "plan", job_id, "release", reconciled=True, **args)
    with Session(engine) as session:
        assert session.get(TerraformStateOperation, STATE_KEY) is None
        assert session.get(ProvisioningJob, job_id).status == "failed"


def test_approval_binds_artifact_and_only_owner_can_queue(client, sandbox, monkeypatch):
    _, job, _, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    principal(client, REVIEWER, "approver")
    assert client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).status_code == 403
    principal(client, "unrelated", "engineer")
    assert client.get(f"/api/v1/jobs/{job}/deployment").status_code == 403
    assert client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).status_code == 403
    principal(client, OWNER, "engineer")
    assert client.get(f"/api/v1/jobs/{job}/deployment").json()["eligible"] is True
    response = client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval))
    assert response.status_code == 202
    assert response.json()["initiator"] == OWNER
    assert response.json()["approver"] == REVIEWER
    assert response.json()["artifact_digest"] == approval["artifact_digest"]
    assert "account_id" not in response.text and "configuration_identity" not in response.text
    assert str(sandbox.root) not in response.text


@pytest.mark.parametrize("fault", ["pending", "rejected", "expired", "approval_expired", "superseded", "digest", "revision", "consumed", "destructive", "self", "file", "backend", "configuration", "account"])
def test_rejects_ineligible_or_tampered_plans(client, sandbox, monkeypatch, fault):
    request_id, job_id, artifact_id, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    with Session(client.app.state.engine) as session:
        artifact = session.get(SavedPlanArtifact, artifact_id)
        row = session.get(ApprovalRequest, approval["id"])
        if fault in {"pending", "rejected"}: row.status = fault
        if fault == "expired": artifact.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if fault == "approval_expired": row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        if fault == "consumed": artifact.consumed_at = datetime.now(timezone.utc)
        if fault == "destructive": artifact.create_only = False
        if fault == "self": row.decided_by = OWNER
        if fault == "digest": row.artifact_digest = "0" * 64
        if fault == "revision": row.plan_revision = "0" * 64
        if fault == "superseded": session.add(ProvisioningJob(server_request_id=request_id))
        session.add(artifact); session.add(row); session.commit()
    if fault == "file": sandbox.path(artifact_id).write_bytes(b"tampered sensitive content")
    if fault == "backend": tf.AWS_BACKEND_METADATA.write_text("{}")
    if fault == "configuration": (tf.AWS_DIRECTORY / "compute.tf").write_text("changed configuration")
    if fault == "account": monkeypatch.setenv(tf.EXPECTED_ACCOUNT_ENV, "999999999999")
    assert client.post(f"/api/v1/jobs/{job_id}/deploy", json=body(approval)).status_code == 409


def test_exact_confirmation_and_digest_are_required(client, sandbox, monkeypatch):
    _, job, _, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    for payload in ({}, {**body(approval), "confirmation": "yes"}, {**body(approval), "path": "../../anything"}):
        assert client.post(f"/api/v1/jobs/{job}/deploy", json=payload).status_code == 422
    assert client.post(f"/api/v1/jobs/{job}/deploy", json={**body(approval), "artifact_digest": "f" * 64}).status_code == 409


def test_concurrent_double_submission_only_queues_once(client, sandbox, monkeypatch):
    _, job, _, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).status_code, range(2)))
    assert sorted(statuses) == [202, 409]


class MockRunner:
    def __init__(self, failure=None): self.executions = 0; self.failure = failure
    def prepare(self, artifact, deployment_id):
        if self.failure == "credentials": raise tf.TerraformRunnerError("identity", "unexpected_identity")
        return object()
    def execute(self, prepared):
        self.executions += 1
        if self.failure: raise TimeoutError("secret raw Terraform diagnostic")
        return {"instance_id": "i-1234567890abcdef0", "secret": "must-not-be-exposed"}


@pytest.mark.parametrize("failure,status,locked", [(None, "succeeded", False), ("credentials", "failed", False), ("timeout", "needs_operator_review", True)])
def test_worker_at_most_once_and_uncertain_outcomes(client, sandbox, monkeypatch, failure, status, locked):
    resource, job, artifact_id, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    assert client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).status_code == 202
    runner = MockRunner(failure)
    assert deployments.process_next_deployment(client.app.state.engine, runner)
    assert not deployments.process_next_deployment(client.app.state.engine, runner)
    view = client.get(f"/api/v1/jobs/{job}/deployment").json()
    assert view["deployment"]["status"] == status
    assert "secret" not in str(view)
    assert runner.executions == (0 if failure == "credentials" else 1)
    with Session(client.app.state.engine) as session:
        assert session.get(SavedPlanArtifact, artifact_id).consumed_at is not None
        assert (session.get(TerraformStateOperation, STATE_KEY) is not None) == locked
    assert client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).status_code == 409
    if locked:
        assert client.post(f"/api/v1/resources/{resource}/plan").status_code == 409


def test_queued_deployment_is_blocked_when_worker_switch_is_disabled(client, sandbox, monkeypatch):
    _, job, _, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    assert client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).status_code == 202
    monkeypatch.setattr(deployments, "apply_enabled", lambda: False)
    runner = MockRunner()
    assert deployments.process_next_deployment(client.app.state.engine, runner)
    assert runner.executions == 0
    assert client.get(f"/api/v1/jobs/{job}/deployment").json()["deployment"]["status"] == "failed"


@pytest.mark.parametrize("mapping", [{}, {OWNER: "engineer", REVIEWER: ["approver"]}, {OWNER: ["engineer"], REVIEWER: ["approver", "invalid"]}])
def test_role_revocation_is_rechecked_by_worker(client, sandbox, monkeypatch, mapping):
    _, job, _, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval))
    monkeypatch.setenv("CLOUDOPS_ROLE_ASSIGNMENTS", json.dumps(mapping))
    runner = MockRunner()
    deployments.process_next_deployment(client.app.state.engine, runner)
    assert runner.executions == 0


def test_interrupted_running_deployment_is_not_retried_and_retains_mutex(client, sandbox, monkeypatch):
    from app.deployment_recovery import recover_operation
    _, job, _, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    response = client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval)).json()
    with Session(client.app.state.engine) as session:
        row = session.get(Deployment, response["id"]); row.status = "running"
        session.add(row); session.commit()
    runner = MockRunner()
    assert not deployments.process_next_deployment(client.app.state.engine, runner)
    recover_operation(client.app.state.engine, "deployment", response["id"], "mark",
                      workers_stopped=True, terraform_stopped=True, evidence="INC-14")
    assert not deployments.process_next_deployment(client.app.state.engine, runner)
    with Session(client.app.state.engine) as session:
        assert session.get(TerraformStateOperation, STATE_KEY) is not None
    assert runner.executions == 0


def test_state_mutex_blocks_other_plans_and_db_blocks_same_request_replan(client, sandbox, monkeypatch):
    resource, job, _, approval = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    client.post(f"/api/v1/jobs/{job}/deploy", json=body(approval))
    assert client.post(f"/api/v1/resources/{resource}/plan").status_code == 409
    other = client.post("/api/v1/resources", json={**SAMPLE, "name": "other-request"}).json()
    assert client.post(f"/api/v1/resources/{other['id']}/plan", json={"plan_mode": "aws_development"}).status_code == 409
    with Session(client.app.state.engine) as session:
        session.add(ProvisioningJob(server_request_id=resource))
        with pytest.raises(IntegrityError): session.commit()


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"], ["update"], ["forget"]])
def test_create_only_policy_rejects_destructive_and_replacement_actions(actions):
    with pytest.raises(ArtifactError):
        aws_execution.require_create_only(json.dumps({"resource_changes": [{"address": "aws_instance.dev", "change": {"actions": actions}}]}))


def test_store_rejects_paths_tampering_hardlinks_and_symlinks(sandbox, tmp_path):
    for value in ("../secrets", "/tmp/arbitrary", "not-a-uuid"):
        with pytest.raises(ArtifactError): sandbox.path(value)
    identifier = str(uuid4())
    digest = sandbox.write(identifier, b"private data")
    assert sandbox.read(identifier, digest) == b"private data"
    with pytest.raises(ArtifactError): sandbox.write(identifier, b"replacement")
    sandbox.path(identifier).write_bytes(b"changed")
    with pytest.raises(ArtifactError): sandbox.read(identifier, digest)
    target = tmp_path / "target"; target.write_bytes(b"linked")
    hardlink = str(uuid4()); os.link(target, sandbox.path(hardlink))
    with pytest.raises(ArtifactError): sandbox.read(hardlink, hashlib.sha256(b"linked").hexdigest())


def test_store_rejects_parent_symlinks(sandbox, tmp_path):
    linked = tmp_path / "linked-root"
    try:
        linked.symlink_to(sandbox.root, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation; reparse guard covered separately")
    with pytest.raises(ArtifactError): ArtifactStore(linked)


def test_reparse_point_guard_is_fail_closed(sandbox, monkeypatch):
    from types import SimpleNamespace
    original = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda path, *args, **kwargs: SimpleNamespace(st_mode=0o40700, st_file_attributes=0x400) if path == sandbox.root else original(path, *args, **kwargs))
    with pytest.raises(ArtifactError): sandbox.path(str(uuid4()))


def test_saved_plan_runner_preserves_binary_and_metadata(client, sandbox, monkeypatch):
    resource = client.post("/api/v1/resources", json=SAMPLE).json()
    queued = client.post(f"/api/v1/resources/{resource['id']}/plan", json={"plan_mode": "aws_development"}).json()
    runner = tf.TerraformPlanRunner()
    monkeypatch.setattr(shutil, "which", lambda name: name)
    def run(executable, args, phase, *rest, **kwargs):
        if phase == "plan":
            Path(next(value[5:] for value in args if value.startswith("-out="))).write_bytes(b"preserved exact binary")
        stdout = (json.dumps({"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/cloudops-terraform-deploy/test"})
                  if phase == "credentials" else RAW_PLAN if phase == "show" else "")
        return subprocess.CompletedProcess([], 0, stdout, "")
    monkeypatch.setattr(runner, "_run", run)
    assert process_next_job(client.app.state.engine, runner)
    with Session(client.app.state.engine) as session:
        artifact = session.exec(select(SavedPlanArtifact).where(SavedPlanArtifact.job_id == queued["id"])).one()
        assert sandbox.read(artifact.id, artifact.digest) == b"preserved exact binary"
        assert artifact.account_id == "123456789012" and artifact.create_only
        assert artifact.request_id == resource["id"]
        assert session.get(TerraformStateOperation, STATE_KEY) is None


def test_execution_uses_exact_plan_and_only_safe_output_ids(client, sandbox, monkeypatch):
    _, _, artifact_id, _ = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: name)
    calls = []
    def run(executable, args, phase, *rest, **kwargs):
        calls.append(args)
        stdout = (json.dumps({"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/cloudops-terraform-deploy/test"}) if phase == "credentials" else RAW_PLAN if phase == "show" else
                  json.dumps({"instance_id": {"value": "i-1234567890abcdef0", "sensitive": False}, "instance_private_ip": {"value": "secret", "sensitive": False}}) if phase == "output" else "")
        return subprocess.CompletedProcess([], 0, stdout, "")
    runner = deployment_runner.DeploymentRunner(); monkeypatch.setattr(runner, "_run", run)
    with Session(client.app.state.engine) as session:
        artifact = session.get(SavedPlanArtifact, artifact_id)
        prepared = runner.prepare(artifact, str(uuid4()))
        assert prepared.path.read_bytes() == sandbox.read(artifact.id, artifact.digest)
        result = runner.execute(prepared)
    assert result == {"instance_id": "i-1234567890abcdef0"}
    apply = next(args for args in calls if args[0] == "apply")
    assert apply == ("apply", "-input=false", "-no-color", "-lock-timeout=5s", str(prepared.path))
    head = next(args for args in calls if args[0] == "s3api")
    assert head[:8] == ("s3api", "list-objects-v2", "--bucket", "cloudops-test-state", "--prefix", "cloudops/development/terraform.tfstate", "--max-keys", "1")
    assert head[head.index("--expected-bucket-owner") + 1] == "123456789012"
    assert not any(args[0] in {"plan", "destroy", "import"} for args in calls)


def test_disabled_flag_parser_is_fail_closed(monkeypatch):
    for value in ("false", "False", "TRUE", "1", "yes", ""):
        monkeypatch.setenv("CLOUDOPS_ENABLE_AWS_APPLY", value)
        assert not aws_execution.apply_enabled()


@pytest.mark.parametrize("field,value", [("bucket", "other-bucket"), ("region", "us-east-1"), ("key", "other/state"),
    ("use_lockfile", False), ("encrypt", False), ("allowed_account_ids", ["999999999999"]),
    ("endpoint", "https://untrusted.example"), ("profile", "administrator"), ("access_key", "not-a-real-key")])
def test_backend_configuration_is_independently_pinned(sandbox, field, value):
    metadata = json.loads(tf.AWS_BACKEND_METADATA.read_text())
    metadata["backend"]["config"][field] = value
    tf.AWS_BACKEND_METADATA.write_text(json.dumps(metadata))
    with pytest.raises(ArtifactError): aws_execution.execution_identity()


def test_invalid_credential_role_is_rejected_before_apply(client, sandbox, monkeypatch):
    _, _, artifact_id, _ = approved(client, sandbox)
    enable_mock_policy(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: name)
    runner = deployment_runner.DeploymentRunner()
    calls = []
    def run(executable, args, phase, *rest, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess([], 0, json.dumps({"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/admin"}), "")
    monkeypatch.setattr(runner, "_run", run)
    with Session(client.app.state.engine) as session, pytest.raises(tf.TerraformRunnerError):
        runner.prepare(session.get(SavedPlanArtifact, artifact_id), str(uuid4()))
    assert len(calls) == 1 and calls[0][0] == "sts"


def test_anonymous_deploy_requests_require_oidc(database_url):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(database_url)) as anonymous:
        assert anonymous.get("/api/v1/jobs/unknown/deployment").status_code == 401
        assert anonymous.post("/api/v1/jobs/unknown/deploy", json={"confirmation": "DEPLOY APPROVED AWS PLAN", "artifact_digest": "a" * 64, "plan_revision": "b" * 64}).status_code == 401


@pytest.mark.parametrize("mode", ["local", "aws_development"])
def test_summary_only_local_and_legacy_plans_are_never_deployable(client, sandbox, monkeypatch, mode):
    resource = client.post("/api/v1/resources", json=SAMPLE).json()
    enable_mock_policy(monkeypatch)
    summary = tf.TerraformPlanRunner._summarize_aws_plan(RAW_PLAN).model_copy(update={"plan_mode": mode}).model_dump()
    with Session(client.app.state.engine) as session:
        job = ProvisioningJob(server_request_id=resource["id"], plan_mode=mode, status="plan_ready", plan_revision=summary["plan_revision"], plan_summary=summary)
        session.add(job); session.commit(); session.refresh(job)
        job_id = job.id
    assert client.get(f"/api/v1/jobs/{job_id}/deployment").json()["eligible"] is False
    assert client.post(f"/api/v1/jobs/{job_id}/deploy", json={"confirmation": "DEPLOY APPROVED AWS PLAN", "artifact_digest": "a" * 64, "plan_revision": summary["plan_revision"]}).status_code == 409
