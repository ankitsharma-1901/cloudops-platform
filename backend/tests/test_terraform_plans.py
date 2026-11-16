"""Local Terraform planning jobs, worker transitions, and output safety."""

import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlmodel import create_engine
from sqlmodel import Session, select

from app.database import initialize_database
from app.models import ProvisioningJob
from app.schemas import CreateServerRequest, TerraformPlanSummary
from app.terraform_runner import (
    AWS_DIRECTORY,
    LAB_DIRECTORY,
    TERRAFORM_FILES,
    TerraformPlanRunner,
    TerraformRunnerError,
)
from app.worker import process_next_job

RESOURCE_ENDPOINT = "/api/v1/resources"
SAMPLE = {
    "name": "web-dev-01",
    "environment": "development",
    "region": "ca-central-1",
    "instance_type": "t3.micro",
    "install_nginx": True,
    "install_docker": True,
}
SUMMARY = TerraformPlanSummary(
    plan_mode="local",
    plan_revision="a" * 64,
    resource="terraform_data.server_configuration",
    resource_changes=[
        {"address": "terraform_data.server_configuration", "actions": ["create"]}
    ],
    to_add=1,
    to_change=0,
    to_destroy=0,
)


def save_request(client) -> dict:
    response = client.post(RESOURCE_ENDPOINT, json=SAMPLE)
    assert response.status_code == 202
    return response.json()


def test_plan_job_is_queued_and_duplicate_request_reuses_active_job(client):
    resource = save_request(client)

    first = client.post(f"{RESOURCE_ENDPOINT}/{resource['id']}/plan")
    second = client.post(f"{RESOURCE_ENDPOINT}/{resource['id']}/plan")

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["server_request_id"] == resource["id"]
    assert first.json()["plan_mode"] == "local"
    assert first.json()["status"] == "queued"
    assert first.json()["plan_summary"] is None
    with Session(client.app.state.engine) as session:
        jobs = session.exec(select(ProvisioningJob)).all()
    assert len(jobs) == 1


@pytest.mark.parametrize("resource_id", [str(uuid4()), "invalid-resource-id"])
def test_plan_job_rejects_missing_resource(client, resource_id):
    response = client.post(f"{RESOURCE_ENDPOINT}/{resource_id}/plan")
    assert response.status_code == 404
    assert response.json() == {"detail": "Server request not found."}
    with Session(client.app.state.engine) as session:
        assert session.exec(select(ProvisioningJob)).all() == []


def test_concurrent_plan_requests_create_one_active_job(client):
    resource = save_request(client)
    endpoint = f"{RESOURCE_ENDPOINT}/{resource['id']}/plan"

    with ThreadPoolExecutor(max_workers=4) as executor:
        responses = list(executor.map(lambda _: client.post(endpoint), range(4)))

    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1
    with Session(client.app.state.engine) as session:
        jobs = session.exec(
            select(ProvisioningJob).where(
                ProvisioningJob.server_request_id == resource["id"],
                ProvisioningJob.status.in_(("queued", "planning")),
            )
        ).all()
    assert len(jobs) == 1


def test_aws_plan_mode_is_explicit_and_only_one_can_be_active(client):
    first_resource = save_request(client)
    second_payload = {**SAMPLE, "name": "web-dev-02"}
    second_resource = client.post(RESOURCE_ENDPOINT, json=second_payload).json()

    first = client.post(
        f"{RESOURCE_ENDPOINT}/{first_resource['id']}/plan",
        json={"plan_mode": "aws_development"},
    )
    second = client.post(
        f"{RESOURCE_ENDPOINT}/{second_resource['id']}/plan",
        json={"plan_mode": "aws_development"},
    )

    assert first.status_code == 202
    assert first.json()["plan_mode"] == "aws_development"
    assert second.status_code == 409
    assert second.json() == {
        "detail": "Another AWS development plan is already queued or running."
    }


