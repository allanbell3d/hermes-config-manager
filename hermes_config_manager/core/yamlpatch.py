"""Two-speed YAML: fast runtime-faithful loading and surgical, verified patching.

* :func:`fast_load` parses with libyaml's SafeLoader - the same YAML 1.1
  semantics the Hermes runtime uses (``yaml.safe_load``), ~18x faster than a
  ruamel round-trip load.
* :func:`render_patched` turns *original text + desired data* into new text by
  editing only the character ranges of values that changed.  Everything else
  (comments, blank lines, quoting, key order, long lines, CRLF/LF, BOM, final
  newline) stays byte-for-byte identical.  A whole-file re-dump is never done
  implicitly: a ruamel/PyYAML re-dump of the real configs changes hundreds to
  thousands of lines.
* Every result is verified: parsing the new text must give exactly the target
  data (type-strict).  Unverifiable output raises :class:`PatchError`.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from .paths import MISSING, PathT, deep_copy, get_in, set_in

_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

__all__ = [
    "MISSING", "PatchError", "PatchResult", "YamlParseError", "diff_paths", "fast_load",
    "rename_key", "renamed", "render_patched", "strict_equal",
]


class YamlParseError(ValueError):
    def __init__(self, problem: str, line: int | None = None, column: int | None = None) -> None:
        self.problem = problem
        self.line = line
        self.column = column
        where = f"line {line}" + (f", column {column}" if column else "") if line else "unknown position"
        super().__init__(f"{problem} ({where})")


class PatchError(RuntimeError):
    pass


@dataclass
class PatchResult:
    text: str
    strategy: str                      # unchanged | surgical | full-rewrite
    changed_paths: list[PathT] = field(default_factory=list)
    rewritten_blocks: list[tuple[PathT, str]] = field(default_factory=list)


# --------------------------------------------------------------------- loading
def fast_load(text: str) -> Any:
    """Parse like the Hermes runtime (PyYAML safe_load, libyaml when available)."""
    try:
        return yaml.load(text, Loader=_Loader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        if getattr(exc, "context", None):
            problem = f"{exc.context}; {problem}"
        raise YamlParseError(problem, mark.line + 1 if mark else None, mark.column + 1 if mark else None) from None


# ------------------------------------------------------------------ comparison
def strict_equal(a: Any, b: Any) -> bool:
    """Deep equality that never treats True == 1 == 1.0 or 'a' == MISSING."""
    if a is MISSING or b is MISSING:
        return a is b
    if isinstance(a, dict):
        if not isinstance(b, dict) or len(a) != len(b):
            return False
        for key, value in a.items():
            if key not in b:
                return False
            if isinstance(key, (bool, int, float)):
                # 1, 1.0 and True are the same dict key; require the same key type too.
                if not any(k == key and type(k) is type(key) for k in b):
                    return False
            if not strict_equal(value, b[key]):
                return False
        return True
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    if type(a) is not type(b):
        return False
    if isinstance(a, float) and a != a:
        return b != b
    return a == b


def diff_paths(a: Any, b: Any, prefix: PathT = ()) -> list[PathT]:
    """Minimal paths where ``a`` and ``b`` differ (strictly)."""
    if strict_equal(a, b):
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[PathT] = []
        for key, value in a.items():
            if key not in b:
                out.append(prefix + (key,))
            else:
                out += diff_paths(value, b[key], prefix + (key,))
        out += [prefix + (key,) for key in b if key not in a]
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += diff_paths(x, y, prefix + (i,))
        return out
    return [prefix]


# ------------------------------------------------------------------ rendering
class _IndentedSeqDumper(yaml.SafeDumper):
    """Emit ``key:\\n  - item`` instead of PyYAML's indentless ``key:\\n- item``."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def _dump(value: Any, *, flow: bool = False, indent: int = 2, seq_offset: int = 0) -> str:
    dumper = _IndentedSeqDumper if seq_offset > 0 else yaml.SafeDumper
    text = yaml.dump(value, Dumper=dumper, default_flow_style=flow, sort_keys=False, allow_unicode=True,
                     width=1_000_000_000, indent=max(2, indent))
    if text.endswith("\n...\n"):
        text = text[: -len("\n...\n")] + "\n"
    return text.rstrip("\n")


