"""hermes-teammates roster files as documents of the session (one per agent that has one).

The plugin's settings live in each agent's ``config.yaml`` under ``plugins.entries.hermes-teammates.settings``:

* ``roster_file``: the roster's YAML file, relative to the agent's Hermes home (next to its ``config.yaml``).
  When set, the file replaces the ``teammates`` setting.  A file whose agent does not point at it is not used.
* ``roster_source``: ``own`` (default) uses the agent's roster; ``root`` the main agent's (the agent's own is
  not read); ``fallback`` the agent's own when it has at least one teammate, otherwise the main agent's.

Each roster file found is a document keyed ``<agent key>#teammates`` next to the agent's ``config.yaml``: it is
drafted, reviewed and written like any config file.  The main agent's document is the *master*: profiles never
edit it.  Which roster an agent uses is worked out in services.roster_edit.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Any

from ..core.agents import AgentRecord, agent_key
from ..core.paths import PathT
from ..transports.base import Transport, TransportError

PLUGIN = "hermes-teammates"
FILE = "teammates.yaml"
SUFFIX = "#teammates"
SETTINGS: PathT = ("plugins", "entries", PLUGIN, "settings")
ROSTER_FILE: PathT = SETTINGS + ("roster_file",)
MAX_LIVE_RUNS: PathT = SETTINGS + ("max_live_runs",)
SOURCES = ("own", "root", "fallback")


def doc_key(agent: str) -> str:
    return agent + SUFFIX


def is_doc(key: str) -> bool:
    return key.endswith(SUFFIX)


def agent_of(key: str) -> str:
    return key[: -len(SUFFIX)] if is_doc(key) else key


def home_of(transport: Transport, root: str, profile: str, config_path: str) -> str:
    """The Hermes home of an agent: the host's root for the main agent, the profile folder otherwise."""
    return root if not profile else transport.parent(config_path)


# ------------------------------------------------------------------ the plugin's settings in a config.yaml
def settings(config: Any) -> dict | None:
    """The plugin's settings in an agent's config (None: the config has no entry for the plugin)."""
    node = config
    for k in SETTINGS[:-1]:
        node = node.get(k) if isinstance(node, dict) else None
    if not isinstance(node, dict):
        return None
    s = node.get("settings")
    return s if isinstance(s, dict) else {}


def roster_file(s: dict | None) -> str:
    rf = (s or {}).get("roster_file")
    return rf.strip() if isinstance(rf, str) else ""


def source(s: dict | None) -> str:
    """own / root / fallback; anything else counts as own (as in the plugin)."""
    v = (s or {}).get("roster_source", "own")
    return v if v in SOURCES else "own"


def inline(s: dict | None) -> dict:
    """The roster kept in config.yaml itself (the ``teammates`` setting)."""
    t = (s or {}).get("teammates")
    return t if isinstance(t, dict) else {}


def outside(rel: str) -> str | None:
    """Why a roster_file is not edited here (None: a path inside the agent's folder, which is)."""
    if rel.startswith(("/", "\\", "~")) or re.match(r"[A-Za-z]:", rel):
        return f"roster_file is an absolute path ({rel})"
    if posixpath.normpath(rel.replace("\\", "/")).startswith(".."):
        return f"roster_file points outside the agent's folder ({rel})"
    return None


def roster_path(transport: Transport, home: str, s: dict | None) -> str:
    """Where the agent's roster file is: its roster_file, or ``teammates.yaml`` next to its config.yaml (where
    Replace creates one).  A roster_file outside the agent's folder is not followed."""
    rel = roster_file(s) or FILE
    if outside(rel):
        rel = FILE
    return transport.join(home, *posixpath.normpath(rel.replace("\\", "/")).split("/"))


# ------------------------------------------------------------------ the documents
@dataclass
class RosterRecord(AgentRecord):
    """One roster file; ``profile`` is the agent's (``""`` = the main agent: the master roster)."""

    @property
    def key(self) -> str:
        return doc_key(agent_key(self.host, self.profile))

    @property
    def is_master(self) -> bool:
        return self.is_main

    @property
    def display(self) -> str:
        return f"{self.host} / {self.name} · teammates roster"


@dataclass
class Found:
    """What a host has for one agent: its roster file (read and parsed, or None when there is none)."""

    path: str
    outcome: object | None          # session.LoadOutcome, or None when there is no file


def scan_rosters(ctx, transport: Transport, agents: list[tuple[str, str]], read) -> dict[str, Found]:
    """For each ``(key, roster path)``: the file read and parsed, when it exists (worker thread).

    ``read(ctx, transport, path, known_sha)`` reads and parses one file (the session's own reader).
    """
    out: dict[str, Found] = {}
    for key, path in agents:
        outcome = None
        try:
            outcome = read(ctx, transport, path, None)
        except TransportError as exc:
            if exc.kind != "not_found":
                raise
        out[key] = Found(path, outcome)
        ctx.check()
    return out


__all__ = ["FILE", "MAX_LIVE_RUNS", "PLUGIN", "ROSTER_FILE", "SETTINGS", "SOURCES", "SUFFIX", "Found",
           "RosterRecord", "agent_of", "doc_key", "home_of", "inline", "is_doc", "outside", "roster_file",
           "roster_path", "scan_rosters", "settings", "source"]
