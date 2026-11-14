"""Separate polling process for local Terraform plan-only jobs."""

import argparse
import logging
import time
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy import update
from sqlmodel import Session, SQLModel, select
from sqlalchemy.engine import Engine

from app.database import create_database_engine
from app.models import ProvisioningJob, ServerRequest
from app.schemas import CreateServerRequest, TerraformPlanSummary
from app.terraform_runner import TerraformPlanRunner, TerraformRunnerError

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 1
SAFE_FAILURE_MESSAGE = "Terraform planning failed. Check the local worker logs."


class PlanRunner(Protocol):
    def plan(self, configuration: CreateServerRequest) -> TerraformPlanSummary: ...


def process_next_job(engine: Engine, runner: PlanRunner) -> bool:
    """Atomically claim and process at most one queued job."""
    with Session(engine) as session:
        queued_id = (
            select(ProvisioningJob.id)
            .where(ProvisioningJob.status == "queued")
            .order_by(ProvisioningJob.created_at, ProvisioningJob.id)
            .limit(1)
            .scalar_subquery()
        )
        result = session.exec(
            update(ProvisioningJob)
            .where(
                ProvisioningJob.id == queued_id,
                ProvisioningJob.status == "queued",
            )
            .values(status="planning")
            .returning(ProvisioningJob.id)
        )
        job_id = result.scalar_one_or_none()
        session.commit()

    if job_id is None:
        return False

    with Session(engine) as session:
        job = session.get(ProvisioningJob, job_id)
        if job is None:
            raise RuntimeError("Claimed provisioning job disappeared.")
        resource = session.get(ServerRequest, job.server_request_id)
        request_values = (
            {
                field: getattr(resource, field)
                for field in CreateServerRequest.model_fields
            }
            if resource is not None
            else None
        )

    if request_values is None:
        _finish_failed(engine, job_id, "The saved server request is missing.")
        return True

    try:
        configuration = CreateServerRequest.model_validate(request_values)
    except ValidationError:
        _finish_failed(engine, job_id, "The saved server request is invalid.")
        return True

    try:
        plan_summary = runner.plan(configuration)
    except TerraformRunnerError as exc:
        logger.error(
            "Terraform plan job %s failed during %s (%s).",
            job_id,
            exc.phase,
            exc.reason,
        )
        _finish_failed(engine, job_id, SAFE_FAILURE_MESSAGE)
        return True

    with Session(engine) as session:
        job = session.get(ProvisioningJob, job_id)
        if job is None or job.status != "planning":
            raise RuntimeError("Provisioning job left planning state unexpectedly.")
        job.status = "plan_ready"
        job.plan_summary = plan_summary.model_dump()
        job.error_message = None
        session.add(job)
        session.commit()
    return True


def _finish_failed(engine: Engine, job_id: str, message: str) -> None:
    with Session(engine) as session:
        job = session.get(ProvisioningJob, job_id)
        if job is None or job.status != "planning":
            raise RuntimeError("Provisioning job left planning state unexpectedly.")
        job.status = "failed"
        job.plan_summary = None
        job.error_message = message
        session.add(job)
        session.commit()


def run_worker(once: bool = False) -> None:
    """Run the local worker until interrupted, or process one job with --once."""
    engine = create_database_engine()
    try:
        SQLModel.metadata.create_all(engine)
        runner = TerraformPlanRunner()
        logger.info("Started local Terraform plan-only worker.")
        while True:
            if not process_next_job(engine, runner):
                if once:
                    return
                time.sleep(POLL_INTERVAL_SECONDS)
            elif once:
                return
    finally:
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Terraform plan worker.")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one queued job and exit.",
    )
    arguments = parser.parse_args()
    run_worker(once=arguments.once)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
