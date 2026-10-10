"""Build the public settings schema shipped with the app: ``data/hermes_schema.json``.

A maintainer tool.  Inputs (not shipped):

* a dashboard export of an EMPTY Hermes profile (``--blank``): the authority for which settings
  exist, their order and their default values;
* the runtime metadata (``--runtime``): descriptions, closed value sets, types, secret flags;
* optionally a Hermes source folder (``--hermes-src``, e.g. ``~/.hermes/hermes-agent``): the
  ``discord:`` keys Hermes reads but does not export (``hermes_source_keys.py``), described by
  ``discord_keys.json``.  Without it the keys listed in ``discord_keys.json`` are used unchecked.

The output keeps only what describes Hermes itself.  Everything that came from someone's own
config files is dropped: settings seen only in configs ("observed-custom"), observed values,
source-file locations and host labels.

Usage:  python tools/build_schema.py --blank PATH --runtime PATH [--hermes-src PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from . import hermes_source_keys as source_keys
except ImportError:                                   # run as a script: python tools/build_schema.py
    import hermes_source_keys as source_keys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "hermes_schema.json"
DEFAULT_KEYS = Path(__file__).resolve().parent / "discord_keys.json"
DEFAULT_LABEL = "Hermes defaults"


def leaves(tree, path=()):
    """(dotted path, value) of every leaf of the BLANK tree, in file order."""
    if isinstance(tree, dict) and tree:
        for key, child in tree.items():
            yield from leaves(child, path + (str(key),))
    elif path:
        yield ".".join(path), tree


def kind_of(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, dict):
        return "map"
    if isinstance(value, list):
        return "list"
    if value is None:
        return "null"
    return "str"


def description_text(raw) -> str | None:
    desc = raw.get("description")
    if isinstance(desc, dict):
        desc = desc.get("text")
    return desc or None


def enum_values(raw):
    enum = raw.get("enum")
    if isinstance(enum, dict):
        enum = enum.get("values")
    return list(enum) if isinstance(enum, list) and enum else None


def runtime_default(raw):
    """A runtime default, whatever host or version label it was recorded under."""
    for value in (raw.get("defaults") or {}).values():
        return True, value
    return False, None


def is_deprecated(description: str | None) -> bool:
    """Hermes marks a key that no longer does anything by starting its comment with DEPRECATED.  Only
    the start counts: some comments merely mention another, deprecated key."""
    return bool(description) and description.lstrip().upper().startswith("DEPRECATED")


def source_fields(known: set[str], keys_file: dict, found: dict | None) -> tuple[list[dict], list[str]]:
    """Fields for the keys Hermes reads but does not export, and a report of what does not match.

    ``found``: the keys read from a Hermes source folder (``hermes_source_keys.read_discord_keys``),
    or None to take ``keys_file`` as it is.  Keys already in ``known`` (the export, the runtime
    metadata) keep that description.
    """
    section = keys_file.get("section") or "discord"
    described = keys_file.get("keys") or {}
    report = []
    checked = found is not None
    if not checked:
        found = {k: {} for k in described}
        report.append(f"no Hermes source given: {len(found)} keys from discord_keys.json, not checked")
    fields = []
    for key, info in found.items():
        path = f"{section}.{key}"
        if path in known:
            continue
        spec = described.get(key)
        origin = info.get("origin")
        if spec is None:
            report.append(f"no description in discord_keys.json: {key} ({origin})")
            spec = {"type": "any", "row": origin != "platform-shared" and not info.get("legacy_of"),
                    "description": _fallback_description(info)}
        out = {"path": path, "type": spec.get("type") or "any"}
        if spec.get("description"):
            out["description"] = spec["description"]
        if spec.get("enum"):
            out["enum"] = list(spec["enum"])
        out["status"] = "deprecated" if is_deprecated(spec.get("description")) else "supported"
        if "default" in spec:
            out["defaults"] = {DEFAULT_LABEL: spec["default"]}
        elif "default" in info:
            out["defaults"] = {DEFAULT_LABEL: info["default"]}
        out["in_export"] = False
        if origin:
            out["origin"] = origin
        out["row"] = bool(spec.get("row"))
        fields.append(out)
    if checked:
        for key in described:
            if key not in found and f"{section}.{key}" not in known:
                report.append(f"in discord_keys.json but no longer in the Hermes source (left out): {key}")
    return fields, report


def _fallback_description(info: dict) -> str:
    if info.get("legacy_of"):
        return f"Older name of {info['legacy_of']}; still read."
    if info.get("origin") == "platform-shared":
        return "Shared chat-platform key: Hermes copies it from every platform block; only some platforms use it."
    return "Read by Hermes' Discord adapter" + (f" (env {info['env']} wins)." if info.get("env") else ".")


def build(blank: dict, runtime: dict, keys_file: dict | None = None, found: dict | None = None,
          report: list | None = None) -> dict:
    documented = {f["path"]: f for f in runtime.get("fields") or []
                  if isinstance(f, dict) and f.get("path") and f.get("status") != "observed-custom"}
    blank_leaves = dict(leaves(blank))
    fields = []

    def entry(path: str, raw: dict | None, in_blank: bool) -> dict:
        out = {"path": path}
        if raw is not None and isinstance(raw.get("type"), str):
            out["type"] = raw["type"]
        elif in_blank:
            out["type"] = kind_of(blank_leaves[path])
        if raw is not None:
            if description_text(raw):
                out["description"] = description_text(raw)
            if enum_values(raw):
                out["enum"] = enum_values(raw)
            if raw.get("runtime_note"):
                out["runtime_note"] = raw["runtime_note"]
            if raw.get("secret"):
                out["secret"] = True
            out["status"] = raw.get("status") or "supported"
            if is_deprecated(description_text(raw)):
                out["status"] = "deprecated"
        else:
            out["status"] = "supported"
        if in_blank:
            out["defaults"] = {DEFAULT_LABEL: blank_leaves[path]}
        elif raw is not None:
            has, value = runtime_default(raw)
            if has:
                out["defaults"] = {DEFAULT_LABEL: value}
        out["in_export"] = in_blank
        return out

    for path in blank_leaves:                        # 1. every exported setting, in Hermes' order
        fields.append(entry(path, documented.get(path), True))
    for path, raw in documented.items():             # 2. documented settings the export leaves out
        if path not in blank_leaves:
            fields.append(entry(path, raw, False))
    if keys_file is not None:                        # 3. discord: keys Hermes' source reads
        extra, notes = source_fields({f["path"] for f in fields}, keys_file, found)
        fields.extend(extra)
        if report is not None:
            report.extend(notes)

    effort = runtime.get("reasoning_effort_values") or {}
    return {
        "provenance": {
            "generator": "tools/build_schema.py",
            "notes": "Setting list, order and defaults from a dashboard export of an empty Hermes "
                     "profile; descriptions and value sets from the Hermes runtime source and docs.",
        },
        "auxiliary_tasks": [{"name": t["name"]} for t in runtime.get("auxiliary_tasks") or []
                            if isinstance(t, dict) and t.get("name")],
        "reasoning_effort_values": {"values": list(effort.get("values") or []), "closed": True},
        "semantics": runtime.get("semantics") or {},
        "defaults_tree": blank,
        "fields": fields,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--blank", type=Path, required=True, help="dashboard export of an empty Hermes profile")
    ap.add_argument("--runtime", type=Path, required=True, help="runtime metadata (descriptions, value sets)")
    ap.add_argument("--hermes-src", type=Path, help="a Hermes source folder, e.g. ~/.hermes/hermes-agent")
    ap.add_argument("--keys", type=Path, default=DEFAULT_KEYS, help="descriptions of the discord: keys")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    blank = json.loads(args.blank.read_text(encoding="utf-8"))
    runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
    keys_file = json.loads(args.keys.read_text(encoding="utf-8"))
    found = source_keys.read_discord_keys(args.hermes_src.expanduser()) if args.hermes_src else None
    report: list[str] = []
    payload = build(blank, runtime, keys_file, found, report)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    exported = sum(1 for f in payload["fields"] if f["in_export"])
    print(f"{len(payload['fields'])} settings ({exported} in the export, "
          f"{len(payload['fields']) - exported} documented only) -> {args.out}")
    for line in report:
        print("  " + line)


if __name__ == "__main__":
    main()
