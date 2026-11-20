"""Only the exact approved binary can cross this fixed apply boundary."""

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app import terraform_runner as tf
from app.artifact_store import ArtifactError, ArtifactStore
from app.aws_execution import apply_enabled, execution_identity, require_create_only


@dataclass
class PreparedDeployment:
    path: Path
    digest: str
    environment: dict[str, str]
    terraform: str
    identity: tuple[str, str, str]
    store: ArtifactStore
    execution_id: str
    stack: str = "aws_development"
    group: str = ""
    aws: str = ""
    pre_apply_check: Callable[[], None] | None = None


class DeploymentRunner(tf.TerraformPlanRunner):
    apply_timeout_seconds = 600

    def prepare(self, artifact, deployment_id: str) -> PreparedDeployment:
        if not apply_enabled():
            raise ArtifactError()
        if artifact.stack_id == "aws_ec2_only":
            return self._prepare_ec2(artifact, deployment_id)
        identity = execution_identity()
        if identity != (artifact.account_id, artifact.backend_identity, artifact.configuration_identity):
            raise ArtifactError()
        store = ArtifactStore()
        data = store.read(artifact.id, artifact.digest)
        # A private execution copy prevents replacement of the archived file between checks.
        store.write(deployment_id, data)
        path = store.path(deployment_id)
        terraform, aws = shutil.which("terraform"), shutil.which("aws")
        if not terraform or not aws:
            raise ArtifactError()
        environment = self._aws_environment(artifact.account_id)
        identity_result = self._run(aws, (
            "sts", "get-caller-identity", "--profile", tf.AWS_PROFILE,
            "--region", tf.AWS_REGION, "--output", "json", "--no-cli-pager",
        ), "credentials", tf.AWS_DIRECTORY, environment, timeout=self.credential_timeout_seconds)
        self._verify_identity(identity_result.stdout, artifact.account_id)
        self._run(aws, (
            "s3api", "list-objects-v2", "--bucket", os.environ["CLOUDOPS_AWS_STATE_BUCKET"],
            "--prefix", "cloudops/development/terraform.tfstate", "--max-keys", "1", "--no-paginate",
            "--query", "KeyCount", "--output", "json",
            "--expected-bucket-owner", artifact.account_id,
            "--profile", tf.AWS_PROFILE, "--region", tf.AWS_REGION, "--no-cli-pager",
        ), "preflight", tf.AWS_DIRECTORY, environment, timeout=self.credential_timeout_seconds)
        self._run(terraform, ("validate", "-no-color"), "validate", tf.AWS_DIRECTORY, environment)
        shown = self._run(terraform, ("show", "-json", str(path)), "show", tf.AWS_DIRECTORY, environment)
        require_create_only(shown.stdout)
        summary = self._summarize_aws_plan(shown.stdout).model_dump()
        if summary != artifact.summary:
            raise ArtifactError()
        return PreparedDeployment(path, artifact.digest, environment, terraform, identity, store, deployment_id)

    def execute(self, prepared: PreparedDeployment) -> dict[str, str]:
        if not apply_enabled():
            raise ArtifactError()
        if prepared.stack == "aws_ec2_only":
            return self._execute_ec2(prepared)
        # Independent final switch and integrity/context checks immediately before subprocess.
        if not apply_enabled() or execution_identity() != prepared.identity:
            raise ArtifactError()
        prepared.store.read(prepared.execution_id, prepared.digest)
        self._run(prepared.terraform, (
            "apply", "-input=false", "-no-color", "-lock-timeout=5s", str(prepared.path),
        ), "apply", tf.AWS_DIRECTORY, prepared.environment, timeout=self.apply_timeout_seconds)
        outputs = self._run(prepared.terraform, ("output", "-json"), "output", tf.AWS_DIRECTORY, prepared.environment)
        return self.safe_ids(outputs.stdout)

    def _ec2_current_state(self, aws, environment):
        from app.ec2_only_stack import ACCOUNT, BUCKET, ROOT, STATE_KEY
        listing = self._run(aws, ("s3api", "list-objects-v2", "--bucket", BUCKET, "--prefix", STATE_KEY,
            "--expected-bucket-owner", ACCOUNT, "--profile", tf.EC2_PROFILE, "--region", tf.AWS_REGION,
            "--output", "json", "--no-cli-pager"), "preflight", ROOT, environment)
        try:
            keys = [item["Key"] for item in json.loads(listing.stdout).get("Contents", [])]
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ArtifactError() from None
        if STATE_KEY not in keys:
            return None
        return self._run(aws, ("s3", "cp", f"s3://{BUCKET}/{STATE_KEY}", "-", "--profile", tf.EC2_PROFILE,
            "--region", tf.AWS_REGION, "--no-cli-pager"), "preflight", ROOT, environment).stdout

    def _ec2_identity_and_network(self, aws, environment, group):
        from app.ec2_only_stack import ACCOUNT, ROOT, verify_network
        caller = self._run(aws, ("sts", "get-caller-identity", "--profile", tf.EC2_PROFILE,
            "--region", tf.AWS_REGION, "--output", "json", "--no-cli-pager"),
            "credentials", ROOT, environment, timeout=self.credential_timeout_seconds)
        self._verify_identity(caller.stdout, ACCOUNT, tf.EC2_ROLE_NAME)
        verify_network(self, aws, environment, group)

    def _prepare_ec2(self, artifact, deployment_id):
        from app.ec2_only_policy import require_one_instance
        from app.ec2_only_stack import ACCOUNT, ROOT, ROOT_ID, STACK, STATE_KEY, network_identity, stack_identity
        from app.ec2_apply_verification import verify_prior_state
        group = os.getenv("CLOUDOPS_EC2_SECURITY_GROUP_ID", "")
        identity = stack_identity(group)
        if (identity != (artifact.account_id, artifact.backend_identity, artifact.configuration_identity)
                or artifact.stack_id != STACK or artifact.terraform_root != ROOT_ID or artifact.state_key != STATE_KEY
                or artifact.network_identity != network_identity(group)):
            raise ArtifactError()
        store = ArtifactStore()
        data = store.read(artifact.id, artifact.digest)
        store.write(deployment_id, data)
        path = store.path(deployment_id)
        terraform, aws = shutil.which("terraform"), shutil.which("aws")
        if not terraform or not aws:
            raise ArtifactError()
        environment = self._aws_environment(ACCOUNT, tf.EC2_PROFILE)
        environment["TF_VAR_security_group_id"] = group
        self._ec2_identity_and_network(aws, environment, group)
        current = self._ec2_current_state(aws, environment)
        verify_prior_state(data, current)
        self._run(terraform, ("validate", "-no-color"), "validate", ROOT, environment)
        shown = self._run(terraform, ("show", "-json", str(path)), "show", ROOT, environment)
        require_one_instance(shown.stdout, group)
        if self._summarize_aws_plan(shown.stdout, plan_mode=STACK).model_dump() != artifact.summary:
            raise ArtifactError()
        return PreparedDeployment(path, artifact.digest, environment, terraform, identity, store, deployment_id, STACK, group, aws)

    def _execute_ec2(self, prepared):
        from app.ec2_only_stack import ROOT, stack_identity
        from app.ec2_apply_verification import state_instance_id, verify_completed_instance, verify_prior_state
        if not apply_enabled() or stack_identity(prepared.group) != prepared.identity:
            raise ArtifactError()
        data = prepared.store.read(prepared.execution_id, prepared.digest)
        self._ec2_identity_and_network(prepared.aws, prepared.environment, prepared.group)
        verify_prior_state(data, self._ec2_current_state(prepared.aws, prepared.environment))
        if not apply_enabled() or stack_identity(prepared.group) != prepared.identity:
            raise ArtifactError()
        prepared.store.read(prepared.execution_id, prepared.digest)
        if prepared.pre_apply_check is None:
            raise ArtifactError()
        prepared.pre_apply_check()
        prepared.store.read(prepared.execution_id, prepared.digest)
        self._run(prepared.terraform, ("apply", "-input=false", "-no-color", "-lock-timeout=5s", str(prepared.path)),
                  "apply", ROOT, prepared.environment, timeout=self.apply_timeout_seconds)
        if stack_identity(prepared.group) != prepared.identity:
            raise ArtifactError()
        self._ec2_identity_and_network(prepared.aws, prepared.environment, prepared.group)
        state = self._ec2_current_state(prepared.aws, prepared.environment)
        if state is None:
            raise ArtifactError()
        instance_id = state_instance_id(state)
        instances = self._run(prepared.aws, ("ec2", "describe-instances", "--instance-ids", instance_id,
            "--profile", tf.EC2_PROFILE, "--region", tf.AWS_REGION, "--output", "json", "--no-cli-pager"),
            "preflight", ROOT, prepared.environment, timeout=self.credential_timeout_seconds)
        return {"instance_id": verify_completed_instance(state, instances.stdout, prepared.group)}

    @staticmethod
    def safe_ids(raw: str) -> dict[str, str]:
        try:
            values = json.loads(raw)
            safe = {}
            for name, prefix in (("instance_id", "i"), ("vpc_id", "vpc"), ("subnet_id", "subnet")):
                output = values.get(name, {})
                value = output.get("value")
                if output.get("sensitive") is False and isinstance(value, str) and re.fullmatch(rf"{prefix}-[0-9a-f]{{8,17}}", value):
                    safe[name] = value
            return safe
        except (ValueError, AttributeError, TypeError):
            raise ArtifactError() from None
