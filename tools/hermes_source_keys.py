"""Find the ``discord:`` keys Hermes reads, straight from its source code.

The dashboard export of an empty profile only lists settings Hermes keeps a default for.  The
Discord adapter reads more keys than that directly from the ``discord:`` block (``allow_bots``,
``allow_mentions.*``, ...).  This module parses Hermes' Python files with :mod:`ast` (nothing is
imported or run) and returns those keys, so ``build_schema.py`` can list them after every Hermes
update.  Types, value sets and descriptions come from ``discord_keys.json``.

Three sources, three origins:

* ``discord-adapter``: ``plugins/platforms/discord/adapter.py`` (the YAML -> env bridge
  ``_apply_yaml_config``, the ``_YAML_*`` key tables, ``_gate_raw(...)`` and ``extra.get(...)``);
* ``platform-adapter``: ``gateway/platforms/base.py``, the base class every adapter inherits;
* ``platform-shared``: ``_SHARED_KEYS`` in ``gateway/config_loader.py``, keys Hermes copies from
  every platform block (only some platforms act on each one).
"""

from __future__ import annotations

import ast
from pathlib import Path

DISCORD_ADAPTER = Path("plugins/platforms/discord/adapter.py")
PLATFORM_BASE = Path("gateway/platforms/base.py")
CONFIG_LOADER = Path("gateway/config_loader.py")

_NOT_KEYS = {"extra"}                       # discord.extra is the generic sub-map, not a setting


def _str(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _literal(node):
    try:
        return True, ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return False, None


def _is_extra(node) -> bool:
    """``extra``, ``self.config.extra``, ``_discord_extra`` and the like."""
    name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""
    return name.endswith("extra")


def _is_name(node, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _add(found: dict, key: str | None, env: str | None = None, default=None, has_default: bool = False) -> None:
    if not key or key.startswith("_") or key.split(".")[0] in _NOT_KEYS:
        return
    info = found.setdefault(key, {})
    if env and not info.get("env"):
        info["env"] = env
    if has_default and "default" not in info:
        info["default"] = default


def _str_tuples(node) -> list[tuple[str, ...]]:
    """``(("a", "ENV_A"), ("b", None, "ENV_B"))`` -> the inner tuples (None kept as None)."""
    if not isinstance(node, (ast.Tuple, ast.List)):
        return []
    out = []
    for elt in node.elts:
        if isinstance(elt, ast.Tuple) and elt.elts and _str(elt.elts[0]):
            out.append(tuple(_str(e) for e in elt.elts))
    return out


def discord_adapter_keys(source: str) -> dict[str, dict]:
    """Keys the Discord adapter reads: ``{key: {"env": ..., "default": ...}}`` (dotted for sub-keys)."""
    tree = ast.parse(source)
    found: dict[str, dict] = {}
    for node in tree.body:                                   # module tables: _YAML_BOOL_ENV_KEYS, ...
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id.startswith("_YAML_")
                                                for t in node.targets):
            for row in _str_tuples(node.value):
                _add(found, row[0], row[-1] if len(row) > 1 else None)
                if len(row) > 2 and row[1]:                  # (key, legacy alias, env)
                    _add(found, row[1])
                    found[row[1]]["legacy_of"] = row[0]
    bridge = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_apply_yaml_config"), None)
    if bridge is not None:
        sub_maps: dict[str, str] = {}                        # local name -> key of a sub-map
        for node in ast.walk(bridge):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call = node.value
                if (isinstance(call.func, ast.Attribute) and call.func.attr == "get"
                        and _is_name(call.func.value, "discord_cfg") and call.args and _str(call.args[0])):
                    for t in node.targets:
                        if isinstance(t, ast.Name):
                            sub_maps[t.id] = _str(call.args[0])
        for node in ast.walk(bridge):
            if isinstance(node, ast.Call) and _is_name(node.func, "_gate") and len(node.args) >= 2:
                _add(found, _str(node.args[0]), _str(node.args[1]))
            elif isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.In) \
                    and _is_name(node.comparators[0], "discord_cfg"):
                _add(found, _str(node.left))
            elif isinstance(node, ast.Subscript) and _is_name(node.value, "discord_cfg"):
                _add(found, _str(node.slice))
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "get" and _is_name(node.func.value, "discord_cfg") and node.args:
                _add(found, _str(node.args[0]))
            elif isinstance(node, ast.For):
                for row in _str_tuples(node.iter):           # for key, env_key in (("a", "ENV"), ...)
                    _add(found, row[0], row[-1] if len(row) > 1 else None)
                if isinstance(node.iter, ast.Tuple) and isinstance(node.target, ast.Name):
                    names = [_str(e) for e in node.iter.elts]
                    if names and all(names):                 # for yaml_key in ("everyone", ...): x in <sub-map>
                        for inner in ast.walk(node):
                            if isinstance(inner, ast.Compare) and _is_name(inner.left, node.target.id) \
                                    and isinstance(inner.comparators[0], ast.Name) \
                                    and inner.comparators[0].id in sub_maps:
                                parent = sub_maps[inner.comparators[0].id]
                                for name in names:
                                    _add(found, f"{parent}.{name}")
                                break
    _add_extra_reads(tree, found)
    return found


