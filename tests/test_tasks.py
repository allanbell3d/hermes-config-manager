from __future__ import annotations

import threading
import time

from hermes_config_manager.services.tasks import TaskRunner, TaskState, TaskTimeout


def pump(app, predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_result_delivered_on_ui_thread(qapp):
    runner = TaskRunner()
    got = []
    ui_thread = threading.get_ident()
    runner.submit(lambda ctx: threading.get_ident(), on_done=lambda r: got.append((r, threading.get_ident())))
    assert pump(qapp, lambda: got)
    worker, callback_thread = got[0]
    assert worker != ui_thread and callback_thread == ui_thread
    runner.shutdown()


def test_stale_results_are_dropped(qapp):
    runner = TaskRunner()
    got = []
    release = threading.Event()

    def slow(ctx, value):
        release.wait(2)
        return value

    runner.submit(slow, 1, key="load:a", on_done=got.append)
    runner.submit(slow, 2, key="load:a", on_done=got.append)
    release.set()
    assert pump(qapp, lambda: got and runner.active_count() == 0)
    time.sleep(0.05)
    qapp.processEvents()
    assert got == [2]
    runner.shutdown()


def test_timeout_reports_and_bounds_hung_threads(qapp):
    runner = TaskRunner(per_host_limit=2)
    errors = []
    block = threading.Event()
    for _ in range(4):
        runner.submit(lambda ctx: block.wait(5), host="smb", timeout=0.3, on_error=errors.append)
    assert pump(qapp, lambda: len(errors) == 4, timeout=3)
    assert all(isinstance(e, TaskTimeout) for e in errors)
    # Two threads were started and are blocked; the other two never started.
    assert runner.hung_count("smb") == 2
    block.set()
    assert pump(qapp, lambda: runner.hung_count("smb") == 0)
    runner.shutdown()


def test_per_host_limit_serialises(qapp):
    runner = TaskRunner(per_host_limit=1)
    active = []
    peak = [0]
    lock = threading.Lock()
    done = []

    def work(ctx):
        with lock:
            active.append(1)
            peak[0] = max(peak[0], len(active))
        time.sleep(0.03)
        with lock:
            active.pop()

    for _ in range(5):
        runner.submit(work, host="ssh", on_done=done.append)
    assert pump(qapp, lambda: len(done) == 5)
    assert peak[0] == 1
    runner.shutdown()


def test_cooperative_cancel(qapp):
    runner = TaskRunner()
    started = threading.Event()
    finished = []

    def loop(ctx):
        started.set()
        while not ctx.wait(0.01):
            pass
        ctx.check()

    task = runner.submit(loop, on_done=finished.append, on_error=finished.append)
    started.wait(2)
    runner.cancel(task)
    assert task.state is TaskState.CANCELLED
    pump(qapp, lambda: False, timeout=0.2)
    assert finished == []  # cancelled tasks do not call back
    runner.shutdown()


def test_callbacks_dropped_when_receiver_destroyed(qapp):
    """A dialog closed while its host query runs must not receive the result."""
    import shiboken6
    from PySide6.QtWidgets import QLabel

    runner = TaskRunner()
    label = QLabel()
    release = threading.Event()
    calls = []

    def on_done(result):          # closes over the widget, like dialog callbacks do
        calls.append(result)
        label.setText(str(result))

    task = runner.submit(lambda ctx: release.wait(2) and "late", on_done=on_done)
    assert label in task.receivers
    shiboken6.delete(label)       # widget destroyed while the task is still running
    release.set()
    assert pump(qapp, lambda: runner.active_count() == 0)
    assert calls == []
    runner.shutdown()


def test_progress_reports(qapp):
    runner = TaskRunner()
    seen = []
    done = []

    def work(ctx):
        for i in range(3):
            ctx.progress(i)
        return "ok"

    runner.submit(work, on_progress=seen.append, on_done=done.append)
    assert pump(qapp, lambda: done)
    assert seen == [0, 1, 2]
    runner.shutdown()


def test_waiting_behind_slow_work_on_the_same_host_is_not_a_timeout(qapp):
    runner = TaskRunner(per_host_limit=1)
    done, errors = [], []
    for i in range(4):          # each takes 0.15 s; together longer than one task's 0.4 s time-out
        runner.submit(lambda ctx, i=i: (time.sleep(0.15), i)[1], host="share", timeout=0.4,
                      on_done=done.append, on_error=errors.append)
    assert pump(qapp, lambda: len(done) + len(errors) == 4, timeout=5)
    assert sorted(done) == [0, 1, 2, 3] and not errors
    runner.shutdown()
