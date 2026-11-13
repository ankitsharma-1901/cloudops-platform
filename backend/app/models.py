"""Database tables, separate from the public request validation schema."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ServerRequest(SQLModel, table=True):
    """A saved server request; queued means recorded, not provisioned."""

    __tablename__ = "server_requests"
    __table_args__ = (
        UniqueConstraint("environment", "name", name="uq_server_environment_name"),
    )

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str
    environment: str
    region: str
    instance_type: str
    install_nginx: bool
    install_docker: bool
    status: str = Field(default="queued")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
