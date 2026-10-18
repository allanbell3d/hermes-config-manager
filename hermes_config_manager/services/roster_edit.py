"""Editing hermes-teammates rosters: which roster each agent uses, the page's working copy, and staging.

Rules (see services.roster for the files and settings):

* An agent's page shows the roster it *uses*, worked out like the plugin does: the main agent's file is the
  master; a profile with ``roster_source: root``, or ``fallback`` without a teammate of its own, uses the
  master (read-only here: a profile never edits the master); a profile whose ``roster_file`` is set edits its
  own file; a roster kept in ``config.yaml`` itself (no ``roster_file``) is shown read-only.
* The working copy is cell-wise, like the model chains: Stage writes exactly the edited cells into the roster
  of each checked agent that has its own file (the master for a checked main agent); the others are skipped
  and named.  ``max_live_runs`` is a setting of each agent's ``config.yaml`` and is staged there.
* A teammate an agent does not have is added only when the edit gives it a description.
* A rename is done in place and never overwrites a teammate of the new name.
* Replace (with the master or the viewed agent's roster) and Remove act on profiles' own files only.  Replace
  also sets the profile's ``roster_file`` when it is not set: without it the plugin would not read the file.

Qt-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..core import roster_rules as R
from ..core.agents import Status, agent_key
from ..core.edits import EditOp, OpKind
from ..core.paths import deep_copy
from ..core.yamlpatch import fast_load
from . import roster as RM
from .work import REMOVE

MASTER = "master"            # the main agent's roster file: the host's master
OWN = "own"                  # a profile's own roster file (or one staged to be created)
INHERITED = "inherited"      # a profile that uses the main agent's roster
CONFIG = "config"            # the roster in effect is the teammates setting in config.yaml (no roster_file)
BROKEN = "broken"            # its roster file is set but missing or unreadable: replace or remove it
OUTSIDE = "outside"          # roster_file points outside the agent's folder: not edited here
NONE = "none"                # nothing to show

FIELDS = R.FIELDS            # description, route, model, reasoning_effort, toolsets, instructions
KNOB = "max_live_runs"       # in config.yaml (plugins.entries.hermes-teammates.settings), not in the roster
KNOB_RANGE = (1, 16)
KNOB_DEFAULT = 4


@dataclass
class TableView:
    agent: str
    kind: str
    doc: str | None              # the document edits go to (None: read-only)
    data: dict | None            # {"teammates": {...}} as it will be (staged edits included)
    path: str = ""
    source: str = "own"          # the agent's roster_source
    pending: str = ""            # "" | "create" | "replace" | "remove": a staged whole-file change
    error: str = ""              # BROKEN / OUTSIDE: why the file is not used as it is
    note: str = ""               # why the roster is shown this way

    @property
    def editable(self) -> bool:
        return self.kind in (MASTER, OWN) and self.pending != "remove"

    @property
    def teammates(self) -> dict:
        t = (self.data or {}).get(R.TOP)
        return t if isinstance(t, dict) else {}

    @property
    def falls_back(self) -> bool:
        """A profile's own file without a teammate, under fallback: the plugin uses the master instead."""
        return self.kind == OWN and self.source == "fallback" and not self.teammates


def config_view(session, agent: str) -> Any:
    """An agent's config as it will be (staged edits included)."""
    rec = session.agents.get(agent)
    draft = session.drafts.draft(agent)
    if draft is not None and draft.ops:
        return draft.view()
    return rec.data if rec is not None else None


def settings_of(session, agent: str) -> dict | None:
    return RM.settings(config_view(session, agent))


def _view(session, key: str) -> tuple[dict | None, str]:
    """The document as it will be after its staged edits, and the pending whole-file change."""
    rec = session.docs.get(key)
    draft = session.drafts.draft(key)
    data = rec.data if rec is not None else None
    if draft is None or not draft.ops:
        return data, ""
    last = next((op for op in reversed(draft.ops) if op.kind in (OpKind.REPLACE_FILE, OpKind.DELETE_FILE)), None)
    if last is not None and last.kind is OpKind.DELETE_FILE:
        return data, "remove"
    pending = "" if last is None else ("replace" if rec is not None else "create")
    return draft.view(), pending


