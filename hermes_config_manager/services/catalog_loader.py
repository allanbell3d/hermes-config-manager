"""Background loading of the model catalog and configuration metadata.

* The user's model list: ``models.md`` in the app's settings folder, or another file chosen in
  the picker (lines like ``- `provider: model` ``; ``data/catalog-example.md`` shows the format).
  When there is a list, it is the list of available models: the hosts' runtime lists are not added.
* Runtime model lists (only without a model list): ``<hermes_root>/provider_models_cache.json`` per host.
* Model metadata: ``<hermes_root>/models_dev_cache.json`` (5 MB) - compacted
  once and cached locally, keyed by the file's SHA-256.
* In-use identities come from the loaded agents (rebuilt cheaply, debounced).

A failed source never discards a working catalog; errors become notes.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from ..core import appdirs
from ..core import catalog as K
from ..core.schema import load_schema
from ..transports.base import TransportError

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
SAMPLE_CATALOG = DATA_DIR / "catalog-example.md"   # example of the Markdown format
LIST_NAME = "models.md"


def model_list_path(chosen: str | None) -> Path:
    """The model list in use: the file chosen in the picker, else ``models.md`` in the settings folder."""
    return Path(chosen) if chosen else appdirs.config_dir() / LIST_NAME


LIST_HEADER = """# My models

The models the model picker offers. One per line, written as - `provider: model`
(provider and model id exactly as in config.yaml). Headings and any other text are ignored.
Save the file; the picker reads it again the next time it opens.
"""


def create_model_list(path: Path) -> None:
    """Start a model list from the example (only when there is none yet)."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text = SAMPLE_CATALOG.read_text(encoding="utf-8") if SAMPLE_CATALOG.exists() else ""
    _intro, sep, body = text.partition("\n## ")
    path.write_text(LIST_HEADER + ("\n## " + body if sep else ""), encoding="utf-8")


def _fetch_runtime(ctx_task, session_hosts: list, transports: dict) -> tuple[dict, dict | None, list[str]]:
    runtime: dict[str, dict[str, list[str]]] = {}
    notes: list[str] = []
    best_mdev: tuple[float, bytes] | None = None
    for host in session_hosts:
        t = transports.get(host.name)
        if t is None or not host.enabled:
            continue
        try:
            raw = t.read_bytes(t.join(host.hermes_root, "provider_models_cache.json")).data
            runtime[host.name] = K.parse_runtime_provider_cache(json.loads(raw.decode("utf-8")))
        except (TransportError, ValueError, UnicodeDecodeError) as exc:
            notes.append(f"{host.name}: runtime model list unavailable ({exc})")
        ctx_task.check()
        try:
            path = t.join(host.hermes_root, "models_dev_cache.json")
            entry = t.stat(path)
            stamp = entry.modified.timestamp() if entry.modified else 0.0
            if best_mdev is None or stamp > best_mdev[0]:
                best_mdev = (stamp, path.encode("utf-8"))
                best_host = host.name
        except TransportError:
            pass
        ctx_task.check()
    mdev = None
    if best_mdev is not None:
        path = best_mdev[1].decode("utf-8")
        t = transports[best_host]
        cache_file = appdirs.cache_dir() / "models_dev.compact.pickle"
        try:
            raw = t.read_bytes(path).data
            sha = hashlib.sha256(raw).hexdigest()
            cached = None
            try:
                with open(cache_file, "rb") as fh:
                    cached = pickle.load(fh)
            except (OSError, pickle.UnpicklingError, EOFError):
                cached = None
            if isinstance(cached, dict) and cached.get("sha") == sha:
                mdev = cached["data"]
            else:
                mdev = K.compact_models_dev(json.loads(raw.decode("utf-8")))
                try:
                    with open(cache_file, "wb") as fh:
                        pickle.dump({"sha": sha, "data": mdev}, fh, protocol=pickle.HIGHEST_PROTOCOL)
                except OSError:
                    pass
            notes.append(f"Model metadata: models.dev cache from {best_host}")
        except (TransportError, ValueError, UnicodeDecodeError) as exc:
            notes.append(f"Model metadata unavailable ({exc})")
    else:
        # Offline: fall back to the last compacted copy, if any.
        try:
            with open(appdirs.cache_dir() / "models_dev.compact.pickle", "rb") as fh:
                cached = pickle.load(fh)
            mdev = cached.get("data") if isinstance(cached, dict) else None
            if mdev:
                notes.append("Model metadata: last cached models.dev copy (hosts unavailable)")
        except (OSError, pickle.UnpicklingError, EOFError):
            pass
    return runtime, mdev, notes


