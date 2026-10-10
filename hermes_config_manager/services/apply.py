"""Preparing and applying pending edits safely, one agent at a time.

prepare  (read-only, worker thread)
    1. Re-read the file from the host (never trust the loaded copy for writing).
    2. If it changed since the edits were made: rebase the field edits onto the
       new content *only if* none of the edited fields changed externally;
       otherwise report a conflict.
    3. Build the target data (current data + operations) and render it by
       surgically patching the current text (comments/format preserved).
    4. Verify semantically: parsing the new text with the runtime's YAML
       loader must give exactly the target data (done by render_patched).
    5. Produce a credential-masked unified diff and a structured change list.

apply    (worker thread)
    write_checked: SHA re-check immediately before writing → backup →
    atomic replace → readback verification.  Every outcome is reported per
    agent; nothing is presented as success unless the readback matched.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any

from ..core import secrets
from ..core.edits import EditOp, apply_ops, op_already_applied
from ..core.paths import MISSING, PathT, format_path, get_in
from ..core.yamlpatch import PatchError, YamlParseError, diff_paths, fast_load, render_patched, strict_equal
from ..transports.base import ConflictError, Revision, Transport, TransportError

READY = "ready"
NOOP = "no change"
CONFLICT = "conflict"
INVALID = "invalid"
ERROR = "error"

WRITTEN = "written"
FAILED = "failed"
UNCERTAIN = "uncertain"
CANCELLED = "cancelled"
SKIPPED = "skipped"
UP_TO_DATE = "up to date"       # the file already had the staged values: nothing written, nothing left staged


@dataclass
class DraftSnapshot:
    """Immutable copy of what the user asked for, captured when review opens."""

    key: str
    host: str
    display: str
    path: str
    base_data: Any
    base_sha: str
    ops: list[EditOp]
    conflicts: list = field(default_factory=list)      # changed on the host after staging (found on reload)


@dataclass
class Change:
    path: PathT
    before: Any
    after: Any

    @property
    def kind(self) -> str:
        if self.before is MISSING:
            return "added"
        if self.after is MISSING:
            return "removed"
        return "changed"


@dataclass
class PreparedChange:
    snapshot: DraftSnapshot
    status: str
    message: str = ""
    current_sha: str | None = None
    original_text: str = ""
    new_text: str | None = None
    target_data: Any = None
    diff: str = ""
    changes: list[Change] = field(default_factory=list)
    rebased: bool = False
    external_conflicts: list[PathT] = field(default_factory=list)
    strategy: str = ""
    rewritten_blocks: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return self.snapshot.key

    @property
    def writable(self) -> bool:
        return self.status == READY and self.new_text is not None


@dataclass
class ApplyResult:
    key: str
    display: str
    status: str
    message: str
    backup_path: str | None = None
    revision: Revision | None = None
    raw: bytes | None = None
    data: Any = None


def masked_diff(before: str, after: str, label: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"{label} (current)", tofile=f"{label} (after apply)", n=3,
    )
    return secrets.mask_text("".join(lines))


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8")


def _encode(text: str) -> bytes:
    return text.encode("utf-8")


def prepare_change(task_ctx, transport: Transport, snap: DraftSnapshot, schema=None) -> PreparedChange:
    try:
        current = transport.read_bytes(snap.path)
    except TransportError as exc:
        return PreparedChange(snap, ERROR, f"Could not read the current file: {exc}")
    if task_ctx is not None:
        task_ctx.check()
    try:
        text = _decode(current.data)
    except UnicodeDecodeError:
        return PreparedChange(snap, INVALID, "The file on the host is not valid UTF-8; not editing it.")
    try:
        current_data = fast_load(text)
    except YamlParseError as exc:
        return PreparedChange(snap, INVALID, f"The file on the host is not valid YAML now ({exc}); not editing it.",
                              current.revision.sha256, text)
    if current_data is None:
        current_data = {}
    if not isinstance(current_data, dict):
        return PreparedChange(snap, INVALID, "The file's top level is not a mapping.", current.revision.sha256, text)

    stale = [p for p in snap.conflicts if any(o.path == p for o in snap.ops)]
    if stale:
        return PreparedChange(
            snap, CONFLICT,
            "Changed on the host after you staged it: " + ", ".join(format_path(p) for p in stale[:5])
            + ". Writing would overwrite that change. Stage the setting again (the page shows the new value) "
              "or unstage it.",
            current.revision.sha256, text, external_conflicts=stale,
        )
    rebased = False
    conflicts: list[PathT] = []
    if current.revision.sha256 != snap.base_sha:
        for op in snap.ops:
            now = get_in(current_data, op.path)
            if not strict_equal(get_in(snap.base_data, op.path), now) and not op_already_applied(op, now):
                conflicts.append(op.path)
        if conflicts:
            return PreparedChange(
                snap, CONFLICT,
                "The file changed on the host after these edits were made, and "
                f"{len(conflicts)} edited field(s) were also changed there: "
                + ", ".join(format_path(p) for p in conflicts[:5])
                + ". Refresh the agent and redo the edits, or skip it.",
                current.revision.sha256, text, external_conflicts=conflicts,
            )
        rebased = True

    target = apply_ops(current_data, snap.ops)
    prepared = PreparedChange(snap, READY, "", current.revision.sha256, text, target_data=target, rebased=rebased)
    if strict_equal(target, current_data):
        prepared.status = NOOP
        prepared.message = "The file already has these values; nothing to write."
        return prepared
    try:
        result = render_patched(text, target)
    except PatchError as exc:
        prepared.status = ERROR
        prepared.message = f"Could not produce a verified edit of the file: {exc}"
        return prepared
    prepared.new_text = result.text
    prepared.strategy = result.strategy
    prepared.rewritten_blocks = list(result.rewritten_blocks)
    prepared.changes = [Change(p, get_in(current_data, p), get_in(target, p)) for p in diff_paths(current_data, target)]
    prepared.diff = masked_diff(text, result.text, f"{snap.display}")
    if rebased:
        prepared.message = "Rebased onto a newer version of the file (edited fields were not changed there)."
    if result.rewritten_blocks:
        prepared.warnings.append(
            "Re-rendered as a whole (comments inside may be lost): "
            + ", ".join(f"{format_path(p)} ({why})" for p, why in result.rewritten_blocks[:5])
        )
    if schema is not None:
        prepared.warnings += type_warnings(prepared.changes, schema)
    return prepared


def type_warnings(changes: list[Change], schema) -> list[str]:
    """Non-blocking hints when a new value's type differs from documented metadata."""
    out = []
    for ch in changes:
        if ch.after is MISSING or ch.after is None:
            continue
        meta = schema.lookup(ch.path)
        if meta is None or not meta.kind or isinstance(ch.after, (dict, list)):
            continue
        actual = {bool: "bool", int: "int", float: "float", str: "str"}.get(type(ch.after))
        if actual and meta.kind != actual and not (meta.kind == "float" and actual == "int"):
            out.append(f"{format_path(ch.path)}: {actual} value where metadata documents {meta.kind}")
        if meta.enum and ch.after not in meta.enum:
            out.append(f"{format_path(ch.path)}: '{ch.after}' is not one of {meta.enum}")
    return out


def apply_change(task_ctx, transport: Transport, prepared: PreparedChange) -> ApplyResult:
    snap = prepared.snapshot
    if not prepared.writable:
        return ApplyResult(snap.key, snap.display, SKIPPED, prepared.message or prepared.status)
    if task_ctx is not None and task_ctx.cancelled:
        return ApplyResult(snap.key, snap.display, CANCELLED, "Cancelled before writing")
    data = _encode(prepared.new_text)
    try:
        written = transport.write_checked(snap.path, data, prepared.current_sha, make_backup=True)
    except ConflictError as exc:
        return ApplyResult(snap.key, snap.display, CONFLICT, f"Not written: {exc}")
    except TransportError as exc:
        status = UNCERTAIN if exc.kind == "uncertain" else FAILED
        return ApplyResult(snap.key, snap.display, status, f"Not written: {exc}" if status == FAILED else str(exc))
    return ApplyResult(
        snap.key, snap.display, WRITTEN,
        "Written and verified by readback" + (f"; backup {written.backup_path}" if written.backup_path else ""),
        written.backup_path, written.revision, data, prepared.target_data,
    )
