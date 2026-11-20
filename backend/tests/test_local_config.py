"""Offline service startup pins; no actual database or AWS CLI execution."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from app import local_config
from app.database import create_database_engine


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    config = json.loads((local_config.ROOT / "config/local.example.json").read_text())
    config.update(account_id="123456789012", bucket="cloudops-test-state")
    (tmp_path / "backend").mkdir()
    with closing(sqlite3.connect(tmp_path / "backend/cloudops.db")) as connection:
        for table in ("server_requests", "provisioning_jobs", "approval_requests"):
            connection.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY)")
    path = tmp_path / "local.json"
    path.write_text(json.dumps(config))
    monkeypatch.setattr(local_config, "ROOT", tmp_path)
    monkeypatch.setenv("CLOUDOPS_LOCAL_CONFIG", str(path))
    for name in ("DATABASE_URL", "CLOUDOPS_ARTIFACT_ROOT", "CLOUDOPS_ENABLE_AWS_APPLY", "CLOUDOPS_ENABLE_EC2_DEMO", "CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID",
                 "CLOUDOPS_AWS_STATE_BUCKET", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_EC2_METADATA_DISABLED",
                 "OIDC_ISSUER", "OIDC_AUDIENCE", "OIDC_JWKS_URL", "OIDC_ALLOWED_ALGORITHMS", "OIDC_ACCESS_TOKEN_TYPES", "CLOUDOPS_ROLE_ASSIGNMENTS", "CLOUDOPS_EC2_SECURITY_GROUP_ID"):
        monkeypatch.delenv(name, raising=False)
        # Track restoration even when the loader sets values directly.
        monkeypatch.setenv(name, "unused"); monkeypatch.delenv(name)
    return path, config


def test_api_worker_share_pins_and_apply_is_always_disabled(manifest):
    first = local_config.load_local_config()
    second = local_config.load_local_config()
    assert first == second
    assert first["CLOUDOPS_ENABLE_AWS_APPLY"] == "false"
    assert first["CLOUDOPS_ENABLE_EC2_DEMO"] == "false"
    assert first["DATABASE_URL"].endswith("/backend/cloudops.db")
    assert first["CLOUDOPS_ARTIFACT_ROOT"].endswith(str(Path("backend/.private/plans")))
    with pytest.raises(ValueError):
        create_database_engine("sqlite:///another.db")


@pytest.mark.parametrize("field,value", [("database", "other.db"), ("artifact_root", "frontend/public/plans"),
    ("region", "us-east-1"), ("account_id", ""), ("enable_aws_apply", True), ("backend_key", "another/key"),
    ("roles", {"wrong-issuer|user": ["engineer"]}), ("secret", "must-not-be-accepted")])
def test_invalid_manifest_fails_closed(manifest, field, value):
    path, config = manifest
    config[field] = value; path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="configuration"):
        local_config.load_local_config()


@pytest.mark.parametrize("name,value", [("DATABASE_URL", "sqlite:///other.db"), ("CLOUDOPS_ENABLE_AWS_APPLY", "true"),
    ("CLOUDOPS_AWS_EXPECTED_ACCOUNT_ID", "999999999999"), ("AWS_PROFILE", "administrator"),
    ("CLOUDOPS_ROLE_ASSIGNMENTS", "{}")])
def test_environment_conflicts_rejected(manifest, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        local_config.load_local_config()


def test_missing_database_is_not_silently_recreated(manifest):
    path, _ = manifest
    (path.parent / "backend/cloudops.db").unlink()
    with pytest.raises(ValueError):
        local_config.load_local_config()
    assert not (path.parent / "backend/cloudops.db").exists()


def test_unrelated_database_is_rejected(manifest):
    path, _ = manifest
    with closing(sqlite3.connect(path.parent / "backend/cloudops.db")) as connection:
        connection.execute("DROP TABLE approval_requests")
    with pytest.raises(ValueError):
        local_config.load_local_config()


@pytest.mark.parametrize("service", ["api", "worker"])
def test_both_launchers_validate_before_starting_same_pinned_services(manifest, monkeypatch, service):
    import sys
    import uvicorn
    from app import aws_execution, local_start, worker
    path, _ = manifest
    calls = []
    monkeypatch.setattr(aws_execution, "execution_identity", lambda: calls.append("validated"))
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append("api"))
    monkeypatch.setattr(worker, "run_worker", lambda: calls.append("worker"))
    monkeypatch.setattr(sys, "argv", ["local_start", "--config", str(path), service])
    local_start.main()
    assert calls == ["validated", service]


def test_bad_backend_prevents_any_service_start(manifest, monkeypatch):
    import sys
    from app import aws_execution, local_start, worker
    from app.artifact_store import ArtifactError
    path, _ = manifest
    def reject():
        raise ArtifactError()
    monkeypatch.setattr(aws_execution, "execution_identity", reject)
    monkeypatch.setattr(worker, "run_worker", lambda: pytest.fail("Worker must not start"))
    monkeypatch.setattr(sys, "argv", ["local_start", "--config", str(path), "worker"])
    with pytest.raises(SystemExit) as exit_error:
        local_start.main()
    assert exit_error.value.code == 2


def test_enabled_requires_fixed_target(manifest):
    path, config = manifest
    config.update(enable_aws_apply=True, account_id="294665681966", bucket="cloudops-tfstate-ca1")
    path.write_text(json.dumps(config))
    assert local_config.load_local_config()["CLOUDOPS_ENABLE_AWS_APPLY"] == "true"


@pytest.mark.parametrize("service", ["api", "worker"])
def test_demo_launcher_requires_explicit_confirmation(manifest, monkeypatch, service):
    import sys
    from app import local_start
    path, config = manifest
    config.update(enable_ec2_demo=True, account_id="294665681966")
    path.write_text(json.dumps(config))
    monkeypatch.setattr(sys, "argv", ["local_start", "--config", str(path), service])
    with pytest.raises(SystemExit) as error:
        local_start.main()
    assert error.value.code == 2


@pytest.mark.parametrize("service", ["api", "worker"])
def test_demo_launcher_accepts_explicit_confirmation(manifest, monkeypatch, service):
    import sys
    import uvicorn
    from app import aws_execution, local_start, worker
    path, config = manifest
    config.update(enable_ec2_demo=True, account_id="294665681966")
    path.write_text(json.dumps(config))
    calls = []
    monkeypatch.setattr(aws_execution, "execution_identity", lambda: ("account", "backend", "config"))
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append("api"))
    monkeypatch.setattr(worker, "run_worker", lambda: calls.append("worker"))
    monkeypatch.setattr(sys, "argv", ["local_start", "--config", str(path), "--confirm-ec2-demo", service])
    local_start.main()
    assert calls == [service]


@pytest.mark.parametrize("service", ["api", "worker"])
def test_enabled_launcher_requires_confirmation(manifest, monkeypatch, service):
    import sys
    from app import local_start
    path, config = manifest
    config.update(enable_aws_apply=True, account_id="294665681966", bucket="cloudops-tfstate-ca1")
    path.write_text(json.dumps(config))
    monkeypatch.setattr(sys, "argv", ["local_start", "--config", str(path), service])
    with pytest.raises(SystemExit) as error:
        local_start.main()
    assert error.value.code == 2


@pytest.mark.parametrize("service", ["api", "worker"])
def test_enabled_launcher_accepts_explicit_confirmation(manifest, monkeypatch, service):
    import sys
    import uvicorn
    from app import aws_execution, local_start, worker
    path, config = manifest
    config.update(enable_aws_apply=True, account_id="294665681966", bucket="cloudops-tfstate-ca1")
    path.write_text(json.dumps(config))
    calls = []
    monkeypatch.setattr(aws_execution, "execution_identity", lambda: ("account", "backend", "config"))
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append("api"))
    monkeypatch.setattr(worker, "run_worker", lambda: calls.append("worker"))
    monkeypatch.setattr(sys, "argv", ["local_start", "--config", str(path), "--confirm-aws-apply", service])
    local_start.main()
    assert calls == [service]


def test_service_agreement_matching_and_mismatch(tmp_path):
    from app.local_agreement import service_agreement
    directory = tmp_path / "agreement"
    with service_agreement(directory, "api", "enabled-target-a"):
        with service_agreement(directory, "worker", "enabled-target-a"):
            pass
        for other in ("disabled-target-a", "enabled-target-b"):
            with pytest.raises(ValueError):
                with service_agreement(directory, "worker", other):
                    pytest.fail("Mismatched worker started")
        with pytest.raises(ValueError):
            with service_agreement(directory, "api", "enabled-target-a"):
                pytest.fail("Duplicate API started")
    with service_agreement(directory, "worker", "disabled-target-a"):
        pass


def test_stopped_disabled_pair_can_restart_enabled_without_removing_metadata(tmp_path):
    from app.local_agreement import service_agreement
    directory = tmp_path / "agreement"
    identity = ("account", "backend", "configuration")
    disabled = local_config.configuration_fingerprint({"CLOUDOPS_ENABLE_AWS_APPLY": "false", "execution_identity": identity})
    enabled = local_config.configuration_fingerprint({"CLOUDOPS_ENABLE_AWS_APPLY": "true", "execution_identity": identity})
    with service_agreement(directory, "api", disabled):
        with service_agreement(directory, "worker", disabled):
            pass
        with pytest.raises(ValueError, match="mismatch"):
            with service_agreement(directory, "worker", enabled):
                pytest.fail("Enabled worker joined disabled API")
    # The old durable metadata remains; with both slots free it may be replaced.
    assert json.loads((directory / "agreement.json").read_text())["fingerprint"] == disabled
    with service_agreement(directory, "api", enabled):
        with service_agreement(directory, "worker", enabled):
            pass


def test_service_agreement_releases_after_interrupted_start(tmp_path):
    from app.local_agreement import service_agreement
    directory = tmp_path / "agreement"
    with pytest.raises(RuntimeError):
        with service_agreement(directory, "api", "old"):
            raise RuntimeError("startup failed")
    with service_agreement(directory, "worker", "new"):
        pass


def test_service_agreement_recovers_after_process_exit(tmp_path):
    import subprocess
    import sys
    from app.local_agreement import service_agreement
    directory = tmp_path / "agreement"
    # On Windows a venv python.exe is a launcher that starts a second process.
    # Terminating the launcher does not necessarily terminate the lock owner.
    code = ("import json,sys,time; sys.path[:0]=json.loads(sys.argv[2]); from pathlib import Path; "
            "from app.local_agreement import service_agreement; "
            "guard=service_agreement(Path(sys.argv[1]), 'api', 'old'); "
            "guard.__enter__(); print('ready', flush=True); time.sleep(60)")
    interpreter = getattr(sys, "_base_executable", sys.executable) if sys.platform == "win32" else sys.executable
    child = subprocess.Popen([interpreter, "-u", "-c", code, str(directory), json.dumps(sys.path)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(ValueError):
            with service_agreement(directory, "worker", "different"):
                pytest.fail("Conflicting worker started")
    finally:
        child.terminate()
        child.wait(timeout=10)
    with service_agreement(directory, "worker", "new"):
        pass


def test_active_peer_with_missing_agreement_fails_closed(tmp_path):
    from app.local_agreement import service_agreement
    directory = tmp_path / "agreement"
    with service_agreement(directory, "api", "same"):
        (directory / "agreement.json").unlink()
        with pytest.raises(ValueError):
            with service_agreement(directory, "worker", "same"):
                pytest.fail("Worker started without agreement")


def test_additive_diagnostic_migration_preserves_existing_deployment(database_url):
    from sqlalchemy import text
    from sqlmodel import Session
    from app.database import initialize_database
    from app.models import Deployment
    engine = create_database_engine(database_url)
    initialize_database(engine)
    with Session(engine) as session:
        session.add(Deployment(id="existing", request_id="r", job_id="j", artifact_id="a", approval_id="p",
                               artifact_digest="d", plan_revision="v", requester="u", approver="a", initiator="u"))
        session.commit()
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE deployments DROP COLUMN phase"))
        connection.execute(text("ALTER TABLE deployments DROP COLUMN reason_code"))
    initialize_database(engine)
    initialize_database(engine)
    with Session(engine) as session:
        row = session.get(Deployment, "existing")
        assert row.status == "queued" and row.phase is None and row.reason_code is None
    engine.dispose()
