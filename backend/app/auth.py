"""OIDC access-token validation and server-controlled role mapping."""

import json
import os
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

ENGINEER_ROLE = "engineer"
APPROVER_ROLE = "approver"
ADMINISTRATOR_ROLE = "administrator"
KNOWN_ROLES = frozenset({ENGINEER_ROLE, APPROVER_ROLE, ADMINISTRATOR_ROLE})
bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """Stable identity derived from a verified API access token."""

    subject: str
    roles: frozenset[str]


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    audience: str
    jwks_url: str
    algorithms: tuple[str, ...]
    token_types: frozenset[str]
    role_assignments: dict[str, frozenset[str]]


class AuthenticationConfigurationError(RuntimeError):
    pass


def load_oidc_settings() -> OIDCSettings:
    required = {
        "OIDC_ISSUER": os.getenv("OIDC_ISSUER", "").rstrip("/"),
        "OIDC_AUDIENCE": os.getenv("OIDC_AUDIENCE", ""),
        "OIDC_JWKS_URL": os.getenv("OIDC_JWKS_URL", ""),
    }
    if any(not value for value in required.values()):
        raise AuthenticationConfigurationError(
            "OIDC authentication is not configured."
        )
    algorithms = tuple(
        value.strip()
        for value in os.getenv("OIDC_ALLOWED_ALGORITHMS", "RS256").split(",")
        if value.strip()
    )
    if not algorithms or any(value.startswith("HS") or value == "none" for value in algorithms):
        raise AuthenticationConfigurationError("OIDC algorithms are invalid.")
    token_types = frozenset(
        value.strip()
        for value in os.getenv("OIDC_ACCESS_TOKEN_TYPES", "at+jwt").split(",")
        if value.strip()
    )
    try:
        raw_assignments = json.loads(os.getenv("CLOUDOPS_ROLE_ASSIGNMENTS", "{}"))
        if not isinstance(raw_assignments, dict):
            raise ValueError
        assignments: dict[str, frozenset[str]] = {}
        for subject, raw_roles in raw_assignments.items():
            if not isinstance(subject, str) or not isinstance(raw_roles, list):
                raise ValueError
            roles = frozenset(raw_roles)
            if not roles.issubset(KNOWN_ROLES):
                raise ValueError
            assignments[subject] = roles
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise AuthenticationConfigurationError(
            "CLOUDOPS_ROLE_ASSIGNMENTS is invalid."
        ) from exc
    return OIDCSettings(
        issuer=required["OIDC_ISSUER"],
        audience=required["OIDC_AUDIENCE"],
        jwks_url=required["OIDC_JWKS_URL"],
        algorithms=algorithms,
        token_types=token_types,
        role_assignments=assignments,
    )


@lru_cache(maxsize=8)
def _jwk_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)


def validate_access_token(
    token: str,
    settings: OIDCSettings,
    jwk_client: PyJWKClient | None = None,
) -> AuthenticatedPrincipal:
    """Verify an RFC 9068-style JWT access token and map its stable subject."""
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in settings.algorithms:
            raise jwt.InvalidAlgorithmError
        if header.get("typ") not in settings.token_types:
            raise jwt.InvalidTokenError("Unexpected token type.")
        key_client = jwk_client or _jwk_client(settings.jwks_url)
        signing_key = key_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=list(settings.algorithms),
            audience=settings.audience,
            issuer=settings.issuer,
            options={"require": ["iss", "sub", "aud", "exp", "iat"]},
        )
        subject = claims["sub"]
        issuer = claims["iss"].rstrip("/")
        if not isinstance(subject, str) or not subject:
            raise jwt.InvalidTokenError("Invalid subject.")
    except (jwt.PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    stable_subject = f"{issuer}|{subject}"
    return AuthenticatedPrincipal(
        subject=stable_subject,
        roles=settings.role_assignments.get(stable_subject, frozenset()),
    )


def get_authenticated_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> AuthenticatedPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        settings = load_oidc_settings()
    except AuthenticationConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return validate_access_token(credentials.credentials, settings)


def require_role(principal: AuthenticatedPrincipal, role: str) -> None:
    if role not in principal.roles:
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
