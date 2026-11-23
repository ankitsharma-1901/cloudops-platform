"""Reviewed EC2 inputs. Phase 1A permits a name and one fixed AWS shape."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from app.ec2_demo_catalog import AMI, GROUP, INSTANCE_NAME, INSTANCE_TYPE, REGION, ROOT_VOLUME_SIZE_GIB, ROOT_VOLUME_TYPE, SUBNET, VPC


class EC2Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    instance_name: StrictStr = Field(min_length=3, max_length=32, pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$")
    region: Literal[REGION]
    operating_system: Literal["amazon-linux-2023"]
    ami: Literal[AMI]
    instance_type: Literal[INSTANCE_TYPE]
    storage_gib: Literal[ROOT_VOLUME_SIZE_GIB]
    storage_type: Literal[ROOT_VOLUME_TYPE]
    encrypted: Literal[True]
    vpc: Literal[VPC]
    subnet: Literal[SUBNET]
    security_group: Literal[GROUP]


def catalog():
    return {"defaults": {"instance_name": INSTANCE_NAME, "region": REGION,
        "operating_system": "amazon-linux-2023", "ami": AMI, "instance_type": INSTANCE_TYPE,
        "storage_gib": ROOT_VOLUME_SIZE_GIB, "storage_type": ROOT_VOLUME_TYPE,
        "encrypted": True, "vpc": VPC, "subnet": SUBNET, "security_group": GROUP},
        "security": {"public_ipv4": False, "imdsv2_required": True, "instance_profile": None}}
