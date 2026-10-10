"""Model assignments and where they live in a Hermes configuration.

A *model assignment* is the recurring block ``{provider, model|default,
reasoning_effort, ...}``.  A *slot* is one place that holds an assignment
(primary model, delegation, one auxiliary task, one MoA reference model, ...)
or an ordered list of them (fallback chains, MoA reference models).

Key-name facts (verified in the Hermes runtime source):

* The primary model id is ``model.default``.  The runtime canonicalises the
  aliases ``model.model`` and ``model.name`` into it (precedence default >
  model > name) and drops them on its next save
  (hermes_cli/config.py ``_normalize_root_model_keys``).  Reading follows the
  same precedence; writing uses ``default`` and removes stale aliases.
* The main model's reasoning effort is ``agent.reasoning_overrides[<model>]``,
  else ``agent.reasoning_effort`` (hermes_constants ``resolve_reasoning_config``).
  ``model.reasoning_effort`` is not read for the main model.
* Other blocks (delegation, auxiliary tasks, fallback items, MoA slots) use
  ``provider`` / ``model`` / ``reasoning_effort``.  Some delegation blocks also
  carry ``default``; the runtime reads ``delegation.model``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .paths import MISSING, PathT, get_in

ASSIGNMENT_KEYS = ("provider", "model", "default", "reasoning_effort")


@dataclass(frozen=True)
class ModelAssignment:
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    @property
    def identity(self) -> tuple[str, str]:
        return (self.provider or "", self.model or "")

    @property
    def empty(self) -> bool:
        return not (self.provider or self.model or self.reasoning_effort)

    def short_label(self) -> str:
        """Compact table form: ``model · effort``.

        Vendor prefixes of the model id (``google/``, ``z-ai/``) are dropped so a
        narrow column still shows the distinguishing part; tooltips and search
        use the full ``provider / model`` label.
        """
        if self.empty:
            return "—"
        text = self.model.rsplit("/", 1)[-1] if self.model else f"{self.provider or '?'} / ?"
        return f"{text} · {self.reasoning_effort}" if self.reasoning_effort else text

    def label(self, with_effort: bool = True) -> str:
        if self.empty:
            return "—"
        if self.provider == "auto" and not self.model:
            text = "auto (Hermes chooses)"      # auxiliary tasks: provider auto, no fixed model
        else:
            text = f"{self.provider or '?'} / {self.model or '?'}"
        if with_effort and self.reasoning_effort:
            text += f"  ({self.reasoning_effort})"
        return text


@dataclass(frozen=True)
class SlotSpec:
    """One place holding an assignment (``kind='single'``) or a list (``'list'``)."""

    key: str                    # stable id, e.g. "primary", "aux:vision", "moa-ref:frontier-gpt"
    label: str
    group: str                  # Primary | Fallbacks | Delegation | Auxiliary | MoA | Other
    path: PathT                 # block path (single) or list path (list)
    kind: str = "single"
    model_key: str = "model"    # key holding the model id for this block
    description: str = ""


def _str(value: Any) -> str | None:
    if value is MISSING or value is None:
        return None
    return str(value)


PRIMARY_MODEL_ALIASES = ("model", "name")


def model_key_for(block: Any, path: PathT) -> str:
    """Which key holds the model id at ``path`` (``default`` for the primary model)."""
    return "default" if path == ("model",) else "model"


def read_assignment(block: Any, path: PathT = ()) -> ModelAssignment:
    if isinstance(block, str):  # legacy: `model: provider/model` string
        return ModelAssignment(None, block, None)
    if not isinstance(block, dict):
        return ModelAssignment()
    if path == ("model",):
        model = block.get("default") or block.get("model") or block.get("name")
    else:
        model = block.get("model")
        if model is None:
            model = block.get("default")
    return ModelAssignment(_str(block.get("provider")), _str(model), _str(block.get("reasoning_effort")))


def _model_variants(model: str) -> list[str]:
    """Spelling variants used by the runtime's per-model override lookup (subset)."""
    out = [model, model.replace(".", "-"), model.replace("-", ".")]
    if "/" in model:
        tail = model.split("/")[-1]
        out += [tail, tail.replace(".", "-"), tail.replace("-", ".")]
    seen, result = set(), []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            result.append(v)
    return result


