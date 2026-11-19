"""Fail-closed validation for a future isolated EC2-only saved-plan path.

This module is not wired into the existing AWS deployment path. Integration must
also bind the separate backend identity and independently inspect the live group.
"""

import json
import re

from app.artifact_store import ArtifactError

AMI = "ami-009d185a5ea80b390"
SUBNET = "subnet-08dfc8d266e48fc91"
VPC = "vpc-05828cfb0f6ae89e4"
DEFAULT_GROUP = "sg-0a1df66f2332f53e3"


def require_one_instance(raw_plan: str, security_group_id: str) -> None:
    try:
        if not re.fullmatch(r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})", security_group_id) or security_group_id == DEFAULT_GROUP:
            raise ArtifactError()
        plan = json.loads(raw_plan)
        if plan.get("errored") or plan.get("applyable") is False or plan.get("complete") is False or plan.get("deferred_changes") or plan.get("resource_drift"):
            raise ArtifactError()
        managed = []
        for item in plan["resource_changes"]:
            address, change = item["address"], item["change"]
            if item.get("mode") == "data":
                if address not in {"data.aws_subnet.selected", "data.aws_security_group.selected"} or change["actions"] not in (["read"], ["no-op"]) or change.get("importing") or item.get("previous_address"):
                    raise ArtifactError()
                continue
            if item.get("mode") != "managed" or address != "aws_instance.dev" or change["actions"] != ["create"] or change.get("importing") or item.get("previous_address"):
                raise ArtifactError()
            managed.append(change)
        if len(managed) != 1:
            raise ArtifactError()
        after = managed[0]["after"]
        unknown = managed[0].get("after_unknown") or {}
        # The absent instance profile is a security property, not a value that
        # may be deferred until the provider creates the instance.
        if unknown.get("iam_instance_profile"):
            raise ArtifactError()
        disk = after["root_block_device"]
        metadata = after["metadata_options"]
        tags = after.get("tags_all") or after.get("tags") or {}
        if (after.get("ami") != AMI or after.get("instance_type") != "t3.micro"
                or after.get("subnet_id") != SUBNET or after.get("vpc_security_group_ids") != [security_group_id]
                or after.get("associate_public_ip_address") is not False or after.get("iam_instance_profile")
                or len(disk) != 1 or disk[0].get("volume_type") != "gp3" or disk[0].get("volume_size") != 8
                or disk[0].get("encrypted") is not True or disk[0].get("delete_on_termination") is not True
                or len(metadata) != 1 or metadata[0].get("http_tokens") != "required"
                or any(tags.get(k) != v for k, v in {"Project": "CloudOps", "Environment": "development", "ManagedBy": "Terraform"}.items())):
            raise ArtifactError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ArtifactError() from None


def require_restricted_network(subnet: dict, group: dict, security_group_id: str) -> None:
    """Require a same-account subnet and a same-VPC, no-ingress group."""
    try:
        if (not re.fullmatch(r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})", security_group_id)
                or subnet["SubnetId"] != SUBNET or subnet["VpcId"] != VPC or subnet["OwnerId"] != "294665681966"
                or subnet["AvailabilityZone"] != "ca-central-1a" or subnet["State"] != "available"
                or group["GroupId"] != security_group_id or security_group_id == DEFAULT_GROUP
                or group["VpcId"] != VPC or group["OwnerId"] != "294665681966"
                or group["GroupName"] == "default" or group["IpPermissions"] != []):
            raise ArtifactError()
    except (KeyError, TypeError):
        raise ArtifactError() from None
