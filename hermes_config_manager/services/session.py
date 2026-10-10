"""Application session: hosts, agents, viewed/checked state, drafts, loading.

The session is the single owner of mutable application state.  Widgets read
from it and call its methods; they never talk to transports directly.  All
host I/O goes through :class:`TaskRunner` (bounded, stale-safe, timeouts).

Loading strategy (startup within seconds, responsive with 100+ agents):

1. Load the local snapshot (cache) synchronously - a few milliseconds - and
   show every cached agent immediately, labelled "cached · <age>".
2. In the background, probe and scan each enabled host (profiles listing).
3. Read each config; unchanged files (same SHA-256) keep their parsed data;
   changed files are parsed in the worker thread with libyaml.
4. Save a new snapshot once the refresh settles.
"""

from __future__ import annotations

import itertools

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Iterable

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.agents import AgentRecord, Status, agent_key
from ..core.edits import DraftStore
from ..core.yamlpatch import YamlParseError, fast_load
from ..transports import transport_for
from ..transports.base import Revision, Transport, TransportError, discover_config_paths
from ..transports.hosts import ACCESS_SSH, HostConfig, load_registry
from . import cache as cache_mod
from .tasks import TaskRunner


class HostState:
    UNKNOWN = "unknown"
    CONNECTING = "connecting"
    OK = "ok"
    PARTIAL = "partial"       # reachable, some agents failed to load
    ERROR = "error"           # reachable but failing (auth, permission, invalid root)
    OFFLINE = "offline"       # unavailable / timed out
    DISABLED = "disabled"


@dataclass
class HostStatus:
    name: str
    state: str = HostState.UNKNOWN
    message: str = ""
    last_ok: float | None = None
    last_attempt: float | None = None
    agent_count: int = 0
    loading: int = 0
    hung: int = 0
    scan_ms: float | None = None

    @property
    def busy(self) -> bool:
        return self.state == HostState.CONNECTING or self.loading > 0


@dataclass
class LoadOutcome:
    raw: bytes
    revision: Revision
    data: Any = None
    status: str = Status.LOADED
    error: str | None = None
    parse_line: int | None = None
    reused: bool = False


def _read_and_parse(ctx, transport: Transport, path: str, known_sha: str | None) -> LoadOutcome:
    result = transport.read_bytes(path)
    ctx.check()
    if known_sha and result.revision.sha256 == known_sha:
        return LoadOutcome(result.data, result.revision, reused=True)
    text = result.data.decode("utf-8-sig", errors="replace")
    try:
        data = fast_load(text)
    except YamlParseError as exc:
        return LoadOutcome(result.data, result.revision, None, Status.INVALID, str(exc), exc.line)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return LoadOutcome(result.data, result.revision, None, Status.INVALID,
                           f"Top level is a {type(data).__name__}, not a mapping", 1)
    return LoadOutcome(result.data, result.revision, data)


def _scan(ctx, transport: Transport, host: HostConfig) -> tuple[list[tuple[str, str]], float]:
    started = time.perf_counter()
    transport.probe()
    ctx.check()
    found = discover_config_paths(transport, host.hermes_root, host.globs)
    return found, (time.perf_counter() - started) * 1000


