"""Screenshots of the screens with the generated fixture (offscreen; the README uses some of them).

Works on temporary copies only.  Walks through: start, edit, stage, details, chain edit, auxiliary
task, Review, Write config, Customize, model picker, My defaults, Hosts and the dark theme.

Usage:  python bench/shots_gui.py [out_dir] [width] [height]
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pump(app, pred, timeout=20.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.01)
    return False


def setup(width=1440, height=900, tmp=None):
    tmp = Path(tmp or tempfile.mkdtemp(prefix="hm-shots-"))
    os.environ["HERMES_CONFIG_MANAGER_HOME"] = str(tmp / "home")
    data = tmp / "data"
    if not data.exists():
        shutil.copytree(ROOT / "bench" / "fixtures" / "generated-100-2hosts", data)
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from hermes_config_manager.gui.app import build
    from hermes_config_manager.services.session import Session
    from hermes_config_manager.transports.hosts import HostConfig

    session = Session(hosts=[HostConfig("studio", hermes_root=str(data / "hostA")),
                             HostConfig("lab", hermes_root=str(data / "hostB"))], use_cache=False)
    app, state, win = build(session)
    win.resize(width, height)
    win.show()
    win.stack.setFocus()
    state.start()
    pump(app, lambda: len(session.agents) >= 100 and not session.is_refreshing(), 60)
    pump(app, lambda: False, 0.5)
    return app, state, win


def shot(app, widget, path):
    pump(app, lambda: False, 0.3)
    widget.grab().save(str(path))
    print("saved", path)


def walkthrough(out: Path, w: int, h: int) -> None:
    import hermes_config_manager.gui.dialogs as D
    from hermes_config_manager.gui.hosts_dialog import HostsDialog
    from hermes_config_manager.gui.review import ReviewDialog, build_groups

    app, state, win = setup(w, h)
    s = state.session
    keys = s.ordered_keys()
    shot(app, win, out / f"01-start-{w}.png")

    win.agents.open_hosts.update({"studio", "lab"})
    win.agents._toggle_models()
    s.set_checked(keys[:12], True)
    pump(app, lambda: False, 0.6)
    shot(app, win, out / f"02-models-{w}.png")

    win.show_page("interface/display")
    pump(app, lambda: False, 0.4)
    view = win.current_page()
    table = view.tables[0]
    table.item_for(("display", "compact")).editor.sw.click()
    ed = table.item_for(("display", "resume_exchanges")).editor.ed
    ed.setText("25")
    ed.editingFinished.emit()
    pump(app, lambda: False, 0.3)
    shot(app, win, out / f"03-edited-{w}.png")
    view.stage_edits()
    pump(app, lambda: False, 0.5)
    shot(app, win, out / f"04-staged-{w}.png")
    row = view.tables[0].item_for(("display", "personality"))
    if row is not None:
        row.toggle_details()
        shot(app, win, out / f"05-details-{w}.png")

    real_pick = D.pick_model
    D.pick_model = lambda *a, **k: ("openrouter", "anthropic/claude-sonnet-4.5")
    try:
        win.show_page("models/Models")
        pump(app, lambda: False, 0.4)
        view = win.current_page()
        view.pick_model("delegation", 0, None)
        view.set_effort("delegation", 0, "high")
        shot(app, win, out / f"06-chain-edited-{w}.png")
        view.chain_stage("delegation")
        pump(app, lambda: False, 0.4)
    finally:
        D.pick_model = real_pick
    view.aux_select("vision")
    pump(app, lambda: False, 0.4)
    view.scroll.ensureWidgetVisible(view.aux, 0, 0)
    shot(app, win, out / f"07-auxiliary-{w}.png")

    dlg = ReviewDialog(state, win)
    groups = build_groups(state)
    if groups:
        dlg.open.add(groups[0].gkey)
    dlg.render()
    dlg.show()
    shot(app, dlg, out / f"08-review-{w}.png")
    dlg.restart.setChecked(False)
    dlg._write()
    pump(app, lambda: not dlg.running, 30)
    shot(app, dlg, out / f"09-written-{w}.png")
    dlg.close()

    win.menu.set_customizing(True)
    pump(app, lambda: False, 0.5)
    shot(app, win, out / f"10-customize-{w}.png")
    win.menu.set_customizing(False)

    picker = D.picker_for(win, state)
    picker.prepare("Head model · delegation", "", None)
    D.overlay_for(win).show_panel(picker)
    shot(app, win, out / f"11-picker-{w}.png")
    D.overlay_for(win).hide()

    state.defaults.add("Fast research", [["openrouter", "google/gemini-2.5-flash", "low"], ["zai", "glm-4.6", None]])
    chain = win.current_page().chains[0]
    popup = D.DefaultsPopup(state, win.current_page(), chain.key)
    popup.show_for(chain, chain)
    shot(app, popup.pop, out / f"12-my-defaults-{w}.png")
    popup.pop.close()

    hosts = HostsDialog(state, win)
    hosts.show()
    shot(app, hosts, out / f"13-hosts-{w}.png")
    hosts.done(0)

    win.toggle_theme()
    pump(app, lambda: False, 0.8)
    shot(app, win, out / f"14-dark-{w}.png")


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "docs" / "screenshots")
    width = int(sys.argv[2]) if len(sys.argv) > 2 else 1440
    height = int(sys.argv[3]) if len(sys.argv) > 3 else 900
    out.mkdir(parents=True, exist_ok=True)
    walkthrough(out, width, height)
