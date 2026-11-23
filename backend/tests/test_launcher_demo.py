import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell is unavailable")
@pytest.mark.parametrize("enabled,confirmed,expected_phase", [
    (False, False, "Local prerequisites"),
    (True, True, "Local prerequisites"),
    (True, False, "Private manifest and AWS safety switches"),
    (False, True, "Private manifest and AWS safety switches"),
])
def test_launcher_requires_matching_demo_confirmation(tmp_path, enabled, confirmed, expected_phase):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = Path(__file__).resolve().parents[2] / "scripts" / "cloudops.ps1"
    shutil.copy(source, scripts / source.name)
    (tmp_path / ".cloudops.local.json").write_text(json.dumps({
        "enable_aws_apply": False, "enable_ec2_demo": enabled,
    }))
    command = ["powershell", "-NoProfile", "-File", str(scripts / source.name), "start"]
    if confirmed:
        command.append("-ConfirmEc2Demo")
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert expected_phase + " startup failed" in result.stdout
