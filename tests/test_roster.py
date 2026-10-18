"""hermes-teammates rosters: the plugin's rules, writing, the session's documents and staging (no screens).

Teammate names, routes and models here are generated test values.
"""

from __future__ import annotations

import copy

import pytest

from hermes_config_manager.core import roster_rules as R


def roster(**over):
    data = {"teammates": {
        "drafter": {"description": "Writes first drafts.", "route": "test-route-a", "model": "test-model-small",
                    "reasoning_effort": "low"},
        "checker": {"description": "Checks the drafts.", "toolsets": ["file", "web"],
                    "instructions": "Read the draft.\nReport problems."},
    }}
    data.update(over)
    return data


# ------------------------------------------------------------------ the plugin's rules
def test_a_valid_roster_has_no_problems():
    got = R.check(roster())
    assert got.problem is None and got.invalid == {} and got.warnings == []


@pytest.mark.parametrize("change, who, word", [
    (lambda t: t.update({"Upper": {"description": "x"}}), "Upper", "lowercase"),
    (lambda t: t.update({"1st": {"description": "x"}}), "1st", "lowercase"),
    (lambda t: t.update({"x" * 33: {"description": "x"}}), "x" * 33, "lowercase"),
    (lambda t: t.update({"yes": {"description": "x"}}), "yes", "boolean"),
    (lambda t: t["drafter"].update(colour="red"), "drafter", "colour"),
    (lambda t: t["drafter"].update(description=5), "drafter", "description"),
    (lambda t: t["drafter"].update(model="m" * 101), "drafter", "model"),
    (lambda t: t["drafter"].update(route=["a"]), "drafter", "route"),
    (lambda t: t["drafter"].update(toolsets=[]), "drafter", "toolsets"),
    (lambda t: t["drafter"].update(toolsets=["two words"]), "drafter", "toolsets"),
    (lambda t: t["drafter"].update(toolsets=[f"t{i}" for i in range(25)]), "drafter", "toolsets"),
    (lambda t: t.update(drafter="not a mapping"), "drafter", "mapping"),
])
def test_each_rule_marks_only_that_teammate(change, who, word):
    data = copy.deepcopy(roster())
    change(data["teammates"])
    got = R.check(data)
    assert got.problem is None and list(got.invalid) == [who] and word in " ".join(got.invalid[who])


def test_empty_labels_count_as_unset_and_long_texts_are_clipped_with_a_warning():
    data = roster()
    data["teammates"]["drafter"].update(route="", model="  ", reasoning_effort="")
    data["teammates"]["checker"]["description"] = "d" * 301
    got = R.check(data)
    assert got.invalid == {} and any("checker" in w and "300" in w for w in got.warnings)


def test_a_file_without_a_teammates_mapping_is_no_roster_and_unknown_top_keys_only_warn():
    assert R.check([1, 2]).problem and R.check({"teammates": [1]}).problem and R.check({"routes": {}}).problem
    got = R.check(roster(extra=1))
    assert got.problem is None and got.invalid == {} and "extra" in got.warnings[0]


def test_review_blocks_a_write_that_makes_a_teammate_unusable_but_not_one_already_broken():
    current = roster()
    current["teammates"]["legacy"] = {"description": "x", "colour": "red"}       # broken on the host already
    target = copy.deepcopy(current)
    target["teammates"]["drafter"]["model"] = "test-model-big"
    why, warn = R.review(target, current)
    assert why is None and any("legacy" in w for w in warn)
    target["teammates"]["checker"]["toolsets"] = []
    why, _warn = R.review(target, current)
    assert why and "checker" in why and "legacy" not in why
    assert R.review({"teammates": "x"}, current)[0]
    assert R.review(roster(), None) == (None, [])                     # a new file


def test_teammate_names():
    assert R.bad_name("drafter") is None and R.bad_name("code-2_x") is None
    for name in ("", "Drafter", "1x", "two words", "a:b", "-x", "no", "x" * 33, 5):
        assert R.bad_name(name), name


# ------------------------------------------------------------------ writing a roster file
ROSTER_YAML = """# master roster (test)
teammates:
  drafter:                 # cheap
    description: Writes first drafts.
    route: test-route-a
    model: test-model-small
    reasoning_effort: low
  checker:
    description: Checks the drafts.
    toolsets: [file, web]
"""
PATH = "/h/teammates.yaml"


