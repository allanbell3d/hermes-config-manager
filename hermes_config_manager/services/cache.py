"""Local snapshot of the last-known agent configurations.

Purpose: a usable window within seconds, even when hosts are slow or offline.
Cached data is always labelled as cached with its age; a background refresh
replaces it.  Explicit invalidation: per-agent by SHA-256 revision (unchanged
files are not re-parsed), whole-cache by schema version or "Clear cache".

The cache lives in ``%LOCALAPPDATA%\\HermesConfigManager\\cache`` (user-only ACL).
A restricted unpickler only admits plain YAML data types, so a planted cache
file cannot execute code.
"""

from __future__ import annotations

import datetime as _dt
import io
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any

from ..core import appdirs
from ..core.agents import AgentRecord, Status
from ..transports.base import Revision

CACHE_VERSION = 3
_ALLOWED = {
    ("datetime", "datetime"), ("datetime", "date"), ("datetime", "time"),
    ("datetime", "timedelta"), ("datetime", "timezone"),
    ("hermes_config_manager.transports.base", "Revision"),
    ("builtins", "set"), ("builtins", "frozenset"), ("_codecs", "encode"),
}


class _SafeUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if (module, name) in _ALLOWED:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"cache contains disallowed type {module}.{name}")


def cache_path() -> Path:
    return appdirs.cache_dir() / "agents.pickle"


def load_snapshot(path: Path | None = None) -> dict[str, dict]:
    """Return ``{agent_key: entry}``; empty on any problem (cache is optional)."""
    target = path or cache_path()
    try:
        with open(target, "rb") as handle:
            payload = _SafeUnpickler(io.BufferedReader(handle)).load()
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != CACHE_VERSION:
        return {}
    agents = payload.get("agents")
    return agents if isinstance(agents, dict) else {}


def record_from_entry(entry: dict) -> AgentRecord | None:
    try:
        rec = AgentRecord(entry["host"], entry["profile"], entry["path"])
        rec.raw = entry.get("raw")
        rec.data = entry.get("data")
        rev = entry.get("revision")
        rec.revision = rev if isinstance(rev, Revision) else None
        rec.loaded_at = entry.get("loaded_at")
        rec.status = entry.get("status", Status.LOADED)
        rec.error = entry.get("error")
        rec.parse_line = entry.get("parse_line")
        rec.from_cache = True
        return rec
    except (KeyError, TypeError):
        return None


def save_snapshot(records: list[AgentRecord], path: Path | None = None) -> None:
    target = path or cache_path()
    agents = {}
    for rec in records:
        if rec.status not in (Status.LOADED, Status.INVALID) or rec.raw is None:
            continue
        agents[rec.key] = {
            "host": rec.host,
            "profile": rec.profile,
            "path": rec.path,
            "raw": rec.raw,
            "data": rec.data,
            "revision": rec.revision,
            "loaded_at": rec.loaded_at,
            "status": rec.status,
            "error": rec.error if rec.status == Status.INVALID else None,
            "parse_line": rec.parse_line,
        }
    payload = {"version": CACHE_VERSION, "saved": _dt.datetime.now().isoformat(), "agents": agents}
    fd, tmp = tempfile.mkstemp(prefix=".agents.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def clear() -> None:
    try:
        cache_path().unlink()
    except FileNotFoundError:
        pass