def _reads_back(text: str, value: Any) -> bool:
    try:
        loaded = yaml.load("k: " + text, Loader=_Loader)
    except yaml.YAMLError:
        return False
    return isinstance(loaded, dict) and "k" in loaded and strict_equal(loaded["k"], value)


def _scalar_text(value: Any, old: ScalarNode | None = None) -> str:
    """One-line YAML for a scalar, keeping the old quote style when possible."""
    if isinstance(value, str):
        style = old.style if (old is not None and old.style in ("'", '"')) else None
        candidates = []
        if style == "'":
            candidates.append("'" + value.replace("'", "''") + "'")
        elif style == '"':
            candidates.append(json.dumps(value, ensure_ascii=False))
        if "\n" not in value and "\r" not in value:
            candidates += [value, "'" + value.replace("'", "''") + "'"]
        candidates.append(json.dumps(value, ensure_ascii=False))
        for text in candidates:
            if text and _reads_back(text, value):
                return text
        raise PatchError(f"Cannot represent string {value[:40]!r} as a one-line YAML scalar")
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, _dt.date, _dt.datetime)):
        text = _dump(value).splitlines()[0]
        if _reads_back(text, value):
            return text
    if value == {} or value == []:
        return "{}" if isinstance(value, dict) else "[]"
    raise PatchError(f"Unsupported scalar type {type(value).__name__}")


# -------------------------------------------------------------------- patcher
@dataclass
class _Slot:
    kind: str            # map | seq | root
    col: int             # column of the key or of the '-' indicator
    after: int           # index just after ':' or '-'


class _Escalate(Exception):
    """Internal: the current block needs a whole-value replacement."""


