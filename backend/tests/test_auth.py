"""OIDC token validation and role/ownership authorization tests."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.auth import (
    AuthenticatedPrincipal,
    OIDCSettings,
    get_authenticated_principal,
    validate_access_token,
)
from app.main import create_app

ISSUER = "https://identity.example/realms/cloudops"
AUDIENCE = "cloudops-api"
SUBJECT = "engineer-id"


@pytest.fixture(scope="module")
def key_pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def settings() -> OIDCSettings:
    stable_subject = f"{ISSUER}|{SUBJECT}"
    return OIDCSettings(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url="https://identity.example/jwks",
        algorithms=("RS256",),
        token_types=frozenset({"at+jwt"}),
        role_assignments={stable_subject: frozenset({"engineer"})},
    )


def access_token(private_key, **overrides) -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER,
        "sub": SUBJECT,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    claims.update(overrides.pop("claims", {}))
    headers = {"typ": "at+jwt", "kid": "test-key"}
    headers.update(overrides.pop("headers", {}))
    return jwt.encode(claims, private_key, algorithm="RS256", headers=headers)


class StaticJWKClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, _token):
        return SimpleNamespace(key=self.public_key)


def test_valid_access_token_uses_stable_subject_and_server_roles(key_pair):
    private, public = key_pair
    principal = validate_access_token(
        access_token(private), settings(), StaticJWKClient(public)
    )
    assert principal.subject == f"{ISSUER}|{SUBJECT}"
    assert principal.roles == frozenset({"engineer"})


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://wrong.example"},
        {"aud": "wrong-api"},
        {"exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
    ],
)
def test_wrong_issuer_audience_and_expired_tokens_are_rejected(key_pair, claims):
    private, public = key_pair
    with pytest.raises(HTTPException) as failure:
        validate_access_token(
            access_token(private, claims=claims),
            settings(),
            StaticJWKClient(public),
        )
    assert failure.value.status_code == 401


def test_wrong_token_type_is_rejected_even_with_a_valid_signature(key_pair):
    private, public = key_pair
    token = access_token(private, headers={"typ": "JWT"})
    with pytest.raises(HTTPException) as failure:
        validate_access_token(token, settings(), StaticJWKClient(public))
    assert failure.value.status_code == 401


def test_token_signed_by_an_untrusted_key_is_rejected(key_pair):
    private, _public = key_pair
    other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(HTTPException) as failure:
        validate_access_token(
            access_token(private),
            settings(),
            StaticJWKClient(other_private.public_key()),
        )
    assert failure.value.status_code == 401


def test_authenticated_subject_without_mapping_has_no_roles(key_pair):
    private, public = key_pair
    no_roles = settings()
    no_roles = OIDCSettings(**{**no_roles.__dict__, "role_assignments": {}})
    principal = validate_access_token(
        access_token(private), no_roles, StaticJWKClient(public)
    )
    assert principal.roles == frozenset()


def test_resource_ownership_and_roles_are_enforced(client):
    sample = {
        "name": "owned-server",
        "environment": "development",
        "region": "ca-central-1",
        "instance_type": "t3.micro",
        "install_nginx": True,
        "install_docker": True,
    }
    created = client.post("/api/v1/resources", json=sample)
    assert created.status_code == 202
    resource_id = created.json()["id"]

    client.app.dependency_overrides[get_authenticated_principal] = lambda: (
        AuthenticatedPrincipal("https://issuer.example|other", frozenset({"engineer"}))
    )
    assert client.get("/api/v1/resources").json() == []
    assert client.get(f"/api/v1/resources/{resource_id}").status_code == 403
    assert client.post(f"/api/v1/resources/{resource_id}/plan").status_code == 403

    client.app.dependency_overrides[get_authenticated_principal] = lambda: (
        AuthenticatedPrincipal("https://issuer.example|admin", frozenset({"administrator"}))
    )
    assert client.post("/api/v1/resources", json={**sample, "name": "admin-server"}).status_code == 403


def test_current_user_returns_only_stable_identity_and_server_roles(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json() == {
        "subject": "https://issuer.example|engineer-user",
        "roles": ["engineer"],
    }


def test_missing_oidc_configuration_fails_closed(database_url, monkeypatch):
    for name in ("OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL"):
        monkeypatch.delenv(name, raising=False)
    with TestClient(create_app(database_url)) as client:
        response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer unverified-token"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": "OIDC authentication is not configured."}
