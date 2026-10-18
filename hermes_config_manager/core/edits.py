"""Pending edits ("drafts") recorded per agent as ordered, field-level operations.

Design rules:

* An edit is recorded against one concrete agent.  Changing which agents are
  checked later never redirects an existing edit.
* Operations patch a path; they never copy the viewed agent's unrelated
  settings.  Replacing a whole block is a distinct, explicit operation kind.
* The editor always shows the *draft view* (base data with the agent's
  operations applied), so each new edit is relative to what the user sees.
* Adding an operation at ``P`` supersedes earlier operations at ``P`` or below
  it.  Later operations below an earlier ancestor operation are kept and are
  applied on top of it (e.g. remove a block, then set one field inside it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable

from .paths import (
    MISSING,
    PathT,
    deep_copy,
    delete_in,
    format_path,
    get_in,
    is_ancestor,
    is_same_or_ancestor,
    set_in,
)


class OpKind(str, Enum):
    SET = "set"                      # set a field (scalar, list or mapping value)
    REMOVE = "remove"                # delete the key: runtime default applies
    REPLACE_BLOCK = "replace_block"  # explicit whole-block replacement
    RENAME_KEY = "rename_key"        # rename the mapping key at ``path`` to ``value``, in place
    REPLACE_FILE = "replace_file"    # the whole file becomes ``text`` (``value`` = its data); creates it if missing
    DELETE_FILE = "delete_file"      # the file is removed (kept next to itself as a backup)


# Kinds the path-wise helpers cannot follow: a draft with one of them is read from its whole view.
WHOLE_KINDS = (OpKind.RENAME_KEY, OpKind.REPLACE_FILE, OpKind.DELETE_FILE)


def op_already_applied(op, current: Any) -> bool:
    """The file already holds what ``op`` would write (the same change was made elsewhere: no conflict)."""
    from .yamlpatch import strict_equal

    if op.kind in WHOLE_KINDS:
        return False
    if op.kind is OpKind.REMOVE:
        return current is MISSING
    return current is not MISSING and strict_equal(current, op.value)


@dataclass(frozen=True)
class EditOp:
    kind: OpKind
    path: PathT
    value: Any = None
    # Where the edit came from: field | clear | revert | preset:<name> | yaml |
    # copy:<agent-key> | block.  Shown in the review so bulk edits are traceable.
    origin: str = "field"
    text: str | None = None          # REPLACE_FILE: the new file's exact text

    def describe(self) -> str:
        where = format_path(self.path) or "<document>"
        if self.kind is OpKind.REMOVE:
            return f"remove {where} (use default)"
        if self.kind is OpKind.REPLACE_BLOCK:
            return f"replace block {where}"
        if self.kind is OpKind.RENAME_KEY:
            return f"rename {where} to {self.value}"
        if self.kind is OpKind.REPLACE_FILE:
            return "replace the whole file"
        if self.kind is OpKind.DELETE_FILE:
            return "remove the file"
        if self.origin == "clear":
            return f"clear {where}"
        return f"set {where}"


def apply_ops(base: Any, ops: Iterable[EditOp]) -> Any:
    """Return a deep copy of ``base`` with ``ops`` applied in order."""
    data = deep_copy(base) if base is not None else {}
    for op in ops:
        if op.kind is OpKind.REMOVE:
            delete_in(data, op.path)
        elif op.kind is OpKind.DELETE_FILE:
            data = {}
        elif op.kind is OpKind.RENAME_KEY:
            from .yamlpatch import renamed

            parent = get_in(data, op.path[:-1])
            if isinstance(parent, dict) and op.path[-1] in parent and op.value not in parent:
                data = set_in(data, op.path[:-1], renamed(parent, op.path[-1], op.value))
        else:
            data = set_in(data, op.path, deep_copy(op.value))
    return data


def view_get(base: Any, ops: list[EditOp], path: PathT) -> Any:
    """Value at ``path`` in the draft view, without copying the whole document."""
    if any(op.kind in WHOLE_KINDS for op in ops):
        return get_in(apply_ops(base, ops), path)
    start = 0
    value: Any = MISSING
    resolved = False
    for index in range(len(ops) - 1, -1, -1):
        op = ops[index]
        if is_same_or_ancestor(op.path, path):
            if op.kind is OpKind.REMOVE:
                value = MISSING
            else:
                value = get_in(op.value, path[len(op.path):])
            start = index + 1
            resolved = True
            break
    if not resolved:
        value = get_in(base, path)
    below = [op for op in ops[start:] if is_ancestor(path, op.path)]
    if not below:
        return value
    value = deep_copy(value) if value is not MISSING else {}
    for op in below:
        rel = op.path[len(path):]
        if op.kind is OpKind.REMOVE:
            delete_in(value, rel)
        else:
            value = set_in(value, rel, deep_copy(op.value))
    return value


@dataclass
class AgentDraft:
    """Ordered operations for one agent, authored against ``base``."""

    agent_key: str
    base: Any
    base_sha: str = ""
    ops: list[EditOp] = field(default_factory=list)
    # Set when the agent was reloaded and its file changed after edits began.
    base_changed: bool = False
    # Edited paths that were also changed on the host after the edit was made (found on reload).
    # Writing them would silently overwrite that change: Review refuses until they are staged again.
    conflicts: list = field(default_factory=list)

    def add(self, op: EditOp) -> None:
        if op.kind is OpKind.RENAME_KEY:          # earlier edits under the old name still apply (renamed)
            self.ops.append(op)
            return
        whole = op.kind in (OpKind.REPLACE_FILE, OpKind.DELETE_FILE)
        # A rename is only undone by reverting it (or by replacing the whole file): a later edit at the old
        # name is about a new key of that name, made after the rename.
        self.ops = [o for o in self.ops if not is_same_or_ancestor(op.path, o.path)
                    or (o.kind is OpKind.RENAME_KEY and not whole)]
        self.conflicts = [c for c in self.conflicts if not (is_same_or_ancestor(op.path, c)
                                                            or is_ancestor(c, op.path))]
        self.ops.append(op)
        self._drop_noop_tail()

    def _drop_noop_tail(self) -> None:
        """Drop the newest op when it merely restores the base value."""
        if not self.ops:
            return
        op = self.ops[-1]
        if op.kind in WHOLE_KINDS or any(is_ancestor(o.path, op.path) for o in self.ops[:-1]):
            return             # a whole-file op is compared by text when the file is written
        base_value = get_in(self.base, op.path)
        if op.kind is OpKind.REMOVE:
            same = base_value is MISSING
        else:
            from .yamlpatch import strict_equal  # local import keeps module load light

            same = base_value is not MISSING and strict_equal(base_value, op.value)
        if same:
            self.ops.pop()

    def revert(self, path: PathT) -> None:
        """Leave ``path`` unchanged (back to the base value)."""
        if any(is_ancestor(o.path, path) for o in self.ops):
            base_value = get_in(self.base, path)
            if base_value is MISSING:
                self.add(EditOp(OpKind.REMOVE, path, origin="revert"))
            else:
                self.add(EditOp(OpKind.SET, path, deep_copy(base_value), origin="revert"))
            # Reverting inside a replaced block may make the block identical to base.
            self._collapse_if_identical()
        else:
            self.ops = [o for o in self.ops if not is_same_or_ancestor(path, o.path)]
            self.conflicts = [c for c in self.conflicts if not is_same_or_ancestor(path, c)]

    def _collapse_if_identical(self) -> None:
        from .yamlpatch import strict_equal

        if strict_equal(apply_ops(self.base, self.ops), self.base):
            self.ops = []

    def get(self, path: PathT) -> Any:
        return view_get(self.base, self.ops, path)

    def view(self) -> Any:
        return apply_ops(self.base, self.ops)

    def touches(self, path: PathT) -> bool:
        """True if any operation is at, above or below ``path``."""
        return any(
            is_same_or_ancestor(o.path, path) or is_ancestor(path, o.path) for o in self.ops
        )

    def is_modified(self, path: PathT) -> bool:
        if not self.touches(path):
            return False
        from .yamlpatch import strict_equal

        return not strict_equal(self.get(path), get_in(self.base, path))

    def rebase(self, new_base: Any, new_sha: str) -> list[PathT]:
        """Adopt a refreshed file; return op paths whose base value changed externally."""
        from .yamlpatch import strict_equal

        conflicts = [
            op.path
            for op in self.ops
            if not strict_equal(get_in(self.base, op.path), get_in(new_base, op.path))
            and not op_already_applied(op, get_in(new_base, op.path))
        ]
        if new_sha != self.base_sha:
            self.base_changed = self.base_changed or bool(self.ops)
        for p in conflicts:
            if p not in self.conflicts:
                self.conflicts.append(p)
        self.base = new_base
        self.base_sha = new_sha
        return conflicts


class DraftStore:
    """All pending edits of the session, keyed by agent key.

    Qt-free; the UI subscribes with :meth:`subscribe` and receives the set of
    agent keys whose drafts changed.  Every mutating call is one undoable step.
    """

    def __init__(self) -> None:
        self._drafts: dict[str, AgentDraft] = {}
        self._listeners: list[Callable[[set[str]], None]] = []
        self._undo: list[dict[str, tuple[list[EditOp], bool, list] | None]] = []
        self._redo: list[dict[str, tuple[list[EditOp], bool, list] | None]] = []

    # -- observation -----------------------------------------------------
    def subscribe(self, callback: Callable[[set[str]], None]) -> None:
        self._listeners.append(callback)

    def _notify(self, keys: set[str]) -> None:
        if keys:
            for callback in list(self._listeners):
                callback(keys)

    # -- access ----------------------------------------------------------
    def draft(self, key: str) -> AgentDraft | None:
        return self._drafts.get(key)

    def ensure(self, key: str, base: Any, base_sha: str) -> AgentDraft:
        draft = self._drafts.get(key)
        if draft is None:
            draft = AgentDraft(key, base, base_sha)
            self._drafts[key] = draft
        elif not draft.ops and draft.base_sha != base_sha:      # "" = the file is gone (a roster file)
            # Nothing pending: new edits are authored against the file as it is now
            # (e.g. after this app wrote it), never against an older base.
            draft.base, draft.base_sha, draft.base_changed = base, base_sha, False
        return draft

    def adopt_applied(self, key: str, new_base: Any, new_sha: str) -> None:
        """The agent's edits were written: the written file becomes the base.

        The applied edits leave this agent's undo history (undoing them now would
        need a new edit and a new review, not a silent draft change).
        """
        draft = self._drafts.get(key)
        if draft is None:
            return
        draft.base, draft.base_sha, draft.ops, draft.base_changed = new_base, new_sha, [], False
        draft.conflicts = []
        for stack in (self._undo, self._redo):
            for entry in stack:
                entry.pop(key, None)
            stack[:] = [e for e in stack if e]
        self._notify({key})

    def keys_with_changes(self) -> list[str]:
        return [k for k, d in self._drafts.items() if d.ops]

    def has_changes(self, key: str | None = None) -> bool:
        if key is None:
            return any(d.ops for d in self._drafts.values())
        draft = self._drafts.get(key)
        return bool(draft and draft.ops)

    # -- mutation (each call = one undo step) ----------------------------
    def _snapshot(self, keys: Iterable[str]) -> dict[str, tuple[list[EditOp], bool, list] | None]:
        snap: dict[str, tuple[list[EditOp], bool, list] | None] = {}
        for key in keys:
            d = self._drafts.get(key)
            snap[key] = (list(d.ops), d.base_changed, list(d.conflicts)) if d else None
        return snap

    def _commit(self, snap: dict[str, tuple[list[EditOp], bool, list] | None]) -> set[str]:
        changed = {
            k
            for k, before in snap.items()
            if (before[0] if before else []) != (self._drafts[k].ops if k in self._drafts else [])
            or (before[2] if before else []) != (self._drafts[k].conflicts if k in self._drafts else [])
        }
        if changed:
            self._undo.append({k: snap[k] for k in changed})
            self._redo.clear()
            del self._undo[:-200]
        self._notify(changed)
        return changed

    def apply(self, targets: dict[str, tuple[Any, str]], op: EditOp | Callable[[str], EditOp | None]) -> set[str]:
        """Add ``op`` to every target.  ``targets`` maps key → (base, sha).

        ``op`` may be a callable producing a per-agent op (or None to skip),
        used e.g. for list edits that depend on each agent's current list.
        """
        snap = self._snapshot(targets)
        for key, (base, sha) in targets.items():
            draft = self.ensure(key, base, sha)
            this = op(key) if callable(op) else op
            if this is not None:
                draft.add(this)
        return self._commit(snap)

    def apply_many(self, per_agent: dict[str, tuple[Any, str, list[EditOp]]]) -> set[str]:
        """Add several ops per agent as one undo step (presets, YAML edits).

        Returns the agents whose pending edits actually changed (ops that merely
        restate the loaded value are dropped and do not count).
        """
        snap = self._snapshot(per_agent)
        for key, (base, sha, ops) in per_agent.items():
            draft = self.ensure(key, base, sha)
            for op in ops:
                draft.add(op)
        return self._commit(snap)

    def revert(self, keys: Iterable[str], path: PathT | None = None) -> None:
        keys = [k for k in keys if k in self._drafts]
        snap = self._snapshot(keys)
        for key in keys:
            draft = self._drafts[key]
            if path is None:
                draft.ops = []
                draft.base_changed = False
            else:
                draft.revert(path)
        self._commit(snap)

    def discard(self, keys: Iterable[str] | None = None) -> None:
        """Forget drafts entirely (e.g. after a successful apply)."""
        keys = list(self._drafts) if keys is None else [k for k in keys if k in self._drafts]
        snap = self._snapshot(keys)
        for key in keys:
            self._drafts[key].ops = []
            self._drafts[key].base_changed = False
        self._commit(snap)

    def rebase(self, key: str, new_base: Any, new_sha: str) -> list[PathT]:
        draft = self._drafts.get(key)
        if draft is None:
            return []
        conflicts = draft.rebase(new_base, new_sha)
        self._notify({key})
        return conflicts

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def _swap(self, source: list, sink: list) -> None:
        if not source:
            return
        step = source.pop()
        sink.append(self._snapshot(step))
        for key, before in step.items():
            draft = self._drafts.get(key)
            if draft is None:
                continue
            # Keep the draft object (it holds the base) so redo can restore into it.
            draft.ops, draft.base_changed = (list(before[0]), before[1]) if before else ([], False)
            draft.conflicts = list(before[2]) if before else []
        self._notify(set(step))

    def undo(self) -> None:
        self._swap(self._undo, self._redo)

    def redo(self) -> None:
        self._swap(self._redo, self._undo)