class _Patcher:
    def __init__(self, text: str) -> None:
        self.text = text
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.edits: list[tuple[int, int, str]] = []
        self.changed: list[PathT] = []
        self.rewritten: list[tuple[PathT, str]] = []
        self.indent = 2
        self.seq_offset = 0

    # -- positions -----------------------------------------------------------
    def line_start(self, idx: int) -> int:
        return self.text.rfind("\n", 0, idx) + 1

    def line_end_incl(self, idx: int) -> int:
        j = self.text.find("\n", idx)
        return len(self.text) if j < 0 else j + 1

    def content_end(self, node: Node) -> int:
        if isinstance(node, ScalarNode):
            end = node.end_mark.index
            if node.style in ("|", ">"):
                while end > node.start_mark.index and self.text[end - 1] in "\r\n":
                    end -= 1
            return max(end, node.start_mark.index)
        if getattr(node, "flow_style", False):
            return node.end_mark.index
        if isinstance(node, MappingNode):
            return self.content_end(node.value[-1][1]) if node.value else node.start_mark.index
        if isinstance(node, SequenceNode):
            return self.content_end(node.value[-1]) if node.value else node.start_mark.index
        return node.end_mark.index

    def colon_after(self, key: Node) -> int:
        i = self.text.find(":", key.end_mark.index)
        if i < 0:
            raise PatchError("mapping key without ':'")
        return i + 1

    def dash_before(self, item: Node) -> int:
        i = self.text.rfind("-", self.line_start(item.start_mark.index), item.start_mark.index + 1)
        if i < 0:  # item starts on the line after a bare '-'
            i = self.text.rfind("-", 0, item.start_mark.index)
        return i

    # -- style detection ----------------------------------------------------
    def detect_style(self, root: Node) -> None:
        indents: Counter[int] = Counter()
        offsets: Counter[int] = Counter()

        def walk(node: Node, key_col: int | None) -> None:
            if isinstance(node, MappingNode) and not node.flow_style:
                if key_col is not None and node.value:
                    delta = node.value[0][0].start_mark.column - key_col
                    if delta > 0 and node.value[0][0].start_mark.line != node.start_mark.line - 1:
                        indents[delta] += 1
                for k, v in node.value:
                    walk(v, k.start_mark.column)
            elif isinstance(node, SequenceNode) and not node.flow_style:
                if key_col is not None and node.value:
                    offsets[node.start_mark.column - key_col] += 1
                for item in node.value:
                    walk(item, None)

        walk(root, None)
        if indents:
            self.indent = indents.most_common(1)[0][0]
        if offsets:
            self.seq_offset = max(0, offsets.most_common(1)[0][0])

    # -- edit recording -----------------------------------------------------
    def replace(self, start: int, end: int, new: str) -> None:
        self.edits.append((start, end, new))

    def block_lines(self, value: Any) -> list[str]:
        return _dump(value, indent=self.indent, seq_offset=self.seq_offset).split("\n")

    def indented(self, lines: list[str], col: int, first_inline: bool) -> str:
        pad = " " * col
        out = []
        for i, line in enumerate(lines):
            if i == 0 and first_inline:
                out.append(line)
            else:
                out.append((pad + line) if line else line)
        return self.nl.join(out)

    # -- recursion ----------------------------------------------------------
    def patch_value(self, node: Node, old: Any, new: Any, path: PathT, slot: _Slot) -> None:
        if strict_equal(old, new):
            return
        try:
            if isinstance(node, MappingNode) and not node.flow_style and isinstance(old, dict) \
                    and isinstance(new, dict) and old and new:
                self.patch_mapping(node, old, new, path)
                return
            if isinstance(node, SequenceNode) and not node.flow_style and isinstance(old, list) \
                    and isinstance(new, list) and old and len(old) == len(new):
                for i, item in enumerate(node.value):
                    self.patch_value(item, old[i], new[i], path + (i,),
                                     _Slot("seq", item.start_mark.column, self.dash_before(item) + 1))
                return
            if isinstance(node, ScalarNode) and not isinstance(new, (dict, list)):
                start, end = node.start_mark.index, self.content_end(node)
                text = _scalar_text(new, node)
                if node.style in ("|", ">"):
                    self.rewritten.append((path, "block scalar re-rendered on one line"))
                self.replace(start, end, text)
                self.changed.append(path)
                return
        except _Escalate as why:
            self.replace_value(node, new, path, slot, str(why) or "re-rendered")
            return
        reason = "length-changed" if isinstance(old, list) and isinstance(new, list) else "type-changed"
        if isinstance(node, (MappingNode, SequenceNode)) and getattr(node, "flow_style", False):
            reason = "flow-style"
        self.replace_value(node, new, path, slot, reason)

    def patch_mapping(self, node: MappingNode, old: dict, new: dict, path: PathT) -> None:
        if len(node.value) != len(old):
            raise _Escalate("duplicate or merge keys")
        pairs = list(zip(old.keys(), node.value))
        for pykey, (knode, _vnode) in pairs:
            if not isinstance(knode, ScalarNode) or str(knode.value) != str(pykey) and not isinstance(pykey, (bool, int, float, type(None))):
                raise _Escalate("complex keys")
        local: list[tuple[int, int, str]] = []
        saved_edits, self.edits = self.edits, local
        try:
            deleted_ranges: list[tuple[int, int]] = []
            last_kept_end: int | None = None
            for pykey, (knode, vnode) in pairs:
                if pykey not in new:
                    ls = self.line_start(knode.start_mark.index)
                    if self.text[ls:knode.start_mark.index].strip():   # key shares a line with '- '
                        raise _Escalate("first key of a list item removed")
                    end = self.line_end_incl(self.content_end(vnode))
                    self.replace(ls, end, "")
                    deleted_ranges.append((ls, end))
                    self.changed.append(path + (pykey,))
                    continue
                last_kept_end = self.line_end_incl(self.content_end(vnode))
                if not strict_equal(old[pykey], new[pykey]):
                    self.patch_value(vnode, old[pykey], new[pykey], path + (pykey,),
                                     _Slot("map", knode.start_mark.column, self.colon_after(knode)))
            added = [k for k in new if k not in old]
            if added:
                if last_kept_end is None:
                    raise _Escalate("all keys replaced")
                col = node.value[0][0].start_mark.column
                lines = self.block_lines({k: new[k] for k in added})
                text = self.indented(lines, col, first_inline=False) + self.nl
                if last_kept_end == len(self.text) and not self.text.endswith("\n"):
                    text = self.nl + text[: -len(self.nl)]
                self.replace(last_kept_end, last_kept_end, text)
                self.changed += [path + (k,) for k in added]
        finally:
            self.edits = saved_edits
        self.edits += local

    def replace_value(self, node: Node, new: Any, path: PathT, slot: _Slot, reason: str) -> None:
        is_collection = isinstance(new, (dict, list)) and bool(new)
        old_block = isinstance(node, (MappingNode, SequenceNode)) and not getattr(node, "flow_style", False) \
            and bool(node.value)
        if not is_collection:
            text = _scalar_text(new, node if isinstance(node, ScalarNode) else None)
            if isinstance(node, ScalarNode) and node.style not in ("|", ">") and node.end_mark.index > node.start_mark.index:
                self.replace(node.start_mark.index, self.content_end(node), text)
            elif slot.kind == "root":
                raise PatchError("document root is not a mapping")
            else:
                end = slot.after if self._empty_null(node) else self.content_end(node)
                self.replace(slot.after, max(end, slot.after), " " + text)
                if old_block:
                    self.rewritten.append((path, reason))
        elif old_block:
            col = node.start_mark.column
            lines = self.block_lines(new)
            self.replace(node.start_mark.index, self.content_end(node), self.indented(lines, col, first_inline=True))
            self.rewritten.append((path, reason))
        else:
            lines = self.block_lines(new)
            if slot.kind == "map":
                child = slot.col + (self.seq_offset if isinstance(new, list) else self.indent)
                text = self.nl + self.indented(lines, child, first_inline=False)
            elif slot.kind == "seq":
                dash_col = slot.after - 1 - self.line_start(slot.after)
                text = " " + self.indented(lines, dash_col + 2, first_inline=True)
            else:
                raise PatchError("document root is not a mapping")
            # Old value is a scalar, an empty null ("key:") or a flow collection.
            end = slot.after if self._empty_null(node) else self.content_end(node)
            self.replace(slot.after, max(end, slot.after), text)
            if getattr(node, "flow_style", False):
                self.rewritten.append((path, reason))
        self.changed.append(path)

    @staticmethod
    def _empty_null(node: Node) -> bool:
        return isinstance(node, ScalarNode) and node.end_mark.index <= node.start_mark.index

    def apply(self) -> str:
        edits = sorted(self.edits, key=lambda e: (e[0], e[1]))
        for (_s1, e1, _), (s2, _e2, _) in zip(edits, edits[1:], strict=False):
            if s2 < e1:
                raise PatchError("internal: overlapping edits")
        out = self.text
        for start, end, new in sorted(self.edits, key=lambda e: (e[0], e[1]), reverse=True):
            out = out[:start] + new + out[end:]
        return out