def _mock():
    from hermes_config_manager.transports.hosts import HostConfig
    from hermes_config_manager.transports.mock import MockTransport

    t = MockTransport(HostConfig("mock", hermes_root="/h"))
    t.fs.put(PATH, ROSTER_YAML)
    return t


def _snap(t, ops, path=PATH, validate=True):
    from hermes_config_manager.core.yamlpatch import fast_load
    from hermes_config_manager.services import apply as A
    from hermes_config_manager.transports.base import TransportError

    try:
        raw = t.read_bytes(path)
        base, sha = fast_load(raw.data.decode()), raw.revision.sha256
    except TransportError:
        base, sha = {}, ""
    return A.DraftSnapshot("mock::@main#teammates", "mock", "mock / main · teammates roster", path, base, sha, ops,
                           validate=R.review if validate else None)


def _write(t, ops, path=PATH):
    from hermes_config_manager.services import apply as A

    prepared = A.prepare_change(None, t, _snap(t, ops, path))
    return prepared, (A.apply_change(None, t, prepared) if prepared.writable else None)


def OWN_PATH(t):
    path = "/h/profiles/p1/teammates.yaml"
    if path not in t.fs.files:
        t.fs.put(path, ROSTER_YAML)
    return path


def test_a_rename_is_written_in_place_with_its_comment():
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.services import apply as A

    t = _mock()
    prepared, result = _write(t, [EditOp(OpKind.RENAME_KEY, ("teammates", "drafter"), "writer"),
                                  EditOp(OpKind.SET, ("teammates", "writer", "model"), "test-model-small-2")])
    assert prepared.status == A.READY and result.status == A.WRITTEN
    text = t.fs.files[PATH].decode()
    line = next(x for x in ROSTER_YAML.splitlines() if "# cheap" in x).replace("drafter", "writer")
    assert line + "\n" in text and "model: test-model-small-2" in text
    assert text.index("writer:") < text.index("checker:")


def test_a_write_that_makes_a_teammate_unusable_is_never_made_and_names_it():
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.services import apply as A

    t = _mock()
    prepared, result = _write(t, [EditOp(OpKind.SET, ("teammates", "checker", "toolsets"), [])])
    assert prepared.status == A.INVALID and "checker" in prepared.message and result is None
    assert t.fs.files[PATH].decode() == ROSTER_YAML


def test_a_teammate_already_broken_on_the_host_is_a_warning_not_a_block():
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.services import apply as A

    t = _mock()
    t.fs.put(PATH, ROSTER_YAML + "  legacy:\n    description: Old.\n    colour: red\n")
    prepared, result = _write(t, [EditOp(OpKind.SET, ("teammates", "drafter", "model"), "test-model-big")])
    assert prepared.status == A.READY and result.status == A.WRITTEN
    assert any("legacy" in w for w in prepared.warnings)


def test_replace_creates_a_missing_file_and_replaces_an_existing_one():
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.core.yamlpatch import fast_load
    from hermes_config_manager.services import apply as A

    t = _mock()
    own = "/h/profiles/p1/teammates.yaml"
    op = EditOp(OpKind.REPLACE_FILE, (), fast_load(ROSTER_YAML), origin="replace:master", text=ROSTER_YAML)
    prepared, result = _write(t, [op], own)
    assert prepared.status == A.READY and result.status == A.WRITTEN and result.backup_path is None
    assert t.fs.files[own].decode() == ROSTER_YAML
    other = ROSTER_YAML.replace("test-model-small", "test-model-other")
    op2 = EditOp(OpKind.REPLACE_FILE, (), fast_load(other), text=other)
    prepared, result = _write(t, [op2, EditOp(OpKind.SET, ("teammates", "checker", "description"), "New.")], own)
    assert result.status == A.WRITTEN and result.backup_path
    assert t.fs.files[own].decode() == other.replace("description: Checks the drafts.", "description: New.")


def test_removing_a_file_keeps_it_as_a_renamed_backup():
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.services import apply as A

    t = _mock()
    own = OWN_PATH(t)
    prepared, result = _write(t, [EditOp(OpKind.DELETE_FILE, ())], own)
    assert prepared.status == A.READY and result.status == A.WRITTEN
    assert own not in t.fs.files
    kept = [p for p in t.fs.files if p.startswith(own + ".removed-")]
    assert len(kept) == 1 and t.fs.files[kept[0]].decode() == ROSTER_YAML and result.backup_path == kept[0]


