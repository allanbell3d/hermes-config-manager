"""Settings model, layout and the Edit -> Stage rules (no screens involved)."""

from __future__ import annotations

import json

from hermes_config_manager.core.layout import MODE_ALL, Layout, default_layout, reconcile
from hermes_config_manager.core.paths import MISSING, get_in
from hermes_config_manager.core.schema import DATA_DIR, load_schema
from hermes_config_manager.core.settings_tree import CHAIN_OWNED, build_model, shape, union_shape, used_paths
from hermes_config_manager.services import chains as C
from hermes_config_manager.services.editing import EditScope
from hermes_config_manager.services.work import REMOVE, WorkStore, chain_staged, rows_of

PAYLOAD = json.loads((DATA_DIR / "hermes_schema.json").read_text(encoding="utf-8"))
SCHEMA = load_schema()


def model_for(datas=()):
    return build_model(PAYLOAD, SCHEMA, union_shape(shape(d) for d in datas))


# ------------------------------------------------------------------ settings model
def test_model_mirrors_the_export_and_leaves_chain_keys_to_the_chain_tables():
    m = model_for()
    assert {"model", "delegation", "auxiliary", "agent", "display"} <= set(m.sections)
    assert not CHAIN_OWNED & set(m.nodes)
    effort = m.nodes[("agent", "reasoning_effort")]
    assert effort.kind == "enum" and "high" in effort.options
    assert "vision" in m.tasks and "review" in m.no_effort
    assert all(c.key not in ("provider", "model", "reasoning_effort", "fallback_chain") for c in m.task_nodes("vision"))
    # sections keep Hermes' nesting: display has groups, each child knows its parent
    disp = m.sections["display"]
    group = next(n for n in disp.nodes if n.is_group)
    assert all(c.parent is group for c in group.children)


def test_keys_only_in_the_agents_files_are_added():
    m = model_for([{"display": {"my_own_knob": 3}, "my_section": {"a": {"b": True}}}])
    assert m.nodes[("display", "my_own_knob")].extra and m.nodes[("display", "my_own_knob")].kind == "int"
    assert "my_section" in m.sections and m.nodes[("my_section", "a", "b")].kind == "bool"
    assert m.default_category("my_section") == "custom"


# ------------------------------------------------------------------ layout
def test_default_layout_pages_and_round_trip():
    m = model_for()
    lay = default_layout(m)
    models = lay.page("models/Models")
    assert models.sections == ["model", "delegation", "auxiliary"]
    placed = [k for p in lay.pages() for k in p.sections]
    assert sorted(placed) == sorted(m.sections) and len(placed) == len(set(placed))
    again = Layout.from_json(json.loads(json.dumps(lay.to_json())))
    assert again.to_json() == lay.to_json()


def test_reconcile_places_new_sections_and_drops_unknown_ones():
    m = model_for([{"brand_new": {"x": 1}}])
    lay = default_layout(model_for())
    lay.cats[0].pages[0].sections.append("gone_section")
    lay = reconcile(lay, m)
    placed = [k for p in lay.pages() for k in p.sections]
    assert "brand_new" in placed and "gone_section" not in placed


def test_visibility_follows_the_loaded_files_until_the_user_decides():
    data = {"display": {"compact": True}}
    m = model_for([data])
    used = used_paths(m, union_shape([shape(data)]))
    lay = default_layout(m)
    assert not lay.node_hidden(("display", "compact"), used)
    assert lay.node_hidden(("display", "skin"), used)
    lay.toggle_node(("display", "skin"), used)                # the eye: show it
    assert not lay.node_hidden(("display", "skin"), used)
    lay.toggle_node(("display", "compact"), used)             # the eye: hide it
    assert lay.node_hidden(("display", "compact"), used)
    assert lay.section_hidden(m.sections["voice"], used) and not lay.section_hidden(m.sections["display"], used)
    lay.show_all()
    assert lay.mode == MODE_ALL and not lay.node_hidden(("display", "skin"), used)


# ------------------------------------------------------------------ edit -> stage
def _scope(session, keys):
    return EditScope(session, keys, keys[0] if keys else "")


def test_working_value_survives_a_selection_change_and_stages_only_for_checked_agents(session):
    keys = session.ordered_keys()
    work = WorkStore()
    path = ("compression", "threshold")
    work.set(path, 0.42, _scope(session, keys[:2]))
    assert work.has(path)
    # the selection changes: the edit stays, untouched
    assert work.value(path) == 0.42 and work.dirty(path, _scope(session, keys[2:]))
    n = work.stage([path], _scope(session, keys[2:]))
    assert n == 2 and not work.has(path)
    staged = [k for k in keys if session.drafts.has_changes(k)]
    assert staged == keys[2:]
    for k in keys[2:]:
        assert session.drafts.draft(k).get(path) == 0.42


def test_remove_key_stages_a_removal(session):
    keys = session.ordered_keys()
    path = ("compression", "threshold")
    scope = _scope(session, keys[:1])
    assert scope.draft_value(keys[0], path) is not MISSING
    work = WorkStore()
    work.set(path, REMOVE, scope)
    work.stage([path], scope)
    assert session.drafts.draft(keys[0]).get(path) is MISSING


