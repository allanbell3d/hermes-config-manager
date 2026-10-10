"""The screens, driven the way a user would (offscreen, temporary copies only)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hermes_config_manager.core.paths import MISSING
from hermes_config_manager.core.yamlpatch import fast_load
from hermes_config_manager.services import apply as A
from hermes_config_manager.services import chains as C
from hermes_config_manager.services.work import rows_of

from .conftest import PROFILES, pump


def make(qapp, session, width=1440, height=900):
    from hermes_config_manager.gui.app import build

    _app, state, win = build(session)
    win.resize(width, height)
    win.show()
    state.start()
    pump(qapp, lambda: not session.is_refreshing() and len(session.agents) == 4, 20)
    pump(qapp, lambda: False, 0.2)
    return state, win


@pytest.fixture()
def gui(qapp, hermes_home):
    from hermes_config_manager.services.session import Session
    from hermes_config_manager.transports.hosts import HostConfig

    session = Session(hosts=[HostConfig("local", hermes_root=str(hermes_home))], use_cache=False)
    state, win = make(qapp, session)
    yield state, win
    session.drafts.discard()
    win.close()
    session.shutdown()


def in_section(text: str, section: str, old: str, new: str) -> str:
    """Replace the first ``old`` inside the top-level ``section:`` block of a config text."""
    start = text.index(f"\n{section}:") + 1
    i = text.index(old, start)
    return text[:i] + new + text[i + len(old):]


def page_with(win, section):
    st = win.state
    win.show_page(st.layout.page_of(section).id)
    view = win.current_page()
    view.reveal(section, None)
    return view


def item(win, path):
    view = win.current_page()
    for t in view.tables:
        it = t.item_for(path)
        if it is not None:
            return it
    return None


def test_startup_checks_nothing_and_keeps_hosts_closed(qapp, gui):
    state, win = gui
    assert state.session.checked == set()
    assert "No agents selected" in win.scope_lbl.text()
    assert all(box.body.isHidden() for box in win.agents.boxes.values())
    assert win.review_btn.text() == "Review (0)"


def test_an_edit_survives_a_selection_change_and_stages_only_for_the_agents_checked_then(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:2], True)
    pump(qapp, lambda: False, 0.1)
    path = ("compression", "threshold")
    view = win.current_page()
    win.go_to("compression", path)
    view = win.current_page()
    it = item(win, path)
    assert it is not None
    view.commit(it, 0.61)
    assert state.work.value(path) == 0.61 and view.bar.isVisible()
    s.replace_checked(keys[2:])                               # the selection changes
    pump(qapp, lambda: False, 0.2)
    assert state.work.value(path) == 0.61                     # the edit stays
    assert item(win, path).line.mark == "edit"
    view.stage_edits()
    pump(qapp, lambda: False, 0.3)
    assert s.drafts.keys_with_changes() == keys[2:]
    assert not state.work.has(path) and not view.bar.isVisible()
    from hermes_config_manager.gui.review import build_groups

    groups = build_groups(state)
    assert len(groups) == 1 and len(groups[0].items) == 2 and groups[0].new_label == "0.61"
    pump(qapp, lambda: win.review_btn.text() == "Review (1)", 3)


def test_write_config_writes_verifies_and_keeps_comments(qapp, gui, hermes_home):
    from hermes_config_manager.gui.review import ReviewDialog

    state, win = gui
    s = state.session
    main = hermes_home / "config.yaml"
    main.write_text(main.read_text(encoding="utf-8") + "\n# my own note, keep me\n", encoding="utf-8")
    s.refresh_all()
    pump(qapp, lambda: not s.is_refreshing(), 10)
    s.set_checked(s.ordered_keys(), True)
    path = ("compression", "threshold")
    state.work.set(path, 0.42, state.scope())
    state.work.stage([path], state.scope())
    dlg = ReviewDialog(state, win)
    restart = dlg.restart
    restart.setChecked(False)
    dlg._write()
    assert pump(qapp, lambda: not dlg.running, 30)
    assert {r.status for r in dlg.results} == {A.WRITTEN}
    text = main.read_text(encoding="utf-8")
    assert "# my own note, keep me" in text and fast_load(text)["compression"]["threshold"] == 0.42
    assert list(main.parent.glob("config.yaml.bak_*"))
    assert not s.drafts.has_changes()
    dlg.close()


def test_a_file_changed_elsewhere_is_left_alone(qapp, gui):
    from hermes_config_manager.gui.review import ReviewDialog

    state, win = gui
    s = state.session
    s.set_checked(s.ordered_keys(), True)
    path = ("compression", "threshold")
    state.work.set(path, 0.33, state.scope())
    state.work.stage([path], state.scope())
    victim = s.record(s.ordered_keys()[1])
    f = Path(victim.path)
    edited = in_section(f.read_text(encoding="utf-8"), "compression", "threshold: ",
                        "threshold: 0.99  # edited elsewhere, was ")
    f.write_text(edited, encoding="utf-8")
    dlg = ReviewDialog(state, win)
    dlg.restart.setChecked(False)
    dlg._write()
    assert pump(qapp, lambda: not dlg.running, 30)
    by_key = {r.key: r for r in dlg.results}
    assert by_key[victim.key].status == A.SKIPPED
    assert f.read_text(encoding="utf-8") == edited
    assert s.drafts.has_changes(victim.key)
    assert sum(1 for r in by_key.values() if r.status == A.WRITTEN) == 3
    dlg.close()


def test_chain_stage_changes_the_head_and_keeps_each_agents_fallbacks(qapp, gui, monkeypatch):
    import hermes_config_manager.gui.dialogs as D

    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys, True)
    scope = state.scope()
    before = {k: rows_of(C.read_chain(scope, C.DELEGATION, k)) for k in keys}
    monkeypatch.setattr(D, "pick_model", lambda *a, **k: ("openrouter", "vendor/test-model"))
    view = page_with(win, "delegation")
    view.pick_model("delegation", 0, None)
    assert state.work.chain("delegation").pm
    view.chain_stage("delegation")
    for k in keys:
        after = rows_of(C.read_chain(state.scope(), C.DELEGATION, k))
        assert after[0][:2] == ["openrouter", "vendor/test-model"]
        assert after[1:] == before[k][1:]


def test_customize_hides_a_section_and_the_choice_is_remembered(qapp, gui):
    from hermes_config_manager.gui.state import AppState

    state, win = gui
    view = page_with(win, "display")
    sec = state.model.sections["display"]
    assert not state.section_hidden("display")
    view.toggle_section("display")
    assert state.section_hidden("display")
    other = AppState(state.session)
    other._data_settled()
    assert other.layout.section_hidden(sec, other.used)


def test_search_goes_to_a_setting_and_related_settings_can_hold_it(qapp, gui):
    state, win = gui
    path = ("terminal", "timeout")
    win.go_to("terminal", path)
    assert "terminal" in win.current_page().page().sections
    assert item(win, path) is not None
    pid = state.layout.page_of("model").id
    win.show_page(pid)
    win._pinned(pid, path)
    pump(qapp, lambda: False, 0.2)
    assert path in [tuple(p) for p in state.layout.pins[pid]]
    assert any(t.mode == "rel" and t.item_for(path) for t in win.current_page().tables)
    win.current_page().unpin(path)
    assert path not in [tuple(p) for p in state.layout.pins[pid]]


def test_hidden_settings_follow_the_loaded_files_and_can_be_shown(qapp, gui):
    state, win = gui
    node = state.model.nodes.get(("display", "skin"))
    if node is None or state.layout.mode != "mine":
        pytest.skip("fixture sets every setting")
    hidden_before = state.node_hidden(node)
    win.current_page().toggle_node(node)
    assert state.node_hidden(node) != hidden_before


def test_custom_section_is_editable_and_written_untouched_elsewhere(qapp, hermes_home):
    from hermes_config_manager.gui.review import ReviewDialog
    from hermes_config_manager.services.session import Session
    from hermes_config_manager.transports.hosts import HostConfig

    custom = "\n# hand-written section the schema does not know\nmy_voice_box:\n  voice: nova   # keep me\n  speed: 1.25\n"
    main = hermes_home / "config.yaml"
    other = hermes_home / "profiles" / PROFILES[2] / "config.yaml"
    for f in (main, other):
        f.write_text(f.read_text(encoding="utf-8") + custom, encoding="utf-8")
    session = Session(hosts=[HostConfig("local", hermes_root=str(hermes_home))], use_cache=False)
    state, win = make(qapp, session)
    try:
        assert "my_voice_box" in state.model.sections
        assert state.model.default_category("my_voice_box") == "custom"
        has = [k for k, r in session.agents.items() if "my_voice_box" in r.data]
        session.replace_checked(has)
        path = ("my_voice_box", "speed")
        state.work.set(path, 1.5, state.scope())
        state.work.stage([path], state.scope())
        dlg = ReviewDialog(state, win)
        dlg.restart.setChecked(False)
        dlg._write()
        assert pump(qapp, lambda: not dlg.running, 30)
        text = main.read_text(encoding="utf-8")
        assert "voice: nova   # keep me" in text and fast_load(text)["my_voice_box"]["speed"] == 1.5
        untouched = hermes_home / "profiles" / PROFILES[0] / "config.yaml"
        assert "my_voice_box" not in fast_load(untouched.read_text(encoding="utf-8"))
        dlg.close()
    finally:
        session.drafts.discard()
        win.close()
        session.shutdown()


def test_hosts_screen_saves_the_registry_and_loads_the_agents(qapp, hermes_home):
    from hermes_config_manager.gui.hosts_dialog import HostsDialog
    from hermes_config_manager.services.session import Session
    from hermes_config_manager.transports.hosts import load_registry

    session = Session(hosts=[], use_cache=False)
    state, win = make(qapp, session)
    try:
        assert not win.agents.empty.isHidden()
        dlg = HostsDialog(state, win)
        dlg._add()
        dlg.name.setText("lab")
        dlg.path.setText(str(hermes_home))
        dlg._commit()
        dlg.accept()
        assert pump(qapp, lambda: len(session.agents) == 4 and not session.is_refreshing(), 20)
        reg = load_registry()
        assert reg.source == "app" and [h.name for h in reg.hosts] == ["lab"]
        assert session.checked == set()
    finally:
        win.close()
        session.shutdown()


def test_everything_fits_at_half_a_1920_screen(qapp, hermes_home):
    from hermes_config_manager.services.session import Session
    from hermes_config_manager.transports.hosts import HostConfig

    session = Session(hosts=[HostConfig("local", hermes_root=str(hermes_home))], use_cache=False)
    state, win = make(qapp, session, 960, 1000)
    try:
        session.set_checked(session.ordered_keys(), True)
        pump(qapp, lambda: False, 0.4)
        for pid in ("models/Models", "interface/display", "agent/Context & memory"):
            if state.layout.page(pid) is None:
                continue
            win.show_page(pid)
            pump(qapp, lambda: False, 0.3)
            view = win.current_page()
            assert view.content.minimumSizeHint().width() <= view.scroll.viewport().width() + 2, pid
    finally:
        win.close()
        session.shutdown()


def test_theme_switch_rebuilds_the_window(qapp, gui):
    state, win = gui
    from hermes_config_manager.gui.theme import T

    dark = T.dark
    win.toggle_theme()
    pump(qapp, lambda: False, 0.2)
    assert T.dark != dark and win.current_page() is not None
    win.toggle_theme()


def test_remove_key_stages_a_removal_from_the_row(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:1], True)
    path = ("compression", "threshold")
    win.go_to("compression", path)
    it = item(win, path)
    from hermes_config_manager.services.work import REMOVE

    win.current_page().commit(it, REMOVE)
    win.current_page().stage_edits()
    assert s.drafts.draft(keys[0]).get(path) is MISSING


def test_staging_after_closing_review_and_switching_theme_is_safe(qapp, gui):
    from hermes_config_manager.gui.review import ReviewDialog

    state, win = gui
    s = state.session
    s.set_checked(s.ordered_keys()[:2], True)
    dlg = ReviewDialog(state, win)
    dlg.show()
    dlg.close()
    dlg.deleteLater()
    win.toggle_theme()                                   # rebuilds the agents pane and the search box
    pump(qapp, lambda: False, 0.2)
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    path = ("compression", "threshold")
    state.work.set(path, 0.44, state.scope())
    state.work.stage([path], state.scope())              # drafts_changed reaches only live widgets
    pump(qapp, lambda: False, 0.3)
    assert s.drafts.has_changes()
    win.toggle_theme()


def test_escape_closes_the_model_picker(qapp, gui):
    from PySide6.QtCore import QTimer

    from hermes_config_manager.gui import dialogs as D

    state, win = gui
    seen = {}

    def press_escape():
        seen["open"] = D.overlay_for(win).isVisible()
        win._escape()

    QTimer.singleShot(50, press_escape)
    result = D.pick_model(win.current_page(), state, "Head model · model", "", None)
    assert seen.get("open") and result is None and not D.overlay_for(win).isVisible()


def test_review_checks_the_files_before_writing_and_can_drop_one_agent(qapp, gui):
    from hermes_config_manager.gui.review import ReviewDialog, build_groups

    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys, True)
    path = ("compression", "threshold")
    state.work.set(path, 0.37, state.scope())
    state.work.stage([path], state.scope())
    victim = s.record(keys[1])
    f = Path(victim.path)
    f.write_text(in_section(f.read_text(encoding="utf-8"), "compression", "threshold: ", "threshold: 0.98  # elsewhere, was "),
                 encoding="utf-8")
    dlg = ReviewDialog(state, win)
    assert pump(qapp, lambda: not dlg.checking, 20)
    assert dlg._status_of(victim.key)[0] == "changed on the host"        # known before Write config
    assert dlg._status_of(keys[0])[0] == ""
    g = build_groups(state)[0]
    dlg._unstage_one(g, keys[2])
    assert not s.drafts.has_changes(keys[2]) and s.drafts.has_changes(keys[0])
    dlg.close()


def test_unused_model_reasoning_effort_can_be_staged_for_removal(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.record(keys[0]).data["model"]["reasoning_effort"] = "high"            # as many real configs still have
    s.set_checked(keys[:2], True)
    view = page_with(win, "model")
    note = view._legacy_effort_note()
    assert note is not None
    view._remove_legacy_effort([keys[0]])
    assert s.drafts.draft(keys[0]).get(("model", "reasoning_effort")) is MISSING


def test_crash_handler_writes_a_masked_log(qapp, tmp_path):
    import sys

    from hermes_config_manager.core import appdirs
    from hermes_config_manager.gui.app import install_crash_handler

    old = sys.excepthook
    install_crash_handler()
    try:
        try:
            raise RuntimeError("token sk-test-abcdefghijklmnopqrstuvwx leaked?")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = old
    log = (appdirs.log_dir() / "errors.log").read_text(encoding="utf-8")
    assert "RuntimeError" in log and "sk-test-abcdefghijklmnopqrstuvwx" not in log


def test_restart_gateways_is_off_every_time_review_opens(qapp, gui):
    from hermes_config_manager.gui.review import ReviewDialog

    state, win = gui
    first = ReviewDialog(state, win)
    assert not first.restart.isChecked()
    first.restart.setChecked(True)
    first.close()
    again = ReviewDialog(state, win)
    assert not again.restart.isChecked()
    again.close()


def test_a_renamed_profile_leaves_the_list(qapp, gui, hermes_home):
    state, win = gui
    s = state.session
    old = next(k for k in s.ordered_keys() if s.record(k).profile == PROFILES[2])
    s.set_checked([old], True)
    renamed = PROFILES[2] + " original"
    (hermes_home / "profiles" / PROFILES[2]).rename(hermes_home / "profiles" / renamed)
    s.refresh_host("local")
    pump(qapp, lambda: not s.is_refreshing() and any(r.profile == renamed for r in s.agents.values()), 20)
    assert old not in s.agents
    assert old not in s.checked
    assert len(s.agents) == 4


def test_stage_for_the_focused_agent_only(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:3], True)
    s.set_viewed(keys[1])
    pump(qapp, lambda: False, 0.1)
    path = ("compression", "threshold")
    win.go_to("compression", path)
    view = win.current_page()
    view.commit(item(win, path), 0.63)
    assert view.bar_one.isVisibleTo(view.bar) and state.ref_name() in view.bar_one.text()
    view.stage_edits(only=True)
    staged = {k for k in keys if s.drafts.draft(k) is not None and s.drafts.draft(k).ops}
    assert staged == {keys[1]}


def test_the_model_list_file_is_the_catalog_and_the_picker_groups_by_provider(qapp, gui):
    from hermes_config_manager.core import appdirs
    from hermes_config_manager.gui import dialogs as D

    state, win = gui
    (appdirs.config_dir() / "models.md").write_text(
        "# mine\n- `alpha: a-1`\n- `alpha: a-2`\n- `beta: b-1`\n", encoding="utf-8")
    pk = D.picker_for(win.current_page(), state)
    pk.prepare("Head model · model")
    pump(qapp, lambda: state.loader.file_path is not None, 10)
    pk.prepare("Head model · model")
    rows = pk.model.rows
    heads = [r[0] for r in rows if r[3] == "head"]
    assert "alpha" in heads and "beta" in heads
    assert ("alpha", "a-1") in [(r[0], r[1]) for r in rows]
    assert "3 models" in pk.list_lbl.text()
    pk.reject()


def _act(menu, start):
    return next(a for a in menu.actions() if a.text().startswith(start))


def test_right_click_stages_one_setting_and_discards_another(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:2], True)
    pump(qapp, lambda: False, 0.1)
    a, b = ("compression", "threshold"), ("compression", "enabled")
    win.go_to("compression", a)
    view = win.current_page()
    view.commit(item(win, a), 0.62)
    it_b = item(win, b)
    if it_b is not None:
        cur = state.shown_value(b)
        view.commit(it_b, not cur if isinstance(cur, bool) else True)
    m = item(win, a).context_menu()
    assert not _act(m, "Unstage").isEnabled()
    _act(m, "Stage this value for 2").trigger()
    pump(qapp, lambda: False, 0.1)
    assert all(s.drafts.draft(k).get(a) == 0.62 for k in keys[:2])
    if it_b is not None:
        assert state.work.dirty(b, state.scope())          # the other edit is still only on the page
        _act(item(win, b).context_menu(), "Discard my edit").trigger()
        assert not state.work.dirty(b, state.scope())
    m = item(win, a).context_menu()
    assert _act(m, "Unstage").isEnabled() and not _act(m, "Copy value").text() == ""
    _act(m, "Unstage").trigger()
    pump(qapp, lambda: False, 0.1)
    assert all(s.drafts.draft(k) is None or s.drafts.draft(k).get(a) != 0.62 for k in keys[:2])


def test_right_click_on_a_chain_stages_it_for_one_agent(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:3], True)
    pump(qapp, lambda: False, 0.1)
    view = page_with(win, "model")
    view.set_effort("primary", 0, "low")
    table = next(c for c in view.chains if c.key == "primary")
    m = table.context_menu()
    assert _act(m, "Save as My default").isEnabled()
    _act(m, f"Stage this chain for {state.ref_name()} only").trigger()
    pump(qapp, lambda: False, 0.1)
    staged = {k for k in keys if s.drafts.draft(k) is not None and s.drafts.draft(k).ops}
    assert staged == {state.ref_key()}


def test_a_change_made_on_the_host_after_staging_is_never_overwritten(qapp, gui):
    from hermes_config_manager.services import apply as A
    from hermes_config_manager.services import review as R

    state, win = gui
    s = state.session
    key = s.ordered_keys()[0]
    rec = s.record(key)
    s.set_checked([key], True)
    pump(qapp, lambda: False, 0.1)
    path = ("compression", "threshold")
    win.go_to("compression", path)
    view = win.current_page()
    view.commit(item(win, path), 0.61)
    view.stage_edits()
    # someone changes the same setting on the host, then the agent is refreshed
    f = Path(rec.path)
    text = f.read_text(encoding="utf-8")
    old = s.drafts.draft(key).base["compression"]["threshold"]
    assert f"threshold: {old}" in text
    f.write_text(text.replace(f"threshold: {old}", "threshold: 0.77", 1), encoding="utf-8")
    s.refresh_agent(key)
    pump(qapp, lambda: s.record(key).data["compression"]["threshold"] == 0.77, 10)
    flow = R.ApplyFlow(s, state.schema)
    got = []
    flow.prepared.connect(got.append)
    flow.prepare([key])
    pump(qapp, lambda: bool(got), 10)
    assert got[0][0].status == A.CONFLICT and "Changed on the host" in got[0][0].message
    # staging it again (now knowing the new value) clears the conflict
    view.commit(item(win, path), 0.62)
    view.stage_edits()
    got.clear()
    flow.prepare([key])
    pump(qapp, lambda: bool(got), 10)
    assert got[0][0].status == A.READY


def test_staging_a_value_the_agent_already_has_keeps_the_edit_and_says_so(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:2], True)
    pump(qapp, lambda: False, 0.1)
    path = ("compression", "threshold")
    win.go_to("compression", path)
    view = win.current_page()
    ref = state.ref_key()
    other = next(k for k in keys[:2] if k != ref)
    s.record(ref).data["compression"]["threshold"] = 0.5
    s.record(other).data["compression"]["threshold"] = 0.7     # the checked agents differ
    view.commit(item(win, path), 0.5)                          # what the shown agent already has
    assert state.work.dirty(path, state.scope())
    said = []
    state.say = said.append
    view.stage_edits(only=True)
    assert state.work.dirty(path, state.scope()) and said and said[-1].startswith("Nothing staged")


def test_an_empty_yaml_block_is_never_staged_as_null(qapp, gui):
    from hermes_config_manager.gui.dialogs import YamlDialog

    state, win = gui
    s = state.session
    s.set_checked(s.ordered_keys()[:1], True)
    pump(qapp, lambda: False, 0.1)
    node = next(n for n in state.model.nodes.values() if n.kind == "block")
    dlg = YamlDialog(state, node, None, win)
    dlg._ok()                                # unchanged text: no edit
    assert dlg.result() == 0 and dlg.value is None
    dlg = YamlDialog(state, node, {"a": 1}, win)
    dlg.ed.setPlainText("# only a comment\n")
    dlg._ok()
    assert not dlg.err.isHidden() and dlg.result() == 0


def test_a_file_that_already_has_the_staged_values_is_done_not_left_staged(qapp, gui):
    from hermes_config_manager.services import apply as A
    from hermes_config_manager.services import review as R

    state, win = gui
    s = state.session
    key = s.ordered_keys()[0]
    rec = s.record(key)
    s.set_checked([key], True)
    pump(qapp, lambda: False, 0.1)
    path = ("compression", "threshold")
    win.go_to("compression", path)
    view = win.current_page()
    view.commit(item(win, path), 0.66)
    view.stage_edits()
    f = Path(rec.path)
    old = s.drafts.draft(key).base["compression"]["threshold"]
    f.write_text(f.read_text(encoding="utf-8").replace(f"threshold: {old}", "threshold: 0.66", 1), encoding="utf-8")
    flow = R.ApplyFlow(s, state.schema)
    prepared, results = [], []
    flow.prepared.connect(prepared.append)
    flow.finished.connect(results.append)
    flow.prepare([key])
    pump(qapp, lambda: bool(prepared), 10)
    assert prepared[0][0].status == A.NOOP
    flow.apply(prepared[0])
    pump(qapp, lambda: bool(results), 10)
    assert results[0][0].status == A.UP_TO_DATE
    assert not s.drafts.has_changes(key)


def test_removing_a_host_drops_its_staged_changes(qapp, gui):
    state, win = gui
    s = state.session
    key = s.ordered_keys()[0]
    s.set_checked([key], True)
    pump(qapp, lambda: False, 0.1)
    path = ("compression", "threshold")
    win.go_to("compression", path)
    view = win.current_page()
    view.commit(item(win, path), 0.67)
    view.stage_edits()
    assert s.drafts.has_changes(key)
    s.replace_hosts([])
    assert not s.drafts.keys_with_changes()


def test_the_picker_takes_a_typed_provider_and_model(qapp, gui):
    from hermes_config_manager.gui import dialogs as D

    state, win = gui
    pk = D.picker_for(win.current_page(), state)
    pk.prepare("Add a fallback · model")
    pk.q.setText("someprov: some-model-x")
    first = pk.model.rows[0]
    assert (first[0], first[1], first[3]) == ("someprov", "some-model-x", True)
    pk.q.setText("only-an-id-zz")
    assert pk.model.rows[0][3] == "head"           # a hint: type provider: model
    pk.reject()


def test_the_page_follows_the_clicked_agent_else_the_first_one_checked(qapp, gui):
    state, win = gui
    s = state.session
    k = s.ordered_keys()
    s.set_viewed(k[3])                      # clicked, but not checked
    s.set_checked([k[2]], True)
    s.set_checked([k[0]], True)             # first in the list, but checked second
    assert state.ref_key() == k[2]
    s.set_checked([k[3]], True)             # the clicked agent is now checked: it leads
    assert state.ref_key() == k[3]
    s.set_checked([k[3], k[2]], False)
    assert state.ref_key() == k[0]


def test_a_value_cell_tooltip_describes_that_setting_and_these_agents(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:3], True)
    pump(qapp, lambda: False, 0.1)
    path = ("compression", "threshold")
    win.go_to("compression", path)
    view = win.current_page()
    it = item(win, path)
    s.record(keys[1]).data["compression"]["threshold"] = 0.91          # make the agents differ
    tip = it.value_tip()
    assert "<b>compression › threshold</b>" in tip and state.ref_name() in tip
    assert "Hermes default" in tip and "checked agents differ" in tip and "0.91 ×1" in tip
    view.commit(it, 0.42)
    tip = item(win, path).value_tip()
    assert "Your edit: 0.42" in tip and "not staged" in tip


def test_a_chain_cell_tooltip_names_its_value_and_who_differs(qapp, gui):
    state, win = gui
    s = state.session
    s.set_checked(s.ordered_keys(), True)
    pump(qapp, lambda: False, 0.1)
    view = page_with(win, "model")
    table = next(c for c in view.chains if c.key == "primary")
    rows = table.shown()
    tip = table._cell_tip(0, 1, rows[0], False, table._per_position(rows))
    assert "model · Head · Model" in tip and rows[0][1] in tip and state.ref_name() in tip


def test_default_on_a_chain_cell_sets_provider_auto_and_clears_the_effort(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:1], True)
    pump(qapp, lambda: False, 0.1)
    view = page_with(win, "delegation")
    table = next(c for c in view.chains if c.key == "delegation")
    view.set_effort("delegation", 0, "high")
    table = next(c for c in view.chains if c.key == "delegation")
    _act(table.context_menu((0, 2)), "Default").trigger()
    assert state.work.chain("delegation").rows[0][:2] == ["auto", ""]
    table = next(c for c in view.chains if c.key == "delegation")
    _act(table.context_menu((0, 3)), "Default").trigger()
    assert state.work.chain("delegation").rows[0][2] is None
    view.chain_stage("delegation")
    d = s.drafts.draft(keys[0])
    assert d.get(("delegation", "provider")) == "auto" and d.get(("delegation", "model")) == ""
    assert d.get(("delegation", "reasoning_effort")) is MISSING


def test_the_model_picker_default_button_gives_auto(qapp, gui):
    from PySide6.QtCore import QTimer

    from hermes_config_manager.gui import dialogs as D

    state, win = gui
    page = win.current_page()
    QTimer.singleShot(50, lambda: D.picker_for(page, state).default_btn.click())
    assert D.pick_model(page, state, "Head model · model", "", None) == ("auto", "")


def test_right_click_on_auxiliary_tasks_sets_them_to_default(qapp, gui):
    state, win = gui
    s = state.session
    keys = s.ordered_keys()
    s.set_checked(keys[:1], True)
    pump(qapp, lambda: False, 0.1)
    view = page_with(win, "auxiliary")
    a, b, c = state.model.tasks[:3]
    for t in (a, b, c):
        view.set_model(f"aux:{t}", 0, ("openrouter", "vendor/test-model"))
        view.set_effort(f"aux:{t}", 0, "high")
    # one task
    _act(view.aux.row_menu(a), "Default (provider auto").trigger()
    assert state.work.chain(f"aux:{a}").rows[0][:2] == ["auto", ""]
    assert state.work.chain(f"aux:{b}").rows[0][:2] == ["openrouter", "vendor/test-model"]
    # a checked task: every checked task
    view.aux_checked = {b, c}
    m = view.aux.row_menu(b)
    assert "2 checked tasks" in _act(m, "Default (provider auto").text()
    _act(m, "Default (no reasoning effort").trigger()
    assert all(state.work.chain(f"aux:{t}").rows[0][2] is None for t in (b, c))
    view.aux_default([b, c], "pm")
    assert all(state.work.chain(f"aux:{t}").rows[0][:2] == ["auto", ""] for t in (b, c))
    view.aux_stage([a, b, c])
    d = s.drafts.draft(keys[0])
    for t in (a, b, c):
        assert d.get(("auxiliary", t, "provider")) == "auto" and d.get(("auxiliary", t, "model")) == ""


def test_a_misspelled_discord_key_is_flagged_and_moved_through_review(qapp, hermes_home):
    from hermes_config_manager.gui.review import ReviewDialog
    from hermes_config_manager.services.session import Session
    from hermes_config_manager.transports.hosts import HostConfig

    lines = {PROFILES[0]: ["discord_allow_bots: mentions"],
             PROFILES[1]: ["discord_allow_bots: all", "allow_bots: all"],
             PROFILES[2]: ["discord_allow_bots: all", "allow_bots: none"]}        # this one keeps its own value
    files = {}
    for name, extra in lines.items():
        f = hermes_home / "profiles" / name / "config.yaml"
        text = f.read_text(encoding="utf-8")
        start = text.index("\ndiscord:\n") + len("\ndiscord:\n")
        indent = text[start:start + len(text[start:]) - len(text[start:].lstrip(" "))]   # the file's own indent
        f.write_text(text[:start] + "".join(f"{indent}{line}\n" for line in extra) + text[start:], encoding="utf-8")
        files[name] = f
    session = Session(hosts=[HostConfig("local", hermes_root=str(hermes_home))], use_cache=False)
    state, win = make(qapp, session)
    try:
        session.set_checked(session.ordered_keys(), True)
        typo, meant = ("discord", "discord_allow_bots"), ("discord", "allow_bots")
        assert state.model.nodes[typo].suggest == meant
        view = page_with(win, "discord")
        notes = view._misnamed_notes(state.model.sections["discord"])
        assert len(notes) == 1
        said = []
        state.toast.connect(said.append)
        view._stage_move(typo, meant)
        assert "staged for 2 agents" in said[-1] and "Left alone" in said[-1]
        from hermes_config_manager.gui.review import build_groups, diff_text

        groups = {(g.path, g.new_label): g for g in build_groups(state) if g.kind == "f"}
        removed = groups[(typo, "key removed")]
        assert len(removed.items) == 2
        moved = next(g for (p, _), g in groups.items() if p == meant)
        assert len(moved.items) == 1 and moved.new_label == "mentions"
        assert ("+", "allow_bots: mentions") in [(t, line.strip()) for t, line in diff_text(state, moved, moved.items[0])]
        dlg = ReviewDialog(state, win)
        dlg.restart.setChecked(False)
        dlg._write()
        assert pump(qapp, lambda: not dlg.running, 30)
        after = {name: fast_load(f.read_text(encoding="utf-8"))["discord"] for name, f in files.items()}
        assert after[PROFILES[0]].get("allow_bots") == "mentions" and "discord_allow_bots" not in after[PROFILES[0]]
        assert after[PROFILES[1]].get("allow_bots") == "all" and "discord_allow_bots" not in after[PROFILES[1]]
        assert after[PROFILES[2]]["discord_allow_bots"] == "all" and after[PROFILES[2]]["allow_bots"] == "none"
        main = fast_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
        assert "allow_bots" not in (main.get("discord") or {})
        dlg.close()
    finally:
        session.drafts.discard()
        win.close()
        session.shutdown()
