"""Auxiliary models: the task table stays; the chosen task opens beside it (wide) or below it.

Editing happens in place: the task's chain table and its other settings fill the detail panel.
Several checked tasks can get one head model at once ("Choose model for N checked tasks…").
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QMenu, QScrollArea, QSizePolicy, QSplitter,
                               QVBoxLayout, QWidget)

from ..services import chains as C
from ..services.work import chain_staged, rows_of, spec_for
from .chain_table import ChainTable, model_text, wrap_model
from .settings_table import GroupRow, SettingsTable, sum_html
from .theme import T, mono, sans
from .widgets import set_only_link, clear_layout, Clickable, IconButton, btn, label, link, plural



AUX_HEAD_TIPS = {
    "Task": "Hermes' helper jobs (auxiliary › <task>), such as vision, compression or web extraction. Click a "
            "task to edit its model chain and its other settings.",
    "Provider": "Provider of the task's head model (auxiliary › <task> › provider)",
    "Model": "Head model of the task (auxiliary › <task> › model). An unedited row shows the reference agent's "
             "value; an amber dot means the checked agents differ.",
    "Effort": "Reasoning effort of the head model; — means the task takes none",
    "Fallbacks": "How many fallback models the task has (auxiliary › <task> › fallback_chain)",
}

class TaskRow(Clickable):
    picked = Signal(str)

    def __init__(self, panel, task: str | None, cells: list[QWidget], widths: list[int], header: bool = False) -> None:
        super().__init__()
        self.panel, self.task, self.header = panel, task, header
        self.on = False
        self.mark = ""
        self._hover = False
        self.widths = widths
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        self.cells = cells
        for i, c in enumerate(cells):
            if widths[i]:
                c.setFixedWidth(widths[i])
            else:
                c.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            h.addWidget(c, 0 if widths[i] else 1)
        self.setMinimumHeight(27 if header else 29)
        if header:
            self.setCursor(Qt.ArrowCursor)
        elif task:
            self.setToolTip(f"Edit {task} in place. Right-click to set it back to the Hermes default.")
            self.clicked.connect(lambda: self.picked.emit(self.task))

    def contextMenuEvent(self, ev) -> None:
        if self.header or not self.task or self.panel.ctx.cust:
            return
        self.panel.row_menu(self.task).exec(ev.globalPos())

    def enterEvent(self, ev) -> None:
        self._hover = not self.header
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        if self.on:
            p.fillRect(self.rect(), T.c("sel"))
        elif self._hover:
            p.fillRect(self.rect(), T.c("hover"))
        elif self.header:
            p.fillRect(self.rect(), T.c("page"))
        p.setPen(QPen(T.c("soft"), 1))
        x = 0
        for c in self.cells[:-1]:
            x += c.width()
            p.drawLine(x, 0, x, self.height())
        p.setPen(QPen(T.c("line") if self.header else T.c("soft"), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        if self.mark:
            p.fillRect(0, 0, 3, self.height(), T.c("accent") if self.mark == "edit" else T.c("amber_edge"))
        elif self.on:
            p.fillRect(0, 0, 2, self.height(), T.c("accent"))


def _dot_label(text_widget: QWidget, n: int, what: str) -> QWidget:
    """A cell with an amber dot in its corner when the checked agents hold several values."""
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(5, 2, 4, 2)
    h.setSpacing(0)
    h.addWidget(text_widget, 1)
    if n > 1:
        dot = QLabel(w)
        dot.setFixedSize(6, 6)
        dot.setStyleSheet(f"background: {T['amber_edge']}; border-radius: 3px;")
        dot.setToolTip(f"{n} {what} across the checked agents")
        w.dot = dot
    return w


class AuxPanel(QWidget):
    def __init__(self, page, ctx) -> None:
        super().__init__()
        self.page, self.ctx = page, ctx
        self.state = ctx.state
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(0)
        self.build()

    def wide(self) -> bool:
        return self.page.aux_wide

    def build(self) -> None:
        clear_layout(self.v)
        st = self.state
        model = st.model
        task = self.page.aux_task if self.page.aux_task in model.tasks else None
        left = self._task_table(task)
        if task is None and not self.wide():
            self.v.addWidget(left)
            return
        split = QSplitter(Qt.Horizontal if self.wide() else Qt.Vertical)
        split.setProperty("aux", True)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(7)
        split.addWidget(left)
        right = self._detail(task)
        split.addWidget(right)
        if self.wide():
            frac = float(st.settings.value("ui/aux_split", 0.54))
            w = max(600, self.page.content_width())
            split.setSizes([int(w * frac), int(w * (1 - frac))])
        else:
            hgt = int(st.settings.value("ui/aux_h", 262))
            left.setMaximumHeight(16777215)
            split.setSizes([hgt, max(300, right.sizeHint().height())])
        split.splitterMoved.connect(lambda *_a, s=split: self._save_split(s))
        self.v.addWidget(split)

    def _save_split(self, split: QSplitter) -> None:
        sizes = split.sizes()
        if self.wide():
            total = sum(sizes) or 1
            self.state.settings.setValue("ui/aux_split", round(max(0.3, min(0.7, sizes[0] / total)), 3))
        else:
            self.state.settings.setValue("ui/aux_h", max(90, min(900, sizes[0])))

    # -- task table --------------------------------------------------------------------
    def _task_table(self, current: str | None) -> QWidget:
        st, ctx = self.state, self.ctx
        model = st.model
        scope = st.scope()
        keys = scope.usable_keys
        ref = st.ref_key()
        cust = ctx.cust
        shown = [t for t in model.tasks if cust or not st.task_hidden(t)]
        hidden = [] if cust else [t for t in model.tasks if st.task_hidden(t)]
        fm_m = QFontMetrics(mono(12 if ctx.narrow else 12.5))
        fm_s = QFontMetrics(sans(12.5))
        provs = {"not set", "Provider"}
        for k in keys:
            for t_ in model.tasks:
                h = rows_of(C.read_chain(scope, spec_for(f"aux:{t_}"), k))
                if h and h[0][0]:
                    provs.add(h[0][0])
        for key_, w_ in st.work.chains.items():
            if key_.startswith("aux:") and w_.rows and w_.rows[0][0]:
                provs.add(w_.rows[0][0])
        widths = [24, min(150 if ctx.narrow else 190, max(fm_m.horizontalAdvance(t) for t in model.tasks or ["task"]) + 14),
                  min(150, max(fm_s.horizontalAdvance(p) for p in provs) + 12), 0,
                  max(fm_s.horizontalAdvance(e) for e in model.efforts + ["default"]) + 12,
                  fm_s.horizontalAdvance("Fallbacks") + 10]
        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(0)
        sel = self.page.aux_checked
        all_box = QCheckBox()
        all_box.setToolTip("Check or uncheck every task shown. Checked tasks can get one head model at once "
                           "(Choose model for checked tasks).")
        all_box.setChecked(bool(sel) and len(sel) == len(shown))
        all_box.clicked.connect(lambda on: self._check_all(on, shown))
        head_cells = [self._center(None if cust else all_box)]
        for text in ("Task", "Provider", "Model", "Effort", "Fallbacks"):
            c = QWidget()
            hl = QHBoxLayout(c)
            hl.setContentsMargins(5, 0, 4, 0)
            hd = label(text, tone="muted", small=True)
            hd.setToolTip(AUX_HEAD_TIPS[text])
            hl.addWidget(hd)
            if text == "Model" and st.ref_name():
                hl.addWidget(label(f"from {st.ref_name()}", tone="faint", small=True))
            hl.addStretch(1)
            head_cells.append(c)
        bv.addWidget(TaskRow(self, None, head_cells, widths, header=True))
        rows_box = QWidget()
        rv = QVBoxLayout(rows_box)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        agent_rows = {k: None for k in keys}
        for t in shown:
            rv.addWidget(self._task_row(t, current, widths, keys, ref, agent_rows))
        if hidden:
            opened = "@hid:auxtasks" in st.grp_open
            g = GroupRow(self, None, 0, f"Hidden ({len(hidden)})", opened=opened, plain=True)
            g.clicked.connect(lambda: self.page.toggle_group("@hid:auxtasks"))
            rv.addWidget(g)
            if opened:
                for t in hidden:
                    rv.addWidget(self._task_row(t, current, widths, keys, ref, agent_rows))
        rv.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(rows_box)
        scroll.setFrameShape(QScrollArea.NoFrame)
        rows_box.setProperty("surface", "page")
        rows_box.setAttribute(Qt.WA_StyledBackground, True)
        n_rows = len(shown) + (1 if hidden else 0) + (len(hidden) if hidden and "@hid:auxtasks" in st.grp_open else 0)
        scroll.setMinimumHeight(min(n_rows * 30 + 2, 262 if current or self.wide() else n_rows * 30 + 2))
        if not current and not self.wide():
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setFixedHeight(n_rows * 30 + 4)
        bv.addWidget(scroll, 1)
        bv.addWidget(self._footer(keys))
        return box

    def _center(self, w: QWidget | None) -> QWidget:
        c = QWidget()
        h = QHBoxLayout(c)
        h.setContentsMargins(0, 0, 0, 0)
        h.setAlignment(Qt.AlignCenter)
        if w is not None:
            h.addWidget(w)
        return c

    def _task_row(self, t: str, current, widths, keys, ref, _cache) -> TaskRow:
        st, ctx = self.state, self.ctx
        scope = st.scope()
        key = f"aux:{t}"
        spec = spec_for(key)
        work = st.work.chain(key)
        rows = st.work.chain_rows(key, scope, ref)
        known = bool(ref) or work is not None
        hidden = st.task_hidden(t)
        per = [rows_of(C.read_chain(scope, spec, k)) for k in keys] if len(keys) > 1 else []
        heads = [r[0] if r else None for r in per]
        n_p = len({h[0] if h else None for h in heads}) if per else 1
        n_m = len({h[1] if h else None for h in heads}) if per else 1
        n_e = len({(h[2] or "") if h else None for h in heads}) if per else 1
        n_f = len({max(0, len(r) - 1) for r in per}) if per else 1
        if ctx.cust or hidden:
            eye = IconButton("eye_off" if hidden else "eye", ("Show" if hidden else "Hide") + " this task",
                             color="faint" if hidden else "muted")
            eye.clicked.connect(lambda: self.page.toggle_node_path(("auxiliary", t)))
            first = self._center(eye)
        else:
            cb = QCheckBox()
            cb.setToolTip("Check this task to give it a head model together with the other checked tasks")
            cb.setChecked(t in self.page.aux_checked)
            cb.clicked.connect(lambda on: self._check(t, on))
            first = self._center(cb)
        name = QLabel(t.replace("_", "_​"))
        name.setWordWrap(True)
        name.setFont(mono(12 if ctx.narrow else 12.5))
        if hidden:
            f = name.font()
            f.setStrikeOut(True)
            name.setFont(f)
            name.setStyleSheet(f"color: {T['faint']};")
        h = rows[0] if rows else None

        def val(text, mono_font=False, faint=False):
            lab = QLabel(wrap_model(text) if mono_font else text)
            lab.setWordWrap(True)
            if mono_font:
                lab.setFont(mono(12.5))
            if faint:
                lab.setStyleSheet(f"color: {T['faint']};")
            lab.setToolTip(text if not faint else "")
            return lab

        if not known:
            cells = [first, _dot_label(name, 1, ""), QWidget(), QWidget(), QWidget(), QWidget()]
        else:
            prov = val(h[0], faint=False) if h and h[0] else val("not set", faint=True)
            mod = val(h[1], True) if h and h[1] else val(model_text(h[0], h[1]) if h and h[0] == "auto" else "not set",
                                                            faint=True)
            if t in st.model.no_effort:
                eff = val("—", faint=True)
                eff.setToolTip("This task takes no reasoning effort")
            else:
                eff = val(h[2], False) if h and h[2] else val("default" if h else "not set", faint=True)
            fb = val(str(max(0, len(rows) - 1)))
            cells = [first, _dot_label(name, 1, ""), _dot_label(prov, n_p, "values"), _dot_label(mod, n_m, "values"),
                     _dot_label(eff, n_e, "values"), _dot_label(fb, n_f, "fallback counts")]
        row = TaskRow(self, t, cells, widths)
        row.on = t == current
        dirty = st.work.chain_dirty(key, scope)
        row.mark = "edit" if dirty else ("pend" if any(chain_staged(scope, key, k) for k in keys) else "")
        row.picked.connect(self._pick)
        for c in cells:
            dot = getattr(c, "dot", None)
            if dot is not None:
                c.resizeEvent = lambda ev, c=c, dot=dot: dot.move(c.width() - 9, 3)
        return row

    def _footer(self, keys) -> QWidget:
        st = self.state
        f = QWidget()
        h = QHBoxLayout(f)
        h.setContentsMargins(10, 4, 6, 4)
        h.setSpacing(8)
        n = len(self.page.aux_checked)
        narrow = self.ctx.narrow
        cb = btn(("Choose model…" if narrow else f"Choose model for {plural(n, 'checked task') if n else 'checked tasks'}…"),
                 sm=True, tip="Pick one head model for the checked tasks. It becomes an edit here; stage it afterwards.")
        cb.setEnabled(bool(n))
        cb.clicked.connect(self._choose_for_checked)
        h.addWidget(cb)
        db = btn("Default" if narrow else f"Default for {plural(n, 'checked task') if n else 'checked tasks'}", sm=True,
                 tip="Set the head of the checked tasks back to the Hermes default:\nprovider: auto\nmodel: ''\n"
                     "(Hermes chooses the model). It becomes an edit here; stage it afterwards.")
        db.setEnabled(bool(n))
        db.clicked.connect(lambda: self.page.aux_default(self._checked(), "pm"))
        h.addWidget(db)
        scope = st.scope()
        dirty = [t for t in st.model.tasks if st.work.chain_dirty(f"aux:{t}", scope)]
        if dirty:
            h.addWidget(label(plural(len(dirty), "task") + ("" if narrow else " edited,") + " not staged",
                              tone="accent", small=True))
            h.addStretch(1)
            sb = btn("Stage", sm=True, pri=True, tip="Stage every edited task for the agents checked right now")
            sb.setEnabled(bool(keys))
            sb.clicked.connect(lambda: self.page.aux_stage(dirty))
            h.addWidget(sb)
            h.addWidget(label(f"for {plural(len(keys), 'agent')}" if keys else "select agents", tone="muted"))
            one = link("")
            set_only_link(one, st, len(keys))
            one.clicked.connect(lambda: self.page.aux_stage(dirty, only=True))
            h.addWidget(one)
            dl = link("Discard", tip="Throw away the task edits that are not staged")
            dl.clicked.connect(lambda: self.page.aux_discard(dirty))
            h.addWidget(dl)
        else:
            h.addStretch(1)
        f.setMinimumHeight(34)
        return f

    def _detail(self, task: str | None) -> QWidget:
        st = self.state
        w = QWidget()
        w.setProperty("surface", "page")
        w.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        if task is None:
            e = label("Click a task in the table to edit its model chain and its other settings here.",
                      tone="muted", wrap=True)
            e.setContentsMargins(18, 26, 18, 26)
            v.addWidget(e)
            v.addStretch(1)
            return w
        head = QWidget()
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setObjectName("dph")
        head.setStyleSheet(f"QWidget#dph {{ background: {T['head']}; border-bottom: 1px solid {T['line']}; }}")
        hh = QHBoxLayout(head)
        hh.setContentsMargins(12, 0, 6, 0)
        t = QLabel(f"auxiliary<span style='color:{T['faint']}'> › </span><b>{task}</b>")
        t.setTextFormat(Qt.RichText)
        t.setFont(mono())
        hh.addWidget(t)
        hh.addStretch(1)
        x = IconButton("x", "Close this task's panel (edits stay on the page until you stage or discard them)")
        x.clicked.connect(lambda: self.page.aux_select(None))
        hh.addWidget(x)
        head.setFixedHeight(33)
        v.addWidget(head)
        v.addWidget(ChainTable(self.page, f"aux:{task}", self.ctx))
        nodes = st.model.task_nodes(task)
        if nodes:
            ls = st.visible_leaves(nodes)
            g = GroupRow(self, None, 0, f"Other settings of {task}", sum_html(len(ls), st.count_differ(ls)))
            g.setCursor(Qt.ArrowCursor)
            v.addWidget(g)
            v.addWidget(SettingsTable(self.page, nodes, self.ctx, hid_key=f"@hid:aux:{task}", fit=self.wide()))
        v.addStretch(1)
        return w

    # -- actions -------------------------------------------------------------------
    def _pick(self, task: str) -> None:
        self.page.aux_select(task)

    def _check(self, task: str, on: bool) -> None:
        (self.page.aux_checked.add if on else self.page.aux_checked.discard)(task)
        self.page.refresh_aux()

    def _check_all(self, on: bool, shown: list[str]) -> None:
        self.page.aux_checked = set(shown) if on else set()
        self.page.refresh_aux()

    def _checked(self) -> list[str]:
        return sorted(self.page.aux_checked, key=self.state.model.tasks.index)

    def _choose_for_checked(self) -> None:
        self.page.aux_choose(self._checked())

    def row_menu(self, task: str) -> QMenu:
        """Right-click on a task: its head back to the Hermes default (every checked task when it is checked)."""
        checked = self._checked()
        tasks = checked if task in checked and len(checked) > 1 else [task]
        who = plural(len(tasks), "checked task") if len(tasks) > 1 else task
        m = QMenu(self)
        m.setToolTipsVisible(True)
        for text, tip, what in (
                (f"Default (provider auto, model '') · {who}",
                 "Head model: provider: auto and model: '' (Hermes chooses the model). Fallbacks are kept.", "pm"),
                (f"Default (no reasoning effort) · {who}",
                 "Head effort: leaves reasoning_effort out of the file, so Hermes uses its own default.", "e")):
            a = m.addAction(text)
            a.setToolTip(tip + " Stage it afterwards.")
            a.triggered.connect(lambda _=False, what=what: self.page.aux_default(tasks, what))
        m.addSeparator()
        a = m.addAction(f"Edit {task}")
        a.triggered.connect(lambda: self._pick(task))
        return m
