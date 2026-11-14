"""Controlled Terraform execution for the trusted, local foundations lab."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

from app.schemas import CreateServerRequest, TerraformPlanSummary

TERRAFORM_FILES = ("main.tf", "variables.tf", "outputs.tf")
LAB_DIRECTORY = (
    Path(__file__).resolve().parents[2]
    / "terraform"
    / "labs"
    / "01-foundations"
)
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
    """Run fixed Terraform commands against a per-job copy of the local lab."""

    command_timeout_seconds = 60

    def plan(self, configuration: CreateServerRequest) -> TerraformPlanSummary:
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

                environment = self._environment(work_directory, configuration)
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
            resource="terraform_data.server_configuration",
            to_add=int(match.group(1)),
            to_change=int(match.group(2)),
            to_destroy=int(match.group(3)),
        )

    def _run(
        self,
        terraform: str,
        arguments: tuple[str, ...],
        phase: Literal["init", "validate", "plan"],
        work_directory: Path,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [terraform, *arguments],
                cwd=work_directory,
                env=environment,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TerraformRunnerError(phase, "timed_out") from exc
        except OSError as exc:
            raise TerraformRunnerError(phase, "command_unavailable") from exc

        if result.returncode != 0:
            raise TerraformRunnerError(phase, "command_failed")
        return result

    @staticmethod
    def _environment(
        work_directory: Path,
        configuration: CreateServerRequest,
    ) -> dict[str, str]:
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
        environment = {
            name: os.environ[name]
            for name in allowed_environment_names
            if name in os.environ
        }
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
