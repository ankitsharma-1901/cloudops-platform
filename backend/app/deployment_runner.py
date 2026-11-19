"""Only the exact approved binary can cross this fixed apply boundary."""

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

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


class DeploymentRunner(tf.TerraformPlanRunner):
    apply_timeout_seconds = 600

    def prepare(self, artifact, deployment_id: str) -> PreparedDeployment:
        if not apply_enabled():
            raise ArtifactError()
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
        # Independent final switch and integrity/context checks immediately before subprocess.
        if not apply_enabled() or execution_identity() != prepared.identity:
            raise ArtifactError()
        prepared.store.read(prepared.execution_id, prepared.digest)
        self._run(prepared.terraform, (
            "apply", "-input=false", "-no-color", "-lock-timeout=5s", str(prepared.path),
        ), "apply", tf.AWS_DIRECTORY, prepared.environment, timeout=self.apply_timeout_seconds)
        outputs = self._run(prepared.terraform, ("output", "-json"), "output", tf.AWS_DIRECTORY, prepared.environment)
        return self.safe_ids(outputs.stdout)

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
