"""Application state: the one object every screen receives.

Owns the session (hosts, agents, staged edits), the settings model and the user's layout, the
edits that are not staged yet, 'My defaults', the model catalog and the window preferences.
Screens read from it and listen to its signals; they never talk to hosts directly.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from ..core import appdirs
from ..core.catalog import Catalog, build_catalog
from ..core.layout import Layout, default_layout, reconcile
from ..core.my_defaults import DefaultsStore
from ..core.paths import MISSING, PathT
from ..core.schema import DATA_DIR, schema_from_payload
from ..core.settings_tree import Node, SettingsModel, build_model, leaves, shape, union_shape, used_paths
from ..services.catalog_loader import CatalogLoader
from ..services.editing import DefaultInfo, EditScope, canonical
from ..services.work import WorkStore

SCHEMA_FILE = DATA_DIR / "hermes_schema.json"


class AppState(QObject):
    schema_loaded = Signal()          # (CatalogLoader compatibility)
    catalog_loaded = Signal()
    model_changed = Signal()          # sections / nodes changed
    layout_changed = Signal()         # arrangement or visibility changed
    scope_changed = Signal()          # checked agents or focused agent changed
    values_changed = Signal()         # agent data or staged edits changed
    work_changed = Signal()           # edits that are not staged changed
    columns_changed = Signal()
    toast = Signal(str)
    go_to = Signal(str, object)       # section, path (or None)

    def __init__(self, session, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.settings = QSettings(str(appdirs.settings_file()), QSettings.IniFormat)
        try:
            self.payload: dict = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.payload = {}
        self.schema = schema_from_payload(self.payload) if self.payload else schema_from_payload({})
        self.catalog: Catalog = build_catalog()
        self.catalog_path: str | None = self.settings.value("catalog/path", None) or None
        self.runtime_versions: dict[str, str] = {}
        self.hub = self
        self.work = WorkStore()
        self.work.subscribe(self.work_changed.emit)
        self.defaults = DefaultsStore(appdirs.config_dir() / "my_defaults.json")
        self._shapes: dict[str, tuple[str, dict]] = {}
        self.present: dict[PathT, Any] = {}
        self.used: set[PathT] | None = None
        self.model: SettingsModel = build_model(self.payload, self.schema, {})
        self.layout: Layout = self._load_layout()
        self.blk_open: dict[str, bool] = self._json("ui/blocks", {})
        self.grp_open: set[str] = set(self._json("ui/groups", []))
        self.cat_shut: set[str] = set(self._json("ui/cat_shut", []))
        self.cv = float(self.settings.value("ui/col_value", 35))
        self.ct = float(self.settings.value("ui/col_status", 19))
        self.cv_set = self.settings.contains("ui/col_value")
        self.page_id: str | None = self.settings.value("ui/page", None) or None
        self.customizing = False
        self.loader = CatalogLoader(self, self)
        self._data_timer = QTimer(self)
        self._data_timer.setSingleShot(True)
        self._data_timer.setInterval(120)
        self._data_timer.timeout.connect(self._data_settled)
        session.agents_changed.connect(self._data_timer.start)
        session.agent_updated.connect(lambda _k: self._data_timer.start())
        session.refresh_finished.connect(self._data_timer.start)
        session.checked_changed.connect(self._scope_changed)
        session.viewed_changed.connect(lambda _k: self._scope_changed())
        session.drafts_changed.connect(lambda _keys: self.values_changed.emit())
        self._scope_cache: EditScope | None = None

    # -- startup ----------------------------------------------------------------
    def start(self) -> None:
        self.session.load_cached()
        self._data_settled()
        self.schema_loaded.emit()
        self.session.refresh_all()
        self.loader.reload()

    # -- catalog loader compatibility ----------------------------------------------
    def set_catalog(self, catalog: Catalog) -> None:
        self.catalog = catalog

    # -- agent data ------------------------------------------------------------

    def _data_settled(self) -> None:
        shapes = {}
        for key, rec in self.session.agents.items():
            if not rec.usable:
                continue
            sha = rec.revision.sha256 if rec.revision else str(id(rec.data))
            cached = self._shapes.get(key)
            if cached is None or cached[0] != sha:
                cached = (sha, shape(rec.data))
            shapes[key] = cached
        self._shapes = shapes
        present = union_shape(s for _, s in shapes.values())
        structural = set(present) != set(self.present)
        self.present = present
        if structural:
            self.model = build_model(self.payload, self.schema, present)
            self.layout = reconcile(self.layout, self.model)
        self.used = used_paths(self.model, present) if shapes else None
        self._scope_cache = None
        if structural:
            self.model_changed.emit()
        self.values_changed.emit()

    # -- scope ------------------------------------------------------------------
    def _scope_changed(self) -> None:
        self._scope_cache = None
        ref = self.ref_key()
        if ref:
            self.work.rebase(self.scope(), ref)
        self.scope_changed.emit()

    def scope(self) -> EditScope:
        if self._scope_cache is None:
            self._scope_cache = EditScope(self.session, self.session.checked_in_order(), self.session.viewed,
                                          self.default_for)
        return self._scope_cache

    def one_scope(self) -> EditScope:
        """Only the agent whose values the page shows (``ref_key``): for "Stage for <name> only"."""
        ref = self.ref_key()
        return EditScope(self.session, [ref] if ref else [], self.session.viewed, self.default_for)

    def checked(self) -> list[str]:
        return self.session.checked_in_order()

    def ref_key(self) -> str | None:
        """The agent whose values an unedited row shows: the one you clicked if it is checked, else the
        first agent you checked (never simply the first in the list)."""
        checked = self.session.checked_by_time()
        if not checked:
            return None
        v = self.session.viewed
        return v if v in self.session.checked else checked[0]

    def ref_name(self) -> str:
        key = self.ref_key()
        rec = self.session.record(key) if key else None
        return rec.name if rec else ""

    def default_for(self, path: PathT, host: str = "") -> DefaultInfo | None:
        node = self.model.nodes.get(tuple(path))
        if node is None or node.default is MISSING:
            return None
        return DefaultInfo(node.default, "Hermes defaults", True)

    def distribution(self, node: Node, keys: list[str] | None = None) -> list[tuple[Any, list[str], int]]:
        """(value, agents, how many of them do not set it) - unset agents count under the default."""
        scope = self.scope()
        keys = scope.usable_keys if keys is None else keys
        groups: dict[Any, list] = {}
        for k in keys:
            v = scope.draft_value(k, node.path)
            unset = v is MISSING
            shown = node.default if unset else v
            c = canonical(shown)
            g = groups.get(c)
            if g is None:
                groups[c] = g = [shown, [], 0]
            g[1].append(k)
            g[2] += int(unset)
        return sorted((tuple(g) for g in groups.values()), key=lambda g: -len(g[1]))

    def differs(self, node: Node, keys: list[str]) -> bool:
        if len(keys) < 2:
            return False
        scope = self.scope()
        first = None
        for i, k in enumerate(keys):
            v = scope.draft_value(k, node.path)
            c = canonical(node.default if v is MISSING else v)
            if i == 0:
                first = c
            elif c != first:
                return True
        return False

    def count_differ(self, nodes: list[Node]) -> int:
        keys = self.scope().usable_keys
        if len(keys) < 2:
            return 0
        return sum(1 for n in nodes if self.differs(n, keys))

    def staged_count(self, path: PathT) -> int:
        drafts = self.session.drafts
        n = 0
        for k in self.scope().usable_keys:
            d = drafts.draft(k)
            if d is not None and d.ops and d.touches(path) and d.is_modified(path):
                n += 1
        return n

    def shown_value(self, path: PathT) -> Any:
        """The edit if there is one, else the reference agent's value (MISSING: the default applies)."""
        if self.work.has(path):
            from ..services.work import REMOVE

            w = self.work.value(path)
            return MISSING if w is REMOVE else w
        ref = self.ref_key()
        return self.scope().draft_value(ref, path) if ref else MISSING

    # -- visibility -------------------------------------------------------------
    def node_hidden(self, node: Node) -> bool:
        n: Node | None = node
        while n is not None:
            if self.layout.node_hidden(n.path, self.used):
                return True
            n = n.parent
        return False

    def node_hidden_self(self, node: Node) -> bool:
        return self.layout.node_hidden(node.path, self.used)

    def section_hidden(self, key: str) -> bool:
        sec = self.model.sections.get(key)
        return sec is None or self.layout.section_hidden(sec, self.used)

    def visible_leaves(self, nodes: list[Node]) -> list[Node]:
        return leaves(nodes, skip=self.node_hidden_self)

    def task_hidden(self, task: str) -> bool:
        return self.layout.node_hidden(("auxiliary", task), self.used)

    # -- layout persistence ---------------------------------------------------------
    def _json(self, key: str, default: Any) -> Any:
        raw = self.settings.value(key, None)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    def _load_layout(self) -> Layout:
        lay = Layout.from_json(self._json("layout/v1", None))
        return reconcile(lay, self.model) if lay is not None else default_layout(self.model)

    def save_layout(self, emit: bool = True) -> None:
        self.settings.setValue("layout/v1", json.dumps(self.layout.to_json()))
        if emit:
            self.layout_changed.emit()

    def reset_layout(self) -> None:
        pins = self.layout.pins                        # Related settings are the user's own list: kept
        self.layout = default_layout(self.model)
        self.layout.pins = pins
        self.blk_open.clear()
        self.grp_open.clear()
        self.cat_shut.clear()
        self.save_open()
        self.save_layout()

    def save_open(self) -> None:
        self.settings.setValue("ui/blocks", json.dumps(self.blk_open))
        self.settings.setValue("ui/groups", json.dumps(sorted(self.grp_open)))
        self.settings.setValue("ui/cat_shut", json.dumps(sorted(self.cat_shut)))

    def set_columns(self, cv: float, ct: float, save: bool = False) -> None:
        self.cv, self.ct, self.cv_set = cv, ct, True
        if save:
            self.settings.setValue("ui/col_value", cv)
            self.settings.setValue("ui/col_status", ct)
        self.columns_changed.emit()

    def set_page(self, page_id: str) -> None:
        self.page_id = page_id
        self.settings.setValue("ui/page", page_id)

    def current_page(self):
        p = self.layout.page(self.page_id)
        if p is None:
            for c in self.layout.cats:
                for x in c.pages:
                    if not c.hidden and not x.hidden and any(not self.section_hidden(k) for k in x.sections):
                        p = x
                        break
                if p:
                    break
            if p is None and self.layout.pages():
                p = self.layout.pages()[0]
            if p is not None:
                self.page_id = p.id
        return p

    def visible_sections(self, page) -> list[str]:
        return [k for k in page.sections if not self.section_hidden(k)]

    def page_shown(self, cat, page) -> bool:
        return not cat.hidden and not page.hidden and bool(self.visible_sections(page))

    # -- helpers ----------------------------------------------------------------
    def say(self, text: str) -> None:
        self.toast.emit(text)

    def head_of(self, key: str, chain: str = "primary") -> tuple[str | None, str | None, str | None]:
        from ..services import chains as C
        from ..services.work import spec_for

        rec = self.session.record(key)
        if rec is None or not rec.usable:
            return None, None, None
        c = C.read_chain(self.scope(), spec_for(chain), key)
        h = c[0]
        return h.provider, h.model, h.reasoning_effort
