"""Explicit, isolated EC2 demo. Uncertain operations are never retried."""
import os
import re
import subprocess
from uuid import uuid4

import boto3
import botocore.session
from botocore.config import Config
from botocore.exceptions import ClientError, CredentialRetrievalError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.auth import AuthenticatedPrincipal, get_authenticated_principal, require_role
from app.database import get_session
from app.models import EC2Demo, EC2DemoAudit

router = APIRouter(prefix="/api/v1/ec2-demo", tags=["ec2-demo"])
ACCOUNT = "294665681966"
REGION = "ca-central-1"
AMI = "ami-009d185a5ea80b390"
SUBNET = "subnet-08dfc8d266e48fc91"
GROUP = "sg-068c70a801608db19"
PROFILE = "cloudops-deploy"
ROLE = "cloudops-terraform-deploy"
TAGS = {"Project": "CloudOps", "Environment": "development", "ManagedBy": "CloudOpsDemo"}


class Confirmation(BaseModel):
    confirmation: str


def enabled():
    if os.getenv("CLOUDOPS_ENABLE_EC2_DEMO", "false") != "true" or not os.getenv("CLOUDOPS_LOCAL_CONFIG"):
        return False
    try:
        from app.local_config import load_local_config
        return load_local_config()["CLOUDOPS_ENABLE_EC2_DEMO"] == "true"
    except ValueError:
        return False


def clients():
    # Explicit profile avoids an ambient administrative credential chain.
    core = botocore.session.get_session()
    core.set_config_variable("profile", PROFILE)
    provider = core.get_component("credential_provider").get_provider("custom-process")
    if provider is None or not hasattr(provider, "_popen"):
        raise ValueError("credential provider unavailable")
    provider._popen = _NoninteractiveProcess
    session = boto3.Session(botocore_session=core, profile_name=PROFILE, region_name=REGION)
    config = Config(connect_timeout=5, read_timeout=15, retries={"total_max_attempts": 1})
    identity = session.client("sts", config=config).get_caller_identity()
    if identity.get("Account") != ACCOUNT or not re.fullmatch(
            rf"arn:aws:sts::{ACCOUNT}:assumed-role/{ROLE}/[^/]+", identity.get("Arn", "")):
        raise ValueError("identity")
    return session.client("ec2", config=config)


class _NoninteractiveProcess:
    """Bound credential_process and prevent MFA prompts in the API process."""
    def __init__(self, args, **kwargs):
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL, **kwargs)

    @property
    def returncode(self):
        return self.process.returncode

    def communicate(self):
        try:
            return self.process.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
            try:
                self.process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                # A descendant can keep the inherited pipes open after its parent dies.
                for pipe in (self.process.stdout, self.process.stderr):
                    if pipe is not None:
                        pipe.close()
            raise CredentialRetrievalError(provider="custom-process", error_msg="Noninteractive credential check timed out") from None


def audit(session, actor, event):
    session.add(EC2DemoAudit(actor_subject=actor, event_type=event))


def safe_error(exc):
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"UnauthorizedOperation", "AccessDenied", "AccessDeniedException"}:
            return "AWS denied the operation; operator review is required."
        if code in {"InsufficientInstanceCapacity", "InstanceLimitExceeded"}:
            return "AWS capacity or quota prevented the operation; operator review is required."
    return "AWS result is uncertain; operator review is required."


def live_instance(ec2, record):
    result = ec2.describe_instances(InstanceIds=[record.instance_id])
    reservations = result.get("Reservations", [])
    if len(reservations) != 1 or reservations[0].get("OwnerId") != ACCOUNT:
        raise ValueError("ownership")
    instances = reservations[0].get("Instances", [])
    if len(instances) != 1:
        raise ValueError("ownership")
    instance = instances[0]
    tags = {x.get("Key"): x.get("Value") for x in instance.get("Tags", [])}
    expected = {**TAGS, "CloudOpsDemoId": record.operation_token}
    if (instance.get("InstanceId") != record.instance_id or instance.get("ImageId") != AMI
            or instance.get("InstanceType") != "t3.micro" or instance.get("SubnetId") != SUBNET
            or instance.get("VpcId") != "vpc-05828cfb0f6ae89e4"
            or [g.get("GroupId") for g in instance.get("SecurityGroups", [])] != [GROUP]
            or instance.get("IamInstanceProfile") or instance.get("PublicIpAddress")
            or any(tags.get(k) != v for k, v in expected.items())):
        raise ValueError("ownership")
    return instance


