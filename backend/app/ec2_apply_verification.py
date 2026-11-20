"""Strict, side-effect-free checks for the EC2-only apply boundary."""

import json
import io
import re
import zipfile

from app.artifact_store import ArtifactError
from app.ec2_only_policy import AMI, SUBNET, VPC


def verify_prior_state(plan_bytes: bytes, current_state: str | None) -> None:
    """Check saved-plan state lineage/serial without printing the plan or state."""
    try:
        with zipfile.ZipFile(io.BytesIO(plan_bytes)) as archive:
            if archive.namelist().count("tfstate") != 1 or archive.getinfo("tfstate").file_size > 64 * 1024 * 1024:
                raise ArtifactError()
            prior = json.loads(archive.read("tfstate"))
        if prior.get("version") != 4 or type(prior.get("serial")) is not int or prior["serial"] < 0 or not isinstance(prior.get("resources"), list):
            raise ArtifactError()
        if current_state is None:
            expected_data = {("aws_subnet", "selected"), ("aws_security_group", "selected")}
            if (prior.get("lineage") not in (None, "") or prior["serial"] != 0
                    or any(item.get("mode") != "data" or item.get("module")
                           or (item.get("type"), item.get("name")) not in expected_data
                           for item in prior["resources"])):
                raise ArtifactError()
            return
        current = json.loads(current_state)
        if (current.get("version") != 4 or type(current.get("serial")) is not int
                or not isinstance(prior.get("lineage"), str) or not prior["lineage"]
                or current.get("lineage") != prior["lineage"] or current.get("serial") != prior["serial"]):
            raise ArtifactError()
    except (ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile):
        raise ArtifactError() from None


def state_instance_id(raw_state: str) -> str:
    """Extract only the one expected managed instance, or fail closed."""
    try:
        state = json.loads(raw_state)
        if state.get("version") != 4 or not isinstance(state.get("resources"), list):
            raise ArtifactError()
        managed = [r for r in state["resources"] if r.get("mode") == "managed"]
        if len(managed) != 1:
            raise ArtifactError()
        resource = managed[0]
        if (resource.get("type") != "aws_instance" or resource.get("name") != "dev"
                or resource.get("module") or len(resource.get("instances", [])) != 1):
            raise ArtifactError()
        entry = resource["instances"][0]
        if entry.get("deposed") or "index_key" in entry:
            raise ArtifactError()
        attributes = entry["attributes"]
        instance_id = attributes["id"]
        if not isinstance(instance_id, str) or not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", instance_id):
            raise ArtifactError()
        return instance_id
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ArtifactError() from None


def verify_completed_instance(raw_state: str, raw_instances: str, security_group_id: str) -> str:
    """Verify one state-owned instance against a fresh, account-verified AWS response."""
    try:
        instance_id = state_instance_id(raw_state)
        data = json.loads(raw_instances)
        reservations = data["Reservations"]
        if len(reservations) != 1 or reservations[0].get("OwnerId") != "294665681966":
            raise ArtifactError()
        instances = [instance for reservation in reservations for instance in reservation["Instances"]]
        if len(instances) != 1:
            raise ArtifactError()
        instance = instances[0]
        tags = {entry["Key"]: entry["Value"] for entry in instance.get("Tags", [])}
        groups = [group["GroupId"] for group in instance.get("SecurityGroups", [])]
        if (instance.get("InstanceId") != instance_id or instance.get("ImageId") != AMI
                or instance.get("InstanceType") != "t3.micro" or instance.get("SubnetId") != SUBNET
                or instance.get("VpcId") != VPC or groups != [security_group_id]
                or instance.get("PublicIpAddress") or instance.get("IamInstanceProfile")
                or any(interface.get("Ipv6Addresses") or (interface.get("Association") or {}).get("PublicIp")
                       for interface in instance.get("NetworkInterfaces", []))
                or instance.get("State", {}).get("Name") not in {"pending", "running"}
                or any(tags.get(key) != value for key, value in {
                    "Project": "CloudOps", "Environment": "development", "ManagedBy": "Terraform"}.items())):
            raise ArtifactError()
        return instance_id
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ArtifactError() from None
