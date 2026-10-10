"""Field-level editing across the current edit scope.

The *edit scope* is either the viewed agent alone or every checked agent.
Editors ask :class:`EditScope` for a :class:`FieldState` (what to display) and
call its mutators (what to record).  Every mutation becomes per-agent
:class:`~hermes_config_manager.core.edits.EditOp` entries for exactly the edited path
- nothing else is copied between agents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.edits import EditOp, OpKind
from ..core.paths import MISSING, PathT, deep_copy, get_in
from ..core.yamlpatch import strict_equal

# Provenance labels shown next to values.
KEEP: Any = object()   # "leave this assignment component unchanged"

SRC_EXPLICIT = "explicit"        # present in the agent's config.yaml (draft view)
SRC_DEFAULT = "default"          # absent; runtime default applies (verified source)
SRC_UNKNOWN_DEFAULT = "unset"    # absent; no verified default known
SRC_MIXED = "mixed"
SRC_UNAVAILABLE = "unavailable"  # agent data not loaded / invalid / host offline


@dataclass(frozen=True)
class DefaultInfo:
    value: Any
    source: str            # e.g. "Hermes defaults"
    certain: bool = True   # False: snapshot-only / versions disagree


def canonical(value: Any) -> Any:
    """Hashable, type-strict representation for grouping mixed values.

    Scalars map to (type, value).  Containers use C-accelerated JSON, which keeps
    true/1/1.0/"1" distinct (the type strictness the UI needs) at a fraction of
    the cost of building nested tuples.
    """
    if value is MISSING:
        return ("<missing>",)
    if isinstance(value, (dict, list)):
        try:
            return ("json", json.dumps(value, sort_keys=True, ensure_ascii=False, default=repr))
        except (TypeError, ValueError):   # e.g. mixed-type keys cannot be sorted
            return ("repr", repr(value))
    return (type(value).__name__, value)


@dataclass
class FieldState:
    path: PathT
    targets: list[str]
    values: dict[str, Any]
    base_values: dict[str, Any]
    unavailable: list[str]
    default: DefaultInfo | None
    groups: list[tuple[Any, list[str]]] = field(default_factory=list)   # (value, agent keys)
    # Agents whose draft differs from the loaded value at this path (computed once).
    changed: list[str] = field(default_factory=list)

    @property
    def uniform(self) -> bool:
        return len(self.groups) <= 1

    @property
    def mixed(self) -> bool:
        return len(self.groups) > 1

    @property
    def value(self) -> Any:
        """Uniform draft value (MISSING if absent); first group's value if mixed."""
        return self.groups[0][0] if self.groups else MISSING

    @property
    def explicit(self) -> bool:
        return self.uniform and self.value is not MISSING

    @property
    def source(self) -> str:
        if not self.targets or len(self.unavailable) == len(self.targets):
            return SRC_UNAVAILABLE
        if self.mixed:
            return SRC_MIXED
        if self.value is not MISSING:
            return SRC_EXPLICIT
        return SRC_DEFAULT if self.default is not None else SRC_UNKNOWN_DEFAULT

    @property
    def modified(self) -> bool:
        return bool(self.changed)


