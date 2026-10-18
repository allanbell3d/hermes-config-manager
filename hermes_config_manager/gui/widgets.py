"""Small building blocks shared by every screen (buttons, links, switch, dots, popovers)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QApplication, QFrame, QLabel, QLayout, QPushButton, QSizePolicy,
                               QToolButton, QWidget)

from .theme import T, icon, mono, sans


def set_prop(widget: QWidget, name: str, value) -> None:
    """Set a style property and re-apply the style sheet to this widget only."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)


def btn(text: str, sm: bool = False, pri: bool = False, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if sm:
        b.setProperty("sm", True)
    if pri:
        b.setProperty("pri", True)
    b.setFixedHeight(24 if sm else 28)
    b.setCursor(Qt.PointingHandCursor)
    if tip:
        b.setToolTip(tip)
    return b


def link(text: str, tone: str | None = None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setProperty("lnk", True)
    if tone:
        b.setProperty("tone", tone)
    b.setCursor(Qt.PointingHandCursor)
    b.setFlat(True)
    b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    b.setFixedHeight(QFontMetrics(b.font()).height() + 6)
    if tip:
        b.setToolTip(tip)
    return b


class IconButton(QToolButton):
    def __init__(self, name: str, tip: str = "", size: int = 24, color: str = "muted", icon_size: int = 14,
                 parent=None) -> None:
        super().__init__(parent)
        self._name, self._color, self._isz = name, color, icon_size
        self.setFixedSize(size, size)
        self.setIconSize(QSize(icon_size, icon_size))
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        if tip:
            self.setToolTip(tip)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        self.setIcon(icon(self._name, self._color if self.isEnabled() else "soft", self._isz))

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == ev.Type.EnabledChange:
            self.refresh_icon()


def label(text: str = "", tone: str | None = None, small: bool = False, bold: bool = False,
          is_mono: bool = False, wrap: bool = False) -> QLabel:
    lab = QLabel(text)
    if tone:
        lab.setProperty("tone", tone)
    if small:
        lab.setProperty("small", True)
    if bold:
        lab.setProperty("bold", True)
    if is_mono:
        lab.setFont(mono())
    lab.setWordWrap(wrap)
    lab.setTextFormat(Qt.PlainText if "<" not in text else Qt.RichText)
    return lab


class ElideLabel(QLabel):
    """One line, cut with … when too narrow (full text in the tooltip)."""

    def __init__(self, text: str = "", tone: str | None = None, small: bool = False, is_mono: bool = False,
                 parent=None) -> None:
        super().__init__(parent)
        if tone:
            self.setProperty("tone", tone)
        if small:
            self.setProperty("small", True)
        if is_mono:
            self.setFont(mono())
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._full = ""
        self.setMinimumWidth(10)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._elide()

    def _elide(self) -> None:
        fm = QFontMetrics(self.font())
        shown = fm.elidedText(self._full, Qt.ElideRight, max(10, self.width()))
        super().setText(shown)
        self.setToolTip(self._full if shown != self._full else "")

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(self._full) + 2, fm.height())


class Switch(QAbstractButton):
    """An on/off switch: a track with a knob, and a word next to it.

    ``state``: True / False / None (mixed); ``dflt``: the default is shown (faded, italic word).
    """

    def __init__(self, text_on: str = "on", text_off: str = "off", small: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self._mixed = False
        self._dflt = False
        self._words = (text_on, text_off)
        self._word: str | None = None
        self._small = small
        self.setCursor(Qt.PointingHandCursor)
        self.setFont(sans(12 if small else 13))
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.toggled.connect(self.update)

    def set_state(self, on: bool | None, dflt: bool = False, word: str | None = None) -> None:
        self.blockSignals(True)
        self._mixed = on is None
        self.setChecked(bool(on))
        self.blockSignals(False)
        self._dflt = dflt
        self._word = word
        self.updateGeometry()
        self.update()

    def word(self) -> str:
        if self._word is not None:
            return self._word
        return self._words[0] if self.isChecked() else self._words[1]

    def _track(self) -> tuple[int, int]:
        return (24, 14) if self._small else (30, 16)

    def sizeHint(self) -> QSize:
        tw, th = self._track()
        w = self.word()
        tx = QFontMetrics(self.font()).horizontalAdvance(w) + 8 if w else 0
        return QSize(tw + tx + 2, max(th, QFontMetrics(self.font()).height()) + 4)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        tw, th = self._track()
        y = (self.height() - th) / 2
        on = self.isChecked()
        col = T.c("amber_edge") if self._mixed else T.c("accent") if on else T.c("faint")
        if self._dflt:
            col.setAlphaF(0.45)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(1, y, tw, th), th / 2, th / 2)
        k = th - 4
        kx = 1 + (tw - k - 2 if on and not self._mixed else (tw - k) / 2 if self._mixed else 2)
        p.setBrush(T.c("page"))
        p.drawEllipse(QRectF(kx, y + 2, k, k))
        w = self.word()
        if w:
            f = self.font()
            f.setItalic(self._dflt or self._mixed)
            p.setFont(f)
            p.setPen(T.c("faint") if (self._dflt or self._mixed) else T.c("muted") if self._small else T.c("ink"))
            p.drawText(QRect(tw + 9, 0, self.width() - tw - 9, self.height()), Qt.AlignVCenter | Qt.AlignLeft, w)
        if self.hasFocus():
            p.setPen(QPen(T.c("accent"), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(0.5, y - 1.5, tw + 1, th + 3), (th + 3) / 2, (th + 3) / 2)


class Dot(QWidget):
    """A status dot: ok / slow / down / off / pending (amber) / edit (accent)."""

    COLORS = {"ok": "ok", "slow": "amber_edge", "down": "bad", "off": "faint", "pending": "amber_edge",
              "edit": "accent", "none": None}

    def __init__(self, state: str = "ok", size: int = 8, parent=None) -> None:
        super().__init__(parent)
        self._state = state
        self._size = size
        self.setFixedSize(size, size)

    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, _ev) -> None:
        name = self.COLORS.get(self._state)
        if not name:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(T.c(name))
        p.drawEllipse(QRectF(0, 0, self._size, self._size))


class Chevron(QWidget):
    """The small open/closed arrow of collapsible rows (paints itself; clicks go to the parent)."""

    def __init__(self, opened: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._open = opened
        self.setFixedSize(14, 14)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def set_open(self, opened: bool) -> None:
        if opened != self._open:
            self._open = opened
            self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        icon("chev_open" if self._open else "chev", "faint", 10).paint(p, QRect(2, 2, 10, 10))


class Clickable(QFrame):
    """A frame that emits ``clicked`` (rows and headers that open or select something)."""

    clicked = Signal()
    right_clicked = Signal(QPoint)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self.rect().contains(ev.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(ev)

    def contextMenuEvent(self, ev) -> None:
        self.right_clicked.emit(ev.globalPos())


class Popover(QFrame):
    """A floating panel (closes on outside click / Esc), placed under or above an anchor."""

    closed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("popover")
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setStyleSheet(f"QFrame#popover {{ background: {T['page']}; border: 1px solid {T['line']}; "
                           f"border-radius: 6px; }}")

    def show_at(self, anchor: QWidget, left: int | None = None, width: int | None = None) -> None:
        if width:
            self.setFixedWidth(width)
        self.adjustSize()
        screen = anchor.screen().availableGeometry()
        g = anchor.mapToGlobal(QPoint(0, anchor.height()))
        x = left if left is not None else g.x()
        h = min(self.sizeHint().height(), int(screen.height() * 0.62))
        self.resize(self.width(), h)
        y = g.y() + 4
        if y + h > screen.bottom():
            y = max(screen.top() + 8, anchor.mapToGlobal(QPoint(0, 0)).y() - h - 4)
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        self.move(x, y)
        self.show()

    def closeEvent(self, ev) -> None:
        self.closed.emit()
        super().closeEvent(ev)


class FlowLayout(QLayout):
    """Lays widgets out left to right, wrapping (provider pills)."""

    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect: QRect, test: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + self._sp
            if nx - self._sp > rect.right() and line > 0:
                x, y = rect.x(), y + line + self._sp
                nx, line = x + hint.width() + self._sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x, line = nx, max(line, hint.height())
        return y + line - rect.y()


def plural(n: int, word: str, plural_word: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural_word or word + 's')}"


def differ(n: int) -> str:
    return f"{n} differs" if n == 1 else f"{n} differ"


def agent_names(state, keys: list[str]) -> list[str]:
    """Agent names for 'who has what'; the host is added when the checked agents come from several hosts."""
    s = state.session
    hosts = {r.host for r in (s.record(k) for k in state.scope().usable_keys) if r is not None}
    out = []
    for k in keys:
        r = s.record(k)
        out.append(k if r is None else r.name + (f" ({r.host})" if len(hosts) > 1 else ""))
    return out


def app() -> QApplication:
    return QApplication.instance()


def clear_layout(layout) -> None:
    """Remove every widget of a layout now (hidden at once, deleted by Qt later)."""
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.hide()
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def set_only_link(lnk, state, n: int) -> None:
    """The "<name> only" link beside a Stage button: shown when more than one agent is checked."""
    name = state.ref_name()
    lnk.setVisible(n > 1 and bool(name))
    if n > 1 and name:
        lnk.setText(f"or {name} only")
        lnk.setToolTip(f"Stage for {name} only: the agent whose values this page shows (the one you clicked, if "
                       f"it is checked; otherwise the first agent you checked). The other checked agents are left as "
                       f"they are. Once staged, the edit leaves the page; edit again to stage something else for "
                       f"the others.")


def menu_tips(menu, tips: dict[str, str]) -> None:
    """Hover descriptions for a menu's actions (matched by the start of their text); shown on hover."""
    menu.setToolTipsVisible(True)
    for a in menu.actions():
        text = a.text().replace("&", "")
        for start, tip in tips.items():
            if text.startswith(start) and (not a.toolTip() or a.toolTip() == text):
                a.setToolTip(tip)
                break


def tip_html(lines: list[str]) -> str:
    """A tooltip Qt wraps to a readable width: the first line bold, a blank line as a paragraph break."""
    esc = [x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for x in lines]
    return "<qt><b>" + esc[0] + "</b><br>" + "<br>".join(esc[1:]) + "</qt>"
