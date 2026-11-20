"""Fail-closed plan and network checks for the isolated EC2-only stack."""

import json
import re

from app.artifact_store import ArtifactError

AMI = "ami-009d185a5ea80b390"
SUBNET = "subnet-08dfc8d266e48fc91"
VPC = "vpc-05828cfb0f6ae89e4"
DEFAULT_GROUP = "sg-0a1df66f2332f53e3"


class EC2PlanPolicyError(ArtifactError):
    """A fixed, non-sensitive category for a rejected EC2-only plan."""

    def __init__(self, reason: str):
        super().__init__()
        self.reason = reason


def require_one_instance(raw_plan: str, security_group_id: str) -> None:
    reason = "security_group_invalid"
    try:
        if not re.fullmatch(r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})", security_group_id) or security_group_id == DEFAULT_GROUP:
            raise ArtifactError()
        reason = "plan_metadata_invalid"
        plan = json.loads(raw_plan)
        if plan.get("errored") or plan.get("applyable") is False or plan.get("complete") is False or plan.get("deferred_changes") or plan.get("resource_drift"):
            raise ArtifactError()
        reason = "resource_actions_invalid"
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
        reason = "instance_attributes_invalid"
        after = managed[0]["after"]
        unknown = managed[0].get("after_unknown", {})
        if not isinstance(unknown, dict):
            raise ArtifactError()
        # The absent instance profile is a security property, not a value that
        # may be deferred until the provider creates the instance.
        if "iam_instance_profile" in unknown and unknown["iam_instance_profile"] is not False:
            reason = "instance_profile_unknown"
            raise ArtifactError()
        # A launch template can supply an instance profile indirectly.
        if ("launch_template" in unknown and unknown["launch_template"] is not False
                or after.get("launch_template") not in (None, [])):
            reason = "launch_template_invalid"
            raise ArtifactError()
        profile = after.get("iam_instance_profile")
        if profile is not None and (not isinstance(profile, str) or profile != ""):
            reason = "instance_profile_invalid"
            raise ArtifactError()
        disk = after["root_block_device"]
        metadata = after["metadata_options"]
        tags = after.get("tags_all") or after.get("tags") or {}
        if (after.get("ami") != AMI or after.get("instance_type") != "t3.micro"
                or after.get("subnet_id") != SUBNET or after.get("vpc_security_group_ids") != [security_group_id]
                or after.get("associate_public_ip_address") is not False
                or len(disk) != 1 or disk[0].get("volume_type") != "gp3" or disk[0].get("volume_size") != 8
                or disk[0].get("encrypted") is not True or disk[0].get("delete_on_termination") is not True
                or len(metadata) != 1 or metadata[0].get("http_tokens") != "required"
                or any(tags.get(k) != v for k, v in {"Project": "CloudOps", "Environment": "development", "ManagedBy": "Terraform"}.items())):
            raise ArtifactError()
    except (ValueError, TypeError, KeyError, AttributeError):
        raise EC2PlanPolicyError(reason) from None


def require_restricted_network(subnet: dict, group: dict, security_group_id: str) -> None:
    """Require a same-account subnet and a same-VPC, no-ingress group."""
    try:
        if (not re.fullmatch(r"sg-(?:[0-9a-f]{8}|[0-9a-f]{17})", security_group_id)
                or subnet["SubnetId"] != SUBNET or subnet["VpcId"] != VPC or subnet["OwnerId"] != "294665681966"
                or subnet["AvailabilityZone"] != "ca-central-1a" or subnet["State"] != "available"
                or subnet.get("AssignIpv6AddressOnCreation") is not False
                or group["GroupId"] != security_group_id or security_group_id == DEFAULT_GROUP
                or group["VpcId"] != VPC or group["OwnerId"] != "294665681966"
                or group["GroupName"] == "default" or group["IpPermissions"] != []):
            raise ArtifactError()
        egress = group["IpPermissionsEgress"]
        if (len(egress) != 1 or egress[0].get("IpProtocol") != "tcp"
                or egress[0].get("FromPort") != 443 or egress[0].get("ToPort") != 443
                or [item.get("CidrIp") for item in egress[0].get("IpRanges", [])] != ["0.0.0.0/0"]
                or egress[0].get("Ipv6Ranges") or egress[0].get("UserIdGroupPairs")
                or egress[0].get("PrefixListIds")):
            raise ArtifactError()
    except (KeyError, TypeError):
        raise ArtifactError() from None
