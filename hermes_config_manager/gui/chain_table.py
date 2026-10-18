"""The model chain table: Role | Provider | Model | Effort | ↑ ↓ ×.

The table is a working copy.  It starts from the reference agent and never changes when agents
are checked or unchecked; a small amber dot marks a cell where the checked agents differ (click
it to see who has what).  Stage writes exactly the edited cells (see services.work).
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QGridLayout, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton,
                               QVBoxLayout, QWidget)

from ..services import chains as C
from ..services.work import chain_staged, rows_of, spec_for, unstage_chain
from .theme import T, mono, sans
from .widgets import (set_only_link, tip_html, agent_names, clear_layout, IconButton, Popover, btn, label, link,
                      plural)

ZW = "​"


def chain_title(key: str) -> str:
    return "model" if key == "primary" else "delegation" if key == "delegation" else "auxiliary › " + key[4:]


def model_text(provider: str | None, model: str | None) -> str:
    """What a model cell shows: the id, 'auto · Hermes chooses' for provider auto + model '', else a prompt."""
    return model or ("auto · Hermes chooses" if provider == "auto" else "choose model")


def value_word(value: str | None, col: int) -> str:
    """A provider (0), model (1) or effort (2) value as 'who has what' shows it; None = no entry at all."""
    if value is None:
        return "no entry"
    if value == "":
        return "'' (Hermes chooses)" if col == 1 else "not set"
    return value


def wrap_model(text: str) -> str:
    return (text or "").replace("/", "/" + ZW).replace("-", "-" + ZW)


class DiffDot(QToolButton):
    """The amber dot in the corner of a cell whose values differ across the checked agents."""

    def __init__(self, tip: str, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QToolButton { border: 0; background: transparent; }")

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(T.c("amber_bg"))
            p.drawEllipse(1, 1, 12, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(T.c("amber_edge"))
        p.drawEllipse(5, 3, 6, 6)


class ModelCell(QLabel):
    clicked = Signal()

    def __init__(self, text: str, placeholder: bool = False) -> None:
        super().__init__(wrap_model(text) if not placeholder else text)
        self.setWordWrap(True)
        self.setFont(sans(13) if placeholder else mono())
        if placeholder:
            self.setStyleSheet(f"color: {T['faint']};")
        self.setCursor(Qt.PointingHandCursor)
        self.setContentsMargins(8, 4, 8, 4)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()

    def enterEvent(self, ev) -> None:
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), T.c("accent_bg"))
        self.setPalette(pal)

    def leaveEvent(self, ev) -> None:
        self.setAutoFillBackground(False)


class Cell(QWidget):
    """A table cell: content + optional diff dot; painted 'edited' when the user changed it."""

    def __init__(self, content: QWidget | None = None, edited: bool = False, dot: DiffDot | None = None,
                 at: tuple[int, int] | None = None) -> None:
        super().__init__()
        self.edited = edited
        self.at = at                # (row, column) for the right-click menu's Default
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if content is not None:
            lay.addWidget(content, 1)
        self.dot = dot
        if dot is not None:
            dot.setParent(self)
        self.setMinimumHeight(31)

    def resizeEvent(self, ev) -> None:
        if self.dot is not None:
            self.dot.move(self.width() - 14, 0)
            self.dot.raise_()

    def paintEvent(self, ev) -> None:
        if self.edited:
            p = QPainter(self)
            p.fillRect(self.rect(), T.c("accent_bg"))
            p.fillRect(0, self.height() - 2, self.width(), 2, T.c("accent"))


class GridLines(QWidget):
    """The table's lines, painted above the cells (cells and editors may paint their own background)."""

    def __init__(self, box) -> None:
        super().__init__(box)
        self.box = box
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, ev) -> None:
        # (QGridLayout.cellRect is not reliable here: the cells' own geometry is used)
        box = self.box
        p = QPainter(self)
        g = box.grid

        def rect(r: int, c: int) -> QRect | None:
            it = g.itemAtPosition(r, c)
            return it.geometry() if it is not None else None

        for r in range(g.rowCount()):
            first = rect(r, 0)
            if first is None or first.height() <= 0:
                continue
            top, y = first.top(), first.bottom()
            p.setPen(QPen(T.c("line") if r < box.header_rows else T.c("soft"), 1))
            p.drawLine(0, y, self.width(), y)
            if r in box.span_rows:
                continue
            p.setPen(QPen(T.c("soft"), 1))
            for c in range(1, g.columnCount()):
                cr = rect(r, c)
                if cr is not None and cr.left() > first.left():
                    p.drawLine(cr.left(), top, cr.left(), y)
            if r in box.pend_rows:
                p.fillRect(QRect(0, top, 3, first.height()), T.c("amber_edge"))


