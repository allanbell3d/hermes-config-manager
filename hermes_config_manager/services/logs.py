"""On-demand log metadata, preview, and streamed download services."""

from __future__ import annotations

import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from ..core import secrets
from ..transports.base import FileEntry, Transport, TransportError


@dataclass
class DownloadResult:
    """Result of a download operation."""
    completed: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    bytes: int = 0
    cancelled: bool = False
    archive: str | None = None


PREVIEW_LIMIT = 65536
CHUNK_SIZE = 64 * 1024


def log_roots(transport: Transport, host, record=None) -> list[str]:
    """Return candidate log directories in order: <parent of hermes_root>/logs-watch,
    <hermes_root>/logs, agent's own <profile dir>/logs (if record given), and
    host.log_roots entries. Duplicates removed, order preserved."""
    seen: set[str] = set()
    roots: list[str] = []

    hermes_root = host.hermes_root or ""

    # Use transport.join/parent to respect the transport's path style
    if hermes_root:
        parent = transport.parent(hermes_root)
        parent_logs_watch = transport.join(parent, "logs-watch")
    else:
        parent_logs_watch = "logs-watch"

    if parent_logs_watch and parent_logs_watch not in seen:
        roots.append(parent_logs_watch)
        seen.add(parent_logs_watch)

    # <hermes_root>/logs
    if hermes_root:
        hermes_logs = transport.join(hermes_root, "logs")
    else:
        hermes_logs = "logs"

    if hermes_logs and hermes_logs not in seen:
        roots.append(hermes_logs)
        seen.add(hermes_logs)

    # <profile_dir>/logs if record given
    if record is not None and record.path:
        profile_dir = transport.parent(record.path)
        if profile_dir:
            agent_logs = transport.join(profile_dir, "logs")
            if agent_logs and agent_logs not in seen:
                roots.append(agent_logs)
                seen.add(agent_logs)

    # host.log_roots entries
    for root in host.log_roots:
        if root and root not in seen:
            roots.append(root)
            seen.add(root)

    return roots


def list_logs(ctx, transport: Transport, root: str) -> list[FileEntry]:
    """List files and directories in root (non-recursive)."""
    ctx.check()
    return transport.list_dir(root)


def preview(ctx, transport: Transport, path: str, limit: int = PREVIEW_LIMIT) -> str:
    """Read up to limit bytes from path via streaming, decode UTF-8 with error replacement,
    and apply secret masking. Append a note if file is larger than limit."""
    ctx.check()
    bound = max(0, min(int(limit), PREVIEW_LIMIT))

    try:
        # Try to get file size for informational note
        try:
            entry = transport.stat(path)
            file_size = entry.size
        except (TransportError, OSError):
            file_size = None

        # Stream read
        data = b""
        with transport.open_read(path) as source:
            while len(data) < bound:
                ctx.check()
                chunk = source.read(bound - len(data))
                if not chunk:
                    break
                data += chunk

        text = data.decode("utf-8", errors="replace")
        text = secrets.mask_text(text)

        # Add note if file is larger
        if file_size is not None and file_size > bound:
            omitted = file_size - len(data)
            text += f"\n\n... ({omitted:,} bytes omitted) ..."

        return text
    except TransportError:
        raise
    except Exception as exc:
        raise TransportError("other", f"preview: {exc}") from exc


