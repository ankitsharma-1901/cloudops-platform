"""One non-secret local manifest. Validate before a writable DB engine or CLI."""

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from app.artifact_store import reject_links

ROOT = Path(__file__).resolve().parents[2]
ERROR = "Local service configuration is missing, incompatible, or conflicts with the process environment."


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(ERROR)
        result[key] = value
    return result


def load_local_config() -> dict[str, str]:
    """No defaults for target pins, no secrets, no silent environment overrides."""
    try:
        path = Path(os.environ["CLOUDOPS_LOCAL_CONFIG"])
        if not path.is_absolute():
            raise ValueError(ERROR)
        reject_links(path)
        config = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
        required = {"database", "artifact_root", "account_id", "bucket", "region", "environment",
                    "backend_key", "enable_aws_apply", "oidc_issuer", "oidc_audience", "roles"}
        if not required <= set(config) or set(config) - required - {"ec2_security_group_id"}:
            raise ValueError(ERROR)
        group = config.get("ec2_security_group_id", "")
        if not isinstance(group, str) or (group and (not re.fullmatch(r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})", group) or group == "sg-0a1df66f2332f53e3")):
            raise ValueError(ERROR)
        if (config["database"] != "backend/cloudops.db" or config["artifact_root"] != "backend/.private/plans"
                or config["region"] != "ca-central-1" or config["environment"] != "development"
                or config["backend_key"] != "cloudops/development/terraform.tfstate"
                or type(config["enable_aws_apply"]) is not bool
                or not re.fullmatch(r"[0-9]{12}", config["account_id"])
                or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", config["bucket"])):
            raise ValueError(ERROR)
        if config["enable_aws_apply"] and (config["account_id"] != "294665681966"
                                          or config["bucket"] != "cloudops-tfstate-ca1"):
            raise ValueError(ERROR)
        database = ROOT / config["database"]
        artifact_root = ROOT / config["artifact_root"]
        reject_links(database)
        reject_links(artifact_root)
        if not database.is_file():
            raise ValueError(ERROR)  # Never create an accidental empty replacement DB.
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"server_requests", "provisioning_jobs", "approval_requests"} <= tables:
                raise ValueError(ERROR)
        issuer = config["oidc_issuer"]
        parsed = urlsplit(issuer)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or issuer.endswith("/")
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost"})
                or config["oidc_audience"] != "cloudops-api" or not isinstance(config["roles"], dict)
                or not config["roles"]):
            raise ValueError(ERROR)
        for subject, roles in config["roles"].items():
            if (not subject.startswith(issuer + "|") or not subject[len(issuer) + 1:]
                    or not isinstance(roles, list) or not roles
                    or any(role not in {"engineer", "approver", "administrator"} for role in roles)):
                raise ValueError(ERROR)
        values = {
            "DATABASE_URL": "sqlite:///" + database.as_posix(),
            "CLOUDOPS_ARTIFACT_ROOT": str(artifact_root),
            "CLOUDOPS_ENABLE_AWS_APPLY": str(config["enable_aws_apply"]).lower(),
            "CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID": config["account_id"],
            "CLOUDOPS_AWS_STATE_BUCKET": config["bucket"],
            "AWS_REGION": config["region"], "AWS_DEFAULT_REGION": config["region"],
            "AWS_PROFILE": "cloudops-deploy", "AWS_EC2_METADATA_DISABLED": "true",
            "CLOUDOPS_EC2_SECURITY_GROUP_ID": group,
            "OIDC_ISSUER": issuer, "OIDC_AUDIENCE": config["oidc_audience"],
            "OIDC_JWKS_URL": issuer + "/protocol/openid-connect/certs",
            "OIDC_ALLOWED_ALGORITHMS": "RS256", "OIDC_ACCESS_TOKEN_TYPES": "JWT",
            "CLOUDOPS_ROLE_ASSIGNMENTS": json.dumps(config["roles"], sort_keys=True, separators=(",", ":")),
        }
        for name, expected in values.items():
            actual = os.getenv(name)
            if actual is not None and actual != expected:
                # Role JSON formatting is immaterial, its contents are not.
                if name != "CLOUDOPS_ROLE_ASSIGNMENTS" or json.loads(actual) != config["roles"]:
                    raise ValueError(ERROR)
        os.environ.update(values)
        return values
    except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error):
        raise ValueError(ERROR) from None


def configuration_fingerprint(values: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