def _unusable(session, key: str, data: Any, pending: str) -> str:
    """Why an existing roster file gives no roster ("" : it is usable)."""
    doc = session.docs.get(key)
    if pending in ("create", "replace"):
        return ""
    if doc is not None and doc.status != Status.LOADED:
        return doc.error or "it cannot be read"
    if not isinstance(data, dict):
        return "it is empty or not a YAML mapping"
    if not isinstance(data.get(R.TOP), dict):
        return f"it needs a top-level '{R.TOP}' mapping"
    return ""


def master_doc(session, host: str) -> str:
    return RM.doc_key(agent_key(host, ""))


def _inherited(session, rec, path: str, src: str, why: str) -> TableView:
    main = table_for(session, agent_key(rec.host, ""))
    view = TableView(rec.key, INHERITED, None, main.data if main.kind in (MASTER, CONFIG) else {R.TOP: {}},
                     path, src, note=why)
    if main.kind not in (MASTER, CONFIG):
        view.error = "the main agent's roster cannot be read here"
    return view


def table_for(session, agent: str) -> TableView:
    rec = session.agents.get(agent)
    if rec is None:
        return TableView(agent, NONE, None, None)
    s = settings_of(session, agent)
    src, rf = RM.source(s), RM.roster_file(s)
    dk = RM.doc_key(agent)
    path = session.roster_paths.get(agent, "")
    if rf and RM.outside(rf):
        return TableView(agent, OUTSIDE, None, None, path, src, error=RM.outside(rf))
    data, pending = _view(session, dk)
    has_file = dk in session.docs or pending == "create"
    if rec.is_main:
        if not rf:
            if s is None:
                return TableView(agent, NONE, None, None, path, src)
            return TableView(agent, CONFIG, None, {R.TOP: RM.inline(s)}, path, src,
                             note="its roster is the teammates setting in config.yaml (roster_file is not set)")
        if not has_file:
            return TableView(agent, BROKEN, None, None, path, src, error=f"{rf} is not there: it has no teammates")
        why = _unusable(session, dk, data, pending)
        if why:
            return TableView(agent, BROKEN, None, None, path, src, pending, f"{rf}: {why}")
        return TableView(agent, MASTER, dk, data, path, src, pending)
    if src == "root":
        return _inherited(session, rec, path, src, "roster_source is root: it uses the main agent's roster and "
                                                   "never reads its own")
    if rf:
        if not has_file:
            if src == "fallback":
                return _inherited(session, rec, path, src, f"it has no {rf}, so it uses the main agent's roster "
                                                           "(roster_source fallback)")
            return TableView(agent, BROKEN, None, None, path, src,
                             error=f"{rf} is not there: with roster_source own it has no teammates")
        if pending == "remove":
            return TableView(agent, OWN, dk, data, path, src, pending)
        why = _unusable(session, dk, data, pending)
        if why:
            return TableView(agent, BROKEN, None, None, path, src, pending, f"{rf}: {why}" + (
                " (the plugin then uses the main agent's roster)" if src == "fallback" else ""))
        return TableView(agent, OWN, dk, data, path, src, pending)
    inline = RM.inline(s)
    unused = " Its teammates.yaml is not used (roster_file is not set)." if dk in session.docs else ""
    if src == "fallback" and not inline:
        return _inherited(session, rec, path, src, "it has no teammates of its own, so it uses the main agent's "
                                                   "roster (roster_source fallback)." + unused)
    return TableView(agent, CONFIG, None, {R.TOP: inline}, path, src,
                     note="its roster is the teammates setting in config.yaml (roster_file is not set)." + unused)


def users_of_master(session, host: str) -> list[str]:
    """The profiles of ``host`` that use the main agent's roster."""
    return [k for k, r in session.agents.items() if r.host == host and not r.is_main
            and table_for(session, k).kind == INHERITED]


def has_rosters(session) -> bool:
    """Whether any loaded host uses hermes-teammates (a roster file, or the plugin's settings in a config)."""
    return bool(session.docs) or any(RM.settings(r.data) is not None for r in session.agents.values())


def knob_value(session, agent: str) -> Any:
    """The agent's max_live_runs as it will be ("" : not set; None: no hermes-teammates settings)."""
    s = settings_of(session, agent)
    return None if s is None else s.get(KNOB, "")


