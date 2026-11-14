"""Local Terraform planning jobs, worker transitions, and output safety."""

import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.models import ProvisioningJob
from app.schemas import CreateServerRequest, TerraformPlanSummary
from app.terraform_runner import (
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
    resource="terraform_data.server_configuration",
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


def test_get_missing_job_returns_not_found(client):
    response = client.get(f"/api/v1/jobs/{uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Provisioning job not found."}


def test_worker_claims_job_and_saves_sanitized_plan_summary(client):
    resource = save_request(client)
    queued_job = client.post(f"{RESOURCE_ENDPOINT}/{resource['id']}/plan").json()
    engine = client.app.state.engine

    class SuccessfulRunner:
        def plan(self, _configuration: CreateServerRequest) -> TerraformPlanSummary:
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
        "status": "plan_ready",
        "plan_summary": SUMMARY.model_dump(),
        "error_message": None,
    }


def test_worker_marks_command_failure_with_safe_message(client):
    resource = save_request(client)
    job = client.post(f"{RESOURCE_ENDPOINT}/{resource['id']}/plan").json()

    class FailedRunner:
        def plan(self, configuration: CreateServerRequest) -> TerraformPlanSummary:
            assert configuration.model_dump() == SAMPLE
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

    assert result == SUMMARY
    assert [call[0][1:] for call in calls] == [
        ["init", "-input=false"],
        ["validate"],
        ["plan", "-input=false"],
    ]
    for _, kwargs in calls:
        assert "shell" not in kwargs
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