class GridBox(QWidget):
    """A grid of cells with table lines (drawn by a transparent layer above the cells)."""

    def __init__(self) -> None:
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(0)
        self.grid.setVerticalSpacing(0)
        self.pend_rows: set[int] = set()
        self.header_rows = 1
        self.span_rows: set[int] = set()
        self.lines = GridLines(self)

    def resizeEvent(self, ev) -> None:
        self.lines.setGeometry(self.rect())
        self.lines.raise_()

    def showEvent(self, ev) -> None:
        self.lines.setGeometry(self.rect())
        self.lines.raise_()


class ChainTable(QWidget):
    def __init__(self, page, key: str, ctx) -> None:
        super().__init__()
        self.page, self.key, self.ctx = page, key, ctx
        self.state = ctx.state
        self.spec = spec_for(key)
        task = self.spec.task
        self.no_effort = bool(task) and task in self.state.model.no_effort
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(0)
        self.build()

    # -- data ------------------------------------------------------------------------
    def shown(self) -> list[list]:
        return self.state.work.chain_rows(self.key, self.state.scope(), self.state.ref_key())

    def agent_rows(self) -> list[tuple[str, list[list]]]:
        scope = self.state.scope()
        return [(k, rows_of(C.read_chain(scope, self.spec, k))) for k in scope.usable_keys]

    def providers(self, rows) -> list[str]:
        out = list(self.state.catalog.providers())
        for r in rows:
            if r[0] and r[0] not in out:
                out.append(r[0])
        for _k, rr in self._agents or []:
            for r in rr:
                if r[0] and r[0] not in out:
                    out.append(r[0])
        return sorted(set(out), key=str.casefold)

    # -- build -----------------------------------------------------------------------
    def build(self) -> None:
        clear_layout(self.v)
        st = self.state
        scope = st.scope()
        keys = scope.usable_keys
        work = st.work.chain(self.key)
        rows = self.shown()
        self._agents = self.agent_rows() if len(keys) > 1 or keys else []
        provs = self.providers(rows)
        efforts = st.model.efforts
        fm = QFontMetrics(sans(13))
        w_role = fm.horizontalAdvance("Fallback 9") + 18
        in_use = {r[0] for r in rows if r[0]} | {r[0] for _k, rr in self._agents for r in rr if r[0]}
        w_prov = min(170, max([fm.horizontalAdvance(p) for p in in_use | {"not set"}]) + 42)
        w_eff = max(fm.horizontalAdvance(e) for e in efforts + ["default"]) + 32
        box = GridBox()
        g = box.grid
        g.setColumnMinimumWidth(0, w_role)
        g.setColumnMinimumWidth(1, w_prov)
        g.setColumnMinimumWidth(3, w_eff)
        g.setColumnMinimumWidth(4, 56)
        g.setColumnStretch(2, 1)
        heads = ["Role", "Provider", "Model", "Effort", ""]
        for c, text in enumerate(heads):
            cell = QWidget()
            h = QHBoxLayout(cell)
            h.setContentsMargins(8, 0, 8, 0)
            head = label(text, tone="muted", small=True)
            head.setToolTip(self._head_tip(c))
            h.addWidget(head)
            if c == 2 and work is None and st.ref_name():
                ref = label(f"from {st.ref_name()}", tone="faint", small=True)
                ref.setToolTip(f"An unedited table shows the chain of {st.ref_name()}: the focused agent if it is "
                               "checked, otherwise the first agent you checked")
                h.addWidget(ref)
            h.addStretch(1)
            cell.setFixedHeight(27)
            g.addWidget(cell, 0, c)
        r_i = 1
        per_pos = self._per_position(rows)
        any_pend = work is None and any(chain_staged(scope, self.key, k) for k in keys)
        if not rows:
            # no entry here; the dots still say when other checked agents have one (click: who has what)
            removed = bool(work) and work.removed
            g.addWidget(self._role("Head"), r_i, 0)
            cb = self._prov_combo(provs, None, 0)
            g.addWidget(Cell(cb, removed, self._dot(per_pos, 0, 0, "Provider"), at=(0, 1)), r_i, 1)
            mc = ModelCell("entry removed · Hermes uses its default" if removed else "choose model", placeholder=True)
            mc.setToolTip("Stage it to remove this entry from the files of the checked agents; pick a model to "
                          "keep one instead." if removed else
                          "No entry in the file of the agent this table shows. Pick a model to add one.")
            mc.clicked.connect(lambda: self.page.pick_model(self.key, 0, None))
            g.addWidget(Cell(mc, removed, self._dot(per_pos, 0, 1, "Model"), at=(0, 2)), r_i, 2)
            g.addWidget(Cell(None, removed, None if self.no_effort else self._dot(per_pos, 0, 2, "Reasoning effort")),
                        r_i, 3)
            g.addWidget(Cell(), r_i, 4)
            r_i += 1
        for i, r in enumerate(rows):
            role = "Head" if i == 0 else f"Fallback {i}"
            ed_pm = bool(work) and (work.fb if i else work.pm)
            ed_e = bool(work) and (work.fb if i else work.e)
            g.addWidget(self._role(role), r_i, 0)
            pc = self._prov_combo(provs, r[0], i)
            pc.setToolTip(self._cell_tip(i, 0, r, ed_pm, per_pos))
            g.addWidget(Cell(pc, ed_pm, self._dot(per_pos, i, 0, "Provider"), at=(i, 1)), r_i, 1)
            mc = ModelCell(model_text(r[0], r[1]), placeholder=not r[1])
            mc.setToolTip(self._cell_tip(i, 1, r, ed_pm, per_pos))
            mc.clicked.connect(lambda _=False, i=i: self.page.pick_model(self.key, i, None))
            g.addWidget(Cell(mc, ed_pm, self._dot(per_pos, i, 1, "Model"), at=(i, 2)), r_i, 2)
            if self.no_effort:
                dash = label("—", tone="faint")
                dash.setContentsMargins(10, 0, 10, 0)
                dash.setToolTip("This task takes no reasoning effort")
                g.addWidget(Cell(dash), r_i, 3)
            else:
                ec = self._eff_combo(efforts, r[2], i)
                ec.setToolTip(self._cell_tip(i, 2, r, ed_e, per_pos))
                g.addWidget(Cell(ec, ed_e, self._dot(per_pos, i, 2, "Reasoning effort"), at=(i, 3)), r_i, 3)
            g.addWidget(self._actions(i, len(rows)), r_i, 4)
            if any_pend:
                box.pend_rows.add(r_i)
            r_i += 1
        self.v.addWidget(box)
        self.v.addWidget(self._footer(rows, work, keys))

    def _head_tip(self, c: int) -> str:
        where = chain_title(self.key)
        if c == 0:
            return ("Head is the model used first. Fallbacks are tried in order when the head fails "
                    "(error, rate limit, provider down).")
        if c == 1:
            return ("The provider that serves the model (written to provider). Choosing another provider opens the "
                    "model picker for it; the head's base_url and api_mode are removed because they belonged to "
                    "the previous provider.")
        if c == 2:
            return f"The model id, exactly as written in config.yaml ({where}). Click a model to pick another one."
        if c == 3:
            if self.no_effort:
                return "This task takes no reasoning effort."
            if self.key == "primary":
                return ("How hard the model thinks before answering. 'not set' leaves the setting out, so Hermes uses "
                        "its own default (medium). "
                        "The head's effort is saved as agent › reasoning_effort, which is "
                        "what Hermes reads for the main model.")
            return ("How hard the model thinks before answering. 'not set' leaves the setting out, so Hermes uses "
                    "its own default.")
        return ("Move a row up or down, or remove it from the chain."
                + (" Removing the only row removes the task's model entry (Hermes then uses its default)."
                   if self.spec.task else ""))

    def _role(self, text: str) -> QWidget:
        lab = label(text, tone="muted")
        lab.setToolTip("The model used first" if text == "Head"
                       else f"Tried when everything above it has failed ({text.lower()})")
        lab.setContentsMargins(10, 0, 4, 0)
        lab.setMinimumHeight(31)
        return lab

    def _prov_combo(self, provs: list[str], cur: str | None, i: int) -> QComboBox:
        cb = QComboBox()
        cb.setProperty("flat", True)
        cb.setFocusPolicy(Qt.StrongFocus)
        cb.setToolTip("Provider of this row. Choosing another one opens the model picker for that provider.")
        cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(4)
        if cur is None:
            cb.addItem("provider")
            cb.model().item(0).setEnabled(False)
            cb.setStyleSheet(f"color: {T['faint']};")
        items = list(provs)
        if cur and cur not in items:
            items.insert(0, cur)
        cb.addItems(items)
        cb.setCurrentIndex(0 if cur is None else items.index(cur))
        cb.activated.connect(lambda _x, cb=cb, i=i: self._prov_chosen(i, cb.currentText()))
        cb.wheelEvent = lambda ev: ev.ignore()          # never change a value by scrolling past it
        return cb

    def _prov_chosen(self, i: int, prov: str) -> None:
        rows = self.shown()
        if i < len(rows) and rows[i][0] == prov:
            return
        self.page.pick_model(self.key, i, prov)

    def _eff_combo(self, efforts: list[str], cur: str | None, i: int) -> QComboBox:
        cb = QComboBox()
        cb.setProperty("flat", True)
        cb.setToolTip("Reasoning effort of this row. 'not set' removes it from the file, so Hermes uses its own "
                      "default" + (" (saved as agent › reasoning_effort)." if self.key == "primary" and i == 0 else "."))
        items = ["not set"] + list(efforts)
        if cur and cur not in items:
            items.append(cur)
        cb.addItems(items)
        cb.setItemData(0, "Remove reasoning_effort from the file: Hermes uses its own default", Qt.ToolTipRole)
        cb.setCurrentIndex(items.index(cur) if cur else 0)
        cb.activated.connect(lambda _x, cb=cb, i=i: self.page.set_effort(self.key, i, None if cb.currentIndex() == 0
                                                                           else cb.currentText()))
        cb.wheelEvent = lambda ev: ev.ignore()
        return cb

    def _actions(self, i: int, n: int) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(3, 0, 3, 0)
        h.setSpacing(0)
        whole = n == 1 and bool(self.spec.task)          # an auxiliary task's only model: remove the entry
        rm_tip = ("Remove this task's model entry from the file (Hermes then uses its own default). Stage it "
                  "afterwards." if whole else "Remove from the chain")
        for name, tip, enabled, fn in (("up", "Move up", i > 0, lambda: self.page.chain_move(self.key, i, -1)),
                                       ("down", "Move down", i < n - 1, lambda: self.page.chain_move(self.key, i, 1)),
                                       ("x", rm_tip, n > 1 or whole, lambda: self.page.chain_remove(self.key, i))):
            b = IconButton(name, tip, size=18, icon_size=12)
            b.setFixedSize(18, 24)
            b.setEnabled(enabled)
            b.clicked.connect(fn)
            h.addWidget(b)
        return w

    def _per_position(self, rows) -> list[list[dict]]:
        """Per position, per column (provider, model, effort): value -> agent keys."""
        out: list[list[dict]] = []
        n = max([len(r) for _k, r in self._agents] + [len(rows)]) if self._agents else len(rows)
        for i in range(n):
            cols = [{}, {}, {}]
            for k, rr in self._agents:
                x = rr[i] if i < len(rr) else None
                for c in range(3):
                    v = (x[c] or "") if x is not None else None
                    cols[c].setdefault(v, []).append(k)
            out.append(cols)
        return out

    def _cell_tip(self, i: int, c: int, row, edited: bool, per_pos) -> str:
        """Tooltip of one chain cell: its value and where it comes from, model facts, who has what."""
        st = self.state
        role = "Head" if i == 0 else f"Fallback {i}"
        what = ("Provider", "Model", "Reasoning effort")[c]
        value = row[c]
        lines = [f"{chain_title(self.key)} · {role} · {what}"]
        if c == 1 and row[0] and row[1]:
            info = st.catalog.get(row[0], row[1])
            if info is not None:
                from .dialogs import _model_tip

                lines += _model_tip(info).split("\n")[1:]
        lines.append("")
        shown_v = value or ("not set (Hermes default)" if c == 2 else
                            "'' (provider auto: Hermes chooses)" if c == 1 and value == "" and row[0] == "auto"
                            else "not set")
        if edited:
            lines.append(f"Your edit: {shown_v} — not staged yet")
        else:
            lines.append(f"{st.ref_name() or 'Shown'}: {shown_v}")
        cols = per_pos[i] if i < len(per_pos) else None
        if cols is not None and len(self._agents) > 1:
            vals = cols[c]
            if len(vals) > 1:
                lines.append(f"The {len(self._agents)} checked agents differ:")
                for v, ks in sorted(vals.items(), key=lambda kv: -len(kv[1]))[:6]:
                    names = agent_names(st, ks)
                    lines.append(f"  {value_word(v, c)}: " + ", ".join(names[:8])
                                 + (f" +{len(names) - 8} more" if len(names) > 8 else ""))
            else:
                lines.append(f"All {len(self._agents)} checked agents have the same {what.lower()} here.")
        if c == 0:
            lines.append("Choosing another provider opens the model picker for it."
                         + (" The head's base_url and api_mode are removed (they belonged to the old provider)."
                            if i == 0 else ""))
            lines.append("Right-click › Default sets provider: auto and model: ''.")
        elif c == 1:
            lines.append("Click to pick another model. Right-click › Default sets provider: auto and model: ''.")
        else:
            lines.append("'not set' leaves the setting out of the file."
                         + (" Saved as agent › reasoning_effort, which Hermes reads for the main model."
                            if self.key == "primary" and i == 0 else ""))
        return tip_html(lines)

    def _dot(self, per_pos, i: int, c: int, what: str) -> DiffDot | None:
        if len(self._agents) < 2 or i >= len(per_pos) or len(per_pos[i][c]) < 2:
            return None
        d = DiffDot(f"{len(per_pos[i][c])} values across the checked agents. Click to see who has what.")
        d.clicked.connect(lambda: self._show_values(d, per_pos[i][c], i, c, what))
        return d

    def _show_values(self, anchor, values: dict, i: int, c: int, what: str) -> None:
        pop = Popover(self)
        v = QVBoxLayout(pop)
        v.setContentsMargins(0, 6, 0, 6)
        v.setSpacing(0)
        t = label(f"{chain_title(self.key)} · {'Head' if i == 0 else f'Fallback {i}'} · what the "
                  f"{plural(len(self._agents), 'checked agent')} have now", tone="muted", small=True)
        t.setContentsMargins(12, 2, 12, 6)
        v.addWidget(t)
        grid = QGridLayout()
        grid.setContentsMargins(12, 0, 12, 4)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.addWidget(label(what, tone="muted", small=True), 0, 0)
        grid.addWidget(label("Agents", tone="muted", small=True), 0, 1)
        for r, (value, keys) in enumerate(sorted(values.items(), key=lambda kv: -len(kv[1])), start=1):
            text = value_word(value, c) + (" (not in the file)" if value is None else "")
            lab = label(text, tone="faint" if value in (None, "") else None)
            if value not in (None, ""):
                lab.setFont(mono())
            grid.addWidget(lab, r, 0, Qt.AlignTop)
            names = label(", ".join(agent_names(self.state, keys)), wrap=True)
            names.setMaximumWidth(360)
            grid.addWidget(names, r, 1, Qt.AlignTop)
            sel = link("select these", tip="Check exactly these agents")
            sel.clicked.connect(lambda _=False, ks=list(keys): (pop.close(), self.page.check_exactly(ks)))
            grid.addWidget(sel, r, 2, Qt.AlignTop)
        v.addLayout(grid)
        pop.show_at(anchor)

    def _footer(self, rows, work, keys) -> QWidget:
        st = self.state
        f = QWidget()
        h = QHBoxLayout(f)
        h.setContentsMargins(10, 4, 6, 4)
        h.setSpacing(8)
        if rows:
            add = link("+ Add fallback", tip="Add a model at the end of the chain; it is tried when all the ones "
                                            "above it fail")
            add.clicked.connect(lambda: self.page.pick_model(self.key, len(self.shown()), None, add=True))
            h.addWidget(add)
        scope = st.scope()
        dirty = st.work.chain_dirty(self.key, scope)
        staged = 0 if dirty else sum(1 for k in keys if chain_staged(scope, self.key, k))
        if dirty:
            t = label("edited, not staged", tone="accent", small=True)
            t.setToolTip("Your chain is only on this page so far. Stage it to put it in Review for the checked agents.")
            h.addWidget(t)
            u = IconButton("undo", "Discard your edits to this chain")
            u.clicked.connect(lambda: self.page.chain_discard(self.key))
            h.addWidget(u)
        elif staged:
            t = label(f"staged · {plural(staged, 'agent')}", tone="amber", small=True)
            t.setToolTip("Staged and listed in Review. Nothing is written yet.")
            h.addWidget(t)
            u = IconButton("undo", "Unstage this chain for the checked agents")
            u.clicked.connect(lambda: self.page.chain_unstage(self.key))
            h.addWidget(u)
        h.addStretch(1)
        sb = btn("Stage", sm=True, pri=True, tip="Stages the cells you edited, for the agents checked right now. "
                                                 "Nothing is written yet.")
        sb.setEnabled(dirty and bool(keys))
        sb.clicked.connect(lambda: self.page.chain_stage(self.key))
        h.addWidget(sb)
        h.addWidget(label(f"for {plural(len(keys), 'agent')}" if keys else "select agents", tone="muted"))
        if dirty:
            one = link("")
            set_only_link(one, st, len(keys))
            one.clicked.connect(lambda: self.page.chain_stage(self.key, only=True))
            h.addWidget(one)
        md = btn("My defaults", sm=True, tip="Chains you saved yourself (stored in this app, not in Hermes). "
                                             "Use fills this table; you can also save the table as a new default, "
                                             "edit, import and export them.")
        md.clicked.connect(lambda: self.page.open_defaults(self, md))
        self.md_btn = md
        h.addWidget(md)
        f.setMinimumHeight(34)
        return f

    def refresh(self) -> None:
        self.build()

    # -- right-click menu --------------------------------------------------------------
    def contextMenuEvent(self, ev) -> None:
        w = self.childAt(ev.pos())
        while w is not None and w is not self and getattr(w, "at", None) is None:
            w = w.parentWidget()
        self.context_menu(getattr(w, "at", None)).exec(ev.globalPos())

    def context_menu(self, at: tuple[int, int] | None = None) -> QMenu:
        """Chain actions; Stage always takes every edited cell of the chain (it writes only those cells).

        ``at`` is the (row, column) right-clicked: the menu then starts with that cell's Default.
        """
        st, page, key = self.state, self.page, self.key
        scope = st.scope()
        keys = scope.usable_keys
        dirty = st.work.chain_dirty(key, scope)
        staged = not dirty and any(chain_staged(scope, key, k) for k in keys)
        ref = st.ref_name()
        m = QMenu(self)
        m.setToolTipsVisible(True)

        def add(text: str, tip: str, fn, enabled: bool = True) -> None:
            a = m.addAction(text)
            a.setToolTip(tip)
            a.setEnabled(enabled)
            a.triggered.connect(fn)

        if at is not None:
            i, col = at
            role = "Head" if i == 0 else f"Fallback {i}"
            if col == 3:
                add("Default (no reasoning effort)", f"{role}: leaves reasoning_effort out of the file, so Hermes uses "
                    "its own default. Stage it afterwards.", lambda: page.chain_default(key, i, col))
            else:
                add("Default (provider auto, model '')", f"{role}: provider: auto and model: '' (Hermes chooses the "
                    "model). Stage it afterwards.", lambda: page.chain_default(key, i, col))
            m.addSeparator()
        add(f"Stage this chain for {plural(len(keys), 'agent')}" if keys else "Stage this chain",
            "Puts the cells you edited in Review for the agents checked now; their other cells stay as they are.",
            lambda: page.chain_stage(key), dirty and bool(keys))
        add(f"Stage this chain for {ref} only" if ref else "Stage this chain for one agent only",
            "Puts your edits in Review only for the agent whose chain the table shows.",
            lambda: page.chain_stage(key, only=True), dirty and len(keys) > 1)
        m.addSeparator()
        add("Discard my edits to this chain", "Throws away your edits; the table shows the current chain again.",
            lambda: page.chain_discard(key), dirty)
        add("Unstage", "Takes this chain out of Review for the checked agents.",
            lambda: page.chain_unstage(key), staged)
        m.addSeparator()
        add("Save as My default…", "Saves the chain shown in this table under a name, to use later on any agent "
            "(stored in this app, not in Hermes).",
            lambda: page.open_defaults(self, self.md_btn, saving=True), bool(self.shown()))
        return m


__all__ = ["ChainTable", "chain_title", "model_text", "unstage_chain"]
