"""Model catalog: every known (provider, model) identity with labelled sources.

Sources (each entry records which ones vouch for it):

* ``file``            - the Markdown catalog (``data/catalog-example.md``
                        format: ``- `provider: model` `` lines).
* ``runtime:<host>``  - Hermes' own ``provider_models_cache.json`` on a host
                        (models the runtime lists as reachable there).
* ``custom:<agent>``  - ``custom_providers[].models`` in an agent's config.
* ``in-use``          - currently assigned somewhere on some agent.

Metadata (context size, reasoning options, vision, tool use, cost) comes
from Hermes' ``models_dev_cache.json`` and custom-provider entries, and is
labelled with where it came from.  Identity is exact: similarly named models
are never merged, and custom identifiers are always accepted by pickers.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import assignments as A

_LINE = re.compile(r"^\s*[-*]\s*`\s*([^:`]+?)\s*:\s*([^`]+?)\s*`")

# Hermes provider id -> models.dev provider ids to try (best effort, labelled
# as such in the UI).  Anything unmapped falls back to exact model-id matches.
PROVIDER_METADATA_MAP: dict[str, tuple[str, ...]] = {
    "zai": ("zai", "zhipuai"),
    "gemini": ("google",),
    "vertex": ("google-vertex", "google"),
    "xai": ("xai",),
    "xai-oauth": ("xai",),
    "openai": ("openai",),
    "openai-api": ("openai",),
    "openai-codex": ("openai",),
    "anthropic": ("anthropic",),
    "deepseek": ("deepseek",),
    "nvidia": ("nvidia",),
    "copilot": ("github-copilot",),
    "openrouter": ("openrouter",),
    "groq": ("groq",),
    "mistral": ("mistral",),
    "alibaba": ("alibaba",),
    "moonshot": ("moonshotai",),
    "minimax": ("minimax",),
}


@dataclass
class ModelInfo:
    provider: str
    model: str
    sources: set[str] = field(default_factory=set)
    name: str | None = None
    description: str | None = None
    context: int | None = None
    max_output: int | None = None
    reasoning: bool | None = None
    efforts: tuple[str, ...] | None = None      # supported reasoning_effort values, if published
    vision: bool | None = None
    tool_call: bool | None = None
    cost_in: float | None = None                 # USD per 1M tokens
    cost_out: float | None = None
    deprecated: bool = False
    capabilities: tuple[str, ...] = ()
    meta_source: str | None = None               # e.g. "models.dev · xai" or "custom provider · host-a/main"
    use_count: int = 0
    used_in: set[str] = field(default_factory=set)   # slot groups: Primary, Delegation, ...
    search: str = ""

    @property
    def identity(self) -> tuple[str, str]:
        return (self.provider, self.model)

    def source_labels(self) -> list[str]:
        out = []
        if "file" in self.sources:
            out.append("catalog file")
        hosts = sorted(s.split(":", 1)[1] for s in self.sources if s.startswith("runtime:"))
        if hosts:
            out.append("runtime: " + ", ".join(hosts))
        if any(s.startswith("custom:") for s in self.sources):
            out.append("custom provider")
        if "in-use" in self.sources:
            out.append(f"in use ×{self.use_count}")
        if "manual" in self.sources:
            out.append("manual entry")
        return out


def parse_markdown_catalog(text: str) -> list[tuple[str, str]]:
    """Parse ``- `provider: model` `` lines; order preserved, duplicates removed."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = _LINE.match(line)
        if not match:
            continue
        ident = (match.group(1).strip(), match.group(2).strip())
        if ident[0] and ident[1] and ident not in seen:
            seen.add(ident)
            out.append(ident)
    return out


