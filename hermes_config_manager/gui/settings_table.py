"""Settings tables: Setting | Value | Status, one row per setting, groups that fold.

A row shows the user's edit if there is one, otherwise the value of the *reference agent* (the
agent you clicked if it is checked, else the first agent you checked).  Status says whether the checked
agents differ ("N values"), whether the row is "edited, not staged", or "staged · N agents".
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QCursor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolTip,
                               QVBoxLayout, QWidget)

from ..core.paths import MISSING
from ..core.settings_tree import Node, key_tags, leaves
from ..services.work import REMOVE
from .editors import BlockEditor, edit_hint, make_editor, shown
from .theme import T, mono, sans
from .widgets import tip_html, agent_names, clear_layout, Chevron, Clickable, IconButton, differ, label, link, plural

ZW = "​"


def wrap_key(key: str) -> str:
    return str(key).replace("_", "_" + ZW).replace(".", "." + ZW).replace("-", "-" + ZW)


def gid(path) -> str:
    return "|".join(str(p) for p in path)


class Ctx:
    """What a table needs to know about the page it sits on."""

    def __init__(self, page, q: str = "", only_diff: bool = False, cust: bool = False) -> None:
        self.page = page
        self.state = page.state
        self.q = q
        self.only_diff = only_diff
        self.cust = cust
        self.keys = self.state.scope().usable_keys

    @property
    def narrow(self) -> bool:
        return self.page.narrow


# ------------------------------------------------------------------ header
class TableHeader(QWidget):
    def __init__(self, table, ref_tag: bool = True) -> None:
        super().__init__()
        self.table = table
        self.setFixedHeight(27)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.lab_k = label("Setting", tone="muted", small=True)
        self.lab_v = label("Value", tone="muted", small=True)
        self.ref = label("", tone="faint", small=True)
        self.lab_s = label("Status", tone="muted", small=True)
        self.lab_k.setToolTip("The setting's key in config.yaml. Click a key to see who has which value. Drag the "
                              "column lines to resize.")
        self.lab_v.setToolTip("The value: your edit if you made one, otherwise the reference agent's value; faint "
                              "text is the default (the key is not in the file)")
        self.lab_s.setToolTip("Edited (only on this page), staged (in Review), how many different values the "
                              "checked agents have, or default / not set")
        for w in (self.lab_k, self.lab_v, self.ref, self.lab_s):
            w.setParent(self)
        self._drag = None
        self._ref_tag = ref_tag

    def set_ref(self, name: str) -> None:
        self.ref.setText(f"from {name}" if name and self._ref_tag else "")
        self.ref.setToolTip(f"An unedited row shows the value of {name}: the focused agent if it is checked, "
                            "otherwise the first agent you checked" if name else "")
        self.ref.adjustSize()
        self._place()

    def resizeEvent(self, ev) -> None:
        self._place()

    def _place(self) -> None:
        kw, vw, sw = self.table.compute_cols(self.width())
        pad = 6 if self.table.ctx.narrow else 10
        h = self.height()
        for w, x in ((self.lab_k, pad), (self.lab_v, kw + pad), (self.lab_s, kw + vw + pad)):
            w.adjustSize()
            w.move(x, (h - w.height()) // 2)
        self.ref.move(self.lab_v.x() + self.lab_v.width() + 8, (h - self.ref.height()) // 2)
        self.ref.setMaximumWidth(max(0, vw - self.lab_v.width() - 2 * pad - 8))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), T.c("page"))
        kw, vw, _ = self.table.compute_cols(self.width())
        p.setPen(QPen(T.c("soft"), 1))
        p.drawLine(kw, 0, kw, self.height())
        p.drawLine(kw + vw, 0, kw + vw, self.height())
        p.setPen(QPen(T.c("line"), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        if self._drag is not None or self._near(self.mapFromGlobal(QCursor.pos()).x()) and self.underMouse():
            which = self._drag[0] if self._drag else self._near(self.mapFromGlobal(QCursor.pos()).x())
            x = kw if which == 1 else kw + vw
            p.fillRect(x - 1, 0, 2, self.height(), T.c("accent"))

    def _near(self, x: int) -> int:
        if self.table.fit:
            return 0
        kw, vw, _ = self.table.compute_cols(self.width())
        if abs(x - kw) <= 5:
            return 1
        if abs(x - (kw + vw)) <= 5:
            return 2
        return 0

    def mouseMoveEvent(self, ev) -> None:
        x = ev.position().x()
        if self._drag is not None:
            which, x0, v0, t0 = self._drag
            st = self.table.ctx.state
            dp = (x - x0) / max(1, self.width()) * 100
            if which == 1:
                cv = max(14.0, min(68 - t0, v0 - dp))
                st.set_columns(round(cv, 1), t0)
            else:
                ct = max(8.0, min(34.0, t0 - dp))
                cv = max(14.0, min(68 - ct, v0 + (t0 - ct)))
                st.set_columns(round(cv, 1), round(ct, 1))
            return
        self.setCursor(Qt.SplitHCursor if self._near(int(x)) else Qt.ArrowCursor)
        self.update()

    def mousePressEvent(self, ev) -> None:
        which = self._near(int(ev.position().x()))
        if which and ev.button() == Qt.LeftButton:
            st = self.table.ctx.state
            cv = st.cv if (st.cv_set or not self.table.ctx.narrow) else 30.0
            self._drag = (which, ev.position().x(), cv, st.ct)

    def mouseReleaseEvent(self, ev) -> None:
        if self._drag is not None:
            st = self.table.ctx.state
            self._drag = None
            st.set_columns(st.cv, st.ct, save=True)
            self.update()

    def leaveEvent(self, ev) -> None:
        self.update()


# ------------------------------------------------------------------ rows
class KeyLabel(QLabel):
    clicked = Signal()

    def __init__(self, text: str) -> None:
        super().__init__(wrap_key(text))
        self.setFont(mono())
        self.setWordWrap(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()

    def enterEvent(self, ev) -> None:
        self.setStyleSheet(f"color: {T['accent']};")

    def leaveEvent(self, ev) -> None:
        self.setStyleSheet("")


class RowLine(QWidget):
    """The three cells of one setting, placed by hand from the table's column widths.

    No layout: the row can shrink to any width (the table decides the columns), and it paints
    its own grid lines and the edit/staged marker.
    """

    def __init__(self, item) -> None:
        super().__init__()
        self.item = item
        self.mark = ""          # "edit" | "pend" | ""
        self._hover = False
        self.flash = False
        self.cells: list[QWidget] = []
        sp = self.sizePolicy()
        sp.setHeightForWidth(True)
        sp.setHorizontalPolicy(QSizePolicy.Ignored)
        self.setSizePolicy(sp)

    def set_cells(self, cells: list[QWidget]) -> None:
        self.cells = cells
        for c in cells:
            c.setParent(self)

    def _cols(self, w: int) -> tuple[int, int, int]:
        return self.item.table.compute_cols(w)

    def _height(self, w: int) -> int:
        h = 32
        for c, cw in zip(self.cells, self._cols(w)):
            lay = c.layout()
            ch = lay.heightForWidth(cw) if lay is not None and lay.hasHeightForWidth() else c.sizeHint().height()
            h = max(h, ch)
        return h

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._height(w)

    def sizeHint(self) -> QSize:
        w = self.width() if self.width() > 50 else 600
        return QSize(w, self._height(w))

    def minimumSizeHint(self) -> QSize:
        return QSize(60, 32)

    def place(self) -> None:
        kw, vw, sw = self._cols(self.width())
        h = self.height()
        x = 0
        for c, cw in zip(self.cells, (kw, vw, sw)):
            c.setGeometry(x, 0, max(0, cw), h)
            x += cw
        self.update()

    def resizeEvent(self, ev) -> None:
        self.place()

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.item.hover(True)
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.item.hover(False)
        self.update()

    def _info_rect(self) -> QRect:
        kw = self._cols(self.width())[0]
        right = kw - (30 if self.item.rm is not None else 6)
        return QRect(right - 14, (min(self.height(), 32) - 16) // 2, 14, 16)

    def event(self, ev) -> bool:
        if ev.type() == QEvent.ToolTip and self.item.node.desc and \
                self._info_rect().adjusted(-4, -4, 4, 4).contains(ev.pos()):
            QToolTip.showText(ev.globalPos(), self.item.node.desc, self)
            return True
        return super().event(ev)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        if self.flash:
            p.fillRect(self.rect(), T.c("accent_bg"))
        elif self._hover:
            p.fillRect(self.rect(), T.c("hover"))
        if self._hover and self.item.node.desc:
            p.setPen(T.c("faint"))
            p.setFont(sans(13))
            p.drawText(self._info_rect(), Qt.AlignCenter, "ⓘ")
        kw, vw, _ = self._cols(self.width())
        p.setPen(QPen(T.c("soft"), 1))
        p.drawLine(kw, 0, kw, self.height())
        p.drawLine(kw + vw, 0, kw + vw, self.height())
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        if self.mark:
            p.fillRect(0, 0, 3, self.height(), T.c("accent") if self.mark == "edit" else T.c("amber_edge"))


class SettingItem(QWidget):
    """One setting: its row and, when open, the details strip below it."""

    def __init__(self, table, node: Node, lv: int, mode: str = "tree") -> None:
        super().__init__()
        self.table, self.node, self.lv, self.mode = table, node, lv, mode
        ctx = table.ctx
        st = ctx.state
        self.strip: QWidget | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.line = RowLine(self)
        v.addWidget(self.line)
        # key cell
        self.kc = QWidget()
        kl = QHBoxLayout(self.kc)
        ind = (lv * 14 + 8) if ctx.narrow else (lv * 18 + 12)
        self.hidden_self = st.node_hidden_self(node)
        show_eye = mode != "rel" and (ctx.cust or (self.hidden_self and mode == "hid"))
        kl.setContentsMargins(4 if show_eye else ind, 2, 4, 2)
        kl.setSpacing(4)
        if show_eye:
            kl.addSpacing(max(0, ind - 28))
            eye = IconButton("eye_off" if self.hidden_self else "eye",
                             ("Show" if self.hidden_self else "Hide") + " this setting",
                             color="faint" if self.hidden_self else "muted")
            eye.clicked.connect(lambda: table.page.toggle_node(node))
            kl.addWidget(eye)
        crumbs = []
        if mode == "rel":
            crumbs = st.model.crumbs(node.path)[:-1]
        elif mode == "hid":
            crumbs = [str(k) for k in node.path[:-1]][1 if node.path[0] == node.section else 0:]
        if crumbs:
            cr = QLabel(" › ".join(crumbs) + " ›")
            cr.setFont(mono())
            cr.setStyleSheet(f"color: {T['faint']};")
            cr.setWordWrap(True)
            kl.addWidget(cr)
        self.key = KeyLabel(node.key)
        if self.hidden_self and mode != "rel":
            f = self.key.font()
            f.setStrikeOut(True)
            self.key.setFont(f)
            self.key.setStyleSheet(f"color: {T['faint']};")
        self.key.setToolTip(((node.desc + "\n") if node.desc else "") + " › ".join(str(k) for k in node.path)
                            + " · click to see who has which value, remove the key, or use the focused agent's value")
        self.key.clicked.connect(self.toggle_details)
        kl.addWidget(self.key, 1)
        for text, tip in key_tags(node):
            kl.addWidget(self._tag(text, tip, warn=True))
        if mode == "tree" and len(node.path) == 1 and node.path[0] != node.section:
            kl.addWidget(self._tag("top-level key", f"Sits at the top level of config.yaml, next to {node.section}:"))
        self.rm = None
        if mode == "rel":
            self.rm = IconButton("x", "Remove from Related settings (the setting itself stays where it is)")
            self.rm.clicked.connect(lambda: table.page.unpin(node.path))
            self.rm.setVisible(False)
            kl.addWidget(self.rm)
        # value cell
        self.vc = QWidget()
        vl = QHBoxLayout(self.vc)
        vl.setContentsMargins(6 if ctx.narrow else 10, 2, 6 if ctx.narrow else 10, 2)
        self.editor = make_editor(node)
        self.editor.committed.connect(lambda val: table.page.commit(self, val))
        self.editor.tip_provider = self.value_tip
        if isinstance(self.editor, BlockEditor):
            self.editor.yaml_requested.connect(lambda: table.page.open_yaml(node))
        vl.addWidget(self.editor, 1)
        # status cell
        self.sc = QWidget()
        sl = QHBoxLayout(self.sc)
        sl.setContentsMargins(5 if ctx.narrow else 10, 2, 2 if ctx.narrow else 4, 2)
        sl.setSpacing(2 if ctx.narrow else 4)
        sl.setSpacing(4)
        self.status = QLabel("")
        self.status.setFont(sans(12))
        self.status.setTextFormat(Qt.RichText)
        self.status.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        self.status.linkActivated.connect(lambda _h: self.toggle_details())
        self.sl = sl
        self.undo: IconButton | None = None
        sl.addWidget(self.status, 1)
        self.line.set_cells([self.kc, self.vc, self.sc])
        self._dist = None
        self.refresh()
        if gid(node.path) in table.page.open_details:
            self.toggle_details(force=True)

    def _tag(self, text: str, tip: str, warn: bool = False) -> QLabel:
        t = QLabel(text)
        t.setFont(sans(11))
        t.setStyleSheet(f"color: {T['amber' if warn else 'faint']};")
        t.setToolTip(tip)
        return t

    def set_cols(self, kw: int, vw: int, sw: int) -> None:
        self.line.updateGeometry()
        self.line.place()

    def hover(self, on: bool) -> None:
        if self.rm is not None:
            self.rm.setVisible(on)

    # -- values ------------------------------------------------------------------
    def refresh(self) -> None:
        ctx = self.table.ctx
        st = ctx.state
        node = self.node
        path = node.path
        scope = st.scope()
        keys = ctx.keys
        dist = st.distribution(node, keys) if len(keys) > 1 else None
        self._dist = dist
        n_values = len(dist) if dist else 1
        dirty = st.work.dirty(path, scope)
        staged = st.staged_count(path) if keys else 0
        shown = st.shown_value(path)
        self.editor.show_value(shown, edited=dirty)
        undo_tip = ""
        if dirty:
            word = "not staged" if ctx.narrow else "edited, not staged"
            text = f"<span style='color:{T['accent']};font-weight:600'>{word}</span>"
            tip = "Your edit is only on this page so far. Stage it to put it in Review for the checked agents."
            undo_tip = "Discard this edit"
        elif staged:
            word = f"staged · {staged}" + ("" if ctx.narrow else f" agent{'s' if staged != 1 else ''}")
            text = f"<span style='color:{T['amber']}'>{word}</span>"
            tip = "Staged and listed in Review. Nothing is written yet."
            undo_tip = "Unstage this for the checked agents"
        elif n_values > 1:
            text = f"<a href='#' style='color:{T['amber']};text-decoration:none'>{n_values} values</a>"
            tip = f"The checked agents hold {n_values} different values. Click to see who has what."
        elif shown is MISSING and not node.secret:
            word = "not set" if node.default is MISSING else "default"
            text = f"<span style='color:{T['faint']}'>{word}</span>"
            tip = ("The key is not in the file and Hermes has no known default" if node.default is MISSING
                   else "The key is not in the file: Hermes uses its default")
        else:
            text, tip = "", ""
        if text != self.status.text():
            self.status.setText(text)
        self.status.setToolTip(tip)
        if undo_tip:
            if self.undo is None:
                self.undo = IconButton("undo")
                if ctx.narrow:
                    self.undo.setFixedSize(18, 24)
                self.undo.clicked.connect(self._undo)
                self.sl.addWidget(self.undo)
            self.undo.setToolTip(undo_tip)
            self.undo.show()
        elif self.undo is not None:
            self.undo.hide()
        mark = "edit" if dirty else "pend" if staged else ""
        if mark != self.line.mark:
            self.line.mark = mark
            self.line.update()
        if self.strip is not None:
            self._fill_strip()

    def _undo(self) -> None:
        st = self.table.ctx.state
        path = self.node.path
        if st.work.has(path) and st.work.dirty(path, st.scope()):
            st.work.discard([path])
        else:
            st.session.drafts.revert(st.scope().usable_keys, path)
            st.work.discard([path])
        self.table.page.after_edit(self)

    # -- details strip ---------------------------------------------------------------
    def toggle_details(self, force: bool = False) -> None:
        opened = self.table.page.open_details
        key = gid(self.node.path)
        if self.strip is not None and not force:
            self.layout().removeWidget(self.strip)
            self.strip.hide()
            self.strip.deleteLater()
            self.strip = None
            opened.discard(key)
            return
        if self.strip is None:
            self.strip = QWidget()
            self.strip.setAttribute(Qt.WA_StyledBackground, True)
            self.strip.setObjectName("strip")
            self.strip.setStyleSheet(f"QWidget#strip {{ background: {T['pane']}; }}")
            self.layout().addWidget(self.strip)
            opened.add(key)
            self._fill_strip()

    def _fill_strip(self) -> None:
        old = self.strip.layout()
        if old is not None:
            QWidget().setLayout(old)          # drop the old layout and its widgets
        st = self.table.ctx.state
        node = self.node
        ind = (self.lv * 14 + 8) if self.table.ctx.narrow else (self.lv * 18 + 12)
        v = QVBoxLayout(self.strip)
        v.setContentsMargins(ind, 8, 12, 12)
        v.setSpacing(8)
        if node.desc:
            d = label(node.desc, tone="muted", wrap=True)
            d.setMaximumWidth(720)
            v.addWidget(d)
        dist = self._dist or st.distribution(node, self.table.ctx.keys)
        chips = QWidget()
        fl = QGridLayout(chips)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setHorizontalSpacing(12)
        fl.setVerticalSpacing(4)
        fl.setColumnStretch(1, 1)
        for r, (value, keys, unset) in enumerate(dist):
            if node.secret:
                text = "not set" if value in (MISSING, None, "") else "set"
                value_tip = text
            else:
                value_tip = shown(value, node.path)
                text = ("not set" if value is MISSING else "default " + value_tip) if unset == len(keys) \
                    else value_tip
            b = link("")
            b.setProperty("lnk", False)
            b.setProperty("chip", True)
            b.setText(f"{text}  ×{len(keys)}")
            b.setFixedHeight(22)
            b.setMaximumWidth(340)
            b.setToolTip(f"{value_tip}\n" + (f"{unset} of them do not set this key in their file\n" if unset else "")
                         + f"Click to check exactly these {plural(len(keys), 'agent')}")
            b.clicked.connect(lambda _=False, ks=list(keys): self.table.page.check_exactly(ks))
            fl.addWidget(b, r, 0, Qt.AlignTop)
            names = label(", ".join(agent_names(st, keys)), tone="muted", wrap=True)
            names.setToolTip("The checked agents that have this value")
            fl.addWidget(names, r, 1, Qt.AlignTop)
        if dist:
            v.addWidget(chips)
        if not node.secret:
            acts = QHBoxLayout()
            acts.setSpacing(22)
            can_rm = any(st.scope().draft_value(k, node.path) is not MISSING for k in self.table.ctx.keys)
            rm = link("Remove the key so the default applies",
                      tip="Edits this row: the key is removed from the file, so the default applies. Stage it to "
                          "put it in Review.")
            rm.setEnabled(can_rm)
            rm.clicked.connect(lambda: self.table.page.commit(self, REMOVE))
            acts.addWidget(rm)
            focus = st.session.viewed
            rec = st.session.record(focus) if focus else None
            if rec is not None and rec.usable:
                fv = st.scope().draft_value(focus, node.path)
                fv_text = "not set" if fv is MISSING else "set (hidden)" if node.secret else shown(fv, node.path)
                use = link(f"Use the value of {rec.name}: {fv_text}",
                           tip="Puts the value of the focused agent (the highlighted one in the Agents pane) in "
                               "this row as your edit")
                use.clicked.connect(lambda: self._use_focus(focus))
                acts.addWidget(use)
            acts.addStretch(1)
            v.addLayout(acts)

    def _use_focus(self, key: str) -> None:
        val = self.table.ctx.state.scope().draft_value(key, self.node.path)
        self.table.page.commit(self, REMOVE if val is MISSING else val)

    # -- tooltip of the value cell (built when hovered) -------------------------------
    def value_tip(self) -> str:
        """What this cell shows, for these agents: where the value comes from, the default, who has
        which value, what is staged, the allowed values, and how to change it."""
        st = self.table.ctx.state
        node, path = self.node, self.node.path
        scope = st.scope()
        keys = scope.usable_keys
        secret = node.secret

        def val(v: Any) -> str:
            if v is MISSING:
                return "not set"
            return "set (hidden)" if secret else shown(v, path)

        lines = [" › ".join(str(k) for k in path)]
        if node.desc:
            lines.append(node.desc)
        lines.append("")
        has_default = node.default is not MISSING
        if not keys:
            lines.append("No agent is checked: check agents to see and change their values.")
        elif st.work.dirty(path, scope):
            w = st.shown_value(path)
            lines.append(f"Your edit: {'remove the key (Hermes default applies)' if w is MISSING else val(w)}"
                         " — not staged yet")
        else:
            ref = st.ref_key()
            name = st.session.record(ref).name if ref else ""
            v = scope.draft_value(ref, path) if ref else MISSING
            if v is MISSING:
                lines.append(f"{name}: not in the file — Hermes uses "
                             + (val(node.default) if has_default else "its own default"))
            else:
                lines.append(f"{name}: {val(v)}")
        if has_default and not secret:
            lines.append(f"Hermes default: {val(node.default)}")
        if len(keys) > 1:
            dist = st.distribution(node, keys)        # fresh: built only on hover
            if len(dist) > 1:
                parts = [f"{'default ' if unset == len(ks) else ''}{val(v)} ×{len(ks)}" for v, ks, unset in dist[:4]]
                more = f" · {len(dist) - 4} more" if len(dist) > 4 else ""
                lines.append(f"The {len(keys)} checked agents differ: " + " · ".join(parts) + more)
            else:
                lines.append(f"All {len(keys)} checked agents have the same value.")
        staged = st.staged_count(path) if keys else 0
        if staged:
            k = next((k for k in keys if (d := st.session.drafts.draft(k)) is not None and d.is_modified(path)), None)
            sv = st.session.drafts.draft(k).get(path) if k else MISSING
            lines.append(f"Staged for {plural(staged, 'agent')}: "
                         + ("remove the key" if sv is MISSING else val(sv)) + " (not written yet)")
        if node.kind == "enum" and node.options:
            opts = [str(o) for o in node.options]
            lines.append("Allowed: " + ", ".join(opts[:10]) + (f" (+{len(opts) - 10} more)" if len(opts) > 10 else ""))
        lines.append(edit_hint(node))
        return tip_html(lines)

    # -- right-click menu --------------------------------------------------------------
    def contextMenuEvent(self, ev) -> None:
        self.context_menu().exec(ev.globalPos())

    def context_menu(self) -> QMenu:
        """Everything you can do with this one setting (items that do not apply are greyed)."""
        page = self.table.page
        st = self.table.ctx.state
        node, path = self.node, self.node.path
        scope = st.scope()
        keys = scope.usable_keys
        dirty = st.work.dirty(path, scope)
        staged = bool(keys) and st.staged_count(path) > 0
        ref = st.ref_name()
        m = QMenu(self)
        m.setToolTipsVisible(True)

        def add(text: str, tip: str, fn, enabled: bool = True) -> None:
            a = m.addAction(text)
            a.setToolTip(tip)
            a.setEnabled(enabled)
            a.triggered.connect(fn)

        add(f"Stage this value for {plural(len(keys), 'agent')}" if keys else "Stage this value",
            "Puts this edit in Review for the agents checked now. Nothing is written until Write config.",
            lambda: page.stage_paths([path]), dirty and bool(keys))
        add(f"Stage this value for {ref} only" if ref else "Stage this value for one agent only",
            "Puts this edit in Review only for the agent whose values the page shows; the other checked agents "
            "are left as they are.", lambda: page.stage_paths([path], only=True), dirty and len(keys) > 1)
        n_page = len(page.page_edits())
        add(f"Stage all edits on this page ({n_page})", "Same as Stage in the bar at the bottom of the page.",
            page.stage_edits, n_page > 0 and bool(keys))
        m.addSeparator()
        add("Discard my edit", "Throws away your edit; the row shows the current value again.",
            lambda: (st.work.discard([path]), page.after_edit(self)), dirty)
        add("Unstage", "Takes this setting out of Review for the checked agents (back to what the files have).",
            lambda: (st.session.drafts.revert(keys, path), page.after_edit(self)), staged)
        m.addSeparator()
        add("Show who has which value", "Opens the details under the row: each value and how many agents have it.",
            lambda: self.toggle_details(force=True), bool(keys))
        focus = st.session.viewed
        rec = st.session.record(focus) if focus else None
        add(f"Use {rec.name}'s value" if rec else "Use the focused agent's value",
            "Copies the value of the agent you clicked in the Agents pane into this row as your edit.",
            lambda: self._use_focus(focus), rec is not None and rec.usable and not node.secret)
        has_default = node.default is not MISSING
        add("Reset to Hermes default", "Edits this row to Hermes' default value"
            + (f" ({shown(node.default, path)})." if has_default and not node.secret else ".")
            + " The key stays in the file with that value.",
            lambda: page.commit(self, node.default), has_default and not node.secret)
        can_rm = any(scope.draft_value(k, path) is not MISSING for k in keys)
        add("Remove this setting from the files", "Edits this row so the key is deleted from config.yaml; "
            "Hermes then uses its own default. Stage it to put it in Review.",
            lambda: page.commit(self, REMOVE), can_rm and not node.secret)
        m.addSeparator()
        value = st.shown_value(path)
        add("Copy value", "Copies the value shown in this row (credentials are never copied).",
            lambda: QApplication.clipboard().setText(self._copy_text(value)),
            value is not MISSING and value is not REMOVE and not node.secret)
        dotted = ".".join(str(k) for k in path)
        add(f"Copy setting name ({dotted})", "Copies the setting's path, as Hermes writes it with dots.",
            lambda: QApplication.clipboard().setText(dotted))
        if self.mode != "rel":
            m.addSeparator()
            hidden = st.node_hidden_self(node)
            add("Show this setting" if hidden else "Hide this setting",
                "Same as the eye in Customize. Hidden settings stay reachable through search (Ctrl+K).",
                lambda: page.toggle_node(node))
        return m

    @staticmethod
    def _copy_text(value: Any) -> str:
        if isinstance(value, (dict, list)):
            import yaml

            return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).rstrip()
        return "" if value is None else str(value).lower() if isinstance(value, bool) else str(value)


class GroupRow(Clickable):
    def __init__(self, table, node: Node | None, lv: int, title: str, summary: str = "", opened: bool = False,
                 plain: bool = False, eye: bool = False, hidden: bool = False) -> None:
        super().__init__()
        self.table, self.node = table, node
        self.plain = plain
        self._hover = False
        h = QHBoxLayout(self)
        ind = (lv * 14 + 8) if table.ctx.narrow else (lv * 18 + 6)
        h.setContentsMargins(ind if not eye else 4, 0, 10, 0)
        h.setSpacing(4)
        if eye and node is not None:
            h.addSpacing(max(0, ind - 26))
            b = IconButton("eye_off" if hidden else "eye", ("Show" if hidden else "Hide") + " this group",
                           color="faint" if hidden else "muted")
            b.clicked.connect(lambda: table.page.toggle_node(node))
            h.addWidget(b)
        self.chev = Chevron(opened)
        h.addWidget(self.chev)
        if node is not None:
            k = QLabel(f"{node.key}<span style='color:{T['faint']};font-weight:400'>:</span>")
            k.setTextFormat(Qt.RichText)
            k.setFont(mono(12.5, 600))
            if hidden:
                f = k.font()
                f.setStrikeOut(True)
                k.setFont(f)
                k.setStyleSheet(f"color: {T['faint']};")
            h.addWidget(k)
            for text, tip in key_tags(node):
                t = QLabel(text)
                t.setToolTip(tip)
                t.setFont(sans(11))
                t.setStyleSheet(f"color: {T['amber']}; margin-left: 8px;")
                h.addWidget(t)
        else:
            t = QLabel(title)
            if plain:
                t.setStyleSheet(f"color: {T['muted']};")
            else:
                t.setFont(sans(13, 600))
            h.addWidget(t)
        self.sum = QLabel(summary)
        self.sum.setTextFormat(Qt.RichText)
        self.sum.setStyleSheet(f"color: {T['muted']}; font-size: 12px; margin-left: 10px;")
        h.addWidget(self.sum)
        h.addStretch(1)
        self.setMinimumHeight(30)
        self.setToolTip(group_tip(node, title))

    def set_summary(self, html: str) -> None:
        self.sum.setText(html)

    def enterEvent(self, ev) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self._hover = False
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        if self._hover:
            p.fillRect(self.rect(), T.c("accent_bg"))
        elif not self.plain:
            p.fillRect(self.rect(), T.c("head"))
        p.setPen(QPen(T.c("soft"), 1))
        p.drawLine(0, self.height() - 1, self.width(), self.height() - 1)


def group_tip(node, title: str) -> str:
    if node is not None:
        path = " › ".join(node.path)
        return ((node.desc + "  ") if node.desc else "") + f"({path}) Click to open or close this group of settings."
    if title.startswith("Hidden ("):
        return ("Settings you hid. Open the line to see them; the eye icon shows one again. Hidden settings keep "
                "their values in the files.")
    return "Click to open or close"


def sum_html(n: int, d: int, word: str = "setting") -> str:
    return plural(n, word) + (f" · <b style='color:{T['amber']}'>{differ(d)}</b>" if d else "")


# ------------------------------------------------------------------ table
class SettingsTable(QWidget):
    """A table of settings (nodes) with an optional "Hidden (n)" line."""

    def __init__(self, page, nodes: list[Node], ctx: Ctx, hid_key: str | None = None, fit: bool = False,
                 mode: str = "tree", header: bool = True) -> None:
        super().__init__()
        self.page, self.nodes, self.ctx = page, nodes, ctx
        self.hid_key, self.fit, self.mode, self.show_header = hid_key, fit, mode, header
        self.cols = (300, 200, 120)
        self.items: list[SettingItem] = []
        self.groups: list[GroupRow] = []
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(0)
        self.header: TableHeader | None = None
        self.empty = True
        self.build()

    # -- geometry ------------------------------------------------------------------
    def compute_cols(self, w: int) -> tuple[int, int, int]:
        st = self.ctx.state
        if self.fit:
            sw = 104
            vw = int((w - sw) / 2.25)
            return w - vw - sw, vw, sw
        narrow = self.ctx.narrow
        cv = st.cv if (st.cv_set or not narrow) else 30.0
        sw = int(max(w * st.ct / 100, 100 if narrow else 0))
        vw = int(w * cv / 100)
        return max(60, w - vw - sw), vw, sw

    def resizeEvent(self, ev) -> None:
        self.apply_cols()

    def apply_cols(self) -> None:
        cols = self.compute_cols(self.width())
        if cols == self.cols:
            return
        self.cols = cols
        for it in self.items:
            it.set_cols(*cols)
            it.line.update()
        if self.header is not None:
            self.header._place()
            self.header.update()

    # -- building -------------------------------------------------------------------
    def build(self) -> None:
        clear_layout(self.v)
        self.items, self.groups = [], []
        self.cols = self.compute_cols(self.width() or 900)
        ctx = self.ctx
        st = ctx.state
        rows: list[QWidget] = []
        qm = bool(ctx.q or ctx.only_diff)
        if self.mode == "rel":
            for n in self.nodes:
                rows.append(self._item(n, 0, "rel"))
        else:
            for n in self.nodes:
                rows.extend(self._node(n, 0, ctx.q))
            if not ctx.cust and not qm and self.hid_key:
                hidden: list[Node] = []

                def walk(ns):
                    for x in ns:
                        if st.node_hidden_self(x):
                            hidden.append(x)
                        elif x.is_group:
                            walk(x.children)

                walk(self.nodes)
                if hidden:
                    opened = self.hid_key in st.grp_open
                    total = sum(len(leaves(x.children)) if x.is_group else 1 for x in hidden)
                    g = GroupRow(self, None, 0, f"Hidden ({total})", opened=opened, plain=True)
                    g.setToolTip("Settings you have hidden here")
                    g.clicked.connect(lambda k=self.hid_key: self.page.toggle_group(k))
                    rows.append(g)
                    if opened:
                        for x in hidden:
                            if x.is_group:
                                rows.extend(self._node(x, 0, "", show_hidden=True))
                            else:
                                rows.append(self._item(x, 0, "hid"))
        self.empty = not rows
        visible_rows = bool(self.items) or any(g.node is not None for g in self.groups)
        if not rows:
            if not qm:
                rows.append(label("No settings here.", tone="muted"))
                rows[-1].setContentsMargins(12, 8, 12, 8)
        elif self.show_header and visible_rows:
            self.header = TableHeader(self)
            self.header.set_ref(st.ref_name())
            rows.insert(0, self.header)
        for r in rows:
            self.v.addWidget(r)

    def _item(self, n: Node, lv: int, mode: str) -> SettingItem:
        it = SettingItem(self, n, lv, mode)
        self.items.append(it)
        return it

    def _node(self, n: Node, lv: int, q: str, show_hidden: bool = False) -> list[QWidget]:
        ctx = self.ctx
        st = ctx.state
        hidden = st.node_hidden_self(n)
        if hidden and not ctx.cust and not show_hidden and not q:
            return []
        if n.is_group:
            qm = bool(q or ctx.only_diff)
            hit = bool(q) and q in n.key.casefold()
            sub_q = "" if hit else q
            opened = qm or gid(n.path) in st.grp_open
            kids: list[QWidget] = []
            if opened:
                for c in n.children:
                    kids.extend(self._node(c, lv + 1, sub_q, show_hidden))
            if qm and not kids:
                return []
            ls = st.visible_leaves(n.children)
            g = GroupRow(self, n, lv, n.key, sum_html(len(ls), st.count_differ(ls)), opened=opened,
                         eye=ctx.cust or (hidden and show_hidden), hidden=hidden)
            g.clicked.connect(lambda p=gid(n.path): self.page.toggle_group(p))
            self.groups.append(g)
            return [g] + kids
        if q and q not in n.key.casefold():
            return []
        if ctx.only_diff:
            if not st.differs(n, ctx.keys) and not st.staged_count(n.path):
                return []
        return [self._item(n, lv, "tree")]

    # -- refresh -------------------------------------------------------------------------
    def refresh(self) -> None:
        self.ctx.keys = self.ctx.state.scope().usable_keys
        for it in self.items:
            it.refresh()
        st = self.ctx.state
        for g in self.groups:
            ls = st.visible_leaves(g.node.children)
            g.set_summary(sum_html(len(ls), st.count_differ(ls)))
        if self.header is not None:
            self.header.set_ref(st.ref_name())

    def item_for(self, path) -> SettingItem | None:
        return next((it for it in self.items if it.node.path == tuple(path)), None)


__all__ = ["SettingsTable", "Ctx", "GroupRow", "sum_html", "gid", "QPoint", "Any"]