def test_same_request_cannot_queue_different_plan_modes_together(client):
    resource = save_request(client)
    local = client.post(f"{RESOURCE_ENDPOINT}/{resource['id']}/plan")
    aws = client.post(
        f"{RESOURCE_ENDPOINT}/{resource['id']}/plan",
        json={"plan_mode": "aws_development"},
    )
    assert local.status_code == 202
    assert aws.status_code == 409
    assert aws.json() == {
        "detail": "This server request already has a different active plan mode."
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("environment", "staging"),
        ("region", "us-east-1"),
        ("instance_type", "t3.small"),
    ],
)
def test_aws_plan_rejects_requests_outside_fixed_dev_stack(client, field, value):
    resource = client.post(
        RESOURCE_ENDPOINT,
        json={**SAMPLE, field: value},
    ).json()

    response = client.post(
        f"{RESOURCE_ENDPOINT}/{resource['id']}/plan",
        json={"plan_mode": "aws_development"},
    )

    assert response.status_code == 409
    assert "development request" in response.json()["detail"]


def test_plan_mode_rejects_unknown_values(client):
    resource = save_request(client)
    response = client.post(
        f"{RESOURCE_ENDPOINT}/{resource['id']}/plan",
        json={"plan_mode": "apply"},
    )
    assert response.status_code == 422


def test_existing_sqlite_job_table_gets_additive_plan_mode_migration(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE provisioning_jobs ("
                "id VARCHAR PRIMARY KEY, server_request_id VARCHAR NOT NULL, "
                "status VARCHAR NOT NULL, plan_summary JSON, "
                "error_message VARCHAR, created_at DATETIME NOT NULL)"
            )
        )

    initialize_database(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("provisioning_jobs")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("provisioning_jobs")}
    assert "plan_mode" in columns
    assert "uq_provisioning_job_active_aws" in indexes
    engine.dispose()


def test_get_missing_job_returns_not_found(client):
    response = client.get(f"/api/v1/jobs/{uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Provisioning job not found."}


def test_worker_claims_job_and_saves_sanitized_plan_summary(client):
    resource = save_request(client)
    queued_job = client.post(f"{RESOURCE_ENDPOINT}/{resource['id']}/plan").json()
    engine = client.app.state.engine

    class SuccessfulRunner:
        def plan(
            self,
            _configuration: CreateServerRequest,
            plan_mode: str,
        ) -> TerraformPlanSummary:
            assert plan_mode == "local"
            assert _configuration.model_dump() == SAMPLE
            with Session(engine) as session:
                claimed_job = session.get(ProvisioningJob, queued_job["id"])
                assert claimed_job.status == "planning"
            return SUMMARY

    assert process_next_job(engine, SuccessfulRunner()) is True
    response = client.get(f"/api/v1/jobs/{queued_job['id']}")
    assert response.status_code == 200
    assert response.json() == {
        "id": queued_job["id"],
        "server_request_id": resource["id"],
        "plan_mode": "local",
        "status": "plan_ready",
        "plan_summary": SUMMARY.model_dump(),
            "plan_revision": SUMMARY.plan_revision,
        "error_message": None,
    }


def test_worker_marks_command_failure_with_safe_message(client):
    resource = save_request(client)
    job = client.post(f"{RESOURCE_ENDPOINT}/{resource['id']}/plan").json()

    class FailedRunner:
        def plan(
            self,
            configuration: CreateServerRequest,
            plan_mode: str,
        ) -> TerraformPlanSummary:
            assert configuration.model_dump() == SAMPLE
            assert plan_mode == "local"
            raise TerraformRunnerError("plan", "command_failed")

    assert process_next_job(client.app.state.engine, FailedRunner()) is True
    response = client.get(f"/api/v1/jobs/{job['id']}")
    assert response.json()["status"] == "failed"
    assert response.json()["error_message"] == (
        "Terraform planning failed. Check the local worker logs."
    )
    assert response.json()["plan_summary"] is None


