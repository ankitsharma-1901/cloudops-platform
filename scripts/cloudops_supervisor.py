"""Local Windows service supervisor; invoked only by cloudops.ps1."""
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

MAX_LOG = 2 * 1024 * 1024
BACKUPS = 2


def write_log(stream, path):
    with stream:
        for line in iter(stream.readline, b""):
            # Do not persist query strings or common credential-bearing log lines.
            text = line.decode("utf-8", "replace").rstrip()
            if re.search(r"(?i)(authorization|password|secret|token|credential|mfa|access.key)", text):
                text = "[sensitive log line suppressed]"
            text = re.sub(r"(?<=\S)\?\S+", "?[query suppressed]", text)
            data = (time.strftime("%Y-%m-%dT%H:%M:%S%z ") + text + "\n").encode("utf-8")
            if path.exists() and path.stat().st_size + len(data) > MAX_LOG:
                for n in range(BACKUPS, 0, -1):
                    source = path if n == 1 else path.with_name(path.name + "." + str(n - 1))
                    target = path.with_name(path.name + "." + str(n))
                    if source.exists():
                        os.replace(source, target)
            with path.open("ab") as output:
                output.write(data[:MAX_LOG])


def main():
    directory = Path(sys.argv[1])
    stop_file = directory / "stop.request"
    command = sys.argv[2:]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    threads = [threading.Thread(target=write_log, args=(stream, directory / name), daemon=True)
               for stream, name in ((process.stdout, "stdout.log"), (process.stderr, "stderr.log"))]
    for thread in threads:
        thread.start()
    try:
        while process.poll() is None and not stop_file.exists():
            time.sleep(0.25)
        if process.poll() is None:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                process.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                # PID is still our live direct child; /T also stops its descendants.
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10, check=False)
                process.wait(timeout=10)
    finally:
        for thread in threads:
            thread.join(timeout=5)
        stop_file.unlink(missing_ok=True)
    return process.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