class CatalogLoader(QObject):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.session = ctx.session
        self.file_entries: list[tuple[str, str]] = []
        self.file_path: str | None = None
        self.file_stamp: tuple[str, float] | None = None
        self.runtime: dict = {}
        self.models_dev: dict | None = None
        self.notes: list[str] = []
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.setInterval(800)
        self._rebuild_timer.timeout.connect(self.rebuild)
        self.session.refresh_finished.connect(self._rebuild_timer.start)
        self.session.agents_changed.connect(self._rebuild_timer.start)

    # -- schema --------------------------------------------------------------
    def load_schema(self) -> None:
        self.session.runner.submit(lambda _c: load_schema(), label="Load metadata", key=("schema",),
                                   on_done=self._schema_done, on_error=lambda e: None)

    def _schema_done(self, schema) -> None:
        self.ctx.schema = schema
        versions = (schema.provenance or {}).get("hermes_versions") or {}
        if isinstance(versions, dict):
            for host, v in versions.items():
                self.ctx.runtime_versions[host] = str(v.get("version") if isinstance(v, dict) else v)
        self.ctx.schema_loaded.emit()

    # -- catalog -------------------------------------------------------------
    def list_path(self) -> Path:
        return model_list_path(self.ctx.catalog_path)

    def _stamp(self) -> tuple[str, float] | None:
        path = self.list_path()
        try:
            return (str(path), path.stat().st_mtime)
        except OSError:
            return None

    def reload(self, force: bool = False) -> None:
        self.file_stamp = self._stamp()
        if self.file_stamp is not None:
            self._reload_file(self.list_path())
        else:
            # No model list: the runtime lists of the hosts and the models already in use are the catalog.
            self.file_entries, self.file_path = [], None
            self.notes = [n for n in self.notes if not n.startswith("Catalog file")]
        self._reload_runtime()

    def reload_if_changed(self) -> bool:
        """Read the model list again when it was created, edited or removed since the last read (cheap)."""
        if self._stamp() == self.file_stamp:
            return False
        self.reload()
        return True

    def _reload_file(self, path: Path) -> None:
        def read_file(_c):
            text = path.read_text(encoding="utf-8")
            entries = K.parse_markdown_catalog(text)
            if not entries:
                raise ValueError(f"{path} contains no `provider: model` entries")
            return entries

        def file_ok(entries):
            self.file_entries, self.file_path = entries, str(path)
            self.rebuild()

        def file_failed(exc):
            self.notes = [n for n in self.notes if not n.startswith("Catalog file")]
            self.notes.append(f"Catalog file not loaded ({exc}); previous entries kept")
            self.rebuild()

        self.session.runner.submit(read_file, label="Read catalog file", key=("catalog-file",),
                                   on_done=file_ok, on_error=file_failed)

    def _reload_runtime(self) -> None:
        hosts = list(self.session.hosts)
        transports = {}
        for h in hosts:
            try:
                transports[h.name] = self.session.transport(h.name)
            except Exception:
                pass

        def runtime_ok(result):
            runtime, mdev, notes = result
            if runtime or not self.runtime:
                self.runtime = runtime or self.runtime
            if mdev:
                self.models_dev = mdev
            self.notes = [n for n in self.notes if n.startswith("Catalog file")] + notes
            self.rebuild()

        self.session.runner.submit(_fetch_runtime, hosts, transports, label="Read runtime model lists",
                                   key=("catalog-runtime",), timeout=90,
                                   on_done=runtime_ok, on_error=lambda e: None)

    def rebuild(self) -> None:
        agents = [r for r in self.session.agents.values() if r.usable]
        schema_efforts = self.ctx.schema.extras.get("reasoning_effort_values") or []
        if isinstance(schema_efforts, dict):
            schema_efforts = schema_efforts.get("values") or []
        runtime = {} if self.file_entries else self.runtime     # a model list replaces the hosts' lists
        catalog = K.build_catalog(self.file_entries, runtime, self.models_dev, agents,
                                  runtime_efforts=[str(v) for v in schema_efforts], file_path=self.file_path)
        catalog.notes = self.notes + catalog.notes
        self.ctx.hub.set_catalog(catalog)
        self.ctx.catalog_loaded.emit()
