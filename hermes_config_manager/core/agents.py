"""Agent records: one Hermes ``config.yaml`` on one host."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..transports.base import Revision

MAIN_PROFILE = ""


def agent_key(host: str, profile: str) -> str:
    return f"{host}::{profile or '@main'}"


class Status:
    UNLOADED = "unloaded"     # discovered, not read yet
    LOADING = "loading"
    LOADED = "loaded"         # parsed successfully
    INVALID = "invalid"       # read, but YAML does not parse (never treated as empty)
    ERROR = "error"           # could not be read
    MISSING = "missing"       # was known, no longer found on the host


@dataclass
class AgentRecord:
    host: str
    profile: str
    path: str
    status: str = Status.UNLOADED
    raw: bytes | None = None
    data: Any = None                  # plain parsed data (runtime semantics)
    revision: Revision | None = None
    loaded_at: float | None = None    # wall clock of the last successful read
    from_cache: bool = False          # data shown comes from the local cache
    error: str | None = None
    error_kind: str | None = None
    parse_line: int | None = None
    refreshing: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return agent_key(self.host, self.profile)

    @property
    def is_main(self) -> bool:
        return self.profile == MAIN_PROFILE

    @property
    def name(self) -> str:
        return "main" if self.is_main else self.profile

    @property
    def display(self) -> str:
        return f"{self.host} / {self.name}"

    @property
    def usable(self) -> bool:
        return self.status == Status.LOADED and isinstance(self.data, dict)

    @property
    def text(self) -> str | None:
        if self.raw is None:
            return None
        return self.raw.decode("utf-8-sig", errors="replace")