def renamed(mapping: dict, old: Any, new: Any) -> dict:
    """A copy of ``mapping`` with key ``old`` renamed to ``new`` at the same position."""
    return {(new if k == old else k): v for k, v in mapping.items()}


def rename_key(text: str, path: PathT, new: str) -> str:
    """Rename the mapping key at ``path`` in place: only the key's characters change, so its position, its
    value and every comment stay.  Verified by parsing the result."""
    if text.startswith("﻿"):
        return "﻿" + rename_key(text[1:], path, new)
    if not path:
        raise PatchError("nothing to rename")
    data = fast_load(text)
    parent_path, old = tuple(path[:-1]), path[-1]
    parent = data
    for key in parent_path:
        parent = parent.get(key) if isinstance(parent, dict) else None
    if not isinstance(parent, dict) or old not in parent:
        raise PatchError(f"'{old}' is not in the file")
    if new in parent:
        raise PatchError(f"'{new}' already exists")
    node = yaml.compose(text, Loader=_Loader)
    key_node = None
    for key in path:
        if not isinstance(node, MappingNode) or node.flow_style:
            raise PatchError("only block mappings can be renamed in place")
        for k, v in node.value:
            if isinstance(k, ScalarNode) and fast_load(text[k.start_mark.index:k.end_mark.index]) == key:
                key_node, node = k, v
                break
        else:
            raise PatchError(f"'{key}' not found in the text")
    out = text[:key_node.start_mark.index] + _scalar_text(new, key_node) + text[key_node.end_mark.index:]
    try:
        check = fast_load(out)
    except YamlParseError as exc:
        raise PatchError(f"renamed text does not parse: {exc}") from None
    want = renamed(parent, old, new)
    expected = set_in(deep_copy(data), parent_path, want)
    got = get_in(check, parent_path)
    if not strict_equal(check, expected) or not isinstance(got, dict) or list(got) != list(want):
        raise PatchError("renamed text does not read back as intended")
    return out


