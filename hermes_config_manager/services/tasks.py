"""Bounded background work for the UI.

Guarantees used throughout the application:

* **Bounded**: at most ``max_workers`` threads overall and ``per_host_limit``
  concurrent operations per host.  A hung SMB/SSH call keeps its slot until
  the thread returns, so a dead host can never consume unbounded threads.
* **Stale results are dropped**: tasks submitted with the same ``key``
  supersede each other; only the newest generation reaches its callbacks.
* **Timeouts**: the deadline counts from submission (queued time included).
  A timed-out task is reported immediately; its thread, if blocked in the
  operating system, is abandoned and its eventual result discarded.
* **Cancellation** is cooperative through :class:`TaskContext`.
* Callbacks always run on the UI thread.
"""

from __future__ import annotations

import itertools
import threading
import time
import traceback
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Hashable

import shiboken6
from PySide6.QtCore import QObject, QTimer, Signal


class CancelledError(Exception):
    """Raised inside a task when its context was cancelled."""


class TaskTimeout(TimeoutError):
    pass


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed out"
    STALE = "stale"


class TaskContext:
    """Handed to task functions for cancellation checks and progress reports."""

    def __init__(self, task: "Task", bridge: "_Bridge") -> None:
        self._task = task
        self._bridge = bridge
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    def check(self) -> None:
        if self._event.is_set():
            raise CancelledError(self._task.label or "cancelled")

    def progress(self, payload: Any) -> None:
        if not self._event.is_set():
            self._bridge.progress.emit(self._task, payload)

    def wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; returns True if cancelled meanwhile."""
        return self._event.wait(seconds)


@dataclass(eq=False)
class Task:
    id: int
    label: str
    fn: Callable[..., Any]
    args: tuple
    kwargs: dict
    key: Hashable | None
    host: str | None
    timeout: float | None
    on_done: Callable[[Any], None] | None
    on_error: Callable[[BaseException], None] | None
    on_progress: Callable[[Any], None] | None
    generation: int = 0
    state: TaskState = TaskState.QUEUED
    submitted: float = field(default_factory=time.monotonic)
    started: float | None = None
    finished: float | None = None
    result: Any = None
    error: BaseException | None = None
    context: TaskContext | None = None
    # Qt objects whose destruction makes the callbacks meaningless (dialogs, pages...).
    receivers: tuple = ()
    # True once the worker thread returned (slot released).
    thread_done: bool = False

    @property
    def elapsed(self) -> float:
        end = self.finished if self.finished is not None else time.monotonic()
        return end - self.submitted


def _receivers(explicit, *callbacks) -> tuple:
    """QObjects the callbacks belong to: bound-method owners and closed-over QObjects."""
    found = []
    if explicit is not None:
        found.append(explicit)
    for cb in callbacks:
        if cb is None:
            continue
        owner = getattr(cb, "__self__", None)
        if isinstance(owner, QObject):
            found.append(owner)
        for cell in getattr(cb, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, QObject):
                found.append(value)
        for default in getattr(cb, "__defaults__", None) or ():
            if isinstance(default, QObject):
                found.append(default)
    unique = []
    for obj in found:
        if all(obj is not u for u in unique):
            unique.append(obj)
    return tuple(unique)


class _Bridge(QObject):
    finished = Signal(object)
    progress = Signal(object, object)


class TaskRunner(QObject):
    """Runs callables in worker threads and reports back on the UI thread."""

    task_started = Signal(object)
    task_finished = Signal(object)
    activity_changed = Signal(int)
    host_hung = Signal(str, int)   # host, number of abandoned operations still blocked

    def __init__(self, max_workers: int = 12, per_host_limit: int = 4, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="hm-task")
        self._per_host_limit = max(1, per_host_limit)
        self._host_limits: dict[str, int] = {}
        self._ids = itertools.count(1)
        self._generations: dict[Hashable, int] = defaultdict(int)
        self._running: dict[str, int] = defaultdict(int)        # host -> occupied slots
        self._hung: dict[str, int] = defaultdict(int)           # host -> abandoned blocked threads
        self._queues: dict[str, deque[Task]] = defaultdict(deque)
        self._active: dict[int, Task] = {}
        self._bridge = _Bridge()
        self._bridge.finished.connect(self._on_thread_finished)
        self._bridge.progress.connect(self._on_progress)
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(150)
        self._watchdog.timeout.connect(self._check_deadlines)
        self._closed = False

    # -- configuration ----------------------------------------------------
    def set_host_limit(self, host: str, limit: int) -> None:
        self._host_limits[host] = max(1, limit)

    def _limit(self, host: str) -> int:
        return self._host_limits.get(host, self._per_host_limit)

    # -- submission -------------------------------------------------------
    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        label: str = "",
        key: Hashable | None = None,
        host: str | None = None,
        timeout: float | None = None,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_progress: Callable[[Any], None] | None = None,
        receiver: QObject | None = None,
        **kwargs: Any,
    ) -> Task:
        """Queue ``fn(ctx, *args, **kwargs)``; returns the task handle.

        Callbacks are dropped if their receiver (``receiver``, or the QObject a
        bound method / lambda closes over) has been destroyed meanwhile - e.g. a
        dialog closed while its host query was still running.
        """
        task = Task(next(self._ids), label, fn, args, kwargs, key, host, timeout, on_done, on_error, on_progress)
        task.context = TaskContext(task, self._bridge)
        task.receivers = _receivers(receiver, on_done, on_error, on_progress)
        if self._closed:
            task.state = TaskState.CANCELLED
            return task
        if key is not None:
            self._generations[key] += 1
            task.generation = self._generations[key]
            self._supersede(key, task.generation)
        self._active[task.id] = task
        slot_host = host or ""
        if self._running[slot_host] < self._limit(slot_host):
            self._start(task)
        else:
            self._queues[slot_host].append(task)
        if not self._watchdog.isActive():
            self._watchdog.start()
        self.activity_changed.emit(self.active_count())
        return task

    def _supersede(self, key: Hashable, generation: int) -> None:
        for task in list(self._active.values()):
            if task.key == key and task.generation < generation and task.state in (TaskState.QUEUED, TaskState.RUNNING):
                self._retire(task, TaskState.STALE)

    def _start(self, task: Task) -> None:
        slot_host = task.host or ""
        self._running[slot_host] += 1
        task.state = TaskState.RUNNING
        task.started = time.monotonic()
        self.task_started.emit(task)
        self._executor.submit(self._run, task)

    def _run(self, task: Task) -> None:  # worker thread
        try:
            assert task.context is not None
            task.context.check()
            task.result = task.fn(task.context, *task.args, **task.kwargs)
        except BaseException as exc:  # noqa: BLE001 - reported to the UI
            task.error = exc
            if not isinstance(exc, (CancelledError, TaskTimeout)):
                task.error.__traceback_text__ = traceback.format_exc()  # type: ignore[attr-defined]
        finally:
            self._bridge.finished.emit(task)

    # -- completion (UI thread) ------------------------------------------
    def _on_thread_finished(self, task: Task) -> None:
        task.thread_done = True
        slot_host = task.host or ""
        self._running[slot_host] = max(0, self._running[slot_host] - 1)
        if task.state is TaskState.TIMED_OUT:
            self._hung[slot_host] = max(0, self._hung[slot_host] - 1)
            self.host_hung.emit(slot_host, self._hung[slot_host])
        elif task.state is TaskState.RUNNING:
            task.finished = time.monotonic()
            if task.error is None:
                task.state = TaskState.DONE
                self._deliver(task.on_done, task.result, task)
            elif isinstance(task.error, CancelledError):
                task.state = TaskState.CANCELLED
            else:
                task.state = TaskState.FAILED
                self._deliver(task.on_error, task.error, task)
            self._active.pop(task.id, None)
            self.task_finished.emit(task)
        self._pump(slot_host)
        self.activity_changed.emit(self.active_count())

    def _deliver(self, callback: Callable[[Any], None] | None, value: Any, task: "Task | None" = None) -> None:
        if callback is None:
            return
        if task is not None and not all(shiboken6.isValid(obj) for obj in task.receivers):
            return  # the widget that asked has gone away
        try:
            callback(value)
        except Exception:  # a UI callback bug must not kill the runner
            traceback.print_exc()

    def _on_progress(self, task: Task, payload: Any) -> None:
        if task.state is TaskState.RUNNING and task.on_progress is not None:
            self._deliver(task.on_progress, payload, task)

    def _pump(self, slot_host: str) -> None:
        queue = self._queues.get(slot_host)
        while queue and self._running[slot_host] < self._limit(slot_host):
            task = queue.popleft()
            if task.state is TaskState.QUEUED:
                self._start(task)

    def _retire(self, task: Task, state: TaskState, error: BaseException | None = None) -> None:
        """Finish a task from the UI side (stale/cancel/timeout)."""
        was_queued = task.state is TaskState.QUEUED
        task.state = state
        task.finished = time.monotonic()
        if task.context is not None:
            task.context.cancel()
        if was_queued:
            queue = self._queues.get(task.host or "")
            if queue and task in queue:
                queue.remove(task)
        if state is TaskState.TIMED_OUT and not was_queued and not task.thread_done:
            slot_host = task.host or ""
            self._hung[slot_host] += 1
            self.host_hung.emit(slot_host, self._hung[slot_host])
        self._active.pop(task.id, None)
        if error is not None:
            task.error = error
            self._deliver(task.on_error, error, task)
        self.task_finished.emit(task)

    def _check_deadlines(self) -> None:
        now = time.monotonic()
        for task in list(self._active.values()):
            if task.timeout is None:
                continue
            where = f" on {task.host}" if task.host else ""
            if task.state is TaskState.QUEUED:
                # Waiting behind other work on the same host is not a time-out: the clock starts when the
                # task starts.  Only a host whose running work has stopped answering ends the wait.
                if self._hung[task.host or ""] and now - task.submitted > task.timeout:
                    self._retire(task, TaskState.TIMED_OUT, TaskTimeout(
                        f"{task.label or 'Operation'}{where}: not attempted (the host stopped answering)"))
                continue
            if task.started is not None and now - task.started > task.timeout:
                self._retire(
                    task,
                    TaskState.TIMED_OUT,
                    TaskTimeout(f"{task.label or 'Operation'}{where} timed out after {task.timeout:.0f}s (no response)"),
                )
        if not self._active:
            self._watchdog.stop()
        self.activity_changed.emit(self.active_count())

    # -- control ----------------------------------------------------------
    def cancel(self, task: Task) -> None:
        if task.state in (TaskState.QUEUED, TaskState.RUNNING):
            self._retire(task, TaskState.CANCELLED)
            self.activity_changed.emit(self.active_count())

    def cancel_where(self, predicate: Callable[[Task], bool]) -> int:
        victims = [t for t in self._active.values() if predicate(t)]
        for task in victims:
            self.cancel(task)
        return len(victims)

    def cancel_host(self, host: str) -> int:
        return self.cancel_where(lambda t: t.host == host)

    def active_count(self) -> int:
        return len(self._active)

    def hung_count(self, host: str) -> int:
        return self._hung.get(host, 0)

    def shutdown(self) -> None:
        """Cancel everything; do not wait for blocked threads."""
        self._closed = True
        self.cancel_where(lambda t: True)
        self._watchdog.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)
