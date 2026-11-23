"""Local CloudOps API for health checks and configuration validation."""

from contextlib import asynccontextmanager
from typing import Annotated
import sqlite3

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.database import create_database_engine, get_session, initialize_database
from app.approvals import router as approval_router
from app.history import router as history_router
from app.deployments import router as deployment_router
from app.infrastructure import router as infrastructure_router
from app.ec2_demo import router as ec2_demo_router
from app.ec2_requests import router as ec2_requests_router
from app.state_lock import reserve_state
from app.auth import (
    APPROVER_ROLE,
    ENGINEER_ROLE,
    AuthenticatedPrincipal,
    get_authenticated_principal,
    require_role,
)
from app.models import ApprovalRequest, ProvisioningJob, ServerRequest, Deployment
from app.schemas import (
    CreateServerRequest,
    ProvisioningJobResponse,
    ServerRequestResponse,
    TerraformPlanRequest,
    ValidationResponse,
    CurrentUserResponse,
)

SessionDependency = Annotated[Session, Depends(get_session)]
PrincipalDependency = Annotated[
    AuthenticatedPrincipal, Depends(get_authenticated_principal)
]
router = APIRouter()


def create_app(database_url: str | None = None) -> FastAPI:
    """Build an app with its own engine, allowing isolated test databases."""
    engine = create_database_engine(database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        initialize_database(engine)
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(title="CloudOps API", version="0.3.0", lifespan=lifespan)
    application.state.engine = engine
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.include_router(router)
    application.include_router(approval_router)
    application.include_router(history_router)
    application.include_router(deployment_router)
    application.include_router(infrastructure_router)
    application.include_router(ec2_demo_router)
    application.include_router(ec2_requests_router)
    return application


@router.get("/health")
def health() -> dict[str, str]:
    """Report that the API is running."""
    return {"status": "healthy"}


@router.get("/api/v1/auth/me", response_model=CurrentUserResponse)
def current_user(principal: PrincipalDependency) -> CurrentUserResponse:
    return CurrentUserResponse(subject=principal.subject, roles=sorted(principal.roles))


@router.post("/api/v1/resources/validate", response_model=ValidationResponse)
def validate_resource(
    configuration: CreateServerRequest,
    principal: PrincipalDependency,
) -> ValidationResponse:
    """Validate server settings without provisioning any resources."""
    require_role(principal, ENGINEER_ROLE)
    return ValidationResponse(configuration=configuration)


@router.post("/api/v1/resources", response_model=ServerRequestResponse, status_code=202)
def create_resource(
    configuration: CreateServerRequest,
    session: SessionDependency,
    principal: PrincipalDependency,
):
    """Save an accepted request without starting provisioning."""
    require_role(principal, ENGINEER_ROLE)
    resource = ServerRequest(
        **configuration.model_dump(), owner_subject=principal.subject
    )
    session.add(resource)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        # Only translate the known SQLite uniqueness violation into a conflict.
        if (isinstance(exc.orig, sqlite3.IntegrityError)
                and getattr(exc.orig, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
                and "server_requests.environment, server_requests.name" in str(exc.orig)):
            raise HTTPException(
                status_code=409,
                detail="A server request with this name already exists in this environment.",
            ) from exc
        raise
    session.refresh(resource)
    return resource


@router.get("/api/v1/resources", response_model=list[ServerRequestResponse])
def list_resources(session: SessionDependency, principal: PrincipalDependency):
    """Return saved requests in creation order."""
    require_role(principal, ENGINEER_ROLE)
    return session.exec(
        select(ServerRequest)
        .where(ServerRequest.owner_subject == principal.subject)
        .order_by(ServerRequest.created_at, ServerRequest.id)
    ).all()


@router.get("/api/v1/resources/{id}", response_model=ServerRequestResponse)
def get_resource(
    id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
):
    """Find a saved request by its UUID string."""
    resource = session.get(ServerRequest, id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Server request not found.")
    if resource.owner_subject == principal.subject and ENGINEER_ROLE in principal.roles:
        return resource
    if APPROVER_ROLE in principal.roles:
        permitted = session.exec(
            select(ApprovalRequest.id)
            .join(ProvisioningJob)
            .where(ProvisioningJob.server_request_id == resource.id)
        ).first()
        if permitted is not None:
            return resource
    raise HTTPException(status_code=403, detail="Server request access denied.")


@router.post(
    "/api/v1/resources/{resource_id}/plan",
    response_model=ProvisioningJobResponse,
    status_code=202,
)
def enqueue_resource_plan(
    resource_id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
    plan_request: TerraformPlanRequest = TerraformPlanRequest(),
) -> ProvisioningJob:
    """Queue one plan-only job for a saved request without running Terraform."""
    resource = session.get(ServerRequest, resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Server request not found.")
    require_role(principal, ENGINEER_ROLE)
    if resource.owner_subject != principal.subject:
        raise HTTPException(status_code=403, detail="Server request access denied.")

    if session.exec(select(Deployment.id).where(
        Deployment.request_id == resource.id,
        Deployment.status.in_(("queued", "running", "needs_operator_review")),
    )).first():
        raise HTTPException(409, "A deployment is active or requires operator reconciliation for this request.")

    if plan_request.plan_mode in ("aws_development", "aws_ec2_only") and (
        resource.environment != "development"
        or resource.region != "ca-central-1"
        or resource.instance_type != "t3.micro"
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "AWS development planning requires a development request in "
                "ca-central-1 using t3.micro."
            ),
        )

    active_job = session.exec(
        select(ProvisioningJob).where(
            ProvisioningJob.server_request_id == resource_id,
            ProvisioningJob.status.in_(("queued", "planning")),
        )
    ).first()
    if active_job is not None:
        if active_job.plan_mode == plan_request.plan_mode:
            return active_job
        raise HTTPException(
            status_code=409,
            detail="This server request already has a different active plan mode.",
        )

    if plan_request.plan_mode in ("aws_development", "aws_ec2_only"):
        active_aws_job = session.exec(
            select(ProvisioningJob).where(
                ProvisioningJob.plan_mode.in_(("aws_development", "aws_ec2_only")),
                ProvisioningJob.status.in_(("queued", "planning")),
            )
        ).first()
        if active_aws_job is not None:
            raise HTTPException(
                status_code=409,
                detail="Another AWS development plan is already queued or running.",
            )

    job = ProvisioningJob(
        server_request_id=resource_id,
        plan_mode=plan_request.plan_mode,
    )
    session.add(job)
    try:
        if plan_request.plan_mode in ("aws_development", "aws_ec2_only"):
            reserve_state(session, job.id, "plan")
        session.commit()
    except IntegrityError:
        session.rollback()
        active_job = session.exec(
            select(ProvisioningJob).where(
                ProvisioningJob.server_request_id == resource_id,
                ProvisioningJob.status.in_(("queued", "planning")),
            )
        ).first()
        if active_job is not None:
            if active_job.plan_mode == plan_request.plan_mode:
                return active_job
            raise HTTPException(
                status_code=409,
                detail="This server request already has a different active plan mode.",
            )
        if plan_request.plan_mode in ("aws_development", "aws_ec2_only"):
            active_aws_job = session.exec(
                select(ProvisioningJob).where(
                    ProvisioningJob.plan_mode.in_(("aws_development", "aws_ec2_only")),
                    ProvisioningJob.status.in_(("queued", "planning")),
                )
            ).first()
            if active_aws_job is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Another AWS development plan is already queued or running.",
                )
        if plan_request.plan_mode in ("aws_development", "aws_ec2_only") or session.exec(select(Deployment.id).where(
            Deployment.request_id == resource_id, Deployment.status.in_(("queued", "running", "needs_operator_review")),
        )).first():
            raise HTTPException(status_code=409, detail="The AWS development state is reserved by another operation or requires operator reconciliation.") from None
        raise
    session.refresh(job)
    return job


@router.get(
    "/api/v1/jobs/{job_id}",
    response_model=ProvisioningJobResponse,
)
def get_provisioning_job(
    job_id: str,
    session: SessionDependency,
    principal: PrincipalDependency,
) -> ProvisioningJob:
    """Return job status and its sanitized plan summary, if ready."""
    job = session.get(ProvisioningJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Provisioning job not found.")
    resource = session.get(ServerRequest, job.server_request_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Server request not found.")
    if resource.owner_subject == principal.subject and ENGINEER_ROLE in principal.roles:
        return job
    if APPROVER_ROLE in principal.roles:
        approval = session.exec(
            select(ApprovalRequest.id).where(
                ApprovalRequest.provisioning_job_id == job.id
            )
        ).first()
        if approval is not None:
            return job
    raise HTTPException(status_code=403, detail="Provisioning job access denied.")


app = create_app()
