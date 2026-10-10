"""Review and apply: pending edits grouped by *change*, then written safely.

Bulk edits read best by change, not by file: "compression.threshold
0.5 -> 0.7 on 41 agents" instead of 41 near-identical diffs.  Two layers:

* pending_changes() - instant, computed from the drafts (no host I/O),
  so the review opens immediately.
* ApplyFlow - re-reads every file on its host (prepare_change: conflict
  check, verified surgical patch, masked diff), then writes (apply_change:
  SHA re-check, backup, atomic replace, readback).  Writes are sequential
  per host and parallel across hosts.  Optionally restarts the gateways of
  the agents that were written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..core.paths import PathT, format_path
from . import apply as A
from . import gateway


@dataclass
class Transition:
    before: Any
    after: Any
    keys: list[str] = field(default_factory=list)


@dataclass
class PendingChange:
    """One changed path: every agent that changes it, grouped by (before, after)."""

    path: PathT
    transitions: list[Transition] = field(default_factory=list)

    @property
    def keys(self) -> list[str]:
        return [k for t in self.transitions for k in t.keys]

    @property
    def label(self) -> str:
        return format_path(self.path)


def snapshot(session, key: str) -> A.DraftSnapshot | None:
    rec, draft = session.record(key), session.drafts.draft(key)
    if rec is None or draft is None or not draft.ops:
        return None
    return A.DraftSnapshot(key, rec.host, rec.display, rec.path, draft.base, draft.base_sha, list(draft.ops),
                            list(draft.conflicts))


class ApplyFlow(QObject):
    """Prepare (read-only) then apply the pending edits of a set of agents."""

    prepared = Signal(object)        # list[PreparedChange], session order
    applied = Signal(object)         # ApplyResult (one agent)
    finished = Signal(object)        # list[ApplyResult]
    restarted = Signal(object)       # list[gateway.RestartResult]

    def __init__(self, session, schema=None, parent: QObject | None = None, tag: str = "prepare") -> None:
        super().__init__(parent)
        self.session = session
        self.schema = schema
        self.tag = tag                      # task key namespace: a read-only check never supersedes a write
        self._prepared: dict[str, A.PreparedChange] = {}
        self._waiting: set[str] = set()
        self._results: dict[str, A.ApplyResult] = {}
        self._restart = False

    # -- prepare ------------------------------------------------------------
    def prepare(self, keys: list[str]) -> None:
        self._prepared.clear()
        snaps = [s for s in (snapshot(self.session, k) for k in keys) if s is not None]
        self._waiting = {s.key for s in snaps}
        if not snaps:
            self.prepared.emit([])
        for snap in snaps:
            host = self.session.host(snap.host)
            self.session.runner.submit(
                A.prepare_change, self.session.transport(snap.host), snap, self.schema,
                label=f"Prepare {snap.display}", key=(self.tag, snap.key), host=snap.host,
                timeout=host.timeout if host else 20.0, receiver=self,
                on_done=self._on_prepared,
                on_error=lambda exc, s=snap: self._on_prepared(A.PreparedChange(s, A.ERROR, str(exc))),
            )

    def _on_prepared(self, result: A.PreparedChange) -> None:
        self._prepared[result.key] = result
        self._waiting.discard(result.key)
        if not self._waiting:
            order = {k: i for i, k in enumerate(self.session.ordered_keys())}
            self.prepared.emit(sorted(self._prepared.values(), key=lambda p: order.get(p.key, 1 << 30)))

    # -- apply --------------------------------------------------------------
    def apply(self, prepared: list[A.PreparedChange], restart_gateways: bool = False) -> None:
        self._results.clear()
        self._restart = restart_gateways
        self._waiting = set()
        runner = self.session.runner
        for item in prepared:
            if item.status == A.NOOP:
                # the file already has these values: the staged change is done, not left pending forever
                self.session.drafts.adopt_applied(item.key, item.target_data, item.current_sha or "")
                self.session.refresh_agent(item.key)
                self._record(A.ApplyResult(item.key, item.snapshot.display, A.UP_TO_DATE,
                                           "The file already had these values; nothing was written."))
                continue
            if not item.writable:
                self._record(A.ApplyResult(item.key, item.snapshot.display, A.SKIPPED, item.message or item.status))
                continue
            host_name = item.snapshot.host
            slot = f"{host_name}\x00write"          # one write at a time per host
            runner.set_host_limit(slot, 1)
            host = self.session.host(host_name)
            self._waiting.add(item.key)
            runner.submit(
                A.apply_change, self.session.transport(host_name), item,
                label=f"Write {item.snapshot.display}", key=("apply", item.key), host=slot,
                timeout=(host.timeout if host else 20.0) * 3, receiver=self,
                on_done=self._on_applied,
                on_error=lambda exc, p=item: self._on_applied(
                    A.ApplyResult(p.key, p.snapshot.display, A.CANCELLED, f"Not written: {exc}")
                    if "not attempted" in str(exc) else
                    A.ApplyResult(p.key, p.snapshot.display, A.UNCERTAIN, f"No answer from the host: {exc}")),
            )
        if not self._waiting:
            self._done()

    def _record(self, result: A.ApplyResult) -> None:
        self._results[result.key] = result
        self.applied.emit(result)

    def _on_applied(self, result: A.ApplyResult) -> None:
        if result.status == A.WRITTEN and result.revision is not None:
            self.session.adopt_written(result.key, result.raw, result.revision, result.data)
            self.session.drafts.adopt_applied(result.key, result.data, result.revision.sha256)
        self._record(result)
        self._waiting.discard(result.key)
        if not self._waiting:
            self._done()

    def _done(self) -> None:
        results = list(self._results.values())
        self.finished.emit(results)
        if self._restart:
            self.restart([r.key for r in results if r.status == A.WRITTEN])

    # -- gateways -----------------------------------------------------------
    def restart(self, keys: list[str]) -> None:
        """Restart the gateway of each agent (hosts without a command endpoint are reported)."""
        outcomes: list[gateway.RestartResult] = []
        pending = set()
        for key in keys:
            rec = self.session.record(key)
            host = self.session.host(rec.host) if rec else None
            if rec is None or host is None:
                continue
            unit = gateway.service_name(rec.profile)
            if not host.can_run_commands:
                outcomes.append(gateway.RestartResult(rec.profile, unit, False,
                                                      f"{host.name} has no SSH endpoint for commands"))
                continue
            slot = f"{host.name}\x00cmd"
            self.session.runner.set_host_limit(slot, 1)
            pending.add(key)

            def done(res, k=key):
                outcomes.append(res)
                pending.discard(k)
                if not pending:
                    self.restarted.emit(outcomes)

            def failed(exc, k=key, p=rec.profile, u=unit):
                done(gateway.RestartResult(p, u, False, str(exc)), k)

            self.session.runner.submit(
                gateway.restart, self.session.transport(host.name), rec.profile,
                label=f"Restart {unit} on {host.name}", key=("restart", key), host=slot,
                timeout=60.0, receiver=self, on_done=done, on_error=failed)
        if not pending:
            self.restarted.emit(outcomes)