class EditScope:
    """Reads and edits one path across a set of agents through the session."""

    def __init__(self, session, keys: list[str], viewed: str,
                 defaults: Callable[[PathT, str], DefaultInfo | None] | None = None) -> None:
        self.session = session
        self.keys = keys
        self.viewed = viewed
        self._defaults = defaults

    # -- scope description --------------------------------------------------
    @property
    def usable_keys(self) -> list[str]:
        return [k for k in self.keys if (r := self.session.record(k)) is not None and r.usable]

    def label(self) -> str:
        if not self.keys:
            return "No agent"
        if len(self.keys) == 1:
            rec = self.session.record(self.keys[0])
            return rec.display if rec else self.keys[0]
        return f"{len(self.keys)} checked agents"

    # -- reading ------------------------------------------------------------
    def draft_value(self, key: str, path: PathT) -> Any:
        rec = self.session.record(key)
        if rec is None or not rec.usable:
            return MISSING
        draft = self.session.drafts.draft(key)
        return draft.get(path) if draft else get_in(rec.data, path)

    def base_value(self, key: str, path: PathT) -> Any:
        rec = self.session.record(key)
        return get_in(rec.data, path) if rec is not None and rec.usable else MISSING

    def default_for(self, path: PathT, key: str | None = None) -> DefaultInfo | None:
        if self._defaults is None:
            return None
        rec = self.session.record(key or self.viewed) if (key or self.viewed) else None
        return self._defaults(path, rec.host if rec else "")

    def state(self, path: PathT) -> FieldState:
        values: dict[str, Any] = {}
        bases: dict[str, Any] = {}
        unavailable: list[str] = []
        groups: dict[Any, tuple[Any, list[str]]] = {}
        # Put the viewed agent first so a mixed display leads with its value.
        ordered = sorted(self.keys, key=lambda k: k != self.viewed)
        for key in ordered:
            rec = self.session.record(key)
            if rec is None or not rec.usable:
                unavailable.append(key)
                continue
            value = self.draft_value(key, path)
            values[key] = value
            bases[key] = get_in(rec.data, path)
            canon = canonical(value)
            if canon in groups:
                groups[canon][1].append(key)
            else:
                groups[canon] = (value, [key])
        # Only agents whose draft touches this path can differ from their loaded value.
        drafts = self.session.drafts
        changed = [k for k in values
                   if (d := drafts.draft(k)) is not None and d.ops and d.touches(path)
                   and not strict_equal(values[k], bases[k])]
        return FieldState(path, list(self.keys), values, bases, unavailable,
                          self.default_for(path), list(groups.values()), changed)

    def states(self, paths: list[PathT]) -> dict[PathT, FieldState]:
        """``state(path)`` for many paths at once (same results, per-agent work done once).

        A family page refreshes dozens of rows over 100+ agents; looking up each
        agent's record and draft once per refresh instead of once per row is
        what keeps that within the UI's time budget.
        """
        ordered = sorted(self.keys, key=lambda k: k != self.viewed)
        agents: list[tuple[str, Any, Any]] = []     # key, loaded data, draft (or None)
        unavailable: list[str] = []
        drafts = self.session.drafts
        for key in ordered:
            rec = self.session.record(key)
            if rec is None or not rec.usable:
                unavailable.append(key)
            else:
                d = drafts.draft(key)
                agents.append((key, rec.data, d if d is not None and d.ops else None))
        out: dict[PathT, FieldState] = {}
        for path in paths:
            values: dict[str, Any] = {}
            bases: dict[str, Any] = {}
            groups: dict[Any, tuple[Any, list[str]]] = {}
            changed: list[str] = []
            for key, data, draft in agents:
                base = get_in(data, path)
                value = draft.get(path) if draft is not None else base
                values[key] = value
                bases[key] = base
                canon = canonical(value)
                if canon in groups:
                    groups[canon][1].append(key)
                else:
                    groups[canon] = (value, [key])
                if draft is not None and draft.touches(path) and not strict_equal(value, base):
                    changed.append(key)
            out[path] = FieldState(path, list(self.keys), values, bases, list(unavailable),
                                   self.default_for(path), list(groups.values()), changed)
        return out

    # -- mutations ----------------------------------------------------------
    def _targets(self) -> dict[str, tuple[Any, str]]:
        out = {}
        for key in self.usable_keys:
            out[key] = self.session.base_of(key)
        return out

    def set(self, path: PathT, value: Any, origin: str = "field") -> None:
        self.session.drafts.apply(self._targets(), EditOp(OpKind.SET, path, deep_copy(value), origin))

    def clear(self, path: PathT, empty: Any) -> None:
        self.set(path, empty, origin="clear")

    def remove(self, path: PathT) -> None:
        """Remove the override so the runtime default applies."""
        self.session.drafts.apply(self._targets(), EditOp(OpKind.REMOVE, path, origin="remove"))

    def revert(self, path: PathT) -> None:
        self.session.drafts.revert(self.usable_keys, path)

    # -- model assignments (see _assignment_state / _set_assignment below) --

# ---------------------------------------------------------------- assignments


def assignment_ops(path: PathT, block: Any, provider: Any = KEEP, model: Any = KEEP, effort: Any = KEEP,
                   origin: str = "field") -> list[EditOp]:
    """Ops that patch provider/model/reasoning_effort of the block at ``path``.

    ``None`` removes that key; ``KEEP`` leaves it; nothing else in the block is
    touched.  For the primary model the id is written to ``model.default`` and
    stale ``model``/``name`` aliases are removed - the same canonicalisation the
    runtime performs on load.  Single source of truth for pickers, presets and copies.
    """
    from ..core.assignments import PRIMARY_MODEL_ALIASES, model_key_for

    ops: list[EditOp] = []
    for comp, value in (("provider", provider), ("model", model), ("reasoning_effort", effort)):
        if value is KEEP:
            continue
        leaf = model_key_for(block if block is not MISSING else None, path) if comp == "model" else comp
        if value is None:
            if isinstance(block, dict) and leaf in block:
                ops.append(EditOp(OpKind.REMOVE, path + (leaf,), origin=origin))
        else:
            ops.append(EditOp(OpKind.SET, path + (leaf,), value, origin))
        if comp == "model" and path == ("model",) and isinstance(block, dict):
            for alias in PRIMARY_MODEL_ALIASES:
                if alias in block and isinstance(block[alias], (str, type(None))):
                    ops.append(EditOp(OpKind.REMOVE, path + (alias,), origin=f"{origin}:canonicalise"))
    return ops

