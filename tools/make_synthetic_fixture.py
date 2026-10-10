"""Generate the benchmark/test fixture: 100 synthetic Hermes agents (1 main + 99 profiles).

Built only from public data: the Hermes defaults in ``data/hermes_schema.json`` plus generic
names, public model names and placeholder secrets.  No real configuration is read.

Each file:
  * starts with "# GENERATED BENCHMARK FIXTURE - not a real agent";
  * is a full config (every exported setting, like an older Hermes writes it), so parse and
    patch costs are realistic;
  * varies the primary model, fallbacks, delegation, auxiliary models, reasoning effort and a
    handful of ordinary settings, with comments, inline comments and mixed indentation;
  * holds placeholder credentials only ("REDACTED-BENCH").
Deterministic (seed 20260929).  Also writes generated-100-2hosts/{hostA,hostB} (50/50 split).

Usage:  python tools/make_synthetic_fixture.py [fixtures_dir]   (default bench/fixtures; overwrites generated-100*)
"""

from __future__ import annotations

import json
import random
import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_config_manager.core import secrets  # noqa: E402
from hermes_config_manager.core.paths import deep_copy, get_in  # noqa: E402
from hermes_config_manager.core.yamlpatch import fast_load, strict_equal  # noqa: E402

SCHEMA = ROOT / "data" / "hermes_schema.json"
FIXTURES = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "bench" / "fixtures"
OUT = FIXTURES / "generated-100"
OUT2 = FIXTURES / "generated-100-2hosts"
HEADER = "# GENERATED BENCHMARK FIXTURE - not a real agent\n"
PLACEHOLDER = "REDACTED-BENCH"
EFFORTS = ["low", "medium", "high", "xhigh"]
ROLES = ["researcher", "coder", "reviewer", "planner", "writer", "analyst", "tester", "designer",
         "translator", "support", "ops", "scheduler", "editor", "monitor", "architect", "debugger",
         "summarizer", "librarian", "assistant", "explorer"]
MODELS = [
    ("openrouter", "anthropic/claude-sonnet-4.5"), ("openrouter", "openai/gpt-5"),
    ("openrouter", "google/gemini-2.5-pro"), ("openrouter", "x-ai/grok-4"),
    ("openrouter", "z-ai/glm-4.6"), ("anthropic", "claude-sonnet-4-5"), ("anthropic", "claude-opus-4-1"),
    ("openai", "gpt-5"), ("openai", "gpt-5-mini"), ("nous", "Hermes-4-405B"), ("nous", "Hermes-4-70B"),
    ("gemini", "gemini-2.5-flash"), ("zai", "glm-4.6"), ("custom", "local-llama-3.3-70b"),
]
LOCAL_URL = "http://localhost:8080/v1"


def profile_names(count: int = 99) -> list[str]:
    return [f"bench-{ROLES[(i - 1) % len(ROLES)]}-{i:03d}" for i in range(1, count + 1)]


def pick(rng: random.Random) -> tuple[str, str]:
    return rng.choice(MODELS)


def chain_entry(rng: random.Random, effort: bool) -> dict:
    prov, model = pick(rng)
    entry = {"provider": prov, "model": model}
    if prov == "custom":
        entry["base_url"] = LOCAL_URL
    if effort:
        entry["reasoning_effort"] = rng.choice(EFFORTS)
    return entry


def scalar_leaves(tree, path=()):
    if isinstance(tree, dict):
        for k, v in tree.items():
            yield from scalar_leaves(v, path + (k,))
    elif isinstance(tree, (bool, int, float)) and path:
        yield path, tree


def set_in(data, path, value):
    for k in path[:-1]:
        data = data.setdefault(k, {})
    data[path[-1]] = value