def test_chain_stage_writes_exactly_the_edited_cells(session):
    keys = session.ordered_keys()
    scope = _scope(session, keys)
    before = {k: rows_of(C.read_chain(scope, C.DELEGATION, k)) for k in keys}
    work = WorkStore()
    w = work.work_chain("delegation", scope, keys[0])
    w.rows[0][0], w.rows[0][1] = "openrouter", "vendor/new-model"
    w.mark(0, "pm")
    work.edited()
    assert work.chain_dirty("delegation", scope)
    work.stage_chain("delegation", scope)
    for k in keys:
        after = rows_of(C.read_chain(scope, C.DELEGATION, k))
        assert after[0][:2] == ["openrouter", "vendor/new-model"]
        assert after[0][2] == before[k][0][2] if before[k] else True      # effort not edited: kept
        assert after[1:] == before[k][1:]                                  # fallbacks not edited: kept
        assert chain_staged(scope, "delegation", k) == (before[k][:1] != after[:1])


def test_primary_effort_goes_to_agent_reasoning_effort_and_provider_change_drops_endpoint(session):
    keys = session.ordered_keys()
    k = keys[0]
    scope = _scope(session, [k])
    rec = session.record(k)
    rec.data["model"]["base_url"] = "https://old-endpoint.example/v1"   # as if the file had one
    work = WorkStore()
    w = work.work_chain("primary", scope, k)
    new_prov = "test-provider-b" if rec.data["model"].get("provider") == "test-provider-a" else "test-provider-a"
    w.rows[0] = [new_prov, "claude-x", "high"]
    w.mark(0, "pm")
    w.mark(0, "e")
    work.stage_chain("primary", scope)
    d = session.drafts.draft(k)
    assert d.get(("agent", "reasoning_effort")) == "high"
    assert get_in(d.view(), ("model", "reasoning_effort")) == get_in(rec.data, ("model", "reasoning_effort"))
    assert d.get(("model", "provider")) == new_prov and d.get(("model", "default")) == "claude-x"
    assert d.get(("model", "base_url")) is MISSING


def test_default_cell_writes_provider_auto_and_an_empty_model(session):
    k = session.ordered_keys()[0]
    scope = _scope(session, [k])
    work = WorkStore()
    w = work.work_chain("delegation", scope, k)
    w.rows[0][0], w.rows[0][1] = C.AUTO_PM
    w.mark(0, "pm")
    w.rows.append([*C.AUTO_PM, None])
    w.mark(len(w.rows) - 1, "pm")
    work.stage_chain("delegation", scope)
    d = session.drafts.draft(k)
    assert d.get(("delegation", "provider")) == "auto" and d.get(("delegation", "model")) == ""
    assert d.get(("delegation", "fallback_providers"))[-1] == {"provider": "auto", "model": ""}
    assert rows_of(C.read_chain(scope, C.DELEGATION, k))[0][:2] == ["auto", ""]


def test_editing_the_effort_of_an_auto_head_keeps_its_empty_model(session):
    spec = C.aux_spec("vision")
    k = next(k for k in session.ordered_keys()
             if rows_of(C.read_chain(_scope(session, [k]), spec, k))[:1] == [["auto", "", ""]])
    scope = _scope(session, [k])
    work = WorkStore()
    w = work.work_chain("aux:vision", scope, k)
    w.rows[0][2] = "high"
    w.mark(0, "e")
    work.stage_chain("aux:vision", scope)
    d = session.drafts.draft(k)
    assert d.get(("auxiliary", "vision", "reasoning_effort")) == "high"
    assert d.get(("auxiliary", "vision", "model")) == "" and d.get(("auxiliary", "vision", "provider")) == "auto"


def test_a_masked_block_gives_every_agent_its_own_credentials_back(session):
    from hermes_config_manager.core import secrets

    keys = session.ordered_keys()[:3]
    path = ("my_providers",)
    for i, k in enumerate(keys[:2]):
        session.record(k).data["my_providers"] = {"local": {"base_url": "http://localhost:8080/v1",
                                                            "api_key": f"sk-test-own-key-{i}-abcdefghijklmnop"}}
    session.record(keys[2]).data["my_providers"] = {"other": {"api_key": "sk-test-third-abcdefghijklmnopqr"}}
    scope = _scope(session, keys)
    shown = secrets.redact(scope.draft_value(keys[0], path), path)
    assert secrets.contains_mask(shown) and "own-key" not in str(shown)
    edited = {"local": dict(shown["local"], base_url="http://localhost:9090/v1")}   # the user changes only the URL
    work = WorkStore()
    work.set(path, edited, scope)
    work.stage([path], scope, block_paths={path})
    for i, k in enumerate(keys[:2]):
        v = session.drafts.draft(k).get(path)
        assert v["local"]["base_url"] == "http://localhost:9090/v1"
        assert v["local"]["api_key"] == f"sk-test-own-key-{i}-abcdefghijklmnop"       # its own, not the first agent's
    assert (keys[2], path) in work.skipped and not session.drafts.has_changes(keys[2])