# ------------------------------------------------------------------ working copy
@dataclass
class RosterWork:
    """The page's edits, not staged yet; teammate names are as in the roster the page shows."""

    cells: dict[tuple[str, str], Any] = field(default_factory=dict)      # (teammate, field) -> value | REMOVE
    knobs: dict[str, Any] = field(default_factory=dict)                  # max_live_runs -> value | REMOVE
    renames: dict[str, str] = field(default_factory=dict)                # old name -> new name
    added: list[str] = field(default_factory=list)                       # new teammates, in order
    removed: set[str] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        return not (self.cells or self.knobs or self.renames or self.added or self.removed)

    @property
    def roster_edits(self) -> bool:
        return bool(self.cells or self.renames or self.added or self.removed)

    def add_teammate(self, name: str) -> None:
        if name not in self.added:
            self.added.append(name)

    def new_name(self, name: str) -> str:
        return self.renames.get(name, name)

    def fields_of(self, name: str, base: dict | None = None) -> dict:
        """A teammate's fields with the page's edits (``base``: as in the shown roster)."""
        out = dict(base or {})
        for (n, f), v in self.cells.items():
            if n == name:
                if v is REMOVE:
                    out.pop(f, None)
                else:
                    out[f] = v
        return out

    def problems(self, teammates: dict | None = None) -> list[str]:
        """What must be fixed before staging.  ``teammates``: the shown roster, to check the edited ones whole."""
        teammates = teammates or {}
        out = []
        for name in self.added:
            fields = self.fields_of(name)
            if not str(fields.get("description") or "").strip():
                out.append(f"{name}: give it a description (how the agent picks a teammate)")
            out += [f"{name}: {p}" for p in R.teammate_problems(fields)]
        for name in dict.fromkeys(n for n, _f in self.cells):
            if name in self.added or name in self.removed:
                continue
            base = teammates.get(name)
            fields = self.fields_of(name, base if isinstance(base, dict) else {})
            out += [f"{name}: {p}" for p in R.teammate_problems(fields)]
            if any(f in ("route", "model") for n, f in self.cells if n == name) and \
                    R.unset(fields.get("route")) != R.unset(fields.get("model")):
                out.append(f"{name}: set route and model together (or neither, to use the agent's own)")
        for name in self.added:
            fields = self.fields_of(name)
            if R.unset(fields.get("route")) != R.unset(fields.get("model")):
                out.append(f"{name}: set route and model together (or neither, to use the agent's own)")
        v = self.knobs.get(KNOB)
        if v is not None and v is not REMOVE and not (isinstance(v, int) and KNOB_RANGE[0] <= v <= KNOB_RANGE[1]):
            out.append(f"{KNOB} must be a whole number {KNOB_RANGE[0]}-{KNOB_RANGE[1]}")
        return out


@dataclass
class StageReport:
    changed: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)       # (agent, why)


def _why_not(view: TableView) -> str:
    if view.kind == BROKEN:
        return f"its roster file is not usable ({view.error}): replace or remove it"
    if view.kind == INHERITED:
        return "uses the main agent's roster (give it its own with Replace roster)"
    if view.kind == CONFIG:
        return "its roster is in config.yaml (Replace roster moves it to a file)"
    if view.kind == OUTSIDE:
        return view.error
    if view.pending == "remove":
        return "its roster file is staged for removal"
    return "has no teammates roster"


def _set(path, value) -> EditOp:
    return EditOp(OpKind.REMOVE, path, origin="teammates") if value is REMOVE else \
        EditOp(OpKind.SET, path, deep_copy(value), origin="teammates")