class Session(QObject):
    hosts_changed = Signal()                 # registry replaced
    host_status_changed = Signal(str)        # host name
    agents_changed = Signal()                # structural: agents added/removed/reordered
    agent_updated = Signal(str)              # one agent's data/status changed
    viewed_changed = Signal(str)             # key ('' = none)
    checked_changed = Signal()
    drafts_changed = Signal(object)          # set of agent keys
    activity = Signal(str)                   # short status-bar text
    refresh_finished = Signal()

    def __init__(self, hosts: list[HostConfig] | None = None, transports: dict[str, Transport] | None = None,
                 use_cache: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Python-owned (no Qt parent): worker threads keep the runner alive until they
        # finish, so its C++ side must not be destroyed together with the session.
        self.runner = TaskRunner(max_workers=12, per_host_limit=4)
        self.destroyed.connect(self.runner.shutdown)
        self.runner.host_hung.connect(self._on_host_hung)
        self.drafts = DraftStore()
        self.drafts.subscribe(lambda keys: self.drafts_changed.emit(set(keys)))
        self.use_cache = use_cache
        self.registry_source = "explicit"
        self.registry_warnings: list[str] = []
        if hosts is None:
            reg = load_registry()
            hosts, self.registry_source, self.registry_warnings = reg.hosts, reg.source, reg.warnings
        self.hosts: list[HostConfig] = list(hosts)
        self._transports: dict[str, Transport] = dict(transports or {})
        self.host_status: dict[str, HostStatus] = {h.name: HostStatus(h.name) for h in self.hosts}
        self.agents: "OrderedDict[str, AgentRecord]" = OrderedDict()
        self.checked: set[str] = set()
        self._check_seq: dict[str, int] = {}
        self._check_counter = itertools.count()
        self.viewed: str = ""
        self._pending_loads: set[str] = set()
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1500)
        self._save_timer.timeout.connect(self.save_cache)
        self.external_conflicts: dict[str, list] = {}
        for host in self.hosts:
            if host.access_mode == ACCESS_SSH:
                self.runner.set_host_limit(host.name, 1)

    # ------------------------------------------------------------------ hosts
    def host(self, name: str) -> HostConfig | None:
        return next((h for h in self.hosts if h.name == name), None)

    def transport(self, name: str) -> Transport:
        t = self._transports.get(name)
        if t is None:
            host = self.host(name)
            if host is None:
                raise KeyError(name)
            t = transport_for(host)
            self._transports[name] = t
        return t

    def replace_hosts(self, hosts: list[HostConfig]) -> None:
        """Apply an edited registry; agents of removed hosts disappear."""
        old = {h.name: h for h in self.hosts}
        self.hosts = list(hosts)
        for name in list(self._transports):
            if name not in old or old[name] != self.host(name):
                self.runner.cancel_host(name)
                try:
                    self._transports.pop(name).close()
                except Exception:
                    pass
        self.host_status = {h.name: self.host_status.get(h.name, HostStatus(h.name)) for h in self.hosts}
        names = {h.name for h in self.hosts}
        dropped = [k for k, r in self.agents.items() if r.host not in names]
        for key in dropped:
            self.agents.pop(key)
            self.checked.discard(key)
        if any(self.drafts.has_changes(k) for k in dropped):
            self.drafts.discard(dropped)        # staged changes of a host that is gone can never be written
        if self.viewed and self.viewed not in self.agents:
            self.viewed = ""
            self.viewed_changed.emit("")
        for host in self.hosts:
            if host.access_mode == ACCESS_SSH:
                self.runner.set_host_limit(host.name, 1)
        self.hosts_changed.emit()
        self.agents_changed.emit()

    def _on_host_hung(self, host: str, count: int) -> None:
        status = self.host_status.get(host)
        if status is not None:
            status.hung = count
            self.host_status_changed.emit(host)

    # ----------------------------------------------------------------- agents
    def record(self, key: str) -> AgentRecord | None:
        return self.agents.get(key)

    def ordered_keys(self) -> list[str]:
        return list(self.agents.keys())

    def _sort_agents(self) -> None:
        order = {h.name: i for i, h in enumerate(self.hosts)}
        items = sorted(self.agents.items(),
                       key=lambda kv: (order.get(kv[1].host, 999), not kv[1].is_main, kv[1].profile.casefold()))
        self.agents = OrderedDict(items)

    def base_of(self, key: str) -> tuple[Any, str]:
        rec = self.agents[key]
        return rec.data, (rec.revision.sha256 if rec.revision else "")

    # ---------------------------------------------------------- view/checked
    def set_viewed(self, key: str) -> None:
        if key != self.viewed and (key == "" or key in self.agents):
            self.viewed = key
            self.viewed_changed.emit(key)

    def _note_checked(self, keys: Iterable[str]) -> None:
        """Remember when each agent was checked (the first one checked is the page's reference)."""
        for key in keys:
            if key not in self._check_seq:
                self._check_seq[key] = next(self._check_counter)
        for key in [k for k in self._check_seq if k not in self.checked]:
            del self._check_seq[key]

    def set_checked(self, keys: Iterable[str], checked: bool = True) -> None:
        before = set(self.checked)
        keys = list(keys)
        for key in keys:
            if key in self.agents:
                (self.checked.add if checked else self.checked.discard)(key)
        self._note_checked([k for k in keys if k in self.checked])
        if self.checked != before:
            self.checked_changed.emit()

    def replace_checked(self, keys: Iterable[str]) -> None:
        keys = [k for k in keys if k in self.agents]
        new = set(keys)
        if new != self.checked:
            self.checked = new
            self._note_checked(keys)
            self.checked_changed.emit()

    def checked_in_order(self) -> list[str]:
        """Checked agents in list order (for display and for writing)."""
        return [k for k in self.agents if k in self.checked]

    def checked_by_time(self) -> list[str]:
        """Checked agents in the order they were checked (first checked first)."""
        return sorted((k for k in self.checked if k in self.agents),
                      key=lambda k: self._check_seq.get(k, 1 << 62))

    # ---------------------------------------------------------------- loading
    def load_cached(self) -> int:
        """Populate agents from the local snapshot (synchronous, fast)."""
        if not self.use_cache:
            return 0
        names = {h.name for h in self.hosts}
        count = 0
        for key, entry in cache_mod.load_snapshot().items():
            rec = cache_mod.record_from_entry(entry)
            if rec is None or rec.host not in names or key in self.agents:
                continue
            self.agents[key] = rec
            count += 1
        if count:
            self._sort_agents()
            for status in self.host_status.values():
                status.agent_count = sum(1 for r in self.agents.values() if r.host == status.name)
            self.agents_changed.emit()
        return count

    def refresh_all(self) -> None:
        for host in self.hosts:
            self.refresh_host(host.name)

    def refresh_host(self, name: str) -> None:
        host = self.host(name)
        status = self.host_status.setdefault(name, HostStatus(name))
        if host is None:
            return
        if not host.enabled:
            status.state, status.message = HostState.DISABLED, "Disabled in host settings"
            self.host_status_changed.emit(name)
            return
        if self.runner.hung_count(name) >= 2:
            status.state = HostState.OFFLINE
            status.message = f"Still waiting for {self.runner.hung_count(name)} earlier operations; not starting more"
            self.host_status_changed.emit(name)
            return
        status.state, status.message, status.last_attempt = HostState.CONNECTING, "Scanning…", time.time()
        self.host_status_changed.emit(name)
        try:
            transport = self.transport(name)
        except Exception as exc:  # misconfigured host
            self._host_failed(name, TransportError("invalid", str(exc)))
            return
        self.runner.submit(
            _scan, transport, host,
            label=f"Scan {name}", key=("scan", name), host=name, timeout=host.timeout,
            on_done=lambda res, n=name: self._on_scanned(n, *res),
            on_error=lambda exc, n=name: self._host_failed(n, exc),
        )
        self._emit_activity()

    def _host_failed(self, name: str, exc: BaseException) -> None:
        status = self.host_status[name]
        kind = getattr(exc, "kind", "other")
        status.state = HostState.OFFLINE if kind in ("unavailable", "timeout", "not_found") or isinstance(exc, TimeoutError) else HostState.ERROR
        status.message = str(exc)
        for rec in self.agents.values():
            if rec.host == name:
                rec.refreshing = False
        self.host_status_changed.emit(name)
        self.agents_changed.emit()
        self._emit_activity()
        self._maybe_finished()

    def _on_scanned(self, name: str, found: list[tuple[str, str]], scan_ms: float) -> None:
        status = self.host_status[name]
        status.scan_ms = scan_ms
        status.message = f"{len(found)} configurations found"
        seen = set()
        structural = False
        for profile, path in found:
            key = agent_key(name, profile)
            seen.add(key)
            rec = self.agents.get(key)
            if rec is None:
                rec = AgentRecord(name, profile, path)
                self.agents[key] = rec
                structural = True
            elif rec.path != path:
                rec.path = path
            if rec.status == Status.MISSING:
                rec.status = Status.UNLOADED
        for key, rec in list(self.agents.items()):
            if rec.host != name or key in seen:
                continue
            if not getattr(self.drafts.draft(key), "ops", None):
                # renamed or deleted on the host (or only known from the last snapshot): gone from the list
                self.agents.pop(key)
                if key in self.checked:
                    self.checked.discard(key)
                    self.checked_changed.emit()
                if self.viewed == key:
                    self.viewed = ""
                    self.viewed_changed.emit("")
                structural = True
            elif rec.status != Status.MISSING:
                # kept (greyed) only while it has staged changes, so Review can show what cannot be written
                rec.status, rec.error = Status.MISSING, "No longer found on the host"
                structural = True
        if structural:
            self._sort_agents()
            self.agents_changed.emit()
        status.agent_count = len(found)
        for profile, path in found:
            self.refresh_agent(agent_key(name, profile), _from_scan=True)
        if not found:
            status.state, status.last_ok = HostState.OK, time.time()
        self.host_status_changed.emit(name)
        self._maybe_finished()

    def refresh_agent(self, key: str, _from_scan: bool = False) -> None:
        rec = self.agents.get(key)
        host = self.host(rec.host) if rec else None
        if rec is None or host is None or rec.status == Status.MISSING:
            return
        rec.refreshing = True
        status = self.host_status[rec.host]
        status.loading += 1
        self._pending_loads.add(key)
        known = rec.revision.sha256 if (rec.revision and rec.status == Status.LOADED) else None
        self.runner.submit(
            _read_and_parse, self.transport(rec.host), rec.path, known,
            label=f"Read {rec.display}", key=("load", key), host=rec.host, timeout=host.timeout,
            on_done=lambda out, k=key: self._on_loaded(k, out),
            on_error=lambda exc, k=key: self._on_load_failed(k, exc),
        )
        self.agent_updated.emit(key)
        self._emit_activity()

    def _finish_load(self, key: str) -> None:
        self._pending_loads.discard(key)
        rec = self.agents.get(key)
        if rec is not None:
            status = self.host_status[rec.host]
            status.loading = max(0, status.loading - 1)
            if status.loading == 0 and status.state == HostState.CONNECTING:
                failed = sum(1 for r in self.agents.values() if r.host == rec.host and r.status == Status.ERROR)
                status.state = HostState.PARTIAL if failed else HostState.OK
                status.last_ok = time.time()
                status.message = (f"{status.agent_count} agents" + (f", {failed} failed to load" if failed else ""))
            self.host_status_changed.emit(rec.host)
        self._emit_activity()
        self._maybe_finished()

    def _on_loaded(self, key: str, out: LoadOutcome) -> None:
        rec = self.agents.get(key)
        if rec is None:
            return
        rec.refreshing = False
        rec.loaded_at = time.time()
        rec.from_cache = False
        rec.error = rec.error_kind = None
        if not out.reused:
            old_sha = rec.revision.sha256 if rec.revision else None
            rec.raw, rec.revision = out.raw, out.revision
            rec.status, rec.error, rec.parse_line = out.status, out.error, out.parse_line
            rec.data = out.data if out.status == Status.LOADED else None
            if out.status == Status.LOADED and old_sha and old_sha != out.revision.sha256:
                conflicts = self.drafts.rebase(key, rec.data, out.revision.sha256)
                if conflicts:
                    self.external_conflicts[key] = conflicts
        else:
            rec.status = Status.LOADED
        self.agent_updated.emit(key)
        self._finish_load(key)
        self._save_timer.start()

    def _on_load_failed(self, key: str, exc: BaseException) -> None:
        rec = self.agents.get(key)
        if rec is None:
            return
        rec.refreshing = False
        rec.error = str(exc)
        rec.error_kind = getattr(exc, "kind", "timeout" if isinstance(exc, TimeoutError) else "other")
        if rec.data is None:
            rec.status = Status.ERROR   # nothing to show; cached data, if any, stays visible
        self.agent_updated.emit(key)
        self._finish_load(key)

    def _maybe_finished(self) -> None:
        if not self._pending_loads and not any(s.state == HostState.CONNECTING for s in self.host_status.values()):
            self.refresh_finished.emit()

    def _emit_activity(self) -> None:
        loading = len(self._pending_loads)
        scanning = sum(1 for s in self.host_status.values() if s.state == HostState.CONNECTING and s.loading == 0)
        if loading or scanning:
            parts = []
            if scanning:
                parts.append(f"scanning {scanning} host{'s' if scanning != 1 else ''}")
            if loading:
                parts.append(f"reading {loading} config{'s' if loading != 1 else ''}")
            self.activity.emit("Refreshing: " + ", ".join(parts) + "…")
        else:
            self.activity.emit("")

    def is_refreshing(self) -> bool:
        return bool(self._pending_loads) or any(s.state == HostState.CONNECTING for s in self.host_status.values())

    def cancel_refresh(self) -> None:
        self.runner.cancel_where(lambda t: isinstance(t.key, tuple) and t.key[0] in ("scan", "load"))
        for key in list(self._pending_loads):
            rec = self.agents.get(key)
            if rec:
                rec.refreshing = False
                self.agent_updated.emit(key)
        self._pending_loads.clear()
        for status in self.host_status.values():
            status.loading = 0
            if status.state == HostState.CONNECTING:
                status.state, status.message = HostState.UNKNOWN, "Refresh cancelled"
            self.host_status_changed.emit(status.name)
        self._emit_activity()
        self.refresh_finished.emit()

    def save_cache(self) -> None:
        if self.use_cache:
            try:
                cache_mod.save_snapshot(list(self.agents.values()))
            except OSError:
                pass

    def adopt_written(self, key: str, raw: bytes, revision: Revision, data: Any) -> None:
        """Record the new file state after a successful apply (from readback)."""
        rec = self.agents.get(key)
        if rec is None:
            return
        rec.raw, rec.revision, rec.data = raw, revision, data
        rec.status, rec.loaded_at, rec.from_cache, rec.error = Status.LOADED, time.time(), False, None
        self.agent_updated.emit(key)
        self._save_timer.start()

    def shutdown(self) -> None:
        self._save_timer.stop()
        self.save_cache()
        self.runner.shutdown()
        for t in self._transports.values():
            try:
                t.close()
            except Exception:
                pass
