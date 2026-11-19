"""Host-local service agreement, serialized by OS locks held for process lifetime."""

import json
import os
from contextlib import contextmanager
from pathlib import Path

from app.artifact_store import reject_links


def _lock(file, blocking=False):
    file.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))


def _unlock(file):
    file.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


@contextmanager
def service_agreement(directory: Path, service: str, fingerprint: str):
    """An active peer must match; a stopped pair may start a new configuration.

    Lock files must never be removed while services run. Process death releases OS
    locks; durable metadata is replaced only while neither service holds a slot.
    """
    if service not in ("api", "worker"):
        raise ValueError("Invalid service")
    reject_links(directory)
    directory.mkdir(parents=True, exist_ok=True)
    handles = []
    held = []
    try:
        for name in ("gate.lock", "api.lock", "worker.lock"):
            path = directory / name
            reject_links(path)
            handle = path.open("a+b")
            handles.append(handle)
        gate, api, worker = handles
        _lock(gate, blocking=True)
        held.append(gate)
        available = {}
        for name, handle in (("api", api), ("worker", worker)):
            try:
                _lock(handle)
                held.append(handle)
                available[name] = True
            except OSError:
                available[name] = False
        if not available[service]:
            raise ValueError("Service already running")
        metadata = directory / "agreement.json"
        reject_links(metadata)
        peer = "worker" if service == "api" else "api"
        if not available[peer]:
            try:
                saved = json.loads(metadata.read_text(encoding="utf-8"))
                if saved != {"version": 1, "fingerprint": fingerprint}:
                    raise ValueError("Service configuration mismatch")
            except (OSError, json.JSONDecodeError):
                raise ValueError("Service agreement unavailable") from None
        else:
            # Gate excludes other startups. A crash during this write leaves no
            # running peer, so the next startup can safely replace the file.
            temporary = directory / "agreement.tmp"
            reject_links(temporary)
            with temporary.open("w", encoding="utf-8") as output:
                json.dump({"version": 1, "fingerprint": fingerprint}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, metadata)
        peer_handle = worker if service == "api" else api
        if available[peer]:
            _unlock(peer_handle)
            held.remove(peer_handle)
        _unlock(gate)
        held.remove(gate)
        yield
    finally:
        for handle in reversed(held):
            _unlock(handle)
        for handle in reversed(handles):
            handle.close()
