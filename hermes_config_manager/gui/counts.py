"""How many settings differ across the checked agents, per section (computed in idle time)."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from ..services import chains as C


class DiffCounter(QObject):
    changed = Signal()

    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.counts: dict[str, int] = {}
        self._queue: list[str] = []
        self._new: dict[str, int] = {}
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._start)
        self._tick = QTimer(self)
        self._tick.setInterval(0)
        self._tick.timeout.connect(self._one)
        for sig in (state.scope_changed, state.values_changed, state.layout_changed, state.model_changed):
            sig.connect(self.schedule)

    def schedule(self, *_args) -> None:
        self._debounce.start()

    def _start(self) -> None:
        keys = self.state.scope().usable_keys
        if len(keys) < 2:
            self._tick.stop()
            if any(self.counts.values()):
                self.counts = {}
                self.changed.emit()
            return
        self._queue = list(self.state.model.sections)
        self._new = {}
        self._tick.start()

    def _one(self) -> None:
        if not self._queue:
            self._tick.stop()
            self.counts = self._new
            self.changed.emit()
            return
        key = self._queue.pop(0)
        self._new[key] = self.section(key)

    def section(self, key: str) -> int:
        st = self.state
        sec = st.model.sections.get(key)
        if sec is None:
            return 0
        scope = st.scope()
        keys = scope.usable_keys
        if len(keys) < 2:
            return 0
        n = st.count_differ(st.visible_leaves(sec.own_nodes()))
        if sec.chain in ("primary", "delegation"):
            spec = C.PRIMARY if sec.chain == "primary" else C.DELEGATION
            n += int(len(C.variants(scope, spec, keys)) > 1)
        elif sec.chain == "roster":
            from ..services.roster_edit import differ_count

            n += differ_count(st.session, keys)
        elif sec.chain == "auxiliary":
            for task in st.model.tasks:
                if st.task_hidden(task):
                    continue
                n += int(len(C.variants(scope, C.aux_spec(task), keys)) > 1)
                n += st.count_differ(st.visible_leaves(st.model.task_nodes(task)))
        return n

    def page(self, page) -> int:
        return sum(self.counts.get(k, 0) for k in self.state.visible_sections(page))
