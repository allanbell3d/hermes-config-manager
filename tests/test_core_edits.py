from __future__ import annotations


from hermes_config_manager.core.edits import AgentDraft, DraftStore, EditOp, OpKind, apply_ops, view_get
from hermes_config_manager.core.paths import (
    MISSING,
    delete_in,
    get_in,
    iter_leaves,
    set_in,
)
from hermes_config_manager.core import secrets

BASE = {
    "model": {"provider": "zai", "model": "glm-5.3", "reasoning_effort": "high"},
    "delegation": {
        "provider": "zai",
        "model": "glm-5.3-flash",
        "fallback_providers": [{"provider": "vertex", "model": "g-flash"}],
        "max_iterations": 50,
    },
    "custom_providers": [{"name": "x", "models": {"glm-5.1": {"category": "zhipu"}}}],
}


def test_get_set_delete():
    data = {"a": {"b": [1, {"c": 2}]}}
    assert get_in(data, ("a", "b", 1, "c")) == 2
    assert get_in(data, ("a", "x")) is MISSING
    set_in(data, ("a", "new", "deep"), True)
    assert data["a"]["new"] == {"deep": True}
    set_in(data, ("a", "b", 2), 3)  # append
    assert data["a"]["b"][-1] == 3
    assert delete_in(data, ("a", "new"))
    assert not delete_in(data, ("a", "new"))


def test_iter_leaves_treats_scalar_lists_as_leaves():
    leaves = dict(iter_leaves({"a": [1, 2], "b": [{"c": 1}], "d": {}}))
    assert leaves[("a",)] == [1, 2]
    assert leaves[("b", 0, "c")] == 1
    assert leaves[("d",)] == {}


def test_field_patch_does_not_copy_other_fields():
    draft = AgentDraft("a", BASE)
    draft.add(EditOp(OpKind.SET, ("model", "model"), "glm-5.4"))
    view = draft.view()
    assert view["model"] == {"provider": "zai", "model": "glm-5.4", "reasoning_effort": "high"}
    assert view["delegation"] == BASE["delegation"]
    assert BASE["model"]["model"] == "glm-5.3"  # base untouched


def test_setting_base_value_is_a_noop():
    draft = AgentDraft("a", BASE)
    draft.add(EditOp(OpKind.SET, ("model", "model"), "other"))
    draft.add(EditOp(OpKind.SET, ("model", "model"), "glm-5.3"))
    assert draft.ops == []


def test_type_sensitive_noop_detection():
    draft = AgentDraft("a", {"x": 1})
    draft.add(EditOp(OpKind.SET, ("x",), True))
    assert len(draft.ops) == 1  # True is not 1


def test_remove_then_set_child_matches_view():
    draft = AgentDraft("a", BASE)
    draft.add(EditOp(OpKind.REMOVE, ("delegation",)))
    draft.add(EditOp(OpKind.SET, ("delegation", "model"), "m2"))
    assert draft.get(("delegation",)) == {"model": "m2"}
    assert draft.get(("delegation", "provider")) is MISSING
    assert draft.view()["delegation"] == {"model": "m2"}


def test_view_get_matches_full_view_for_all_paths():
    ops = [
        EditOp(OpKind.REPLACE_BLOCK, ("delegation",), {"provider": "p", "model": "m"}),
        EditOp(OpKind.SET, ("delegation", "fallback_providers"), [{"provider": "q", "model": "n"}]),
        EditOp(OpKind.REMOVE, ("model", "reasoning_effort")),
    ]
    full = apply_ops(BASE, ops)
    for path, _ in iter_leaves(full):
        assert view_get(BASE, ops, path) == get_in(full, path)
    assert view_get(BASE, ops, ("model", "reasoning_effort")) is MISSING


def test_later_ancestor_supersedes_children():
    draft = AgentDraft("a", BASE)
    draft.add(EditOp(OpKind.SET, ("delegation", "model"), "x"))
    draft.add(EditOp(OpKind.REPLACE_BLOCK, ("delegation",), {"model": "y"}))
    assert [o.path for o in draft.ops] == [("delegation",)]


def test_revert_inside_replaced_block():
    draft = AgentDraft("a", BASE)
    draft.add(EditOp(OpKind.REPLACE_BLOCK, ("model",), {"provider": "p", "model": "m", "reasoning_effort": "high"}))
    draft.revert(("model", "provider"))
    draft.revert(("model", "model"))
    assert draft.ops == []  # block became identical to base


def test_rebase_reports_external_conflicts():
    draft = AgentDraft("a", BASE, "sha1")
    draft.add(EditOp(OpKind.SET, ("model", "model"), "x"))
    changed = dict(BASE, model=dict(BASE["model"], model="changed-elsewhere"))
    conflicts = draft.rebase(changed, "sha2")
    assert conflicts == [("model", "model")]
    assert draft.base_changed


def test_store_bulk_undo_redo():
    store = DraftStore()
    seen = []
    store.subscribe(seen.append)
    targets = {"a": (BASE, "s"), "b": (BASE, "s")}
    store.apply(targets, EditOp(OpKind.SET, ("model", "model"), "new"))
    assert store.keys_with_changes() == ["a", "b"]
    assert seen[-1] == {"a", "b"}
    store.undo()
    assert not store.has_changes()
    store.redo()
    assert sum(len(store.draft(k).ops) for k in store.keys_with_changes()) == 2
    store.discard(["a"])
    assert store.keys_with_changes() == ["b"]


def test_secret_classification():
    assert secrets.is_secret(("providers", "x", "api_key"), "sk-abcdefghijklmnopqrstuv")
    assert secrets.is_secret(("dashboard", "basic_auth", "password"), "hunter2hunter2")
    assert not secrets.is_secret(("providers", "x", "key_env"), "DASHSCOPE_API_KEY")
    assert not secrets.is_secret(("delegation", "api_mode"), "chat_completions")
    assert not secrets.is_secret(("x", "token"), "${TELEGRAM_TOKEN}")
    assert not secrets.is_secret(("agent", "max_tokens"), 4000)
    assert secrets.is_secret(("anything",), "sk-ant-REDACTEDREDACTEDREDACTED")


def test_mask_text_masks_yaml_and_inline_tokens():
    text = "a:\n  api_key: sk-12345678901234567890\n  key_env: OPENAI_API_KEY\n+  token: abcdefghijklmnopqrstuvwx\nurl: https://u:pw12345@host/x\n"
    masked = secrets.mask_text(text)
    assert "sk-1234" not in masked and "abcdefghijklmnop" not in masked and "pw12345" not in masked
    assert "OPENAI_API_KEY" in masked
    assert masked.count(secrets.MASK) == 3
