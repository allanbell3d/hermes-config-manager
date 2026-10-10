"""Model chains: a head assignment plus an ordered fallback list.

Primary model, delegation and every auxiliary task have the same shape, so
the UI edits all of them with one component:

==============  ===================================  ======================================
chain           head (row 0)                          fallbacks (rows 1..n)
==============  ===================================  ======================================
primary         ``model`` (provider, ``default``)      ``fallback_providers[]``
                effort: ``agent.reasoning_effort``
                (or the matching ``agent.reasoning_overrides`` entry)
delegation      ``delegation``                         ``delegation.fallback_providers[]``
aux task *T*    ``auxiliary.T``                        ``auxiliary.T.fallback_chain[]``
==============  ===================================  ======================================

The primary model's effort is *not* ``model.reasoning_effort``: the runtime
ignores that key for the main model (``resolve_reasoning_config``).

Edits are patches: only head components that change are written, the
fallback list is set as one value, and extra keys of a fallback item
(``base_url``, ``api_mode``...) are kept when the same provider/model stays
in the list.  Everything goes through the draft store as one undo step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from ..core.assignments import ModelAssignment, _model_variants, list_item, read_assignment
from ..core.edits import EditOp, OpKind
from ..core.paths import MISSING, PathT, deep_copy
from .editing import KEEP, assignment_ops

Chain = tuple[ModelAssignment, ...]

# The "Default" of a provider/model cell, as Hermes writes an unconfigured auxiliary task:
# ``provider: auto`` and ``model: ''`` (Hermes chooses the model).
AUTO_PM = ("auto", "")


@dataclass(frozen=True)
class ChainSpec:
    key: str                       # "primary" | "delegation" | "aux:<task>"
    label: str
    head: PathT                    # path of the head block
    fallbacks: PathT               # path of the fallback list
    main_effort: bool = False      # effort lives under agent.* (primary model only)

    @property
    def task(self) -> str | None:
        return self.key.split(":", 1)[1] if self.key.startswith("aux:") else None


PRIMARY = ChainSpec("primary", "Primary model", ("model",), ("fallback_providers",), main_effort=True)
DELEGATION = ChainSpec("delegation", "Delegation", ("delegation",), ("delegation", "fallback_providers"))


def aux_spec(task: str) -> ChainSpec:
    return ChainSpec(f"aux:{task}", task, ("auxiliary", task), ("auxiliary", task, "fallback_chain"))


# ------------------------------------------------------------------ reading
def _main_effort(scope, key: str, model: str | None) -> tuple[str | None, PathT]:
    """(effort, path holding it) for the primary model in the draft view."""
    overrides = scope.draft_value(key, ("agent", "reasoning_overrides"))
    if isinstance(overrides, dict) and model:
        for variant in _model_variants(model):
            if overrides.get(variant) not in (None, ""):
                return str(overrides[variant]), ("agent", "reasoning_overrides", variant)
    effort = scope.draft_value(key, ("agent", "reasoning_effort"))
    if effort is MISSING or effort in (None, ""):
        return None, ("agent", "reasoning_effort")
    if isinstance(effort, bool):
        return ("none" if effort is False else None), ("agent", "reasoning_effort")
    return str(effort).lower(), ("agent", "reasoning_effort")


def read_chain(scope, spec: ChainSpec, key: str) -> Chain:
    block = scope.draft_value(key, spec.head)
    head = read_assignment(None if block is MISSING else block, spec.head)
    if spec.main_effort:
        effort, _ = _main_effort(scope, key, head.model)
        head = ModelAssignment(head.provider, head.model, effort)
    items = scope.draft_value(key, spec.fallbacks)
    rows = [read_assignment(item) for item in items] if isinstance(items, list) else []
    return (head, *rows)


def variants(scope, spec: ChainSpec, keys: Iterable[str] | None = None) -> list[tuple[Chain, list[str]]]:
    """Distinct chains across the scope, largest group first (viewed agent breaks ties)."""
    groups: dict[Chain, list[str]] = {}
    for key in (scope.usable_keys if keys is None else keys):
        groups.setdefault(read_chain(scope, spec, key), []).append(key)
    viewed = scope.viewed
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), viewed not in kv[1]))


# ------------------------------------------------------------------ writing
def _component(new: str | None, old: str | None) -> Any:
    return KEEP if new == old else new          # None removes the key


def chain_ops(scope, spec: ChainSpec, key: str, head: ModelAssignment | Any = KEEP,
              fallbacks: Sequence[ModelAssignment] | Any = KEEP, origin: str = "chain") -> list[EditOp]:
    """Patch operations that turn this agent's chain into (head, fallbacks).

    ``KEEP`` leaves that part as it is (e.g. "set the model of 9 auxiliary
    tasks" changes heads only).
    """
    ops: list[EditOp] = []
    current = read_chain(scope, spec, key)
    if head is not KEEP:
        old = current[0]
        block = scope.draft_value(key, spec.head)
        if spec.main_effort:
            ops += assignment_ops(spec.head, block, _component(head.provider, old.provider),
                                  _component(head.model, old.model), KEEP, origin)
            # Effort follows the model the runtime will resolve it for.
            effort, where = _main_effort(scope, key, head.model)
            if head.reasoning_effort != effort:
                if head.reasoning_effort is None:
                    ops.append(EditOp(OpKind.REMOVE, where, origin=origin))
                else:
                    ops.append(EditOp(OpKind.SET, where, head.reasoning_effort, origin))
        else:
            ops += assignment_ops(spec.head, block, _component(head.provider, old.provider),
                                  _component(head.model, old.model),
                                  _component(head.reasoning_effort, old.reasoning_effort), origin)
    if fallbacks is not KEEP and tuple(fallbacks) != current[1:]:
        existing = scope.draft_value(key, spec.fallbacks)
        templates = {}
        if isinstance(existing, list):
            for item in existing:
                a = read_assignment(item)
                templates.setdefault(a.identity, item)
        items = [list_item(a, templates.get(a.identity)) for a in fallbacks if not a.empty]
        if items or existing is not MISSING:
            ops.append(EditOp(OpKind.SET, spec.fallbacks, deep_copy(items), origin))
    return ops


def chain_label(chain: Chain, with_effort: bool = True) -> str:
    parts = [a.short_label() if with_effort else (a.model or "—").rsplit("/", 1)[-1] for a in chain]
    return " → ".join(parts) if parts else "—"

