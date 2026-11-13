"""Configurable database engine and one session per API request."""

import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine
from sqlmodel import Session, create_engine

DEFAULT_DATABASE_URL = "sqlite:///" + str(
    Path(__file__).resolve().parents[1] / "cloudops.db"
)


def create_database_engine(database_url: str | None = None) -> Engine:
    """Explicit URLs override the environment and local development default."""
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite:") else {}
    return create_engine(url, connect_args=connect_args)


def get_session(request: Request) -> Iterator[Session]:
    """Yield a session and always close it after the request completes."""
    with Session(request.app.state.engine) as session:
        yield session
