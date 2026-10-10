"""Host registry (``hosts.toml``).

When the application starts it reads its registry
(``%APPDATA%\\HermesConfigManager\\hosts.toml``).  Without one the registry is
empty and the UI runs its first-run "add your first host" flow.  Nothing is
built in: hosts are always added by the user.

The registry is only written when the user saves the host manager.  Secrets
are never stored: SSH uses key files / the SSH agent that already exist.
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from ..core import appdirs

ACCESS_LOCAL = "local-path"   # local folder, mapped drive or UNC path (SMB)
ACCESS_SSH = "ssh"            # SFTP for files, OpenSSH for commands
ACCESS_MOCK = "mock"          # tests / benchmarks only
DEFAULT_GLOBS = ("config.yaml", "profiles/*/config.yaml")


class HostConfigError(ValueError):
    pass


@dataclass(frozen=True)
class HostConfig:
    name: str
    access_mode: str = ACCESS_LOCAL
    hermes_root: str = ""
    ssh_hostname: str | None = None
    ssh_user: str | None = None
    ssh_key: str | None = None
    ssh_port: int = 22
    globs: tuple[str, ...] = DEFAULT_GLOBS
    # Optional settings (hosts.toml may leave them out).
    enabled: bool = True
    timeout: float = 20.0
    # Remote Hermes home used for gateway/log commands when files are reached via SMB.
    remote_root: str | None = None
    log_roots: tuple[str, ...] = ()

    @property
    def is_ssh(self) -> bool:
        return self.access_mode == ACCESS_SSH

    @property
    def can_run_commands(self) -> bool:
        """Gateway control and diagnostics need an SSH address (any access mode); the user may come
        from ``~/.ssh/config``."""
        return bool(self.ssh_hostname)

    @property
    def location(self) -> str:
        if self.is_ssh:
            user = f"{self.ssh_user}@" if self.ssh_user else ""
            port = f":{self.ssh_port}" if self.ssh_port and self.ssh_port != 22 else ""
            return f"ssh://{user}{self.ssh_hostname or self.name}{port}{self.hermes_root}"
        return self.hermes_root

# No built-in hosts: the registry starts empty and the user adds hosts (first-run flow).
DEFAULT_HOSTS: tuple[HostConfig, ...] = ()


@dataclass
class RegistryLoad:
    hosts: list[HostConfig]
    source: str          # 'app' | 'legacy' | 'defaults'
    path: Path | None
    warnings: list[str] = field(default_factory=list)


def parse_hosts_toml(text: str, origin: str = "hosts.toml") -> list[HostConfig]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise HostConfigError(f"{origin}: {exc}") from exc
    raw_hosts = data.get("hosts", [])
    if not isinstance(raw_hosts, list):
        raise HostConfigError(f"{origin}: 'hosts' must be an array of tables")
    hosts: list[HostConfig] = []
    names: set[str] = set()
    for index, raw in enumerate(raw_hosts):
        if not isinstance(raw, dict):
            raise HostConfigError(f"{origin}: hosts[{index}] is not a table")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise HostConfigError(f"{origin}: hosts[{index}] has no name")
        if name in names:
            raise HostConfigError(f"{origin}: duplicate host name '{name}'")
        names.add(name)
        mode = str(raw.get("access_mode") or ACCESS_LOCAL)
        if mode not in (ACCESS_LOCAL, ACCESS_SSH, ACCESS_MOCK):
            raise HostConfigError(f"{origin}: host '{name}' has unknown access_mode '{mode}'")
        try:
            port = int(raw.get("ssh_port") or 22)
            timeout = float(raw.get("timeout") or 20.0)
        except (TypeError, ValueError) as exc:
            raise HostConfigError(f"{origin}: host '{name}' has an invalid number ({exc})") from exc
        hosts.append(
            HostConfig(
                name=name,
                access_mode=mode,
                hermes_root=str(raw.get("hermes_root") or ""),
                ssh_hostname=(str(raw["ssh_hostname"]).strip() or None) if raw.get("ssh_hostname") else None,
                ssh_user=(str(raw["ssh_user"]).strip() or None) if raw.get("ssh_user") else None,
                ssh_key=(str(raw["ssh_key"]).strip() or None) if raw.get("ssh_key") else None,
                ssh_port=port,
                globs=tuple(str(g) for g in (raw.get("globs") or DEFAULT_GLOBS)),
                enabled=bool(raw.get("enabled", True)),
                timeout=timeout,
                remote_root=(str(raw["remote_root"]).strip() or None) if raw.get("remote_root") else None,
                log_roots=tuple(str(p) for p in (raw.get("log_roots") or ())),
            )
        )
    return hosts


def dump_hosts_toml(hosts: list[HostConfig]) -> str:
    def s(value: object) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    chunks = []
    for h in hosts:
        lines = [
            "[[hosts]]",
            f"name = {s(h.name)}",
            f"access_mode = {s(h.access_mode)}",
            f"hermes_root = {s(h.hermes_root)}",
            f"ssh_port = {int(h.ssh_port)}",
        ]
        for key in ("ssh_hostname", "ssh_user", "ssh_key", "remote_root"):
            value = getattr(h, key)
            if value:
                lines.append(f"{key} = {s(value)}")
        lines.append("globs = [" + ", ".join(s(g) for g in h.globs) + "]")
        if not h.enabled:
            lines.append("enabled = false")
        if h.timeout != 20.0:
            lines.append(f"timeout = {float(h.timeout)}")
        if h.log_roots:
            lines.append("log_roots = [" + ", ".join(s(p) for p in h.log_roots) + "]")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + ("\n" if chunks else "")


def load_registry() -> RegistryLoad:
    """This application's registry; empty (source ``"empty"``) on first run."""
    own = appdirs.hosts_file()
    if own.is_file():
        return RegistryLoad(parse_hosts_toml(own.read_text(encoding="utf-8"), str(own)), "app", own)
    return RegistryLoad(list(DEFAULT_HOSTS), "empty", None)


def save_registry(hosts: list[HostConfig]) -> Path:
    target = appdirs.hosts_file()
    parse_hosts_toml(dump_hosts_toml(hosts))  # validate before writing
    fd, tmp = tempfile.mkstemp(prefix=".hosts.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(dump_hosts_toml(hosts))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target
