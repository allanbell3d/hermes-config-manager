"""In-memory host for tests and benchmarks: latency, failures, hangs, disconnects.

Paths are POSIX-style strings.  Fault injection is keyed by operation name
(``read``, ``write``, ``list``, ``stat``, ``copy``, ``run``) and optionally
a path substring, so tests can fail exactly one agent in a bulk apply.
"""

from __future__ import annotations

import io
import posixpath
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import BinaryIO, Callable

from .base import CommandResult, FileEntry, ReadResult, Revision, Transport, TransportError


@dataclass
class Fault:
    op: str
    path_contains: str = ""
    kind: str = "other"           # TransportError kind, or 'hang'
    remaining: int = 1_000_000    # how many times it fires
    message: str = "injected failure"


@dataclass
class MockFS:
    files: dict[str, bytes] = field(default_factory=dict)
    mtimes: dict[str, float] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def put(self, path: str, data: bytes | str) -> None:
        with self.lock:
            self.files[path] = data.encode("utf-8") if isinstance(data, str) else data
            self.mtimes[path] = time.time()

    def dirs(self) -> set[str]:
        out = set()
        for path in self.files:
            parent = posixpath.dirname(path)
            while parent and parent not in out:
                out.add(parent)
                if parent == "/":
                    break
                parent = posixpath.dirname(parent)
        return out


class MockTransport(Transport):
    kind = "mock"

    def __init__(self, host, fs: MockFS | None = None, latency: float = 0.0) -> None:
        super().__init__(host)
        self.fs = fs or MockFS()
        self.latency = latency
        self.faults: list[Fault] = []
        self.connected = True
        self.release = threading.Event()   # set to unblock injected hangs
        self.calls: list[tuple[str, str]] = []
        self.commands: list[str] = []
        self.command_handler: Callable[[str], CommandResult] | None = None

    # --- fault machinery --------------------------------------------------
    def inject(self, op: str, kind: str = "other", path_contains: str = "", times: int = 1_000_000,
               message: str = "injected failure") -> Fault:
        fault = Fault(op, path_contains, kind, times, message)
        self.faults.append(fault)
        return fault

    def _enter(self, op: str, path: str) -> None:
        self.calls.append((op, path))
        if self.latency:
            time.sleep(self.latency)
        if not self.connected:
            raise TransportError("unavailable", f"{self.host.name}: disconnected")
        for fault in self.faults:
            if fault.remaining > 0 and fault.op == op and fault.path_contains in path:
                fault.remaining -= 1
                if fault.kind == "hang":
                    self.release.wait(60)
                    raise TransportError("timeout", f"{path}: hung operation released")
                raise TransportError(fault.kind, f"{path}: {fault.message}")

    # --- paths ------------------------------------------------------------
    def join(self, *parts: str) -> str:
        return posixpath.join(*parts)

    def parent(self, path: str) -> str:
        return posixpath.dirname(path)

    def basename(self, path: str) -> str:
        return posixpath.basename(path)

    # --- files ------------------------------------------------------------
    def probe(self) -> str:
        self._enter("probe", self.host.hermes_root)
        if self.host.hermes_root not in self.fs.dirs():
            raise TransportError("not_found", f"{self.host.hermes_root} does not exist")
        return "mock reachable"

    def list_dir(self, path: str) -> list[FileEntry]:
        self._enter("list", path)
        with self.fs.lock:
            dirs = self.fs.dirs()
            if path not in dirs:
                raise TransportError("not_found", f"{path}: not found")
            names: dict[str, FileEntry] = {}
            prefix = path.rstrip("/") + "/"
            for file_path, data in self.fs.files.items():
                if file_path.startswith(prefix):
                    rest = file_path[len(prefix):]
                    head = rest.split("/", 1)[0]
                    child = prefix + head
                    if "/" in rest:
                        names[head] = FileEntry(head, child, 0, None, True)
                    else:
                        names[head] = FileEntry(head, child, len(data),
                                                datetime.fromtimestamp(self.fs.mtimes[file_path], timezone.utc), False)
            return sorted(names.values(), key=lambda e: (not e.is_dir, e.name.casefold()))

    def stat(self, path: str) -> FileEntry:
        self._enter("stat", path)
        with self.fs.lock:
            if path in self.fs.files:
                return FileEntry(posixpath.basename(path), path, len(self.fs.files[path]),
                                 datetime.fromtimestamp(self.fs.mtimes[path], timezone.utc), False)
            if path in self.fs.dirs():
                return FileEntry(posixpath.basename(path), path, 0, None, True)
        raise TransportError("not_found", f"{path}: not found")

    def read_bytes(self, path: str) -> ReadResult:
        self._enter("read", path)
        with self.fs.lock:
            if path not in self.fs.files:
                raise TransportError("not_found", f"{path}: not found")
            data = self.fs.files[path]
            return ReadResult(data, Revision.of(data, self.fs.mtimes[path]))

    def open_read(self, path: str) -> BinaryIO:
        return io.BytesIO(self.read_bytes(path).data)

    def copy_file(self, source: str, target: str) -> None:
        self._enter("copy", target)
        with self.fs.lock:
            if source not in self.fs.files:
                raise TransportError("not_found", f"{source}: not found")
            self.fs.put(target, self.fs.files[source])

    def replace_bytes(self, path: str, data: bytes) -> None:
        self._enter("write", path)
        self.fs.put(path, data)

    def rename(self, source: str, target: str) -> None:
        self._enter("rename", source)
        with self.fs.lock:
            if source not in self.fs.files:
                raise TransportError("not_found", f"{source}: not found")
            if target in self.fs.files:
                raise TransportError("conflict", f"{target} already exists")
            self.fs.files[target] = self.fs.files.pop(source)
            self.fs.mtimes[target] = self.fs.mtimes.pop(source, time.time())

    def run(self, command: str, timeout: float = 20.0) -> CommandResult:
        self._enter("run", command)
        self.commands.append(command)
        if self.command_handler is not None:
            return self.command_handler(command)
        return CommandResult(command, 0, "active\n", "", 0.0)