def test_runner_uses_only_fixed_commands_in_an_isolated_work_directory(
    monkeypatch,
):
    calls = []
    outputs = [
        "Terraform has been successfully initialized!",
        "Success! The configuration is valid.",
        "\x1b[1mPlan:\x1b[0m \x1b[0m1 to add, 0 to change, 0 to destroy.",
    ]
    monkeypatch.setattr(
        "app.terraform_runner.shutil.which",
        lambda _: "C:\\Terraform\\terraform.exe",
    )
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-be-passed")
    monkeypatch.setenv("TF_CLI_ARGS_plan", "-destroy")

    def fake_run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        work_directory = Path(kwargs["cwd"])
        assert work_directory != LAB_DIRECTORY
        assert {path.name for path in work_directory.glob("*.tf")} == set(
            TERRAFORM_FILES
        )
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=outputs[len(calls) - 1],
            stderr="",
        )

    monkeypatch.setattr("app.terraform_runner.subprocess.run", fake_run)
    result = TerraformPlanRunner().plan(CreateServerRequest(**SAMPLE))

    assert result.model_dump(exclude={"plan_revision"}) == SUMMARY.model_dump(
        exclude={"plan_revision"}
    )
    assert re.fullmatch(r"[0-9a-f]{64}", result.plan_revision)
    assert [call[0][1:] for call in calls] == [
        ["init", "-input=false"],
        ["validate"],
        ["plan", "-input=false"],
    ]
    for _, kwargs in calls:
        assert "shell" not in kwargs
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["timeout"] == TerraformPlanRunner.command_timeout_seconds
        assert "AWS_SECRET_ACCESS_KEY" not in kwargs["env"]
        assert "TF_CLI_ARGS_plan" not in kwargs["env"]
        assert kwargs["env"]["TF_VAR_name"] == SAMPLE["name"]
        assert kwargs["env"]["TF_VAR_environment"] == SAMPLE["environment"]
        assert kwargs["env"]["TF_VAR_region"] == SAMPLE["region"]
        assert kwargs["env"]["TF_VAR_instance_type"] == SAMPLE["instance_type"]
        assert kwargs["env"]["TF_VAR_install_nginx"] == "true"
        assert kwargs["env"]["TF_VAR_install_docker"] == "true"


def test_runner_command_error_does_not_expose_raw_terraform_output(monkeypatch):
    monkeypatch.setattr(
        "app.terraform_runner.shutil.which",
        lambda _: "terraform.exe",
    )

    def failed_run(arguments, **kwargs):
        assert kwargs["timeout"] == TerraformPlanRunner.command_timeout_seconds
        return subprocess.CompletedProcess(
            arguments,
            1,
            stdout="private plan contents",
            stderr="credential-like diagnostic",
        )

    monkeypatch.setattr("app.terraform_runner.subprocess.run", failed_run)
    with pytest.raises(TerraformRunnerError) as raised:
        TerraformPlanRunner().plan(CreateServerRequest(**SAMPLE))

    assert "private plan contents" not in str(raised.value)
    assert "credential-like diagnostic" not in str(raised.value)
    assert raised.value.phase == "init"


def test_runner_timeout_does_not_expose_captured_output(monkeypatch):
    monkeypatch.setattr(
        "app.terraform_runner.shutil.which",
        lambda _: "terraform.exe",
    )

    def timeout_run(arguments, **kwargs):
        assert kwargs["timeout"] == TerraformPlanRunner.command_timeout_seconds
        raise subprocess.TimeoutExpired(arguments, 60, output="private contents")

    monkeypatch.setattr("app.terraform_runner.subprocess.run", timeout_run)
    with pytest.raises(TerraformRunnerError) as raised:
        TerraformPlanRunner().plan(CreateServerRequest(**SAMPLE))

    assert raised.value.reason == "timed_out"
    assert "private contents" not in str(raised.value)