def download(ctx, transport: Transport, paths: list[str], destination_dir: str,
             as_zip: bool = False, zip_name: str | None = None) -> DownloadResult:
    """Stream remote files to local directory (individual) or ZIP archive.

    Streams in 64 KiB chunks; reports progress via ctx.progress((done_bytes, total_bytes, current_name));
    checks ctx.cancelled between chunks; handles name collisions with " (N)" suffix;
    deletes partial local files on failure/cancel; for ZIP, writes to temp file then os.replace.
    Records per-file failures without stopping."""

    result = DownloadResult()
    destination_path = Path(destination_dir)

    if not paths:
        if as_zip:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            final_zip = _collision_safe_path(destination_path)
            with zipfile.ZipFile(final_zip, "w", zipfile.ZIP_DEFLATED):
                pass
            result.archive = str(final_zip)
        return result

    # Calculate total size
    total_bytes = 0
    for path in paths:
        try:
            entry = transport.stat(path)
            total_bytes += entry.size
        except (TransportError, OSError):
            pass

    result.bytes = 0
    used_names: set[str] = set()

    # Prepare destination
    if as_zip:
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        # Create temp file for ZIP
        temp_zip = tempfile.NamedTemporaryFile(mode="w+b", dir=destination_path.parent, delete=False, suffix=".zip")
        temp_zip_path = temp_zip.name
        temp_zip.close()
        zip_handle = zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED)
    else:
        destination_path.mkdir(parents=True, exist_ok=True)
        # Collect existing file names for collision detection
        used_names = {item.name for item in destination_path.iterdir() if item.is_file()}
        zip_handle = None

    try:
        for source_path in paths:
            if ctx.cancelled:
                result.cancelled = True
                break

            ctx.check()

            # Get file size
            try:
                transport.stat(source_path)  # fail early (and per file) when the source vanished
            except (TransportError, OSError) as e:
                result.failures[source_path] = str(e)
                continue

            # Determine local name (with collision handling)
            basename = transport.basename(source_path)
            # Same-named logs from different folders must not collide (on disk or in the ZIP).
            local_name = _collision_safe_name(basename, used_names)

            file_bytes = 0
            partial_path: Path | None = None

            try:
                # Stream from remote
                with transport.open_read(source_path) as source:
                    if as_zip:
                        # Write to temporary file, then into ZIP
                        temp_file = tempfile.TemporaryFile(mode="w+b")
                        try:
                            while True:
                                if ctx.cancelled:
                                    result.cancelled = True
                                    break

                                ctx.check()
                                chunk = source.read(CHUNK_SIZE)
                                if not chunk:
                                    break

                                temp_file.write(chunk)
                                file_bytes += len(chunk)
                                result.bytes += len(chunk)
                                ctx.progress((result.bytes, total_bytes, source_path))

                            if not result.cancelled:
                                # Write to ZIP
                                temp_file.seek(0)
                                with zip_handle.open(local_name, "w") as writer:
                                    while True:
                                        chunk = temp_file.read(CHUNK_SIZE)
                                        if not chunk:
                                            break
                                        writer.write(chunk)
                                result.completed.append(source_path)
                                result.outputs.append(local_name)
                        finally:
                            temp_file.close()
                    else:
                        # Write directly to file
                        target = destination_path / local_name
                        partial_path = target
                        with open(target, "wb") as writer:
                            while True:
                                if ctx.cancelled:
                                    result.cancelled = True
                                    break

                                ctx.check()
                                chunk = source.read(CHUNK_SIZE)
                                if not chunk:
                                    break

                                writer.write(chunk)
                                file_bytes += len(chunk)
                                result.bytes += len(chunk)
                                ctx.progress((result.bytes, total_bytes, source_path))

                        if result.cancelled:
                            # Clean up partial file
                            if partial_path and partial_path.exists():
                                try:
                                    partial_path.unlink()
                                except (OSError, IOError):
                                    pass
                            break
                        else:
                            result.completed.append(source_path)
                            result.outputs.append(str(target))
                            partial_path = None

            except Exception as exc:
                result.failures[source_path] = str(exc)
                # Try to clean up partial file
                if partial_path and partial_path.exists():
                    try:
                        partial_path.unlink()
                    except (OSError, IOError):
                        pass

            if result.cancelled:
                break

    finally:
        if as_zip and zip_handle:
            zip_handle.close()
            final_zip = destination_path.parent / zip_name if zip_name else destination_path
            final_zip = _collision_safe_path(final_zip)
            try:
                if result.cancelled or not result.completed:
                    # Never leave a partial archive that looks complete.
                    os.unlink(temp_zip_path)
                else:
                    os.replace(temp_zip_path, final_zip)
                    result.archive = str(final_zip)
            except (OSError, IOError) as e:
                result.failures["archive"] = str(e)
                # Try to clean up temp file
                try:
                    os.unlink(temp_zip_path)
                except (OSError, IOError):
                    pass

    return result


def _collision_safe_name(name: str, used: set[str]) -> str:
    """Choose a new filename with (' (N)') suffix if needed."""
    candidate = name or "download"
    if candidate not in used:
        used.add(candidate)
        return candidate
    stem, suffix = os.path.splitext(candidate)
    index = 2
    while True:
        candidate = f"{stem} ({index}){suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


def _collision_safe_path(path: Path) -> Path:
    """Choose a new path with (' (N)') suffix if the file exists."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    index = 2
    while True:
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
        index += 1
