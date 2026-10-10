"""Menu pane: categories are boxes, pages are the rows inside.

Customize mode adds an eye (hide / show) and arrows (reorder) to every category and page, plus
the arrangement shortcuts.  Hidden pages stay reachable under "Hidden (n)" and through search.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..core.layout import hermes_order_layout
from .theme import T, mono, sans
from .widgets import clear_layout, Chevron, Clickable, IconButton, btn, label, link, plural


class PageItem(Clickable):
    def __init__(self, text: str, key_page: bool, narrow: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.on = False
        self._hover = False
        self.lay = QHBoxLayout(self)
        self.lay.setContentsMargins(14 if narrow else 24, 2, 8, 2)
        self.lay.setSpacing(5)
        self.lb = QLabel(text)
        self.lb.setWordWrap(True)
        if key_page:
            self.lb.setFont(mono())
        self.lay.addWidget(self.lb, 1)
        self.setMinimumHeight(25)

    def set_on(self, on: bool) -> None:
        self.on = on
        f = self.lb.font()
        f.setWeight(f.Weight.DemiBold if on else f.Weight.Normal)
        self.lb.setFont(f)
        self.update()

    def set_off(self, off: bool) -> None:
        f = self.lb.font()
        f.setStrikeOut(off)
        self.lb.setFont(f)
        self.lb.setStyleSheet(f"color: {T['faint']};" if off else "")

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        if self.on:
            p.fillRect(self.rect(), T.c("sel"))
            p.fillRect(0, 0, 2, self.height(), T.c("accent"))
        elif self._hover:
            p.fillRect(self.rect(), T.c("hover"))
        super().paintEvent(ev)


class CatBox(QFrame):
    """A category: a box with a header; the whole box is outlined while the mouse is over it."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hover = False
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(1, 1, 1, 1)
        self.lay.setSpacing(0)

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(T.c("page"))
        p.setPen(QPen(T.c("accent") if self._hover else T.c("line"), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 6, 6)


class MenuPane(QWidget):
    page_chosen = Signal(str)
    customize_toggled = Signal(bool)

    def __init__(self, state, counter, parent=None) -> None:
        super().__init__(parent)
        self.state, self.counter = state, counter
        self.setProperty("pane", True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.hidden_open = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QWidget()
        self.hl = QVBoxLayout(self.head)
        self.hl.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.scroll, 1)
        self.items: dict[str, PageItem] = {}
        state.layout_changed.connect(self.rebuild)
        state.model_changed.connect(self.rebuild)
        state.work_changed.connect(self.refresh_marks)
        state.values_changed.connect(self.refresh_marks)
        counter.changed.connect(self.refresh_marks)
        self.rebuild()

    # ------------------------------------------------------------------ build
    def _header(self) -> None:
        clear_layout(self.hl)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 10, 6, 8)
        if self.state.customizing:
            h.addWidget(label("Customize", bold=True))
            h.addStretch(1)
            done = btn("Done", sm=True, pri=True, tip="Leave Customize. Your arrangement is saved as you go.")
            done.clicked.connect(lambda: self.set_customizing(False))
            h.addWidget(done)
            self.hl.addWidget(row)
            tools = QWidget()
            tl = QVBoxLayout(tools)
            tl.setContentsMargins(12, 0, 12, 10)
            tl.setSpacing(1)
            tl.addWidget(label("The eye hides or shows a category, a page, a section or a single setting. "
                               "Arrows reorder.", tone="muted", small=True, wrap=True))
            tips = {
                "Show only what my configs use": "Show a setting only when at least one loaded config contains it "
                                                 "(the default). Everything else stays reachable through search.",
                "Show everything": "Show every setting Hermes knows, including the ones none of your configs use",
                "Hermes dashboard order": "Order sections and settings as the Hermes dashboard exports them. "
                                          "Related settings are kept.",
                "Reset to default": "Undo all your hiding, showing and reordering. Related settings are kept. "
                                    "Your configs are not touched.",
            }
            for text, fn in (("Show only what my configs use", self._mine), ("Show everything", self._all),
                             ("Hermes dashboard order", self._hermes), ("Reset to default", self._reset)):
                b = link(text, tip=tips[text])
                b.setFont(sans(12.5))
                b.clicked.connect(fn)
                tl.addWidget(b)
            self.hl.addWidget(tools)
        else:
            title = label("Settings", bold=True)
            title.setToolTip("Categories and their pages. A page shows one or more sections of config.yaml; an "
                             "amber number counts the settings where the checked agents differ.")
            h.addWidget(title)
            h.addStretch(1)
            cu = btn("Customize", sm=True, tip="Hide, show and reorder categories, pages, sections and single settings")
            cu.clicked.connect(lambda: self.set_customizing(True))
            h.addWidget(cu)
            self.hl.addWidget(row)

    def rebuild(self) -> None:
        st = self.state
        self._header()
        inner = QWidget()
        inner.setProperty("pane", True)
        il = QVBoxLayout(inner)
        il.setContentsMargins(8, 0, 3, 10)
        il.setSpacing(6)
        self.items = {}
        cust = st.customizing
        cats = st.layout.cats
        cur = st.current_page()
        for ci, c in enumerate(cats):
            pages = c.pages if cust else [p for p in c.pages if st.page_shown(c, p)]
            if not cust and not pages:
                continue
            opened = c.id not in st.cat_shut
            box = CatBox()
            head = Clickable()
            head.setObjectName("mh")
            head.setStyleSheet(f"QFrame#mh {{ background: {T['head']}; border-radius: 5px; }}")
            hl = QHBoxLayout(head)
            hl.setContentsMargins(5, 2, 8, 2)
            hl.setSpacing(5)
            hl.addWidget(Chevron(opened))
            if cust:
                eye = IconButton("eye_off" if c.hidden else "eye", "Show this category" if c.hidden else
                                 "Hide this category", color="faint" if c.hidden else "muted")
                eye.clicked.connect(lambda _=False, c=c: self._cat_eye(c))
                hl.addWidget(eye)
            lb = QLabel(c.label)
            lb.setFont(sans(13, 600))
            lb.setWordWrap(True)
            if c.hidden:
                f = lb.font()
                f.setStrikeOut(True)
                lb.setFont(f)
                lb.setStyleSheet(f"color: {T['faint']};")
            hl.addWidget(lb, 1)
            if cust and len(cats) > 1:
                up = IconButton("up", "Move category up")
                up.setEnabled(ci > 0)
                up.clicked.connect(lambda _=False, i=ci: self._move_cat(i, -1))
                dn = IconButton("down", "Move category down")
                dn.setEnabled(ci < len(cats) - 1)
                dn.clicked.connect(lambda _=False, i=ci: self._move_cat(i, 1))
                hl.addWidget(up)
                hl.addWidget(dn)
            elif not opened:
                hl.addWidget(label(plural(len(pages), "page"), tone="faint", small=True))
                n = sum(self.counter.page(p) for p in pages)
                if n:
                    d = label(str(n), tone="amber", small=True)
                    d.setToolTip(f"{n} settings differ across the checked agents")
                    hl.addWidget(d)
            head.setMinimumHeight(27)
            head.setToolTip(f"{c.label}: click to open or close this category")
            head.clicked.connect(lambda c=c: self._toggle_cat(c.id))
            box.lay.addWidget(head)
            if opened:
                for pi, p in enumerate(pages):
                    item = self._page_item(c, p, pi, len(pages), cust, cur)
                    if pi > 0:
                        sep = QFrame()
                        sep.setFixedHeight(1)
                        sep.setStyleSheet(f"background: {T['soft']};")
                        box.lay.addWidget(sep)
                    box.lay.addWidget(item)
            il.addWidget(box)
        if not cust:
            hidden = [p for c in cats for p in c.pages if not st.page_shown(c, p)]
            if hidden:
                box = CatBox()
                head = Clickable()
                hl = QHBoxLayout(head)
                hl.setContentsMargins(5, 2, 8, 2)
                hl.addWidget(Chevron(self.hidden_open))
                hl.addWidget(label(f"Hidden ({len(hidden)})", tone="muted"), 1)
                head.setToolTip("Pages you have hidden. They stay reachable here and through search.")
                head.setMinimumHeight(27)
                head.clicked.connect(self._toggle_hidden)
                box.lay.addWidget(head)
                if self.hidden_open:
                    for p in hidden:
                        item = PageItem(p.label, self._key_page(p), self.window().width() < 1100)
                        item.lb.setStyleSheet(f"color: {T['muted']};")
                        item.set_on(cur is not None and cur.id == p.id)
                        item.clicked.connect(lambda pid=p.id: self.page_chosen.emit(pid))
                        self.items[p.id] = item
                        box.lay.addWidget(item)
                il.addWidget(box)
        il.addStretch(1)
        old = self.scroll.takeWidget()
        pos = self.scroll.verticalScrollBar().value()
        self.scroll.setWidget(inner)
        self.scroll.verticalScrollBar().setValue(pos)
        if old is not None:
            old.deleteLater()
        self.refresh_marks()

    @staticmethod
    def _key_page(p) -> bool:
        return len(p.sections) == 1 and p.label == p.sections[0]

    def _page_item(self, c, p, pi, n, cust, cur) -> PageItem:
        st = self.state
        item = PageItem(p.label, self._key_page(p), self.window().width() < 1100)
        item.setToolTip("Sections on this page: " + ", ".join(p.sections) if p.sections else "An empty page")
        if cust:
            item.lay.setContentsMargins(6, 2, 8, 2)
            eye = IconButton("eye_off" if p.hidden else "eye", "Show this page" if p.hidden else "Hide this page",
                             color="faint" if p.hidden else "muted")
            eye.clicked.connect(lambda _=False, p=p: self._page_eye(p))
            item.lay.insertWidget(0, eye)
            up = IconButton("up", "Move page up")
            up.setEnabled(pi > 0)
            up.clicked.connect(lambda _=False, c=c, i=pi: self._move_page(c, i, -1))
            dn = IconButton("down", "Move page down")
            dn.setEnabled(pi < n - 1)
            dn.clicked.connect(lambda _=False, c=c, i=pi: self._move_page(c, i, 1))
            item.lay.addWidget(up)
            item.lay.addWidget(dn)
        else:
            item.ed = label("●", tone="accent")
            item.ed.setFont(sans(9))
            item.ed.setToolTip("This page has edits that are not staged")
            item.dn = label("", tone="amber", small=True)
            item.dn.setToolTip("How many settings on this page differ across the checked agents")
            item.dn.setMinimumWidth(item.dn.fontMetrics().horizontalAdvance("9999"))
            item.dn.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.lay.addWidget(item.ed)
            item.lay.addWidget(item.dn)
        item.set_off(c.hidden or p.hidden or not st.visible_sections(p))
        item.set_on(cur is not None and cur.id == p.id)
        item.setToolTip(", ".join(p.sections))
        item.clicked.connect(lambda pid=p.id: self.page_chosen.emit(pid))
        self.items[p.id] = item
        return item

    # ------------------------------------------------------------- live marks
    def refresh_marks(self, *_args) -> None:
        st = self.state
        if st.customizing:
            return
        scope = st.scope()
        work_secs = set()
        for p in st.work.values:
            s = st.model.section_of(p)
            if s and st.work.dirty(p, scope):
                work_secs.add(s)
        for key in st.work.chains:
            if st.work.chain_dirty(key, scope):
                work_secs.add("model" if key == "primary" else "delegation" if key == "delegation" else "auxiliary")
        for pid, item in self.items.items():
            p = st.layout.page(pid)
            if p is None or not hasattr(item, "dn"):
                continue
            pins = st.layout.pins.get(pid, [])
            unstaged = bool(work_secs & set(p.sections)) or any(
                tuple(x) in st.work.values and st.work.dirty(tuple(x), scope) for x in pins)
            item.ed.setVisible(unstaged)
            n = self.counter.page(p)
            item.dn.setText(str(n) if n else "")
            item.dn.setToolTip(f"{n} settings differ across the checked agents" if n else "")

    def select(self, page_id: str) -> None:
        for pid, item in self.items.items():
            item.set_on(pid == page_id)
        item = self.items.get(page_id)
        if item is not None:
            self.scroll.ensureWidgetVisible(item, 0, 20)

    # ----------------------------------------------------------------- actions
    def set_customizing(self, on: bool) -> None:
        self.state.customizing = on
        self.rebuild()
        self.customize_toggled.emit(on)

    def _toggle_cat(self, cid: str) -> None:
        (self.state.cat_shut.discard if cid in self.state.cat_shut else self.state.cat_shut.add)(cid)
        self.state.save_open()
        self.rebuild()

    def _toggle_hidden(self) -> None:
        self.hidden_open = not self.hidden_open
        self.rebuild()

    def _changed(self) -> None:
        self.state.save_layout()

    def _cat_eye(self, c) -> None:
        c.hidden = not c.hidden
        self._changed()

    def _page_eye(self, p) -> None:
        p.hidden = not p.hidden
        self._changed()

    def _move_cat(self, i: int, d: int) -> None:
        cats = self.state.layout.cats
        j = i + d
        if 0 <= j < len(cats):
            cats[i], cats[j] = cats[j], cats[i]
            self._changed()

    def _move_page(self, c, i: int, d: int) -> None:
        j = i + d
        if 0 <= j < len(c.pages):
            c.pages[i], c.pages[j] = c.pages[j], c.pages[i]
            self._changed()

    def _mine(self) -> None:
        self.state.layout.show_mine()
        self._changed()

    def _all(self) -> None:
        self.state.layout.show_all()
        self._changed()

    def _hermes(self) -> None:
        st = self.state
        cur = st.current_page()
        first = cur.sections[0] if cur and cur.sections else None
        st.layout = hermes_order_layout(st.model, st.layout)
        st.cat_shut.discard("flat")
        if first:
            st.set_page(f"flat/{first}")
        st.save_layout()
        st.say("One page per section: Hermes dashboard order first, then the rest alphabetically")

    def _reset(self) -> None:
        st = self.state
        cur = st.current_page()
        first = cur.sections[0] if cur and cur.sections else None
        st.reset_layout()
        win = self.window()
        if hasattr(win, "_default_split"):
            win._default_split()
            st.set_columns(35.0, 19.0, save=True)
        p = st.layout.page_of(first) if first else None
        if p:
            st.set_page(p.id)
            st.layout_changed.emit()
        st.say("Menu, pages and hidden items reset to the default arrangement")
