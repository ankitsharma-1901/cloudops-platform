"""Controlled Terraform planning for two fixed, trusted configurations."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from app.schemas import (
    CreateServerRequest,
    PlanMode,
    TerraformPlanSummary,
    TerraformResourceChange,
)

TERRAFORM_FILES = ("main.tf", "variables.tf", "outputs.tf")
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
LAB_DIRECTORY = (
    REPOSITORY_ROOT
    / "terraform"
    / "labs"
    / "01-foundations"
)
AWS_DIRECTORY = REPOSITORY_ROOT / "terraform" / "aws" / "dev"
AWS_BACKEND_METADATA = AWS_DIRECTORY / ".terraform" / "terraform.tfstate"
AWS_PROFILE = "cloudops-deploy"
AWS_REGION = "ca-central-1"
AWS_ROLE_NAME = "cloudops-terraform-deploy"
EXPECTED_ACCOUNT_ENV = "CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID"
PLAN_COUNTS = re.compile(
    r"(?m)^Plan: (\d+) to add, (\d+) to change, (\d+) to destroy\.$"
)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class TerraformRunnerError(RuntimeError):
    """A safe-to-handle Terraform failure without raw CLI output."""

    def __init__(self, phase: str, reason: str) -> None:
        self.phase = phase
        self.reason = reason
        super().__init__(f"Terraform {phase} failed ({reason}).")


class TerraformPlanRunner:
    """Run fixed plan-only commands for a selected server-controlled target."""

    command_timeout_seconds = 60
    aws_plan_timeout_seconds = 180
    credential_timeout_seconds = 20

    def plan(
        self,
        configuration: CreateServerRequest,
        plan_mode: PlanMode = "local",
    ) -> TerraformPlanSummary:
        if plan_mode == "aws_development":
            return self._plan_aws_development()
        return self._plan_local(configuration)

    def _plan_local(
        self,
        configuration: CreateServerRequest,
    ) -> TerraformPlanSummary:
        terraform = shutil.which("terraform")
        if terraform is None:
            raise TerraformRunnerError("init", "terraform_unavailable")

        try:
            with tempfile.TemporaryDirectory(
                prefix="cloudops-terraform-plan-"
            ) as temporary_directory:
                work_directory = Path(temporary_directory)
                for filename in TERRAFORM_FILES:
                    shutil.copyfile(
                        LAB_DIRECTORY / filename,
                        work_directory / filename,
                    )

                environment = self._local_environment(work_directory, configuration)
                self._run(
                    terraform,
                    ("init", "-input=false"),
                    "init",
                    work_directory,
                    environment,
                )
                self._run(
                    terraform,
                    ("validate",),
                    "validate",
                    work_directory,
                    environment,
                )
                result = self._run(
                    terraform,
                    ("plan", "-input=false"),
                    "plan",
                    work_directory,
                    environment,
                )
        except OSError as exc:
            raise TerraformRunnerError("init", "working_directory_failed") from exc

        plain_output = ANSI_ESCAPE.sub("", result.stdout)
        match = PLAN_COUNTS.search(plain_output)
        if match is None:
            raise TerraformRunnerError("plan", "unrecognized_plan_summary")

        return TerraformPlanSummary(
            plan_mode="local",
            plan_revision=hashlib.sha256(
                json.dumps(
                    {
                        "configuration": configuration.model_dump(mode="json"),
                        "plan_output": plain_output,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            resource="terraform_data.server_configuration",
            resource_changes=[
                TerraformResourceChange(
                    address="terraform_data.server_configuration",
                    actions=["create" if int(match.group(1)) else "no-op"],
                )
            ],
            to_add=int(match.group(1)),
            to_change=int(match.group(2)),
            to_destroy=int(match.group(3)),
        )

    def _plan_aws_development(self) -> TerraformPlanSummary:
        terraform = shutil.which("terraform")
        aws = shutil.which("aws")
        if terraform is None:
            raise TerraformRunnerError("preflight", "terraform_unavailable")
        if aws is None:
            raise TerraformRunnerError("credentials", "aws_cli_unavailable")
        self._verify_backend_metadata()

        expected_account_id = os.getenv(EXPECTED_ACCOUNT_ENV, "")
        if not re.fullmatch(r"[0-9]{12}", expected_account_id):
            raise TerraformRunnerError("identity", "expected_account_missing")

        environment = self._aws_environment(expected_account_id)
        identity = self._run(
            aws,
            (
                "sts",
                "get-caller-identity",
                "--profile",
                AWS_PROFILE,
                "--region",
                AWS_REGION,
                "--output",
                "json",
                "--no-cli-pager",
            ),
            "credentials",
            AWS_DIRECTORY,
            environment,
            timeout=self.credential_timeout_seconds,
        )
        self._verify_identity(identity.stdout, expected_account_id)
        self._run(
            terraform,
            ("validate", "-no-color"),
            "validate",
            AWS_DIRECTORY,
            environment,
        )

        try:
            with tempfile.TemporaryDirectory(
                prefix="cloudops-aws-plan-"
            ) as temporary_directory:
                plan_path = Path(temporary_directory) / "aws-development.tfplan"
                self._run(
                    terraform,
                    (
                        "plan",
                        "-input=false",
                        "-no-color",
                        "-lock-timeout=5s",
                        f"-out={plan_path}",
                    ),
                    "plan",
                    AWS_DIRECTORY,
                    environment,
                    timeout=self.aws_plan_timeout_seconds,
                )
                shown = self._run(
                    terraform,
                    ("show", "-json", str(plan_path)),
                    "show",
                    AWS_DIRECTORY,
                    environment,
                )
                return self._summarize_aws_plan(shown.stdout)
        except OSError as exc:
            raise TerraformRunnerError("plan", "plan_artifact_failed") from exc

    def _run(
        self,
        executable: str,
        arguments: tuple[str, ...],
        phase: Literal[
            "init", "preflight", "credentials", "identity", "validate", "plan", "show"
        ],
        work_directory: Path,
        environment: dict[str, str],
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [executable, *arguments],
                cwd=work_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout or self.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            reason = "interaction_required" if phase == "credentials" else "timed_out"
            raise TerraformRunnerError(phase, reason) from exc
        except OSError as exc:
            raise TerraformRunnerError(phase, "command_unavailable") from exc

        if result.returncode != 0:
            reason = "credentials_unavailable" if phase == "credentials" else "command_failed"
            raise TerraformRunnerError(phase, reason)
        return result

    @staticmethod
    def _verify_identity(raw_identity: str, expected_account_id: str) -> None:
        try:
            identity = json.loads(raw_identity)
            account = identity["Account"]
            arn = identity["Arn"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise TerraformRunnerError("identity", "unrecognized_identity") from exc

        expected_arn = re.compile(
            rf"^arn:aws:sts::{re.escape(expected_account_id)}:assumed-role/"
            rf"{re.escape(AWS_ROLE_NAME)}/[^/]+$"
        )
        if account != expected_account_id or expected_arn.fullmatch(arn) is None:
            raise TerraformRunnerError("identity", "unexpected_identity")

    @staticmethod
    def _verify_backend_metadata() -> None:
        try:
            metadata = json.loads(AWS_BACKEND_METADATA.read_text(encoding="utf-8"))
            backend = metadata["backend"]
            backend_type = backend["type"]
            backend_config = backend["config"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise TerraformRunnerError("preflight", "backend_not_initialized") from exc

        if (
            backend_type != "s3"
            or backend_config.get("region") != AWS_REGION
            or backend_config.get("key") != "cloudops/development/terraform.tfstate"
        ):
            raise TerraformRunnerError("preflight", "unexpected_backend")

    @staticmethod
    def _summarize_aws_plan(raw_plan: str) -> TerraformPlanSummary:
        try:
            plan = json.loads(raw_plan)
            raw_changes = plan.get("resource_changes", [])
        except (json.JSONDecodeError, AttributeError) as exc:
            raise TerraformRunnerError("show", "unrecognized_plan") from exc

        changes: list[TerraformResourceChange] = []
        to_add = to_change = to_destroy = 0
        for change in raw_changes:
            try:
                address = change["address"]
                actions = change["change"]["actions"]
            except (KeyError, TypeError) as exc:
                raise TerraformRunnerError("show", "unrecognized_plan") from exc
            if not isinstance(address, str) or not isinstance(actions, list):
                raise TerraformRunnerError("show", "unrecognized_plan")
            if actions in (["no-op"], ["read"]):
                continue
            changes.append(TerraformResourceChange(address=address, actions=actions))
            to_add += int("create" in actions)
            to_change += int("update" in actions)
            to_destroy += int("delete" in actions)

        return TerraformPlanSummary(
            plan_mode="aws_development",
            plan_revision=hashlib.sha256(raw_plan.encode("utf-8")).hexdigest(),
            resource_changes=changes,
            to_add=to_add,
            to_change=to_change,
            to_destroy=to_destroy,
        )

    @staticmethod
    def _base_environment() -> dict[str, str]:
        allowed_environment_names = (
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "HOMEDRIVE",
            "HOMEPATH",
        )
        return {
            name: os.environ[name]
            for name in allowed_environment_names
            if name in os.environ
        }

    @classmethod
    def _local_environment(
        cls,
        work_directory: Path,
        configuration: CreateServerRequest,
    ) -> dict[str, str]:
        environment = cls._base_environment()
        environment["TF_IN_AUTOMATION"] = "1"
        environment["TF_DATA_DIR"] = str(work_directory / ".terraform")
        terraform_variables = {
            "name": configuration.name,
            "environment": configuration.environment,
            "region": configuration.region,
            "instance_type": configuration.instance_type,
            "install_nginx": configuration.install_nginx,
            "install_docker": configuration.install_docker,
        }
        for field, value in terraform_variables.items():
            environment[f"TF_VAR_{field}"] = (
                str(value).lower() if isinstance(value, bool) else str(value)
            )
        return environment

    @classmethod
    def _aws_environment(cls, expected_account_id: str) -> dict[str, str]:
        environment = cls._base_environment()
        environment.update(
            {
                "AWS_PROFILE": AWS_PROFILE,
                "AWS_REGION": AWS_REGION,
                "AWS_DEFAULT_REGION": AWS_REGION,
                "AWS_CLI_AUTO_PROMPT": "off",
                "AWS_PAGER": "",
                "TF_IN_AUTOMATION": "1",
                "TF_INPUT": "0",
                "TF_VAR_expected_account_id": expected_account_id,
            }
        )
        return environment