def test_a_file_changed_on_the_host_after_staging_is_not_replaced_or_removed():
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.services import apply as A

    t = _mock()
    snap = _snap(t, [EditOp(OpKind.DELETE_FILE, ())])
    t.fs.put(PATH, ROSTER_YAML.replace("reasoning_effort: low", "reasoning_effort: high"))
    prepared = A.prepare_change(None, t, snap)
    assert prepared.status == A.CONFLICT and PATH in t.fs.files


def test_local_rename_refuses_to_overwrite(tmp_path):
    from hermes_config_manager.transports.base import TransportError
    from hermes_config_manager.transports.hosts import HostConfig
    from hermes_config_manager.transports.local import LocalTransport

    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    a.write_text("x: 1\n")
    b.write_text("y: 2\n")
    t = LocalTransport(HostConfig("local", hermes_root=str(tmp_path)))
    with pytest.raises(TransportError):
        t.rename(str(a), str(b))
    t.rename(str(a), str(tmp_path / "c.yaml"))
    assert not a.exists() and (tmp_path / "c.yaml").read_text() == "x: 1\n"


def test_the_master_is_never_replaced_or_removed_by_the_write_path():
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.core.yamlpatch import fast_load
    from hermes_config_manager.services import apply as A

    t = _mock()
    for op in (EditOp(OpKind.DELETE_FILE, ()), EditOp(OpKind.REPLACE_FILE, (), fast_load(ROSTER_YAML),
                                                     text=ROSTER_YAML.replace("# master", "# other"))):
        snap = _snap(t, [op])
        snap.whole_file_allowed = False
        prepared = A.prepare_change(None, t, snap)
        assert prepared.status == A.INVALID and not prepared.writable
    assert t.fs.files[PATH].decode() == ROSTER_YAML


def test_replace_with_the_same_teammates_but_other_comments_is_still_written():
    from hermes_config_manager.core.edits import AgentDraft, EditOp, OpKind
    from hermes_config_manager.core.yamlpatch import fast_load
    from hermes_config_manager.services import apply as A

    other = ROSTER_YAML.replace("# master roster (test)", "# a different header")
    draft = AgentDraft("k#teammates", fast_load(ROSTER_YAML), "sha")
    draft.add(EditOp(OpKind.REPLACE_FILE, (), fast_load(other), text=other))
    assert draft.ops, "same data, other text: not a no-op"
    t = _mock()
    path = OWN_PATH(t)
    prepared, _result = _write(t, [EditOp(OpKind.REPLACE_FILE, (), fast_load(other), text=other)], path)
    assert prepared.status == A.READY and t.fs.files[path].decode() == other
    prepared, _ = _write(t, [EditOp(OpKind.REPLACE_FILE, (), fast_load(other), text=other)], path)
    assert prepared.status == A.NOOP


def test_a_rename_whose_new_name_appeared_on_the_host_is_a_conflict():
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.services import apply as A

    t = _mock()
    snap = _snap(t, [EditOp(OpKind.RENAME_KEY, ("teammates", "drafter"), "writer")], path=OWN_PATH(t))
    theirs = "  writer:\n    description: Theirs.\n  checker:"
    t.fs.put(OWN_PATH(t), ROSTER_YAML.replace("  checker:", theirs))
    assert A.prepare_change(None, t, snap).status == A.CONFLICT


# ------------------------------------------------------------------ settings in config.yaml
def test_roster_settings_paths_and_sources():
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.transports.hosts import HostConfig
    from hermes_config_manager.transports.mock import MockTransport

    t = MockTransport(HostConfig("mock", hermes_root="/h"))
    cfg = {"plugins": {"entries": {"hermes-teammates": {"settings": {"roster_file": " rosters/t.yaml ",
                                                                      "roster_source": "fallback"}}}}}
    s = RM.settings(cfg)
    assert RM.roster_file(s) == "rosters/t.yaml" and RM.source(s) == "fallback"
    assert RM.roster_path(t, "/h/profiles/p", s) == "/h/profiles/p/rosters/t.yaml"
    assert RM.roster_path(t, "/h", None) == "/h/teammates.yaml" and RM.settings({"plugins": {}}) is None
    assert RM.source({"roster_source": "everyone"}) == "own"           # anything else counts as own
    assert RM.roster_file({"roster_file": 3}) == ""
    for rel in ("/abs/t.yaml", "~/t.yaml", "C:/t.yaml", "../t.yaml", "a/../../t.yaml"):
        assert RM.outside(rel), rel
        assert RM.roster_path(t, "/h/profiles/p", {"roster_file": rel}) == "/h/profiles/p/teammates.yaml"
    assert RM.outside("sub/../t.yaml") is None


