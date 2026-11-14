"""Local CloudOps API for health checks and configuration validation."""

from contextlib import asynccontextmanager
from typing import Annotated
import sqlite3

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from app.database import create_database_engine, get_session
from app.models import ProvisioningJob, ServerRequest
from app.schemas import (
    CreateServerRequest,
    ProvisioningJobResponse,
    ServerRequestResponse,
    ValidationResponse,
)

SessionDependency = Annotated[Session, Depends(get_session)]
router = APIRouter()


def create_app(database_url: str | None = None) -> FastAPI:
    """Build an app with its own engine, allowing isolated test databases."""
    engine = create_database_engine(database_url)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        SQLModel.metadata.create_all(engine)
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(title="CloudOps API", version="0.2.0", lifespan=lifespan)
    application.state.engine = engine
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    application.include_router(router)
    return application


@router.get("/health")
def health() -> dict[str, str]:
    """Report that the API is running."""
    return {"status": "healthy"}


@router.post("/api/v1/resources/validate", response_model=ValidationResponse)
def validate_resource(configuration: CreateServerRequest) -> ValidationResponse:
    """Validate server settings without provisioning any resources."""
    return ValidationResponse(configuration=configuration)


@router.post("/api/v1/resources", response_model=ServerRequestResponse, status_code=202)
def create_resource(configuration: CreateServerRequest, session: SessionDependency):
    """Save an accepted request without starting provisioning."""
    resource = ServerRequest(**configuration.model_dump())
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
def list_resources(session: SessionDependency):
    """Return saved requests in creation order."""
    return session.exec(
        select(ServerRequest).order_by(ServerRequest.created_at, ServerRequest.id)
    ).all()


@router.get("/api/v1/resources/{id}", response_model=ServerRequestResponse)
def get_resource(id: str, session: SessionDependency):
    """Find a saved request by its UUID string."""
    resource = session.get(ServerRequest, id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Server request not found.")
    return resource


@router.post(
    "/api/v1/resources/{resource_id}/plan",
    response_model=ProvisioningJobResponse,
    status_code=202,
)
def enqueue_resource_plan(
    resource_id: str,
    session: SessionDependency,
) -> ProvisioningJob:
    """Queue one plan-only job for a saved request without running Terraform."""
    if session.get(ServerRequest, resource_id) is None:
        raise HTTPException(status_code=404, detail="Server request not found.")

    active_job = session.exec(
        select(ProvisioningJob).where(
            ProvisioningJob.server_request_id == resource_id,
            ProvisioningJob.status.in_(("queued", "planning")),
        )
    ).first()
    if active_job is not None:
        return active_job

    job = ProvisioningJob(server_request_id=resource_id)
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        active_job = session.exec(
            select(ProvisioningJob).where(
                ProvisioningJob.server_request_id == resource_id,
                ProvisioningJob.status.in_(("queued", "planning")),
            )
        ).first()
        if active_job is None:
            raise
        return active_job
    session.refresh(job)
    return job


@router.get(
    "/api/v1/jobs/{job_id}",
    response_model=ProvisioningJobResponse,
)
def get_provisioning_job(
    job_id: str,
    session: SessionDependency,
) -> ProvisioningJob:
    """Return job status and its sanitized plan summary, if ready."""
    job = session.get(ProvisioningJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Provisioning job not found.")
    return job


app = create_app()
