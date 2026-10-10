"""Right-click actions on an agent or a host (refresh, files, gateways, logs, shortcuts)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QMenu, QMessageBox, QPlainTextEdit,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ..core import appdirs, secrets
from ..core.assignments import ModelAssignment
from ..core.paths import MISSING
from ..services import chains as C
from ..services import gateway
from ..services.editing import EditScope
from ..services.review import ApplyFlow
from ..services.work import endpoint_cleanup, rows_of
from .theme import mono
from .widgets import menu_tips, plural


AGENT_TIPS = {
    "Edit only this agent": "Uncheck every other agent and check only this one. Nothing is changed in any file.",
    "Set primary model": "Pick the main model (model › provider and default) for this agent only. It becomes a "
                         "staged change in Review; nothing is written until Write config.",
    "Set delegation model": "Pick the delegation model (delegation › provider and model) for this agent only. "
                            "It becomes a staged change in Review; nothing is written until Write config.",
    "All models of this agent": "A read-only overview of every model this agent uses: main, delegation, "
                                "auxiliary tasks and their fallbacks.",
    "All models of the": "A read-only overview of the models the checked agents use, side by side.",
    "Refresh this agent": "Read this agent's config.yaml from its host again. Staged changes are kept.",
    "View config.yaml": "Show this agent's config.yaml as it is on the host, with credentials masked. Read-only.",
    "Save a local copy": "Save this agent's config.yaml to a file of your choice (you choose whether credentials "
                         "are masked). The agent's own file is not changed.",
    "Open agent folder": "Open the folder holding this agent's config.yaml in the file explorer (not for SSH hosts).",
    "Logs": "Read the Hermes log files on this host (read-only).",
    "Restart this agent's gateway": "Run the gateway restart command for this agent over SSH. Running "
                                    "conversations of this agent are interrupted. Asks before running.",
}

HOST_TIPS = {
    "Edit only this host's": "Check exactly the agents of this host and uncheck all others. Nothing is changed "
                             "in any file.",
    "Refresh this host": "Look for agents on this host again and re-read their config.yaml. Staged changes are kept.",
    "Gateway status": "Ask this host (over SSH) which Hermes gateways are running. Read-only.",
    "Restart gateways of the": "Run the gateway restart command for the checked agents of this host over SSH. "
                               "Running conversations are interrupted. Asks before running.",
    "Logs": "Read the Hermes log files on this host (read-only).",
    "Host details": "Where this host is, how it is reached, and how loading it went.",
    "Hosts": "Add, change, test, disable or remove hosts.",
}


class TextDialog(QDialog):
    def __init__(self, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 640)
        lay = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setFont(mono())
        view.setPlainText(text)
        lay.addWidget(view)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class AgentActions(QObject):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.state = window.state
        self.session = window.state.session
        self.flow = ApplyFlow(self.session, parent=self)
        self.flow.restarted.connect(self._restarted)

    # -- menus -----------------------------------------------------------------------
    def agent_menu(self, key: str) -> QMenu:
        rec = self.session.record(key)
        host = self.session.host(rec.host) if rec else None
        menu = QMenu(self.window)
        if rec is None or host is None:
            return menu
        usable = rec.usable
        menu.addAction("Edit only this agent", lambda: self.session.replace_checked([key]))
        a = menu.addAction("Copy this agent's values on this page as edits", lambda: self.copy_page(key))
        a.setEnabled(usable)
        a.setToolTip("Every setting and chain on the current page becomes an edit with this agent's value; stage it "
                     "for the checked agents")
        a = menu.addAction("Set primary model…", lambda: self.set_model(key, C.PRIMARY))
        a.setEnabled(usable)
        a = menu.addAction("Set delegation model…", lambda: self.set_model(key, C.DELEGATION))
        a.setEnabled(usable)
        a = menu.addAction("All models of this agent…", lambda: self.model_map([key]))
        a.setEnabled(usable)
        checked = [k for k in self.session.checked_in_order() if self.session.record(k).usable]
        a = menu.addAction(f"All models of the {len(checked)} checked agents…", lambda: self.model_map(checked))
        a.setEnabled(len(checked) > 1)
        menu.addSeparator()
        menu.addAction("Refresh this agent", lambda: self.session.refresh_agent(key))
        a = menu.addAction("View config.yaml (credentials masked)", lambda: self.view_yaml(key))
        a.setEnabled(rec.raw is not None)
        a = menu.addAction("Save a local copy…", lambda: self.export(key))
        a.setEnabled(rec.raw is not None)
        a = menu.addAction("Open agent folder", lambda: self.open_folder(key))
        a.setEnabled(not host.is_ssh)
        menu.addAction("Logs…", lambda: self.window.open_logs(key))
        menu.addSeparator()
        a = menu.addAction("Restart this agent's gateway…", lambda: self.restart([key]))
        a.setEnabled(host.can_run_commands)
        menu_tips(menu, AGENT_TIPS)
        return menu

    def host_menu(self, host_name: str) -> QMenu:
        host = self.session.host(host_name)
        menu = QMenu(self.window)
        if host is None:
            return menu
        keys = [k for k, r in self.session.agents.items() if r.host == host_name and r.usable]
        menu.addAction(f"Edit only this host's {plural(len(keys), 'agent')}", lambda: self.session.replace_checked(keys))
        menu.addAction("Refresh this host", lambda: self.session.refresh_host(host_name))
        menu.addSeparator()
        a = menu.addAction("Gateway status…", lambda: self.gateway_status(host_name))
        a.setEnabled(host.can_run_commands)
        checked = [k for k in keys if k in self.session.checked]
        a = menu.addAction(f"Restart gateways of the {len(checked)} checked agents here…", lambda: self.restart(checked))
        a.setEnabled(host.can_run_commands and bool(checked))
        menu.addAction("Logs…", lambda: self.window.open_logs(None, host_name))
        menu.addSeparator()
        menu.addAction("Host details…", lambda: self.host_details(host_name))
        menu.addAction("Hosts…", self.window.open_hosts)
        menu_tips(menu, HOST_TIPS)
        return menu

    # -- editing shortcuts --------------------------------------------------------------
    def set_model(self, key: str, spec: C.ChainSpec) -> None:
        from .dialogs import pick_model

        scope = EditScope(self.session, [key], key, self.state.default_for)
        cur = C.read_chain(scope, spec, key)[0]
        rec = self.session.record(key)
        picked = pick_model(self.window, self.state, f"{spec.label} · {rec.name}", cur.provider or "",
                            (cur.provider, cur.model) if cur.provider and cur.model else None)
        if picked is None:
            return
        head = ModelAssignment(picked[0], picked[1], cur.reasoning_effort)
        ops = C.chain_ops(scope, spec, key, head=head, origin="agent-menu")
        if (picked[0] or None) != (cur.provider or None):
            ops += endpoint_cleanup(scope, spec, key, "agent-menu")
        if ops:
            base, sha = self.session.base_of(key)
            self.session.drafts.apply_many({key: (base, sha, ops)})
        self.state.say(f"{rec.name}: {spec.label} → {picked[0]} / {picked[1]}. Staged; see Review.")

    def copy_page(self, key: str) -> None:
        st = self.state
        page = st.current_page()
        if page is None:
            return
        src = EditScope(self.session, [key], key)
        scope = st.scope()
        n = 0
        from ..services.work import REMOVE, spec_for

        for k in st.visible_sections(page):
            sec = st.model.sections.get(k)
            if sec is None:
                continue
            nodes = st.visible_leaves(sec.own_nodes())
            if sec.chain == "auxiliary":
                for t in st.model.tasks:
                    if not st.task_hidden(t):
                        nodes += st.visible_leaves(st.model.task_nodes(t))
            for node in nodes:
                if node.secret:
                    continue
                v = src.draft_value(key, node.path)
                st.work.values[node.path] = REMOVE if v is MISSING else v
                if st.work.dirty(node.path, scope):
                    n += 1
                else:
                    del st.work.values[node.path]
            chains = ([sec.chain] if sec.chain in ("primary", "delegation") else
                      [f"aux:{t}" for t in st.model.tasks if not st.task_hidden(t)] if sec.chain == "auxiliary" else [])
            for ck in chains:
                rows = rows_of(C.read_chain(src, spec_for(ck), key))
                if not rows:
                    continue
                st.work.drop_chain(ck)
                w = st.work.work_chain(ck, scope, st.ref_key())
                w.rows, w.pm, w.e, w.fb = [list(r) for r in rows], True, True, True
                if st.work.chain_dirty(ck, scope):
                    n += 1
                else:
                    st.work.drop_chain(ck)
        st.work.edited()
        rec = self.session.record(key)
        st.say(f"{plural(n, 'edit')} from {rec.name} on this page. Not staged yet.")

    def model_map(self, keys: list[str]) -> None:
        """Every model slot (primary, fallbacks, delegation, auxiliary, MoA, ...) of the given agents."""
        from ..core.assignments import all_assignments, effective_main_effort

        dlg = QDialog(self.window)
        dlg.setWindowTitle("All models" if len(keys) > 1 else f"All models · {self.session.record(keys[0]).name}")
        dlg.resize(860, 620)
        lay = QVBoxLayout(dlg)
        tree = QTreeWidget()
        tree.setHeaderLabels(["Agent", "Where", "Provider", "Model", "Effort"])
        tree.setRootIsDecorated(False)
        tree.setSortingEnabled(True)
        for k in keys:
            rec = self.session.record(k)
            if rec is None or not rec.usable:
                continue
            for slot, a in all_assignments(rec.data):
                effort = a.reasoning_effort
                if slot.key == "primary":
                    effort = effective_main_effort(rec.data)[0]
                tree.addTopLevelItem(QTreeWidgetItem([rec.name, slot.label, a.provider or "", a.model or "",
                                                      effort or ""]))
        for c, w in enumerate((180, 190, 110, 260, 70)):
            tree.setColumnWidth(c, w)
        lay.addWidget(tree)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        dlg.exec()

    # -- files -------------------------------------------------------------------------
    def view_yaml(self, key: str) -> None:
        rec = self.session.record(key)
        if rec is None or rec.text is None:
            return
        TextDialog(f"{rec.display} — {rec.path}", secrets.mask_text(rec.text), self.window).exec()

    def export(self, key: str) -> None:
        rec = self.session.record(key)
        if rec is None or rec.raw is None:
            return
        default = appdirs.exports_dir() / f"{rec.host}__{rec.name}.config.yaml"
        target, _ = QFileDialog.getSaveFileName(self.window, "Save a local copy", str(default), "YAML (*.yaml *.yml)")
        if target:
            answer = QMessageBox.question(
                self.window, "Save a local copy",
                "Mask the credentials in the copy?\n\nYes: credentials are replaced by " + secrets.MASK +
                ".\nNo: an exact copy, credentials included.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.Yes:
                Path(target).write_text(secrets.mask_text(rec.text or ""), encoding="utf-8")
                self.state.say(f"Saved {target} (credentials masked)")
            else:
                Path(target).write_bytes(rec.raw)
                self.state.say(f"Saved {target} (an exact copy: it contains the file's credentials)")

    def open_folder(self, key: str) -> None:
        rec = self.session.record(key)
        if rec is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(rec.path).parent)))

    # -- gateways ------------------------------------------------------------------------
    def restart(self, keys: list[str]) -> None:
        if not keys:
            return
        names = ", ".join(self.session.record(k).display for k in keys[:6]) + (" …" if len(keys) > 6 else "")
        answer = QMessageBox.question(self.window, "Restart gateways",
                                      f"Restart the gateway of {plural(len(keys), 'agent')}?\n{names}")
        if answer == QMessageBox.Yes:
            self.state.say(f"Restarting {plural(len(keys), 'gateway')}…")
            self.flow.restart(keys)

    def _restarted(self, outcomes) -> None:
        ok = sum(1 for o in outcomes if o.ok)
        self.state.say(f"Gateways: {ok} restarted, {len(outcomes) - ok} not")
        failed = [o for o in outcomes if not o.ok]
        if failed:
            QMessageBox.warning(self.window, "Gateways", "\n".join(f"{o.unit}: {o.message}" for o in failed[:20]))

    def gateway_status(self, host_name: str) -> None:
        host = self.session.host(host_name)
        profiles = [r.profile for r in self.session.agents.values() if r.host == host_name]
        dlg = QDialog(self.window)
        dlg.setWindowTitle(f"Gateways on {host_name}")
        dlg.resize(560, 520)
        lay = QVBoxLayout(dlg)
        tree = QTreeWidget()
        tree.setHeaderLabels(["Agent", "Service", "State"])
        tree.setRootIsDecorated(False)
        tree.setColumnWidth(0, 220)
        tree.setColumnWidth(1, 220)
        tree.addTopLevelItem(QTreeWidgetItem(["Asking the host…", "", ""]))
        lay.addWidget(tree)

        def done(states) -> None:
            tree.clear()
            for s in states:
                tree.addTopLevelItem(QTreeWidgetItem([s.profile or "main", s.unit, s.state]))

        def failed(exc) -> None:
            tree.clear()
            tree.addTopLevelItem(QTreeWidgetItem([f"Failed: {exc}", "", ""]))

        self.session.runner.submit(gateway.query_status, self.session.transport(host_name), profiles,
                                   label=f"Gateway status {host_name}", key=("gateway-status", host_name),
                                   host=f"{host_name}\x00cmd", timeout=host.timeout + 10, receiver=dlg,
                                   on_done=done, on_error=failed)
        dlg.exec()

    def host_details(self, host_name: str) -> None:
        host = self.session.host(host_name)
        st = self.session.host_status.get(host_name)
        if host is None:
            return

        def when(ts):
            return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "never"

        failed = [r for r in self.session.agents.values() if r.host == host_name and r.error]
        lines = [f"Name: {host.name}", f"Access: {host.access_mode}", f"Location: {host.location}",
                 "Commands (gateways/logs): " + (f"SSH {host.ssh_user + '@' if host.ssh_user else ''}{host.ssh_hostname}" if host.can_run_commands
                                                 else "not configured"),
                 f"Timeout: {host.timeout:.0f} s", "",
                 f"State: {st.state if st else 'unknown'}", f"Message: {st.message if st else ''}",
                 f"Last successful refresh: {when(st.last_ok) if st else 'never'}",
                 f"Last attempt: {when(st.last_attempt) if st else 'never'}",
                 f"Scan time: {st.scan_ms:.0f} ms" if st and st.scan_ms is not None else "Scan time: -",
                 f"Agents: {st.agent_count if st else 0}", f"Operations still blocked: {st.hung if st else 0}"]
        if failed:
            lines += ["", "Agents with errors:"] + [f"  {r.name}: {r.error}" for r in failed[:30]]
        TextDialog(f"Host {host_name}", "\n".join(lines), self.window).exec()
