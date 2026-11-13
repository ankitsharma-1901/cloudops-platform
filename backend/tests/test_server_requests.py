"""Persistence, uniqueness, and HTTP tests for saved server requests."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.engine import make_url
from sqlmodel import Session, select

from app.database import create_database_engine, get_session
from app.main import create_app
from app.models import ServerRequest

ENDPOINT = "/api/v1/resources"
SAMPLE = {
    "name": "web-dev-01",
    "environment": "development",
    "region": "ca-central-1",
    "instance_type": "t3.micro",
    "install_nginx": True,
    "install_docker": True,
}


def test_create_saved_request(client):
    before = datetime.now(timezone.utc)
    response = client.post(ENDPOINT, json=SAMPLE)
    assert response.status_code == 202
    saved = response.json()
    assert str(UUID(saved["id"])) == saved["id"]
    assert saved["status"] == "queued"
    assert {field: saved[field] for field in SAMPLE} == SAMPLE
    created_at = datetime.fromisoformat(saved["created_at"])
    assert created_at.utcoffset().total_seconds() == 0
    assert before <= created_at <= datetime.now(timezone.utc)


def test_list_starts_empty(client):
    response = client.get(ENDPOINT)
    assert response.status_code == 200
    assert response.json() == []


def test_list_and_retrieve(client):
    first = client.post(ENDPOINT, json=SAMPLE).json()
    second = client.post(ENDPOINT, json={**SAMPLE, "name": "web-dev-02"}).json()
    assert first["id"] != second["id"]
    response = client.get(ENDPOINT)
    assert response.status_code == 200
    assert response.json() == [first, second]
    response = client.get(f"{ENDPOINT}/{first['id']}")
    assert response.status_code == 200
    assert response.json() == first


@pytest.mark.parametrize("request_id", [str(uuid4()), "unknown"])
def test_missing_request(client, request_id):
    response = client.get(f"{ENDPOINT}/{request_id}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Server request not found."}


def test_duplicate_conflict_and_recovery(client):
    first = client.post(ENDPOINT, json=SAMPLE).json()
    # Changing region does not make the environment/name combination unique.
    response = client.post(ENDPOINT, json={**SAMPLE, "region": "us-east-1"})
    assert response.status_code == 409
    assert client.get(ENDPOINT).json() == [first]
    assert client.post(ENDPOINT, json={**SAMPLE, "name": "another-server"}).status_code == 202


def test_same_name_in_different_environment(client):
    assert client.post(ENDPOINT, json=SAMPLE).status_code == 202
    assert client.post(
        ENDPOINT, json={**SAMPLE, "environment": "staging"}
    ).status_code == 202
    assert len(client.get(ENDPOINT).json()) == 2


@pytest.mark.parametrize("field,value", [
    ("name", "Invalid"), ("name", "ab"), ("name", "web-"),
    ("environment", "production"), ("region", "eu-west-1"),
    ("instance_type", "t3.large"), ("install_nginx", "true"),
    ("install_docker", 1), ("install_docker", None),
    ("status", "running"), ("id", str(uuid4())),
    ("created_at", "2026-01-01T00:00:00Z"),
])
def test_invalid_create_does_not_save(client, field, value):
    response = client.post(ENDPOINT, json={**SAMPLE, field: value})
    assert response.status_code == 422
    assert client.get(ENDPOINT).json() == []


@pytest.mark.parametrize("field", list(SAMPLE))
def test_missing_create_fields(client, field):
    payload = SAMPLE.copy()
    del payload[field]
    assert client.post(ENDPOINT, json=payload).status_code == 422
    assert client.get(ENDPOINT).json() == []


def test_validation_endpoint_does_not_save(client):
    assert client.post(f"{ENDPOINT}/validate", json=SAMPLE).status_code == 200
    assert client.get(ENDPOINT).json() == []


def test_persistence_across_app_restarts(database_url):
    with TestClient(create_app(database_url)) as first_client:
        saved = first_client.post(ENDPOINT, json=SAMPLE).json()
    # A new app, engine, connection and session must still read the committed row.
    with TestClient(create_app(database_url)) as second_client:
        assert second_client.get(f"{ENDPOINT}/{saved['id']}").json() == saved
        assert second_client.get(ENDPOINT).json() == [saved]
        assert second_client.post(ENDPOINT, json=SAMPLE).status_code == 409


def test_database_constraint_across_sessions(client):
    engine = client.app.state.engine
    with Session(engine) as writer:
        resource = ServerRequest(**SAMPLE)
        writer.add(resource)
        writer.commit()
        request_id = resource.id
    with Session(engine) as reader:
        assert reader.get(ServerRequest, request_id).name == SAMPLE["name"]
    # Bypass the API to prove the database itself enforces uniqueness.
    with Session(engine) as another_writer:
        another_writer.add(ServerRequest(**SAMPLE))
        with pytest.raises(IntegrityError):
            another_writer.commit()
        another_writer.rollback()
        assert len(another_writer.exec(select(ServerRequest)).all()) == 1


def test_session_dependency_can_be_overridden(client, tmp_path):
    from sqlmodel import SQLModel

    other_engine = create_database_engine("sqlite:///" + str(tmp_path / "override.db"))
    SQLModel.metadata.create_all(other_engine)

    def override_session():
        with Session(other_engine) as session:
            yield session

    client.app.dependency_overrides[get_session] = override_session
    try:
        assert client.post(ENDPOINT, json=SAMPLE).status_code == 202
        assert len(client.get(ENDPOINT).json()) == 1
    finally:
        client.app.dependency_overrides.clear()
        other_engine.dispose()
    assert client.get(ENDPOINT).json() == []


def test_engine_uses_environment_url(monkeypatch, database_url):
    monkeypatch.setenv("DATABASE_URL", database_url)
    engine = create_database_engine()
    try:
        assert engine.url == make_url(database_url)
    finally:
        engine.dispose()
