"""UI speed benchmark of the screens (101 agents, 2 hosts).

Each measurement = the action + processing the events it causes (repaints of the visible widgets
included), timed with perf_counter.  Budgets are the first build's numbers (UI-REDESIGN-BRIEF.md).

    switch section          show a page (all pages, three rounds; pages are prebuilt in idle time)
    select agent            check one more agent while a 59-setting page is showing
    open model picker       build and show the picker
    filter agents           type in the agents filter
    single edit             change one value on a page (working value, one agent checked)
    bulk edit (all agents)  stage one edit for all 101 agents (drafts + refresh)

Usage:  python bench/bench_ui.py [--real]   (--real: native Windows platform, a window appears briefly)
"""

import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

if "--real" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
HOME = Path(tempfile.mkdtemp(prefix="hm-bench-"))
os.environ["HERMES_CONFIG_MANAGER_HOME"] = str(HOME / "home")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from hermes_config_manager.services.session import Session  # noqa: E402
from hermes_config_manager.transports.hosts import HostConfig  # noqa: E402

BUDGET = {"switch section": 83, "select agent": 100, "open model picker": 65, "filter agents": 32,
          "single edit": 42, "bulk edit (all agents)": 39}


def settle(app) -> None:
    for _ in range(3):
        app.processEvents()


def timed(app, fn) -> float:
    start = time.perf_counter()
    fn()
    settle(app)
    return (time.perf_counter() - start) * 1000


def p95(values) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]


def wait(app, pred, timeout=60.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        app.processEvents()
        time.sleep(0.002)


def main() -> None:
    data = HOME / "hosts"
    shutil.copytree(ROOT / "bench" / "fixtures" / "generated-100-2hosts", data)
    app = QApplication([])
    from hermes_config_manager.gui.app import build
    from hermes_config_manager.gui.dialogs import overlay_for, picker_for

    hosts = [HostConfig("studio", hermes_root=str(data / "hostA")), HostConfig("nas", hermes_root=str(data / "hostB"))]
    session = Session(hosts=hosts, use_cache=False)
    t0 = time.perf_counter()
    _app, state, win = build(session)
    win.resize(1440, 900)
    win.show()
    state.start()
    wait(app, lambda: len(session.agents) > 100 and not session.is_refreshing())
    settle(app)
    load_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    wait(app, lambda: not win._warm_timer.isActive())
    warm_ms = (time.perf_counter() - t1) * 1000
    keys = session.ordered_keys()
    session.set_checked(keys[:12], True)
    wait(app, lambda: False, 0.5)
    pages = [p.id for p in state.layout.pages()]
    results: dict[str, list[float]] = {k: [] for k in BUDGET}
    for _round in range(3):
        for pid in pages:
            results["switch section"].append(timed(app, lambda p=pid: win.show_page(p)))
    win.show_page("interface/display")
    settle(app)
    session.replace_checked(keys[:1])
    settle(app)
    for key in keys[1:41]:
        results["select agent"].append(timed(app, lambda k=key: session.set_checked([k], True)))
    picker = picker_for(win, state)                      # built once per window, like the app does
    overlay = overlay_for(win)
    for _ in range(15):
        results["open model picker"].append(timed(app, lambda: (picker.prepare("Head model · model"),
                                                                overlay.show_panel(picker))))
        overlay.hide()
        settle(app)
    for text in ["ana", "bench-ana-dis", "glm", "studio", "zzz", "", "avatar", "grok", ""] * 3:
        results["filter agents"].append(timed(app, lambda t=text: win.agents.filter.setText(t)))
    win.agents.filter.setText("")
    path = ("compression", "threshold")
    win.go_to("compression", path)
    settle(app)
    session.replace_checked(keys[3:4])
    settle(app)
    view = win.current_page()
    values = [0.5, 0.6, 0.7, 0.8, 0.9] * 4

    def row():
        for t in view.tables:
            it = t.item_for(path)
            if it is not None:
                return it
        raise RuntimeError("row not found")

    for v in values:
        results["single edit"].append(timed(app, lambda v=v: view.commit(row(), v)))
    state.work.discard([path])
    session.replace_checked(keys)
    wait(app, lambda: False, 0.5)
    for v in values:
        def bulk(v=v):
            state.work.set(path, v, state.scope())
            view.stage_edits()
        results["bulk edit (all agents)"].append(timed(app, bulk))
    print(f"platform: {app.platformName()} · agents: {len(session.agents)} · start + load + parse: {load_ms:.0f} ms "
          f"· pages: {len(pages)} · built in idle time afterwards: {warm_ms:.0f} ms")
    print(f"{'measure':26} {'n':>3} {'median':>8} {'p95':>8} {'max':>8} {'budget':>8}  verdict")
    ok_all = True
    for name, vals in results.items():
        ok = p95(vals) <= BUDGET[name]
        ok_all &= ok
        print(f"{name:26} {len(vals):3} {statistics.median(vals):8.1f} {p95(vals):8.1f} {max(vals):8.1f} "
              f"{BUDGET[name]:8} {'ok' if ok else 'OVER'}")
    session.drafts.discard()
    session.shutdown()
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
