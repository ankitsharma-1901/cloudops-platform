"""Explicit, isolated EC2 demo. Uncertain operations are never retried."""
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import boto3
import botocore.session
from botocore.config import Config
from botocore.exceptions import ClientError, CredentialRetrievalError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.auth import AuthenticatedPrincipal, get_authenticated_principal, require_role
from app.database import get_session
from app.ec2_demo_catalog import (
    ACCOUNT, AMI, GROUP, INSTANCE_NAME, INSTANCE_TYPE, PROFILE, REGION, ROLE,
    ROOT_VOLUME_SIZE_GIB, ROOT_VOLUME_TYPE, SUBNET, TAGS, VPC,
)
from app.models import EC2Demo, EC2DemoAudit, EC2DemoHistory, EC2DemoReview
from app.ec2_configuration import EC2Configuration, catalog

router = APIRouter(prefix="/api/v1/ec2-demo", tags=["ec2-demo"])


class Confirmation(BaseModel):
    confirmation: str
    model_config = ConfigDict(extra="forbid")


class CreateConfirmation(Confirmation):
    review_id: str | None = None


@router.get("/catalog")
def get_catalog(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal)):
    require_role(principal, "engineer")
    return catalog()


@router.post("/review")
def review_demo(configuration: EC2Configuration, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    review = EC2DemoReview(owner_subject=principal.subject, configuration=configuration.model_dump(),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15))
    session.add(review)
    session.commit()
    return {"review_id": review.id, "configuration": review.configuration, "expires_at": review.expires_at,
        "security": catalog()["security"]}


def enabled():
    if os.getenv("CLOUDOPS_ENABLE_EC2_DEMO", "false") != "true" or not os.getenv("CLOUDOPS_LOCAL_CONFIG"):
        return False
    try:
        from app.local_config import load_local_config
        return load_local_config()["CLOUDOPS_ENABLE_EC2_DEMO"] == "true"
    except ValueError:
        return False


def require_provisioning_approval():
    """Fail closed until direct EC2 requests have a worker-enforced approval."""
    raise HTTPException(503, "EC2 creation is unavailable until approval enforcement is configured.")


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


