"""Only approved local frontend origins may use browser API access."""

import pytest


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:3000"])
def test_allowed_preflight(client, origin):
    response = client.options("/api/v1/resources", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in response.headers


def test_unapproved_origin(client):
    response = client.options("/api/v1/resources", headers={
        "Origin": "https://unapproved.example",
        "Access-Control-Request-Method": "POST",
    })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_actual_response_has_cors_headers(client):
    response = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_unapproved_method(client):
    response = client.options("/api/v1/resources", headers={
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "DELETE",
    })
    assert response.status_code == 400