def vary(data: dict, rng: random.Random, aux_tasks: list[str], enums: dict) -> None:
    prov, model = pick(rng)
    block = {"default": model, "provider": prov}
    if prov == "custom" or rng.random() < 0.15:
        block["base_url"] = LOCAL_URL if prov == "custom" else "https://api.example.com/v1"
        block["api_mode"] = "chat_completions"
    if rng.random() < 0.4:
        block["reasoning_effort"] = rng.choice(EFFORTS)       # legacy key some configs still carry
    data["model"] = block
    data.setdefault("agent", {})["reasoning_effort"] = rng.choice(EFFORTS)
    data["fallback_providers"] = [chain_entry(rng, False) for _ in range(rng.randint(0, 3))]

    d = data.setdefault("delegation", {})
    d["provider"], d["model"] = pick(rng)
    d["reasoning_effort"] = rng.choice(EFFORTS)
    d["fallback_providers"] = [chain_entry(rng, True) for _ in range(rng.randint(0, 3))]

    aux = data.setdefault("auxiliary", {})
    for task in rng.sample(aux_tasks, k=min(len(aux_tasks), rng.randint(3, 8))):
        t = aux.setdefault(task, {})
        if not isinstance(t, dict):
            continue
        t["provider"], t["model"] = pick(rng)
        if "reasoning_effort" in t or rng.random() < 0.5:
            t["reasoning_effort"] = rng.choice(EFFORTS)
        if rng.random() < 0.3:
            t["fallback_chain"] = [chain_entry(rng, False) for _ in range(rng.randint(1, 2))]

    # ordinary settings: flip switches, scale numbers, pick other allowed values
    leaves = [(p, v) for p, v in scalar_leaves(data) if p[0] not in ("model", "delegation", "auxiliary", "agent")
              or p[:2] == ("agent", "max_turns")]
    for path, value in rng.sample(leaves, k=min(len(leaves), rng.randint(8, 20))):
        if isinstance(value, bool):
            set_in(data, path, not value)
        elif isinstance(value, int):
            set_in(data, path, max(1, value * rng.choice([2, 3])) if value else rng.randint(1, 50))
        elif isinstance(value, float):
            set_in(data, path, round(min(0.95, value + 0.1), 2))
    for dotted, values in enums.items():
        path = tuple(dotted.split("."))
        if get_in(data, path, None) is not None and rng.random() < 0.1:
            set_in(data, path, rng.choice(values))

    # platform lists with fake ids, custom providers, MCP servers, placeholder credentials
    if isinstance(data.get("discord"), dict) and rng.random() < 0.6:
        data["discord"]["allowed_channels"] = [str(rng.randrange(10**17, 10**18)) for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.25:
        data["custom_providers"] = [{"name": "local", "base_url": LOCAL_URL, "api_key": PLACEHOLDER,
                                     "models": ["local-llama-3.3-70b"]}]
    if rng.random() < 0.3:
        data["mcp_servers"] = {"filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem",
                                                                         "/tmp/work"]}}
    if rng.random() < 0.2:
        d["api_key"] = PLACEHOLDER


def render(data: dict, rng: random.Random) -> str:
    indent = 4 if rng.random() < 0.3 else 2
    flow = False                              # Hermes writes block style
    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=flow, allow_unicode=True,
                          width=10**6, indent=indent)
    lines = text.splitlines(keepends=True)
    out = [HEADER, "# Hermes agent configuration\n"]
    scalar = re.compile(r"^(\s+[A-Za-z_][\w-]*: (?:true|false|-?\d+(?:\.\d+)?))$")
    for line in lines:
        if (line[0].isalpha() or line[0] == "_") and ":" in line and rng.random() < 0.2:   # top-level keys only
            out.append(f"# --- {line.split(':', 1)[0]} ---\n")
        m = scalar.match(line.rstrip("\n"))
        if m and rng.random() < 0.02:
            line = f"{m.group(1)}  # tuned\n"
        out.append(line)
    return "".join(out)


def generate() -> dict:
    rng = random.Random(20260929)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    base = schema["defaults_tree"]
    aux_tasks = [t["name"] for t in schema.get("auxiliary_tasks") or []
                 if isinstance(get_in(base, ("auxiliary", t["name"]), None), dict)]
    enums = {f["path"]: f["enum"] for f in schema["fields"]
             if f.get("enum") and f.get("in_export") and "*" not in f["path"] and not f["path"].startswith(
                 ("model.", "agent.reasoning", "delegation.", "auxiliary."))}
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "profiles").mkdir(parents=True)
    names = ["main"] + profile_names()
    for i, name in enumerate(names):
        data = deep_copy(base)
        vary(data, rng, aux_tasks, enums)
        data["_config_version"] = 22
        text = render(data, rng)
        assert strict_equal(fast_load(text), data), f"fixture {name} does not round-trip"
        path = OUT / "config.yaml" if i == 0 else OUT / "profiles" / name / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    (OUT / "GENERATED_BENCHMARK_FIXTURE.txt").write_text(
        "GENERATED benchmark fixture - 100 synthetic Hermes agent configs built from the public Hermes defaults.\n"
        "Generic names, public model names, placeholder credentials. Not real agents. "
        "Regenerate: python tools/make_synthetic_fixture.py\n", encoding="utf-8")
    if OUT2.exists():
        shutil.rmtree(OUT2)
    profiles = sorted((OUT / "profiles").iterdir())
    for idx, host in enumerate(("hostA", "hostB")):
        root = OUT2 / host
        (root / "profiles").mkdir(parents=True)
        shutil.copy(OUT / "config.yaml", root / "config.yaml")
        for p in profiles[idx::2]:
            shutil.copytree(p, root / "profiles" / p.name)
        shutil.copy(OUT / "GENERATED_BENCHMARK_FIXTURE.txt", root / "GENERATED_BENCHMARK_FIXTURE.txt")
    return {"files": len(names)}


if __name__ == "__main__":
    s = generate()
    leaked = 0
    for f in OUT.rglob("config.yaml"):
        data = fast_load(f.read_text(encoding="utf-8"))
        leaked += len([p for p in secrets.contains_secrets(data) if get_in(data, p) != PLACEHOLDER])
    print(f"generated {s['files']} configs in {OUT} (secret-like values other than the placeholder: {leaked})")