def _add_extra_reads(tree, found: dict) -> None:
    """``extra.get("k", default)``, ``extra["k"]`` and ``_gate_raw("k", "ENV")`` anywhere in a file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                and _is_extra(node.func.value) and node.args and _str(node.args[0]):
            if len(node.args) > 1:
                ok, value = _literal(node.args[1])
                _add(found, _str(node.args[0]), default=value, has_default=ok)
            else:
                _add(found, _str(node.args[0]))
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _is_extra(node.value):
            _add(found, _str(node.slice))                    # reads only: extra["is_bot"] = True is not a setting
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "_gate_raw" and len(node.args) >= 2:
            _add(found, _str(node.args[0]), _str(node.args[1]))


def base_adapter_keys(source: str) -> dict[str, dict]:
    """Keys the adapter base class reads from ``extra`` (every platform, Discord included)."""
    found: dict[str, dict] = {}
    _add_extra_reads(ast.parse(source), found)
    return found


def shared_keys(source: str, platform: str = "DISCORD") -> dict[str, dict]:
    """``_SHARED_KEYS`` rows that apply to ``platform`` (rows limited to other platforms are left out)."""
    tree = ast.parse(source)
    found: dict[str, dict] = {}
    table = None
    for n in tree.body:                                      # _SHARED_KEYS = (...) or _SHARED_KEYS: tuple = (...)
        targets = n.targets if isinstance(n, ast.Assign) else [n.target] if isinstance(n, ast.AnnAssign) else []
        if any(_is_name(t, "_SHARED_KEYS") for t in targets):
            table = n.value
    if not isinstance(table, (ast.Tuple, ast.List)):
        return found
    for elt in table.elts:
        if isinstance(elt, ast.Starred) and isinstance(elt.value, ast.Call):      # *_plain("a", "b")
            for arg in elt.value.args:
                _add(found, _str(arg))
        elif isinstance(elt, ast.Tuple) and elt.elts and _str(elt.elts[0]):
            only = elt.elts[1] if len(elt.elts) > 1 else None
            if only is None or (isinstance(only, ast.Constant) and only.value is None) \
                    or (isinstance(only, ast.Name) and platform in only.id.upper()):
                _add(found, _str(elt.elts[0]))
    return found


def read_discord_keys(hermes_root: Path) -> dict[str, dict]:
    """All three sources of a Hermes checkout; each key gets its ``origin`` (first source wins)."""
    out: dict[str, dict] = {}
    for rel, parse, origin in ((DISCORD_ADAPTER, discord_adapter_keys, "discord-adapter"),
                               (PLATFORM_BASE, base_adapter_keys, "platform-adapter"),
                               (CONFIG_LOADER, shared_keys, "platform-shared")):
        path = hermes_root / rel
        if not path.is_file():
            raise FileNotFoundError(f"not a Hermes source folder (missing {rel}): {hermes_root}")
        for key, info in parse(path.read_text(encoding="utf-8")).items():
            if key not in out:
                out[key] = dict(info, origin=origin)
    return out