# ------------------------------------------------------------------ rosters in the session
ALPHA_YAML = ROSTER_YAML.replace("# master roster (test)", "# alpha's own roster (test)").replace(
    "test-model-small", "test-model-alpha")


def set_plugin(config_path, **settings):
    """Give a generated config.yaml the plugin's settings (inserted under its existing ``plugins:`` key)."""
    text = config_path.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    i = lines.index("plugins:")
    sp = " " * (len(lines[i + 1]) - len(lines[i + 1].lstrip(" ")))
    block = [f"{sp}entries:", f"{sp * 2}hermes-teammates:", f"{sp * 3}settings:"]
    block += [f"{sp * 4}{k}: {v}" for k, v in settings.items()]
    lines[i + 1:i + 1] = block
    config_path.write_bytes(nl.join(lines).encode("utf-8"))


def add_teammates(root):
    """Generated rosters: the main agent's file (master); alpha its own file; beta and gamma fall back to the
    master (beta's roster_file is set but has no file; gamma has no roster_file)."""
    set_plugin(root / "config.yaml", roster_file="teammates.yaml", max_live_runs=6)
    (root / "teammates.yaml").write_bytes(ROSTER_YAML.encode("utf-8"))
    set_plugin(root / "profiles" / "alpha" / "config.yaml", roster_source="fallback", roster_file="teammates.yaml")
    (root / "profiles" / "alpha" / "teammates.yaml").write_bytes(ALPHA_YAML.encode("utf-8"))
    set_plugin(root / "profiles" / "beta" / "config.yaml", roster_source="fallback", roster_file="teammates.yaml")
    set_plugin(root / "profiles" / "gamma" / "config.yaml", roster_source="fallback")
    return root


@pytest.fixture()
def tsession(qapp, hermes_home):
    from hermes_config_manager.services.session import Session
    from hermes_config_manager.transports.hosts import HostConfig

    from .conftest import pump

    add_teammates(hermes_home)
    s = Session(hosts=[HostConfig("local", hermes_root=str(hermes_home))], use_cache=False)
    s.reasoning_levels = ["low", "medium", "high"]
    s.refresh_all()
    assert pump(qapp, lambda: len(s.agents) == 4 and not s.is_refreshing() and len(s.docs) == 2)
    yield s
    s.drafts.discard()
    s.shutdown()


def _keys(s):
    return s.ordered_keys()          # main, alpha, beta, gamma


def _settings(s, agent):
    return s.agents[agent].data["plugins"]["entries"]["hermes-teammates"]["settings"]


def test_the_session_reads_the_master_and_each_profiles_own_roster(tsession):
    from hermes_config_manager.services import roster as RM

    s = tsession
    main, alpha, beta, gamma = _keys(s)
    assert s.docs[RM.doc_key(main)].is_master
    assert s.docs[RM.doc_key(main)].data["teammates"]["drafter"]["model"] == "test-model-small"
    assert s.docs[RM.doc_key(alpha)].data["teammates"]["drafter"]["model"] == "test-model-alpha"
    assert RM.doc_key(beta) not in s.docs and RM.doc_key(gamma) not in s.docs
    stand_in = s.record(RM.doc_key(gamma))
    assert stand_in is not None and stand_in.data is None
    assert stand_in.path.replace("\\", "/").endswith("profiles/gamma/teammates.yaml")
    assert s.base_of(RM.doc_key(gamma)) == ({}, "")


def test_the_session_follows_a_roster_file_elsewhere_in_the_folder(qapp, tsession, hermes_home):
    from hermes_config_manager.services import roster as RM

    from .conftest import pump

    s = tsession
    alpha = _keys(s)[1]
    cfg = hermes_home / "profiles" / "alpha" / "config.yaml"
    cfg.write_bytes(cfg.read_bytes().replace(b"roster_file: teammates.yaml", b"roster_file: rosters/team.yaml"))
    (hermes_home / "profiles" / "alpha" / "rosters").mkdir()
    (hermes_home / "profiles" / "alpha" / "rosters" / "team.yaml").write_bytes(
        ROSTER_YAML.replace("test-model-small", "test-model-team").encode())
    s.refresh_all()
    assert pump(qapp, lambda: not s.is_refreshing() and
                s.roster_paths[alpha].replace("\\", "/").endswith("rosters/team.yaml"))
    assert pump(qapp, lambda: not s.is_refreshing())
    assert s.docs[RM.doc_key(alpha)].data["teammates"]["drafter"]["model"] == "test-model-team"


