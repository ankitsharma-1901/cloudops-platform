"""Offline, targeted recovery. Never calls AWS/Terraform or resets consumption."""

import argparse
import getpass
import json
import re
from datetime import datetime, timezone

from sqlalchemy import text
from sqlmodel import Session, select

from app.database import create_database_engine
from app.models import Deployment, OperatorRecoveryEvent, ProvisioningJob, SavedPlanArtifact, TerraformStateOperation
from app.state_lock import STATE_KEY, owns_state, release_state
from app.deployments import audit


def inspect_operations(engine) -> dict:
    with Session(engine) as session:
        lock = session.get(TerraformStateOperation, STATE_KEY)
        return {
            "recovery_events": [{"target_id": e.target_id, "action": e.action,
                                 "operator": e.operator, "evidence_reference": e.evidence_reference}
                                for e in session.exec(select(OperatorRecoveryEvent).order_by(
                                    OperatorRecoveryEvent.occurred_at.desc()).limit(100)).all()],
            "reservation": {"owner_id": lock.owner_id, "operation": lock.operation} if lock else None,
            "deployments": [{"id": d.id, "status": d.status, "phase": d.phase,
                             "reason_code": d.reason_code, "apply_marker_present": d.apply_started_at is not None}
                            for d in session.exec(select(Deployment).where(
                                Deployment.status.in_(["queued", "running", "needs_operator_review"]) |
                                (Deployment.id == (lock.owner_id if lock else ""))
                            ).order_by(Deployment.created_at.desc()).limit(100)).all()],
            "planning_jobs": [{"id": j.id, "mode": j.plan_mode} for j in session.exec(
                select(ProvisioningJob).where((ProvisioningJob.status == "planning") |
                    (ProvisioningJob.id == (lock.owner_id if lock else ""))).limit(100)).all()],
        }


def recover_operation(engine, kind: str, target_id: str, action: str, *, workers_stopped: bool,
                      terraform_stopped: bool, evidence: str, reconciled: bool = False) -> None:
    """Stopped-process/evidence attestations are operator duties, never inferred."""
    if (not workers_stopped or not terraform_stopped or kind not in {"plan", "deployment"}
            or action not in {"mark", "release"} or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", evidence)):
        raise ValueError("Recovery requires stopped processes and a non-secret evidence reference.")
    with Session(engine) as session:
        session.connection().execute(text("BEGIN IMMEDIATE"))
        row = session.get(Deployment if kind == "deployment" else ProvisioningJob, target_id)
        if row is None:
            raise ValueError("Recovery target not found.")
        requires_lock = kind == "deployment" or row.plan_mode in ("aws_development", "aws_ec2_only")
        if requires_lock and not owns_state(session, target_id):
            raise ValueError("The exact target must own the AWS reservation.")
        if action == "mark":
            if row.status != ("running" if kind == "deployment" else "planning"):
                raise ValueError("Only an interrupted active operation may be marked.")
            if kind == "deployment":
                uncertain = row.apply_started_at is not None
                row.status = "needs_operator_review" if uncertain else "failed"
                row.phase = "interrupted"
                row.reason_code = "needs_operator_review" if uncertain else "interrupted_before_apply"
                row.message = "Interrupted operation; reservation retained pending operator reconciliation. No automatic retry."
                row.finished_at = datetime.now(timezone.utc)
                artifact = session.get(SavedPlanArtifact, row.artifact_id)
                if artifact and artifact.consumed_at is None:
                    artifact.consumed_at = datetime.now(timezone.utc)
                    session.add(artifact)
                audit(session, row, row.reason_code, "operator-recovery")
            else:
                row.status = "failed"
                row.error_message = "Planning interrupted; operator inspection required before releasing any AWS reservation. Generate a new plan."
        else:
            if not reconciled or row.status not in {"failed", "needs_operator_review"}:
                raise ValueError("Release requires a marked terminal operation and state/resource reconciliation.")
            if not requires_lock:
                raise ValueError("Local plans have no AWS reservation to release.")
            if kind == "deployment":
                row.status = "failed"
                row.phase = "operator_reconciled"
                row.reason_code = "reconciled_no_retry"
                row.message = "Operator reconciled the outcome. This attempt cannot be retried; generate a new plan."
                audit(session, row, "reconciled_no_retry", "operator-recovery")
            release_state(session, target_id)
        session.add(row)
        session.add(OperatorRecoveryEvent(target_id=target_id, action=f"{kind}_{action}",
                                         operator=getpass.getuser(), evidence_reference=evidence))
        session.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline targeted inspection/reconciliation. No AWS commands.")
    parser.add_argument("action", choices=("inspect", "mark", "release"))
    parser.add_argument("--kind", choices=("plan", "deployment"))
    parser.add_argument("--id")
    parser.add_argument("--confirm-workers-stopped", action="store_true")
    parser.add_argument("--confirm-terraform-stopped", action="store_true")
    parser.add_argument("--confirm-state-and-resources-reconciled", action="store_true")
    parser.add_argument("--evidence", default="")
    args = parser.parse_args(argv)
    engine = create_database_engine()
    try:
        if args.action == "inspect":
            print(json.dumps(inspect_operations(engine), indent=2))
        else:
            recover_operation(engine, args.kind, args.id, args.action,
                              workers_stopped=args.confirm_workers_stopped,
                              terraform_stopped=args.confirm_terraform_stopped,
                              reconciled=args.confirm_state_and_resources_reconciled, evidence=args.evidence)
            print("Recorded targeted operator recovery. No AWS commands or execution retry occurred.")
    except ValueError:
        parser.exit(2, "Recovery refused: check target, reservation, stopped-process attestations and reconciliation evidence.\n")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
