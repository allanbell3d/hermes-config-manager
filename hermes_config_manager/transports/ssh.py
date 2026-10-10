"""SSH/SFTP host access and remote commands.

* Files over SFTP (paramiko), one connection per host, serialised by a lock
  (paramiko SFTP channels are not safe for concurrent use).
* Commands (gateway control, diagnostics) through the Windows OpenSSH client
  with ``BatchMode=yes`` (never prompts for a password).
  It uses the operator's existing keys/agent/``~/.ssh/config``; the
  application never sees or stores a password.
* ``~/.ssh/config`` is read for both: the host's SSH address may be a ``Host``
  alias, and fields left empty in the app (user, port, key) come from the
  config (:func:`resolve_endpoint`).  Fields filled in the app win, like
  command-line options do in ``ssh``.  ``ProxyCommand`` works for file access;
  ``ProxyJump`` only for commands (OpenSSH handles it).
* Server keys: only servers in ``~/.ssh/known_hosts`` are trusted.  A first
  connection to an unknown server raises :class:`UnknownHostKey` with the key's
  fingerprint; the Hosts screen asks the user, and :func:`trust_host_key`
  adds the key to ``~/.ssh/known_hosts`` (which OpenSSH uses too).
"""

from __future__ import annotations

import posixpath
import shlex
import stat as statmod
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .base import (
    CommandResult,
    FileEntry,
    ReadResult,
    Revision,
    Transport,
    TransportError,
    classify_exception,
)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UnknownHostKey(TransportError):
    """A server whose key is not in ``~/.ssh/known_hosts`` yet: never trusted without asking."""

    def __init__(self, entry: str, key_type: str, key_b64: str, fingerprint: str) -> None:
        super().__init__("auth", f"Unknown server key for {entry} ({fingerprint}). "
                                 "Open Hosts and press Test to check and trust it.")
        self.entry, self.key_type, self.key_b64, self.fingerprint = entry, key_type, key_b64, fingerprint


def fingerprint(key_bytes: bytes) -> str:
    """OpenSSH-style SHA256 fingerprint (what ``ssh-keygen -lf`` prints)."""
    import base64
    import hashlib

    return "SHA256:" + base64.b64encode(hashlib.sha256(key_bytes).digest()).decode("ascii").rstrip("=")


def known_hosts_file() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


def trust_host_key(err: UnknownHostKey, path: Path | None = None) -> Path:
    """Add the server key of ``err`` to ``known_hosts`` (the user said yes)."""
    target = path or known_hosts_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.read_bytes() if target.exists() else b""
    line = f"{err.entry} {err.key_type} {err.key_b64}\n".encode("ascii")
    with open(target, "ab") as handle:
        if old and not old.endswith(b"\n"):
            handle.write(b"\n")
        handle.write(line)
    return target


def ssh_config_file() -> Path:
    return Path.home() / ".ssh" / "config"


@dataclass(frozen=True)
class Endpoint:
    """Where an SSH host really is, after ``~/.ssh/config``."""

    alias: str
    hostname: str
    port: int
    user: str | None
    keys: tuple[str, ...]
    identities_only: bool = False
    proxy_command: str | None = None
    proxy_jump: str | None = None
    from_config: bool = False

    def describe(self) -> str:
        text = f"{self.user + '@' if self.user else ''}{self.hostname}:{self.port}"
        return text + (" (from ~/.ssh/config)" if self.from_config else "")


def resolve_endpoint(host, config_path: Path | None = None) -> Endpoint:
    """The host's SSH address, user, port and keys, filling empty fields from ``~/.ssh/config``.

    The app's port field cannot tell "22" from "not set", so only a port other than 22 overrides the config.
    """
    alias = host.ssh_hostname or host.name
    path = config_path or ssh_config_file()
    found: dict = {}
    if path.is_file():
        import paramiko

        try:
            found = paramiko.SSHConfig.from_path(str(path)).lookup(alias)
        except Exception as exc:
            raise TransportError("invalid", f"Could not read {path}: {exc}") from None
    explicit_port = int(host.ssh_port) if host.ssh_port and int(host.ssh_port) != 22 else None
    try:
        port = explicit_port or int(found.get("port") or 22)
    except ValueError:
        raise TransportError("invalid", f"{path}: Port of {alias} is not a number") from None
    if host.ssh_key:
        keys = (host.ssh_key,)
    else:
        keys = tuple(k for k in found.get("identityfile") or () if Path(k).is_file())
    hostname = found.get("hostname") or alias
    from_config = hostname != alias or any(k in found for k in ("user", "port", "identityfile", "proxycommand",
                                                                "proxyjump"))
    return Endpoint(alias, hostname, port, host.ssh_user or found.get("user"), keys,
                    str(found.get("identitiesonly", "")).lower() == "yes",
                    found.get("proxycommand") or None, found.get("proxyjump") or None, from_config)