def test_each_agent_shows_the_roster_it_uses(tsession):
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main, alpha, beta, gamma = _keys(s)
    assert E.table_for(s, main).kind == E.MASTER and E.table_for(s, main).editable
    assert E.table_for(s, alpha).kind == E.OWN and E.table_for(s, alpha).editable
    for k in (beta, gamma):                                    # fallback without an own roster
        v = E.table_for(s, k)
        assert v.kind == E.INHERITED and not v.editable and v.teammates["drafter"]["model"] == "test-model-small"
    assert E.users_of_master(s, "local") == [beta, gamma]
    _settings(s, alpha)["roster_source"] = "root"               # root: its own file is never read
    assert E.table_for(s, alpha).kind == E.INHERITED
    _settings(s, alpha)["roster_source"] = "own"
    s.docs[E.RM.doc_key(alpha)].data["teammates"] = {}
    assert E.table_for(s, alpha).kind == E.OWN and not E.table_for(s, alpha).falls_back
    _settings(s, alpha)["roster_source"] = "fallback"
    assert E.table_for(s, alpha).falls_back                    # empty own file: the master is used meanwhile
    _settings(s, beta)["roster_source"] = "own"                 # own, roster_file set, no file: no teammates
    assert E.table_for(s, beta).kind == E.BROKEN
    _settings(s, gamma).update(roster_source="own", teammates={"inline-one": {"description": "x"}})
    v = E.table_for(s, gamma)
    assert v.kind == E.CONFIG and not v.editable and list(v.teammates) == ["inline-one"]
    _settings(s, gamma)["roster_file"] = "/abs/teammates.yaml"
    assert E.table_for(s, gamma).kind == E.OUTSIDE


def test_a_roster_edit_goes_through_review_and_is_written(qapp, tsession, hermes_home):
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.services import apply as A
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services.review import ApplyFlow

    from .conftest import pump

    s = tsession
    alpha = _keys(s)[1]
    key = RM.doc_key(alpha)
    base, sha = s.base_of(key)
    s.drafts.apply({key: (base, sha)}, EditOp(OpKind.SET, ("teammates", "checker", "model"), "test-model-new"))
    s.drafts.apply({key: (base, sha)}, EditOp(OpKind.SET, ("teammates", "checker", "route"), "test-route-b"))
    flow = ApplyFlow(s)
    got = {}
    flow.prepared.connect(lambda p: got.setdefault("prepared", p))
    flow.prepare([key])
    assert pump(qapp, lambda: "prepared" in got)
    assert got["prepared"][0].status == A.READY
    flow.finished.connect(lambda r: got.setdefault("done", r))
    flow.apply(got["prepared"])
    assert pump(qapp, lambda: "done" in got)
    assert got["done"][0].status == A.WRITTEN
    text = (hermes_home / "profiles" / "alpha" / "teammates.yaml").read_text()
    assert "model: test-model-new" in text and text.startswith("# alpha's own roster (test)")
    assert s.docs[key].data["teammates"]["checker"]["model"] == "test-model-new" and not s.drafts.has_changes(key)


# ------------------------------------------------------------------ the working copy and staging
def test_a_cell_edit_stages_for_the_agents_with_a_file_and_skips_the_others(tsession):
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main, alpha, beta, gamma = _keys(s)
    w = E.RosterWork()
    w.cells[("checker", "description")] = "Checks everything."
    w.cells[("drafter", "reasoning_effort")] = E.REMOVE
    rep = E.stage_roster(s, w, [alpha, beta])                  # beta uses the master, which is not checked
    assert rep.changed == 1 and [a for a, _ in rep.skipped] == [beta]
    rep = E.stage_roster(s, w, [main, alpha, beta, gamma])      # with the master: beta and gamma get it through it
    assert rep.changed == 1 and rep.skipped == []
    for a in (main, alpha):
        v = s.drafts.draft(RM.doc_key(a)).view()
        assert v["teammates"]["checker"]["description"] == "Checks everything."
        assert "reasoning_effort" not in v["teammates"]["drafter"]
    assert E.table_for(s, beta).teammates["checker"]["description"] == "Checks everything."


