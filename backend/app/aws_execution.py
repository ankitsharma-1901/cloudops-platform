"""Fixed sandbox identity and create-only policy. No generic Terraform execution."""

import hashlib
import json
import os
import re
from pathlib import Path

from app.artifact_store import ArtifactError, reject_links
from app import terraform_runner as tf

ALLOWED_RESOURCES = frozenset({
    "aws_vpc.dev", "aws_subnet.public", "aws_internet_gateway.dev", "aws_route_table.public",
    "aws_route.internet", "aws_route_table_association.public", "aws_security_group.instance",
    "aws_vpc_security_group_egress_rule.https", "aws_iam_role.instance",
    "aws_iam_role_policy_attachment.ssm", "aws_iam_instance_profile.instance", "aws_instance.dev",
})
CONFIG_FILES = frozenset({"versions.tf", "variables.tf", "providers.tf", "network.tf", "compute.tf", "iam.tf", "outputs.tf", ".terraform.lock.hcl"})


def apply_enabled() -> bool:
    return os.getenv("CLOUDOPS_ENABLE_AWS_APPLY", "false") == "true"


def digest_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def execution_identity() -> tuple[str, str, str]:
    """Independently pinned account/bucket plus exact backend/config fingerprints."""
    if os.getenv("CLOUDOPS_LOCAL_CONFIG"):
        from app.local_config import load_local_config
        try:
            load_local_config()
        except ValueError:
            raise ArtifactError() from None
    account = os.getenv(tf.EXPECTED_ACCOUNT_ENV, "")
    bucket = os.getenv("CLOUDOPS_AWS_STATE_BUCKET", "")
    if not re.fullmatch(r"[0-9]{12}", account) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ArtifactError()
    try:
        reject_links(tf.AWS_BACKEND_METADATA)
        backend = json.loads(tf.AWS_BACKEND_METADATA.read_text(encoding="utf-8"))["backend"]
        config = backend["config"]
        if (backend["type"] != "s3" or config.get("bucket") != bucket
                or config.get("region") != tf.AWS_REGION
                or config.get("key") != "cloudops/development/terraform.tfstate"
                or config.get("allowed_account_ids") != [account]
                or config.get("encrypt") is not True or config.get("use_lockfile") is not True
                or config.get("profile") not in (None, "", tf.AWS_PROFILE)):
            raise ArtifactError()
        for name in ("access_key", "secret_key", "token", "endpoints", "endpoint", "s3_endpoint", "sts_endpoint", "iam_endpoint", "insecure", "assume_role", "assume_role_with_web_identity",
                     "skip_credentials_validation", "skip_region_validation", "skip_requesting_account_id", "skip_metadata_api_check"):
            if config.get(name):
                raise ArtifactError()
        workspace = tf.AWS_DIRECTORY / ".terraform" / "environment"
        reject_links(workspace)
        if workspace.exists() and workspace.read_text().strip() != "default":
            raise ArtifactError()
        actual = {p.name for p in tf.AWS_DIRECTORY.iterdir() if p.suffix in {".tf", ".tfvars"} or p.name.endswith((".tf.json", ".tfvars.json"))}
        if actual != CONFIG_FILES - {".terraform.lock.hcl"}:
            raise ArtifactError()
        contents = {}
        for name in sorted(CONFIG_FILES):
            path = tf.AWS_DIRECTORY / name
            reject_links(path)
            contents[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, ValueError, KeyError, TypeError):
        raise ArtifactError() from None
    return account, digest_json(backend), digest_json(contents)


def require_create_only(raw_plan: str) -> None:
    try:
        plan = json.loads(raw_plan)
        if plan.get("errored") or plan.get("applyable") is False or plan.get("complete") is False or plan.get("deferred_changes"):
            raise ArtifactError()
        changes = plan["resource_changes"]
        creates = 0
        for item in changes:
            actions = item["change"]["actions"]
            if item.get("mode") == "data" or item["address"].startswith("data."):
                if item["address"] != "data.aws_ssm_parameter.al2023" or actions not in (["read"], ["no-op"]):
                    raise ArtifactError()
                continue
            if (item["address"] not in ALLOWED_RESOURCES or actions not in (["create"], ["no-op"])
                    or item["change"].get("importing") or item.get("previous_address")):
                raise ArtifactError()
            creates += actions == ["create"]
        if not creates or plan.get("resource_drift"):
            raise ArtifactError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ArtifactError() from None
