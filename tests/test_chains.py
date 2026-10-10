"""Model chains: reading, variants, patch-only writes (primary / delegation / auxiliary)."""

from __future__ import annotations

from hermes_config_manager.core import assignments as A
from hermes_config_manager.core.assignments import ModelAssignment as M
from hermes_config_manager.core.edits import apply_ops
from hermes_config_manager.core.paths import get_in
from hermes_config_manager.core.yamlpatch import diff_paths
from hermes_config_manager.services import chains as C
from hermes_config_manager.services.apply import READY, DraftSnapshot, prepare_change
from hermes_config_manager.services.editing import EditScope


def _scope(session, keys=None):
    keys = keys or session.ordered_keys()
    return EditScope(session, keys, keys[0])


def _apply(scope, ops_for) -> int:
    per_agent = {}
    for key in scope.usable_keys:
        ops = ops_for(key)
        if ops:
            base, sha = scope.session.base_of(key)
            per_agent[key] = (base, sha, ops)
    return len(scope.session.drafts.apply_many(per_agent)) if per_agent else 0


def set_chain(scope, spec, head=C.KEEP, fallbacks=C.KEEP) -> int:
    """(head, fallbacks) for every agent of the scope as one undo step (what Stage does for a chain)."""
    return _apply(scope, lambda key: C.chain_ops(scope, spec, key, head, fallbacks))


def set_heads(scope, specs, head) -> int:
    return _apply(scope, lambda key: [op for spec in specs for op in C.chain_ops(scope, spec, key, head, C.KEEP)])


def _after(session, key):
    draft = session.drafts.draft(key)
    return apply_ops(session.record(key).data, draft.ops) if draft else session.record(key).data


def test_read_primary_chain_uses_runtime_effort(session):
    scope = _scope(session)
    for key in scope.keys:
        data = session.record(key).data
        chain = C.read_chain(scope, C.PRIMARY, key)
        assert chain[0].identity == A.primary(data).identity
        assert chain[0].reasoning_effort == A.effective_main_effort(data)[0]
        assert [a.identity for a in chain[1:]] == [a.identity for a in A.assignment_list_at(data, ("fallback_providers",))]


def test_variants_cover_scope(session):
    scope = _scope(session)
    groups = C.variants(scope, C.DELEGATION)
    assert sorted(k for _, keys in groups for k in keys) == sorted(scope.keys)
    assert [len(keys) for _, keys in groups] == sorted((len(keys) for _, keys in groups), reverse=True)


def test_set_primary_chain_patches_only_chain_keys(session):
    scope = _scope(session)
    head = M("zai", "glm-5.3", "high")
    fallbacks = [M("vertex", "google/gemini-3.8-flash", "medium"), M("openai-codex", "gpt-5.6-terra", None)]
    assert set_chain(scope, C.PRIMARY, head, fallbacks) == len(scope.keys)
    for key in scope.keys:
        before, after = session.record(key).data, _after(session, key)
        assert C.read_chain(scope, C.PRIMARY, key) == (head, *fallbacks)
        # read with the runtime's precedence (default > model > name); an unchanged
        # alias spelling is left alone rather than rewritten
        assert A.primary(after).identity == ("zai", "glm-5.3")
        # effort written where the runtime reads it, never model.reasoning_effort
        assert get_in(after, ("model", "reasoning_effort")) == get_in(before, ("model", "reasoning_effort"))
        allowed = {("model",), ("agent",), ("fallback_providers",)}
        for path in diff_paths(before, after):
            assert path[:1] in allowed or path[:2] in allowed, path
        if A.primary(before).model != "glm-5.3":   # changed: canonical key, stale aliases gone
            assert after["model"]["default"] == "glm-5.3"
            for alias in A.PRIMARY_MODEL_ALIASES:
                assert alias not in after["model"] or not isinstance(after["model"][alias], str)


def test_one_undo_step_for_bulk_chain(session):
    scope = _scope(session)
    set_chain(scope, C.DELEGATION, M("zai", "glm-5.3-flash", "medium"), [])
    assert session.drafts.has_changes()
    session.drafts.undo()
    assert not session.drafts.has_changes()


def test_fallbacks_only_leave_head_untouched(session):
    scope = _scope(session)
    heads = {k: C.read_chain(scope, C.DELEGATION, k)[0] for k in scope.keys}
    set_chain(scope, C.DELEGATION, fallbacks=[M("zai", "glm-5.3", "low")])
    for key in scope.keys:
        chain = C.read_chain(scope, C.DELEGATION, key)
        assert chain[0] == heads[key] and chain[1:] == (M("zai", "glm-5.3", "low"),)


def test_reorder_keeps_extra_keys_of_fallback_items(session):
    key = session.ordered_keys()[0]
    scope = _scope(session, [key])
    items = [{"provider": "a", "model": "m1", "base_url": "http://one"}, {"provider": "b", "model": "m2"}]
    scope.set(("delegation", "fallback_providers"), items)
    set_chain(scope, C.DELEGATION, fallbacks=[M("b", "m2"), M("a", "m1")])
    after = scope.draft_value(key, ("delegation", "fallback_providers"))
    assert after == [{"provider": "b", "model": "m2"}, {"provider": "a", "model": "m1", "base_url": "http://one"}]


def test_set_heads_for_several_aux_tasks(session):
    scope = _scope(session)
    tasks = [t for t in A.auxiliary_tasks(session.record(scope.keys[0]).data)][:3]
    specs = [C.aux_spec(t) for t in tasks]
    before_fallbacks = {(k, t): C.read_chain(scope, C.aux_spec(t), k)[1:] for k in scope.keys for t in tasks}
    set_heads(scope, specs, M("vertex", "google/gemini-3.8-flash", "high"))
    for key in scope.keys:
        for task in tasks:
            chain = C.read_chain(scope, C.aux_spec(task), key)
            assert chain[0] == M("vertex", "google/gemini-3.8-flash", "high")
            assert chain[1:] == before_fallbacks[(key, task)]


def test_prepared_change_is_minimal_and_verified(session):
    key = session.ordered_keys()[1]
    scope = _scope(session, [key])
    set_chain(scope, C.PRIMARY, M("zai", "glm-5.3", "high"), C.KEEP)
    rec = session.record(key)
    draft = session.drafts.draft(key)
    snap = DraftSnapshot(key, rec.host, rec.display, rec.path, draft.base, draft.base_sha, list(draft.ops))
    prepared = prepare_change(None, session.transport(rec.host), snap)
    assert prepared.status == READY, prepared.message
    changed = [line for line in prepared.diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert 1 <= len(changed) <= 8, changed      # a handful of lines, not a re-dump