@router.get("")
def get_demo(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    record = session.get(EC2Demo, 1)
    result = {"enabled": enabled(), "instance": None}
    if not record:
        return result
    if record.owner_subject != principal.subject:
        raise HTTPException(403, "Demo belongs to another engineer.")
    result["instance"] = {"id": record.instance_id, "status": record.status, "message": record.message, "region": REGION}
    if record.instance_id and record.status in {"created", "terminating"}:
        try:
            instance = live_instance(clients(), record)
            state = instance.get("State", {}).get("Name", "unknown")
            result["instance"]["aws_state"] = state
            result["instance"]["instance_type"] = instance.get("InstanceType")
            result["instance"]["ami"] = instance.get("ImageId")
            result["instance"]["subnet"] = instance.get("SubnetId")
            result["instance"]["security_group"] = GROUP
            if record.status == "terminating" and state == "terminated":
                record.status = "terminated"
                audit(session, principal.subject, "termination_confirmed")
                session.add(record)
                session.commit()
                result["instance"]["status"] = "terminated"
        except Exception:
            result["instance"]["aws_state"] = "unavailable"
    return result


@router.post("/create")
def create_demo(body: Confirmation, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    if not enabled():
        raise HTTPException(503, "EC2 Demo Mode is disabled.")
    if body.confirmation != "CREATE ONE EC2 DEMO INSTANCE":
        raise HTTPException(400, "Explicit create confirmation is required.")
    token = str(uuid4())
    record = EC2Demo(id=1, owner_subject=principal.subject, operation_token=token, status="creating")
    session.add(record)
    audit(session, principal.subject, "create_started")
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "A demo instance or unresolved operation already exists.") from None
    try:
        if not enabled():
            raise ValueError("disabled")
        ec2 = clients()
        from app.ec2_only_policy import require_restricted_network
        subnets = ec2.describe_subnets(SubnetIds=[SUBNET]).get("Subnets", [])
        groups = ec2.describe_security_groups(GroupIds=[GROUP]).get("SecurityGroups", [])
        if len(subnets) != 1 or len(groups) != 1:
            raise ValueError("network")
        require_restricted_network(subnets[0], groups[0], GROUP)
        images = ec2.describe_images(ImageIds=[AMI]).get("Images", [])
        if len(images) != 1 or images[0].get("State") != "available" or images[0].get("Architecture") != "x86_64":
            raise ValueError("image")
        root = images[0].get("RootDeviceName")
        if not isinstance(root, str) or not re.fullmatch(r"/dev/[a-z0-9]+", root):
            raise ValueError("root")
        tags = [{"Key": k, "Value": v} for k, v in {**TAGS, "CloudOpsDemoId": token, "Name": "cloudops-ec2-demo"}.items()]
        if not enabled():
            raise ValueError("disabled")
        response = ec2.run_instances(ImageId=AMI, InstanceType="t3.micro", MinCount=1, MaxCount=1,
            ClientToken=token, MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": SUBNET, "Groups": [GROUP], "AssociatePublicIpAddress": False}],
            BlockDeviceMappings=[{"DeviceName": root, "Ebs": {"VolumeSize": 8, "VolumeType": "gp3", "Encrypted": True, "DeleteOnTermination": True}}],
            TagSpecifications=[{"ResourceType": "instance", "Tags": tags}, {"ResourceType": "volume", "Tags": tags}])
        instances = response.get("Instances", [])
        if len(instances) != 1 or not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", instances[0].get("InstanceId", "")):
            raise ValueError("instance")
        record.instance_id = instances[0]["InstanceId"]
        record.status = "created"
        audit(session, principal.subject, "create_returned")
        session.add(record)
        session.commit()
        return {"id": record.instance_id, "status": record.status}
    except Exception as exc:
        session.rollback()
        record = session.get(EC2Demo, 1)
        if record:
            record.status = "needs_operator_review"
            record.message = safe_error(exc)
            audit(session, principal.subject, "create_uncertain")
            session.add(record)
            session.commit()
        raise HTTPException(503, safe_error(exc)) from None


@router.post("/terminate")
def terminate_demo(body: Confirmation, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    if not enabled():
        raise HTTPException(503, "EC2 Demo Mode is disabled.")
    record = session.get(EC2Demo, 1)
    if not record or record.owner_subject != principal.subject or not record.instance_id:
        raise HTTPException(404, "Owned demo instance not found.")
    if body.confirmation != "TERMINATE " + record.instance_id:
        raise HTTPException(400, "Confirm the exact instance ID.")
    result = session.exec(update(EC2Demo).where(EC2Demo.id == 1, EC2Demo.status == "created", EC2Demo.owner_subject == principal.subject).values(status="terminating"))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "Demo operation is already running or needs review.")
    audit(session, principal.subject, "termination_started")
    session.commit()
    try:
        if not enabled():
            raise ValueError("disabled")
        ec2 = clients()
        live_instance(ec2, record)
        if not enabled():
            raise ValueError("disabled")
        ec2.terminate_instances(InstanceIds=[record.instance_id])
        audit(session, principal.subject, "termination_returned")
        session.commit()
        return {"id": record.instance_id, "status": "terminating"}
    except Exception as exc:
        session.rollback()
        record = session.get(EC2Demo, 1)
        record.status = "needs_operator_review"
        record.message = safe_error(exc)
        audit(session, principal.subject, "termination_uncertain")
        session.add(record)
        session.commit()
        raise HTTPException(503, safe_error(exc)) from None
