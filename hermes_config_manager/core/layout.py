"""The user's arrangement of the settings: categories, pages, section order, what is hidden.

Visibility of a section or a setting:

* explicitly hidden by the user (eye)  -> hidden;
* explicitly shown by the user         -> shown;
* otherwise, in "mine" mode, shown only when some loaded agent's file contains it
  ("Show only what my configs use"); in "all" mode, shown.

Nothing here is tied to one person's data: "mine" is worked out from whatever agents are
loaded.  Plain data, saved as JSON by the UI.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .paths import PathT
from .settings_tree import DEFAULT_PINS, HERMES_MENU, PAGE_PLAN, SettingsModel, section_used

MODE_MINE = "mine"
MODE_ALL = "all"


@dataclass
class Page:
    id: str
    label: str
    sections: list[str]
    hidden: bool = False


@dataclass
class Category:
    id: str
    label: str
    pages: list[Page]
    hidden: bool = False


@dataclass
class Layout:
    cats: list[Category]
    mode: str = MODE_MINE
    flat: bool = False
    hidden_secs: set[str] = field(default_factory=set)
    shown_secs: set[str] = field(default_factory=set)
    hidden_nodes: set[PathT] = field(default_factory=set)
    shown_nodes: set[PathT] = field(default_factory=set)
    pins: dict[str, list[PathT]] = field(default_factory=dict)

    # -- lookups ---------------------------------------------------------------
    def pages(self) -> list[Page]:
        return [p for c in self.cats for p in c.pages]

    def page(self, page_id: str | None) -> Page | None:
        return next((p for p in self.pages() if p.id == page_id), None)

    def page_of(self, section: str) -> Page | None:
        return next((p for p in self.pages() if section in p.sections), None)

    def cat_of(self, page_id: str) -> Category | None:
        return next((c for c in self.cats if any(p.id == page_id for p in c.pages)), None)

    # -- visibility -------------------------------------------------------------
    def section_hidden(self, sec, used: set[PathT] | None) -> bool:
        key = sec.key
        if key in self.hidden_secs:
            return True
        if key in self.shown_secs or self.mode == MODE_ALL or used is None:
            return False
        return not section_used(sec, used)

    def node_hidden(self, path: PathT, used: set[PathT] | None) -> bool:
        """Hidden by itself (a hidden group hides its children too; the UI walks the parents)."""
        if path in self.hidden_nodes:
            return True
        if path in self.shown_nodes or self.mode == MODE_ALL or used is None:
            return False
        return path not in used

    def toggle_node(self, path: PathT, used: set[PathT] | None) -> None:
        if self.node_hidden(path, used):
            self.hidden_nodes.discard(path)
            if self.node_hidden(path, used):
                self.shown_nodes.add(path)
        else:
            self.shown_nodes.discard(path)
            if not self.node_hidden(path, used):
                self.hidden_nodes.add(path)

    def toggle_section(self, sec, used: set[PathT] | None) -> None:
        if self.section_hidden(sec, used):
            self.hidden_secs.discard(sec.key)
            if self.section_hidden(sec, used):
                self.shown_secs.add(sec.key)
        else:
            self.shown_secs.discard(sec.key)
            if not self.section_hidden(sec, used):
                self.hidden_secs.add(sec.key)

    def show_mine(self) -> None:
        self.mode = MODE_MINE
        self._clear_visibility()

    def show_all(self) -> None:
        self.mode = MODE_ALL
        self._clear_visibility()

    def _clear_visibility(self) -> None:
        self.hidden_secs.clear()
        self.shown_secs.clear()
        self.hidden_nodes.clear()
        self.shown_nodes.clear()
        for c in self.cats:
            c.hidden = False
            for p in c.pages:
                p.hidden = False

    # -- persistence --------------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "version": 1, "mode": self.mode, "flat": self.flat,
            "cats": [{"id": c.id, "label": c.label, "hidden": c.hidden,
                      "pages": [{"id": p.id, "label": p.label, "hidden": p.hidden, "sections": list(p.sections)}
                                for p in c.pages]} for c in self.cats],
            "hidden_secs": sorted(self.hidden_secs), "shown_secs": sorted(self.shown_secs),
            "hidden_nodes": [list(p) for p in sorted(self.hidden_nodes, key=repr)],
            "shown_nodes": [list(p) for p in sorted(self.shown_nodes, key=repr)],
            "pins": {k: [list(p) for p in v] for k, v in self.pins.items()},
        }

    @classmethod
    def from_json(cls, raw: Any) -> "Layout | None":
        if not isinstance(raw, dict) or not isinstance(raw.get("cats"), list) or not raw["cats"]:
            return None
        try:
            cats = [Category(str(c["id"]), str(c["label"]),
                             [Page(str(p["id"]), str(p["label"]), [str(s) for s in p.get("sections") or []],
                                   bool(p.get("hidden"))) for p in c.get("pages") or []],
                             bool(c.get("hidden"))) for c in raw["cats"]]
            return cls(
                cats, raw.get("mode") if raw.get("mode") in (MODE_MINE, MODE_ALL) else MODE_MINE,
                bool(raw.get("flat")),
                set(map(str, raw.get("hidden_secs") or [])), set(map(str, raw.get("shown_secs") or [])),
                {tuple(p) for p in raw.get("hidden_nodes") or [] if isinstance(p, list) and p},
                {tuple(p) for p in raw.get("shown_nodes") or [] if isinstance(p, list) and p},
                {str(k): [tuple(p) for p in v if isinstance(p, list) and p]
                 for k, v in (raw.get("pins") or {}).items() if isinstance(v, list)},
            )
        except (KeyError, TypeError, ValueError):
            return None


def default_layout(model: SettingsModel) -> Layout:
    cats = []
    for cid, label, keys in model.categories:
        placed: set[str] = set()
        pages = []
        for plabel, pkeys in PAGE_PLAN.get(cid, []):
            pkeys = [k for k in pkeys if k in keys]
            placed.update(pkeys)
            if pkeys:
                pages.append(Page(f"{cid}/{plabel}", plabel, pkeys))
        rest = [k for k in keys if k not in placed]
        if rest:
            other = next((p for p in pages if p.label == "Other"), None)
            if other is None:
                pages.append(Page(f"{cid}/Other", "Other", rest))
            else:
                other.sections.extend(rest)
        cats.append(Category(cid, label, pages))
    return Layout(cats, pins=copy.deepcopy(DEFAULT_PINS))


def hermes_order_layout(model: SettingsModel, current: Layout) -> Layout:
    """One page per section: Hermes' dashboard menu first, then the rest alphabetically."""
    first = [k for k in HERMES_MENU if k in model.sections]
    rest = sorted(k for k in model.sections if k not in first)
    pages = [Page(f"flat/{k}", k, [k]) for k in first + rest]
    out = Layout([Category("flat", "Hermes dashboard order", pages)], current.mode, True,
                 set(current.hidden_secs), set(current.shown_secs), set(current.hidden_nodes),
                 set(current.shown_nodes), {k: list(v) for k, v in current.pins.items()})
    return out


def reconcile(layout: Layout, model: SettingsModel) -> Layout:
    """Fit a saved layout to the sections that exist now (no duplicates, nothing lost)."""
    seen: set[str] = set()
    for c in layout.cats:
        c.pages = [p for p in c.pages if p.id]
        for p in c.pages:
            p.sections = [k for k in p.sections if k in model.sections and not (k in seen or seen.add(k))]
    missing = [k for k in model.sections if k not in seen]
    for key in missing:
        cid = model.default_category(key)
        cat = next((c for c in layout.cats if c.id == cid), None) or layout.cats[-1]
        page = next((p for p in cat.pages if p.label == "Other"), None)
        if page is None:
            page = Page(f"{cat.id}/Other", "Other", [])
            cat.pages.append(page)
        page.sections.append(key)
    return layout
