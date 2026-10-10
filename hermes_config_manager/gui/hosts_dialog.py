"""Hosts: where the agents live (local folder, network share or SSH).  One host down never blocks the others."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QSplitter, QVBoxLayout,
                               QWidget)

from ..transports import transport_for
from ..transports.base import discover_config_paths
from ..transports.hosts import ACCESS_LOCAL, ACCESS_SSH, HostConfig, save_registry
from ..transports.ssh import UnknownHostKey, known_hosts_file, resolve_endpoint, trust_host_key
from .theme import T, mono
from .widgets import btn, label, link, plural

ACCESS = ["local folder", "network share", "SSH"]


def _probe(ctx_task, host: HostConfig) -> str:
    t = transport_for(host)
    try:
        t.probe()
        ctx_task.check()
        found = discover_config_paths(t, host.hermes_root, host.globs)
        where = f" to {resolve_endpoint(host).describe()}" if host.access_mode == ACCESS_SSH else ""
        return f"Connected{where}. Found {plural(len(found), 'agent')}."
    finally:
        t.close()


def access_of(h: HostConfig) -> str:
    if h.access_mode == ACCESS_SSH:
        return "SSH"
    return "network share" if (h.hermes_root or "").startswith(("\\\\", "//")) else "local folder"


class HostsDialog(QDialog):
    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Hosts")
        self.setModal(True)
        self.resize(780, 440)
        self.hosts: list[HostConfig] = list(state.session.hosts)
        self.access: list[str] = [access_of(h) for h in self.hosts]
        self.row = 0 if self.hosts else -1
        self._loading = False
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 12)
        v.setSpacing(10)
        h = QHBoxLayout()
        t = QLabel("Hosts")
        t.setProperty("h2", True)
        h.addWidget(t)
        h.addWidget(label("Where your agents live. One host being down never blocks the others.", tone="muted"), 1)
        v.addLayout(h)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        self.list.setToolTip("Right-click a host to duplicate it or move it up or down")
        lv.addWidget(self.list, 1)
        add = link("+ Add host", tip="Add a new host: a local folder, a network share or an SSH server that holds "
                                     "a Hermes home")
        add.clicked.connect(self._add)
        lv.addWidget(add)
        split.addWidget(left)
        self.form_box = QWidget()
        outer = QVBoxLayout(self.form_box)
        outer.setContentsMargins(16, 4, 4, 4)
        self.form_w = QWidget()
        fv = QVBoxLayout(self.form_w)
        fv.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.form_w)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignLeft)
        self.name = QLineEdit()
        self.name.setPlaceholderText("a short name you will recognise")
        self.acc = QComboBox()
        self.acc.addItems(ACCESS)
        self.path = QLineEdit()
        self.path.setFont(mono())
        self.ssh_host = QLineEdit()
        self.ssh_host.setFont(mono())
        self.ssh_host.setPlaceholderText("host, host:port or a Host alias from ~/.ssh/config")
        self.ssh_user = QLineEdit()
        self.ssh_user.setFont(mono())
        self.ssh_key = QLineEdit()
        self.ssh_key.setFont(mono())
        self.ssh_key.setPlaceholderText("private key file (empty = ~/.ssh/config or SSH agent)")
        self.key_pick = link("Browse…")
        self.key_pick.clicked.connect(self._browse_key)
        keyrow = QWidget()
        kh = QHBoxLayout(keyrow)
        kh.setContentsMargins(0, 0, 0, 0)
        kh.addWidget(self.ssh_key, 1)
        kh.addWidget(self.key_pick)
        self.remote_root = QLineEdit()
        self.remote_root.setFont(mono())
        self.remote_root.setPlaceholderText("the same folder as seen on that machine, e.g. /home/me/.hermes")
        self.enabled = QCheckBox("Enabled (a disabled host is skipped when loading and writing)")
        self.name.setToolTip("Any short name; it is shown in the Agents pane and the status bar")
        self.acc.setToolTip("How this app reaches the files: a folder on this computer, a network share "
                            "(a UNC path or a mapped drive), or SSH (files read and written over SFTP)")
        self.path.setToolTip("The Hermes home: the folder that holds config.yaml and profiles/ (usually .hermes). "
                             "For SSH, the path as seen on the server.")
        self.ssh_host.setToolTip("The server's name or IP address, optionally with :port, or a Host alias "
                                 "from ~/.ssh/config (its HostName, User, Port and IdentityFile are used "
                                 "for the fields left empty here)")
        self.ssh_user.setToolTip("The user name to log in with over SSH. Empty: the User in ~/.ssh/config, "
                                 "else your local user name.")
        self.ssh_key.setToolTip("Your private key file. Empty: the IdentityFile in ~/.ssh/config, "
                                "else the keys of your SSH agent and ~/.ssh.")
        self.key_pick.setToolTip("Choose the private key file")
        self.remote_root.setToolTip("Only for gateway commands: the same Hermes home as seen on the SSH server, "
                                    "for example /home/me/.hermes")
        self.enabled.setToolTip("Untick to keep the host in the list but skip it when loading and writing")
        self.form.addRow("Name", self.name)
        self.form.addRow("Access", self.acc)
        self.path_label = QLabel("Path")
        self.form.addRow(self.path_label, self.path)
        self.cmd_link = link("Gateway commands over SSH (optional)…",
                             tip="Restarting gateways needs SSH even when the files are reached through a share")
        self.cmd_link.clicked.connect(self._show_cmd)
        self.form.addRow("", self.cmd_link)
        self.form.addRow("SSH host", self.ssh_host)
        self.form.addRow("SSH user", self.ssh_user)
        self.form.addRow("Key file", keyrow)
        self.form.addRow("Remote path", self.remote_root)
        self.form.addRow("", self.enabled)
        fv.addLayout(self.form)
        tr = QHBoxLayout()
        self.test = btn("Test connection", tip="Try to reach the host and look for config.yaml and profiles/ "
                                               "with these settings. Read-only.")
        self.test.clicked.connect(self._test)
        tr.addWidget(self.test)
        self.test_msg = label("", tone="muted", wrap=True)
        tr.addWidget(self.test_msg, 1)
        fv.addLayout(tr)
        rr = QHBoxLayout()
        rr.addStretch(1)
        self.rm = link("Remove host", tone="bad", tip="Remove this host from the app (asks first). Files on the "
                                                      "host are not touched.")
        self.rm.clicked.connect(self._remove_ask)
        self.rm_q = label("Remove from this app? Files on the host are not touched.", tone="muted")
        self.rm_yes = btn("Remove", sm=True)
        self.rm_yes.clicked.connect(self._remove)
        self.rm_no = btn("Keep", sm=True)
        self.rm_no.clicked.connect(self._remove_cancel)
        for w in (self.rm_q, self.rm_yes, self.rm_no, self.rm):
            rr.addWidget(w)
        fv.addLayout(rr)
        self.empty = label("No host yet. Add the folder, share or SSH server where your Hermes agents live.",
                           tone="muted", wrap=True)
        outer.addWidget(self.empty)
        outer.addStretch(1)
        split.addWidget(self.form_box)
        split.setSizes([260, 520])
        v.addWidget(split, 1)
        f = QHBoxLayout()
        self.count = label("", tone="muted")
        f.addWidget(self.count, 1)
        self.err = label("", tone="bad")
        f.addWidget(self.err)
        done = btn("Done", pri=True, tip="Save the host list and load the hosts")
        done.clicked.connect(self.accept)
        f.addWidget(done)
        v.addLayout(f)
        for w in (self.name, self.path, self.ssh_host, self.ssh_user, self.ssh_key, self.remote_root):
            w.textEdited.connect(self._commit)
        self.acc.activated.connect(lambda _i: self._access_changed())
        self.enabled.toggled.connect(lambda _on: self._commit())
        self._cmd_open = False
        self._remove_cancel()
        self._fill_list()

    # -- list ----------------------------------------------------------------------
    def _fill_list(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for h, acc in zip(self.hosts, self.access):
            n = sum(1 for r in self.state.session.agents.values() if r.host == h.name)
            item = QListWidgetItem(f"{h.name or 'unnamed host'}\n{acc}{'' if h.enabled else ' · disabled'} · "
                                   f"{plural(n, 'agent')}")
            self.list.addItem(item)
        self.list.setCurrentRow(self.row)
        self.list.blockSignals(False)
        self._load_form()
        self.count.setText(plural(sum(1 for h in self.hosts if h.enabled), "host") + " enabled · " +
                           plural(len(self.state.session.agents), "agent"))

    def _select(self, row: int) -> None:
        self.row = row
        self._remove_cancel()
        self._load_form()

    def _load_form(self) -> None:
        has = 0 <= self.row < len(self.hosts)
        self.form_w.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            return
        self._loading = True
        h = self.hosts[self.row]
        acc = self.access[self.row]
        self.name.setText(h.name)
        self.acc.setCurrentText(acc)
        self.path.setText(h.hermes_root)
        self.path_label.setText("Remote path" if acc == "SSH" else "Path")
        self.path.setPlaceholderText("/home/me/.hermes" if acc == "SSH" else "\\\\server\\share\\.hermes"
                                     if acc == "network share" else "folder that holds config.yaml and profiles")
        self.ssh_host.setText((h.ssh_hostname or "") + (f":{h.ssh_port}" if h.ssh_hostname and h.ssh_port != 22 else ""))
        self.ssh_user.setText(h.ssh_user or "")
        self.ssh_key.setText(h.ssh_key or "")
        self.remote_root.setText(h.remote_root or "")
        self.enabled.setChecked(h.enabled)
        self._loading = False
        self._visibility()
        self.test_msg.setText("")

    def _visibility(self) -> None:
        if not (0 <= self.row < len(self.hosts)):
            return
        ssh = self.access[self.row] == "SSH"
        h = self.hosts[self.row]
        cmd = ssh or self._cmd_open or bool(h.ssh_hostname)
        for w in (self.ssh_host, self.ssh_user, self.ssh_key.parentWidget(), self.key_pick):
            w.setVisible(cmd)
            lab = self.form.labelForField(w)
            if lab is not None:
                lab.setVisible(cmd)
        rr_vis = cmd and not ssh
        self.remote_root.setVisible(rr_vis)
        lab = self.form.labelForField(self.remote_root)
        if lab is not None:
            lab.setVisible(rr_vis)
        self.cmd_link.setVisible(not cmd)

    def _show_cmd(self) -> None:
        self._cmd_open = True
        self._visibility()

    # -- editing -------------------------------------------------------------------------
    def _access_changed(self) -> None:
        if 0 <= self.row < len(self.hosts):
            self.access[self.row] = self.acc.currentText()
            self._commit()
            self._load_form()

    def _commit(self, *_a) -> None:
        if self._loading or not (0 <= self.row < len(self.hosts)):
            return
        h = self.hosts[self.row]
        acc = self.access[self.row]
        host_text = self.ssh_host.text().strip()
        port = 22
        if ":" in host_text:
            host_text, _, p = host_text.rpartition(":")
            port = int(p) if p.isdigit() else 22
        self.hosts[self.row] = replace(
            h, name=self.name.text().strip(), access_mode=ACCESS_SSH if acc == "SSH" else ACCESS_LOCAL,
            hermes_root=self.path.text().strip(), ssh_hostname=host_text or None,
            ssh_user=self.ssh_user.text().strip() or None, ssh_key=self.ssh_key.text().strip() or None,
            ssh_port=port, remote_root=self.remote_root.text().strip() or None, enabled=self.enabled.isChecked())
        item = self.list.item(self.row)
        if item is not None:
            n = sum(1 for r in self.state.session.agents.values() if r.host == h.name)
            item.setText(f"{self.hosts[self.row].name or 'unnamed host'}\n{acc}"
                         f"{'' if self.hosts[self.row].enabled else ' · disabled'} · {plural(n, 'agent')}")
        self.test_msg.setText("")

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Private key file")
        if path:
            self.ssh_key.setText(path)
            self._commit()

    def _add(self) -> None:
        base = "new host"
        names = {h.name for h in self.hosts}
        name, i = base, 2
        while name in names:
            name, i = f"{base} {i}", i + 1
        self.hosts.append(HostConfig(name))
        self.access.append("local folder")
        self.row = len(self.hosts) - 1
        self._fill_list()
        self.name.setFocus()
        self.name.selectAll()

    def _list_menu(self, pos) -> None:
        row = self.list.indexAt(pos).row()
        if not (0 <= row < len(self.hosts)):
            return
        m = QMenu(self)
        m.setToolTipsVisible(True)
        m.addAction("Duplicate", lambda: self._duplicate(row)).setToolTip(
            "Add a copy of this host (for example another folder on the same server)")
        up = m.addAction("Move up", lambda: self._move(row, -1))
        up.setEnabled(row > 0)
        dn = m.addAction("Move down", lambda: self._move(row, 1))
        dn.setEnabled(row < len(self.hosts) - 1)
        m.exec(self.list.mapToGlobal(pos))

    def _duplicate(self, row: int) -> None:
        h = self.hosts[row]
        names = {x.name for x in self.hosts}
        name, i = f"{h.name} copy", 2
        while name in names:
            name, i = f"{h.name} copy {i}", i + 1
        self.hosts.insert(row + 1, replace(h, name=name))
        self.access.insert(row + 1, self.access[row])
        self.row = row + 1
        self._fill_list()

    def _move(self, row: int, d: int) -> None:
        j = row + d
        if 0 <= j < len(self.hosts):
            self.hosts[row], self.hosts[j] = self.hosts[j], self.hosts[row]
            self.access[row], self.access[j] = self.access[j], self.access[row]
            self.row = j
            self._fill_list()

    def _remove_ask(self) -> None:
        for w in (self.rm_q, self.rm_yes, self.rm_no):
            w.show()
        self.rm.hide()

    def _remove_cancel(self) -> None:
        for w in (self.rm_q, self.rm_yes, self.rm_no):
            w.hide()
        self.rm.show()

    def _remove(self) -> None:
        if 0 <= self.row < len(self.hosts):
            gone = self.hosts.pop(self.row)
            self.access.pop(self.row)
            self.row = min(self.row, len(self.hosts) - 1)
            self._remove_cancel()
            self._fill_list()
            self.state.say(f"Removed host {gone.name} from this app. Its files were not touched.")

    def _test(self) -> None:
        if not (0 <= self.row < len(self.hosts)):
            return
        h = self.hosts[self.row]
        if not h.hermes_root:
            self.test_msg.setText("Could not connect: no path given.")
            self.test_msg.setStyleSheet(f"color: {T['bad']};")
            return
        if h.access_mode == ACCESS_SSH and not h.ssh_hostname:
            self.test_msg.setText("Could not connect: no SSH host given.")
            self.test_msg.setStyleSheet(f"color: {T['bad']};")
            return
        self.test.setEnabled(False)
        self.test_msg.setStyleSheet(f"color: {T['muted']};")
        self.test_msg.setText("Testing…")

        def done(msg: str) -> None:
            self.test.setEnabled(True)
            self.test_msg.setStyleSheet(f"color: {T['ok']};")
            self.test_msg.setText(msg)

        def failed(exc) -> None:
            self.test.setEnabled(True)
            self.test_msg.setStyleSheet(f"color: {T['bad']};")
            self.test_msg.setText(f"Could not connect: {exc}")
            if isinstance(exc, UnknownHostKey) and self._ask_trust(exc):
                self._test()

        self.state.session.runner.submit(_probe, h, label=f"Test {h.name}", key=("host-test",),
                                         timeout=h.timeout, receiver=self, on_done=done, on_error=failed)

    def _ask_trust(self, err) -> bool:
        """First connection to a server: show its key fingerprint and ask before trusting it."""
        box = QMessageBox(QMessageBox.Question, "Unknown server",
                          f"This is the first connection to {err.entry}.\n\n"
                          f"Its {err.key_type} key fingerprint is:\n{err.fingerprint}\n\n"
                          "Trust it only if it matches the server's own key (on the server: "
                          "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub). Trusting adds it to "
                          f"{known_hosts_file()}.", parent=self)
        yes = box.addButton("Trust and connect", QMessageBox.AcceptRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return False
        trust_host_key(err)
        return True

    # -- done -----------------------------------------------------------------------
    def accept(self) -> None:
        names = [h.name for h in self.hosts]
        if any(not n for n in names) or len(set(names)) != len(names):
            self.err.setText("Every host needs a different name.")
            return
        if any(not h.hermes_root for h in self.hosts if h.enabled):
            self.err.setText("Every enabled host needs a path.")
            return
        changed = list(self.hosts) != list(self.state.session.hosts)
        session = self.state.session
        kept = set(names)
        lost = [k for k in session.drafts.keys_with_changes()
                if (r := session.record(k)) is not None and r.host not in kept]
        if changed and lost:
            from PySide6.QtWidgets import QMessageBox

            gone = sorted({session.record(k).host for k in lost})
            if QMessageBox.question(self, "Hosts", f"{plural(len(lost), 'agent')} on {', '.join(gone)} "
                                    "have staged changes that are not written. Removing or renaming the host "
                                    "discards them. Continue?") != QMessageBox.Yes:
                return
            session.drafts.discard(lost)
        if changed:
            try:
                save_registry(self.hosts)
            except Exception as exc:  # invalid values, disk errors
                self.err.setText(f"Could not save: {exc}")
                return
            self.state.session.replace_hosts(self.hosts)
            self.state.session.refresh_all()
            self.state.loader.reload()
        super().accept()

    def reject(self) -> None:
        self.accept()