def effective_main_effort(data: Any) -> tuple[str | None, str]:
    """(effort, source) the runtime uses for the main model.

    Mirrors ``resolve_reasoning_config``: a matching ``agent.reasoning_overrides``
    entry, else ``agent.reasoning_effort``; unset means the provider default.
    The override match covers exact, dot/dash and provider-stripped spellings.
    """
    agent = get_in(data, ("agent",), None)
    agent = agent if isinstance(agent, dict) else {}
    model = primary(data).model or ""
    overrides = agent.get("reasoning_overrides")
    if isinstance(overrides, dict) and model:
        for variant in _model_variants(model):
            if variant in overrides and overrides[variant] not in (None, ""):
                return str(overrides[variant]), f"agent.reasoning_overrides[{variant!r}]"
    effort = agent.get("reasoning_effort")
    if effort not in (None, ""):
        return str(effort).lower() if not isinstance(effort, bool) else ("none" if effort is False else None), "agent.reasoning_effort"
    return None, "provider default (agent.reasoning_effort not set)"


def assignment_at(data: Any, path: PathT) -> ModelAssignment:
    return read_assignment(get_in(data, path, None), path)


def assignment_list_at(data: Any, path: PathT) -> list[ModelAssignment]:
    value = get_in(data, path, None)
    if not isinstance(value, list):
        return []
    return [read_assignment(item) for item in value]


def primary(data: Any) -> ModelAssignment:
    return assignment_at(data, ("model",))


def list_item(assignment: ModelAssignment, template: Any = None) -> dict[str, Any]:
    """A fallback/MoA list item; keeps extra keys of ``template`` when given."""
    item: dict[str, Any] = {}
    if isinstance(template, dict):
        item.update({k: v for k, v in template.items() if k not in ASSIGNMENT_KEYS})
    if assignment.provider:
        item["provider"] = assignment.provider
    if assignment.model is not None:              # model '' is kept: provider auto, Hermes chooses
        item["model"] = assignment.model
    if assignment.reasoning_effort:
        item["reasoning_effort"] = assignment.reasoning_effort
    # Stable, readable key order: provider, model, reasoning_effort, extras.
    ordered = {k: item[k] for k in ("provider", "model", "reasoning_effort") if k in item}
    ordered.update({k: v for k, v in item.items() if k not in ordered})
    return ordered


# Keys under `auxiliary` that are settings, not tasks (observed in live configs).
AUX_NON_TASK_KEYS = {"transient_retries", "free_only", "openrouter_model", "stream_only_base_urls"}


def auxiliary_tasks(data: Any, known: Iterable[str] = ()) -> list[str]:
    aux = get_in(data, ("auxiliary",), None)
    names = list(known)
    if isinstance(aux, dict):
        for name, value in aux.items():
            if name not in AUX_NON_TASK_KEYS and isinstance(value, dict) and name not in names:
                names.append(name)
    return names


def moa_presets(data: Any) -> list[str]:
    presets = get_in(data, ("moa", "presets"), None)
    return list(presets.keys()) if isinstance(presets, dict) else []


def slots_for(data: Any, aux_known: Iterable[str] = ()) -> list[SlotSpec]:
    """Every model slot present in (or supported for) ``data``."""
    slots = [
        SlotSpec("primary", "Primary model", "Primary", ("model",), model_key="default"),
        SlotSpec("fallback", "Fallback chain", "Fallbacks", ("fallback_providers",), "list"),
        SlotSpec("delegation", "Delegation model", "Delegation", ("delegation",)),
        SlotSpec("delegation-fallback", "Delegation fallbacks", "Delegation", ("delegation", "fallback_providers"), "list"),
    ]
    for task in auxiliary_tasks(data, aux_known):
        slots.append(SlotSpec(f"aux:{task}", task, "Auxiliary", ("auxiliary", task)))
        slots.append(SlotSpec(f"aux-fallback:{task}", f"{task} fallbacks", "Auxiliary", ("auxiliary", task, "fallback_chain"), "list"))
    for preset in moa_presets(data):
        slots.append(SlotSpec(f"moa-ref:{preset}", f"{preset} reference models", "MoA", ("moa", "presets", preset, "reference_models"), "list"))
        slots.append(SlotSpec(f"moa-agg:{preset}", f"{preset} aggregator", "MoA", ("moa", "presets", preset, "aggregator")))
    slots.append(SlotSpec("x_search", "X search", "Other", ("x_search",)))
    slots.append(SlotSpec("image_gen", "Image generation", "Other", ("image_gen",)))
    return slots


def all_assignments(data: Any) -> list[tuple[SlotSpec, ModelAssignment]]:
    """Flatten every assignment in ``data`` (lists expanded) - used by the catalog."""
    out: list[tuple[SlotSpec, ModelAssignment]] = []
    for slot in slots_for(data):
        if slot.kind == "list":
            for item in assignment_list_at(data, slot.path):
                if not item.empty:
                    out.append((slot, item))
        else:
            a = assignment_at(data, slot.path)
            if not a.empty:
                out.append((slot, a))
    return out
