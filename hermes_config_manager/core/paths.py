"""Addressing values inside parsed configuration data.

A path is a tuple of mapping keys (str) and sequence indexes (int), e.g.
``("auxiliary", "vision", "fallback_chain", 0, "model")``.  Hermes keys can
contain dots (``custom_providers[0].models["glm-5.1"]``), so the string form
quotes such keys in brackets.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator, Union

PathKey = Union[str, int]
PathT = tuple[PathKey, ...]


class _Missing:
    """Sentinel for "no value at this path" (distinct from an explicit null)."""

    _instance: "_Missing | None" = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False

    def __reduce__(self):
        return (_Missing, ())


MISSING: Any = _Missing()

_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
_TOKEN = re.compile(r'\.?([A-Za-z_][A-Za-z0-9_\-]*)|\[(\d+)\]|\[("(?:[^"\\]|\\.)*")\]')


def format_path(path: PathT) -> str:
    """Render a path for display and for stable string keys."""
    parts: list[str] = []
    for key in path:
        if isinstance(key, int):
            parts.append(f"[{key}]")
        elif _PLAIN_KEY.match(key):
            parts.append(("." if parts else "") + key)
        else:
            parts.append("[" + json.dumps(key, ensure_ascii=False) + "]")
    return "".join(parts)


def is_ancestor(ancestor: PathT, path: PathT) -> bool:
    """True if ``ancestor`` is a proper prefix of ``path``."""
    return len(ancestor) < len(path) and path[: len(ancestor)] == ancestor


def is_same_or_ancestor(ancestor: PathT, path: PathT) -> bool:
    return len(ancestor) <= len(path) and path[: len(ancestor)] == ancestor


def get_in(data: Any, path: PathT, default: Any = MISSING) -> Any:
    """Return the value at ``path`` or ``default`` when any step is absent."""
    node = data
    for key in path:
        if isinstance(key, int):
            if not isinstance(node, list) or not -len(node) <= key < len(node):
                return default
            node = node[key]
        else:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
    return node


def set_in(data: Any, path: PathT, value: Any) -> Any:
    """Set ``value`` at ``path`` in place, creating intermediate mappings.

    Returns the (possibly replaced) root, so ``set_in(None, ("a",), 1)`` works.
    A list index may address an existing item or ``len(list)`` (append).
    Intermediate non-container values are replaced by mappings, mirroring what
    a YAML editor would produce when a scalar becomes a section.
    """
    if not path:
        return value
    if not isinstance(data, (dict, list)):
        data = {}
    node = data
    for i, key in enumerate(path[:-1]):
        nxt_key = path[i + 1]
        if isinstance(key, int):
            if not isinstance(node, list) or not 0 <= key <= len(node):
                raise KeyError(f"Index {key} out of range at {format_path(path[:i + 1])}")
            if key == len(node):
                node.append({} if isinstance(nxt_key, str) else [])
            child = node[key]
            if not isinstance(child, (dict, list)):
                child = {} if isinstance(nxt_key, str) else []
                node[key] = child
        else:
            child = node.get(key, MISSING) if isinstance(node, dict) else MISSING
            if not isinstance(child, (dict, list)):
                child = {} if isinstance(nxt_key, str) else []
                node[key] = child
        node = child
    last = path[-1]
    if isinstance(last, int):
        if not isinstance(node, list) or not 0 <= last <= len(node):
            raise KeyError(f"Index {last} out of range at {format_path(path)}")
        if last == len(node):
            node.append(value)
        else:
            node[last] = value
    else:
        node[last] = value
    return data


def delete_in(data: Any, path: PathT) -> bool:
    """Delete the value at ``path``; returns False when it was absent."""
    if not path:
        return False
    parent = get_in(data, path[:-1])
    last = path[-1]
    if isinstance(last, int):
        if isinstance(parent, list) and -len(parent) <= last < len(parent):
            del parent[last]
            return True
        return False
    if isinstance(parent, dict) and last in parent:
        del parent[last]
        return True
    return False


def deep_copy(value: Any) -> Any:
    """Fast deep copy for plain YAML data (dict/list/scalars only)."""
    if isinstance(value, dict):
        return {k: deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [deep_copy(v) for v in value]
    return value


def iter_leaves(data: Any, prefix: PathT = ()) -> Iterator[tuple[PathT, Any]]:
    """Yield (path, value) for scalar leaves, empty containers and scalar lists.

    Lists whose items are all scalars are treated as a single leaf value, which
    is how the generic editor presents them (an ordered list editor).
    """
    if isinstance(data, dict):
        if not data:
            yield prefix, data
            return
        for key, value in data.items():
            yield from iter_leaves(value, prefix + (key,))
    elif isinstance(data, list):
        if not data or all(not isinstance(v, (dict, list)) for v in data):
            yield prefix, data
            return
        for index, value in enumerate(data):
            yield from iter_leaves(value, prefix + (index,))
    else:
        yield prefix, data

