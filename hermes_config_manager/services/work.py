"""Edits that are not staged yet ("edited, not staged").

The three steps of every change:

1. **Edit** - the value lives here, on the page, as a *working value*.  It does not change when
   agents are checked or unchecked.
2. **Stage** - the working value becomes per-agent draft operations (``DraftStore``) for the
   agents checked at that moment, and leaves this store.
3. **Review -> Write config** - the drafts are written (``services.review.ApplyFlow``).

Model chains have a working copy too (rows of provider, model, effort).  Staging writes exactly
the cells that were edited: the head provider+model (a model belongs to its provider), the head
effort, and the fallback list only when a fallback row was edited, added, removed or moved.
Qt-free; the UI subscribes with :meth:`WorkStore.subscribe`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from ..core import secrets
from ..core.assignments import ModelAssignment
from ..core.edits import EditOp, OpKind
from ..core.paths import MISSING, PathT, deep_copy
from ..core.settings_tree import TASK_CHAIN_KEYS
from ..core.yamlpatch import strict_equal
from . import chains as C


class _Remove:
    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:
        return "REMOVE"


REMOVE: Any = _Remove()        # working value meaning "remove the key so the default applies"

Row = list                      # [provider, model, effort]; effort None = default


@dataclass
class ChainWork:
    rows: list[Row]
    pm: bool = False            # head provider+model edited
    e: bool = False             # head effort edited
    fb: bool = False            # fallback list edited
    noref: bool = False         # started while no agent was checked
    removed: bool = False       # the whole entry (head and fallbacks) is removed from the file

    def remove_all(self) -> None:
        """Remove the whole entry: an auxiliary task then has no model of its own (Hermes' default)."""
        self.rows = []
        self.removed = True
        self.pm = self.e = self.fb = True

    def mark(self, i: int, what: str) -> None:
        self.removed = False
        if i > 0:
            self.fb = True
        elif what == "pm":
            self.pm = True
        elif what == "e":
            self.e = True
        else:
            self.pm = self.e = self.fb = True


def spec_for(key: str) -> C.ChainSpec:
    if key == "primary":
        return C.PRIMARY
    if key == "delegation":
        return C.DELEGATION
    return C.aux_spec(key.split(":", 1)[1])


def rows_of(chain: Iterable[ModelAssignment]) -> list[Row]:
    return [[a.provider, a.model, a.reasoning_effort] for a in chain if not a.empty]


def _as_assignment(row: Row) -> ModelAssignment:
    # '' is a value (written as ''), None removes the key: model '' is how "auto" is written.
    return ModelAssignment(row[0], row[1], row[2])


def apply_edits(work: ChainWork, old: list[Row]) -> list[Row]:
    """The agent's chain ``old`` with exactly the edited cells of ``work`` applied."""
    if work.removed:
        return []
    new = [list(r) for r in old]
    w = work.rows
    if w and (work.pm or work.e):
        if not new:
            new.append(list(w[0]))
        else:
            if work.pm:
                new[0][0], new[0][1] = w[0][0], w[0][1]
            if work.e:
                new[0][2] = w[0][2]
    if work.fb:
        new = new[:1] + [list(r) for r in w[1:]]
    return new


class WorkStore:
    def __init__(self) -> None:
        self.values: dict[PathT, Any] = {}
        self.chains: dict[str, ChainWork] = {}
        self.skipped: list[tuple[str, PathT]] = []     # (agent, path) left out of the last stage
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, callback: Callable[[], None]) -> None:
        self._listeners.append(callback)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # ------------------------------------------------------------- settings
    def has(self, path: PathT) -> bool:
        return path in self.values

    def value(self, path: PathT) -> Any:
        return self.values.get(path, MISSING)

    def dirty(self, path: PathT, scope) -> bool:
        """The working value differs from what at least one checked agent has (or none is checked)."""
        if path not in self.values:
            return False
        keys = scope.usable_keys
        if not keys:
            return True
        w = self.values[path]
        masked = w is not REMOVE and secrets.contains_mask(w)
        for key in keys:
            cur = scope.draft_value(key, path)
            if w is REMOVE:
                if cur is not MISSING:
                    return True
                continue
            target = secrets.restore_masks(w, cur) if masked else w
            if cur is MISSING or not strict_equal(cur, target):
                return True
        return False

    def set(self, path: PathT, value: Any, scope) -> None:
        self.values[path] = deep_copy(value) if value is not REMOVE else REMOVE
        if not self.dirty(path, scope):
            del self.values[path]
        self._notify()

    def discard(self, paths: Iterable[PathT]) -> None:
        changed = False
        for p in list(paths):
            if p in self.values:
                del self.values[p]
                changed = True
        if changed:
            self._notify()

    def stage(self, paths: Iterable[PathT], scope, block_paths: set[PathT] = frozenset(),
              origin: str = "field") -> int:
        """Stage working values for every usable agent of ``scope`` as one undo step.

        Returns the number of agents whose drafts changed.  Staged values leave the store.
        """
        paths = [p for p in paths if p in self.values]
        per_agent: dict[str, tuple[Any, str, list[EditOp]]] = {}
        self.skipped = []
        for key in scope.usable_keys:
            ops = []
            for p in paths:
                w = self.values[p]
                if w is REMOVE:
                    if scope.draft_value(key, p) is not MISSING:
                        ops.append(EditOp(OpKind.REMOVE, p, origin=origin))
                    continue
                if secrets.contains_mask(w):
                    # each agent keeps its own credentials; a mask that cannot be matched is never written
                    w = secrets.restore_masks(w, scope.draft_value(key, p))
                    if secrets.contains_mask(w):
                        self.skipped.append((key, p))
                        continue
                kind = OpKind.REPLACE_BLOCK if p in block_paths else OpKind.SET
                ops.append(EditOp(kind, p, deep_copy(w), origin))
            if ops:
                base, sha = scope.session.base_of(key)
                per_agent[key] = (base, sha, ops)
        changed = scope.session.drafts.apply_many(per_agent) if per_agent else set()
        if changed:             # nothing staged (the agents already have these values): the edit stays
            for p in paths:
                self.values.pop(p, None)
            self._notify()
        return len(changed)

    # --------------------------------------------------------------- chains
    def chain(self, key: str) -> ChainWork | None:
        return self.chains.get(key)

    def chain_rows(self, key: str, scope, ref: str | None) -> list[Row]:
        """What the chain table shows: the working copy, else the reference agent's chain."""
        w = self.chains.get(key)
        if w is not None:
            return w.rows
        if not ref:
            return []
        return rows_of(C.read_chain(scope, spec_for(key), ref))

    def work_chain(self, key: str, scope, ref: str | None) -> ChainWork:
        w = self.chains.get(key)
        if w is None:
            rows = [list(r) for r in self.chain_rows(key, scope, ref)]
            w = ChainWork(rows, noref=not ref)
            self.chains[key] = w
        return w

    def drop_chain(self, key: str) -> None:
        if self.chains.pop(key, None) is not None:
            self._notify()

    def edited(self) -> None:
        """Call after changing a working chain in place."""
        self._notify()

    def rebase(self, scope, ref: str | None) -> None:
        """A chain edited while no agent was checked: unedited cells fill from the reference agent."""
        if not ref:
            return
        for key, w in self.chains.items():
            if not w.noref:
                continue
            w.noref = False
            if w.removed:
                continue
            base = rows_of(C.read_chain(scope, spec_for(key), ref))
            if not base:
                continue
            if w.rows:
                if w.pm:
                    base[0][0], base[0][1] = w.rows[0][0], w.rows[0][1]
                if w.e:
                    base[0][2] = w.rows[0][2]
            w.rows = base[:1] + [list(r) for r in w.rows[1:]] if w.fb else base

    def chain_dirty(self, key: str, scope) -> bool:
        w = self.chains.get(key)
        if w is None:
            return False
        keys = scope.usable_keys
        if not keys:
            return True
        spec = spec_for(key)
        for k in keys:
            old = rows_of(C.read_chain(scope, spec, k))
            if apply_edits(w, old) != old:
                return True
        return False

    def stage_chain(self, key: str, scope, origin: str = "chain") -> int:
        """Stage the edited cells of chain ``key`` for every usable agent (one undo step)."""
        w = self.chains.get(key)
        if w is None or not scope.usable_keys:
            return 0
        per_agent = chain_ops_for(scope, key, w, origin)
        changed = scope.session.drafts.apply_many(per_agent) if per_agent else set()
        if changed:
            self.chains.pop(key, None)
            self._notify()
        return len(changed)

    def stage_chains(self, keys: list[str], scope, origin: str = "chain") -> int:
        """Stage several chains together (auxiliary tasks) as one undo step."""
        per_agent: dict[str, tuple[Any, str, list[EditOp]]] = {}
        for key in keys:
            w = self.chains.get(key)
            if w is None:
                continue
            for agent, (base, sha, ops) in chain_ops_for(scope, key, w, origin).items():
                per_agent.setdefault(agent, (base, sha, []))[2].extend(ops)
        changed = scope.session.drafts.apply_many(per_agent) if per_agent else set()
        if changed:
            for key in keys:
                self.chains.pop(key, None)
            self._notify()
        return len(changed)

    def clear(self) -> None:
        if self.values or self.chains:
            self.values.clear()
            self.chains.clear()
            self._notify()