def test_a_teammate_one_agent_lacks_is_added_only_with_a_description(tsession):
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main, alpha = _keys(s)[:2]
    s.docs[RM.doc_key(alpha)].data["teammates"].pop("drafter")
    w = E.RosterWork()
    w.cells[("drafter", "model")] = "test-model-2"
    rep = E.stage_roster(s, w, [main, alpha])
    assert rep.changed == 1 and rep.skipped and rep.skipped[0][0] == alpha
    w = E.RosterWork()
    w.cells.update({("drafter", "description"): "Drafts.", ("drafter", "toolsets"): ["file"]})
    assert E.stage_roster(s, w, [alpha]).changed == 1
    assert s.drafts.draft(RM.doc_key(alpha)).view()["teammates"]["drafter"] == {"description": "Drafts.",
                                                                               "toolsets": ["file"]}


def test_rename_never_overwrites_an_existing_teammate_and_edits_follow_it(tsession):
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main, alpha = _keys(s)[:2]
    s.docs[RM.doc_key(alpha)].data["teammates"]["writer"] = {"description": "x"}
    w = E.RosterWork()
    w.renames["drafter"] = "writer"
    w.cells[("drafter", "model")] = "test-model-3"
    rep = E.stage_roster(s, w, [main, alpha])
    assert rep.changed == 2 and [a for a, _ in rep.skipped] == [alpha]
    v = s.drafts.draft(RM.doc_key(main)).view()
    assert list(v["teammates"]) == ["writer", "checker"] and v["teammates"]["writer"]["model"] == "test-model-3"
    assert v["teammates"]["writer"]["description"] == "Writes first drafts."
    a = s.drafts.draft(RM.doc_key(alpha)).view()["teammates"]
    assert a["writer"] == {"description": "x"} and a["drafter"]["model"] == "test-model-3"


def test_an_added_teammate_needs_a_description_and_route_with_model(tsession):
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main = _keys(s)[0]
    w = E.RosterWork()
    w.add_teammate("reviewer")
    w.cells[("reviewer", "route")] = "test-route-a"
    probs = w.problems()
    assert any("description" in p for p in probs) and any("route and model" in p for p in probs)
    w.cells.update({("reviewer", "description"): "Reviews.", ("reviewer", "model"): "test-model-small"})
    assert not w.problems()
    assert E.stage_roster(s, w, [main]).changed == 1
    assert list(s.drafts.draft(RM.doc_key(main)).view()["teammates"]) == ["drafter", "checker", "reviewer"]


def test_editing_only_the_route_of_a_teammate_with_a_model_is_refused():
    from hermes_config_manager.services import roster_edit as E

    w = E.RosterWork()
    w.cells[("drafter", "model")] = E.REMOVE
    assert any("route and model" in p for p in w.problems(roster()["teammates"]))
    w.cells[("drafter", "route")] = E.REMOVE
    assert w.problems(roster()["teammates"]) == []


def test_max_live_runs_is_staged_in_each_agents_config(tsession):
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main, alpha, beta, gamma = _keys(s)
    assert E.who_has(s, [main, alpha], E.KNOB) == {6: [main], "": [alpha]}
    w = E.RosterWork()
    w.knobs[E.KNOB] = 17
    assert any(E.KNOB in p for p in w.problems())
    w.knobs[E.KNOB] = 8
    _settings(s, gamma).clear()
    s.agents[gamma].data["plugins"].pop("entries")              # gamma: no hermes-teammates settings at all
    rep = E.stage_roster(s, w, [main, beta, gamma])
    assert rep.changed == 2 and [a for a, _ in rep.skipped] == [gamma]
    for a in (main, beta):
        assert s.drafts.draft(a).view()["plugins"]["entries"]["hermes-teammates"]["settings"]["max_live_runs"] == 8
        assert not s.drafts.has_changes(RM.doc_key(a))