def _ask_first_policy():
    import paramiko

    class AskFirst(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            raise UnknownHostKey(hostname, key.get_name(), key.get_base64(), fingerprint(key.asbytes()))

    return AskFirst()


def ssh_command_line(host, remote_command: str, connect_timeout: int = 6) -> list[str]:
    if not host.can_run_commands:
        raise TransportError("unsupported", f"Host '{host.name}' has no SSH endpoint configured.")
    args = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={connect_timeout}",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=2",
    ]
    # ssh reads ~/.ssh/config itself: pass only what the app's fields set, so an alias keeps its settings.
    if host.ssh_port and int(host.ssh_port) != 22:
        args += ["-p", str(int(host.ssh_port))]
    if host.ssh_user:
        args += ["-l", host.ssh_user]
    if host.ssh_key:
        import os

        if os.path.exists(host.ssh_key):
            args += ["-i", host.ssh_key]
    args += [host.ssh_hostname, remote_command]
    return args


def run_ssh_command(host, remote_command: str, timeout: float = 20.0) -> CommandResult:
    args = ssh_command_line(host, remote_command, connect_timeout=max(3, int(min(timeout, 10))))
    started = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise TransportError("timeout", f"ssh {host.ssh_hostname}: command timed out after {timeout:.0f}s") from None
    except FileNotFoundError:
        raise TransportError("unsupported", "OpenSSH client (ssh.exe) not found") from None
    elapsed = time.monotonic() - started
    if proc.returncode == 255:
        err = (proc.stderr or "").lower()
        if "permission denied" in err or "publickey" in err:
            raise TransportError("auth", f"ssh {host.ssh_hostname}: authentication failed")
        if "timed out" in err:
            raise TransportError("timeout", f"ssh {host.ssh_hostname}: connection timed out")
        raise TransportError("unavailable", f"ssh {host.ssh_hostname}: {(proc.stderr or 'connection failed').strip()[:200]}")
    return CommandResult(remote_command, proc.returncode, proc.stdout, proc.stderr, elapsed)


def connect_kwargs(ep: Endpoint, timeout: float) -> dict:
    """paramiko ``connect()`` arguments for an endpoint (IdentitiesOnly: only its keys, no key search)."""
    t = min(timeout, 10)
    kwargs = dict(hostname=ep.hostname, port=ep.port, username=ep.user, timeout=t, banner_timeout=t,
                  auth_timeout=t, allow_agent=True, look_for_keys=not (ep.identities_only and ep.keys))
    if ep.keys:
        kwargs["key_filename"] = list(ep.keys)
    return kwargs