def chain_ops_for(scope, key: str, w: ChainWork, origin: str) -> dict[str, tuple[Any, str, list[EditOp]]]:
    spec = spec_for(key)
    per_agent: dict[str, tuple[Any, str, list[EditOp]]] = {}
    for agent in scope.usable_keys:
        old = rows_of(C.read_chain(scope, spec, agent))
        new = apply_edits(w, old)
        if new == old:
            continue
        if not new:
            ops = entry_removal_ops(scope, spec, agent, origin)
            if ops:
                base, sha = scope.session.base_of(agent)
                per_agent[agent] = (base, sha, ops)
            continue
        head_changed = (new[:1] != old[:1])
        head = _as_assignment(new[0]) if head_changed and new else C.KEEP
        fallbacks = [_as_assignment(r) for r in new[1:]] if new[1:] != old[1:] else C.KEEP
        ops = C.chain_ops(scope, spec, agent, head, fallbacks, origin)
        if head_changed and new and old and (new[0][0] or None) != (old[0][0] or None):
            ops += endpoint_cleanup(scope, spec, agent, origin)
        if ops:
            base, sha = scope.session.base_of(agent)
            per_agent[agent] = (base, sha, ops)
    return per_agent


def entry_removal_ops(scope, spec: C.ChainSpec, agent: str, origin: str) -> list[EditOp]:
    """Remove an auxiliary task's model entry: its chain keys, or the whole block when nothing else is in it."""
    block = scope.draft_value(agent, spec.head)
    if not isinstance(block, dict):
        return []
    # the model's keys, and the endpoint keys that belonged to its provider (as on a provider change)
    owned = [k for k in block if k in TASK_CHAIN_KEYS or k in ("base_url", "api_mode")]
    if not owned:
        return []
    if len(owned) == len(block):
        return [EditOp(OpKind.REMOVE, spec.head, origin=f"{origin}:remove-entry")]
    return [EditOp(OpKind.REMOVE, spec.head + (k,), origin=f"{origin}:remove-entry") for k in owned]


