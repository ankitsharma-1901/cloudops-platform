"""Shared fixtures: every test uses its own temporary SQLite database."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def database_url(tmp_path):
    return "sqlite:///" + str(tmp_path / "requests.db")


@pytest.fixture
def client(database_url):
    with TestClient(create_app(database_url)) as test_client:
        yield test_client
