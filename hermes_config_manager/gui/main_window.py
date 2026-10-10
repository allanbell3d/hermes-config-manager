"""The main window: top bar, Agents | Menu | Page, status bar."""

from __future__ import annotations

import time

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QSplitter,
                               QStackedWidget, QVBoxLayout, QWidget)

from .. import __version__
from ..services.session import HostState
from .agent_actions import AgentActions
from .agents_pane import HOST_DOT, AgentsPane
from .counts import DiffCounter
from .menu_pane import MenuPane
from .page import PageView
from .search import SearchBox
from .theme import T, apply_theme, sans
from .widgets import clear_layout, Dot, btn, label, link, plural, set_prop


class MainWindow(QMainWindow):
    def __init__(self, state) -> None:
        super().__init__()
        self.state = state
        self.setWindowTitle(f"Hermes Config Manager v{__version__}")
        self.counter = DiffCounter(state, self)
        self.actions_ = AgentActions(self)
        self.pages: dict[str, PageView] = {}
        self.loaded_at: float | None = None
        self._build()
        s = state.session
        state.layout_changed.connect(self._layout_changed)
        state.model_changed.connect(self._layout_changed)
        state.scope_changed.connect(self._values_changed)
        state.values_changed.connect(self._values_changed)
        state.work_changed.connect(self._work_changed)
        state.columns_changed.connect(self._columns_changed)
        state.toast.connect(self.toast)
        self.counter.changed.connect(self._counts_changed)
        s.checked_changed.connect(self._scope_label)
        s.agents_changed.connect(self._scope_label)
        s.host_status_changed.connect(lambda _n: self._status())
        s.hosts_changed.connect(self._status)
        s.agents_changed.connect(self._status)
        s.refresh_finished.connect(self._refreshed)
        s.activity.connect(self._activity)
        s.drafts_changed.connect(lambda _k: self._review_timer.start())
        self._review_timer = QTimer(self)
        self._review_timer.setSingleShot(True)
        self._review_timer.setInterval(150)
        self._review_timer.timeout.connect(self._review_count)
        self._fresh_timer = QTimer(self)
        self._fresh_timer.setInterval(10000)
        self._fresh_timer.timeout.connect(self._fresh)
        self._fresh_timer.start()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(5000)
        self._toast_timer.timeout.connect(lambda: self.toast_label.setText(""))
        self._warm: list[str] = []
        self._warm_timer = QTimer(self)
        self._warm_timer.setInterval(60)
        self._warm_timer.timeout.connect(self._warm_one)
        self._shortcuts()
        self._restore_geometry()
        self._scope_label()
        self._status()
        self.show_page(None)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("app")
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # top bar
        top = QWidget()
        top.setProperty("pane", True)
        top.setAttribute(Qt.WA_StyledBackground, True)
        top.setFixedHeight(48)
        top.setObjectName("top")
        top.setStyleSheet(f"QWidget#top {{ background: {T['pane']}; border-bottom: 1px solid {T['line']}; }}")
        th = QHBoxLayout(top)
        th.setContentsMargins(14, 0, 14, 0)
        th.setSpacing(14)
        brand = QLabel("Hermes Config Manager")
        brand.setFont(sans(14, 700))
        brand.setToolTip(f"Hermes Config Manager v{__version__}: view and change the config.yaml of many Hermes agents at once. "
                         "Edit, Stage, Review, Write config: nothing is written before Write config.")
        th.addWidget(brand)
        self.scope_lbl = QLabel("")
        self.scope_lbl.setTextFormat(Qt.RichText)
        self.scope_lbl.setStyleSheet(f"color: {T['muted']};")
        self.scope_lbl.setToolTip("How many agents are checked (they receive what you stage) and on how many hosts")
        th.addWidget(self.scope_lbl, 1)
        self.search = SearchBox(self.state)
        self.search.chosen.connect(self.go_to)
        self.search.pinned.connect(self._pinned)
        th.addWidget(self.search, 1)
        self.review_btn = btn("Review (0)", tip="Everything you staged, grouped by change, with a diff per agent. "
                                                "Write config there writes it to the files. Shortcut: Ctrl+S.")
        self.review_btn.clicked.connect(self.open_review)
        th.addWidget(self.review_btn)
        self.theme_btn = btn("Light" if T.dark else "Dark", tip="Switch between the light and dark theme (remembered)")
        self.theme_btn.clicked.connect(self.toggle_theme)
        th.addWidget(self.theme_btn)
        v.addWidget(top)
        # body
        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(5)
        self.split.setChildrenCollapsible(False)
        self.agents = AgentsPane(self.state)
        self.agents.hosts_requested.connect(self.open_hosts)
        self.agents.agent_menu.connect(lambda k, pos: self.actions_.agent_menu(k).exec(pos))
        self.agents.host_menu.connect(lambda h, pos: self.actions_.host_menu(h).exec(pos))
        self.menu = MenuPane(self.state, self.counter)
        self.menu.page_chosen.connect(self.show_page)
        self.menu.customize_toggled.connect(lambda _on: self._layout_changed())
        self.stack = QStackedWidget()
        self.agents.setMinimumWidth(170)
        self.menu.setMinimumWidth(170)
        for w in (self.agents, self.menu, self.stack):
            self.split.addWidget(w)
        self.split.setStretchFactor(2, 1)
        v.addWidget(self.split, 1)
        # status bar
        sb = QWidget()
        sb.setObjectName("status")
        sb.setAttribute(Qt.WA_StyledBackground, True)
        sb.setStyleSheet(f"QWidget#status {{ background: {T['pane']}; border-top: 1px solid {T['line']}; }}")
        sb.setFixedHeight(28)
        self.sh = QHBoxLayout(sb)
        self.sh.setContentsMargins(14, 0, 14, 0)
        self.sh.setSpacing(18)
        self.hosts_box = QWidget()
        self.hosts_lay = QHBoxLayout(self.hosts_box)
        self.hosts_lay.setContentsMargins(0, 0, 0, 0)
        self.hosts_lay.setSpacing(18)
        self.sh.addWidget(self.hosts_box)
        self.fresh_lbl = label("", tone="muted", small=True)
        self.fresh_lbl.setToolTip("When the configs were last read from the hosts")
        self.sh.addWidget(self.fresh_lbl)
        self.refresh_link = link("Refresh")
        self.refresh_link.setFont(sans(12))
        self.refresh_link.setToolTip("Read every host's configs again (F5); while loading, Cancel stops it. Staged "
                                     "changes and edits are kept.")
        self.refresh_link.clicked.connect(self._refresh_clicked)
        self.sh.addWidget(self.refresh_link)
        self.sh.addStretch(1)
        self.toast_label = QLabel("")
        self.toast_label.setStyleSheet(f"color: {T['accent']}; font-weight: 600; font-size: 12px;")
        self.sh.addWidget(self.toast_label)
        v.addWidget(sb)
        self.setCentralWidget(central)

    def _restore_geometry(self) -> None:
        st = self.state.settings
        geo = st.value("ui/geometry")
        if isinstance(geo, QByteArray) and not geo.isEmpty():
            self.restoreGeometry(geo)
        else:
            self.resize(1440, 900)
        sp = st.value("ui/splitter")
        self._split_pending = True
        if isinstance(sp, QByteArray) and not sp.isEmpty():
            self._split_pending = not self.split.restoreState(sp)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if getattr(self, "_split_pending", False):
            self._split_pending = False
            QTimer.singleShot(0, self, self._default_split)

    def _default_split(self) -> None:
        w = max(self.width(), 900)
        if w < 1100:
            a, m = int(w * 0.25), int(w * 0.19)
        else:
            a, m = 260, 250
        self.split.setSizes([a, m, max(400, w - a - m)])

    def closeEvent(self, ev) -> None:
        if not self._may_quit():
            ev.ignore()
            return
        st = self.state.settings
        st.setValue("ui/geometry", self.saveGeometry())
        st.setValue("ui/splitter", self.split.saveState())
        super().closeEvent(ev)

    def _may_quit(self) -> bool:
        from PySide6.QtWidgets import QMessageBox

        st = self.state
        staged = len(st.session.drafts.keys_with_changes())
        scope = st.scope()
        unstaged = sum(1 for p in st.work.values if st.work.dirty(p, scope)) + \
            sum(1 for k in st.work.chains if st.work.chain_dirty(k, scope))
        if not staged and not unstaged:
            return True
        if QApplication.platformName() == "offscreen":       # nobody to ask (tests, benchmarks)
            return True
        parts = []
        if staged:
            parts.append(f"staged changes for {plural(staged, 'agent')} (not written)")
        if unstaged:
            parts.append(plural(unstaged, "edit") + " not staged")
        answer = QMessageBox.question(self, "Quit", "You have " + " and ".join(parts) + ".\n\nQuit and discard them?",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return answer == QMessageBox.Yes

    # ------------------------------------------------------------------ pages
    def current_page(self) -> PageView | None:
        w = self.stack.currentWidget()
        return w if isinstance(w, PageView) else None

    def show_page(self, page_id: str | None) -> None:
        st = self.state
        if page_id is not None:
            st.set_page(page_id)
        p = st.current_page()
        if p is None:
            return
        view = self.pages.get(p.id)
        if view is None:
            view = self._new_page(p.id)
            self.stack.setCurrentWidget(view)
        else:
            self.stack.setCurrentWidget(view)
            if view.stale:
                view.rebuild()
            elif view.stale_values:
                view.stale_values = False
                view.refresh()
        self.search.end_pin()
        self.menu.select(p.id)
        if self.search.hasFocus() and not self.search.text():
            view.setFocus()

    def _new_page(self, page_id: str) -> PageView:
        view = PageView(self.state, page_id, self)
        self.pages[page_id] = view
        self.stack.addWidget(view)
        view.resize(self.stack.size())
        view.rebuild(keep_scroll=False)
        return view

    def warm_up(self) -> None:
        """Build the pages not visited yet, one per idle moment (first visits are then instant)."""
        self._warm = [p.id for p in self.state.layout.pages() if p.id not in self.pages or self.pages[p.id].stale]
        self._warm_timer.start()

    def _warm_one(self) -> None:
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return
        while self._warm:
            pid = self._warm.pop(0)
            if self.state.layout.page(pid) is None:
                continue
            view = self.pages.get(pid)
            if view is None:
                self._new_page(pid)
                return
            if view.stale and view is not self.current_page():
                view.rebuild(keep_scroll=False)
                return
        self._warm_timer.stop()

    def _layout_changed(self, *_a) -> None:
        for pid, view in list(self.pages.items()):
            if self.state.layout.page(pid) is None:
                self.stack.removeWidget(view)
                view.hide()
                view.deleteLater()
                del self.pages[pid]
            else:
                view.stale = True
        cur = self.state.current_page()
        if cur is not None:
            self.show_page(cur.id)
        self.warm_up()

    def _values_changed(self) -> None:
        cur = self.current_page()
        for view in self.pages.values():
            if view is not cur:
                view.stale_values = True
        if cur is not None:
            cur.refresh()

    def _work_changed(self) -> None:
        cur = self.current_page()
        if cur is not None:
            cur.refresh_bar()
        self.menu.refresh_marks()

    def _columns_changed(self) -> None:
        cur = self.current_page()
        if cur is not None:
            for t in cur.tables:
                t.apply_cols()

    def _counts_changed(self) -> None:
        cur = self.current_page()
        if cur is not None:
            cur.refresh_header()

    # ------------------------------------------------------------------ navigation
    def go_to(self, section: str, path) -> None:
        st = self.state
        p = st.layout.page_of(section)
        if p is None:
            return
        cat = st.layout.cat_of(p.id)
        if cat is not None and cat.id in st.cat_shut:
            st.cat_shut.discard(cat.id)
            st.save_open()
            self.menu.rebuild()
        self.show_page(p.id)
        view = self.current_page()
        if view is not None:
            view.reveal(section, path)

    def start_pin(self, page_id: str) -> None:
        self.search.start_pin(page_id)

    def _pinned(self, page_id: str, path) -> None:
        st = self.state
        pins = st.layout.pins.setdefault(page_id, [])
        if tuple(path) not in [tuple(x) for x in pins]:
            pins.append(tuple(path))
        st.blk_open[f"@rel:{page_id}"] = True
        st.save_open()
        st.save_layout()
        st.say("Added " + " › ".join(st.model.crumbs(path)) + " to Related settings")

    # ------------------------------------------------------------------ top bar
    def _scope_label(self) -> None:
        s = self.state.session
        keys = s.checked_in_order()
        if not keys:
            self.scope_lbl.setText("No agents selected")
            return
        hosts = {s.record(k).host for k in keys if s.record(k)}
        self.scope_lbl.setText(f"Editing <b style='color:{T['ink']}'>{len(keys)} of {len(s.agents)}</b> agents · "
                               + plural(len(hosts), "host"))

    def _review_count(self) -> None:
        from .review import build_groups

        n = len(build_groups(self.state)) if self.state.session.drafts.has_changes() else 0
        self.review_btn.setText(f"Review ({n})")
        set_prop(self.review_btn, "has", n > 0)

    def open_review(self) -> None:
        from .review import ReviewDialog

        dlg = ReviewDialog(self.state, self)
        dlg.exec()
        dlg.deleteLater()
        self._review_count()

    def open_hosts(self) -> None:
        from .hosts_dialog import HostsDialog

        HostsDialog(self.state, self).exec()

    def open_logs(self, key: str | None, host: str | None = None) -> None:
        from .logs_dialog import LogsDialog

        dlg = LogsDialog(self.state, key, self)
        if host and key is None:
            dlg.host.setCurrentText(host)
        dlg.exec()

    def toggle_theme(self) -> None:
        dark = not T.dark
        self.state.settings.setValue("ui/theme", "dark" if dark else "light")
        apply_theme(QApplication.instance(), dark)
        # widgets that took colours at creation are rebuilt
        sp = self.split.saveState()
        for attr in ("_picker", "_overlay"):
            w = getattr(self, attr, None)
            if w is not None:
                w.hide()
                w.deleteLater()
                setattr(self, attr, None)
        for view in self.pages.values():
            self.stack.removeWidget(view)
            view.hide()
            view.deleteLater()
        self.pages.clear()
        old = self.takeCentralWidget()
        old.deleteLater()
        self._build()
        self.split.restoreState(sp)
        self._scope_label()
        self._status()
        self._review_count()
        self.show_page(None)

    # ------------------------------------------------------------------ status bar
    def _status(self, *_a) -> None:
        clear_layout(self.hosts_lay)
        s = self.state.session
        for h in s.hosts:
            if not h.enabled:
                continue
            st = s.host_status.get(h.name)
            w = QWidget()
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(6)
            hl.addWidget(Dot(HOST_DOT.get(st.state if st else HostState.UNKNOWN, "off")))
            hl.addWidget(label(h.name, small=True))
            n = sum(1 for r in s.agents.values() if r.host == h.name)
            hl.addWidget(label(plural(n, "agent"), tone="faint", small=True))
            w.setToolTip(f"{h.location}\n{st.message if st else ''}".strip())
            self.hosts_lay.addWidget(w)
        self._fresh()

    def _fresh(self) -> None:
        s = self.state.session
        if s.is_refreshing():
            self.fresh_lbl.setText("refreshing…")
            self.refresh_link.setText("Cancel")
            return
        self.refresh_link.setText("Refresh")
        if self.loaded_at is None:
            self.fresh_lbl.setText("")
            return
        sec = int(time.time() - self.loaded_at)
        self.fresh_lbl.setText("loaded " + ("just now" if sec < 10 else f"{sec} s ago" if sec < 60
                                            else f"{sec // 60} min ago"))

    def _refreshed(self) -> None:
        first = self.loaded_at is None
        self.loaded_at = time.time()
        if first:
            self.warm_up()
        self._status()
        self._scope_label()

    def _activity(self, text: str) -> None:
        self._fresh()

    def _refresh_clicked(self) -> None:
        s = self.state.session
        if s.is_refreshing():
            s.cancel_refresh()
        else:
            s.refresh_all()
            self.state.loader.reload()
        self._fresh()

    def toast(self, text: str) -> None:
        self.toast_label.setText(text)
        self._toast_timer.start()

    # ------------------------------------------------------------------ keyboard
    def _shortcuts(self) -> None:
        def text_focus() -> bool:
            w = QApplication.focusWidget()
            return isinstance(w, (QLineEdit, QPlainTextEdit))

        QShortcut(QKeySequence("Ctrl+K"), self, activated=lambda: (self.search.end_pin(), self.search.setFocus(),
                                                                   self.search.selectAll()))
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_clicked)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.open_review)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: (self.agents.filter.setFocus(),
                                                                   self.agents.filter.selectAll()))

        def undo() -> None:
            if not text_focus() and self.state.session.drafts.can_undo():
                self.state.session.drafts.undo()
                self.toast("Undid the last staged change")

        def redo() -> None:
            if not text_focus() and self.state.session.drafts.can_redo():
                self.state.session.drafts.redo()
                self.toast("Redid the staged change")

        QShortcut(QKeySequence.Undo, self, activated=undo)
        QShortcut(QKeySequence.Redo, self, activated=redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=redo)
        QShortcut(QKeySequence("Escape"), self, activated=self._escape)

    def _escape(self) -> None:
        overlay = getattr(self, "_overlay", None)
        if overlay is not None and overlay.isVisible() and overlay.panel is not None:
            overlay.panel.reject()
        elif self.search.pop.isVisible() or self.search.pin_page:
            self.search.hide_results()
            self.search.end_pin()
            self.search.clear()
            self.search.clearFocus()
        elif self.state.customizing:
            self.menu.set_customizing(False)

    def first_run(self) -> None:
        if not self.state.session.hosts:
            self.open_hosts()