def parse_runtime_provider_cache(data: Any) -> dict[str, list[str]]:
    """``provider_models_cache.json`` → {provider: [model, ...]}.

    Entries keyed ``custom:<url>[#fp]`` describe custom endpoints; they are
    kept under that literal key so they are never confused with named providers.
    """
    out: dict[str, list[str]] = {}
    if not isinstance(data, dict):
        return out
    for provider, entry in data.items():
        models = entry.get("models") if isinstance(entry, dict) else None
        if isinstance(models, list):
            out[str(provider)] = [str(m) for m in models if isinstance(m, (str, int))]
    return out


def compact_models_dev(data: Any) -> dict[str, dict[str, dict]]:
    """Reduce the 5 MB models.dev cache to {provider: {model_id: meta}}."""
    out: dict[str, dict[str, dict]] = {}
    if not isinstance(data, dict):
        return out
    for pid, prov in data.items():
        models = prov.get("models") if isinstance(prov, dict) else None
        if not isinstance(models, dict):
            continue
        slim = {}
        for mid, m in models.items():
            if not isinstance(m, dict):
                continue
            efforts = None
            for opt in m.get("reasoning_options") or []:
                if isinstance(opt, dict) and opt.get("type") == "effort" and isinstance(opt.get("values"), list):
                    efforts = tuple(str(v) for v in opt["values"])
            limit = m.get("limit") or {}
            cost = m.get("cost") or {}
            mods = (m.get("modalities") or {}).get("input") or []
            slim[str(mid)] = {
                "name": m.get("name"),
                "description": m.get("description"),
                "context": limit.get("context") or None,
                "output": limit.get("output") or None,
                "reasoning": m.get("reasoning"),
                "efforts": efforts,
                "vision": ("image" in mods) if mods else None,
                "tool_call": m.get("tool_call"),
                "cost_in": cost.get("input"),
                "cost_out": cost.get("output"),
                "deprecated": m.get("status") == "deprecated",
            }
        out[str(pid)] = slim
    return out


class Catalog:
    """Immutable index; rebuild (cheaply) when sources change."""

    def __init__(self, entries: dict[tuple[str, str], ModelInfo], efforts: list[str],
                 file_path: str | None = None, notes: list[str] | None = None) -> None:
        self._entries = entries
        self._by_provider: dict[str, list[ModelInfo]] = defaultdict(list)
        self._by_model: dict[str, list[ModelInfo]] = defaultdict(list)
        for info in sorted(entries.values(), key=lambda i: (i.provider.casefold(), i.model.casefold())):
            self._by_provider[info.provider].append(info)
            self._by_model[info.model].append(info)
        self.efforts = efforts
        self.file_path = file_path
        self.notes = notes or []

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[ModelInfo]:
        return [i for p in self.providers() for i in self._by_provider[p]]

    def providers(self) -> list[str]:
        return sorted(self._by_provider, key=str.casefold)

    def models_for(self, provider: str) -> list[ModelInfo]:
        return list(self._by_provider.get(provider, []))

    def get(self, provider: str | None, model: str | None) -> ModelInfo | None:
        return self._entries.get((provider or "", model or ""))

    def search(self, text: str = "", provider: str | None = None, *, only_runtime: bool = False,
               only_in_use: bool = False, reasoning: bool | None = None, vision: bool | None = None) -> list[ModelInfo]:
        terms = [t for t in text.casefold().split() if t]
        pool = self.models_for(provider) if provider else self.entries()
        out = []
        for info in pool:
            if only_runtime and not any(s.startswith("runtime:") for s in info.sources):
                continue
            if only_in_use and "in-use" not in info.sources:
                continue
            if reasoning is not None and info.reasoning is not reasoning:
                continue
            if vision is not None and info.vision is not vision:
                continue
            if terms and not all(t in info.search for t in terms):
                continue
            out.append(info)
        return out


def _metadata_for(provider: str, model: str, mdev: dict[str, dict[str, dict]]) -> tuple[dict | None, str | None]:
    for pid in PROVIDER_METADATA_MAP.get(provider, ()):
        prov = mdev.get(pid)
        if prov:
            meta = prov.get(model) or (prov.get(model.split("/", 1)[1]) if "/" in model else None)
            if meta:
                return meta, f"models.dev · {pid}"
    # Exact model-id match anywhere (heuristic; labelled as such).
    candidates = [(pid, prov[model]) for pid, prov in mdev.items() if model in prov]
    if len(candidates) == 1:
        return candidates[0][1], f"models.dev · {candidates[0][0]} (matched by model id)"
    return None, None


