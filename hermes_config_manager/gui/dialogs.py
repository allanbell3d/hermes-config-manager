"""Model picker, 'My defaults' popover and the YAML editor for block settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from PySide6.QtCore import QAbstractTableModel, QEventLoop, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QScrollArea, QTableView,
                               QVBoxLayout, QWidget)

from ..core import secrets
from ..core.catalog import ModelInfo
from .theme import T, mono, sans
from .widgets import FlowLayout, IconButton, Popover, btn, label, link, plural, set_prop


# ------------------------------------------------------------------ model picker
class _Models(QAbstractTableModel):
    HEAD = ("Provider", "Model", "In use")

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, str, str, bool | str]] = []    # provider, model, used, typed / "head"
        self.tips: list[str] = []
        self.fonts = (sans(12), mono(), sans(12))
        self.colors = (QColor(T["muted"]), None, QColor(T["faint"]))
        self.head_font = sans(13, 700)
        self.grouped = False

    def set_rows(self, rows, tips=None, grouped: bool = False) -> None:
        self.beginResetModel()
        self.rows = rows
        self.tips = tips or []
        self.grouped = grouped
        self.endResetModel()

    def is_head(self, row: int) -> bool:
        return 0 <= row < len(self.rows) and self.rows[row][3] == "head"

    def flags(self, index):
        if self.is_head(index.row()):
            return Qt.ItemIsEnabled
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 3

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEAD[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        r = self.rows[index.row()]
        c = index.column()
        if r[3] == "head":
            if role == Qt.DisplayRole:
                return r[c]
            if role == Qt.FontRole:
                return self.head_font
            if role == Qt.ForegroundRole:
                return self.colors[2] if c == 2 else None
            if role == Qt.BackgroundRole:
                return QColor(T["head"])
            if role == Qt.ToolTipRole:
                return self.tips[index.row()] if index.row() < len(self.tips) else None
            return None
        if role == Qt.DisplayRole:
            if r[3] and c == 1:
                return f"Use “{r[0]}: {r[1]}” (typed)"
            if c == 0 and self.grouped and not r[3]:
                return ""
            return r[c]
        if role == Qt.FontRole:
            return self.fonts[c]
        if role == Qt.ForegroundRole:
            return self.colors[c]
        if role == Qt.ToolTipRole:
            return self.tips[index.row()] if index.row() < len(self.tips) else f"{r[0]} / {r[1]}"
        return None


class ModalOverlay(QWidget):
    """An in-window modal: a shade over the window with a centred panel.

    Cheaper than a separate native window (no window creation or show on Windows) and it looks
    like the approved design.  ``run(panel)`` blocks in a local event loop until the panel is done.
    """

    def __init__(self, window) -> None:
        super().__init__(window)
        self.window_ = window
        self.panel: QWidget | None = None
        self.hide()
        window.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.window_ and ev.type() == ev.Type.Resize and self.isVisible():
            self._place()
        return False

    def _place(self) -> None:
        w = self.window_
        top = 48 if w.height() > 200 else 0
        self.setGeometry(0, top, w.width(), max(100, w.height() - top - 28))
        if self.panel is not None:
            pw = min(600, self.width() - 40)
            ph = min(520, int(self.height() * 0.9))
            self.panel.setGeometry((self.width() - pw) // 2, max(10, int(self.height() * 0.05)), pw, ph)

    def show_panel(self, panel: QWidget) -> None:
        if panel.parent() is not self:
            panel.setParent(self)
        self.panel = panel
        self._place()
        self.show()
        self.raise_()
        panel.show()

    def run(self, panel) -> None:
        loop = QEventLoop()
        panel.picked.connect(loop.quit)
        self.show_panel(panel)
        panel.q.setFocus()
        loop.exec()
        panel.picked.disconnect(loop.quit)
        self.hide()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 140) if T.dark else QColor(20, 28, 34, 97))

    def mousePressEvent(self, ev) -> None:
        if self.panel is not None and not self.panel.geometry().contains(ev.position().toPoint()):
            self.panel.reject()


class ModelPicker(QFrame):
    """The model picker panel (shown in the window's overlay; built once per window and reused)."""

    picked = Signal()

    def __init__(self, parent, state, title: str = "", provider: str = "",
                 selected: tuple[str, str] | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setObjectName("picker")
        self.setStyleSheet(f"QFrame#picker {{ background: {T['page']}; border: 1px solid {T['line']}; "
                           f"border-radius: 8px; }}")
        self.prov = provider or ""
        self.sel: tuple[str, str] | None = selected
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 12)
        v.setSpacing(10)
        h = QHBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setProperty("h2", True)
        h.addWidget(self.title_lbl)
        h.addStretch(1)
        v.addLayout(h)
        self.q = QLineEdit()
        self.q.setPlaceholderText("Search models (or type a model id)")
        self.q.textChanged.connect(self._fill)
        v.addWidget(self.q)
        self.pills_box = QWidget()
        self.flow = FlowLayout(self.pills_box)
        self.pills: dict[str, Any] = {}
        self._pill_set: tuple = ()
        v.addWidget(self.pills_box)
        self.view = QTableView()
        self.model = _Models()
        self.view.setModel(self.model)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.verticalHeader().hide()
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.verticalHeader().setDefaultSectionSize(26)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.Fixed)
        hh.resizeSection(2, 150)
        hh.setHighlightSections(False)
        self.view.selectionModel().currentRowChanged.connect(self._row)
        self.view.doubleClicked.connect(lambda _i: self._ok())
        v.addWidget(self.view, 1)
        f = QHBoxLayout()
        self.list_lbl = label("", tone="faint", small=True)
        f.addWidget(self.list_lbl)
        ed = link("Edit list", tip="Open your model list in the text editor (created from an example if you have "
                                   "none yet).\nOne model per line, written as - `provider: model`. Save the file; "
                                   "the picker reads it again the next time it opens.")
        ed.clicked.connect(self._edit_list)
        f.addWidget(ed)
        cat = link("Other file…", tip="Use another model list file (Markdown, lines like - `provider: model`), "
                                      "for example one shared with your team")
        cat.clicked.connect(self._catalog_file)
        f.addWidget(cat)
        self.cur = QLabel("")
        self.cur.setFont(mono())
        self.cur.setStyleSheet(f"color: {T['muted']};")
        f.addWidget(self.cur, 1)
        self.default_btn = btn("Default", tip="Put the Hermes default in the table as your edit:\n"
                                              "provider: auto\nmodel: ''\n(Hermes chooses the model). Stage it "
                                              "afterwards.")
        self.default_btn.clicked.connect(self._default)
        f.addWidget(self.default_btn)
        cancel = btn("Cancel", tip="Close the picker without changing anything (Esc)")
        cancel.clicked.connect(self.reject)
        self.ok = btn("Use this model", pri=True, tip="Put the selected model in the table as your edit (Enter or "
                                                      "double-click). Stage it afterwards.")
        self.ok.clicked.connect(self._ok)
        f.addWidget(cancel)
        f.addWidget(self.ok)
        v.addLayout(f)
        self.result_pm: tuple[str, str] | None = None
        self.prepare(title, provider, selected)

    def prepare(self, title: str, provider: str = "", selected: tuple[str, str] | None = None) -> None:
        """Reset for a new pick (title, provider filter, current selection)."""
        self.setWindowTitle(title)
        self.title_lbl.setText(title)
        self.prov = provider or ""
        self.sel = selected
        self.start_sel = selected          # the cell being edited (its provider is used for a typed id)
        self.result_pm = None
        loader = getattr(self.state, "loader", None)
        if loader is not None:
            if loader.reload_if_changed():
                self._listen()
            self._list_text()
        provs = self.state.catalog.providers()
        if self.prov and self.prov not in provs:
            provs = sorted(set(provs) | {self.prov}, key=str.casefold)
        if tuple(provs) != self._pill_set:
            self._pill_set = tuple(provs)
            while self.flow.count():
                w = self.flow.takeAt(0).widget()
                if w is not None:
                    w.hide()
                    w.deleteLater()
            self.pills = {}
            for value, text in [("", "All providers")] + [(p, p) for p in provs]:
                b = btn(text, sm=True, tip="Every provider, grouped" if not value else f"Only the models of {value}")
                b.setProperty("pill", True)
                b.setFixedHeight(22)
                b.clicked.connect(lambda _=False, value=value: self._set_prov(value))
                self.pills[value] = b
                self.flow.addWidget(b)
            fm_s = QFontMetrics(sans(12))
            self.view.horizontalHeader().resizeSection(
                0, min(220, max([fm_s.horizontalAdvance(x) for x in provs] + [70]) + 24))
        self.q.blockSignals(True)
        self.q.clear()
        self.q.blockSignals(False)
        self._set_prov(self.prov)
        self.q.setFocus()

    def _list_text(self) -> None:
        loader = self.state.loader
        path = loader.list_path()
        if loader.file_path:
            self.list_lbl.setText(f"Model list: {path.name} ({plural(len(loader.file_entries), 'model')})")
            self.list_lbl.setToolTip(f"{path}\nThe picker offers the models of this list and the models your agents "
                                     "already use.")
        else:
            self.list_lbl.setText("No model list")
            self.list_lbl.setToolTip(f"Without a list ({path}) the picker offers the models your hosts' Hermes "
                                     "knows (provider_models_cache.json) and the models your agents use. "
                                     "Edit list creates one.")

    def _listen(self) -> None:
        if not getattr(self, "_listening", False):
            self.state.catalog_loaded.connect(self._fill)
            self.state.catalog_loaded.connect(self._list_text)
            self._listening = True

    def _edit_list(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from ..services.catalog_loader import create_model_list

        path = self.state.loader.list_path()
        try:
            create_model_list(path)
        except OSError as exc:
            self.list_lbl.setText(f"Could not create {path.name}: {exc}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _catalog_file(self) -> None:
        cur = str(self.state.loader.list_path())
        path, _ = QFileDialog.getOpenFileName(self, "Model list file", cur, "Markdown (*.md *.txt);;All files (*)")
        if not path:
            return
        self.state.catalog_path = path
        self.state.settings.setValue("catalog/path", path)
        self._listen()
        self.state.loader.reload()

    def _finish(self) -> None:
        if getattr(self, "_listening", False):
            for fn in (self._fill, self._list_text):
                try:
                    self.state.catalog_loaded.disconnect(fn)
                except (RuntimeError, TypeError):
                    pass
            self._listening = False
        self.picked.emit()

    def reject(self) -> None:
        self.result_pm = None
        self._finish()

    def setWindowTitle(self, title: str) -> None:      # (kept for callers that set a title)
        self.title_lbl.setText(title) if hasattr(self, "title_lbl") else None

    def _set_prov(self, value: str) -> None:
        self.prov = value
        for k, b in self.pills.items():
            set_prop(b, "on", k == value)
        self._fill()

    def _fill(self, *_a) -> None:
        text = self.q.text().strip()
        cat = self.state.catalog
        infos: list[ModelInfo] = cat.search(text, self.prov or None)
        grouped = not self.prov
        if grouped:
            infos.sort(key=lambda i: (i.provider.casefold(), -i.use_count, i.model.casefold()))
        else:
            infos.sort(key=lambda i: (-i.use_count, i.model.casefold()))
        counts: dict[str, int] = {}
        for i in infos:
            counts[i.provider] = counts.get(i.provider, 0) + 1
        rows, tips = [], []
        exact = False
        last = None
        for i in infos:
            if grouped and i.provider != last:
                last = i.provider
                rows.append((i.provider, f"{i.provider}  ·  {plural(counts[i.provider], 'model')}", "", "head"))
                tips.append(f"Models of {i.provider}. Click {i.provider} above to show only these.")
            used = plural(i.use_count, "chain") if i.use_count else ""
            rows.append((i.provider, i.model, used, False))
            tips.append(_model_tip(i))
            exact = exact or i.model == text
        if text and not exact:
            # a model that is not in the list: "provider: model", or just the id when a provider is known
            if ": " in text:
                prov, mid = (x.strip() for x in text.split(": ", 1))
            else:
                prov, mid = self.prov or (self.start_sel[0] if self.start_sel else ""), text
            if prov and mid and " " not in mid and " " not in prov:
                rows.insert(0, (prov, mid, "not in the list", True))
                tips.insert(0, f"{prov} / {mid}\nNot in your model list: used exactly as typed.")
            elif " " not in text:
                rows.insert(0, ("", "To use a model that is not listed, type  provider: model", "", "head"))
                tips.insert(0, "Or pick a provider above and type just the model id.")
        self.model.set_rows(rows, tips, grouped)
        self.view.setColumnHidden(0, grouped)        # grouped: the provider is the group's heading
        target = None
        if self.sel:
            target = next((n for n, r in enumerate(rows) if (r[0], r[1]) == self.sel and r[3] != "head"), None)
        if target is None and rows and text:
            target = next((n for n, r in enumerate(rows) if r[3] != "head"), None)
        if target is not None:
            self.view.selectRow(target)
            self.view.scrollTo(self.model.index(target, 0), QAbstractItemView.PositionAtCenter)
        else:
            self.view.clearSelection()
            self._row(QModelIndex())

    def _row(self, index, _prev=None) -> None:
        if index is not None and index.isValid() and self.model.is_head(index.row()):
            return
        if index is not None and index.isValid() and index.row() < len(self.model.rows):
            r = self.model.rows[index.row()]
            self.sel = (r[0], r[1])
            self.cur.setText(f"{r[0]} / {r[1]}")
            self.ok.setEnabled(True)
        else:
            self.cur.setText(f"{self.sel[0]} / {self.sel[1]}" if self.sel else "")
            self.ok.setEnabled(bool(self.sel))

    def _ok(self) -> None:
        if self.sel:
            self.result_pm = self.sel
            self._finish()

    def _default(self) -> None:
        from ..services.chains import AUTO_PM

        self.result_pm = AUTO_PM
        self._finish()

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key_Down, Qt.Key_Up) and self.q.hasFocus():
            self.view.setFocus()
            self.view.keyPressEvent(ev)
            return
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._ok()
            return
        if ev.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(ev)


def _model_tip(i: ModelInfo) -> str:
    lines = [f"{i.provider} / {i.model}"]
    facts = []
    if i.context:
        facts.append(f"context {i.context // 1000}k" if i.context >= 1000 else f"context {i.context}")
    if i.reasoning is not None:
        facts.append("reasoning" if i.reasoning else "no reasoning")
    if i.vision:
        facts.append("vision")
    if i.tool_call is False:
        facts.append("no tool use")
    if i.cost_in is not None and i.cost_out is not None:
        facts.append(f"${i.cost_in:g} / ${i.cost_out:g} per 1M tokens")
    if facts:
        lines.append(" · ".join(facts) + (f"  ({i.meta_source})" if i.meta_source else ""))
    if i.source_labels():
        lines.append("Known from: " + ", ".join(i.source_labels()))
    return "\n".join(lines)


def overlay_for(win) -> ModalOverlay:
    ov = getattr(win, "_overlay", None)
    if ov is None:
        ov = ModalOverlay(win)
        win._overlay = ov
    return ov


def picker_for(parent, state) -> ModelPicker:
    win = parent.window()
    pk = getattr(win, "_picker", None)
    if pk is None or pk.state is not state:
        pk = ModelPicker(overlay_for(win), state)
        pk.hide()
        win._picker = pk
    return pk


def pick_model(parent, state, title: str, provider: str = "", selected=None) -> tuple[str, str] | None:
    pk = picker_for(parent, state)
    pk.prepare(title, provider, selected)
    overlay_for(parent.window()).run(pk)
    return pk.result_pm


# ------------------------------------------------------------------ YAML editor
class YamlDialog(QDialog):
    def __init__(self, state, node, current: Any, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.resize(640, 420)
        crumbs = " › ".join(state.model.crumbs(node.path))
        self.setWindowTitle(crumbs)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 12)
        v.setSpacing(10)
        t = QLabel(crumbs)
        t.setFont(mono(13.5, 600))
        v.addWidget(t)
        if node.desc:
            v.addWidget(label(node.desc, tone="muted", wrap=True))
        self.ed = QPlainTextEdit()
        self.ed.setFont(mono())
        self.ed.setProperty("mono", True)
        self.ed.setToolTip("The block as YAML, starting from the reference agent's value. Leave masked credentials "
                           "as they are: each agent keeps its own.")
        ref = state.ref_name()
        if current is None:
            text = f"# {ref or 'The checked agents'}: no value in the file.\n"
        else:
            text = yaml.safe_dump(secrets.redact(current, tuple(node.path)), sort_keys=False, allow_unicode=True,
                                  default_flow_style=False, width=4096)
            if secrets.contains_mask(secrets.redact(current, tuple(node.path))):
                text = ("# Credentials are shown as " + secrets.MASK + ". Leave them as they are: each agent keeps "
                        "its own.\n") + text
        self.ed.setPlainText(text)
        self.start_text = text
        self.had_value = current is not None
        keys = state.scope().usable_keys
        if len(keys) > 1:
            n_values = len(state.distribution(node, keys))
            if n_values > 1:
                v.addWidget(label(f"The {len(keys)} checked agents have {n_values} different values here. Staging "
                                  "this YAML replaces the whole block for all of them; each keeps only its own "
                                  "credentials.", tone="amber", wrap=True))
        v.addWidget(self.ed, 1)
        self.err = label("", tone="bad", wrap=True)
        self.err.hide()
        v.addWidget(self.err)
        f = QHBoxLayout()
        f.addStretch(1)
        c = btn("Cancel", tip="Close without changing anything")
        c.clicked.connect(self.reject)
        ok = btn("Use this YAML", pri=True, tip="Puts this YAML in the row as your edit. Stage it afterwards.")
        ok.clicked.connect(self._ok)
        f.addWidget(c)
        f.addWidget(ok)
        v.addLayout(f)
        self.value: Any = None

    def _ok(self) -> None:
        text = self.ed.toPlainText()
        if text == self.start_text:
            self.reject()                     # nothing changed: no edit
            return
        try:
            self.value = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            self.err.setText(f"Not valid YAML: {exc}")
            self.err.show()
            return
        if self.value is None:
            # empty (or only comments): never stage "key: null" by accident
            self.err.setText("The YAML is empty. To take the setting out of the files, use “Remove this setting "
                             "from the files” (right-click the row)." if self.had_value else
                             "Nothing to use yet: type the block's YAML, or Cancel.")
            self.err.show()
            return
        self.accept()


# ------------------------------------------------------------------ my defaults
@dataclass
class _DF:
    opened: set[int] = field(default_factory=lambda: {0})
    edit: int = -1
    draft: dict | None = None
    saving: bool = False


class DefaultsPopup:
    """'My defaults': cards with a readable chain table; Use fills the chain table (nothing staged)."""

    _state_by_key: dict[str, _DF] = {}

    def __init__(self, state, page, key: str) -> None:
        self.state, self.page, self.key = state, page, key
        self.df = DefaultsPopup._state_by_key.setdefault(key, _DF())
        self.pop: Popover | None = None
        self.table = None
        self.anchor = None

    def show_for(self, table, anchor) -> None:
        self.table, self.anchor = table, anchor
        self._open()

    def _open(self) -> None:
        pop = Popover(self.page)
        self.pop = pop
        outer = QVBoxLayout(pop)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        inner.setProperty("surface", "page")
        inner.setAttribute(Qt.WA_StyledBackground, True)
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 6, 0, 6)
        v.setSpacing(0)
        t = label("My defaults · Use fills the table; nothing is staged yet", tone="muted", small=True)
        t.setContentsMargins(12, 2, 12, 6)
        v.addWidget(t)
        items = self.state.defaults.items
        if not items:
            e = label("Nothing saved yet.", tone="muted", small=True)
            e.setContentsMargins(12, 4, 12, 6)
            v.addWidget(e)
        for i, d in enumerate(items):
            v.addWidget(self._card(i, d))
        v.addWidget(self._footer())
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        g = self.table.mapToGlobal(QPoint(0, 0))
        pop.show_at(self.anchor, left=g.x(), width=max(420, self.table.width()))
        if self.df.saving:
            name = pop.findChild(QLineEdit, "dfname")
            if name is not None:
                name.setFocus()

    def _reopen(self) -> None:
        if self.pop is not None:
            self.pop.close()
        self._open()

    def _card(self, i: int, d) -> QWidget:
        df = self.df
        editing = df.edit == i
        chain = df.draft["chain"] if editing else d.chain
        opened = editing or i in df.opened
        card = QWidget()
        card.setObjectName("dfc")
        card.setStyleSheet(f"QWidget#dfc {{ border: 1px solid {T['line']}; border-radius: 6px; }}")
        card.setAttribute(Qt.WA_StyledBackground, True)
        wrap = QVBoxLayout()
        wrap.setContentsMargins(8, 6, 8, 0)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(1, 1, 1, 1)
        cv.setSpacing(0)
        head = QWidget()
        head.setAttribute(Qt.WA_StyledBackground, True)
        head.setObjectName("dfh")
        head.setStyleSheet(f"QWidget#dfh {{ background: {T['head']}; border-radius: 5px; }}")
        h = QHBoxLayout(head)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)
        if editing:
            name = QLineEdit(df.draft["name"])
            name.setPlaceholderText("Name")
            name.setToolTip("The name of this default")
            name.textChanged.connect(lambda s: df.draft.__setitem__("name", s))
            h.addWidget(name, 1)
            sv = btn("Save", sm=True, pri=True, tip="Keep the changes to this default (stored in this app only)")
            sv.clicked.connect(lambda: self._edit_save(i))
            cc = btn("Cancel", sm=True, tip="Forget the changes to this default")
            cc.clicked.connect(self._edit_cancel)
            h.addWidget(sv)
            h.addWidget(cc)
        else:
            tg = IconButton("chev_open" if opened else "chev", "Show or hide the models of this default", size=18, color="faint", icon_size=10)
            tg.clicked.connect(lambda: self._toggle(i))
            h.addWidget(tg)
            n = QLabel(d.name)
            n.setFont(sans(13, 600))
            n.setWordWrap(True)
            h.addWidget(n, 1)
            if not opened:
                h.addWidget(label(plural(len(chain), "model"), tone="muted", small=True))
            use = btn("Use", sm=True, pri=True, tip="Fill the chain table with this default. It is an edit on the "
                                                    "page until you stage it.")
            use.clicked.connect(lambda: self._use(i))
            ed = btn("Edit", sm=True, tip="Rename this default or change its models, efforts and order")
            ed.clicked.connect(lambda: self._edit(i))
            dl = IconButton("x", "Delete this default (from this app only; no config is changed)")
            dl.clicked.connect(lambda: self._delete(i))
            h.addWidget(use)
            h.addWidget(ed)
            h.addWidget(dl)
        cv.addWidget(head)
        if opened:
            cv.addWidget(self._chain_table(chain, editing, i))
        holder = QWidget()
        hv = QVBoxLayout(holder)
        hv.setContentsMargins(8, 6, 8, 0)
        hv.addWidget(card)
        return holder

    def _chain_table(self, chain, editing: bool, i: int) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(8, 4, 8, 6)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(3)
        for c, text in enumerate(("Role", "Provider", "Model", "Effort")):
            g.addWidget(label(text, tone="muted", small=True), 0, c)
        efforts = self.state.model.efforts
        tips = ("Click to choose the provider (opens the model picker)", "Click to choose the model",
                "Reasoning effort; default leaves it out")
        for r, row in enumerate(chain, start=1):
            k = r - 1
            g.addWidget(label("Head" if k == 0 else f"Fallback {k}", tone="muted"), r, 0)
            if editing:
                pb = link(row[0] or "provider", tip=tips[0])
                pb.clicked.connect(lambda _=False, k=k: self._row_pick(k, provider_first=True))
                g.addWidget(pb, r, 1)
                mb = link(row[1] or "choose model", tip=tips[1])
                mb.setFont(mono())
                mb.clicked.connect(lambda _=False, k=k: self._row_pick(k))
                g.addWidget(mb, r, 2)
                cb = QComboBox()
                cb.addItems(["default"] + efforts)
                cb.setToolTip(tips[2])
                cb.setCurrentIndex((efforts.index(row[2]) + 1) if row[2] in efforts else 0)
                cb.activated.connect(lambda _x, cb=cb, k=k: self.df.draft["chain"][k].__setitem__(
                    2, None if cb.currentIndex() == 0 else cb.currentText()))
                g.addWidget(cb, r, 3)
                acts = QWidget()
                ah = QHBoxLayout(acts)
                ah.setContentsMargins(0, 0, 0, 0)
                ah.setSpacing(0)
                for name, tip, en, fn in (("up", "Move up", k > 0, lambda k=k: self._row_move(k, -1)),
                                          ("down", "Move down", k < len(chain) - 1, lambda k=k: self._row_move(k, 1)),
                                          ("x", "Remove", len(chain) > 1, lambda k=k: self._row_del(k))):
                    b = IconButton(name, tip, size=18, icon_size=12)
                    b.setEnabled(en)
                    b.clicked.connect(fn)
                    ah.addWidget(b)
                g.addWidget(acts, r, 4)
            else:
                g.addWidget(label(row[0] or ""), r, 1)
                m = label(row[1] or "", wrap=True)
                m.setFont(mono())
                g.addWidget(m, r, 2)
                g.addWidget(label(row[2] or "default", tone=None if row[2] else "faint"), r, 3)
        g.setColumnStretch(2, 1)
        if editing:
            add = link("+ Add fallback", tip="Add a model at the end of this default's chain")
            add.clicked.connect(self._row_add)
            g.addWidget(add, len(chain) + 1, 0, 1, 3)
        return w

    def _footer(self) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(12, 8, 12, 4)
        h.setSpacing(8)
        cur = self.state.work.chain_rows(self.key, self.state.scope(), self.state.ref_key())
        if self.df.saving:
            name = QLineEdit()
            name.setObjectName("dfname")
            name.setPlaceholderText("Name for this default")
            name.setToolTip("Type a name and press Enter to save the table's chain as a default")
            name.returnPressed.connect(lambda: self._save_current(name.text()))
            h.addWidget(name, 1)
            sv = btn("Save", sm=True, pri=True, tip="Save the chain shown in the table under this name")
            sv.clicked.connect(lambda: self._save_current(name.text()))
            h.addWidget(sv)
        else:
            s = btn("Save current as default…", sm=True, tip="Save the chain shown in the table as a new default "
                                                              "(stored in this app, not in any config)")
            s.setEnabled(bool(cur))
            s.clicked.connect(self._start_save)
            h.addWidget(s)
            h.addStretch(1)
        more = IconButton("more", "Import or export your defaults")
        more.clicked.connect(lambda: self._more(more))
        h.addWidget(more)
        return w

    # -- actions ---------------------------------------------------------------------
    def _toggle(self, i: int) -> None:
        (self.df.opened.discard if i in self.df.opened else self.df.opened.add)(i)
        self._reopen()

    def _use(self, i: int) -> None:
        d = self.state.defaults.items[i]
        if self.pop:
            self.pop.close()
        self.page.use_default(self.key, d.chain)
        self.state.say(f"Filled the {self._title()} table from “{d.name}”. Not staged yet.")

    def _title(self) -> str:
        from .chain_table import chain_title

        return chain_title(self.key)

    def _edit(self, i: int) -> None:
        d = self.state.defaults.items[i]
        self.df.edit, self.df.saving = i, False
        self.df.draft = {"name": d.name, "chain": [list(r) for r in d.chain]}
        self._reopen()

    def _edit_save(self, i: int) -> None:
        self.state.defaults.replace(i, self.df.draft["name"], self.df.draft["chain"])
        self.df.opened.add(i)
        self.df.edit, self.df.draft = -1, None
        self._reopen()

    def _edit_cancel(self) -> None:
        self.df.edit, self.df.draft = -1, None
        self._reopen()

    def _delete(self, i: int) -> None:
        self.state.defaults.delete(i)
        self.df.opened = {x - 1 if x > i else x for x in self.df.opened if x != i} or {0}
        self.df.edit, self.df.draft = -1, None
        self._reopen()

    def _row_pick(self, k: int, provider_first: bool = False) -> None:
        chain = self.df.draft["chain"]
        row = chain[k]
        if self.pop:
            self.pop.close()
        picked = pick_model(self.page, self.state, ("Head" if k == 0 else f"Fallback {k}") + " model · default",
                            row[0] or "", (row[0], row[1]) if row[0] and row[1] else None)
        if picked:
            row[0], row[1] = picked
        self._open()

    def _row_move(self, k: int, d: int) -> None:
        c = self.df.draft["chain"]
        j = k + d
        if 0 <= j < len(c):
            c[k], c[j] = c[j], c[k]
        self._reopen()

    def _row_del(self, k: int) -> None:
        c = self.df.draft["chain"]
        if len(c) > 1:
            del c[k]
        self._reopen()

    def _row_add(self) -> None:
        if self.pop:
            self.pop.close()
        picked = pick_model(self.page, self.state, "Add a fallback · default", "", None)
        if picked:
            self.df.draft["chain"].append([picked[0], picked[1], None])
        self._open()

    def _start_save(self) -> None:
        self.df.saving, self.df.edit, self.df.draft = True, -1, None
        self._reopen()

    def _save_current(self, name: str) -> None:
        cur = self.state.work.chain_rows(self.key, self.state.scope(), self.state.ref_key())
        if not cur:
            return
        self.state.defaults.add(name, cur)
        self.df.saving = False
        self.df.opened.add(len(self.state.defaults.items) - 1)
        self._reopen()

    def _more(self, anchor) -> None:
        m = QMenu(self.page)
        m.setToolTipsVisible(True)
        imp = m.addAction("Import defaults…")
        imp.setToolTip("Add the defaults from a JSON file exported earlier (yours or someone else's)")
        exp = m.addAction("Export defaults…")
        exp.setToolTip("Save all your defaults to a JSON file, to keep or share")
        exp.setEnabled(bool(self.state.defaults.items))
        chosen = m.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if chosen is imp:
            if self.pop:
                self.pop.close()
            path, _ = QFileDialog.getOpenFileName(self.page, "Import defaults", "", "Defaults (*.json)")
            if path:
                try:
                    n = self.state.defaults.import_file(Path(path))
                    self.state.say(f"Imported {plural(n, 'default')}.")
                except (OSError, ValueError) as exc:
                    self.state.say(f"Could not import: {exc}")
            self._open()
        elif chosen is exp:
            if self.pop:
                self.pop.close()
            path, _ = QFileDialog.getSaveFileName(self.page, "Export defaults", "my-defaults.json",
                                                  "Defaults (*.json)")
            if path:
                n = self.state.defaults.export(Path(path))
                self.state.say(f"Exported {plural(n, 'default')} to {path}")
            self._open()