def endpoint_cleanup(scope, spec: C.ChainSpec, agent: str, origin: str) -> list[EditOp]:
    """The provider changed: the old provider's endpoint keys no longer apply to the head."""
    ops = []
    for leaf in ("base_url", "api_mode"):
        if scope.draft_value(agent, spec.head + (leaf,)) is not MISSING:
            ops.append(EditOp(OpKind.REMOVE, spec.head + (leaf,), origin=f"{origin}:provider-changed"))
    return ops


def move_key_ops(scope, old: PathT, new: PathT, origin: str = "move") -> tuple[dict, list[str]]:
    """Per-agent ops that move each checked agent's own ``old`` value to ``new`` (a misspelled key).

    Returns ``(per_agent, conflicts)`` for ``drafts.apply_many``.  An agent whose ``new`` already
    holds the same value only loses ``old``; one whose ``new`` holds a different value is left
    alone and listed in ``conflicts`` (never overwritten).
    """
    per_agent, conflicts = {}, []
    for agent in scope.usable_keys:
        value = scope.draft_value(agent, old)
        if value is MISSING:
            continue
        target = scope.draft_value(agent, new)
        if target is MISSING:
            ops = [EditOp(OpKind.SET, new, deep_copy(value), origin=origin), EditOp(OpKind.REMOVE, old, origin=origin)]
        elif strict_equal(target, value):
            ops = [EditOp(OpKind.REMOVE, old, origin=origin)]
        else:
            conflicts.append(agent)
            continue
        base, sha = scope.session.base_of(agent)
        per_agent[agent] = (base, sha, ops)
    return per_agent, conflicts


def chain_staged(scope, key: str, agent: str) -> bool:
    """The agent's draft changes this chain (head, effort or fallbacks)."""
    spec = spec_for(key)
    rec = scope.session.record(agent)
    draft = scope.session.drafts.draft(agent)
    if rec is None or not rec.usable or draft is None or not draft.ops:
        return False
    paths = [spec.head, spec.fallbacks] + ([("agent", "reasoning_effort"), ("agent", "reasoning_overrides")]
                                          if spec.main_effort else [])
    if not any(draft.touches(p) for p in paths):
        return False
    return C.read_chain(scope, spec, agent) != C.read_chain(_BaseScope(scope.session), spec, agent)


def unstage_chain(scope, key: str) -> None:
    spec = spec_for(key)
    paths = [spec.head, spec.fallbacks]
    if spec.main_effort:
        paths += [("agent", "reasoning_effort"), ("agent", "reasoning_overrides")]
    keys = scope.usable_keys
    for p in paths:
        scope.session.drafts.revert(keys, p)


class _BaseScope:
    """Reads the loaded (not drafted) values, for comparing a draft with its file."""

    def __init__(self, session) -> None:
        self.session = session

    def draft_value(self, key: str, path: PathT) -> Any:
        from ..core.paths import get_in

        rec = self.session.record(key)
        return get_in(rec.data, path) if rec is not None and rec.usable else MISSING