def test_replace_creates_a_profiles_file_and_sets_its_roster_file(tsession):
    from hermes_config_manager.core.edits import OpKind
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main, alpha, beta, gamma = _keys(s)
    rep = E.replace_ops(s, [main, alpha, beta, gamma], None)            # with the master
    assert rep.changed == 3 and [a for a, _ in rep.skipped] == [main]
    op = s.drafts.draft(RM.doc_key(gamma)).ops[0]
    assert op.kind is OpKind.REPLACE_FILE and op.text == ROSTER_YAML
    cfg = s.drafts.draft(gamma).view()
    assert cfg["plugins"]["entries"]["hermes-teammates"]["settings"]["roster_file"] == "teammates.yaml"
    assert not s.drafts.has_changes(beta)                               # its roster_file was already set
    for k in (beta, gamma):
        assert E.table_for(s, k).kind == E.OWN and E.table_for(s, k).pending == "create"
    s.drafts.discard()
    rep = E.replace_ops(s, [beta], alpha)                               # with alpha's roster
    assert rep.changed == 1 and s.drafts.draft(RM.doc_key(beta)).ops[0].text == ALPHA_YAML
    s.drafts.discard()
    _settings(s, beta)["roster_source"] = "root"
    rep = E.replace_ops(s, [beta], None)
    assert rep.changed == 0 and "root" in rep.skipped[0][1]


def test_remove_own_file_only_where_the_profile_then_falls_back(tsession):
    from hermes_config_manager.core.edits import OpKind
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main, alpha, beta, _gamma = _keys(s)
    rep = E.delete_ops(s, [main, alpha, beta])
    assert rep.changed == 1 and {a for a, _ in rep.skipped} == {main, beta}
    assert s.drafts.draft(RM.doc_key(alpha)).ops[-1].kind is OpKind.DELETE_FILE
    assert E.table_for(s, alpha).pending == "remove" and not s.drafts.has_changes(alpha)   # roster_file left as is
    s.drafts.discard()
    _settings(s, alpha)["roster_source"] = "own"
    rep = E.delete_ops(s, [alpha])
    assert rep.changed == 0 and "fallback" in rep.skipped[0][1]


def test_who_has_what_names_the_agents_per_value(tsession):
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main, alpha, beta, gamma = _keys(s)
    got = E.who_has(s, [main, alpha, beta, gamma], "drafter", "model")
    assert got == {"test-model-small": [main, beta, gamma], "test-model-alpha": [alpha]}
    assert E.who_has(s, [main, alpha], "checker", "toolsets") == {"file, web": [main, alpha]}
    assert E.who_has(s, [main, alpha], "nope", "model") == {None: [main, alpha]}
    assert E.differ_count(s, [main, alpha]) == 2                         # drafter.model and max_live_runs


def test_restarting_after_writing_the_master_includes_the_profiles_that_use_it(tsession):
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services.review import restart_targets

    s = tsession
    main, alpha, beta, gamma = _keys(s)
    got = restart_targets(s, [RM.doc_key(main), main, RM.doc_key(alpha)])
    assert sorted(got) == sorted([main, alpha, beta, gamma]) and len(got) == len(set(got))


def test_reviews_snapshot_of_the_master_forbids_whole_file_changes(tsession):
    from hermes_config_manager.core.edits import EditOp, OpKind
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services.review import snapshot

    s = tsession
    main, alpha = _keys(s)[:2]
    for agent, allowed in ((main, False), (alpha, True)):
        key = RM.doc_key(agent)
        base, sha = s.base_of(key)
        s.drafts.apply({key: (base, sha)}, EditOp(OpKind.DELETE_FILE, ()))
        snap = snapshot(s, key)
        assert snap.whole_file_allowed is allowed and snap.validate is R.review


def test_a_file_that_appears_after_replace_was_staged_can_be_replaced_after_staging_again(qapp, tsession, hermes_home):
    from hermes_config_manager.services import apply as A
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E
    from hermes_config_manager.services.review import snapshot

    from .conftest import pump

    s = tsession
    beta = _keys(s)[2]
    key = RM.doc_key(beta)
    assert E.replace_ops(s, [beta], None).changed == 1
    (hermes_home / "profiles" / "beta" / "teammates.yaml").write_bytes(ALPHA_YAML.encode())
    s.refresh_rosters("local")
    assert pump(qapp, lambda: key in s.docs and not s.is_refreshing())
    t = s.transport("local")
    assert A.prepare_change(None, t, snapshot(s, key)).status == A.CONFLICT
    assert E.replace_ops(s, [beta], None).changed == 1                  # staged again: now against that file
    assert A.prepare_change(None, t, snapshot(s, key)).status == A.READY


