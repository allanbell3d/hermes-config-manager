"""A page: its sections as boxed blocks (collapsed to name + counts), Related settings, the Stage bar.

Every edit made here is a *working value* until Stage; the bar at the bottom appears as soon as
the page has edits that are not staged.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMenu, QScrollArea, QVBoxLayout, QWidget)

from ..core.paths import MISSING, format_path
from ..core.settings_tree import leaves
from ..services import chains as C
from ..services.roster_edit import differ_count, table_for
from ..services.work import REMOVE, move_key_ops, rows_of, spec_for, unstage_chain
from .aux_panel import AuxPanel
from .chain_table import ChainTable, chain_title
from .roster_table import RosterTable
from .settings_table import Ctx, GroupRow, SettingsTable, gid, sum_html
from .theme import T, mono, sans
from .widgets import set_only_link, clear_layout, Chevron, Clickable, ElideLabel, IconButton, Switch, btn, differ, label, link, plural


class Block(QFrame):
    """A section (or Related settings): a bordered box with a clickable header."""

    def __init__(self, page, key: str, title: str, mono_title: bool, summary: str, opened: bool,
                 off: bool = False) -> None:
        super().__init__()
        self.page, self.key = page, key
        self.opened = opened
        v = QVBoxLayout(self)
        v.setContentsMargins(1, 1, 1, 1)
        v.setSpacing(0)
        self.head = Clickable()
        self.head.setMinimumHeight(36)
        h = QHBoxLayout(self.head)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(8)
        self.hl = h
        self.chev = Chevron(opened)
        h.addWidget(self.chev)
        self.eye_slot = h.count()
        t = QLabel(title)
        t.setFont(mono(13, 600) if mono_title else sans(13, 600))
        if off:
            f = t.font()
            f.setStrikeOut(True)
            t.setFont(f)
            t.setStyleSheet(f"color: {T['faint']};")
        h.addWidget(t)
        self.sum = QLabel(summary)
        self.sum.setTextFormat(Qt.RichText)
        self.sum.setStyleSheet(f"color: {T['muted']}; font-size: 12px;")
        self.sum.setWordWrap(page.narrow)
        h.addWidget(self.sum, 1)
        self.head.setToolTip(f"The {title}: section of config.yaml" + (" (hidden)" if off else "")
                             + ". Click to open or close it." if mono_title else title)
        v.addWidget(self.head)
        self.body = QWidget()
        self.bv = QVBoxLayout(self.body)
        self.bv.setContentsMargins(0, 0, 0, 0)
        self.bv.setSpacing(0)
        v.addWidget(self.body)
        self.body.setVisible(opened)
        self._hover = False
        self.head.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == ev.Type.Enter:
            self._hover = True
            self.update()
        elif ev.type() == ev.Type.Leave:
            self._hover = False
            self.update()
        return False

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(0, 0, -1, -1)
        p.setPen(QPen(T.c("line"), 1))
        p.setBrush(T.c("page"))
        p.drawRoundedRect(r, 6, 6)
        hr = self.head.geometry()
        p.setPen(Qt.NoPen)
        p.setBrush(T.c("accent_bg") if self._hover else T.c("head"))
        p.drawRoundedRect(hr.adjusted(0, 0, 0, 0 if not self.opened else 0), 5, 5)
        if self.opened:
            p.fillRect(hr.left(), hr.bottom() - 5, hr.width(), 6, T.c("accent_bg") if self._hover else T.c("head"))
            p.setPen(QPen(T.c("line"), 1))
            p.drawLine(hr.left(), hr.bottom() + 1, hr.right(), hr.bottom() + 1)


class PageView(QWidget):
    def __init__(self, state, page_id: str, window) -> None:
        super().__init__()
        self.state, self.page_id, self.main = state, page_id, window
        self.q = ""
        self.only_diff = False
        self.narrow = False
        self.aux_wide = False
        self.aux_task: str | None = None
        self.aux_checked: set[str] = set()
        self.open_details: set[str] = set()
        self.tables: list[SettingsTable] = []
        self.chains: list[ChainTable] = []
        self.aux: AuxPanel | None = None
        self.blocks: dict[str, Block] = {}
        self.stale = False
        self.stale_values = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self.content = QWidget()
        self.content.setProperty("surface", "bg")
        self.content.setAttribute(Qt.WA_StyledBackground, True)
        self.cv = QVBoxLayout(self.content)
        self.cv.setContentsMargins(24, 18, 24, 80)
        self.cv.setSpacing(0)
        self.scroll.setWidget(self.content)
        # header
        head = QHBoxLayout()
        head.setSpacing(14)
        self.h1 = QLabel("")
        self.h1.setProperty("h1", True)
        self.sub = label("", tone="muted")
        self.sub.setToolTip("Sections and settings on this page; 'differ' counts the settings where the checked "
                            "agents have different values")
        head.addWidget(self.h1)
        head.addWidget(self.sub, 1)
        self.cv.addLayout(head)
        tools = QHBoxLayout()
        tools.setContentsMargins(0, 12, 0, 4)
        tools.setSpacing(12)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter this page")
        self.filter.setToolTip("Show only the settings of this page whose key contains this text")
        self.filter.setClearButtonEnabled(True)
        self.filter.setMaximumWidth(230)
        self.filter.setMinimumWidth(90)
        self._ftimer = QTimer(self)
        self._ftimer.setSingleShot(True)
        self._ftimer.setInterval(120)
        self._ftimer.timeout.connect(self._apply_filter)
        self.filter.textChanged.connect(lambda _t: self._ftimer.start())
        tools.addWidget(self.filter, 1)
        self.od = Switch("Only what differs", "Only what differs")
        self.od.setToolTip("Show only the settings and chains where the checked agents do not all have the same "
                           "value")
        self.od.clicked.connect(self._toggle_od)
        tools.addWidget(self.od)
        tools.addStretch(1)
        ea = link("Expand all", tip="Open every section and group on this page")
        ea.clicked.connect(lambda: self.expand_all(True))
        ca = link("Collapse all", tip="Close every section on this page down to its name and counts")
        ca.clicked.connect(lambda: self.expand_all(False))
        tools.addWidget(ea)
        tools.addWidget(ca)
        self.cv.addLayout(tools)
        self.blocks_box = QWidget()
        self.bl = QVBoxLayout(self.blocks_box)
        self.bl.setContentsMargins(0, 0, 0, 0)
        self.bl.setSpacing(8)
        self.cv.addWidget(self.blocks_box)
        self.cv.addStretch(1)
        # the Stage bar floats over the bottom of the page
        self.bar = QFrame(self)
        self.bar.setObjectName("sbar")
        self.bar.setStyleSheet(f"QFrame#sbar {{ background: {T['accent_bg']}; border: 1px solid {T['accent']}; "
                               f"border-radius: 6px; }}")
        bh = QHBoxLayout(self.bar)
        bh.setContentsMargins(12, 6, 12, 6)
        bh.setSpacing(12)
        self.bar_text = QLabel("")
        self.bar_text.setFont(sans(13, 600))
        self.bar_text.setToolTip("Edits on this page that are not staged yet: they exist only in this window")
        self.bar_stage = btn("Stage", pri=True, tip="Stage these edits for the agents checked right now. They go "
                                                    "to Review; nothing is written yet.")
        self.bar_stage.clicked.connect(self.stage_edits)
        self.bar_for = QLabel("")
        self.bar_for.setToolTip("The agents checked right now: Stage puts the edits in Review for them")
        self.bar_one = link("", tip="")
        self.bar_one.clicked.connect(lambda: self.stage_edits(only=True))
        bh.addWidget(self.bar_text)
        bh.addWidget(self.bar_stage)
        bh.addWidget(self.bar_for)
        bh.addWidget(self.bar_one)
        bh.addStretch(1)
        dl = link("Discard", tip="Throw away the edits on this page that are not staged")
        dl.clicked.connect(self.discard_edits)
        bh.addWidget(dl)
        self.bar.hide()

    # ------------------------------------------------------------------ geometry
    def content_width(self) -> int:
        return self.scroll.viewport().width() - self.cv.contentsMargins().left() - self.cv.contentsMargins().right()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._place_bar()
        w = self.width() - 12
        narrow = w < 700
        wide = min(w, 1240) - 50 >= 1080
        if narrow != self.narrow or wide != self.aux_wide:
            self.narrow, self.aux_wide = narrow, wide
            m = (8, 12, 8, 70) if narrow else (24, 18, 24, 80)
            self.cv.setContentsMargins(*m)
            if self.blocks:
                self.rebuild()

    def _place_bar(self) -> None:
        if not self.bar.isVisible():
            return
        m = self.cv.contentsMargins()
        w = min(self.scroll.viewport().width() - m.left() - m.right(), 1240)
        self.bar.setFixedWidth(max(200, w))
        self.bar.adjustSize()
        self.bar.move(m.left(), self.height() - self.bar.height() - 10)
        self.bar.raise_()

    # ------------------------------------------------------------------ building
    def page(self):
        return self.state.layout.page(self.page_id)

    def ctx(self) -> Ctx:
        return Ctx(self, self.q.strip().casefold(), self.only_diff, self.state.customizing)

    def rebuild(self, keep_scroll: bool = True) -> None:
        st = self.state
        p = self.page()
        if p is None:
            return
        pos = self.scroll.verticalScrollBar().value()
        self.content.setUpdatesEnabled(False)
        key_page = len(p.sections) == 1 and p.label == p.sections[0]
        self.h1.setText(p.label)
        self.h1.setFont(mono(20, 600) if key_page else sans(20, 600))
        self.h1.setToolTip(", ".join(p.sections))
        clear_layout(self.bl)
        self.tables, self.chains, self.aux, self.blocks = [], [], None, {}
        ctx = self.ctx()
        qm = bool(ctx.q or ctx.only_diff)
        secs = p.sections if (ctx.cust or qm) else st.visible_sections(p)
        if self.state.ref_key():
            st.work.rebase(st.scope(), st.ref_key())
        built = 0
        for k in secs:
            b = self._block(k, p, ctx)
            if b is not None:
                self.bl.addWidget(b)
                built += 1
        if qm and not built:
            n = label(f"No setting on this page matches “{self.q}”. The search at the top looks through every page."
                      if ctx.q else "Nothing differs across the checked agents on this page.", tone="muted")
            n.setContentsMargins(4, 22, 4, 22)
            self.bl.addWidget(n)
        if not qm:
            hid = [] if ctx.cust else [k for k in p.sections if st.section_hidden(k)]
            if hid:
                hk = f"@hidsec:{p.id}"
                o = hk in st.grp_open
                line = Clickable()
                line.setObjectName("hidline")
                line.setStyleSheet(f"QFrame#hidline {{ border: 1px dashed {T['line']}; border-radius: 6px; }}")
                hl = QHBoxLayout(line)
                hl.setContentsMargins(8, 0, 8, 0)
                hl.addWidget(Chevron(o))
                hl.addWidget(label(f"Hidden sections ({len(hid)})", tone="muted"))
                names = label(", ".join(hid), tone="faint", is_mono=True)
                hl.addWidget(names, 1)
                line.setFixedHeight(30)
                line.setToolTip("Sections you have hidden on this page")
                line.clicked.connect(lambda: self.toggle_group(hk))
                self.bl.addWidget(line)
                if o:
                    for k in hid:
                        b = self._block(k, p, ctx)
                        if b is not None:
                            self.bl.addWidget(b)
            self.bl.addSpacing(12)
            self.bl.addWidget(self._related(p, ctx))
        self.refresh_header()
        self.refresh_bar()
        self.content.setUpdatesEnabled(True)
        if keep_scroll:
            QTimer.singleShot(0, self.scroll, lambda: self.scroll.verticalScrollBar().setValue(pos))
        self.stale = False
        self.stale_values = False

    def _block_open(self, k: str, p) -> bool:
        st = self.state
        if k in st.blk_open:
            return st.blk_open[k]
        sec = st.model.sections.get(k)
        vis = st.visible_sections(p)
        if sec is not None and sec.chain:
            return True
        if len(vis) == 1:
            return True
        h = 0
        for key in vis:
            s = st.model.sections.get(key)
            if s is not None:
                h += 64 + 32 * sum(1 for n in s.own_nodes() if not st.node_hidden_self(n))
        return h <= 660

    def _block(self, k: str, p, ctx: Ctx) -> Block | None:
        st = self.state
        sec = st.model.sections.get(k)
        if sec is None:
            return None
        off = st.section_hidden(k)
        qm = bool(ctx.q or ctx.only_diff)
        if qm:
            hit = bool(ctx.q) and ctx.q in k.casefold()
            sub = Ctx(self, "" if hit else ctx.q, ctx.only_diff, ctx.cust)
            body = self._body(k, sub, force=hit, qm=not hit or ctx.only_diff)
            if body is None:
                return None
            opened = True
        else:
            opened = self._block_open(k, p)
            body = self._body(k, ctx) if opened else None
        b = Block(self, k, k, True, self.block_summary(k), opened, off)
        if ctx.cust or off:
            eye = IconButton("eye_off" if off else "eye", ("Show" if off else "Hide") + " this section",
                             color="faint" if off else "muted")
            eye.clicked.connect(lambda: self.toggle_section(k))
            b.hl.insertWidget(b.eye_slot, eye)
        if ctx.cust:
            i = p.sections.index(k)
            for name, tip, en, fn in (("up", "Move this section up", i > 0, lambda: self.move_section(k, -1)),
                                      ("down", "Move this section down", i < len(p.sections) - 1,
                                       lambda: self.move_section(k, 1)),
                                      ("move", "Move this section to another page", True, None)):
                bt = IconButton(name, tip)
                bt.setEnabled(en)
                if fn is not None:
                    bt.clicked.connect(fn)
                else:
                    bt.clicked.connect(lambda _=False, bt=bt: self.move_section_menu(k, bt))
                b.hl.addWidget(bt)
        b.head.clicked.connect(lambda: self.toggle_block(k))
        if body is not None:
            b.bv.addWidget(body)
        self.blocks[k] = b
        return b

    def _body(self, k: str, ctx: Ctx, force: bool = False, qm: bool = False) -> QWidget | None:
        st = self.state
        sec = st.model.sections[k]
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        has = False
        if sec.chain == "roster":
            if (qm and ctx.q) or (ctx.only_diff and not differ_count(st.session, st.scope().usable_keys)):
                return None
            t = RosterTable(self, ctx)
            self.chains.append(t)
            v.addWidget(t)
            return box
        if not sec.chain:
            t = SettingsTable(self, sec.own_nodes(), ctx, hid_key=f"@hid:{k}")
            if qm and t.empty:
                return None
            self.tables.append(t)
            v.addWidget(t)
            if not qm:
                for note in self._misnamed_notes(sec):
                    v.addWidget(note)
            return box
        if not (qm and ctx.q):
            if sec.chain == "auxiliary":
                if not ctx.only_diff or self._aux_differs():
                    self.aux = AuxPanel(self, ctx)
                    v.addWidget(self.aux)
                    has = True
            elif not ctx.only_diff or len(C.variants(st.scope(), spec_for(sec.chain), st.scope().usable_keys)) > 1:
                ch = ChainTable(self, sec.chain, ctx)
                self.chains.append(ch)
                v.addWidget(ch)
                if sec.chain == "primary":
                    note = self._legacy_effort_note()
                    if note is not None:
                        v.addWidget(note)
                has = True
        own = sec.own_nodes()
        if own:
            gk = f"@other:{k}"
            opened = qm or force or gk in st.grp_open
            t = SettingsTable(self, own, ctx, hid_key=f"@hid:{k}") if opened else None
            if qm and (t is None or t.empty):
                return box if has else None
            ls = st.visible_leaves(own)
            g = GroupRow(t or SettingsTableStub(ctx), None, 0, f"Other {k} settings",
                         sum_html(len(ls), st.count_differ(ls)), opened=opened)
            g.clicked.connect(lambda: self.toggle_group(gk))
            g.setProperty("other", k)
            v.addWidget(g)
            if t is not None:
                self.tables.append(t)
                v.addWidget(t)
            has = True
        return box if has else None

    def _legacy_effort_note(self) -> QWidget | None:
        """model.reasoning_effort is ignored by Hermes for the main model; offer to remove it."""
        st = self.state
        scope = st.scope()
        path = ("model", "reasoning_effort")
        keys = [k for k in scope.usable_keys if scope.draft_value(k, path) is not MISSING]
        if not keys:
            return None
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(10, 4, 8, 4)
        h.setSpacing(12)
        lab = label(f"{plural(len(keys), 'checked agent')} {'has' if len(keys) == 1 else 'have'} "
                    "model › reasoning_effort, which Hermes does not read for the main model "
                    "(it uses agent › reasoning_effort, shown above).", tone="amber", small=True, wrap=True)
        h.addWidget(lab, 1)
        rm = link("Stage removal", tip="Removes model › reasoning_effort from these agents (listed in Review)")
        rm.clicked.connect(lambda: self._remove_legacy_effort(keys))
        h.addWidget(rm)
        w.setMinimumHeight(30)
        return w

    def _remove_legacy_effort(self, keys: list[str]) -> None:
        from ..core.edits import EditOp, OpKind

        st = self.state
        per_agent = {}
        for k in keys:
            base, sha = st.session.base_of(k)
            per_agent[k] = (base, sha, [EditOp(OpKind.REMOVE, ("model", "reasoning_effort"), origin="unused-key")])
        st.session.drafts.apply_many(per_agent)
        st.say(f"Removal of model › reasoning_effort staged for {plural(len(keys), 'agent')}. See Review.")

    def _misnamed_notes(self, sec) -> list[QWidget]:
        """A note per misspelled key the checked agents have, with a button that stages the move."""
        scope = self.state.scope()
        notes = []
        for n in _walk(sec.nodes):
            if n.is_group or not n.suggest:
                continue
            keys = [k for k in scope.usable_keys if scope.draft_value(k, n.path) is not MISSING]
            if not keys:
                continue
            old, new = _crumbs(n.path), _crumbs(n.suggest)
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(10, 4, 8, 4)
            h.setSpacing(12)
            h.addWidget(label(f"{plural(len(keys), 'checked agent')} {'has' if len(keys) == 1 else 'have'} {old}, "
                              f"which Hermes does not read. Did you mean {new}?",
                              tone="amber", small=True, wrap=True), 1)
            mv = link(f"Stage move to {n.suggest[-1]}",
                      tip=f"Each agent's own value moves to {new} and {old} is removed (listed in Review). "
                          f"An agent whose {new} already has a different value is left alone.")
            mv.clicked.connect(lambda _=False, a=n.path, b=n.suggest: self._stage_move(a, b))
            h.addWidget(mv)
            w.setMinimumHeight(30)
            notes.append(w)
        return notes

    def _stage_move(self, old, new) -> None:
        st = self.state
        per_agent, conflicts = move_key_ops(st.scope(), old, new)
        if per_agent:
            st.session.drafts.apply_many(per_agent)
        text = f"Move of {_crumbs(old)} to {_crumbs(new)} staged for {plural(len(per_agent), 'agent')}. See Review."
        if conflicts:
            names = ", ".join(st.session.record(k).display for k in conflicts)
            text += f" Left alone (their {_crumbs(new)} has another value): {names}."
        st.say(text)

    def _aux_differs(self) -> bool:
        st = self.state
        scope = st.scope()
        keys = scope.usable_keys
        return any(len(C.variants(scope, C.aux_spec(t), keys)) > 1 for t in st.model.tasks)

    def _related(self, p, ctx: Ctx) -> Block:
        st = self.state
        pins = [tuple(x) for x in st.layout.pins.get(p.id, [])]
        nodes = [st.model.nodes[x] for x in pins if x in st.model.nodes and not st.model.nodes[x].is_group]
        rk = f"@rel:{p.id}"
        opened = st.blk_open.get(rk, bool(nodes))
        summ = (plural(len(nodes), "setting") + " from other places, repeated here") if nodes else "none yet"
        b = Block(self, rk, "Related settings", False, summ, opened)
        b.head.setToolTip("Settings from other sections, repeated here so you do not have to go and find them")
        b.head.clicked.connect(lambda: self.toggle_block(rk, default=bool(nodes)))
        if opened:
            if nodes:
                t = SettingsTable(self, nodes, ctx, mode="rel")
                self.tables.append(t)
                b.bv.addWidget(t)
            add = QWidget()
            ah = QHBoxLayout(add)
            ah.setContentsMargins(12, 0, 12, 0)
            ah.setSpacing(12)
            al = link("+ Add a setting…", tip="Search for any setting and repeat it here. It stays in its own "
                                              "section too; this only changes what the page shows.")
            al.clicked.connect(lambda: self.main.start_pin(p.id))
            ah.addWidget(al)
            ah.addWidget(ElideLabel("pick any setting from the search; it stays in its own section too", tone="faint"), 1)
            add.setMinimumHeight(32)
            b.bv.addWidget(add)
        return b

    # ------------------------------------------------------------------ summaries
    def block_summary(self, k: str) -> str:
        st = self.state
        sec = st.model.sections[k]
        if sec.chain == "roster":
            ref = st.ref_key()
            n = len(table_for(st.session, ref).teammates) if ref else 0
            d = differ_count(st.session, st.scope().usable_keys)
            return plural(n, "teammate") + (f" · <b style='color:{T['amber']}'>{differ(d)}</b>" if d else "")
        own = st.visible_leaves(sec.own_nodes())
        d = st.count_differ(own)
        if not sec.chain:
            return sum_html(len(own), d)
        other = (" · " + plural(len(own), "other setting") +
                 (f" · <b style='color:{T['amber']}'>{differ(d)}</b>" if d else "")) if own else ""
        scope = st.scope()
        keys = scope.usable_keys
        if sec.chain == "auxiliary":
            ts = [t for t in st.model.tasks if not st.task_hidden(t)]
            td = sum(1 for t in ts if len(keys) > 1 and len(C.variants(scope, C.aux_spec(t), keys)) > 1)
            return plural(len(ts), "task") + (f" · <b style='color:{T['amber']}'>{differ(td)}</b>" if td else "") + other
        v = len(C.variants(scope, spec_for(sec.chain), keys)) if len(keys) > 1 else 1
        return "model chain" + (f" · <b style='color:{T['amber']}'>{v} different</b>" if v > 1 else "") + other

    def refresh_header(self) -> None:
        st = self.state
        p = self.page()
        if p is None:
            return
        secs = st.visible_sections(p)
        n = 0
        for k in secs:
            sec = st.model.sections.get(k)
            if sec is None:
                continue
            n += len(st.visible_leaves(sec.own_nodes()))
            if sec.chain == "roster" and st.ref_key():
                n += len(table_for(st.session, st.ref_key()).teammates)
            if sec.chain == "auxiliary":
                for t in st.model.tasks:
                    if not st.task_hidden(t):
                        n += len(st.visible_leaves(st.model.task_nodes(t)))
        keys = st.scope().usable_keys
        d = sum(self.main.counter.counts.get(k, 0) for k in secs)
        self.sub.setText(plural(len(secs), "section") + " · " + plural(n, "setting") +
                         ((" · " + (differ(d) if d else "none differ")) if len(keys) > 1 else ""))
        for k, b in self.blocks.items():
            if k in st.model.sections:
                b.sum.setText(self.block_summary(k))

    # ------------------------------------------------------------------ refresh
    def refresh(self) -> None:
        """Values changed (scope, staged edits, agent data): update what is built."""
        if self.only_diff:
            self.rebuild()
            return
        for t in self.tables:
            t.refresh()
        for c in self.chains:
            c.refresh()
        if self.aux is not None:
            self.aux.build()
        self.refresh_header()
        self.refresh_bar()

    def refresh_aux(self) -> None:
        if self.aux is not None:
            self.aux.build()

    def page_edits(self) -> list:
        st = self.state
        p = self.page()
        if p is None:
            return []
        scope = st.scope()
        pins = {tuple(x) for x in st.layout.pins.get(p.id, [])}
        out = []
        for path in list(st.work.values):
            sec = st.model.section_of(path)
            if (sec in p.sections or path in pins) and st.work.dirty(path, scope):
                out.append(path)
        return out

    def refresh_bar(self) -> None:
        edits = self.page_edits()
        n = len(self.state.scope().usable_keys)
        if not edits:
            self.bar.hide()
            return
        self.bar_text.setText(plural(len(edits), "edit") + " not staged")
        self.bar_stage.setEnabled(bool(n))
        self.bar_for.setText(f"for {plural(n, 'agent')}" if n else "select agents to stage")
        set_only_link(self.bar_one, self.state, n)
        self.bar.show()
        self._place_bar()

    # ------------------------------------------------------------------ open/close
    def toggle_block(self, k: str, default: bool | None = None) -> None:
        st = self.state
        cur = st.blk_open.get(k, default if default is not None else self._block_open(k, self.page()))
        st.blk_open[k] = not cur
        st.save_open()
        self.rebuild()

    def toggle_group(self, key: str) -> None:
        st = self.state
        (st.grp_open.discard if key in st.grp_open else st.grp_open.add)(key)
        st.save_open()
        self.rebuild()

    def expand_all(self, on: bool) -> None:
        st = self.state
        p = self.page()
        for k in st.visible_sections(p):
            st.blk_open[k] = on
            sec = st.model.sections.get(k)
            if sec is None:
                continue
            groups = [gid(n.path) for n in _walk(sec.own_nodes()) if n.is_group]
            if sec.chain:
                groups.append(f"@other:{k}")
            for g in groups:
                (st.grp_open.add if on else st.grp_open.discard)(g)
        st.save_open()
        self.rebuild()

    def _apply_filter(self) -> None:
        self.q = self.filter.text()
        self.rebuild(keep_scroll=False)

    def _toggle_od(self) -> None:
        self.only_diff = not self.only_diff
        self.od.set_state(self.only_diff)
        self.rebuild()

    # ------------------------------------------------------------------ customize
    def toggle_section(self, k: str) -> None:
        st = self.state
        sec = st.model.sections.get(k)
        if sec is not None:
            st.layout.toggle_section(sec, st.used)
            st.save_layout()

    def toggle_node(self, node) -> None:
        self.toggle_node_path(node.path)

    def toggle_node_path(self, path) -> None:
        st = self.state
        st.layout.toggle_node(tuple(path), st.used)
        st.save_layout()

    def move_section(self, k: str, d: int) -> None:
        p = self.page()
        i = p.sections.index(k)
        j = i + d
        if 0 <= j < len(p.sections):
            p.sections[i], p.sections[j] = p.sections[j], p.sections[i]
            self.state.save_layout()

    def move_section_menu(self, k: str, anchor) -> None:
        st = self.state
        cur = self.page()
        m = QMenu(self)
        m.setToolTipsVisible(True)
        m.addSection(f"Move section {k} to page")
        for c in st.layout.cats:
            ps = [p for p in c.pages if p is not cur]
            if not ps:
                continue
            if not st.layout.flat:
                m.addSection(c.label)
            for p in ps:
                a = m.addAction(p.label)
                a.setToolTip(f"Show section {k} on page {p.label} instead (only the arrangement changes)")
                a.triggered.connect(lambda _=False, p=p: self._move_to(k, p))
        m.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def _move_to(self, k: str, target) -> None:
        cur = self.page()
        cur.sections.remove(k)
        target.sections.append(k)
        self.state.save_layout()
        self.state.say(f"Moved section {k} to page {target.label}")

    def unpin(self, path) -> None:
        st = self.state
        pins = st.layout.pins.get(self.page_id, [])
        st.layout.pins[self.page_id] = [p for p in pins if tuple(p) != tuple(path)]
        st.save_layout()

    # ------------------------------------------------------------------ editing
    def commit(self, item, value) -> None:
        st = self.state
        st.work.set(item.node.path, value, st.scope())
        self.after_edit(item)

    def after_edit(self, item=None) -> None:
        """Refresh just what one edit can change (fast path)."""
        if item is not None:
            for t in self.tables:
                it = t.item_for(item.node.path)
                if it is not None:
                    it.refresh()
            for t in self.tables:
                st = self.state
                for g in t.groups:
                    ls = st.visible_leaves(g.node.children)
                    g.set_summary(sum_html(len(ls), st.count_differ(ls)))
        self.refresh_header()
        self.refresh_bar()

    def stage_edits(self, only: bool = False) -> None:
        self.stage_paths(self.page_edits(), only)

    def stage_paths(self, edits: list, only: bool = False) -> None:
        """Stage these edited settings (all of the page's, or one from the right-click menu)."""
        st = self.state
        scope = st.one_scope() if only else st.scope()
        if not edits or not scope.usable_keys:
            return
        blocks = {p for p in edits if (n := st.model.nodes.get(p)) is not None and n.kind == "block"}
        n = st.work.stage(edits, scope, blocks)
        skipped = {k for k, _p in st.work.skipped}
        if skipped:
            names = ", ".join(st.session.record(k).name for k in list(skipped)[:4])
            st.say(f"Staged, except for {plural(len(skipped), 'agent')} ({names}): their credentials in this block "
                   "could not be matched. Edit the block one agent at a time.")
        else:
            st.say(staged_text(n, scope))

    def discard_edits(self) -> None:
        self.state.work.discard(self.page_edits())

    def open_yaml(self, node) -> None:
        from .dialogs import YamlDialog

        st = self.state
        cur = st.shown_value(node.path)
        dlg = YamlDialog(st, node, None if cur is MISSING else cur, self)
        if dlg.exec():
            st.work.set(node.path, dlg.value, st.scope())
            self.refresh()

    def check_exactly(self, keys: list[str]) -> None:
        st = self.state
        st.session.replace_checked(keys)
        st.say(f"Checked exactly {plural(len(keys), 'agent')}. Use All to check everyone again.")

    # -- chains --------------------------------------------------------------------
    def _work_chain(self, key: str):
        st = self.state
        return st.work.work_chain(key, st.scope(), st.ref_key())

    def pick_model(self, key: str, i: int, provider: str | None, add: bool = False) -> None:
        from .dialogs import pick_model

        st = self.state
        rows = st.work.chain_rows(key, st.scope(), st.ref_key())
        cur = rows[i] if i < len(rows) else None
        if provider is None:
            provider = cur[0] if cur else ""
        role = "Head" if i == 0 else f"Fallback {i}"
        title = (f"Add a fallback · {chain_title(key)}" if add else f"{role} model · {chain_title(key)}")
        sel = (cur[0], cur[1]) if cur and cur[0] == provider else None
        picked = pick_model(self, st, title, provider or "", sel)
        if picked is None:
            self.refresh_chain(key)
            return
        self.set_model(key, i, picked)

    def set_model(self, key: str, i: int, pm: tuple[str, str]) -> None:
        """Provider and model of row ``i`` (a row past the end is added)."""
        w = self._work_chain(key)
        if i < len(w.rows):
            w.rows[i][0], w.rows[i][1] = pm
            w.mark(i, "pm")
        else:
            w.rows.append([pm[0], pm[1], None])
            w.mark(len(w.rows) - 1, "pm")
        self.state.work.edited()
        self.refresh_chain(key)

    def chain_default(self, key: str, i: int, col: int) -> None:
        """Right-click › Default on a chain cell: provider auto + model '' (col 1, 2), or no effort (col 3)."""
        if col == 3:
            self.set_effort(key, i, None)
        else:
            self.set_model(key, i, C.AUTO_PM)

    def set_effort(self, key: str, i: int, effort: str | None) -> None:
        w = self._work_chain(key)
        if i < len(w.rows):
            w.rows[i][2] = effort
            w.mark(i, "e")
        self.state.work.edited()
        self.refresh_chain(key)

    def chain_move(self, key: str, i: int, d: int) -> None:
        w = self._work_chain(key)
        j = i + d
        if 0 <= j < len(w.rows):
            w.rows[i], w.rows[j] = w.rows[j], w.rows[i]
            w.mark(min(i, j), "all")
        self.state.work.edited()
        self.refresh_chain(key)

    def chain_remove(self, key: str, i: int) -> None:
        w = self._work_chain(key)
        if i < len(w.rows) and len(w.rows) > 1:
            del w.rows[i]
            w.mark(i, "all")
        elif len(w.rows) == 1 and key.startswith("aux:"):
            w.remove_all()             # an auxiliary task's only model: its whole entry goes
        self.state.work.edited()
        self.refresh_chain(key)

    def chain_stage(self, key: str, only: bool = False) -> None:
        st = self.state
        scope = st.one_scope() if only else st.scope()
        st.say(staged_text(st.work.stage_chain(key, scope), scope))

    def chain_discard(self, key: str) -> None:
        self.state.work.drop_chain(key)
        self.refresh_chain(key)

    def chain_unstage(self, key: str) -> None:
        st = self.state
        unstage_chain(st.scope(), key)
        st.work.drop_chain(key)

    def refresh_chain(self, key: str) -> None:
        if key.startswith("aux:"):
            self.refresh_aux()
        else:
            for c in self.chains:
                if c.key == key:
                    c.refresh()
        self.main.menu.refresh_marks()

    def use_default(self, key: str, chain: list[list]) -> None:
        st = self.state
        task = key[4:] if key.startswith("aux:") else None
        no_eff = bool(task) and task in st.model.no_effort
        st.work.drop_chain(key)
        w = st.work.work_chain(key, st.scope(), st.ref_key())
        w.rows = [[r[0], r[1], None if no_eff else r[2]] for r in chain]
        w.pm = w.e = w.fb = True
        st.work.edited()
        self.refresh_chain(key)

    def open_defaults(self, table, anchor, saving: bool = False) -> None:
        from .dialogs import DefaultsPopup

        pop = DefaultsPopup(self.state, self, table.key)
        if saving:
            pop.df.saving, pop.df.edit, pop.df.draft = True, -1, None
        pop.show_for(table, anchor)

    # -- auxiliary -----------------------------------------------------------------
    def aux_select(self, task: str | None) -> None:
        self.aux_task = task
        self.refresh_aux()

    def aux_stage(self, tasks: list[str], only: bool = False) -> None:
        st = self.state
        scope = st.one_scope() if only else st.scope()
        st.say(staged_text(st.work.stage_chains([f"aux:{t}" for t in tasks], scope), scope))

    def aux_discard(self, tasks: list[str]) -> None:
        for t in tasks:
            self.state.work.drop_chain(f"aux:{t}")
        self.refresh_aux()

    def aux_choose(self, tasks: list[str]) -> None:
        from .dialogs import pick_model

        st = self.state
        if not tasks:
            return
        picked = pick_model(self, st, f"Head model for {plural(len(tasks), 'task')}", "", None)
        if picked is None:
            return
        for t in tasks:
            w = st.work.work_chain(f"aux:{t}", st.scope(), st.ref_key())
            if w.rows:
                w.rows[0][0], w.rows[0][1] = picked
            else:
                w.rows.append([picked[0], picked[1], None])
            w.mark(0, "pm")
        st.work.edited()
        self.refresh_aux()
        st.say(f"Head model of {plural(len(tasks), 'task')} edited. Not staged yet.")

    def aux_default(self, tasks: list[str], what: str) -> None:
        """Head of each task back to the Hermes default: provider auto + model '' ("pm"), or no effort ("e")."""
        st = self.state
        for t in tasks:
            w = st.work.work_chain(f"aux:{t}", st.scope(), st.ref_key())
            if what == "e":
                if w.rows:
                    w.rows[0][2] = None
                    w.mark(0, "e")
            else:
                if w.rows:
                    w.rows[0][0], w.rows[0][1] = C.AUTO_PM
                else:
                    w.rows.append([*C.AUTO_PM, None])
                w.mark(0, "pm")
        st.work.edited()
        self.refresh_aux()
        what_text = "no reasoning effort" if what == "e" else "provider auto, model ''"
        st.say(f"{plural(len(tasks), 'task')} set to default ({what_text}). Not staged yet.")

    # ------------------------------------------------------------------ jump to a setting
    def reveal(self, section: str, path) -> None:
        """Open what is needed to show ``path`` (or the section) and flash it."""
        st = self.state
        st.blk_open[section] = True
        if st.section_hidden(section):
            st.grp_open.add(f"@hidsec:{self.page_id}")
        target = None
        if path:
            path = tuple(path)
            self.only_diff = False
            self.od.set_state(False)
            node = st.model.nodes.get(path)
            task = st.model.task_of(path)
            if task:
                self.aux_task = task
            elif st.model.sections[section].chain:
                st.grp_open.add(f"@other:{section}")
            n = node.parent if node else None
            while n is not None:
                st.grp_open.add(gid(n.path))
                n = n.parent
            if node is not None and st.node_hidden(node):
                st.grp_open.add(f"@hid:aux:{task}" if task else f"@hid:{section}")
                if task and st.task_hidden(task):
                    st.grp_open.add("@hid:auxtasks")
            target = path
        st.save_open()
        self.q = ""
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.blockSignals(False)
        self.rebuild(keep_scroll=False)
        QTimer.singleShot(30, self, lambda: self._flash(section, target))

    def _flash(self, section: str, path) -> None:
        widget = None
        if path:
            for t in self.tables:
                it = t.item_for(path)
                if it is not None:
                    widget = it.line
                    break
        if widget is None:
            b = self.blocks.get(section)
            widget = b.head if b else None
        if widget is None:
            return
        self.scroll.ensureWidgetVisible(widget, 0, self.scroll.viewport().height() // 3)
        if hasattr(widget, "flash"):
            widget.flash = True
            widget.update()
            QTimer.singleShot(1800, widget, lambda: (setattr(widget, "flash", False), widget.update()))


class SettingsTableStub:
    """Lets a GroupRow be built before its table exists (only ``ctx`` is used)."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.page = ctx.page


def _crumbs(path) -> str:
    return " › ".join(str(k) for k in path)


def _walk(nodes):
    for n in nodes:
        yield n
        if n.children:
            yield from _walk(n.children)


def staged_text(n: int, scope=None) -> str:
    """Status line after Stage; ``n`` is the number of agents whose staged changes actually changed."""
    if n == 0 and scope is not None:
        keys = scope.usable_keys
        who = scope.session.record(keys[0]).name if len(keys) == 1 else plural(len(keys), "agent")
        return (f"Nothing staged: {who} already {'has' if len(keys) == 1 else 'have'} these values. "
                "Your edit stays on the page.")
    total = len(scope.usable_keys) if scope is not None else n
    same = f" ({total - n} already had these values)" if scope is not None and total > n else ""
    return f"Staged for {plural(n, 'agent')}{same}. Nothing is written until you press Write config in Review."


__all__ = ["PageView", "format_path", "leaves", "rows_of", "REMOVE"]
