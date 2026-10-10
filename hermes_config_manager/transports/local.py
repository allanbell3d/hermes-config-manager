"""Local folders, Windows mapped drives and UNC paths (SMB via the OS)."""

from __future__ import annotations

import ntpath
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import BinaryIO

from .base import FileEntry, ReadResult, Revision, Transport, TransportError, classify_exception


class LocalTransport(Transport):
    kind = "local"

    def join(self, *parts: str) -> str:
        return os.path.join(*parts)

    def parent(self, path: str) -> str:
        return os.path.dirname(path)

    def basename(self, path: str) -> str:
        return os.path.basename(path)

    def probe(self) -> str:
        root = self.host.hermes_root
        try:
            if not os.path.isdir(root):
                if os.path.exists(root):
                    raise TransportError("invalid", f"{root} is not a folder")
                drive = ntpath.splitdrive(root)[0]
                if drive and not os.path.exists(drive + "\\"):
                    raise TransportError("unavailable", f"{drive} is not connected")
                raise TransportError("not_found", f"{root} does not exist")
        except TransportError:
            raise
        except OSError as exc:
            raise classify_exception(exc, root) from None
        return f"{root} reachable"

    def list_dir(self, path: str) -> list[FileEntry]:
        try:
            entries = []
            with os.scandir(path) as it:
                for item in it:
                    is_dir = item.is_dir(follow_symlinks=True)
                    try:
                        st = item.stat(follow_symlinks=True)
                        size, mtime = (0 if is_dir else st.st_size), datetime.fromtimestamp(st.st_mtime, timezone.utc)
                    except OSError:
                        size, mtime = 0, None
                    entries.append(FileEntry(item.name, item.path, size, mtime, is_dir))
            return sorted(entries, key=lambda e: (not e.is_dir, e.name.casefold()))
        except OSError as exc:
            raise classify_exception(exc, path) from None

    def stat(self, path: str) -> FileEntry:
        try:
            st = os.stat(path)
        except OSError as exc:
            raise classify_exception(exc, path) from None
        is_dir = os.path.isdir(path)
        return FileEntry(os.path.basename(path), path, 0 if is_dir else st.st_size,
                         datetime.fromtimestamp(st.st_mtime, timezone.utc), is_dir)

    def read_bytes(self, path: str) -> ReadResult:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
                mtime = os.fstat(handle.fileno()).st_mtime
        except OSError as exc:
            raise classify_exception(exc, path) from None
        return ReadResult(data, Revision.of(data, mtime))

    def open_read(self, path: str) -> BinaryIO:
        try:
            return open(path, "rb")
        except OSError as exc:
            raise classify_exception(exc, path) from None

    def copy_file(self, source: str, target: str) -> None:
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            raise classify_exception(exc, target) from None

    def replace_bytes(self, path: str, data: bytes) -> None:
        folder = os.path.dirname(path) or "."
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.hm-", suffix=".tmp", dir=folder)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                shutil.copymode(path, tmp)
            except OSError:
                pass  # new file or mode not representable (SMB) - keep defaults
            os.replace(tmp, path)
            tmp = None
        except OSError as exc:
            raise classify_exception(exc, path) from None
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def run(self, command: str, timeout: float = 20.0):
        """Files come from SMB; commands go to the same machine over SSH."""
        from .ssh import run_ssh_command

        return run_ssh_command(self.host, command, timeout)