def launch_parameters(ec2, token, instance_name):
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
    tags = [{"Key": k, "Value": v} for k, v in {**TAGS, "CloudOpsDemoId": token, "Name": instance_name}.items()]
    return dict(ImageId=AMI, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
        ClientToken=token, MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
        NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": SUBNET, "Groups": [GROUP], "AssociatePublicIpAddress": False}],
        BlockDeviceMappings=[{"DeviceName": root, "Ebs": {"VolumeSize": ROOT_VOLUME_SIZE_GIB, "VolumeType": ROOT_VOLUME_TYPE, "Encrypted": True, "DeleteOnTermination": True}}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": tags}, {"ResourceType": "volume", "Tags": tags}])


def launch_instance(ec2, parameters):
    response = ec2.run_instances(**parameters)
    instances = response.get("Instances", [])
    if len(instances) != 1 or not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", instances[0].get("InstanceId", "")):
        raise ValueError("instance")
    return instances[0]["InstanceId"]


def live_instance(ec2, record, *, allow_terminated_network_absence=False):
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
    terminal = allow_terminated_network_absence and instance.get("State", {}).get("Name") == "terminated"
    subnet = instance.get("SubnetId")
    vpc = instance.get("VpcId")
    groups = [g.get("GroupId") for g in instance.get("SecurityGroups", [])]
    if (instance.get("InstanceId") != record.instance_id or instance.get("ImageId") != AMI
            or instance.get("InstanceType") != INSTANCE_TYPE
            or (subnet != SUBNET and not (terminal and subnet is None))
            or (vpc != VPC and not (terminal and vpc is None))
            or (groups != [GROUP] and not (terminal and not groups))
            or instance.get("IamInstanceProfile") or instance.get("PublicIpAddress")
            or any(tags.get(k) != v for k, v in expected.items())):
        raise ValueError("ownership")
    return instance


@router.get("")
def get_demo(principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    record = session.get(EC2Demo, 1)
    history = session.exec(select(EC2DemoHistory).where(EC2DemoHistory.owner_subject == principal.subject).order_by(EC2DemoHistory.completed_at.desc())).all()
    result = {"enabled": enabled(), "instance": None, "history": [
        {"id": item.instance_id, "name": item.instance_name or INSTANCE_NAME, "status": item.status, "created_at": item.created_at, "completed_at": item.completed_at}
        for item in history
    ]}
    if not record:
        return result
    if record.owner_subject != principal.subject:
        raise HTTPException(403, "Demo belongs to another engineer.")
    result["instance"] = {"id": record.instance_id, "name": record.instance_name or INSTANCE_NAME, "status": record.status, "message": record.message, "region": REGION}
    if record.instance_id and record.status in {"created", "terminating", "termination_submitted"}:
        try:
            instance = live_instance(clients(), record, allow_terminated_network_absence=True)
            state = instance.get("State", {}).get("Name", "unknown")
            result["instance"]["aws_state"] = state
            result["instance"]["instance_type"] = instance.get("InstanceType")
            result["instance"]["ami"] = instance.get("ImageId")
            result["instance"]["subnet"] = instance.get("SubnetId")
            result["instance"]["security_group"] = next((group.get("GroupId") for group in instance.get("SecurityGroups", []) if group.get("GroupId")), None)
            result["instance"]["name"] = next((tag.get("Value") for tag in instance.get("Tags", []) if tag.get("Key") == "Name"), record.instance_name or INSTANCE_NAME)
            result["instance"]["vpc"] = instance.get("VpcId")
            result["instance"]["private_ip"] = instance.get("PrivateIpAddress")
            result["instance"]["volumes"] = [
                {"device": mapping.get("DeviceName"), "id": mapping.get("Ebs", {}).get("VolumeId")}
                for mapping in instance.get("BlockDeviceMappings", []) if mapping.get("Ebs", {}).get("VolumeId")
            ]
            legacy_returned = record.status == "terminating" and session.exec(
                select(EC2DemoAudit.id).where(EC2DemoAudit.actor_subject == record.owner_subject,
                    EC2DemoAudit.event_type == "termination_returned", EC2DemoAudit.occurred_at >= record.created_at)
            ).first() is not None
            if state == "terminated" and (record.status == "termination_submitted" or legacy_returned):
                changed = session.exec(update(EC2Demo).where(EC2Demo.id == 1,
                    EC2Demo.operation_token == record.operation_token, EC2Demo.status == record.status)
                    .values(status="terminated").execution_options(synchronize_session=False))
                if changed.rowcount == 1:
                    audit(session, principal.subject, "termination_confirmed")
                    session.commit()
                    result["instance"]["status"] = "terminated"
                else:
                    session.rollback()
                    result["instance"]["aws_state"] = "unavailable"
        except Exception:
            session.rollback()
            result["instance"]["aws_state"] = "unavailable"
    return result


@router.post("/complete")
def complete_demo(body: Confirmation, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    if not enabled():
        raise HTTPException(503, "EC2 Demo Mode is disabled.")
    record = session.get(EC2Demo, 1)
    if not record or record.owner_subject != principal.subject or not record.instance_id:
        raise HTTPException(404, "Owned demo instance not found.")
    if body.confirmation != "COMPLETE " + record.instance_id:
        raise HTTPException(400, "Confirm the exact instance ID.")
    if record.status not in {"created", "terminated"}:
        raise HTTPException(409, "Demo operation is still running or requires operator review.")
    try:
        instance = live_instance(clients(), record, allow_terminated_network_absence=True)
        if instance.get("State", {}).get("Name") != "terminated":
            raise ValueError("not terminated")
        if not enabled():
            raise ValueError("disabled")
    except Exception:
        raise HTTPException(503, "Termination could not be verified; the reservation is retained.") from None
    history = EC2DemoHistory(operation_token=record.operation_token, reservation_id=record.id,
        owner_subject=record.owner_subject, instance_id=record.instance_id,
        message=record.message, instance_name=record.instance_name, created_at=record.created_at)
    result = session.exec(delete(EC2Demo).where(EC2Demo.id == 1,
        EC2Demo.operation_token == record.operation_token, EC2Demo.owner_subject == principal.subject,
        EC2Demo.instance_id == record.instance_id, EC2Demo.status == record.status)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        session.rollback()
        raise HTTPException(409, "Demo reservation changed; refresh its status.")
    try:
        session.add(history)
        audit(session, principal.subject, "lifecycle_completed")
        session.commit()
    except Exception:
        session.rollback()
        raise HTTPException(503, "Completion result is uncertain; refresh and review before retrying.") from None
    return {"id": history.instance_id, "status": "completed"}


@router.post("/create")
def create_demo(body: CreateConfirmation, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal), session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    require_provisioning_approval()
    if not enabled():
        raise HTTPException(503, "EC2 Demo Mode is disabled.")
    if body.confirmation != "CREATE ONE EC2 DEMO INSTANCE":
        raise HTTPException(400, "Explicit create confirmation is required.")
    instance_name = INSTANCE_NAME
    if body.review_id is not None:
        review = session.get(EC2DemoReview, body.review_id)
        if not review or review.owner_subject != principal.subject:
            raise HTTPException(404, "Reviewed configuration not found.")
        try:
            configuration = EC2Configuration.model_validate(review.configuration)
        except ValidationError:
            raise HTTPException(409, "Reviewed configuration is no longer supported.") from None
        now = datetime.now(timezone.utc)
        consumed = session.exec(update(EC2DemoReview).where(EC2DemoReview.id == body.review_id,
            EC2DemoReview.owner_subject == principal.subject, EC2DemoReview.consumed_at.is_(None),
            EC2DemoReview.expires_at > now).values(consumed_at=now).execution_options(synchronize_session=False))
        if consumed.rowcount != 1:
            session.rollback()
            raise HTTPException(409, "Reviewed configuration expired or was already used.")
        instance_name = configuration.instance_name
    token = str(uuid4())
    record = EC2Demo(id=1, owner_subject=principal.subject, operation_token=token, status="creating", instance_name=instance_name)
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
        parameters = launch_parameters(ec2, token, instance_name)
        if not enabled():
            raise ValueError("disabled")
        record.instance_id = launch_instance(ec2, parameters)
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
    instance_id = record.instance_id
    operation_token = record.operation_token
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
        ec2.terminate_instances(InstanceIds=[instance_id])
        changed = session.exec(update(EC2Demo).where(EC2Demo.id == 1,
            EC2Demo.operation_token == operation_token, EC2Demo.status == "terminating")
            .values(status="termination_submitted").execution_options(synchronize_session=False))
        if changed.rowcount != 1:
            raise ValueError("reservation changed")
        audit(session, principal.subject, "termination_returned")
        session.commit()
        return {"id": instance_id, "status": "termination_submitted"}
    except Exception as exc:
        session.rollback()
        changed = session.exec(update(EC2Demo).where(EC2Demo.id == 1,
            EC2Demo.operation_token == operation_token, EC2Demo.status == "terminating")
            .values(status="needs_operator_review", message=safe_error(exc)).execution_options(synchronize_session=False))
        if changed.rowcount == 1:
            audit(session, principal.subject, "termination_uncertain")
            session.commit()
        else:
            session.rollback()
        raise HTTPException(503, safe_error(exc)) from None