def test_aws_runner_verifies_identity_and_uses_only_fixed_plan_commands(
    monkeypatch,
    tmp_path,
):
    backend_metadata = tmp_path / "terraform.tfstate"
    backend_metadata.write_text(
        json.dumps(
            {
                "backend": {
                    "type": "s3",
                    "config": {
                        "region": "ca-central-1",
                        "key": "cloudops/development/terraform.tfstate",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.terraform_runner.AWS_BACKEND_METADATA", backend_metadata)
    monkeypatch.setenv("CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-be-passed")
    monkeypatch.setenv("TF_CLI_ARGS_plan", "-destroy")
    monkeypatch.setattr(
        "app.terraform_runner.shutil.which",
        lambda executable: f"C:\\Tools\\{executable}.exe",
    )
    plan_json = json.dumps(
        {
            "resource_changes": [
                {
                    "address": "aws_vpc.main",
                    "change": {"actions": ["create"]},
                },
                {
                    "address": "aws_instance.dev",
                    "change": {"actions": ["delete", "create"]},
                },
                {
                    "address": "data.aws_ssm_parameter.al2023",
                    "change": {"actions": ["read"]},
                },
            ]
        }
    )
    outputs = [
        json.dumps(
            {
                "Account": "123456789012",
                "Arn": (
                    "arn:aws:sts::123456789012:assumed-role/"
                    "cloudops-terraform-deploy/cloudops-dev-deployment"
                ),
            }
        ),
        "Success!",
        "Saved the plan",
        plan_json,
    ]
    calls = []

    def fake_run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=outputs[len(calls) - 1],
            stderr="",
        )

    monkeypatch.setattr("app.terraform_runner.subprocess.run", fake_run)
    result = TerraformPlanRunner().plan(
        CreateServerRequest(**SAMPLE),
        "aws_development",
    )

    assert result.plan_mode == "aws_development"
    assert result.to_add == 2
    assert result.to_change == 0
    assert result.to_destroy == 1
    assert [change.address for change in result.resource_changes] == [
        "aws_vpc.main",
        "aws_instance.dev",
    ]
    commands = [call[0][1:] for call in calls]
    assert commands[0][:3] == ["sts", "get-caller-identity", "--profile"]
    assert commands[1] == ["validate", "-no-color"]
    assert commands[2][:4] == [
        "plan",
        "-input=false",
        "-no-color",
        "-lock-timeout=5s",
    ]
    assert commands[3][0:2] == ["show", "-json"]
    assert not any(
        forbidden in command
        for command in commands
        for forbidden in ("apply", "destroy", "import", "state")
    )
    for _, kwargs in calls:
        assert kwargs["cwd"] == AWS_DIRECTORY
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["env"]["AWS_PROFILE"] == "cloudops-deploy"
        assert kwargs["env"]["AWS_REGION"] == "ca-central-1"
        assert kwargs["env"]["TF_VAR_expected_account_id"] == "123456789012"
        assert "AWS_SECRET_ACCESS_KEY" not in kwargs["env"]
        assert "TF_CLI_ARGS_plan" not in kwargs["env"]


def test_aws_runner_rejects_unexpected_role_without_running_terraform(
    monkeypatch,
    tmp_path,
):
    backend_metadata = tmp_path / "terraform.tfstate"
    backend_metadata.write_text(
        json.dumps(
            {
                "backend": {
                    "type": "s3",
                    "config": {
                        "region": "ca-central-1",
                        "key": "cloudops/development/terraform.tfstate",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.terraform_runner.AWS_BACKEND_METADATA", backend_metadata)
    monkeypatch.setenv("CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID", "123456789012")
    monkeypatch.setattr(
        "app.terraform_runner.shutil.which",
        lambda executable: f"{executable}.exe",
    )
    calls = []

    def fake_run(arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            0,
            stdout=json.dumps(
                {
                    "Account": "123456789012",
                    "Arn": "arn:aws:iam::123456789012:user/cloudops-dev",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("app.terraform_runner.subprocess.run", fake_run)
    with pytest.raises(TerraformRunnerError) as raised:
        TerraformPlanRunner().plan(
            CreateServerRequest(**SAMPLE),
            "aws_development",
        )

    assert raised.value.reason == "unexpected_identity"
    assert len(calls) == 1


def test_aws_runner_rejects_non_s3_backend_before_credentials(monkeypatch, tmp_path):
    backend_metadata = tmp_path / "terraform.tfstate"
    backend_metadata.write_text(
        json.dumps({"backend": {"type": "local", "config": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.terraform_runner.AWS_BACKEND_METADATA", backend_metadata)
    monkeypatch.setattr(
        "app.terraform_runner.shutil.which",
        lambda executable: f"{executable}.exe",
    )
    monkeypatch.setattr(
        "app.terraform_runner.subprocess.run",
        lambda *args, **kwargs: pytest.fail("subprocess must not run"),
    )

    with pytest.raises(TerraformRunnerError) as raised:
        TerraformPlanRunner().plan(CreateServerRequest(**SAMPLE), "aws_development")

    assert raised.value.reason == "unexpected_backend"


def test_worker_returns_safe_actionable_aws_credential_error(client):
    resource = save_request(client)
    job = client.post(
        f"{RESOURCE_ENDPOINT}/{resource['id']}/plan",
        json={"plan_mode": "aws_development"},
    ).json()

    class FailedAwsRunner:
        def plan(self, configuration, plan_mode):
            assert plan_mode == "aws_development"
            raise TerraformRunnerError("credentials", "interaction_required")

    assert process_next_job(client.app.state.engine, FailedAwsRunner()) is True
    response = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert response["status"] == "failed"
    assert "interactive MFA" in response["error_message"]
    assert response["plan_summary"] is None
