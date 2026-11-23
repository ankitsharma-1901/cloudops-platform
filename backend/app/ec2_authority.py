"""Fresh Keycloak account checks against the shared local role manifest."""

import json
import os
import re
import ssl
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.local_config import load_local_config


@dataclass(frozen=True)
class AuthorityResult:
    allowed: bool
    reason: str


class CurrentAuthority:
    def check(self, requester: str, approver: str) -> AuthorityResult:
        unavailable = AuthorityResult(False, "current_identity_authority_unavailable")
        try:
            client_id = os.environ["CLOUDOPS_EC2_KEYCLOAK_CLIENT_ID"]
            secret = os.environ["CLOUDOPS_EC2_KEYCLOAK_CLIENT_SECRET"]
            if not client_id.strip() or not secret.strip():
                return unavailable
            settings = load_local_config()
            issuer = settings["OIDC_ISSUER"]
            parsed = urlsplit(issuer)
            realm = re.fullmatch(r"(?P<base>(?:/[a-zA-Z0-9._~-]+)*)/realms/(?P<realm>[a-zA-Z0-9_-]+)", parsed.path)
            if (parsed.scheme != "https" or not realm or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment
                    or any(segment in {".", ".."} for segment in realm["base"].split("/"))):
                return unavailable
            assignments = json.loads(settings["CLOUDOPS_ROLE_ASSIGNMENTS"])
            if requester == approver:
                return AuthorityResult(False, "self_approval")
            users = []
            for subject, role in ((requester, "engineer"), (approver, "approver")):
                prefix, separator, user_id = subject.rpartition("|")
                if (not separator or prefix != issuer or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", user_id)
                        or role not in assignments.get(subject, [])):
                    return AuthorityResult(False, "current_role_denied")
                users.append((user_id, role))
            admin = f"{parsed.scheme}://{parsed.netloc}{realm['base']}/admin/realms/{realm['realm']}"
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False,
                              verify=ssl.create_default_context()) as client:
                response = client.post(issuer + "/protocol/openid-connect/token", auth=(client_id, secret),
                                       data={"grant_type": "client_credentials"})
                token = response.raise_for_status().json()["access_token"]
                if not isinstance(token, str) or not token or any(char.isspace() for char in token):
                    return unavailable
                headers = {"Authorization": f"Bearer {token}"}
                for user_id, required_role in users:
                    user_url = f"{admin}/users/{user_id}"
                    user = client.get(user_url, headers=headers).raise_for_status().json()
                    if user.get("id") != user_id or user.get("enabled") is not True:
                        return AuthorityResult(False, "current_identity_denied")
                    response = client.get(f"{user_url}/role-mappings/realm/composite", headers=headers)
                    roles = response.raise_for_status().json()
                    if not isinstance(roles, list) or any(
                        not isinstance(role, dict) or not isinstance(role.get("name"), str)
                        or role.get("clientRole") is not False for role in roles
                    ):
                        return unavailable
                    if not any(role["name"] == required_role for role in roles):
                        return AuthorityResult(False, "current_role_denied")
            if load_local_config() != settings:
                return unavailable
        except (OSError, ValueError, KeyError, TypeError, AttributeError, httpx.HTTPError):
            return unavailable
        return AuthorityResult(True, "current_authority_verified")
