"""Shared entry point for API, continuous worker and offline recovery."""

import argparse
import logging
import os
from contextlib import ExitStack
from pathlib import Path

from app.local_config import load_local_config, configuration_fingerprint


def main():
    parser = argparse.ArgumentParser(description="Start CloudOps from one private service manifest.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--confirm-aws-apply", action="store_true")
    parser.add_argument("service", choices=("api", "worker", "recovery"))
    args, remaining = parser.parse_known_args()
    os.environ["CLOUDOPS_LOCAL_CONFIG"] = str(args.config.absolute())
    try:
        values = load_local_config()
        if args.service != "recovery" and (values["CLOUDOPS_ENABLE_AWS_APPLY"] == "true") != args.confirm_aws_apply:
            raise ValueError("Explicit confirmation must match enabled mode")
        if args.service == "recovery" and args.confirm_aws_apply:
            raise ValueError("Recovery does not enable apply")
        if args.service != "recovery":
            from app.aws_execution import execution_identity
            identity = execution_identity()  # Recovery remains available with broken backend metadata.
    except ValueError:
        parser.exit(2, "CloudOps refused incompatible local/backend configuration. No service started.\n")
    logging.basicConfig(level=logging.INFO)
    if args.service == "recovery":
        from app.deployment_recovery import main as recovery
        recovery(remaining)
        return
    if remaining:
        parser.error("Unexpected service arguments")
    from app.local_agreement import service_agreement
    from app.local_config import ROOT
    fingerprint = configuration_fingerprint({**values, "execution_identity": identity})
    with ExitStack() as stack:
        try:
            stack.enter_context(service_agreement(ROOT / "backend" / ".private" / "service-agreement", args.service, fingerprint))
        except (OSError, ValueError):
            parser.exit(2, "CloudOps refused incompatible or unavailable service agreement. No service started.\n")
        logging.info("Validated service configuration fingerprint=%s", fingerprint)
        if args.service == "api":
            import uvicorn
            uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
        else:
            from app.worker import run_worker
            run_worker()


if __name__ == "__main__":
    main()