def build_catalog(
    file_entries: Iterable[tuple[str, str]] = (),
    runtime: dict[str, dict[str, list[str]]] | None = None,     # host -> provider -> models
    models_dev: dict[str, dict[str, dict]] | None = None,
    agents: Iterable[Any] = (),                                  # AgentRecord-like: .key, .data, .usable, .display
    observed_efforts: Iterable[str] = (),
    runtime_efforts: Iterable[str] = (),
    file_path: str | None = None,
    manual: Iterable[tuple[str, str]] = (),
) -> Catalog:
    entries: dict[tuple[str, str], ModelInfo] = {}

    def add(provider: str, model: str, source: str) -> ModelInfo:
        key = (provider, model)
        info = entries.get(key)
        if info is None:
            info = ModelInfo(provider, model)
            entries[key] = info
        info.sources.add(source)
        return info

    for provider, model in file_entries:
        add(provider, model, "file")
    for host, providers in (runtime or {}).items():
        for provider, models in providers.items():
            if provider.startswith("custom:"):
                continue  # endpoint-keyed; custom providers are covered by agent configs
            for model in models:
                add(provider, model, f"runtime:{host}")
    for provider, model in manual:
        add(provider, model, "manual")

    efforts: list[str] = []
    for value in list(runtime_efforts) + list(observed_efforts):
        if value and value not in efforts:
            efforts.append(value)

    for rec in agents:
        if not getattr(rec, "usable", False):
            continue
        data = rec.data
        for cp in data.get("custom_providers") or []:
            if not isinstance(cp, dict) or not cp.get("name"):
                continue
            models = cp.get("models")
            if isinstance(models, dict):
                for mid, meta in models.items():
                    info = add(str(cp["name"]), str(mid), f"custom:{rec.key}")
                    if isinstance(meta, dict) and info.meta_source is None:
                        caps = tuple(str(c) for c in meta.get("capabilities") or [])
                        info.capabilities = caps
                        info.reasoning = "reasoning" in caps if caps else None
                        info.vision = bool(meta.get("supports_vision")) or ("vision_understanding" in caps) if caps or "supports_vision" in meta else None
                        info.context = meta.get("context_length")
                        info.meta_source = f"custom provider · {rec.display}"
        for slot, assignment in A.all_assignments(data):
            if assignment.provider and assignment.model:
                info = add(assignment.provider, assignment.model, "in-use")
                info.use_count += 1
                info.used_in.add(slot.group)
            if assignment.reasoning_effort and assignment.reasoning_effort not in efforts:
                efforts.append(assignment.reasoning_effort)

    notes = []
    if models_dev:
        for info in entries.values():
            if info.meta_source:
                continue
            meta, src = _metadata_for(info.provider, info.model, models_dev)
            if meta:
                info.name = meta.get("name")
                info.description = meta.get("description")
                info.context = meta.get("context")
                info.max_output = meta.get("output")
                info.reasoning = meta.get("reasoning")
                info.efforts = meta.get("efforts")
                info.vision = meta.get("vision")
                info.tool_call = meta.get("tool_call")
                info.cost_in = meta.get("cost_in")
                info.cost_out = meta.get("cost_out")
                info.deprecated = bool(meta.get("deprecated"))
                info.meta_source = src
    else:
        notes.append("No models.dev metadata loaded; context/reasoning details unavailable.")

    for info in entries.values():
        info.search = " ".join(
            x for x in (info.provider, info.model, info.name or "", info.description or "",
                        " ".join(info.capabilities)) if x
        ).casefold()
    return Catalog(entries, efforts, file_path, notes)
