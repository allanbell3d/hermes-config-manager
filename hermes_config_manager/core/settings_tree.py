"""The settings the UI shows: sections of nested nodes, grouped into categories and pages.

Built from the public schema (``data/hermes_schema.json``):

* ``defaults_tree``: every setting a Hermes dashboard export contains, nested exactly as in
  ``config.yaml`` and in Hermes' own order, with its default value;
* field metadata: descriptions, closed value sets, secret flags.

Keys the loaded agents have that Hermes does not export are added as well (``extra`` when no
documentation knows them), so nothing in a config file is out of reach.  So are the keys Hermes
reads without exporting them (schema fields with ``row``, e.g. ``discord.allow_bots``).  An
``extra`` key that looks like a misspelling of a known key gets a ``suggest``ion.  The grouping of
top-level keys into sections and categories comes from :mod:`families`; the default pages
are :data:`PAGE_PLAN`.

A *section* is one top-level key (plus the top-level scalars that belong with it).  Its own
mapping keys sit directly on the page; nested mappings are *group* nodes.  The model chains
(primary model, delegation, auxiliary tasks) are edited by a dedicated table, so the keys it
owns are not nodes.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import secrets
from .families import build_tree
from .paths import MISSING, PathT

# Keys the chain tables own (not shown as rows).
CHAIN_OWNED: set[PathT] = {
    ("model", "provider"), ("model", "default"), ("model", "model"), ("model", "name"),
    ("model", "reasoning_effort"), ("fallback_providers",),
    ("delegation", "provider"), ("delegation", "model"), ("delegation", "default"),
    ("delegation", "reasoning_effort"), ("delegation", "fallback_providers"),
}
# Keys of an auxiliary task block that its chain table owns.
TASK_CHAIN_KEYS = frozenset({"provider", "model", "default", "reasoning_effort", "fallback_chain"})
CHAIN_OF_SECTION = {"model": "primary", "delegation": "delegation", "auxiliary": "auxiliary"}
# Not a config.yaml key: the hermes-teammates plugin's rosters (teammates.yaml next to each config.yaml),
# edited by their own table (gui.roster_table) on the page the plan gives it.
TEAMMATES_SECTION = "hermes-teammates"
MAIN_EFFORT: PathT = ("agent", "reasoning_effort")
DEFAULT_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"]

# Default pages: (page label, section keys) per category.  Section keys stay as in the file;
# page labels that are not a section key are ours.  Sections not listed fall into an "Other"
# page of their category.
PAGE_PLAN: dict[str, list[tuple[str, list[str]]]] = {
    "models": [("Models", ["model", "delegation", "auxiliary"]),
               ("Teammates", ["hermes-teammates"]),
               ("Advanced", ["moa", "x_search", "image_gen", "vision", "model_overrides"])],
    "providers": [("Providers", ["providers", "custom_providers", "openrouter", "bedrock", "vertex", "nous",
                                 "local_runtime", "model_catalog", "models_dev", "prompt_caching",
                                 "credential_pool_strategies", "auth"])],
    "agent": [("agent", ["agent"]),
              ("Context & memory", ["compression", "context", "memory", "checkpoints", "lcm", "curator"]),
              ("Other", ["human_delay", "goals", "loops", "honcho", "personalities"])],
    "tools": [("Terminal & code", ["terminal", "code_execution", "computer_use", "lsp"]),
              ("Web & browser", ["web", "browser"]),
              ("Tools & toolsets", ["toolsets", "tools", "tool_output", "tool_loop_guardrails", "mcp",
                                    "quick_commands"]),
              ("Skills, plugins & hooks", ["skills", "plugins", "hooks"]),
              ("Automation", ["cron", "kanban"])],
    "interface": [("display", ["display"]),
                  ("Dashboard & desktop", ["dashboard", "desktop", "bot_desktop", "streaming", "onboarding",
                                           "attachments"])],
    "messaging": [("Gateway & sessions", ["gateway", "sessions", "session", "session_reset", "bot_mode",
                                          "platform_hints"]),
                  ("Platforms", ["discord", "telegram", "whatsapp", "slack", "mattermost", "matrix"])],
    "voice": [("Voice", ["tts", "stt", "voice", "wake_word"])],
    "safety": [("Safety", ["approvals", "security", "secrets", "privacy", "vault", "proxy"])],
    "system": [("System", ["runtime", "logging", "network", "updates", "database", "monitoring", "telemetry",
                           "doctor"])],
}
# Settings from other sections repeated on a page ("Related settings"); the user edits this list.
DEFAULT_PINS: dict[str, list[PathT]] = {
    "models/Models": [("agent", "api_max_retries"), ("agent", "gateway_timeout"),
                      ("auxiliary", "transient_retries"), ("fallback", "min_switch_reset_seconds"),
                      ("model_context_length",)],
}
# Hermes' own dashboard menu (flat), for the optional "Hermes dashboard order" arrangement.
HERMES_MENU = ["agent", "terminal", "display", "delegation", "memory", "compression", "security", "browser",
               "voice", "tts", "stt", "logging", "discord", "auxiliary"]

_NOT_SECRET_SUFFIXES = ("_env", "_chars", "_flow", "_file", "_path")


@dataclass(eq=False)
class Node:
    key: str
    path: PathT
    kind: str                         # group | bool | int | float | str | enum | list | block
    section: str
    default: Any = MISSING            # MISSING: no default known
    desc: str = ""
    options: tuple = ()
    secret: bool = False
    extra: bool = False               # not documented anywhere (only in the agents' files)
    status: str = ""                  # "deprecated": Hermes keeps the key but it does nothing
    suggest: PathT | None = None      # extra key: the known key it most likely means
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None

    @property
    def is_group(self) -> bool:
        return self.kind == "group"

    @property
    def label(self) -> str:
        return self.key


@dataclass(eq=False)
class Section:
    key: str
    label: str
    category: str
    keys: tuple[str, ...]
    nodes: list[Node]
    chain: str | None = None          # primary | delegation | auxiliary
    in_hermes: bool = True

    def own_nodes(self) -> list[Node]:
        """Rows of the section outside its chain table."""
        if self.chain == "primary":
            return [n for n in self.nodes if n.path != (self.key,)]
        if self.chain == "auxiliary":
            return [n for n in self.nodes if not n.is_group]
        return self.nodes


@dataclass
class SettingsModel:
    sections: dict[str, Section]
    categories: list[tuple[str, str, list[str]]]      # (id, label, section keys)
    nodes: dict[PathT, Node]
    tasks: list[str]                                  # auxiliary task names, in order
    no_effort: set[str]                               # tasks without a reasoning effort
    efforts: list[str]

    def section_of(self, path: PathT) -> str | None:
        node = self.nodes.get(tuple(path))
        return node.section if node else None

    def task_node(self, task: str) -> Node | None:
        return self.nodes.get(("auxiliary", task))

    def task_nodes(self, task: str) -> list[Node]:
        node = self.task_node(task)
        return [c for c in node.children if c.key not in TASK_CHAIN_KEYS] if node else []

    def task_of(self, path: PathT) -> str | None:
        path = tuple(path)
        if len(path) > 2 and path[0] == "auxiliary" and path[1] in self.tasks:
            return path[1]
        return None

    def crumbs(self, path: PathT) -> list[str]:
        """Section, then the keys down to the setting (what search and Review show)."""
        node = self.nodes.get(tuple(path))
        keys = [str(k) for k in path]
        if node is None or keys[0] == node.section:
            return keys
        return [node.section] + keys

    def default_category(self, section: str) -> str | None:
        return next((cid for cid, _, keys in self.categories if section in keys), None)


def leaves(nodes: Iterable[Node], skip=None) -> list[Node]:
    out: list[Node] = []

    def walk(ns):
        for n in ns:
            if skip is not None and skip(n):
                continue
            if n.is_group:
                walk(n.children)
            else:
                out.append(n)

    walk(nodes)
    return out


def walk_nodes(nodes: Iterable[Node]):
    for n in nodes:
        yield n
        if n.children:
            yield from walk_nodes(n.children)


# ---------------------------------------------------------------- building
def _kind_of(value: Any, schema, path: PathT) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, list):
        return "block" if any(isinstance(v, (dict, list)) for v in value) else "list"
    if isinstance(value, dict):
        return "group" if value else "block"
    if value is None or value is MISSING:
        k = schema.kind(path) if schema is not None else "str"
        if k == "map":
            return "block"
        return k if k in ("bool", "int", "float", "str", "list", "enum") else "str"
    return "str"


def _leaf(schema, section: str, path: PathT, value: Any, documented_default: bool) -> Node:
    meta = schema.lookup(path) if schema is not None else None
    kind = _kind_of(value, schema, path)
    if kind == "group":
        kind = "block"
    node = Node(str(path[-1]), path, kind, section)
    if meta is not None and meta.enum and kind in ("str", "enum"):
        node.kind, node.options = "enum", tuple(v for v in meta.enum if v is not None)
    if documented_default and kind != "block":
        node.default = value
    elif meta is not None and meta.defaults and kind != "block":
        node.default = next(iter(meta.defaults.values()))
    if meta is not None and meta.description:
        node.desc = meta.description.split("\n", 1)[0]
    node.extra = meta is None
    if meta is not None and meta.status == "deprecated":
        node.status = "deprecated"
    key = str(path[-1])
    if node.kind == "str" and ((meta is not None and meta.secret) or secrets.is_secret_path(path)) \
            and not key.endswith(_NOT_SECRET_SUFFIXES):
        node.secret = True
    return node


def _build(schema, section: str, value: Any, path: PathT, documented: bool) -> Node | None:
    if path in CHAIN_OWNED:
        return None
    if isinstance(value, dict) and value:
        node = Node(str(path[-1]), path, "group", section)
        node.extra = not documented and (schema is None or schema.lookup(path) is None)
        for k, v in value.items():
            child = _build(schema, section, v, path + (k,), documented)
            if child is not None:
                child.parent = node
                node.children.append(child)
        return node
    return _leaf(schema, section, path, value, documented)


def _ensure(schema, section: Section, start: list[Node], path: PathT, depth: int, sample: Any,
            parent: Node | None) -> None:
    """Make sure a node exists for ``path`` (from ``path[depth]`` down), creating extra nodes."""
    nodes = start
    for i in range(depth, len(path)):
        sub = path[: i + 1]
        if sub in CHAIN_OWNED:
            return
        node = next((n for n in nodes if n.key == str(path[i]) and n.path == sub), None)
        if node is None:
            last = i == len(path) - 1
            if last:
                node = _leaf(schema, section.key, sub, sample, False)
            else:
                node = Node(str(path[i]), sub, "group", section.key)
                node.extra = schema is None or schema.lookup(sub) is None
            node.parent = parent
            nodes.append(node)
        if not node.is_group:
            return                      # a leaf (or block) covers everything below it
        parent, nodes = node, node.children


def shape(data: Any) -> dict[PathT, Any]:
    """Every key path in ``data`` -> its value (mappings included), in file order."""
    out: dict[PathT, Any] = {}

    def walk(value, path):
        out[path] = value
        if isinstance(value, dict):
            for k, v in value.items():
                if isinstance(k, (str, int)) and not isinstance(k, bool):
                    walk(v, path + (k,))

    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(k, str):
                walk(v, (k,))
    return out


def union_shape(shapes: Iterable[dict[PathT, Any]]) -> dict[PathT, Any]:
    union: dict[PathT, Any] = {}
    for s in shapes:
        for p, v in s.items():
            if p not in union:
                union[p] = v
    return union


def build_model(payload: dict, schema, present: dict[PathT, Any] | None = None) -> SettingsModel:
    """Sections, categories and nodes for the schema ``payload`` plus the agents' key paths.

    ``present``: union of the loaded agents' key paths (see :func:`shape`), or None.
    """
    blank = payload.get("defaults_tree") or {}
    present = present or {}
    present_tops = [p[0] for p in present if len(p) == 1]
    tree = build_tree(present_tops, list(blank))
    order = {k: i for i, k in enumerate(blank)}
    sections: dict[str, Section] = {}
    nodes_by_path: dict[PathT, Node] = {}

    for fam in tree.families:
        keys = [k for k in fam.keys if k in blank or (k,) in present]
        if not keys:
            continue
        sec = Section(fam.key, fam.label, fam.group, tuple(keys), [], CHAIN_OF_SECTION.get(fam.key),
                      any(k in blank for k in keys))
        for k in sorted(keys, key=lambda k: (k != fam.key and not isinstance(blank.get(k), dict),
                                             order.get(k, 1 << 20))):
            if k in blank:
                built = _build(schema, fam.key, blank[k], (k,), True)
            else:
                built = _build(schema, fam.key, present.get((k,)), (k,), False)
            if built is None:
                continue
            if k == fam.key and built.is_group:
                for child in built.children:      # the section's own keys sit directly on its page
                    child.parent = None
                    sec.nodes.append(child)
            else:
                sec.nodes.append(built)
        sections[fam.key] = sec

    # keys Hermes reads without exporting them (shown so they can be set)
    for raw in payload.get("fields") or []:
        if not (isinstance(raw, dict) and raw.get("row") and raw.get("path")):
            continue
        path = tuple(str(raw["path"]).split("."))
        sec_fam = tree.owner(path[0])
        if len(path) > 1 and sec_fam is not None and sec_fam.key in sections and path[0] == sec_fam.key:
            _ensure(schema, sections[sec_fam.key], sections[sec_fam.key].nodes, path, 1, MISSING, None)

    # keys the agents have that the export does not
    for path, sample in present.items():
        sec_fam = tree.owner(path[0])
        if sec_fam is None or sec_fam.key not in sections or path in CHAIN_OWNED:
            continue
        sec = sections[sec_fam.key]
        if path[0] == sec.key:
            if len(path) == 1:
                continue
            _ensure(schema, sec, sec.nodes, path, 1, sample, None)
        else:
            _ensure(schema, sec, sec.nodes, path, 0, sample, None)

    # The runtime reads agent.reasoning_effort (main model effort) although the export omits it.
    efforts = list((payload.get("reasoning_effort_values") or {}).get("values") or DEFAULT_EFFORTS)
    agent = sections.get("agent")
    if agent is not None:
        node = next((n for n in agent.nodes if n.path == MAIN_EFFORT), None)
        if node is None:
            node = Node("reasoning_effort", MAIN_EFFORT, "enum", "agent")
            agent.nodes.insert(0, node)
        node.kind, node.options, node.extra = "enum", tuple(efforts), False
        node.desc = node.desc or "Reasoning effort of the main model. Unset = medium."

    for sec in sections.values():
        for n in walk_nodes(sec.nodes):
            nodes_by_path[n.path] = n
        _suggest(sec.nodes)

    aux = sections.get("auxiliary")
    tasks = [n.key for n in aux.nodes if n.is_group] if aux else []
    blank_aux = blank.get("auxiliary") if isinstance(blank.get("auxiliary"), dict) else {}
    no_effort = {k for k, v in blank_aux.items() if isinstance(v, dict) and "reasoning_effort" not in v}

    sections[TEAMMATES_SECTION] = Section(TEAMMATES_SECTION, "hermes-teammates roster", "models", (), [], "roster",
                                          False)
    categories = []
    for gid, label in tree.groups:
        keys = [f.key for f in tree.in_group(gid) if f.key in sections]
        if gid != "models":
            keys.sort(key=lambda k: order.get(k, 1 << 20))
        else:
            keys.append(TEAMMATES_SECTION)
        if keys:
            categories.append((gid, label, keys))
    return SettingsModel(sections, categories, nodes_by_path, tasks, no_effort, efforts)


def did_you_mean(key: str, known: Iterable[str], parents: Iterable[str] = ()) -> str | None:
    """The known key an unknown ``key`` most likely means, or None.

    ``discord_allow_bots`` under ``discord`` -> ``allow_bots`` (the section name typed twice), else a
    close spelling (``alow_bots`` -> ``allow_bots``).
    """
    known = [k for k in known if k != key]
    for parent in parents:
        head = f"{parent}_"
        if key.startswith(head) and key[len(head):] in known:
            return key[len(head):]
    close = difflib.get_close_matches(key, known, n=1, cutoff=0.85)
    return close[0] if close else None


def key_tags(node: Node) -> list[tuple[str, str]]:
    """(tag, tooltip) pairs shown next to a key: misspelled, unknown, deprecated."""
    where = " › ".join(str(k) for k in node.path)
    tags = []
    if node.suggest:
        meant = " › ".join(str(k) for k in node.suggest)
        tags.append(("unknown to Hermes",                  # the note under the table asks "did you mean"
                     f"Hermes does not read {where}, so its value has no effect. It most likely means {meant}. "
                     "“Stage move” under the table moves each agent's value there (shown in Review)."))
    elif node.extra:
        tags.append(("not in Hermes' settings list",
                     "Your config files have this key, but the list of Hermes settings this app knows does not. "
                     "Hermes may ignore it (an old or misspelled key), or it may be a setting this app does not "
                     "know yet."))
    if node.status == "deprecated":
        no_op = "no-op" in node.desc.lower()
        tags.append(("deprecated · no effect" if no_op else "deprecated",
                     node.desc or "Hermes marks this setting as deprecated."))
    return tags


def _suggest(nodes: list[Node]) -> None:
    """Give each unknown leaf among ``nodes`` (and below) its likely meant sibling."""
    known = [n.key for n in nodes if not n.extra]
    for n in nodes:
        if n.is_group:
            _suggest(n.children)
        elif n.extra:
            hit = did_you_mean(n.key, known, [str(k) for k in n.path[:-1]])
            n.suggest = n.path[:-1] + (hit,) if hit else None


# ------------------------------------------------------------------ usage
def used_paths(model: SettingsModel, present: dict[PathT, Any]) -> set[PathT]:
    """Node paths (and section keys, as 1-tuples) that some loaded agent's file contains."""
    used: set[PathT] = set()
    for path in present:
        for i in range(1, len(path) + 1):
            used.add(path[:i])
    for sec in model.sections.values():
        if any((k,) in present for k in sec.keys):
            used.add(("@section", sec.key))
    return used


def section_used(sec: Section, used: set[PathT]) -> bool:
    return ("@section", sec.key) in used
