"""Shared eligibility rules for approval of the current, intact plan result."""

from pydantic import ValidationError
from sqlmodel import Session, select

from app.models import ProvisioningJob
from app.schemas import TerraformPlanSummary


def latest_job_query(resource_id):
    return (select(ProvisioningJob.id)
            .where(ProvisioningJob.server_request_id == resource_id)
            .order_by(ProvisioningJob.created_at.desc(), ProvisioningJob.id.desc())
            .limit(1))


def is_current_plan(session: Session, job: ProvisioningJob) -> bool:
    if job.status != "plan_ready" or not job.plan_revision:
        return False
    try:
        summary = TerraformPlanSummary.model_validate(job.plan_summary)
    except ValidationError:
        return False
    return (
        summary.plan_revision == job.plan_revision
        and summary.plan_mode == job.plan_mode
        and session.exec(latest_job_query(job.server_request_id)).first() == job.id
    )
