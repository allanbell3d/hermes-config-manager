"""Agents pane: one box per host (collapsed when the app opens), a checkbox per agent.

Checked agents are the ones a Stage applies to.  Clicking a row *focuses* that agent: its values
fill the page when it is checked.  Nothing is checked at startup.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QToolTip,
                               QVBoxLayout, QWidget)

from ..core.agents import Status
from ..services.session import HostState
from .theme import T, mono, sans
from .widgets import clear_layout, Chevron, Clickable, Dot, ElideLabel, Switch, btn, label, link

HOST_DOT = {HostState.OK: "ok", HostState.PARTIAL: "slow", HostState.CONNECTING: "slow", HostState.ERROR: "down",
            HostState.OFFLINE: "down", HostState.DISABLED: "off", HostState.UNKNOWN: "off"}


class AgentRow(Clickable):
    """One agent: a checkbox (a widget) and a painted name, staged dot and optional model line.

    Painting instead of child labels keeps 100+ rows cheap to show, hide and filter.
    """

    toggled = Signal(str, bool)
    focused = Signal(str)
    menu = Signal(str, QPoint)

    def __init__(self, state, key: str, parent=None) -> None:
        super().__init__(parent)
        self.state, self.key = state, key
        self.setCursor(Qt.ArrowCursor)
        self._focus = False
        self._hover = False
        self._name = ""
        self._usable = True
        self._staged = False
        self._model: str | None = None
        self.box = QCheckBox(self)
        self.box.setFocusPolicy(Qt.NoFocus)
        self.box.setToolTip("Checked agents receive the changes you stage. Checking changes nothing in the files.")
        self.box.toggled.connect(lambda on: self.toggled.emit(self.key, on))
        self.box.move(28, 6)
        self.setFixedHeight(26)
        self.clicked.connect(lambda: self.focused.emit(self.key))
        self.right_clicked.connect(lambda pos: self.menu.emit(self.key, pos))
        self.refresh()

    def refresh(self, show_models: bool = False) -> None:
        rec = self.state.session.record(self.key)
        if rec is None:
            return
        self._name = rec.name
        self._usable = rec.usable
        self.box.setEnabled(rec.usable)
        self.box.blockSignals(True)
        self.box.setChecked(self.key in self.state.session.checked)
        self.box.blockSignals(False)
        self._staged = self.state.session.drafts.has_changes(self.key)
        if show_models:
            p, m, e = self.state.head_of(self.key) if rec.usable else (None, None, None)
            text = (m.rsplit("/", 1)[-1] + (f" · {e}" if e else "")) if m else (
                "not loaded" if not rec.usable else "no model")
            if rec.status in (Status.ERROR, Status.INVALID):
                text = "could not be read"
            self._model = text
        else:
            self._model = None
        self.setFixedHeight(42 if self._model is not None else 26)
        self.update()

    def set_focus(self, on: bool) -> None:
        if on != self._focus:
            self._focus = on
            self.update()

    def event(self, ev) -> bool:
        if ev.type() == QEvent.ToolTip:
            if self._staged and ev.pos().x() > self.width() - 22 and ev.pos().y() < 26:
                QToolTip.showText(ev.globalPos(), "Has staged changes: they are listed in Review and not written yet",
                                  self)
            else:
                QToolTip.showText(ev.globalPos(), self._tip(), self)
            return True
        return super().event(ev)

    def _tip(self) -> str:
        rec = self.state.session.record(self.key)
        if rec is None:
            return ""
        if not rec.usable:
            return f"{rec.display}\n{rec.error or rec.status}"

        def f(chain):
            p, m, e = self.state.head_of(self.key, chain)
            return f"{p or '?'} / {m}" + (f" · {e}" if e else "") if m else "not set"

        return (f"{rec.display}\nPrimary: {f('primary')}\nDelegation: {f('delegation')}\n"
                "Click the name to show this agent's values on the page; tick the box to include it when you "
                "stage. Right-click for more.")

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()
        super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        if self._focus or self._hover:
            p.fillRect(self.rect(), T.c("sel") if self._focus else T.c("hover"))
            if self._focus:
                p.fillRect(0, 0, 2, self.height(), T.c("accent"))
        x = 28 + 14 + 8
        right = self.width() - 10 - (15 if self._staged else 0)
        p.setFont(self.font())
        p.setPen(T.c("ink") if self._usable else T.c("faint"))
        fm = p.fontMetrics()
        p.drawText(QRect(x, 0, max(0, right - x), 26), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self._name, Qt.ElideRight, max(0, right - x)))
        if self._staged:
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(T.c("amber_edge"))
            p.drawEllipse(QRectF(self.width() - 17, 9.5, 7, 7))
        if self._model is not None:
            f = mono(11.5)
            p.setFont(f)
            p.setPen(T.c("muted"))
            w = self.width() - 10 - x
            p.drawText(QRect(x, 24, max(0, w), 16), Qt.AlignVCenter | Qt.AlignLeft,
                       p.fontMetrics().elidedText(self._model, Qt.ElideRight, max(0, w)))


class HostBox(QFrame):
    toggled = Signal(str)                 # host name: open/close
    check_all = Signal(str, bool)
    menu = Signal(str, QPoint)

    def __init__(self, state, host, parent=None) -> None:
        super().__init__(parent)
        self.state, self.host = state, host
        self.setObjectName("hbox")
        self.setStyleSheet(f"QFrame#hbox {{ background: {T['page']}; border: 1px solid {T['line']}; "
                           f"border-radius: 6px; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.head = Clickable()
        self.head.setObjectName("hh")
        self.head.setStyleSheet(f"QFrame#hh {{ background: {T['head']}; border: 0; border-radius: 5px; }}")
        self.head.setFixedHeight(32)
        h = QHBoxLayout(self.head)
        h.setContentsMargins(6, 0, 10, 0)
        h.setSpacing(8)
        self.chev = Chevron()
        self.box = QCheckBox()
        self.box.setTristate(True)
        self.box.setFocusPolicy(Qt.NoFocus)
        self.box.setToolTip("Check or uncheck every loaded agent of this host")
        self.box.clicked.connect(self._box_clicked)
        self.dot = Dot()
        self.nm = ElideLabel(host.name)
        self.nm.setFont(sans(13, 600))
        self.ct = QLabel("")
        self.ct.setProperty("tone", "muted")
        self.ct.setProperty("small", True)
        for w in (self.chev, self.box, self.dot):
            h.addWidget(w)
        h.addWidget(self.nm, 1)
        h.addWidget(self.ct)
        self.head.clicked.connect(lambda: self.toggled.emit(self.host.name))
        self.head.right_clicked.connect(lambda pos: self.menu.emit(self.host.name, pos))
        lay.addWidget(self.head)
        self.body = QWidget()
        self.body.setProperty("surface", "page")
        self.bl = QVBoxLayout(self.body)
        self.bl.setContentsMargins(0, 2, 0, 4)
        self.bl.setSpacing(0)
        lay.addWidget(self.body)
        self.rows: dict[str, AgentRow] = {}

    def _box_clicked(self) -> None:
        keys = self.keys()
        all_on = keys and all(k in self.state.session.checked for k in keys)
        self.check_all.emit(self.host.name, not all_on)

    def keys(self) -> list[str]:
        return [k for k, r in self.state.session.agents.items() if r.host == self.host.name and r.usable]

    def refresh_head(self, pending: bool) -> None:
        st = self.state.session.host_status.get(self.host.name)
        state = HostState.DISABLED if not self.host.enabled else (st.state if st else HostState.UNKNOWN)
        self.dot.set_state(HOST_DOT.get(state, "off"))
        self.head.setToolTip(f"{self.host.location}\n{st.message if st else ''}".strip()
                             + "\nClick to open or close this host's agents; right-click for host actions. "
                               "The count is checked / all agents.")
        all_keys = [k for k, r in self.state.session.agents.items() if r.host == self.host.name]
        n = sum(1 for k in all_keys if k in self.state.session.checked)
        if not self.host.enabled:
            self.ct.setText("disabled")
        else:
            self.ct.setText(("<span style='color:%s'>● </span>" % T["amber"] if pending else "") +
                            f"{n}/{len(all_keys)}")
        usable = self.keys()
        self.box.setEnabled(bool(usable) and self.host.enabled)
        self.box.blockSignals(True)
        on = sum(1 for k in usable if k in self.state.session.checked)
        self.box.setCheckState(Qt.Unchecked if on == 0 else Qt.Checked if on == len(usable) else Qt.PartiallyChecked)
        self.box.blockSignals(False)


class AgentsPane(QWidget):
    hosts_requested = Signal()
    agent_menu = Signal(str, QPoint)
    host_menu = Signal(str, QPoint)

    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setProperty("pane", True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.open_hosts: set[str] = state.__dict__.setdefault("open_hosts", set())
        self.show_models = state.settings.value("ui/show_models", "false") == "true"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(12, 10, 6, 8)
        title = label("Agents", bold=True)
        title.setToolTip("Every Hermes config found on your hosts: the main config.yaml and each "
                         "profiles/<name>/config.yaml. One box per host.")
        head.addWidget(title)
        head.addStretch(1)
        self.hosts_btn = btn("Hosts…", sm=True, tip="Add, edit, remove and test the hosts your agents live on")
        self.hosts_btn.clicked.connect(self.hosts_requested.emit)
        head.addWidget(self.hosts_btn)
        lay.addLayout(head)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter agents")
        self.filter.setToolTip("Show only agents whose name contains this text (Ctrl+F). Hidden agents keep their checks.")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._filter)
        fw = QHBoxLayout()
        fw.setContentsMargins(12, 0, 6, 6)
        fw.addWidget(self.filter)
        lay.addLayout(fw)
        links = QHBoxLayout()
        links.setContentsMargins(12, 0, 6, 8)
        links.setSpacing(14)
        for text, fn, tip in (("All", self.sel_all, "Check every agent shown"),
                              ("None", self.sel_none, "Uncheck every agent shown"),
                              ("Invert", self.sel_inv, "Swap checked and unchecked")):
            b = link(text, tip=tip)
            b.setFont(sans(12))
            b.clicked.connect(fn)
            links.addWidget(b)
        links.addStretch(1)
        self.models_sw = Switch("Models", "Models", small=True)
        self.models_sw.setToolTip("Show each agent's primary model and effort under its name (display only)")
        self.models_sw.set_state(self.show_models)
        self.models_sw.clicked.connect(self._toggle_models)
        links.addWidget(self.models_sw)
        lay.addLayout(links)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T['soft']};")
        lay.addWidget(sep)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.inner.setProperty("pane", True)
        self.il = QVBoxLayout(self.inner)
        self.il.setContentsMargins(8, 6, 3, 8)
        self.il.setSpacing(6)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        self.boxes: dict[str, HostBox] = {}
        self.empty = label("", tone="muted", wrap=True)
        self.empty.setContentsMargins(6, 14, 6, 14)
        s = state.session
        s.agents_changed.connect(self.rebuild)
        s.hosts_changed.connect(self.rebuild)
        s.agent_updated.connect(self._agent_updated)
        s.checked_changed.connect(self.refresh_checks)
        s.viewed_changed.connect(self._focus_changed)
        s.drafts_changed.connect(self.refresh_rows)
        s.host_status_changed.connect(self._host_status)
        self._upd = QTimer(self)
        self._upd.setSingleShot(True)
        self._upd.setInterval(80)
        self._upd.timeout.connect(self._flush_updates)
        self._dirty_rows: set[str] = set()
        self.rebuild()

    # -- building ------------------------------------------------------------------
    def rebuild(self) -> None:
        self.inner.setUpdatesEnabled(False)
        self.empty.setParent(None)
        clear_layout(self.il)
        self.boxes.clear()
        s = self.state.session
        for host in s.hosts:
            box = HostBox(self.state, host)
            box.toggled.connect(self._toggle_host)
            box.check_all.connect(self._check_host)
            box.menu.connect(self.host_menu.emit)
            for key, rec in s.agents.items():
                if rec.host != host.name:
                    continue
                row = AgentRow(self.state, key)
                row.toggled.connect(self._row_toggled)
                row.focused.connect(self._row_focused)
                row.menu.connect(self.agent_menu.emit)
                row.set_focus(key == s.viewed)
                box.rows[key] = row
                box.bl.addWidget(row)
            self.boxes[host.name] = box
            self.il.addWidget(box)
        self.il.addWidget(self.empty)
        self.il.addStretch(1)
        self._filter()
        self.inner.setUpdatesEnabled(True)

    def _filter(self, *_args) -> None:
        self.inner.setUpdatesEnabled(False)
        try:
            self._apply_filter()
        finally:
            self.inner.setUpdatesEnabled(True)

    def _apply_filter(self) -> None:
        q = self.filter.text().strip().casefold()
        s = self.state.session
        shown_any = False
        for name, box in self.boxes.items():
            visible = 0
            for key, row in box.rows.items():
                rec = s.record(key)
                ok = not q or q in f"{rec.name} {rec.host}".casefold()
                row.setHidden(not ok)
                visible += ok
            open_ = box.host.enabled and (bool(q) or name in self.open_hosts)
            was_open = not box.body.isHidden()
            box.body.setHidden(not (open_ and bool(box.rows)))
            box.chev.set_open(open_)
            box.setHidden(bool(q) and visible == 0)
            if open_ and (not was_open or getattr(box, "models_shown", None) != self.show_models):
                box.models_shown = self.show_models
                for row in box.rows.values():
                    row.refresh(self.show_models)
            box.refresh_head(any(s.drafts.has_changes(k) for k in box.rows))
            shown_any = shown_any or not box.isHidden()
        if not s.hosts:
            self.empty.setText("No hosts yet. Use Hosts… to add the folder, share or SSH server where your "
                               "Hermes agents live.")
        elif not shown_any:
            self.empty.setText(f"No agent matches “{self.filter.text()}”.")
        self.empty.setHidden(bool(s.hosts) and shown_any)

    def visible_keys(self) -> list[str]:
        q = self.filter.text().strip().casefold()
        out = []
        for box in self.boxes.values():
            if not box.host.enabled:
                continue
            for key in box.rows:
                rec = self.state.session.record(key)
                if rec.usable and (not q or q in f"{rec.name} {rec.host}".casefold()):
                    out.append(key)
        return out

    # -- actions -----------------------------------------------------------------
    def sel_all(self) -> None:
        self.state.session.set_checked(self.visible_keys(), True)

    def sel_none(self) -> None:
        self.state.session.set_checked(self.visible_keys(), False)

    def sel_inv(self) -> None:
        s = self.state.session
        keys = self.visible_keys()
        s.replace_checked([k for k in s.checked if k not in keys] + [k for k in keys if k not in s.checked])

    def _toggle_host(self, name: str) -> None:
        if name in self.open_hosts:
            self.open_hosts.discard(name)
        else:
            self.open_hosts.add(name)
        self._filter()

    def _check_host(self, name: str, on: bool) -> None:
        box = self.boxes.get(name)
        if box:
            self.state.session.set_checked(box.keys(), on)

    def _row_toggled(self, key: str, on: bool) -> None:
        self.state.session.set_checked([key], on)

    def _row_focused(self, key: str) -> None:
        self.state.session.set_viewed(key)

    def _toggle_models(self) -> None:
        self.show_models = not self.show_models
        self.models_sw.set_state(self.show_models)
        self.state.settings.setValue("ui/show_models", "true" if self.show_models else "false")
        self._filter()

    # -- updates -----------------------------------------------------------------
    def refresh_checks(self) -> None:
        for box in self.boxes.values():
            for row in box.rows.values():
                if not row.isHidden():
                    row.box.blockSignals(True)
                    row.box.setChecked(row.key in self.state.session.checked)
                    row.box.blockSignals(False)
            box.refresh_head(any(self.state.session.drafts.has_changes(k) for k in box.rows))

    def refresh_rows(self, keys) -> None:
        self._dirty_rows |= set(keys)
        self._upd.start()

    def _agent_updated(self, key: str) -> None:
        self.refresh_rows({key})

    def _flush_updates(self) -> None:
        keys, self._dirty_rows = self._dirty_rows, set()
        for box in self.boxes.values():
            touched = False
            for key in keys:
                row = box.rows.get(key)
                if row is not None:
                    touched = True
                    if not box.body.isHidden():
                        row.refresh(self.show_models)
            if touched:
                box.refresh_head(any(self.state.session.drafts.has_changes(k) for k in box.rows))

    def _focus_changed(self, key: str) -> None:
        for box in self.boxes.values():
            for k, row in box.rows.items():
                row.set_focus(k == key)

    def _host_status(self, name: str) -> None:
        box = self.boxes.get(name)
        if box:
            box.refresh_head(any(self.state.session.drafts.has_changes(k) for k in box.rows))
