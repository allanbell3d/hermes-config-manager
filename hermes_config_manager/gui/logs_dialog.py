"""Logs: browse a host's log folders, preview (credentials masked), download files or a ZIP."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QProgressBar, QPushButton, QSpinBox, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from ..services import logs as L

PATH_ROLE = Qt.UserRole + 30
DIR_ROLE = Qt.UserRole + 31


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


class LogsDialog(QDialog):
    def __init__(self, ctx, key: str | None = None, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.session = ctx.session
        self.record = self.session.record(key) if key else None
        self.setObjectName("logsDialog")
        self.setWindowTitle("Logs")
        self.resize(1100, 700)
        self._entries: list = []
        self._task = None
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Host"))
        self.host = QComboBox()
        for h in self.session.hosts:
            self.host.addItem(h.name)
        if self.record is not None:
            self.host.setCurrentText(self.record.host)
        self.host.setToolTip("The host whose log files are listed")
        self.host.currentTextChanged.connect(self._host_changed)
        top.addWidget(self.host)
        top.addWidget(QLabel("Folder"))
        self.root = QComboBox()
        self.root.setEditable(True)
        self.root.setMinimumWidth(380)
        self.root.setToolTip("The folder to list log files from. Pick one or type a path and press Enter.")
        self.root.activated.connect(lambda _i: self.reload())
        self.root.lineEdit().returnPressed.connect(self.reload)
        top.addWidget(self.root, 1)
        self.name_filter = QLineEdit(placeholderText="Name contains…")
        self.name_filter.setToolTip("Show only files whose name contains this text")
        self.name_filter.textChanged.connect(self._fill)
        top.addWidget(self.name_filter)
        top.addWidget(QLabel("Changed in the last"))
        self.days = QSpinBox()
        self.days.setRange(0, 3650)
        self.days.setSpecialValueText("any time")
        self.days.setSuffix(" days")
        self.days.setToolTip("Show only files changed in the last N days (0 = any time)")
        self.days.valueChanged.connect(self._fill)
        top.addWidget(self.days)
        lay.addLayout(top)
        split = QSplitter(Qt.Horizontal)
        self.files = QTreeWidget()
        self.files.setObjectName("logFiles")
        self.files.setHeaderLabels(["Name", "Size", "Modified"])
        self.files.setRootIsDecorated(False)
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.setColumnWidth(0, 300)
        self.files.setToolTip("Click a file to preview its end; double-click to open its folder (local hosts). "
                              "Select several to download them.")
        self.files.currentItemChanged.connect(self._preview)
        self.files.itemDoubleClicked.connect(self._open_dir)
        split.addWidget(self.files)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setToolTip("The end of the selected log file (read-only)")
        mono = QFont("Cascadia Mono")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSizeF(9.5)
        self.preview.setFont(mono)
        split.addWidget(self.preview)
        split.setSizes([480, 620])
        lay.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.status = QLabel()
        self.status.setProperty("muted", True)
        bottom.addWidget(self.status, 1)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        bottom.addWidget(self.progress)
        self.cancel = QPushButton("Cancel")
        self.cancel.setToolTip("Stop the current listing or download")
        self.cancel.clicked.connect(self._cancel)
        self.cancel.hide()
        bottom.addWidget(self.cancel)
        self.dl_files = QPushButton("Download selected…")
        self.dl_files.setToolTip("Copy the selected log files to a folder on this computer")
        self.dl_files.clicked.connect(lambda: self._download(False))
        bottom.addWidget(self.dl_files)
        self.dl_zip = QPushButton("Download selected as ZIP…")
        self.dl_zip.setToolTip("Save the selected log files in one ZIP file on this computer")
        self.dl_zip.clicked.connect(lambda: self._download(True))
        bottom.addWidget(self.dl_zip)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        self._host_changed(self.host.currentText())

    # -- listing ---------------------------------------------------------------------
    def _transport(self):
        return self.session.transport(self.host.currentText())

    def _host_changed(self, name: str) -> None:
        host = self.session.host(name)
        if host is None:
            return
        record = self.record if self.record is not None and self.record.host == name else None
        try:
            roots = L.log_roots(self._transport(), host, record)
        except Exception as exc:  # misconfigured host
            self.status.setText(str(exc))
            return
        self.root.blockSignals(True)
        self.root.clear()
        self.root.addItems(roots)
        # an agent's own folder first when opened from an agent
        if record is not None and len(roots) >= 3:
            self.root.setCurrentIndex(2)
        self.root.blockSignals(False)
        self.reload()

    def reload(self) -> None:
        root = self.root.currentText().strip()
        host = self.session.host(self.host.currentText())
        if not root or host is None:
            return
        self.status.setText(f"Listing {root}…")
        self.files.clear()
        self.session.runner.submit(
            L.list_logs, self._transport(), root, label=f"List {root}", key=("logs-list",), host=host.name,
            timeout=host.timeout, receiver=self, on_done=self._listed,
            on_error=lambda exc: self.status.setText(f"Could not list {root}: {exc}"))

    def _listed(self, entries) -> None:
        self._entries = sorted(entries, key=lambda e: (not e.is_dir, -(e.modified.timestamp() if e.modified else 0)))
        self._fill()

    def _fill(self, *_args) -> None:
        text = self.name_filter.text().casefold()
        cutoff = None
        if self.days.value():
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=self.days.value())
        self.files.clear()
        shown = 0
        for e in self._entries:
            if text and text not in e.name.casefold():
                continue
            modified = e.modified
            if cutoff is not None and not e.is_dir and modified is not None:
                aware = modified if modified.tzinfo else modified.replace(tzinfo=dt.timezone.utc)
                if aware < cutoff:
                    continue
            when = modified.astimezone().strftime("%Y-%m-%d %H:%M") if modified and modified.tzinfo else (
                modified.strftime("%Y-%m-%d %H:%M") if modified else "")
            item = QTreeWidgetItem([e.name + ("/" if e.is_dir else ""), "" if e.is_dir else _size(e.size), when])
            item.setData(0, PATH_ROLE, e.path)
            item.setData(0, DIR_ROLE, e.is_dir)
            self.files.addTopLevelItem(item)
            shown += 1
        self.status.setText(f"{shown} of {len(self._entries)} entries in {self.root.currentText()}")

    def _open_dir(self, item, _col) -> None:
        if item.data(0, DIR_ROLE):
            self.root.setEditText(item.data(0, PATH_ROLE))
            self.reload()

    def _preview(self, item, _prev) -> None:
        if item is None or item.data(0, DIR_ROLE):
            return
        path = item.data(0, PATH_ROLE)
        host = self.session.host(self.host.currentText())
        self.preview.setPlainText("Loading…")
        self.session.runner.submit(
            L.preview, self._transport(), path, label=f"Preview {path}", key=("logs-preview",), host=host.name,
            timeout=host.timeout, receiver=self, on_done=self.preview.setPlainText,
            on_error=lambda exc: self.preview.setPlainText(f"Could not read {path}: {exc}"))

    # -- download --------------------------------------------------------------------
    def _selected(self) -> list[str]:
        return [i.data(0, PATH_ROLE) for i in self.files.selectedItems() if not i.data(0, DIR_ROLE)]

    def _download(self, as_zip: bool) -> None:
        paths = self._selected()
        if not paths:
            self.status.setText("Select one or more files first.")
            return
        host = self.session.host(self.host.currentText())
        if as_zip:
            default = f"{host.name}-logs-{dt.datetime.now():%Y%m%d-%H%M}.zip"
            target, _ = QFileDialog.getSaveFileName(self, "Save ZIP", default, "ZIP (*.zip)")
        else:
            target = QFileDialog.getExistingDirectory(self, "Download into folder")
        if not target:
            return
        self._busy(True)
        self._task = self.session.runner.submit(
            L.download, self._transport(), paths, target, as_zip, label=f"Download {len(paths)} logs",
            host=f"{host.name}\x00download", timeout=None, receiver=self,
            on_done=self._downloaded, on_error=self._download_failed, on_progress=self._progress)

    def _busy(self, busy: bool) -> None:
        self.progress.setVisible(busy)
        self.cancel.setVisible(busy)
        self.dl_files.setEnabled(not busy)
        self.dl_zip.setEnabled(not busy)
        if busy:
            self.progress.setRange(0, 0)

    def _progress(self, payload) -> None:
        done, total, name = payload
        if total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done * 1000 / total))
        self.status.setText(f"{_size(done)} of {_size(total)} · {name.rsplit('/', 1)[-1]}")

    def _downloaded(self, result) -> None:
        self._busy(False)
        where = result.archive or (f"{len(result.outputs)} files" if result.outputs else "nothing")
        text = f"Downloaded {where} ({_size(result.bytes)})"
        if result.cancelled:
            text = "Download cancelled; partial files were removed"
        if result.failures:
            text += f" · {len(result.failures)} failed: " + "; ".join(
                f"{k.rsplit('/', 1)[-1]}: {v}" for k, v in list(result.failures.items())[:3])
        self.status.setText(text)

    def _download_failed(self, exc) -> None:
        self._busy(False)
        self.status.setText(f"Download failed: {exc}")

    def _cancel(self) -> None:
        if self._task is not None:
            self.session.runner.cancel(self._task)
            self._busy(False)
            self.status.setText("Download cancelled")
