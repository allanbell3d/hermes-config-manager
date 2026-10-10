"""Review: everything staged, grouped by change, then 'Write config'.

"compression › threshold  0.5 → 0.7 · 41 agents" reads better than 41 near-identical diffs.
Writing re-reads every file first (an edit made elsewhere is reported as a conflict and left
alone), keeps a backup, writes, and reads the file back to verify.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Any

import yaml
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from ..core.paths import MISSING, PathT, get_in, is_same_or_ancestor
from ..core.yamlpatch import diff_paths
from ..services import apply as A
from ..services import chains as C
from ..services.editing import canonical
from ..services.review import ApplyFlow
from ..services.work import rows_of, spec_for
from ..core import secrets
from .editors import shown
from .theme import T, mono, sans
from .widgets import Chevron, Clickable, IconButton, btn, label, link, plural


class _DataScope:
    def __init__(self, data) -> None:
        self.data = data

    def draft_value(self, _key, path):
        return get_in(self.data, path)


@dataclass
class Item:
    key: str
    old: Any
    new: Any


@dataclass
class Group:
    gkey: str
    kind: str                 # "f" field | "c" chain
    crumbs: list[str]
    new_label: str
    path: PathT | None = None
    chain: str | None = None
    part: str = ""
    items: list[Item] = field(default_factory=list)


def chain_paths(spec: C.ChainSpec) -> list[PathT]:
    paths = [spec.head + (k,) for k in ("provider", "model", "default", "name", "reasoning_effort", "base_url",
                                        "api_mode")] + [spec.fallbacks]
    if spec.main_effort:
        paths += [("agent", "reasoning_effort"), ("agent", "reasoning_overrides")]
    return paths


def _auto(r) -> bool:
    return r[0] == "auto" and not r[1]          # provider auto, model '': Hermes chooses


def head_label(rows) -> str:
    if not rows:
        return "no model"
    h = rows[0]
    text = "auto (Hermes chooses)" if _auto(h) else f"{h[0] or '?'} / {h[1] or '?'}"
    return text + (f" · {h[2]}" if h[2] else "")


def chain_label(rows) -> str:
    return " → ".join(("auto" if _auto(r) else (r[1] or "?").rsplit("/", 1)[-1]) + (f" · {r[2]}" if r[2] else "")
                      for r in rows) or "no model"


def build_groups(state) -> list[Group]:
    session = state.session
    model = state.model
    groups: dict[str, Group] = {}
    for key in session.drafts.keys_with_changes():
        draft = session.drafts.draft(key)
        if draft is None or not draft.ops:
            continue
        base, after = draft.base, draft.view()
        covered: list[PathT] = []
        tasks = set(model.tasks)
        for d in (base, after):
            aux = get_in(d, ("auxiliary",), None)
            if isinstance(aux, dict):
                tasks |= {k for k, v in aux.items() if isinstance(v, dict)}
        for ck in ["primary", "delegation"] + [f"aux:{t}" for t in sorted(tasks)]:
            spec = spec_for(ck)
            old = rows_of(C.read_chain(_DataScope(base), spec, key))
            new = rows_of(C.read_chain(_DataScope(after), spec, key))
            covered += chain_paths(spec)
            if old == new:
                continue
            head_same, rest_same = old[:1] == new[:1], old[1:] == new[1:]
            part = "head" if not head_same and rest_same else "fallbacks" if head_same else "whole"
            sig = new[:1] if part == "head" else new[1:] if part == "fallbacks" else new
            gk = f"c|{ck}|{part}|{json.dumps(sig)}"
            sec = "model" if ck == "primary" else "delegation" if ck == "delegation" else "auxiliary"
            crumbs = [sec] + ([ck[4:]] if ck.startswith("aux:") else []) + \
                [{"head": "head model", "fallbacks": "fallbacks", "whole": "model chain"}[part]]
            nl = head_label(new) if part == "head" else (chain_label(new[1:]) if new[1:] else "no fallbacks") \
                if part == "fallbacks" else chain_label(new)
            g = groups.setdefault(gk, Group(gk, "c", crumbs, nl, chain=ck, part=part))
            g.items.append(Item(key, old, new))
        for path in diff_paths(base, after):
            if any(is_same_or_ancestor(c, path) for c in covered):
                continue
            nv = get_in(after, path)
            gk = f"f|{json.dumps([str(p) for p in path])}|{json.dumps(canonical(nv), default=str)}"
            g = groups.get(gk)
            if g is None:
                g = groups[gk] = Group(gk, "f", model.crumbs(path), "key removed" if nv is MISSING else
                                       shown(nv, path), path=path)
            g.items.append(Item(key, get_in(base, path), nv))
    order = {k: i for i, k in enumerate(session.ordered_keys())}
    out = sorted(groups.values(), key=lambda g: ".".join(g.crumbs))
    for g in out:
        g.items.sort(key=lambda it: order.get(it.key, 1 << 30))
    return out


def old_summary(g: Group) -> str:
    if g.kind == "c":
        labels = {(head_label(i.old) if g.part == "head" else chain_label(i.old[1:]) if g.part == "fallbacks"
                   else chain_label(i.old)) for i in g.items}
        if len(labels) > 1:
            return f"{len(labels)} " + {"head": "models", "fallbacks": "fallback lists", "whole": "chains"}[g.part]
        return labels.pop()
    vals = {json.dumps(canonical(i.old), default=str) for i in g.items}
    if len(vals) > 1:
        return f"{len(vals)} values"
    return shown(g.items[0].old, g.path or ())


def _yaml_lines(path: PathT, value: Any) -> list[str]:
    lines = []
    for i, k in enumerate(path[:-1]):
        lines.append("  " * i + f"{k}:")
    ind = "  " * (len(path) - 1)
    if value is MISSING:
        return lines
    text = yaml.safe_dump({path[-1]: secrets.redact(value, tuple(path))}, sort_keys=False, allow_unicode=True,
                          default_flow_style=False, width=4096).rstrip("\n").split("\n")
    text = secrets.mask_text("\n".join(text)).split("\n")
    return lines + [ind + t for t in text]


def _chain_lines(ck: str, rows) -> list[str]:
    spec = spec_for(ck)
    h = rows[0] if rows else [None, None, None]
    out = []
    head = list(spec.head)
    for i, k in enumerate(head):
        out.append("  " * i + f"{k}:")
    ind = "  " * len(head)
    out.append(f"{ind}provider: {h[0]}")
    out.append(f"{ind}{'default' if ck == 'primary' else 'model'}: {h[1]}")
    if h[2]:
        if ck == "primary":
            out += ["agent:", f"  reasoning_effort: {h[2]}"]
        else:
            out.append(f"{ind}reasoning_effort: {h[2]}")
    fb = list(spec.fallbacks)
    fi = "  " * (len(fb) - 1)
    out.append(f"{fi}{fb[-1]}:" + (" []" if len(rows) < 2 else ""))
    for r in rows[1:]:
        out.append(f"{fi}  - provider: {r[0]}")
        out.append(f"{fi}    model: {r[1]}")
        if r[2]:
            out.append(f"{fi}    reasoning_effort: {r[2]}")
    return out


def diff_text(state, g: Group, item: Item) -> list[tuple[str, str]]:
    if g.kind == "c":
        old, new = _chain_lines(g.chain, item.old), _chain_lines(g.chain, item.new)
    else:
        old, new = _yaml_lines(g.path, item.old), _yaml_lines(g.path, item.new)
    out = []
    for line in difflib.ndiff(old, new):
        tag = line[0]
        if tag == "?":
            continue
        out.append((tag, line[2:]))
    return out


class ReviewDialog(QDialog):
    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("Review")
        self.setModal(True)
        self.resize(960, 640)
        self.open: set[str] = set()
        self.agent_sel: dict[str, str] = {}
        self.results: list[A.ApplyResult] | None = None
        self.running = False
        self.flow = ApplyFlow(state.session, state.schema, self)
        self.flow.prepared.connect(self._prepared)
        self.flow.applied.connect(self._applied)
        self.flow.finished.connect(self._finished)
        self.flow.restarted.connect(self._restarted)
        self.restart_note = ""
        # Opening Review re-reads every file it would write (read-only): conflicts and the real file diff
        # are known before Write config.
        self.check = ApplyFlow(state.session, state.schema, self, tag="review-check")
        self.check.prepared.connect(self._checked)
        self.checked: dict[str, A.PreparedChange] = {}
        self.checking = False
        self.whole_file: set[str] = set()
        self.last_note = ""
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 12)
        v.setSpacing(8)
        h = QHBoxLayout()
        t = QLabel("Review")
        t.setProperty("h2", True)
        h.addWidget(t)
        self.sub = label("", tone="muted")
        h.addWidget(self.sub, 1)
        v.addLayout(h)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.addWidget(self.scroll, 1)
        f = QHBoxLayout()
        # Off every time Review opens: restarting interrupts running agents, so it is asked for each time.
        self.restart = QCheckBox("Restart gateways of written agents")
        self.restart.setChecked(False)
        self.restart.setToolTip("After writing, run the gateway restart command (over SSH) for every agent whose "
                                "file was written, so Hermes reads the new config. Running conversations are "
                                "interrupted. Off each time Review opens.")
        f.addWidget(self.restart)
        f.addStretch(1)
        self.unstage_all = link("Unstage all", tip="Remove every staged change from Review. Nothing is written; "
                                                   "the files stay as they are.")
        self.unstage_all.clicked.connect(self._unstage_all)
        f.addWidget(self.unstage_all)
        close = btn("Close", tip="Close Review. Staged changes stay staged.")
        close.clicked.connect(self.close)
        f.addWidget(close)
        self.write = btn("Write config", pri=True,
                         tip="Write every staged change to the agents' config.yaml files. Only the changed keys "
                             "are touched (comments and layout are kept); each file is backed up first "
                             "(config.yaml.bak_…) and read back to check. A file changed elsewhere is skipped.")
        self.write.clicked.connect(self._write)
        f.addWidget(self.write)
        self.files = label("", tone="muted")
        self.files.setToolTip("How many config files Write config will change")
        f.addWidget(self.files)
        v.addLayout(f)
        state.session.drafts_changed.connect(self._drafts_changed)
        self.groups: list[Group] = []
        g = build_groups(state)
        if len(g) == 1:
            self.open.add(g[0].gkey)
        last = getattr(state, "last_results", None)
        if last and not g:
            self.results, self.last_note = last
        self.recheck()
        self.render()

    def recheck(self) -> None:
        keys = self.state.session.drafts.keys_with_changes()
        if not keys:
            return
        self.checking = True
        self.check.prepare(keys)

    def _checked(self, prepared) -> None:
        self.checking = False
        self.checked = {p.key: p for p in prepared}
        if self.isVisible() or not self.running:
            self.render()

    def _status_of(self, key: str) -> tuple[str, str]:
        p = self.checked.get(key)
        if p is None:
            return ("checking…" if self.checking else ""), ""
        if p.status == A.READY:
            notes = (["changed on the host; edits re-applied"] if p.rebased else []) + (["warning"] if p.warnings else [])
            return "; ".join(notes), "\n".join([m for m in [p.message] if m] + p.warnings)
        word = {A.CONFLICT: "changed on the host", A.INVALID: "invalid file", A.ERROR: "not reachable",
                A.NOOP: "nothing to change"}.get(p.status, p.status)
        return word, p.message

    # ------------------------------------------------------------------ render
    def _drafts_changed(self, _keys) -> None:
        if self.isVisible():
            self.render()

    def done(self, code: int) -> None:
        try:
            self.state.session.drafts_changed.disconnect(self._drafts_changed)
        except (RuntimeError, TypeError):
            pass
        super().done(code)

    def render(self) -> None:
        st = self.state
        self.groups = build_groups(st)
        files = st.session.drafts.keys_with_changes()
        blocked = [k for k in files if (p := self.checked.get(k)) is not None and not p.writable]
        warned = [k for k in files if (p := self.checked.get(k)) is not None and p.writable and p.warnings]
        self.sub.setText((plural(len(self.groups), "staged change") + " · " + plural(len(files), "agent file") +
                          ". Only the touched keys are rewritten, and a backup is kept." +
                          (f" {plural(len(blocked), 'file')} cannot be written as it is (marked below)." if blocked
                           else "")
                          + (f" {plural(len(warned), 'file')} can be written with a warning (amber below; hover "
                             "for details)." if warned else "")
                          + (" Checking the files…" if self.checking else "")) if self.groups else "")
        inner = QWidget()
        inner.setProperty("surface", "page")
        inner.setAttribute(Qt.WA_StyledBackground, True)
        iv = QVBoxLayout(inner)
        iv.setContentsMargins(0, 0, 0, 0)
        iv.setSpacing(0)
        if self.results is not None:
            iv.addWidget(self._results())
        if not self.groups:
            n = label("Nothing is staged any more." if self.results is not None else
                      "Nothing is staged yet. Edit a value, press Stage, and it is listed here before anything is "
                      "written.", tone="muted", wrap=True)
            n.setContentsMargins(4, 10, 4, 20)
            iv.addWidget(n)
        for g in self.groups:
            iv.addWidget(self._group(g))
        iv.addStretch(1)
        pos = self.scroll.verticalScrollBar().value()
        old = self.scroll.takeWidget()
        self.scroll.setWidget(inner)
        self.scroll.verticalScrollBar().setValue(pos)
        if old is not None:
            old.deleteLater()
        can = bool(self.groups) and not self.running
        self.write.setEnabled(can)
        self.write.setText("Writing…" if self.running else "Write config")
        self.unstage_all.setEnabled(can)
        self.files.setText(plural(len(files), "agent file") if can else "")

    def _group(self, g: Group) -> QWidget:
        st = self.state
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = Clickable()
        h = QHBoxLayout(head)
        h.setContentsMargins(4, 8, 4, 8)
        h.setSpacing(10)
        opened = g.gkey in self.open
        h.addWidget(Chevron(opened))
        cr = QLabel(f"<span style='color:{T['faint']}'> › </span>".join(
            (f"<b>{c}</b>" if i == len(g.crumbs) - 1 else c) for i, c in enumerate(g.crumbs)))
        cr.setTextFormat(Qt.RichText)
        cr.setFont(mono())
        h.addWidget(cr)
        ov = label(old_summary(g), tone="muted", is_mono=True)
        ov.setToolTip(old_summary(g))
        h.addSpacing(14)
        h.addWidget(ov)
        h.addWidget(label("→", tone="faint"))
        nv = QLabel(g.new_label)
        nv.setFont(mono(12.5, 600))
        nv.setToolTip(g.new_label)
        h.addWidget(nv)
        h.addWidget(label("· " + plural(len(g.items), "agent"), tone="muted"))
        h.addStretch(1)
        un = link("Unstage", tip="Remove this change from Review for all its agents. Nothing is written.")
        un.setEnabled(not self.running)
        un.clicked.connect(lambda: self._unstage(g))
        h.addWidget(un)
        head.setToolTip("Click to see the agents this change applies to and the diff of each agent's file")
        head.clicked.connect(lambda: self._toggle(g.gkey))
        v.addWidget(head)
        if opened:
            body = QSplitter(Qt.Horizontal)
            lst = QListWidget()
            lst.setMaximumWidth(300)
            lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            lst.setTextElideMode(Qt.ElideRight)
            lst.setToolTip("The agents this change is staged for. Pick one to see its diff; red means its file "
                           "cannot be written as it is (see the agent's note).")
            cur = self.agent_sel.get(g.gkey) or g.items[0].key
            for it in g.items:
                rec = st.session.record(it.key)
                word, msg = self._status_of(it.key)
                li = QListWidgetItem((f"{rec.name}    {rec.host}" if rec else it.key) + (f"   · {word}" if word else ""))
                li.setData(Qt.UserRole, it.key)
                p = self.checked.get(it.key)
                if p is not None and not p.writable:
                    li.setForeground(T.c("bad"))
                elif p is not None and p.warnings:
                    li.setForeground(T.c("amber"))
                if msg:
                    li.setToolTip(msg)
                lst.addItem(li)
                if it.key == cur:
                    lst.setCurrentItem(li)
            lst.setMaximumHeight(264)
            diff = QPlainTextEdit()
            diff.setReadOnly(True)
            diff.setFont(mono())
            diff.setMaximumHeight(264)
            diff.setToolTip("The lines of this agent's config.yaml that will change: - removed, + added. "
                            "Credentials are masked.")
            diff.setLineWrapMode(QPlainTextEdit.NoWrap)
            self._fill_diff(diff, g, cur)
            lst.currentItemChanged.connect(lambda item, _p, g=g, diff=diff: self._pick_agent(g, item, diff))
            body.addWidget(lst)
            body.addWidget(diff)
            body.setSizes([260, 640])
            holder = QWidget()
            hl = QVBoxLayout(holder)
            hl.setContentsMargins(24, 0, 0, 12)
            hl.addWidget(body)
            acts = QHBoxLayout()
            acts.setSpacing(18)
            whole = link("Show this change" if g.gkey in self.whole_file else "Show the whole-file diff",
                         tip="The exact lines that will change in this agent's file (all of its staged changes), "
                             "as re-read from the host just now")
            whole.clicked.connect(lambda _=False, g=g: self._toggle_whole(g.gkey))
            acts.addWidget(whole)
            one = link("Unstage for this agent only", tip="Remove this change for the selected agent only; the "
                                                          "other agents keep it")
            one.setEnabled(not self.running)
            one.clicked.connect(lambda _=False, g=g: self._unstage_one(g, self.agent_sel.get(g.gkey) or g.items[0].key))
            acts.addWidget(one)
            acts.addStretch(1)
            hl.addLayout(acts)
            v.addWidget(holder)
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {T['soft']};")
        v.addWidget(sep)
        return w

    def _fill_diff(self, view: QPlainTextEdit, g: Group, key: str) -> None:
        st = self.state
        item = next((i for i in g.items if i.key == key), g.items[0])
        rec = st.session.record(item.key)
        if g.gkey in self.whole_file:
            p = self.checked.get(item.key)
            if p is None:
                text = "Checking the file on the host…" if self.checking else "Not checked."
            elif p.diff:
                text = p.diff
            else:
                text = f"{p.status}: {p.message}"
            view.setPlainText(secrets.mask_text(text))
            return
        lines = [f"<span style='color:{T['faint']}'># {rec.name if rec else key} on "
                 f"{rec.host if rec else ''} · config.yaml</span>"]
        for tag, text in diff_text(st, g, item):
            esc = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace(" ", "&nbsp;")
            bg = T["add"] if tag == "+" else T["del"] if tag == "-" else None
            mark = tag if tag in "+-" else "&nbsp;"
            lines.append(f"<div style='background:{bg}'>{mark}&nbsp;{esc}</div>" if bg else f"<div>{mark}&nbsp;{esc}</div>")
        view.document().setDefaultFont(mono())
        view.appendHtml("".join(lines))
        view.moveCursor(view.textCursor().MoveOperation.Start)
        view.verticalScrollBar().setValue(0)

    def _pick_agent(self, g: Group, item, diff) -> None:
        if item is None:
            return
        self.agent_sel[g.gkey] = item.data(Qt.UserRole)
        diff.clear()
        self._fill_diff(diff, g, self.agent_sel[g.gkey])

    def _toggle_whole(self, gk: str) -> None:
        (self.whole_file.discard if gk in self.whole_file else self.whole_file.add)(gk)
        self.render()

    def _unstage_one(self, g: Group, key: str) -> None:
        drafts = self.state.session.drafts
        if g.kind == "f":
            drafts.revert([key], g.path)
        else:
            for p in chain_paths(spec_for(g.chain)):
                drafts.revert([key], p)

    def _toggle(self, gk: str) -> None:
        (self.open.discard if gk in self.open else self.open.add)(gk)
        self.render()

    def _results(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 4, 0, 12)
        v.setSpacing(4)
        rows = self.results or []
        good = (A.WRITTEN, A.UP_TO_DATE)
        ok = sum(1 for r in rows if r.status in good)
        bad = [r for r in rows if r.status not in good]
        t = QLabel((self.last_note + " · " if self.last_note and not self.running else "") +
                   ("Writing… " + f"{len(rows)} done" if self.running else f"{ok} written") +
                   (f" · <span style='color:{T['bad']}'>{len(bad)} not written</span>" if bad else ""))
        t.setTextFormat(Qt.RichText)
        t.setFont(sans(13, 600))
        v.addWidget(t)
        if bad and not self.running:
            v.addWidget(label("Agents that were not written keep their staged change. Press Write config again to "
                              "retry them.", tone="muted", wrap=True))
        if self.restart_note:
            v.addWidget(label(self.restart_note, tone="muted", wrap=True))
        box = QListWidget()
        box.setMaximumHeight(230)
        box.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        box.setTextElideMode(Qt.ElideRight)
        for r in bad + [r for r in rows if r.status in good]:
            rec = self.state.session.record(r.key)
            word = {A.WRITTEN: "written", A.SKIPPED: "skipped", A.FAILED: "failed", A.UNCERTAIN: "check",
                    A.CANCELLED: "cancelled", A.UP_TO_DATE: "up to date"}.get(r.status, r.status)
            short = "backup kept" if r.status == A.WRITTEN and r.backup_path else r.message.split("; backup")[0]
            li = QListWidgetItem(f"{word:10}  {rec.name if rec else r.key}  ({rec.host if rec else ''})  {short}")
            li.setToolTip(r.message)
            li.setForeground(T.c("ok") if r.status in good else T.c("bad"))
            box.addItem(li)
        box.setFont(mono(12))
        v.addWidget(box)
        return w

    # ------------------------------------------------------------------ actions
    def _unstage(self, g: Group) -> None:
        drafts = self.state.session.drafts
        keys = [i.key for i in g.items]
        if g.kind == "f":
            drafts.revert(keys, g.path)
        else:
            for p in chain_paths(spec_for(g.chain)):
                drafts.revert(keys, p)

    def _unstage_all(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        n = len(self.groups)
        if QMessageBox.question(self, "Unstage all", f"Take all {plural(n, 'staged change')} out of Review? "
                                "Nothing is written. Ctrl+Z brings them back.") != QMessageBox.Yes:
            return
        self.state.session.drafts.discard()
        self.state.say(f"Unstaged {plural(n, 'change')}. Ctrl+Z brings them back.")

    def _write(self) -> None:
        keys = self.state.session.drafts.keys_with_changes()
        if not keys or self.running:
            return
        self.running = True
        self.results = []
        self.last_note = ""
        self.restart_note = ""
        self.render()
        self.flow.prepare(keys)

    def _prepared(self, prepared) -> None:
        self.flow.apply(prepared, restart_gateways=self.restart.isChecked())

    def _applied(self, result) -> None:
        self.results.append(result)

    def _finished(self, results) -> None:
        import time as _time

        self.running = False
        self.results = list(results)
        self.state.last_results = (list(results), "Last write at " + _time.strftime("%H:%M"))
        self.checked = {}
        self.recheck()
        ok = sum(1 for r in results if r.status == A.WRITTEN)
        same = sum(1 for r in results if r.status == A.UP_TO_DATE)
        bad = len(results) - ok - same
        self.state.say(f"Wrote {plural(ok, 'agent file')}" + (f"; {same} already up to date" if same else "")
                       + (f"; {bad} not written" if bad else "") + ".")
        self.render()

    def _restarted(self, outcomes) -> None:
        if not outcomes:
            return
        ok = sum(1 for o in outcomes if getattr(o, "ok", False))
        reasons: dict[str, int] = {}
        for o in outcomes:
            if not getattr(o, "ok", False):
                msg = getattr(o, "message", "") or "failed"
                reasons[msg] = reasons.get(msg, 0) + 1
        self.restart_note = f"Gateways restarted: {ok} of {len(outcomes)}." + "".join(
            f" Not restarted ({n}): {msg}." for msg, n in list(reasons.items())[:4])
        self.render()

    def closeEvent(self, ev) -> None:
        if self.running:
            ev.ignore()
            return
        super().closeEvent(ev)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape and self.running:
            return
        if not self.running and ev.modifiers() & Qt.ControlModifier and ev.key() in (Qt.Key_Z, Qt.Key_Y):
            drafts = self.state.session.drafts
            redo = ev.key() == Qt.Key_Y or bool(ev.modifiers() & Qt.ShiftModifier)
            (drafts.redo if redo else drafts.undo)()      # undo / redo staging, as in the main window
            return
        super().keyPressEvent(ev)


__all__ = ["ReviewDialog", "build_groups", "IconButton"]