def _ops_for(view: TableView, work: RosterWork) -> tuple[list[EditOp], list[str]]:
    """Operations that put ``work`` into one agent's roster; plus why parts of it were left out."""
    roster = view.teammates
    ops: list[EditOp] = []
    notes: list[str] = []
    names: dict[str, str] = {}                       # page name -> name in this roster after renames
    now = set(roster)                                # the names as the renames are applied, in order
    for old, new in work.renames.items():
        if old not in roster:
            continue
        if new in now:
            notes.append(f"already has a teammate '{new}'; '{old}' was not renamed")
            names[old] = old
            continue
        ops.append(EditOp(OpKind.RENAME_KEY, (R.TOP, old), new, origin="teammates"))
        now.discard(old)
        now.add(new)
        names[old] = new
    missing: dict[str, dict] = {}
    for (name0, f), v in work.cells.items():
        if name0 in work.added or name0 in work.removed:
            continue
        name = names.get(name0, name0)
        if name in roster or (name != name0 and name0 in roster):
            ops.append(_set((R.TOP, name, f), v))
        elif v is not REMOVE:
            missing.setdefault(name, {})[f] = v
    for name, fields in missing.items():
        if not str(fields.get("description") or "").strip() or R.teammate_problems(fields):
            notes.append(f"has no teammate '{name}' (the edit does not give it a description)")
        else:
            ops.append(EditOp(OpKind.SET, (R.TOP, name), deep_copy(fields), origin="teammates"))
    for name in work.added:
        if name in now:
            notes.append(f"already has a teammate '{name}'")
            continue
        ops.append(EditOp(OpKind.SET, (R.TOP, name), deep_copy(work.fields_of(name)), origin="teammates"))
    for name0 in work.removed:
        name = names.get(name0, name0)
        if name in now:
            ops.append(EditOp(OpKind.REMOVE, (R.TOP, name), origin="teammates"))
    return ops, notes


def stage_roster(session, work: RosterWork, agents: list[str]) -> StageReport:
    """Stage ``work`` for each agent's own roster (the master for the main agent) and its max_live_runs, as
    one undo step."""
    report = StageReport()
    per_doc: dict[str, tuple[Any, str, list[EditOp]]] = {}
    readers: list[tuple[str, TableView]] = []
    if work.roster_edits:
        for agent in agents:
            view = table_for(session, agent)
            if not view.editable:
                readers.append((agent, view))
                continue
            ops, notes = _ops_for(view, work)
            report.skipped += [(agent, n) for n in notes]
            if ops:
                base, sha = session.base_of(view.doc)
                per_doc[view.doc] = (base, sha, ops)
    for agent, view in readers:
        # a profile using the master gets the edit through it when its master is staged too
        rec = session.agents.get(agent)
        if not (view.kind == INHERITED and rec is not None and master_doc(session, rec.host) in per_doc):
            report.skipped.append((agent, _why_not(view)))
    if KNOB in work.knobs:
        for agent in agents:
            rec = session.agents.get(agent)
            if rec is None or not isinstance(rec.data, dict):
                report.skipped.append((agent, "its config.yaml is not loaded"))
            elif settings_of(session, agent) is None:
                report.skipped.append((agent, "has no hermes-teammates settings in its config.yaml"))
            else:
                base, sha = session.base_of(agent)
                per_doc[agent] = (base, sha, [_set(RM.MAX_LIVE_RUNS, work.knobs[KNOB])])
    report.changed = len(session.drafts.apply_many(per_doc)) if per_doc else 0
    return report


# ------------------------------------------------------------------ whole files
def _source_text(session, doc: str) -> tuple[str, Any] | None:
    rec = session.docs.get(doc)
    if rec is None or rec.raw is None or not isinstance(rec.data, dict) or not isinstance(rec.data.get(R.TOP), dict):
        return None
    text = rec.raw.decode("utf-8-sig", errors="replace")
    return text, fast_load(text)


def replace_ops(session, agents: list[str], source: str | None) -> StageReport:
    """Give each checked profile a copy of a roster file, exactly as it is on the host: its host's master
    (``source`` None) or the agent ``source``'s own file.  Creates the file when the profile has none, and sets
    its roster_file when that is not set (one undo step)."""
    report = StageReport()
    per_doc: dict[str, tuple[Any, str, list[EditOp]]] = {}
    for agent in agents:
        rec = session.agents.get(agent)
        if rec is None:
            continue
        if rec.is_main:
            report.skipped.append((agent, "is the main agent: its roster is the master"))
            continue
        if source == agent:
            report.skipped.append((agent, "is the roster being copied"))
            continue
        if not isinstance(rec.data, dict):
            report.skipped.append((agent, "its config.yaml is not loaded"))
            continue
        s = settings_of(session, agent)
        if RM.source(s) == "root":
            report.skipped.append((agent, "roster_source is root: it would never read its own roster (set it to "
                                          "fallback or own first)"))
            continue
        if RM.roster_file(s) and RM.outside(RM.roster_file(s)):
            report.skipped.append((agent, RM.outside(RM.roster_file(s))))
            continue
        src_doc = master_doc(session, rec.host) if source is None else RM.doc_key(source)
        got = _source_text(session, src_doc)
        if got is None:
            report.skipped.append((agent, "the roster to copy is not a usable roster file"))
            continue
        text, data = got
        origin = "replace:master" if source is None else f"replace:{source}"
        dk = RM.doc_key(agent)
        base, sha = session.base_of(dk)
        per_doc[dk] = (base, sha, [EditOp(OpKind.REPLACE_FILE, (), data, origin=origin, text=text)])
        if not RM.roster_file(s):
            base, sha = session.base_of(agent)
            per_doc[agent] = (base, sha, [EditOp(OpKind.SET, RM.ROSTER_FILE, RM.FILE, origin=origin)])
    report.changed = len([k for k in session.drafts.apply_many(per_doc) if RM.is_doc(k)]) if per_doc else 0
    return report


