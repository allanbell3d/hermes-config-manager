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
from datetime import datetime, timezone
from typing import Any, Callable

from ..core import secrets
from ..core.edits import EditOp, OpKind, apply_ops, op_already_applied
from ..core.paths import MISSING, PathT, format_path, get_in
from ..core.yamlpatch import (PatchError, YamlParseError, diff_paths, fast_load, rename_key, render_patched,
                              strict_equal)
from ..transports.base import BACKUP_DIR, ConflictError, Revision, Transport, TransportError

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
    # (result, file now) -> (why the program reading this file would not use the result | None, warnings).
    # Set for files with rules of their own (hermes-teammates rosters); a rejected result is never written.
    validate: Callable[[Any, Any], tuple[str | None, list[str]]] | None = None
    # False for a file that may only be edited, never replaced or removed as a whole (the master roster).
    whole_file_allowed: bool = True

    @property
    def removes_file(self) -> bool:
        return bool(self.ops) and self.ops[-1].kind is OpKind.DELETE_FILE


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
    removed: bool = False             # the file was removed (renamed to a backup), nothing is in its place


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


def _starting_text(text: str, ops: list[EditOp]) -> tuple[str, list[EditOp]]:
    """The text the patch starts from: the last whole-file replacement's, else the file's; then the ops after it."""
    last = max((i for i, op in enumerate(ops) if op.kind is OpKind.REPLACE_FILE), default=-1)
    if last < 0:
        return text, ops
    return ops[last].text or "", ops[last + 1:]


def _renamed_text(text: str, ops: list[EditOp]) -> str:
    """Apply key renames to the text itself, so a renamed key keeps its place and its comments."""
    for op in ops:
        if op.kind is OpKind.RENAME_KEY:
            try:
                text = rename_key(text, op.path, op.value)
            except PatchError:
                pass        # e.g. a key added by these edits: the patch below writes it (at the end)
    return text