def test_a_broken_own_file_says_so_and_can_be_replaced_or_removed(qapp, tsession, hermes_home):
    from hermes_config_manager.services import roster_edit as E

    from .conftest import pump

    s = tsession
    alpha = _keys(s)[1]
    (hermes_home / "profiles" / "alpha" / "teammates.yaml").write_bytes(b"teammates: [unclosed")
    s.refresh_rosters("local")
    assert pump(qapp, lambda: not s.is_refreshing())
    v = E.table_for(s, alpha)
    assert v.kind == E.BROKEN and not v.editable and v.error
    assert E.replace_ops(s, [alpha], None).changed == 1
    s.drafts.discard()
    (hermes_home / "profiles" / "alpha" / "teammates.yaml").write_bytes(b"routes: {}\n")
    s.refresh_rosters("local")
    assert pump(qapp, lambda: not s.is_refreshing())
    assert E.table_for(s, alpha).kind == E.BROKEN and "teammates" in E.table_for(s, alpha).error
    assert E.delete_ops(s, [alpha]).changed == 1


def test_replace_then_write_creates_the_file_and_points_the_config_at_it(qapp, tsession, hermes_home):
    from hermes_config_manager.core.yamlpatch import fast_load
    from hermes_config_manager.services import apply as A
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E
    from hermes_config_manager.services.review import ApplyFlow

    from .conftest import pump

    s = tsession
    gamma = _keys(s)[3]
    assert E.replace_ops(s, [gamma], None).changed == 1
    flow = ApplyFlow(s)
    got = {}
    flow.prepared.connect(lambda p: got.setdefault("prepared", p))
    flow.prepare([gamma, RM.doc_key(gamma)])
    assert pump(qapp, lambda: "prepared" in got)
    assert [p.status for p in got["prepared"]] == [A.READY, A.READY]
    flow.finished.connect(lambda r: got.setdefault("done", r))
    flow.apply(got["prepared"])
    assert pump(qapp, lambda: "done" in got)
    assert [r.status for r in got["done"]] == [A.WRITTEN, A.WRITTEN]
    folder = hermes_home / "profiles" / "gamma"
    assert (folder / "teammates.yaml").read_text() == ROSTER_YAML
    cfg = fast_load((folder / "config.yaml").read_text())
    assert cfg["plugins"]["entries"]["hermes-teammates"]["settings"] == {"roster_source": "fallback",
                                                                         "roster_file": "teammates.yaml"}
    assert pump(qapp, lambda: not s.is_refreshing() and E.table_for(s, gamma).kind == E.OWN)


# ------------------------------------------------------------------ drafts (regressions)
def test_a_later_edit_never_drops_a_staged_rename():
    from hermes_config_manager.core.edits import AgentDraft, EditOp, OpKind
    from hermes_config_manager.core.yamlpatch import fast_load

    draft = AgentDraft("k#teammates", fast_load(ROSTER_YAML), "sha")
    draft.add(EditOp(OpKind.RENAME_KEY, ("teammates", "drafter"), "writer"))
    draft.add(EditOp(OpKind.SET, ("teammates", "drafter"), {"description": "New."}))
    view = draft.view()
    assert view["teammates"]["writer"]["model"] == "test-model-small" and view["teammates"]["drafter"] == \
        {"description": "New."}


def test_a_draft_of_a_file_that_vanished_takes_the_new_empty_base():
    from hermes_config_manager.core.edits import DraftStore

    store = DraftStore()
    d = store.ensure("k#teammates", {"teammates": {}}, "old-sha")
    assert not d.ops
    assert store.ensure("k#teammates", {}, "").base_sha == ""


def test_chained_and_swapped_renames_are_applied_in_order(tsession):
    from hermes_config_manager.services import roster as RM
    from hermes_config_manager.services import roster_edit as E

    s = tsession
    main = _keys(s)[0]
    w = E.RosterWork()
    w.renames.update({"checker": "writer", "drafter": "checker"})
    rep = E.stage_roster(s, w, [main])
    assert rep.changed == 1 and rep.skipped == []
    v = s.drafts.draft(RM.doc_key(main)).view()
    assert list(v["teammates"]) == ["checker", "writer"] and v["teammates"]["checker"]["model"] == "test-model-small"
