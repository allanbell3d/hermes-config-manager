"""Transport contract shared by local/SMB, SSH/SFTP and mock hosts.

Every operation is blocking and is expected to run in a worker thread
(:mod:`hermes_config_manager.services.tasks`).  Errors are normalised into
:class:`TransportError` with a ``kind`` the UI can present consistently.
"""

from __future__ import annotations

import hashlib
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Sequence


class TransportError(OSError):
    """``kind``: unavailable | timeout | auth | not_found | permission |
    conflict | uncertain | unsupported | invalid | other."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind

    def __str__(self) -> str:  # keep messages short and credential-free
        return self.args[0] if self.args else self.kind


class ConflictError(TransportError):
    def __init__(self, message: str) -> None:
        super().__init__("conflict", message)


@dataclass(frozen=True)
class Revision:
    """Identity of one file state; ``sha256`` is authoritative."""

    size: int
    mtime: float | None
    sha256: str

    @classmethod
    def of(cls, data: bytes, mtime: float | None = None) -> "Revision":
        return cls(len(data), mtime, hashlib.sha256(data).hexdigest())

    @property
    def short(self) -> str:
        return self.sha256[:10]


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    size: int = 0
    modified: datetime | None = None
    is_dir: bool = False


@dataclass(frozen=True)
class ReadResult:
    data: bytes
    revision: Revision


@dataclass(frozen=True)
class CommandResult:
    command: str
    returncode: int
    stdout: str
    stderr: str
    elapsed: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class WriteResult:
    revision: Revision
    backup_path: str | None
    verified: bool


class Transport:
    """Blocking file/command access to one host."""

    kind = "base"

    def __init__(self, host) -> None:  # host: hermes_config_manager.transports.hosts.HostConfig
        self.host = host

    # --- paths ------------------------------------------------------------
    def join(self, *parts: str) -> str:
        raise NotImplementedError

    def parent(self, path: str) -> str:
        raise NotImplementedError

    def basename(self, path: str) -> str:
        raise NotImplementedError

    # --- files ------------------------------------------------------------
    def probe(self) -> str:
        """Cheap reachability check of the Hermes root; returns a description."""
        raise NotImplementedError

    def list_dir(self, path: str) -> list[FileEntry]:
        raise NotImplementedError

    def stat(self, path: str) -> FileEntry:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        try:
            self.stat(path)
            return True
        except TransportError as exc:
            if exc.kind == "not_found":
                return False
            raise

    def read_bytes(self, path: str) -> ReadResult:
        raise NotImplementedError

    def open_read(self, path: str) -> BinaryIO:
        raise NotImplementedError

    def copy_file(self, source: str, target: str) -> None:
        raise NotImplementedError

    def replace_bytes(self, path: str, data: bytes) -> None:
        """Atomically replace (or create) ``path`` with ``data``."""
        raise NotImplementedError

    def rename(self, source: str, target: str) -> None:
        """Rename ``source`` to ``target``; refuses (``conflict``) when ``target`` exists."""
        raise NotImplementedError

    # --- commands ---------------------------------------------------------
    def run(self, command: str, timeout: float = 20.0) -> CommandResult:
        raise TransportError("unsupported", f"Commands are not available for host '{self.host.name}'.")

    def close(self) -> None:
        pass

    # --- composite operations (shared by all transports) ------------------
    def backup(self, path: str) -> str:
        """Copy ``path`` next to itself as ``<name>.bak_<timestamp>``; returns the copy's path."""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        candidate = f"{path}.bak_{stamp}"
        suffix = 2
        while self.exists(candidate):
            candidate = f"{path}.bak_{stamp}_{suffix}"
            suffix += 1
        self.copy_file(path, candidate)
        copied = self.read_bytes(candidate).revision
        original = self.read_bytes(path).revision
        if copied.sha256 != original.sha256:
            raise TransportError("other", f"Backup verification failed for {candidate}.")
        return candidate

    def write_checked(self, path: str, data: bytes, expected_sha: str | None, make_backup: bool = True) -> WriteResult:
        """Safe write: conflict check → backup → atomic replace → readback.

        ``expected_sha`` is the revision the change was prepared from; ``None``
        means the file must not exist yet.
        """
        current: ReadResult | None
        try:
            current = self.read_bytes(path)
        except TransportError as exc:
            if exc.kind != "not_found":
                raise
            current = None
        if expected_sha is None and current is not None:
            raise ConflictError(f"{path} appeared since it was loaded.")
        if expected_sha is not None:
            if current is None:
                raise ConflictError(f"{path} was deleted since it was loaded.")
            if current.revision.sha256 != expected_sha:
                raise ConflictError(f"{path} changed on the host since it was loaded (external edit).")
        backup_path = self.backup(path) if (make_backup and current is not None) else None
        self.replace_bytes(path, data)
        readback = self.read_bytes(path)
        expected = Revision.of(data)
        if readback.revision.sha256 != expected.sha256:
            raise TransportError(
                "uncertain",
                f"Readback of {path} does not match what was written; backup kept at {backup_path}.",
            )
        return WriteResult(readback.revision, backup_path, True)


def classify_exception(exc: BaseException, what: str) -> TransportError:
    """Map OS/paramiko errors to a :class:`TransportError` without leaking secrets."""
    if isinstance(exc, TransportError):
        return exc
    name = type(exc).__name__
    text = str(exc)
    lowered = f"{name} {text}".lower()
    if isinstance(exc, FileNotFoundError) or "no such file" in lowered or getattr(exc, "errno", None) == 2:
        return TransportError("not_found", f"{what}: not found")
    if isinstance(exc, PermissionError) or "permission denied" in lowered and "publickey" not in lowered:
        return TransportError("permission", f"{what}: permission denied")
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in lowered or "timeout" in lowered:
        return TransportError("timeout", f"{what}: timed out")
    if "authentication" in lowered or "publickey" in lowered or "auth" in name.lower():
        return TransportError("auth", f"{what}: authentication failed")
    if isinstance(exc, (ConnectionError, socket.gaierror)) or "unreachable" in lowered or "network" in lowered \
            or "winerror 53" in lowered or "winerror 64" in lowered or "winerror 67" in lowered \
            or "winerror 1231" in lowered or "no route" in lowered or "refused" in lowered:
        return TransportError("unavailable", f"{what}: host unavailable ({name})")
    return TransportError("other", f"{what}: {name}: {text[:200]}")


def discover_config_paths(transport: Transport, root: str, globs: Sequence[str]) -> list[tuple[str, str]]:
    """Return ``(profile_name, config_path)`` for the main agent and every profile.

    ``profile_name`` is ``""`` for the main ``<root>/config.yaml``.  Only the
    documented layouts are supported: ``config.yaml`` and
    ``profiles/*/config.yaml`` (the Hermes layout).
    """
    found: list[tuple[str, str]] = []
    main = transport.join(root, "config.yaml")
    if "config.yaml" in globs and transport.exists(main):
        found.append(("", main))
    if any(g.startswith("profiles/") for g in globs):
        profiles_dir = transport.join(root, "profiles")
        try:
            entries = transport.list_dir(profiles_dir)
        except TransportError as exc:
            if exc.kind != "not_found":
                raise
            entries = []
        for entry in sorted(entries, key=lambda e: e.name.casefold()):
            if not entry.is_dir or entry.name.startswith("."):
                continue
            path = transport.join(entry.path, "config.yaml")
            if transport.exists(path):
                found.append((entry.name, path))
    return found