def delete_ops(session, agents: list[str]) -> StageReport:
    """Remove each checked profile's own roster file, so it uses the main agent's roster (fallback)."""
    report = StageReport()
    per_doc: dict[str, tuple[Any, str, list[EditOp]]] = {}
    for agent in agents:
        rec = session.agents.get(agent)
        if rec is None:
            continue
        dk = RM.doc_key(agent)
        view = table_for(session, agent)
        if rec.is_main:
            report.skipped.append((agent, "is the main agent: the master is never removed here"))
        elif dk not in session.docs or view.kind not in (OWN, BROKEN):
            report.skipped.append((agent, "has no roster file in use"))
        elif view.source != "fallback":
            report.skipped.append((agent, "roster_source is own: without the file it would have no teammates "
                                          "(set roster_source to fallback first)"))
        else:
            base, sha = session.base_of(dk)
            per_doc[dk] = (base, sha, [EditOp(OpKind.DELETE_FILE, (), origin="remove-own")])
    report.changed = len(session.drafts.apply_many(per_doc)) if per_doc else 0
    return report


# ------------------------------------------------------------------ differences
def tables_for(session, agents: list[str]) -> dict[str, TableView]:
    return {a: table_for(session, a) for a in agents}


def shown(value: Any) -> Any:
    """A value as 'who has what' compares and shows it (a toolsets list as one line)."""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return str(value)
    return value


def who_has(session, agents: list[str], name: str, f: str | None = None,
            tables: dict[str, TableView] | None = None) -> dict[Any, list[str]]:
    """Value -> agents, for one teammate field (``f``) or max_live_runs (``name`` = the knob, ``f`` None).

    ``None``: the agent has no such teammate (or no hermes-teammates settings); ``""``: not set.
    ``tables``: :func:`tables_for` of the same agents, when asking many times.
    """
    out: dict[Any, list[str]] = {}
    for agent in agents:
        if f is None:
            value = knob_value(session, agent)
        else:
            view = tables[agent] if tables is not None else table_for(session, agent)
            t = view.teammates.get(name)
            value = None if not isinstance(t, dict) else t.get(f, "")
        out.setdefault(shown(value), []).append(agent)
    return out


def teammate_names(tables: dict[str, TableView]) -> list[str]:
    """Every teammate name of these rosters, in the order they first appear."""
    out: dict[str, None] = {}
    for view in tables.values():
        for name in view.teammates:
            out.setdefault(name, None)
    return list(out)


def differ_count(session, agents: list[str]) -> int:
    """How many teammate fields (and max_live_runs) differ across these agents."""
    if len(agents) < 2:
        return 0
    tables = tables_for(session, agents)
    n = int(len(who_has(session, agents, KNOB, None, tables)) > 1)
    for name in teammate_names(tables):
        n += sum(len(who_has(session, agents, name, f, tables)) > 1 for f in FIELDS)
    return n


__all__ = ["BROKEN", "CONFIG", "FIELDS", "INHERITED", "KNOB", "MASTER", "NONE", "OUTSIDE", "OWN", "REMOVE",
           "RosterWork", "StageReport", "TableView", "delete_ops", "differ_count", "has_rosters", "knob_value",
           "master_doc", "replace_ops", "stage_roster", "table_for", "users_of_master", "who_has"]
