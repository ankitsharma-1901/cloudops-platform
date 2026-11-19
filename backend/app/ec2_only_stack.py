"""Identity and read-only network checks for the isolated EC2-only stack.

These helpers are deliberately not an execution entry point. Callers must bind
the identity to the saved artifact and approval before enabling this stack.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from app.artifact_store import ArtifactError, reject_links
from app.ec2_only_policy import DEFAULT_GROUP, SUBNET, VPC, require_restricted_network
from app.aws_execution import digest_json

ACCOUNT = "294665681966"
BUCKET = "cloudops-tfstate-ca1"
REGION = "ca-central-1"
STATE_KEY = "cloudops/development/ec2-only/terraform.tfstate"
STACK = "aws_ec2_only"
FILES = frozenset({"main.tf", "variables.tf", "versions.tf", "outputs.tf", ".terraform.lock.hcl"})
ROOT = Path(__file__).resolve().parents[2] / "terraform" / "aws" / "ec2-only"
ROOT_ID = "terraform/aws/ec2-only"


def network_identity(group_id: str) -> str:
    return digest_json({"account": ACCOUNT, "region": REGION, "vpc": VPC, "subnet": SUBNET, "security_group_id": group_id})


def stack_identity(group_id: str, root: Path | None = None) -> tuple[str, str, str]:
    """Hash the initialized backend and trusted files, including the group pin."""
    root = root or ROOT
    try:
        if os.getenv("CLOUDOPS_LOCAL_CONFIG"):
            from app.local_config import load_local_config
            if load_local_config()["CLOUDOPS_EC2_SECURITY_GROUP_ID"] != group_id:
                raise ArtifactError()
        if not isinstance(group_id, str) or not re.fullmatch(r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})", group_id) or group_id == DEFAULT_GROUP:
            raise ArtifactError()
        metadata = root / ".terraform" / "terraform.tfstate"
        reject_links(metadata)
        backend = json.loads(metadata.read_text(encoding="utf-8"))["backend"]
        config = backend["config"]
        if (backend["type"] != "s3" or config.get("bucket") != BUCKET or config.get("key") != STATE_KEY
                or config.get("region") != REGION or config.get("allowed_account_ids") != [ACCOUNT]
                or config.get("encrypt") is not True or config.get("use_lockfile") is not True
                or config.get("profile") not in (None, "", "cloudops-deploy")):
            raise ArtifactError()
        for name in ("access_key", "secret_key", "token", "endpoints", "endpoint", "s3_endpoint", "sts_endpoint", "iam_endpoint", "insecure", "assume_role", "assume_role_with_web_identity", "skip_credentials_validation", "skip_region_validation", "skip_requesting_account_id", "skip_metadata_api_check"):
            if config.get(name):
                raise ArtifactError()
        workspace = root / ".terraform" / "environment"
        reject_links(workspace)
        if workspace.exists() and workspace.read_text().strip() != "default":
            raise ArtifactError()
        actual = {p.name for p in root.iterdir() if p.suffix in {".tf", ".tfvars"} or p.name.endswith((".tf.json", ".tfvars.json"))}
        if actual != FILES - {".terraform.lock.hcl"}:
            raise ArtifactError()
        hashes = {}
        for name in sorted(FILES):
            path = root / name
            reject_links(path)
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        return ACCOUNT, digest_json({"stack": STACK, "backend": backend}), digest_json({"files": hashes, "security_group_id": group_id})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise ArtifactError() from None


def verify_network(runner, aws: str, environment: dict[str, str], group_id: str) -> None:
    """Use the existing bounded, noninteractive CLI runner; expose no raw output."""
    try:
        subnet_result = runner._run(aws, ("ec2", "describe-subnets", "--subnet-ids", SUBNET,
            "--profile", "cloudops-deploy", "--region", REGION, "--output", "json", "--no-cli-pager"),
            "preflight", ROOT, environment, timeout=runner.credential_timeout_seconds)
        group_result = runner._run(aws, ("ec2", "describe-security-groups", "--group-ids", group_id,
            "--profile", "cloudops-deploy", "--region", REGION, "--output", "json", "--no-cli-pager"),
            "preflight", ROOT, environment, timeout=runner.credential_timeout_seconds)
        subnets = json.loads(subnet_result.stdout)["Subnets"]
        groups = json.loads(group_result.stdout)["SecurityGroups"]
        if len(subnets) != 1 or len(groups) != 1:
            raise ArtifactError()
        require_restricted_network(subnets[0], groups[0], group_id)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ArtifactError() from None
