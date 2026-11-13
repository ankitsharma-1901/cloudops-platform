"""HTTP-level tests for health and server configuration validation."""

from itertools import product

import pytest

ENDPOINT = "/api/v1/resources/validate"
SAMPLE = {
    "name": "web-dev-01",
    "environment": "development",
    "region": "ca-central-1",
    "instance_type": "t3.micro",
    "install_nginx": True,
    "install_docker": True,
}


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_sample_configuration(client):
    response = client.post(ENDPOINT, json=SAMPLE)
    assert response.status_code == 200
    assert response.json() == {"status": "validated", "configuration": SAMPLE}


@pytest.mark.parametrize("name", ["abc", "a01", "a-b", "a--b", "a" * 32])
def test_valid_name_boundaries(client, name):
    payload = {**SAMPLE, "name": name}
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 200
    assert response.json()["configuration"] == payload


@pytest.mark.parametrize(
    "environment,region,instance_type,install_nginx,install_docker",
    list(product(
        ["development", "staging"],
        ["ca-central-1", "us-east-1"],
        ["t3.micro", "t3.small"],
        [True, False],
        [True, False],
    )),
)
def test_allowed_settings(client, environment, region, instance_type,
                          install_nginx, install_docker):
    payload = {
        **SAMPLE,
        "environment": environment,
        "region": region,
        "instance_type": instance_type,
        "install_nginx": install_nginx,
        "install_docker": install_docker,
    }
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "validated", "configuration": payload}


@pytest.mark.parametrize("name", [
    "", "a", "ab", "a" * 33, "1web", "-web", "web-", "Web",
    "web_dev", "web.dev", "web dev", " web", "web ", "web\n", "wéb", 123,
])
def test_invalid_names(client, name):
    response = client.post(ENDPOINT, json={**SAMPLE, "name": name})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "name"]


@pytest.mark.parametrize("field,value", [
    ("environment", "production"),
    ("environment", "Development"),
    ("region", "eu-west-1"),
    ("instance_type", "t3.large"),
])
def test_unsupported_settings(client, field, value):
    response = client.post(ENDPOINT, json={**SAMPLE, field: value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]


@pytest.mark.parametrize("field", ["install_nginx", "install_docker"])
@pytest.mark.parametrize("value", ["true", "false", "yes", "", 0, 1, 0.0, 1.0, [], {}])
def test_booleans_are_strict(client, field, value):
    response = client.post(ENDPOINT, json={**SAMPLE, field: value})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "bool_type"


@pytest.mark.parametrize("field", list(SAMPLE))
def test_every_field_is_required(client, field):
    payload = SAMPLE.copy()
    del payload[field]
    response = client.post(ENDPOINT, json=payload)
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "missing"
    assert response.json()["detail"][0]["loc"] == ["body", field]


@pytest.mark.parametrize("field", list(SAMPLE))
def test_null_fields_are_rejected(client, field):
    response = client.post(ENDPOINT, json={**SAMPLE, field: None})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", field]


def test_extra_fields_are_rejected(client):
    response = client.post(ENDPOINT, json={**SAMPLE, "admin": True})
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "extra_forbidden"
    assert response.json()["detail"][0]["loc"] == ["body", "admin"]


@pytest.mark.parametrize("payload", [None, [], "server", 123])
def test_invalid_request_body(client, payload):
    assert client.post(ENDPOINT, json=payload).status_code == 422


def test_malformed_json(client):
    response = client.post(
        ENDPOINT, content="{invalid", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "json_invalid"
