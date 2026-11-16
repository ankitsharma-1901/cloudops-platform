"""Shared fixtures: every test uses its own temporary SQLite database."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.auth import AuthenticatedPrincipal, get_authenticated_principal


@pytest.fixture
def database_url(tmp_path):
    return "sqlite:///" + str(tmp_path / "requests.db")


@pytest.fixture
def client(database_url):
    application = create_app(database_url)
    application.dependency_overrides[get_authenticated_principal] = lambda: (
        AuthenticatedPrincipal(
            "https://issuer.example|engineer-user",
            frozenset({"engineer"}),
        )
    )
    with TestClient(application) as test_client:
        yield test_client
