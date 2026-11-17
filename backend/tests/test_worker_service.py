"""Continuous worker tests execute no Terraform and use isolated SQLite files."""

import logging

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app import worker
from app.models import ProvisioningJob
from app.schemas import TerraformPlanSummary

SAMPLE = dict(name="service-worker", environment="development", region="ca-central-1",
              instance_type="t3.micro", install_nginx=True, install_docker=True)
SUMMARY = TerraformPlanSummary(plan_revision="a" * 64, to_add=1, to_change=0, to_destroy=0)


def test_continuous_worker_drains_queue_survives_runner_fault_and_polls_again(client, database_url, monkeypatch, caplog):
    resource = client.post("/api/v1/resources", json=SAMPLE).json()
    current = client.post(f"/api/v1/resources/{resource['id']}/plan").json()
    ids = [current["id"]]
    observed = []
    monkeypatch.setenv("DATABASE_URL", database_url)

    class Runner:
        def plan(self, _configuration, _mode):
            response = client.get(f"/api/v1/jobs/{ids[-1]}")
            assert response.status_code == 200
            observed.append(response.json()["status"])
            if len(observed) == 1:
                raise RuntimeError("secret-raw-runner-diagnostic")
            return SUMMARY

    def idle(_seconds):
        status = client.get(f"/api/v1/jobs/{ids[-1]}").json()["status"]
        if len(ids) == 1:
            assert status == "failed"
            ids.append(client.post(f"/api/v1/resources/{resource['id']}/plan").json()["id"])
        else:
            assert status == "plan_ready"
            raise KeyboardInterrupt  # Controlled service shutdown after second job.

    monkeypatch.setattr(worker, "TerraformPlanRunner", Runner)
    monkeypatch.setattr(worker.time, "sleep", idle)
    with caplog.at_level(logging.ERROR), pytest.raises(KeyboardInterrupt):
        worker.run_worker()
    assert observed == ["planning", "planning"]
    assert "secret-raw-runner-diagnostic" not in caplog.text
    first = client.get(f"/api/v1/jobs/{ids[0]}").json()
    assert first["error_message"] == worker.SAFE_FAILURE_MESSAGE
    assert "secret-raw-runner-diagnostic" not in str(first)


def test_once_exits_after_one_job(client, database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    resource = client.post("/api/v1/resources", json=SAMPLE).json()
    job = client.post(f"/api/v1/resources/{resource['id']}/plan").json()
    monkeypatch.setattr(worker, "TerraformPlanRunner", lambda: type("Runner", (), {"plan": lambda self, config, mode: SUMMARY})())
    monkeypatch.setattr(worker.time, "sleep", lambda _: pytest.fail("--once must not sleep"))
    worker.run_worker(once=True)
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "plan_ready"
    worker.run_worker(once=True)  # Empty queue also exits immediately.


def test_continuous_worker_retries_database_failure_without_logging_details(database_url, monkeypatch, caplog):
    monkeypatch.setenv("DATABASE_URL", database_url)
    calls = []

    def process(*_):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("sensitive-sql", {}, Exception("secret-database-error"))
        raise KeyboardInterrupt

    monkeypatch.setattr(worker, "process_next_job", process)
    monkeypatch.setattr(worker.time, "sleep", lambda _: None)
    with caplog.at_level(logging.ERROR), pytest.raises(KeyboardInterrupt):
        worker.run_worker()
    assert len(calls) == 2
    assert "secret-database-error" not in caplog.text
    assert "sensitive-sql" not in caplog.text
