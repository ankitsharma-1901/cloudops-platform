"""Private binary plan storage. Paths are generated internally, never accepted by APIs."""

import hashlib
import os
import stat
from functools import lru_cache
from pathlib import Path
from uuid import UUID

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".private" / "plans"
MAX_PLAN_BYTES = 64 * 1024 * 1024


class ArtifactError(ValueError):
    def __init__(self):
        super().__init__("Saved plan is unavailable or failed integrity/security validation.")


def reject_links(path: Path) -> None:
    """Reject symlinks and Windows reparse points, including parent junctions."""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ArtifactError()


@lru_cache(maxsize=1)
def _windows_sid() -> str:
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(handle)):
        raise ArtifactError()
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(handle, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(handle, 1, buffer, size, ctypes.byref(size)):
            raise ArtifactError()
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        value = ctypes.c_void_p()
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise ArtifactError()
        try:
            return ctypes.wstring_at(value)
        finally:
            kernel.LocalFree(value)
    finally:
        kernel.CloseHandle(handle)


def restrict(path: Path, directory: bool = False) -> None:
    reject_links(path)
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600, follow_symlinks=False)
        return
    # Atomically replace the DACL, rather than Windows chmod (which is not an ACL).
    import ctypes
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    inherit = "OICI" if directory else ""
    sddl = f"D:P(A;{inherit};FA;;;{_windows_sid()})"
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ArtifactError()
    try:
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise ArtifactError()
    finally:
        kernel.LocalFree(descriptor)


class ArtifactStore:
    def __init__(self, root: Path | None = None):
        self.root = (root or Path(os.getenv("CLOUDOPS_ARTIFACT_ROOT", str(DEFAULT_ROOT)))).absolute()
        reject_links(self.root)
        # Create/restrict each missing directory before putting any data in it.
        missing = []
        parent = self.root
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
            restrict(directory, directory=True)
        restrict(self.root, directory=True)

    def path(self, artifact_id: str) -> Path:
        try:
            if str(UUID(artifact_id)) != artifact_id:
                raise ValueError
        except (ValueError, AttributeError):
            raise ArtifactError() from None
        reject_links(self.root)
        path = self.root / f"{artifact_id}.tfplan"
        reject_links(path)
        return path

    def write(self, artifact_id: str, data: bytes) -> str:
        if not data or len(data) > MAX_PLAN_BYTES:
            raise ArtifactError()
        path = self.path(artifact_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(path, flags, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                restrict(path)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            raise ArtifactError() from None
        return hashlib.sha256(data).hexdigest()

    def read(self, artifact_id: str, digest: str) -> bytes:
        path = self.path(artifact_id)
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_PLAN_BYTES:
                raise ArtifactError()
            restrict(path)
            with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)), "rb") as source:
                opened = os.fstat(source.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ArtifactError()
                data = source.read(MAX_PLAN_BYTES + 1)
        except OSError:
            raise ArtifactError() from None
        if not data or len(data) > MAX_PLAN_BYTES or hashlib.sha256(data).hexdigest() != digest:
            raise ArtifactError()
        return data