def prepare_change(task_ctx, transport: Transport, snap: DraftSnapshot, schema=None) -> PreparedChange:
    creating = bool(snap.ops) and snap.ops[0].kind is OpKind.REPLACE_FILE and not snap.base_sha
    try:
        current = transport.read_bytes(snap.path)
    except TransportError as exc:
        if not (creating and exc.kind == "not_found"):
            return PreparedChange(snap, ERROR, f"Could not read the current file: {exc}")
        current = None
    if task_ctx is not None:
        task_ctx.check()
    current_sha = current.revision.sha256 if current is not None else None
    try:
        text = _decode(current.data) if current is not None else ""
    except UnicodeDecodeError:
        return PreparedChange(snap, INVALID, "The file on the host is not valid UTF-8; not editing it.")
    try:
        current_data = fast_load(text)
    except YamlParseError as exc:
        if not (snap.ops and snap.ops[0].kind in (OpKind.REPLACE_FILE, OpKind.DELETE_FILE)):
            return PreparedChange(snap, INVALID, f"The file on the host is not valid YAML now ({exc}); not editing "
                                  "it.", current_sha, text)
        current_data = {}                     # replaced or removed as a whole: its content does not matter
    if current_data is None:
        current_data = {}
    if not isinstance(current_data, dict):
        return PreparedChange(snap, INVALID, "The file's top level is not a mapping.", current_sha, text)
    if not snap.whole_file_allowed and any(op.kind in (OpKind.REPLACE_FILE, OpKind.DELETE_FILE) for op in snap.ops):
        return PreparedChange(snap, INVALID, "Not written: this file is never replaced or removed as a whole here.",
                              current_sha, text)
    if current is not None and creating:
        return PreparedChange(snap, CONFLICT, "The file appeared on the host after this was staged. Unstage it and "
                              "replace the table again to overwrite what is there now.", current_sha, text)

    stale = [p for p in snap.conflicts if any(o.path == p for o in snap.ops)]
    if stale:
        return PreparedChange(
            snap, CONFLICT,
            "Changed on the host after you staged it: " + ", ".join(format_path(p) for p in stale[:5])
            + ". Writing would overwrite that change. Stage the setting again (the page shows the new value) "
              "or unstage it.",
            current_sha, text, external_conflicts=stale,
        )
    rebased = False
    conflicts: list[PathT] = []
    if current is not None and current_sha != snap.base_sha:
        for op in snap.ops:
            now = get_in(current_data, op.path)
            if not strict_equal(get_in(snap.base_data, op.path), now) and not op_already_applied(op, now):
                conflicts.append(op.path)
            if op.kind is OpKind.RENAME_KEY:          # the new name must still be free
                to = op.path[:-1] + (op.value,)
                if not strict_equal(get_in(snap.base_data, to), get_in(current_data, to)):
                    conflicts.append(to)
        if conflicts:
            return PreparedChange(
                snap, CONFLICT,
                "The file changed on the host after these edits were made, and "
                f"{len(conflicts)} edited field(s) were also changed there: "
                + ", ".join(format_path(p) for p in conflicts[:5])
                + ". Refresh the agent and redo the edits, or skip it.",
                current_sha, text, external_conflicts=conflicts,
            )
        rebased = True

    if snap.removes_file:
        prepared = PreparedChange(snap, READY, f"Removed from its place: the file is kept in {BACKUP_DIR} as "
                                  f"{transport.basename(snap.path)}.removed-<time>.", current_sha, text,
                                  new_text="", target_data=None, rebased=rebased)
        prepared.changes = [Change((), current_data, MISSING)]
        prepared.diff = masked_diff(text, "", f"{snap.display}")
        return prepared
    target = apply_ops(current_data, snap.ops)
    prepared = PreparedChange(snap, READY, "", current_sha, text, target_data=target, rebased=rebased)
    whole = any(op.kind is OpKind.REPLACE_FILE for op in snap.ops)
    if strict_equal(target, current_data) and current is not None and not whole:
        prepared.status = NOOP
        prepared.message = "The file already has these values; nothing to write."
        return prepared
    if snap.validate is not None:
        why, warned = snap.validate(target, current_data if current is not None else None)
        if why:
            prepared.status = INVALID
            prepared.message = f"Not written: {why}."
            return prepared
        prepared.warnings += warned
    start, rest = _starting_text(text, snap.ops)
    try:
        result = render_patched(_renamed_text(start, rest), target)
    except PatchError as exc:
        prepared.status = ERROR
        prepared.message = f"Could not produce a verified edit of the file: {exc}"
        return prepared
    if whole and current is not None and result.text == text:
        prepared.status = NOOP
        prepared.message = "The file is already exactly this text; nothing to write."
        return prepared
    prepared.new_text = result.text
    prepared.strategy = result.strategy
    prepared.rewritten_blocks = list(result.rewritten_blocks)
    prepared.changes = [Change(p, get_in(current_data, p), get_in(target, p)) for p in diff_paths(current_data, target)]
    prepared.diff = masked_diff(text, result.text, f"{snap.display}")
    if rebased:
        prepared.message = "Rebased onto a newer version of the file (edited fields were not changed there)."
    if whole:
        prepared.message = ("Creates the file." if current is None else "Replaces the whole file.") + \
            (" " + prepared.message if prepared.message else "")
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
    if snap.removes_file:
        return _remove_file(transport, prepared)
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
        "Written and verified by readback" + (f"; backup {written.backup_path}" if written.backup_path else "")
        + (f"; old backups not tidied: {written.tidy_error}" if written.tidy_error else ""),
        written.backup_path, written.revision, data, prepared.target_data,
    )


def _remove_file(transport: Transport, prepared: PreparedChange) -> ApplyResult:
    """Remove the file by moving it into its backup folder as ``<name>.removed-<UTC time>`` (the removal is
    its own backup; it is never rotated away)."""
    snap = prepared.snapshot
    try:
        current = transport.read_bytes(snap.path)
        if current.revision.sha256 != prepared.current_sha:
            return ApplyResult(snap.key, snap.display, CONFLICT, "Not removed: the file changed on the host since "
                               "it was checked.")
        folder = transport.ensure_backup_dir(snap.path)
        target = transport.join(folder, f"{transport.basename(snap.path)}.removed-"
                                        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    except TransportError as exc:
        return ApplyResult(snap.key, snap.display, FAILED, f"Not removed: {exc}")
    try:
        transport.rename(snap.path, target)
    except TransportError as exc:
        # the host may have done the rename before the connection failed: say so instead of "not removed"
        status = FAILED if exc.kind in ("conflict", "not_found", "permission") else UNCERTAIN
        return ApplyResult(snap.key, snap.display, status,
                           f"Not removed: {exc}" if status == FAILED else f"Check the host: {exc}")
    try:
        gone, kept = not transport.exists(snap.path), transport.exists(target)
    except TransportError as exc:
        return ApplyResult(snap.key, snap.display, UNCERTAIN, f"Renamed to {target}; could not check it: {exc}")
    if not (gone and kept):
        return ApplyResult(snap.key, snap.display, UNCERTAIN, f"Renamed to {target}, but the result does not look "
                           "right: check the host.")
    return ApplyResult(snap.key, snap.display, WRITTEN, f"Removed; kept as {target}", target, removed=True)
