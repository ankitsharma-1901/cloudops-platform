import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Windows PowerShell is unavailable")
def test_readiness_accepts_response_slower_than_two_seconds(tmp_path):
    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(3)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        launcher = (Path(__file__).resolve().parents[2] / "scripts" / "cloudops.ps1").read_text()
        function = launcher[launcher.index("function Wait-Http("):launcher.index("function Start-ServiceProcess(")]
        script = tmp_path / "readiness.ps1"
        script.write_text(function + f"\nWait-Http 'http://127.0.0.1:{server.server_port}' 8 'test'\n")
        result = subprocess.run(["powershell", "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
