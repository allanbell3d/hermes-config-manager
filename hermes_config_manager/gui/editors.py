"""Value editors, one per kind of setting (switch, number, list of choices, text, list, block, secret).

An editor shows a value and emits ``committed(value)`` when the user changes it.  Showing the
default (the key is not in the file) is drawn faded.  Nothing is staged here:
the page turns a commit into a working value.
"""

from __future__ import annotations

import json
from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QToolTip, QWidget

from ..core import secrets
from ..core.paths import MISSING
from .theme import T, mono
from .widgets import ElideLabel, Switch, btn, set_prop


def fmt(value: Any) -> str:
    """Short display text of a value (chips, summaries, review)."""
    if value is MISSING:
        return "not set"
    if value is None:
        return "null"
    if value == "":
        return '""'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        if any(isinstance(v, (dict, list)) for v in value):
            return f"{len(value)} item{'s' if len(value) != 1 else ''}"
        return ", ".join(fmt(v) if not isinstance(v, str) else v for v in value) if value else "[]"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str) if len(value) < 4 else \
            f"{len(value)} keys"
    return str(value).split("\n", 1)[0]


def shown(value: Any, path=()) -> str:
    """Display text with credentials masked (keys that name a secret, token-shaped values, URLs with passwords)."""
    if value is MISSING:
        return "not set"
    return fmt(secrets.redact(value, tuple(path)))


def _text(value: Any) -> str:
    if value is MISSING or value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class Editor(QWidget):
    committed = Signal(object)

    def __init__(self, node, parent=None) -> None:
        super().__init__(parent)
        self.node = node
        self._shown: Any = MISSING
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.lay = lay

    tip_provider = None             # set by the row: builds this cell's tooltip when it is hovered

    def show_value(self, value: Any, edited: bool = False) -> None:
        self._shown = value

    def _emit(self, value: Any) -> None:
        self.committed.emit(value)

    def event(self, ev) -> bool:
        # The parts of an editor have no tooltip of their own: a hover asks the row for one about
        # this setting and these agents (built only when needed, so pages stay fast).
        if ev.type() == QEvent.ToolTip and self.tip_provider is not None:
            QToolTip.showText(ev.globalPos(), self.tip_provider(), self)
            return True
        return super().event(ev)


class BoolEditor(Editor):
    def __init__(self, node, parent=None) -> None:
        super().__init__(node, parent)
        self.sw = Switch()
        self.sw.clicked.connect(self._clicked)
        self.lay.addWidget(self.sw)
        self.lay.addStretch(1)

    def show_value(self, value: Any, edited: bool = False) -> None:
        super().show_value(value, edited)
        dflt = value is MISSING
        v = self.node.default if dflt else value
        none = v is MISSING or v is None
        self.sw.set_state(bool(v) if not none else False, dflt=dflt, word="not set" if none else None)

    def _clicked(self) -> None:
        v = self.node.default if self._shown is MISSING else self._shown
        self._emit(not bool(v) if v is not MISSING else True)


class LineEditor(Editor):
    """Numbers, text and lists (comma separated)."""

    def __init__(self, node, parent=None) -> None:
        super().__init__(node, parent)
        self.ed = QLineEdit()
        self.ed.setProperty("mono", True)
        self.ed.setFont(mono())
        if node.kind == "int":
            self.ed.setValidator(QIntValidator())
            self.ed.setFixedWidth(120)
        elif node.kind == "float":
            v = QDoubleValidator()
            v.setNotation(QDoubleValidator.StandardNotation)
            self.ed.setValidator(v)
            self.ed.setFixedWidth(120)
        else:
            self.ed.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.ed.editingFinished.connect(self._finished)
        self.lay.addWidget(self.ed, 1 if node.kind not in ("int", "float") else 0)
        if node.kind in ("int", "float"):
            self.lay.addStretch(1)

    def show_value(self, value: Any, edited: bool = False) -> None:
        super().show_value(value, edited)
        dflt = value is MISSING
        d = self.node.default
        masked = not dflt and value is not None and secrets.is_secret(self.node.path, value)
        self.ed.setReadOnly(masked)
        self.ed.blockSignals(True)
        if masked:
            self.ed.setText(secrets.MASK)
            self.ed.setPlaceholderText("")
            self.ed.blockSignals(False)
            return
        self.ed.setText("" if dflt or value is None else _text(secrets.redact(value, tuple(self.node.path))))
        if dflt:
            ph = "empty" if d == "" else "null" if d is None else "not set" if d is MISSING else _text(d)
        else:
            ph = "null" if value is None else ""
        self.ed.setPlaceholderText(ph)
        self.ed.blockSignals(False)
        set_prop(self.ed, "edited", bool(edited))

    def _finished(self) -> None:
        if self.ed.isReadOnly():
            return
        text = self.ed.text()
        cur = ("" if self._shown is MISSING or self._shown is None
               else _text(secrets.redact(self._shown, tuple(self.node.path))))
        if text == cur:
            return
        value = self.coerce(text)
        if value is not MISSING:
            self._emit(value)
        else:
            self.show_value(self._shown)

    def coerce(self, text: str) -> Any:
        t = text.strip()
        kind = self.node.kind
        if kind == "int":
            try:
                return int(t)
            except ValueError:
                return MISSING
        if kind == "float":
            try:
                return float(t)
            except ValueError:
                return MISSING
        if kind == "list":
            return [s.strip() for s in t.split(",") if s.strip()] if t else []
        sample = self.node.default if self._shown is MISSING else self._shown
        if isinstance(sample, bool) and t in ("true", "false"):
            return t == "true"
        if isinstance(sample, (int, float)) and not isinstance(sample, bool):
            try:
                return int(t) if t.lstrip("-").isdigit() else float(t)
            except ValueError:
                pass
        return text


