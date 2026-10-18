"""The hermes-teammates roster: Teammate | Description | Route | Model | Reasoning | Toolsets | Instructions | ×.

It shows the roster the page's agent *uses* (services.roster_edit.table_for): the master for the main agent, a
profile's own roster file, or - read-only - the master a profile falls back to, or a roster kept in config.yaml.
Like the chain tables it is a working copy: edits stay on the page until Stage puts exactly the edited cells into
the rosters of the checked agents; an amber dot marks a cell where the checked agents differ (click: who has what).
``max_live_runs`` sits above the table: it is each agent's own config.yaml setting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget)

from ..core import roster_rules as R
from ..services import roster as RM
from ..services import roster_edit as E
from ..services.work import REMOVE
from .chain_table import Cell, DiffDot, GridBox, ModelCell
from .theme import T, mono
from .widgets import (Popover, agent_names, btn, clear_layout, IconButton, label, link, plural, set_only_link,
                      tip_html)

HEADS = [("Teammate", "The teammate's name: agents give it to teammate_assign. Lowercase letters, digits, '-' "
                      "and '_'; renaming keeps its place in the file."),
         ("Description", "Shown to the agent word for word: how it picks this teammate (up to 300 characters)."),
         ("Route", "A configured Hermes provider for the teammate's model. Set route and model together, or "
                   "neither to use the agent's own (or its delegation settings)."),
         ("Model", "The model id, exactly as Hermes takes it. Click to pick another one."),
         ("Reasoning", "reasoning_effort for the teammate. Stored now; applied where the Hermes build supports it "
                       "(/teammates lists it as unsupported otherwise). 'not set': the default."),
         ("Toolsets", "Narrows the agent's toolsets for this teammate: 1-24 names, comma-separated. Empty: "
                      "inherit the agent's."),
         ("Instructions", "The teammate's standing brief (up to 4,000 characters). Click to edit."),
         ("", "Remove the teammate (click again to keep it).")]
COLS = ["name", "description", "route", "model", "reasoning_effort", "toolsets", "instructions"]
RUNTIME = ("The plugin reads the roster on every call: edits apply to the next assignment, no restart. A new "
           "teammate's name shows in the teammate_assign tool after a gateway restart (Review › Restart gateways). "
           "/teammates in a chat shows 'Roster from: …' and any problems.")


@dataclass
class Row:
    orig: str                    # the name in the shown roster (for added teammates: their current name)
    name: str                    # the name after the page's rename
    fields: dict = field(default_factory=dict)
    added: bool = False
    removed: bool = False
    elsewhere: bool = False      # not in the shown roster: only other checked agents have it
    holders: list[str] = field(default_factory=list)    # checked agents whose roster has it (several checked)


def shown_rows(view: E.TableView, work: E.RosterWork, tables: dict | None = None) -> list[Row]:
    """The shown roster's teammates with the page's edits applied; with ``tables`` (several agents checked) also
    the teammates only other checked agents have, after them."""
    tables = tables or {}

    def holders(name: str) -> list[str]:
        return [a for a, t in tables.items() if name in t.teammates]

    out = []
    for name, t in view.teammates.items():
        out.append(Row(name, work.new_name(name), work.fields_of(name, t if isinstance(t, dict) else {}),
                       removed=name in work.removed, holders=holders(name)))
    seen = set(view.teammates) | set(work.added)
    for tv in tables.values():
        for name, t in tv.teammates.items():
            if name in seen:
                continue
            seen.add(name)
            out.append(Row(name, work.new_name(name), work.fields_of(name, t if isinstance(t, dict) else {}),
                           removed=name in work.removed, elsewhere=True, holders=holders(name)))
    for name in work.added:
        out.append(Row(name, name, work.fields_of(name), added=True))
    return out


def shown_table(view: E.TableView, work: E.RosterWork) -> dict:
    """The shown roster as it would be with the page's edits (to check it with the plugin's rules)."""
    data = {k: v for k, v in (view.data or {}).items() if k != R.TOP}
    data[R.TOP] = {r.name: r.fields for r in shown_rows(view, work) if not r.removed and not r.elsewhere}
    return data


def value_text(value: Any) -> str:
    """A value in 'who has what': None = no such teammate, '' = not set."""
    if value is None:
        return "no such teammate"
    if value == "":
        return "not set"
    text = str(value)
    return text if len(text) <= 120 else text[:117] + "…"


def toolsets_of(text: str) -> Any:
    """The Toolsets box: comma- or space-separated names; empty = REMOVE (inherit)."""
    names = [t for t in text.replace(",", " ").split() if t]
    return names or REMOVE


class InstructionsDialog(QDialog):
    """Edit a teammate's instructions (a multi-line brief)."""

    def __init__(self, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.resize(640, 420)
        self.setWindowTitle(title)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 12)
        v.setSpacing(10)
        t = QLabel(title)
        t.setFont(mono(13.5, 600))
        v.addWidget(t)
        v.addWidget(label("The teammate's standing brief, sent with every assignment. Up to "
                          f"{R.TEXTS['instructions']:,} characters; longer text is clipped by the plugin.",
                          tone="muted", wrap=True))
        self.ed = QPlainTextEdit()
        self.ed.setPlainText(text)
        v.addWidget(self.ed, 1)
        self.count = label("", tone="faint", small=True)
        v.addWidget(self.count)
        self.ed.textChanged.connect(self._count)
        self._count()
        f = QHBoxLayout()
        f.addStretch(1)
        c = btn("Cancel", tip="Close without changing anything")
        c.clicked.connect(self.reject)
        ok = btn("Use this text", pri=True, tip="Puts this text in the row as your edit. Stage it afterwards.")
        ok.clicked.connect(self.accept)
        f.addWidget(c)
        f.addWidget(ok)
        v.addLayout(f)

    def _count(self) -> None:
        n, limit = len(self.ed.toPlainText()), R.TEXTS["instructions"]
        self.count.setText(f"{n:,} / {limit:,} characters" + (" - will be clipped" if n > limit else ""))

    @property
    def text(self) -> str:
        return self.ed.toPlainText().strip()


