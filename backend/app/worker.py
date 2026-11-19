"""Separate polling process for fixed-target Terraform plan-only jobs."""

import argparse
import logging
import time
from typing import Protocol, cast

from pydantic import ValidationError
from sqlalchemy import update
from sqlmodel import Session, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.database import create_database_engine, initialize_database
from app.models import ProvisioningJob, ServerRequest
from app.schemas import CreateServerRequest, PlanMode, TerraformPlanSummary
from app.terraform_runner import TerraformPlanRunner, TerraformRunnerError
from app.state_lock import reserve_state, release_state, owns_state
from app.artifact_store import ArtifactError

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 1
SAFE_FAILURE_MESSAGE = "Terraform planning failed. Check the local worker logs."
SAFE_AWS_FAILURE_MESSAGES = {
    "aws_cli_unavailable": "AWS CLI v2 is required for AWS development plans.",
    "backend_not_initialized": (
        "The AWS Terraform backend is not initialized on this worker."
    ),
    "unexpected_backend": (
        "The AWS Terraform backend is not the approved ca-central-1 S3 state path."
    ),
    "expected_account_missing": (
        "The worker's expected AWS account setting is missing or invalid."
    ),
    "credentials_unavailable": (
        "AWS credentials are missing or expired. Refresh the cloudops-deploy "
        "MFA session in the worker terminal and retry."
    ),
    "interaction_required": (
        "AWS authentication needs interactive MFA. Refresh cloudops-deploy "
        "before requesting the plan, then retry."
    ),
    "unexpected_identity": (
        "AWS identity verification failed. The expected account and restricted "
        "deployment role are required."
    ),
    "unrecognized_identity": "AWS returned an identity response that could not be verified.",
}


class PlanRunner(Protocol):
    def plan(
        self,
        configuration: CreateServerRequest,
        plan_mode: PlanMode,
    ) -> TerraformPlanSummary: ...


def process_next_job(engine: Engine, runner: PlanRunner) -> bool:
    """Atomically claim and process at most one queued job."""
    with Session(engine) as session:
        # The durable state mutex is shared with deployment workers, not just plan jobs.
        from app.models import TerraformStateOperation
        from app.state_lock import STATE_KEY
        locked = session.get(TerraformStateOperation, STATE_KEY)
        queued_id = (
            select(ProvisioningJob.id)
            .where(ProvisioningJob.status == "queued")
            .where((~ProvisioningJob.plan_mode.in_(("aws_development", "aws_ec2_only"))) |
                   (ProvisioningJob.id == locked.owner_id if locked else True))
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
        if job_id is not None:
            claimed = session.get(ProvisioningJob, job_id)
            if claimed.plan_mode in ("aws_development", "aws_ec2_only") and not owns_state(session, job_id):
                reserve_state(session, job_id, "plan")
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
        artifact = None
        if job.plan_mode == "aws_ec2_only":
            if not isinstance(runner, TerraformPlanRunner):
                raise ArtifactError()
            planned, artifact = runner.plan_saved_ec2_only(job, resource)
        elif job.plan_mode == "aws_development" and isinstance(runner, TerraformPlanRunner):
            planned, artifact = runner.plan_saved_aws(job, resource)
        else:
            planned = runner.plan(configuration, cast(PlanMode, job.plan_mode))
        plan_summary = TerraformPlanSummary.model_validate(planned)
        if plan_summary.plan_mode != job.plan_mode:
            raise ValueError("Plan mode mismatch")
    except TerraformRunnerError as exc:
        logger.error(
            "Terraform plan job %s failed during %s (%s).",
            job_id,
            exc.phase,
            exc.reason,
        )
        message = (
            SAFE_AWS_FAILURE_MESSAGES.get(exc.reason, SAFE_FAILURE_MESSAGE)
            if job.plan_mode in ("aws_development", "aws_ec2_only")
            else SAFE_FAILURE_MESSAGE
        )
        _finish_failed(engine, job_id, message)
        return True
    except ArtifactError:
        logger.error("Saved AWS plan failed private configuration or artifact validation.")
        message = ("EC2-only plan validation failed. Check the reviewed security group, isolated backend, and plan policy."
                   if job.plan_mode == "aws_ec2_only" else
                   "Saved AWS plan validation failed. Check private account/state-bucket settings, backend locking and artifact directory permissions.")
        _finish_failed(engine, job_id, message)
        return True
    except Exception as exc:
        # Unexpected runner faults must not strand this job or terminate the loop.
        # Log only the exception class; exception text may contain CLI output.
        logger.error("Terraform plan job %s failed (%s).", job_id, type(exc).__name__)
        _finish_failed(engine, job_id, SAFE_FAILURE_MESSAGE)
        return True

    with Session(engine) as session:
        job = session.get(ProvisioningJob, job_id)
        if job is None or job.status != "planning":
            raise RuntimeError("Provisioning job left planning state unexpectedly.")
        job.status = "plan_ready"
        job.plan_summary = plan_summary.model_dump()
        job.plan_revision = plan_summary.plan_revision
        job.error_message = None
        session.add(job)
        if artifact is not None:
            session.add(artifact)
        release_state(session, job_id)
        session.commit()
    return True


def _finish_failed(engine: Engine, job_id: str, message: str) -> None:
    with Session(engine) as session:
        job = session.get(ProvisioningJob, job_id)
        if job is None or job.status != "planning":
            raise RuntimeError("Provisioning job left planning state unexpectedly.")
        job.status = "failed"
        job.plan_summary = None
        job.plan_revision = None
        job.error_message = message
        session.add(job)
        release_state(session, job_id)
        session.commit()


def run_worker(once: bool = False) -> None:
    """Run the local worker until interrupted, or process one job with --once."""
    engine = create_database_engine()
    try:
        initialize_database(engine)
        runner = TerraformPlanRunner()
        logger.info("Started CloudOps worker. AWS deployment remains gated by the server safety switch.")
        while True:
            try:
                from app.deployments import process_next_deployment
                processed = process_next_deployment(engine) or process_next_job(engine, runner)
            except SQLAlchemyError:
                if once:
                    raise
                # Preserve claimed states; never replay a possibly running job.
                logger.error("Worker database operation failed; retrying without exposing database diagnostics.")
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            if not processed:
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