class EnumEditor(Editor):
    def __init__(self, node, parent=None) -> None:
        super().__init__(node, parent)
        self.cb = QComboBox()
        self.cb.setMinimumWidth(130)
        self.cb.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cb.activated.connect(self._activated)
        self.lay.addWidget(self.cb)
        self.lay.addStretch(1)

    def show_value(self, value: Any, edited: bool = False) -> None:
        super().show_value(value, edited)
        dflt = value is MISSING
        v = self.node.default if dflt else value
        none = v is MISSING or v is None or v == ""
        opts = [str(o) for o in self.node.options]
        if not none and str(v) not in opts:
            opts.insert(0, str(v))
        self.cb.blockSignals(True)
        self.cb.clear()
        if none:
            self.cb.addItem("not set")
            self.cb.model().item(0).setEnabled(False)
        self.cb.addItems(opts)
        self.cb.setCurrentIndex(0 if none else opts.index(str(v)) + (1 if none else 0))
        self.cb.blockSignals(False)
        set_prop(self.cb, "dflt", dflt)

    def _activated(self, _i: int) -> None:
        text = self.cb.currentText()
        if text == "not set":
            return
        cur = self.node.default if self._shown is MISSING else self._shown
        if self._shown is not MISSING and str(cur) == text:
            return
        self._emit(text)


class BlockEditor(Editor):
    yaml_requested = Signal()

    def __init__(self, node, parent=None) -> None:
        super().__init__(node, parent)
        self.b = btn("Edit as YAML…", sm=True)
        self.b.clicked.connect(self.yaml_requested.emit)
        self.sum = ElideLabel("", tone="faint", small=True)
        self.lay.addWidget(self.b)
        self.lay.addWidget(self.sum, 1)

    def show_value(self, value: Any, edited: bool = False) -> None:
        super().show_value(value, edited)
        self.sum.set_text("" if value is MISSING else shown(value, self.node.path))


class SecretEditor(Editor):
    def __init__(self, node, parent=None) -> None:
        super().__init__(node, parent)
        self.lab = QLabel("")
        self.lay.addWidget(self.lab)
        self.lay.addStretch(1)

    def show_value(self, value: Any, edited: bool = False) -> None:
        super().show_value(value, edited)
        if value is MISSING or value in (None, ""):
            self.lab.setText("not set")
            self.lab.setStyleSheet(f"color: {T['faint']};")
        else:
            self.lab.setText("••••••")
            self.lab.setStyleSheet(f"color: {T['muted']}; letter-spacing: 2px;")


def edit_hint(node) -> str:
    """One line on how to change this kind of setting (the last line of its tooltip)."""
    if node.secret:
        return "Credentials are never shown or edited in the tables."
    return {
        "bool": "Click the switch to turn it on or off.",
        "enum": "Choose one of the values Hermes accepts.",
        "list": "Type the items separated by commas, then press Enter.",
        "block": "Edit as YAML… changes the whole block.",
    }.get(node.kind, "Type a value and press Enter or Tab.") + " It stays an edit on this page until you stage it."


def make_editor(node) -> Editor:
    if node.secret:
        return SecretEditor(node)
    if node.kind == "bool":
        return BoolEditor(node)
    if node.kind == "enum":
        return EnumEditor(node)
    if node.kind == "block":
        return BlockEditor(node)
    return LineEditor(node)


__all__ = ["Editor", "make_editor", "fmt", "Qt"]
