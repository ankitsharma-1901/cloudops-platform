"""Read-only AWS inventory; Terraform state is inspected in memory only."""

import json
import os
import re
import shutil

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session, select

from app.auth import AuthenticatedPrincipal, get_authenticated_principal, require_role
from app.database import get_session
from app.ec2_only_stack import ACCOUNT, BUCKET, STATE_KEY, stack_identity
from app.models import Deployment, ProvisioningJob
from app.terraform_runner import AWS_DIRECTORY, AWS_PROFILE, AWS_REGION, TerraformPlanRunner

router = APIRouter(prefix="/api/v1", tags=["infrastructure"])


def _read_json(runner, executable, args, env):
    result = runner._run(executable, tuple(args), "preflight", AWS_DIRECTORY, env, timeout=7)
    return json.loads(result.stdout)


def read_inventory():
    """Only fixed read operations; no raw AWS responses or state leave this function."""
    result = {"aws_status": "unavailable", "state_status": "unknown", "instances": []}
    runner = TerraformPlanRunner()
    aws = shutil.which("aws")
    if not aws:
        return result
    env = runner._aws_environment(ACCOUNT)
    try:
        identity = _read_json(runner, aws, ["sts", "get-caller-identity", "--profile", AWS_PROFILE,
            "--region", AWS_REGION, "--output", "json", "--no-cli-pager"], env)
        runner._verify_identity(json.dumps(identity), ACCOUNT)
        data = _read_json(runner, aws, ["ec2", "describe-instances", "--profile", AWS_PROFILE,
            "--region", AWS_REGION, "--output", "json", "--no-cli-pager"], env)
        instances = [i for r in data["Reservations"] for i in r["Instances"]]
        if not isinstance(instances, list):
            raise ValueError()
        result["aws_status"] = "available"
    except Exception:
        return result

    managed = None
    try:
        stack_identity(os.getenv("CLOUDOPS_EC2_SECURITY_GROUP_ID", ""))
        listing = _read_json(runner, aws, ["s3api", "list-objects-v2", "--bucket", BUCKET,
            "--prefix", STATE_KEY, "--expected-bucket-owner", ACCOUNT, "--profile", AWS_PROFILE,
            "--region", AWS_REGION, "--output", "json", "--no-cli-pager"], env)
        if not any(o.get("Key") == STATE_KEY for o in listing.get("Contents", [])):
            raise ValueError("State object is absent")
        state = _read_json(runner, aws, ["s3", "cp", f"s3://{BUCKET}/{STATE_KEY}", "-",
            "--profile", AWS_PROFILE, "--region", AWS_REGION, "--no-cli-pager"], env)
        if state.get("version") != 4 or not isinstance(state.get("resources"), list):
            raise ValueError("State format unavailable")
        managed = set()
        for resource in state["resources"]:
            if resource.get("mode") != "managed":
                continue
            if resource.get("type") != "aws_instance" or resource.get("name") != "dev" or resource.get("module"):
                raise ValueError("Unexpected managed resource")
            for entry in resource.get("instances", []):
                if entry.get("deposed") or "index_key" in entry:
                    raise ValueError("Unexpected state instance")
                instance_id = entry.get("attributes", {}).get("id", "")
                if not re.fullmatch(r"i-[0-9a-f]{8}(?:[0-9a-f]{9})?", instance_id):
                    raise ValueError("Invalid state identity")
                managed.add(instance_id)
        if len(managed) > 1:
            raise ValueError("Unexpected instance count")
        result["state_status"] = "available"
    except Exception:
        managed = None

    for instance in instances:
        instance_id = instance.get("InstanceId", "")
        if not re.fullmatch(r"i-[0-9a-f]{8}(?:[0-9a-f]{9})?", instance_id):
            continue
        names = [t.get("Value", "") for t in instance.get("Tags", []) if t.get("Key") == "Name"]
        ownership = "unknown" if managed is None else "managed" if instance_id in managed else "unmanaged"
        result["instances"].append({"id": instance_id, "name": str(names[0])[:128] if names else "",
            "state": instance.get("State", {}).get("Name", "unknown"), "region": AWS_REGION,
            "instance_type": instance.get("InstanceType", "unknown"), "subnet_id": instance.get("SubnetId"),
            "security_groups": [g.get("GroupId") for g in instance.get("SecurityGroups", []) if g.get("GroupId")],
            "ownership": ownership, "stack": "aws_ec2_only" if ownership == "managed" else None})
    return result


@router.get("/infrastructure")
def infrastructure(response: Response, principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
                   session: Session = Depends(get_session)):
    require_role(principal, "engineer")
    response.headers["Cache-Control"] = "no-store"
    inventory = read_inventory()
    deployments = session.exec(select(Deployment, ProvisioningJob).join(
        ProvisioningJob, ProvisioningJob.id == Deployment.job_id).where(
        Deployment.requester == principal.subject).order_by(Deployment.created_at.desc()).limit(100)).all()
    latest = {}
    activity = []
    for deployment, job in deployments:
        item = {"id": deployment.id, "status": deployment.status, "stack": job.plan_mode,
                "created_at": deployment.created_at.isoformat()}
        if len(activity) < 10:
            activity.append(item)
        instance_id = (deployment.result or {}).get("instance_id")
        if instance_id and instance_id not in latest:
            latest[instance_id] = item
    for instance in inventory["instances"]:
        instance["latest_deployment"] = latest.get(instance["id"])
    inventory["recent_deployments"] = activity
    return inventory
