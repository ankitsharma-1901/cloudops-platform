"""Allowlisted diagnostics only; exception messages and CLI output are never stored."""

from app.artifact_store import ArtifactError
from app.terraform_runner import TerraformRunnerError


class DeploymentBlocked(ArtifactError):
    def __init__(self, reason_code):
        super().__init__()
        self.reason_code = reason_code if reason_code == "execution_disabled" else "preflight_failed"


def failure_code(error: Exception, crossed_boundary: bool) -> tuple[str, str]:
    if crossed_boundary:
        return "apply_started", "needs_operator_review"
    if isinstance(error, DeploymentBlocked):
        return "eligibility", error.reason_code
    if isinstance(error, TerraformRunnerError):
        return {
            "credentials": ("credential_preflight", "credential_preflight_failed"),
            "identity": ("credential_preflight", "credential_preflight_failed"),
            "preflight": ("backend_preflight", "backend_preflight_denied"),
            "validate": ("configuration_preflight", "configuration_validation_failed"),
            "show": ("plan_preflight", "plan_integrity_failed"),
        }.get(error.phase, ("preflight", "preflight_failed"))
    return "preflight", "plan_integrity_failed" if isinstance(error, ArtifactError) else "preflight_failed"