def _full_rewrite(target: Any, original: str) -> str:
    nl = "\r\n" if "\r\n" in original else "\n"
    bom = "﻿" if original.startswith("﻿") else ""
    body = _dump(target) if target not in (None, {}) else "{}"
    return bom + body.replace("\n", nl) + nl


def render_patched(original_text: str, target: Any, *, allow_full_rewrite: bool = False) -> PatchResult:
    original = fast_load(original_text)
    if strict_equal(original, target):
        return PatchResult(original_text, "unchanged")
    if original_text.startswith("﻿"):
        # libyaml's marks do not count a leading BOM; patch without it, then restore it.
        inner = render_patched(original_text[1:], target, allow_full_rewrite=allow_full_rewrite)
        inner.text = "﻿" + inner.text
        return inner
    try:
        if original is None:
            # Empty or comment-only document: append the new content after it.
            nl = "\r\n" if "\r\n" in original_text else "\n"
            prefix = original_text if (not original_text or original_text.endswith("\n")) else original_text + nl
            text = prefix + (_dump(target) if target not in (None, {}) else "{}").replace("\n", nl) + nl
            result = PatchResult(text, "surgical", diff_paths({}, target))
        else:
            root = yaml.compose(original_text, Loader=_Loader)
            if not isinstance(root, MappingNode) or root.flow_style or not isinstance(original, dict) \
                    or not isinstance(target, dict) or not target:
                raise PatchError("only non-empty block mappings can be patched surgically")
            patcher = _Patcher(original_text)
            patcher.detect_style(root)
            try:
                patcher.patch_mapping(root, original, target, ())
            except _Escalate as why:
                raise PatchError(f"cannot patch the top level surgically ({why})") from None
            result = PatchResult(patcher.apply(), "surgical", patcher.changed, patcher.rewritten)
        try:
            check = fast_load(result.text)
        except YamlParseError as exc:
            raise PatchError(f"patched text does not parse: {exc}") from None
        if not strict_equal(check, target):
            wrong = diff_paths(check, target)[:3]
            raise PatchError(f"patched text does not read back as intended (at {wrong})")
        return result
    except PatchError:
        if not allow_full_rewrite:
            raise
        text = _full_rewrite(target, original_text)
        if not strict_equal(fast_load(text), target):
            raise PatchError("full rewrite could not be verified") from None
        return PatchResult(text, "full-rewrite", diff_paths(original, target), [((), "full rewrite")])