class SSHTransport(Transport):
    kind = "ssh"

    def __init__(self, host) -> None:
        super().__init__(host)
        self._lock = threading.RLock()
        self._client = None
        self._sftp = None

    # --- connection -------------------------------------------------------
    def _connect(self):
        if self._sftp is not None:
            transport = self._client.get_transport() if self._client else None
            if transport is not None and transport.is_active():
                return self._sftp
            self.close()
        import paramiko

        ep = resolve_endpoint(self.host)
        if ep.proxy_jump and not ep.proxy_command:
            raise TransportError("unsupported", f"{ep.alias}: ProxyJump in ~/.ssh/config is not supported for "
                                                "file access (ProxyCommand is). Gateway commands still work.")
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(_ask_first_policy())   # unknown server: ask the user first
        kwargs = connect_kwargs(ep, self.host.timeout)
        if ep.proxy_command:
            kwargs["sock"] = paramiko.ProxyCommand(ep.proxy_command)
        try:
            client.connect(**kwargs)
            sftp = client.open_sftp()
            sftp.get_channel().settimeout(self.host.timeout)
        except Exception as exc:
            try:
                client.close()
            except Exception:
                pass
            raise classify_exception(exc, f"ssh {self.host.ssh_hostname}") from None
        self._client, self._sftp = client, sftp
        return sftp

    def _call(self, what: str, fn):
        with self._lock:
            sftp = self._connect()
            try:
                return fn(sftp)
            except TransportError:
                raise
            except Exception as exc:
                err = classify_exception(exc, what)
                if err.kind in ("unavailable", "timeout", "other"):
                    self.close()  # force reconnect next time
                raise err from None

    def close(self) -> None:
        with self._lock:
            for obj in (self._sftp, self._client):
                if obj is not None:
                    try:
                        obj.close()
                    except Exception:
                        pass
            self._sftp = self._client = None

    # --- paths ------------------------------------------------------------
    def join(self, *parts: str) -> str:
        return posixpath.join(*parts)

    def parent(self, path: str) -> str:
        return posixpath.dirname(path)

    def basename(self, path: str) -> str:
        return posixpath.basename(path)

    # --- files ------------------------------------------------------------
    def probe(self) -> str:
        root = self.host.hermes_root
        if not root:
            raise TransportError("invalid", "Remote Hermes root is not configured")
        entry = self.stat(root)
        if not entry.is_dir:
            raise TransportError("invalid", f"{root} is not a directory")
        return f"{self.host.ssh_hostname}:{root} reachable"

    def list_dir(self, path: str) -> list[FileEntry]:
        def op(sftp):
            out = []
            for attrs in sftp.listdir_attr(path):
                is_dir = statmod.S_ISDIR(attrs.st_mode or 0)
                modified = datetime.fromtimestamp(attrs.st_mtime, timezone.utc) if attrs.st_mtime else None
                out.append(FileEntry(attrs.filename, posixpath.join(path, attrs.filename),
                                     0 if is_dir else int(attrs.st_size or 0), modified, is_dir))
            return sorted(out, key=lambda e: (not e.is_dir, e.name.casefold()))

        return self._call(path, op)

    def stat(self, path: str) -> FileEntry:
        def op(sftp):
            attrs = sftp.stat(path)
            is_dir = statmod.S_ISDIR(attrs.st_mode or 0)
            modified = datetime.fromtimestamp(attrs.st_mtime, timezone.utc) if attrs.st_mtime else None
            return FileEntry(posixpath.basename(path), path, 0 if is_dir else int(attrs.st_size or 0), modified, is_dir)

        return self._call(path, op)

    def read_bytes(self, path: str) -> ReadResult:
        def op(sftp):
            with sftp.open(path, "rb") as handle:
                handle.prefetch()
                data = handle.read()
            mtime = sftp.stat(path).st_mtime
            return ReadResult(data, Revision.of(data, float(mtime) if mtime else None))

        return self._call(path, op)

    def open_read(self, path: str) -> BinaryIO:
        # Caller must finish with the stream before other calls on this host;
        # the log downloader holds the transport exclusively while streaming.
        return self._call(path, lambda sftp: sftp.open(path, "rb"))

    def copy_file(self, source: str, target: str) -> None:
        def op(sftp):
            # Server-side copy keeps the bytes; preserve mode of the source.
            with sftp.open(source, "rb") as src:
                src.prefetch()
                data = src.read()
            with sftp.open(target, "wb") as dst:
                dst.write(data)
            try:
                sftp.chmod(target, statmod.S_IMODE(sftp.stat(source).st_mode))
            except OSError:
                pass

        self._call(target, op)

    def replace_bytes(self, path: str, data: bytes) -> None:
        def op(sftp):
            tmp = f"{path}.hm-{uuid.uuid4().hex[:12]}.tmp"
            with sftp.open(tmp, "wb") as handle:
                handle.write(data)
            try:
                try:
                    sftp.chmod(tmp, statmod.S_IMODE(sftp.stat(path).st_mode))
                except FileNotFoundError:
                    pass
                posix_rename = getattr(sftp, "posix_rename", None)
                if posix_rename is not None:
                    try:
                        posix_rename(tmp, path)
                        return
                    except OSError as exc:
                        if "unsupported" not in str(exc).lower() and getattr(exc, "errno", None) != 8:
                            raise TransportError("uncertain", f"{path}: replace outcome uncertain; verify before retrying") from None
                sftp.rename(tmp, path)
            except Exception:
                try:
                    sftp.remove(tmp)
                except Exception:
                    pass
                raise

        self._call(path, op)

    # --- commands ---------------------------------------------------------
    def run(self, command: str, timeout: float = 20.0) -> CommandResult:
        return run_ssh_command(self.host, command, timeout)


def quote(value: str) -> str:
    return shlex.quote(value)