class RosterTable(QWidget):
    key = "roster"

    def __init__(self, page, ctx) -> None:
        super().__init__()
        self.page, self.ctx, self.state = page, ctx, ctx.state
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(0)
        self.view: E.TableView | None = None
        self._tables: dict = {}
        self.build()

    @property
    def work(self) -> E.RosterWork:
        return self.state.roster_work

    def refresh(self) -> None:
        self.build()

    # ------------------------------------------------------------------ building
    def build(self) -> None:
        clear_layout(self.v)
        st = self.state
        s = st.session
        keys = st.scope().usable_keys
        ref = st.ref_key()
        if not E.has_rosters(s):
            self._note(f"No host uses the hermes-teammates plugin (no {RM.FILE} and no plugin settings in any "
                       "config.yaml).")
            return
        if not ref:
            self._note("Check agents to see the teammates roster each one uses.")
            return
        view = self.view = E.table_for(s, ref)
        self._tables = E.tables_for(s, keys) if len(keys) > 1 else {}
        self.v.addWidget(self._header(view, keys))
        self.v.addWidget(self._knobs(view))
        if view.data is not None:
            self.v.addWidget(self._grid(view))
        self.v.addWidget(self._footer(view, keys))

    def _note(self, text: str) -> None:
        n = label(text, tone="muted", wrap=True)
        n.setContentsMargins(12, 12, 12, 12)
        self.v.addWidget(n)

    def _header_text(self, view: E.TableView) -> str:
        s = self.state.session
        name = self.state.ref_name()
        if view.kind == E.MASTER:
            users = E.users_of_master(s, s.agents[view.agent].host)
            return f"{name}: the master roster · used by {plural(len(users), 'profile')}"
        if view.kind == E.OWN:
            text = f"{name}: its own roster file" + {
                "create": " (staged: created)", "replace": " (staged: replaced)",
                "remove": " (staged: removed; it will use the main agent's roster)"}.get(view.pending, "")
            if view.falls_back:
                text += (". It has no teammate yet, so with roster_source fallback it uses the main agent's roster "
                         "until it has one")
            return text
        if view.kind == E.BROKEN:
            return f"{name}'s roster file is not usable: {view.error}. Replace it with a working roster, or remove it."
        if view.kind == E.INHERITED:
            return f"{name}: {view.note} (read-only here)." + (f" {view.error}." if view.error else "") + \
                ("" if view.source == "root" else " Replace roster gives it a roster file of its own.")
        if view.kind == E.CONFIG:
            return (f"{name}: {view.note} Read-only here; Replace roster gives it a roster file "
                    "(and sets its roster_file).")
        if view.kind == E.OUTSIDE:
            return f"{name}: {view.error}; not edited here."
        return f"No teammates roster for {name} (no hermes-teammates settings in its config.yaml)."

    def _header(self, view: E.TableView, keys: list[str]) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 6, 6, 6)
        h.setSpacing(10)
        name = self.state.ref_name()
        t = label(self._header_text(view), tone="muted", wrap=True)
        t.setToolTip(view.path or "")
        h.addWidget(t, 1)
        targets = keys or [view.agent]          # the actions act on the checked agents (the page's agent if none)
        rb = btn("Replace roster", sm=True, tip="Give the checked profiles a copy of a roster file, exactly as it is "
                                                  "on the host (comments included). Creates teammates.yaml when a "
                                                  "profile has none and sets its roster_file. Goes to Review.")
        rb.setEnabled(self._can_replace(targets))
        menu = QMenu(rb)
        menu.addAction("With the master roster", lambda: self._replace(None))
        if self._own_source(view):
            menu.addAction(f"With {name}'s roster", lambda: self._replace(view.agent))
        rb.setMenu(menu)
        h.addWidget(rb)
        dl = btn("Remove own file", sm=True, tip="Remove the checked profiles' own roster files, so they use the main "
                                                 "agent's roster (roster_source fallback). The file is kept in "
                                                 f"config-backups as {RM.FILE}.removed-<time>. Goes to Review.")
        dl.setEnabled(self._can_remove(targets))
        dl.clicked.connect(self._remove_own)
        h.addWidget(dl)
        return w

    def _values(self, name: str, f: str | None) -> dict | None:
        """Value -> checked agents for a teammate cell (``f``) or max_live_runs (``f`` None); None when they agree."""
        if not self._tables:
            return None
        values = E.who_has(self.state.session, list(self._tables), name, f, self._tables)
        return values if len(values) > 1 else None

    def _dot(self, name: str, f: str | None) -> DiffDot | None:
        values = self._values(name, f)
        if values is None:
            return None
        what = name if f is None else f"{name} · {f}"
        d = DiffDot(f"{len(values)} values across the checked agents. Click to see who has what.")
        d.clicked.connect(lambda: self._show_values(d, values, what, name, f))
        return d

    def _use(self, name: str, f: str | None, value: Any) -> None:
        """'use this' in the who-has-what popup: that value becomes the page's edit."""
        if f is None:
            self._set_knob(REMOVE if value == "" else value)
            return
        if f == "toolsets" and isinstance(value, str):
            value = toolsets_of(value)
        row = next((r for r in shown_rows(self.view, self.work, self._tables) if r.orig == name), None)
        if row is not None:
            self._set_cell(row, f, REMOVE if value == "" else value)

    def _show_values(self, anchor, values: dict, what: str, name: str = "", f: str | None = None) -> None:
        pop = Popover(self)
        v = QVBoxLayout(pop)
        v.setContentsMargins(0, 6, 0, 6)
        t = label(f"teammates · {what} · what the checked agents have", tone="muted", small=True)
        t.setContentsMargins(12, 2, 12, 6)
        v.addWidget(t)
        grid = QGridLayout()
        grid.setContentsMargins(12, 0, 12, 4)
        grid.setHorizontalSpacing(16)
        can_use = self.view is not None and (self.view.editable if f is not None else self._knob_editable())
        for r, (value, keys) in enumerate(sorted(values.items(), key=lambda kv: -len(kv[1]))):
            lab = label(value_text(value), tone="faint" if value in (None, "") else None)
            if value not in (None, ""):
                lab.setFont(mono())
            lab.setToolTip(str(value) if value not in (None, "") else "")
            grid.addWidget(lab, r, 0, Qt.AlignTop)
            names = label(", ".join(agent_names(self.state, keys)), wrap=True)
            names.setMaximumWidth(360)
            grid.addWidget(names, r, 1, Qt.AlignTop)
            sel = link("select these", tip="Check exactly these agents")
            sel.clicked.connect(lambda _=False, ks=list(keys): (pop.close(), self.page.check_exactly(ks)))
            grid.addWidget(sel, r, 2, Qt.AlignTop)
            if value is not None and name and can_use:
                use = link("use this", tip="Make this value your edit on the page (stage it afterwards)")
                use.clicked.connect(lambda _=False, val=value: (pop.close(), self._use(name, f, val)))
                grid.addWidget(use, r, 3, Qt.AlignTop)
        v.addLayout(grid)
        pop.show_at(anchor)

    def _knob_editable(self) -> bool:
        return self.view is not None and E.knob_value(self.state.session, self.view.agent) is not None

    def _knob_shown(self) -> Any:
        """max_live_runs as the page shows it (the edit, else the agent's value; "" = not set)."""
        if E.KNOB in self.work.knobs:
            v = self.work.knobs[E.KNOB]
            return "" if v is REMOVE else v
        v = E.knob_value(self.state.session, self.view.agent) if self.view is not None else None
        return "" if v is None else v

    def _knobs(self, view: E.TableView) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 0, 6, 6)
        h.setSpacing(8)
        h.addWidget(label(E.KNOB, tone="muted"))
        value = self._knob_shown()
        le = QLineEdit("" if value == "" else str(value))
        le.setFont(mono())
        le.setValidator(QIntValidator(*E.KNOB_RANGE, le))
        le.setPlaceholderText(f"not set ({E.KNOB_DEFAULT})")
        le.setMaximumWidth(110)
        le.setToolTip(f"Most teammate runs one conversation may have in flight ({E.KNOB_RANGE[0]}-{E.KNOB_RANGE[1]}; "
                      f"not set: {E.KNOB_DEFAULT}). A setting of each agent's config.yaml, not of the roster: "
                      "staging it changes the checked agents' config.yaml.")
        le.setEnabled(self._knob_editable())
        le.setContextMenuPolicy(Qt.NoContextMenu)          # right-click: the table's menu
        le.editingFinished.connect(lambda le=le: self._set_knob(int(le.text()) if le.text() else REMOVE))
        h.addWidget(Cell(le, E.KNOB in self.work.knobs, self._dot(E.KNOB, None), at=(None, E.KNOB)))
        h.addWidget(label("per conversation · in config.yaml", tone="faint", small=True))
        h.addStretch(1)
        return w

    def _grid(self, view: E.TableView) -> QWidget:
        st = self.state
        box = GridBox()
        g = box.grid
        g.setColumnMinimumWidth(7, 26)
        for c, stretch in ((0, 2), (1, 4), (2, 2), (3, 3), (4, 2), (5, 2), (6, 2)):
            g.setColumnStretch(c, stretch)          # every column may shrink: the page fits half a 1920 screen
        for c, (text, tip) in enumerate(HEADS):
            hl = label(text, tone="muted", small=True)
            hl.setToolTip(tip)
            hl.setContentsMargins(8, 0, 8, 0)
            hl.setFixedHeight(27)
            hl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)     # a narrow column clips its heading
            g.addWidget(hl, 0, c)
        if not view.editable:
            return self._grid_readonly(view, box)
        provs = sorted(set(st.catalog.providers()) | {str(t.get("route")) for t in view.teammates.values()
                                                       if isinstance(t, dict) and t.get("route")}, key=str.casefold)
        efforts = list(st.model.efforts)
        n_checked = len(self._tables)
        for i, row in enumerate(shown_rows(view, self.work, self._tables), start=1):
            live = not row.removed
            edited = {f for (n, f) in self.work.cells if n == row.orig}
            problems = [] if row.removed else ([R.bad_name(row.name)] if R.bad_name(row.name) else
                                               R.teammate_problems(row.fields))
            name = QLineEdit(row.name)
            name.setFont(mono())
            name.setEnabled(live)
            tips = [f"teammates › {row.name}"] + (["Renamed from " + row.orig] if row.name != row.orig else []) + \
                (["The plugin cannot use it as it is: " + "; ".join(problems)] if problems else [])
            name.setToolTip(tip_html(tips))
            if problems or row.removed:
                f_ = name.font()
                f_.setStrikeOut(row.removed)
                name.setFont(f_)
                name.setStyleSheet(f"color: {T['bad' if problems else 'faint']};")
            name.editingFinished.connect(lambda le=name, r=row: self._rename(r, le.text().strip()))
            name_box = name
            if n_checked and not row.added and len(row.holders) < n_checked:
                name_box = QWidget()
                nh = QHBoxLayout(name_box)
                nh.setContentsMargins(0, 0, 4, 0)
                nh.setSpacing(4)
                nh.addWidget(name, 1)
                badge = label(f"in {len(row.holders)} of {n_checked}", tone="faint", small=True)
                lacking = [a for a in self._tables if a not in row.holders]
                badge.setToolTip("Only some checked agents' rosters have this teammate. Without it: "
                                 + ", ".join(agent_names(st, lacking)) + ". Staging an edit adds it to them only "
                                 "when the teammate then has a description.")
                nh.addWidget(badge)
            g.addWidget(Cell(name_box, row.added or row.name != row.orig, at=(row.orig, "name")), i, 0)
            desc = QLineEdit(str(row.fields.get("description", "")))
            desc.setEnabled(live)
            desc.setPlaceholderText("how the agent picks it")
            desc.setToolTip(str(row.fields.get("description", "")))
            desc.editingFinished.connect(lambda le=desc, r=row: self._set_cell(r, "description", le.text().strip()))
            g.addWidget(Cell(desc, "description" in edited, self._dot(row.orig, "description"),
                             at=(row.orig, "description")), i, 1)
            pc = _combo()
            cur = row.fields.get("route")
            items = ["not set"] + [p for p in provs if p != cur] if R.unset(cur) else \
                [str(cur)] + [p for p in provs if p != cur] + ["not set"]
            pc.addItems(items)
            pc.setCurrentIndex(0)
            pc.setEnabled(live)
            pc.wheelEvent = lambda ev: ev.ignore()
            pc.activated.connect(lambda _i, cb=pc, r=row: self._pick_route(r, cb.currentText()))
            g.addWidget(Cell(pc, "route" in edited, self._dot(row.orig, "route"), at=(row.orig, "route")), i, 2)
            model = row.fields.get("model")
            mc = ModelCell(str(model) if not R.unset(model) else "not set", placeholder=R.unset(model))
            mc.setEnabled(live)
            if live:
                mc.clicked.connect(lambda r=row: self._pick(r, str(r.fields.get("route") or "")))
            g.addWidget(Cell(mc, "model" in edited, self._dot(row.orig, "model"), at=(row.orig, "model")), i, 3)
            rc = _combo()
            level = row.fields.get("reasoning_effort")
            items = ["not set"] + efforts + ([str(level)] if not R.unset(level) and str(level) not in efforts else [])
            rc.addItems(items)
            rc.setItemData(0, "Leave reasoning_effort out: the default level", Qt.ToolTipRole)
            rc.setCurrentIndex(items.index(str(level)) if not R.unset(level) else 0)
            rc.setEnabled(live)
            rc.wheelEvent = lambda ev: ev.ignore()
            rc.activated.connect(lambda _i, cb=rc, r=row: self._set_cell(
                r, "reasoning_effort", REMOVE if cb.currentIndex() == 0 else cb.currentText()))
            g.addWidget(Cell(rc, "reasoning_effort" in edited, self._dot(row.orig, "reasoning_effort"),
                             at=(row.orig, "reasoning_effort")), i, 4)
            ts = row.fields.get("toolsets")
            tl = QLineEdit(", ".join(str(x) for x in ts) if isinstance(ts, list) else "" if ts is None else str(ts))
            tl.setFont(mono())
            tl.setPlaceholderText("inherit")
            tl.setEnabled(live)
            tl.setToolTip(HEADS[5][1])
            tl.editingFinished.connect(lambda le=tl, r=row: self._set_cell(r, "toolsets", toolsets_of(le.text())))
            g.addWidget(Cell(tl, "toolsets" in edited, self._dot(row.orig, "toolsets"), at=(row.orig, "toolsets")),
                        i, 5)
            ins = str(row.fields.get("instructions") or "")
            ic = ModelCell(_preview(ins) if ins else "not set", placeholder=not ins)
            ic.setToolTip(ins[:600] + ("…" if len(ins) > 600 else "") if ins else "No instructions. Click to write some.")
            ic.setEnabled(live)
            if live:
                ic.clicked.connect(lambda r=row: self._edit_instructions(r))
            g.addWidget(Cell(ic, "instructions" in edited, self._dot(row.orig, "instructions"),
                             at=(row.orig, "instructions")), i, 6)
            for le in (name, desc, tl):
                le.setContextMenuPolicy(Qt.NoContextMenu)      # right-click: the table's menu
                le.setMinimumWidth(40)
                le.setCursorPosition(0)                         # a narrow box shows the start of its text
            x = IconButton("undo" if row.removed else "x", "Keep this teammate" if row.removed else
                           "Remove this teammate (from the checked agents' rosters, when you stage)",
                           size=18, icon_size=12)
            x.clicked.connect(lambda _=False, r=row: self._toggle_remove(r))
            g.addWidget(x, i, 7)
        return box

    def _grid_readonly(self, view: E.TableView, box: GridBox) -> QWidget:
        """The roster of an agent that cannot edit it here: text only."""
        g = box.grid
        for i, (name, t) in enumerate(view.teammates.items(), start=1):
            t = t if isinstance(t, dict) else {}
            for c, f in enumerate(COLS):
                value = name if f == "name" else t.get(f)
                if isinstance(value, list):
                    value = ", ".join(str(x) for x in value)
                text = "not set" if value in (None, "") else (_preview(str(value)) if f == "instructions"
                                                              else str(value))
                lab = label(text, tone="faint" if value in (None, "") else None, is_mono=c in (0, 2, 3, 5))
                lab.setContentsMargins(8, 0, 8, 0)
                lab.setMinimumHeight(31)
                lab.setToolTip("" if value in (None, "") else str(value)[:600])
                g.addWidget(Cell(lab, False, self._dot(name, f) if c else None, at=(name, f)), i, c)
        return box

    def _footer(self, view: E.TableView, keys: list[str]) -> QWidget:
        st = self.state
        f = QWidget()
        v = QVBoxLayout(f)
        v.setContentsMargins(10, 4, 6, 4)
        v.setSpacing(2)
        h = QHBoxLayout()
        h.setSpacing(8)
        if view.editable:
            add = link("+ Add teammate", tip="Add a teammate; give it a name and a description (and route + model "
                                             "if it should not use the agent's), then stage it")
            add.clicked.connect(self._add_teammate)
            h.addWidget(add)
        work = self.work
        dirty = not work.empty
        staged = len(self._staged_docs())
        if dirty:
            t = label("edited, not staged", tone="accent", small=True)
            t.setToolTip("Your edits are only on this page so far. Stage them to put them in Review.")
            h.addWidget(t)
            u = IconButton("undo", "Discard your edits")
            u.clicked.connect(self.discard)
            h.addWidget(u)
        elif staged:
            t = label(f"staged · {plural(staged, 'roster')}", tone="amber", small=True)
            t.setToolTip("Staged and listed in Review. Nothing is written yet.")
            h.addWidget(t)
            u = IconButton("undo", "Unstage the rosters of the checked agents")
            u.clicked.connect(self.unstage)
            h.addWidget(u)
        h.addStretch(1)
        probs = self._problems()
        sb = btn("Stage", sm=True, pri=True, tip="Stages the cells you edited into the rosters of the agents checked "
                                                 "right now (the master for a checked main agent), and max_live_runs "
                                                 "into their config.yaml. Profiles that use the master are skipped. "
                                                 "Nothing is written yet.")
        sb.setEnabled(dirty and bool(keys) and not probs)
        sb.clicked.connect(lambda: self.stage())
        h.addWidget(sb)
        h.addWidget(label(f"for {plural(len(keys), 'agent')}" if keys else "select agents", tone="muted"))
        if dirty:
            one = link("")
            set_only_link(one, st, len(keys))
            one.clicked.connect(lambda: self.stage(only=True))
            h.addWidget(one)
        v.addLayout(h)
        for text in probs[:3]:
            p = label(text, wrap=True)
            p.setStyleSheet(f"color: {T['bad']};")
            v.addWidget(p)
        v.addWidget(label(RUNTIME, tone="faint", small=True, wrap=True))
        return f

    def _problems(self) -> list[str]:
        """What keeps Stage off: the page's own checks, then the plugin's rules on the shown roster."""
        view = self.view
        if view is None:
            return []
        out = self.work.problems(view.teammates)
        if view.editable and self.work.roster_edits and not out:
            why, _warn = R.review(shown_table(view, self.work), view.data)
            if why:
                out.append(f"Not usable: {why}")
        return out

    # ------------------------------------------------------------------ edits
    def _changed(self) -> None:
        self.state.work_changed.emit()
        self.build()

    def _put(self, row: Row, f: str, value: Any) -> None:
        """An edit of one cell; setting a cell back to the roster's value drops the edit."""
        if value == "" or value == []:
            value = REMOVE
        orig = REMOVE if row.added else (self.view.teammates.get(row.orig) or {}).get(f, REMOVE)
        if value == orig and not row.added:
            self.work.cells.pop((row.orig, f), None)
        else:
            self.work.cells[(row.orig, f)] = value

    def _set_cell(self, row: Row, f: str, value: Any) -> None:
        self._put(row, f, value)
        self._changed()

    def _set_knob(self, value: Any) -> None:
        orig = E.knob_value(self.state.session, self.view.agent)
        orig = REMOVE if orig in (None, "") else orig
        if value == orig or (value is not REMOVE and orig is not REMOVE and str(value) == str(orig)):
            self.work.knobs.pop(E.KNOB, None)
        else:
            self.work.knobs[E.KNOB] = value
        self._changed()

    def _rename(self, row: Row, new: str) -> None:
        w = self.work
        if not new or new == row.name:
            self.build()
            return
        rows = shown_rows(self.view, w, self._tables)
        why = R.bad_name(new)
        if why is None and new in {r.name for r in rows if r is not row}:
            why = f"there is already a teammate '{new}'"
        if why is None and row.added and (new in self.view.teammates or new in w.renames):
            why = f"'{new}' is still the name of a teammate being renamed: stage that rename first"
        if why:
            self.state.say(f"Not renamed: {why}.")
            self.build()
            return
        if row.added:
            w.added[w.added.index(row.orig)] = new
            w.cells = {((new if n == row.orig else n), f): v for (n, f), v in w.cells.items()}
        elif new == row.orig:
            w.renames.pop(row.orig, None)
        else:
            w.renames[row.orig] = new
        self._changed()

    def _toggle_remove(self, row: Row) -> None:
        w = self.work
        if row.added:
            w.added.remove(row.orig)
            w.cells = {k: v for k, v in w.cells.items() if k[0] != row.orig}
        elif row.orig in w.removed:
            w.removed.discard(row.orig)
        else:
            w.removed.add(row.orig)
        self._changed()

    def _add_teammate(self) -> None:
        names = {r.name for r in shown_rows(self.view, self.work, self._tables)} | set(self.view.teammates) | \
            set(self.work.renames)
        n = 1
        while f"new-teammate-{n}" in names:
            n += 1
        self.work.add_teammate(f"new-teammate-{n}")
        self._changed()

    def _pick_route(self, row: Row, route: str) -> None:
        """The Route box: 'not set' clears route and model; another route opens the model picker on it."""
        if route == "not set":
            self._put(row, "route", REMOVE)
            self._set_cell(row, "model", REMOVE)
            return
        self._pick(row, route)

    def _pick(self, row: Row, route: str) -> None:
        from .dialogs import pick_model

        cur = (str(row.fields.get("route") or ""), str(row.fields.get("model") or ""))
        picked = pick_model(self.page, self.state, f"Teammate {row.name} · model", route,
                            cur if cur[0] == route and cur[1] else None)
        if picked is None:
            self.build()
            return
        self._put(row, "route", picked[0])
        self._set_cell(row, "model", picked[1])

    def _edit_instructions(self, row: Row) -> None:
        dlg = InstructionsDialog(f"teammates › {row.name} › instructions", str(row.fields.get("instructions") or ""),
                                 self)
        if dlg.exec() == QDialog.Accepted:
            self._set_cell(row, "instructions", dlg.text)

    def discard(self) -> None:
        self.state.roster_work = E.RosterWork()
        self._changed()

    def stage(self, only: bool = False) -> None:
        st = self.state
        agents = [st.ref_key()] if only else st.scope().usable_keys
        rep = E.stage_roster(st.session, self.work, [a for a in agents if a])
        if rep.changed:
            st.roster_work = E.RosterWork()
        st.say(report_text(st, rep, "Staged for"))
        self._changed()

    def _replace(self, source: str | None) -> None:
        st = self.state
        keys = st.scope().usable_keys or [st.ref_key()]
        rep = E.replace_ops(st.session, [k for k in keys if k], source)
        st.say(report_text(st, rep, "Roster replaced (staged) for"))
        self._changed()

    def _remove_own(self) -> None:
        st = self.state
        keys = st.scope().usable_keys or [st.ref_key()]
        rep = E.delete_ops(st.session, [k for k in keys if k])
        st.say(report_text(st, rep, "Removal staged for"))
        self._changed()

    def _staged_docs(self) -> list[str]:
        """The checked agents' staged roster files, and their configs when the page's edits are staged there."""
        drafts = self.state.session.drafts
        out = []
        for k in self.state.scope().usable_keys:
            if drafts.has_changes(RM.doc_key(k)):
                out.append(RM.doc_key(k))
            draft = drafts.draft(k)
            if draft is not None and any(op.path in (RM.MAX_LIVE_RUNS, RM.ROSTER_FILE) for op in draft.ops):
                out.append(k)
        return out

    def unstage(self) -> None:
        """Takes the checked agents' rosters (and the roster_file / max_live_runs staged with them) out of Review
        (Ctrl+Z brings them back)."""
        drafts = self.state.session.drafts
        docs = self._staged_docs()
        configs = [k for k in docs if not RM.is_doc(k)]
        drafts.discard([k for k in docs if RM.is_doc(k)])
        for path in (RM.MAX_LIVE_RUNS, RM.ROSTER_FILE):
            drafts.revert(configs, path)
        self.state.say(f"Unstaged {plural(len([k for k in docs if RM.is_doc(k)]), 'roster')}"
                       + (f" and the teammates settings of {plural(len(configs), 'config')}" if configs else "")
                       + ". Ctrl+Z brings them back.")
        self._changed()

    def _can_replace(self, targets: list[str]) -> bool:
        return any(not self.state.session.agents[k].is_main for k in targets if k in self.state.session.agents)

    def _can_remove(self, targets: list[str]) -> bool:
        return any(E.table_for(self.state.session, k).kind in (E.OWN, E.BROKEN) for k in targets)

    @staticmethod
    def _own_source(view: E.TableView) -> bool:
        """Whether the shown roster can be copied to the checked profiles (a profile's own file, on the host)."""
        return view.kind == E.OWN and view.pending != "create"

    # ------------------------------------------------------------------ right-click menu
    def contextMenuEvent(self, ev) -> None:
        w = self.childAt(ev.pos())
        while w is not None and w is not self and getattr(w, "at", None) is None:
            w = w.parentWidget()
        at = getattr(w, "at", None)
        self.context_menu(at, w if at is not None else None).exec(ev.globalPos())

    def context_menu(self, at: tuple[str | None, str] | None = None, anchor: QWidget | None = None) -> QMenu:
        """Everything you can do here (items that do not apply are greyed), like the chain tables' menu.

        ``at`` is the cell right-clicked: (teammate, field), or (None, "max_live_runs"). The menu then starts
        with that cell's and that teammate's actions.
        """
        st, view, w = self.state, self.view, self.work
        keys = st.scope().usable_keys
        ref = st.ref_name()
        m = QMenu(self)
        m.setToolTipsVisible(True)

        def add(text: str, tip: str, fn, enabled: bool = True) -> None:
            a = m.addAction(text)
            a.setToolTip(tip)
            a.setEnabled(enabled)
            a.triggered.connect(fn)

        ed = view is not None and view.editable
        if at is not None and view is not None:
            self._cell_actions(add, m, at, anchor, ed)
        dirty = not w.empty
        can_stage = dirty and bool(keys) and view is not None and not self._problems()
        add(f"Stage the roster for {plural(len(keys), 'agent')}" if keys else "Stage the roster",
            "Puts the cells you edited in the rosters of the agents checked now; their other cells stay as they are.",
            lambda: self.stage(), can_stage)
        add(f"Stage the roster for {ref} only" if ref else "Stage the roster for one agent only",
            "Puts your edits in Review only for the agent whose roster the page shows.",
            lambda: self.stage(only=True), can_stage and len(keys) > 1)
        m.addSeparator()
        add("Discard my roster edits", "Throws away your edits; the table shows the current roster again.",
            self.discard, dirty)
        add("Unstage", "Takes the rosters of the checked agents out of Review.",
            self.unstage, not dirty and bool(self._staged_docs()))
        m.addSeparator()
        targets = keys or ([view.agent] if view is not None else [])
        can_replace = view is not None and self._can_replace(targets)
        add("Replace roster with the master roster", "Gives the checked profiles a copy of the master, exactly as it "
            "is on the host, and sets their roster_file. Goes to Review.", lambda: self._replace(None), can_replace)
        if view is not None and self._own_source(view):
            add(f"Replace roster with {ref}'s roster", f"Gives the checked profiles a copy of {ref}'s roster file, "
                "exactly as it is on the host. Goes to Review.", lambda: self._replace(view.agent), can_replace)
        add("Remove own file", "Removes the checked profiles' own roster files, so they use the main agent's roster "
            f"(the file is kept in config-backups as {RM.FILE}.removed-<time>). Goes to Review.", self._remove_own,
            self._can_remove(targets))
        return m

    def _cell_actions(self, add, m: QMenu, at: tuple[str | None, str], anchor: QWidget | None, ed: bool) -> None:
        w = self.work
        name, f = at
        if name is None:                                    # max_live_runs
            value = self._knob_shown()
            ked = self._knob_editable()
            add(f"Not set (no {f})", f"Leaves {f} out of config.yaml: the plugin's default ({E.KNOB_DEFAULT}). Stage "
                "it afterwards.", lambda: self._set_knob(REMOVE), ked and value != "")
            add("Discard my edit", "Throws away your edit; the box shows the agent's value again.",
                lambda: (w.knobs.pop(f, None), self._changed()), ked and f in w.knobs)
            values = self._values(f, None)
            add("Show who has which value", "Each value and the checked agents that have it.",
                lambda: self._show_values(anchor or self, values, f, f, None), values is not None)
            m.addSeparator()
            add("Copy value", "Copies the value shown.", lambda: QApplication.clipboard().setText(str(value)),
                value != "")
            add(f"Copy setting name ({f})", "Copies the key as config.yaml writes it.",
                lambda: QApplication.clipboard().setText(".".join(RM.MAX_LIVE_RUNS)))
            m.addSeparator()
            return
        row = next((r for r in shown_rows(self.view, w, self._tables) if r.orig == name), None)
        if row is None:
            return
        live = ed and not row.removed
        if f == "name":
            add(f"Undo the rename (back to {row.orig})", "Gives the teammate its name in the roster again.",
                lambda: self._rename(row, row.orig), live and not row.added and row.name != row.orig)
        else:
            if f in ("route", "model"):
                add("Pick another model…", "Opens the model picker for this teammate (route and model).",
                    lambda: self._pick(row, str(row.fields.get("route") or "")), live)
                add("Not set (no route and model)", "Leaves route and model out: the teammate uses the agent's own "
                    "model (or its delegation settings). Stage it afterwards.",
                    lambda: self._pick_route(row, "not set"),
                    live and not (R.unset(row.fields.get("route")) and R.unset(row.fields.get("model"))))
            if f == "instructions":
                add("Edit instructions…", "Opens the teammate's instructions.", lambda: self._edit_instructions(row),
                    live)
            if f in ("reasoning_effort", "toolsets", "instructions", "description"):
                add(f"Not set (no {f})", {"reasoning_effort": "Leaves reasoning_effort out: the default level.",
                                          "toolsets": "Leaves toolsets out: the teammate inherits the agent's.",
                                          "instructions": "Leaves instructions out: no standing brief.",
                                          "description": "Leaves the description out (the agent sees none)."}[f]
                    + " Stage it afterwards.", lambda: self._set_cell(row, f, REMOVE),
                    live and row.fields.get(f) not in (None, "", []))
            add("Discard my edit", "Throws away your edit of this cell; it shows the roster's value again.",
                lambda: (w.cells.pop((row.orig, f), None), self._changed()), live and (row.orig, f) in w.cells)
            values = self._values(row.orig, f)
            add("Show who has which value", "Each value and the checked agents that have it.",
                lambda: self._show_values(anchor or self, values, f"{row.orig} · {f}", row.orig, f),
                values is not None)
        if self._tables and row.holders:
            add(f"Check the agents that have {row.name} ({len(row.holders)})",
                "Checks exactly the agents whose roster has this teammate.",
                lambda: self.page.check_exactly(list(row.holders)), len(row.holders) < len(self._tables))
        m.addSeparator()
        value = row.name if f == "name" else E.shown(row.fields.get(f))
        add("Copy value", "Copies the value of this cell.",
            lambda: QApplication.clipboard().setText(str(value)), value not in (None, ""))
        add(f"Copy teammate name ({row.name})", "Copies the teammate's name, as agents give it to teammate_assign.",
            lambda: QApplication.clipboard().setText(row.name))
        m.addSeparator()
        add(f"Keep teammate {row.name}" if row.removed else f"Remove teammate {row.name}",
            "Takes back the removal." if row.removed else
            "Removes the teammate from the checked agents' rosters when you stage. Same as × at the end of the row.",
            lambda: self._toggle_remove(row), ed)
        add("Add teammate", "Adds a teammate at the end of the roster; give it a name and a description.",
            self._add_teammate, ed)
        m.addSeparator()


def _combo() -> QComboBox:
    cb = QComboBox()
    cb.setProperty("flat", True)
    cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    cb.setMinimumContentsLength(3)
    return cb


def _preview(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= 40 else line[:39] + "…"


def report_text(state, rep: E.StageReport, done: str) -> str:
    text = f"{done} {plural(rep.changed, 'file')}." if rep.changed else "Nothing staged."
    if rep.skipped:
        names = agent_names(state, [a for a, _ in rep.skipped])
        parts = [f"{n}: {why}" for n, (_a, why) in zip(names, rep.skipped)]
        text += " Skipped " + "; ".join(parts[:3]) + (f" (+{len(parts) - 3} more)" if len(parts) > 3 else "") + "."
    return text + (" Nothing is written until Write config in Review." if rep.changed else "")


__all__ = ["InstructionsDialog", "RosterTable", "Row", "report_text", "shown_rows", "shown_table", "toolsets_of"]
