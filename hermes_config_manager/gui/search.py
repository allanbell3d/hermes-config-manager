"""Search every setting (Ctrl+K): results show where a setting lives; Enter goes there.

The same box picks a setting to add to Related settings ("pin mode").
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from .theme import T, mono
from .widgets import set_prop


SEARCH_TIP = ("Search every setting Hermes knows by key or path, also hidden ones (Ctrl+K). The results show where "
              "each setting lives; Enter or a click goes there.")
PIN_TIP = "Type to find the setting to repeat under Related settings on this page; Enter adds it. Esc cancels."


class SearchBox(QLineEdit):
    chosen = Signal(str, object)        # section, path (None = the section itself)
    pinned = Signal(str, object)        # page id, path

    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setPlaceholderText("Search settings")
        self.setToolTip(SEARCH_TIP)
        self.setProperty("search", True)
        self.setFixedHeight(30)
        self.setMinimumWidth(180)
        self.setMaximumWidth(340)
        self.pin_page: str | None = None
        self.index: list[tuple] = []
        self._index_ver = None
        self.pop = QFrame(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self.pop.setObjectName("qres")
        self.pop.setStyleSheet(f"QFrame#qres {{ background: {T['page']}; border: 1px solid {T['line']}; "
                               f"border-radius: 6px; }}")
        pv = QVBoxLayout(self.pop)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)
        self.note = QLabel("")
        self.note.setStyleSheet(f"color: {T['muted']}; padding: 8px 10px;")
        pv.addWidget(self.note)
        self.list = QListWidget()
        self.list.setStyleSheet("QListWidget { border: 0; }")
        self.list.setUniformItemSizes(True)
        self.list.itemClicked.connect(self._go)
        self.list.setMouseTracking(True)
        pv.addWidget(self.list)
        self.more = QLabel("")
        self.more.setStyleSheet(f"color: {T['muted']}; padding: 6px 10px;")
        pv.addWidget(self.more)
        self.textEdited.connect(self._run)
        self.installEventFilter(self)
        hint = QLabel("Ctrl+K", self)
        hint.setFont(mono(11))
        hint.setStyleSheet(f"color: {T['muted']}; border: 1px solid {T['line']}; border-radius: 3px; "
                           f"padding: 0 4px; background: {T['pane']};")
        hint.adjustSize()
        self.hint = hint
        self.setTextMargins(0, 0, hint.width() + 10, 0)
        state.model_changed.connect(self._model_changed)

    def _model_changed(self) -> None:
        self._index_ver = None

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self.hint.move(self.width() - self.hint.width() - 7, (self.height() - self.hint.height()) // 2)

    # -- index -----------------------------------------------------------------------
    def _build_index(self) -> None:
        model = self.state.model
        idx = []
        for sec in model.sections.values():
            idx.append((sec.key, None, [sec.key], f"{sec.key} {sec.label}".casefold(), sec.key.casefold(), True, False))
        for path, node in model.nodes.items():
            crumbs = model.crumbs(path)
            idx.append((node.section, path, crumbs, " ".join(crumbs).casefold(), node.key.casefold(), False,
                        not node.is_group))
        self.index = idx
        self._index_ver = id(model)

    def _run(self, *_a) -> None:
        if self._index_ver != id(self.state.model):
            self._build_index()
        q = self.text().strip().casefold()
        self.list.clear()
        if not q:
            if self.pin_page:
                self.note.setText("Type the name of the setting to add to Related settings on this page.")
                self.note.show()
                self.list.hide()
                self.more.hide()
                self._show()
            else:
                self.pop.hide()
            return
        import re

        toks = [t for t in re.split(r"[\s›>.]+", q) if t]
        last = toks[-1] if toks else ""
        res = []
        for it in self.index:
            sec, path, crumbs, hay, key, is_sec, leaf = it
            if self.pin_page and not leaf:
                continue
            if all(t in hay for t in toks):
                rank = (0 if key.startswith(toks[0]) else 1) if is_sec else 2 if key.startswith(last) else \
                    3 if last in key else 4
                res.append((rank, len(crumbs), hay, it))
        res.sort(key=lambda r: r[:3])
        shown = res[:40]
        lay = self.state.layout
        for _r, _n, _h, (sec, path, crumbs, _hay, _k, _s, _l) in shown:
            page = lay.page_of(sec)
            node = self.state.model.nodes.get(path) if path else None
            hidden = self.state.section_hidden(sec) or (node is not None and self.state.node_hidden(node))
            text = "  ›  ".join(crumbs)
            item = QListWidgetItem(f"{text}        {'hidden · ' if hidden else ''}page {page.label if page else ''}")
            item.setData(Qt.UserRole, (sec, path))
            item.setFont(mono())
            self.list.addItem(item)
        self.note.setText("Pick a setting to add to Related settings" if self.pin_page and shown else
                          "Nothing matches." if not shown else "")
        self.note.setVisible(bool(self.note.text()))
        self.list.setVisible(bool(shown))
        self.more.setText(f"Showing 40 of {len(res)}. Type more to narrow it down." if len(res) > 40 else "")
        self.more.setVisible(len(res) > 40)
        if shown:
            self.list.setCurrentRow(0)
        self._show()

    def _show(self) -> None:
        w = 520
        g = self.mapToGlobal(QPoint(self.width() - w, self.height() + 4))
        rows = self.list.count()
        h = (self.note.sizeHint().height() if self.note.isVisible() else 0) + \
            (min(rows, 14) * 26 + 6 if self.list.isVisible() else 0) + \
            (self.more.sizeHint().height() if self.more.isVisible() else 0)
        self.pop.setFixedSize(w, max(36, h))
        self.pop.move(max(8, g.x()), g.y())
        self.pop.show()
        self.pop.raise_()

    def hide_results(self) -> None:
        self.pop.hide()

    # -- choosing -----------------------------------------------------------------------
    def _go(self, item) -> None:
        sec, path = item.data(Qt.UserRole)
        self.pop.hide()
        self.clear()
        if self.pin_page:
            page = self.pin_page
            self.end_pin()
            self.pinned.emit(page, path)
        else:
            self.chosen.emit(sec, path)
        self.clearFocus()

    def start_pin(self, page_id: str) -> None:
        self.pin_page = page_id
        set_prop(self, "pin", True)
        self.setPlaceholderText("Setting to add…")
        self.setToolTip(PIN_TIP)
        self.clear()
        self.setFocus()
        self._run()

    def end_pin(self) -> None:
        if self.pin_page is None:
            return
        self.pin_page = None
        set_prop(self, "pin", False)
        self.setPlaceholderText("Search settings")
        self.setToolTip(SEARCH_TIP)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Down, Qt.Key_Up) and self.list.count():
                r = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                self.list.setCurrentRow(r % self.list.count())
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter) and self.list.currentItem() is not None and self.pop.isVisible():
                self._go(self.list.currentItem())
                return True
            if k == Qt.Key_Escape:
                self.pop.hide()
                self.end_pin()
                self.clear()
                self.clearFocus()
                return True
        if obj is self and ev.type() == QEvent.FocusOut:
            if not self.pop.underMouse():
                self.pop.hide()
                self.end_pin()
        if obj is self and ev.type() == QEvent.FocusIn and (self.text().strip() or self.pin_page):
            self._run()
        return super().eventFilter(obj, ev)
