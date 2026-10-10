"""discord: keys Hermes reads without exporting them: source extraction, schema build, tree tags, the move fix."""

from __future__ import annotations

import json
from types import SimpleNamespace

from hermes_config_manager.core.edits import OpKind
from hermes_config_manager.core.paths import MISSING, get_in
from hermes_config_manager.core.schema import DATA_DIR, load_schema
from hermes_config_manager.core.settings_tree import build_model, did_you_mean, key_tags, shape, union_shape
from hermes_config_manager.services.work import move_key_ops
from tools import build_schema as B
from tools import hermes_source_keys as K

PAYLOAD = json.loads((DATA_DIR / "hermes_schema.json").read_text(encoding="utf-8"))
SCHEMA = load_schema()

# A cut-down adapter in the shape of Hermes' plugins/platforms/discord/adapter.py.
ADAPTER = '''
_YAML_BOOL_ENV_KEYS = (
    ("require_mention", "DISCORD_REQUIRE_MENTION"),
)
_YAML_WEBSOCKET_LIVENESS_KEYS = (
    ("websocket_liveness_interval_seconds", "liveness_interval_seconds", "HERMES_LIVENESS"),
    ("websocket_max_latency_seconds", None, None),
)

class Adapter:
    def __init__(self, config):
        self._slash = self.config.extra.get("slash_commands", True)
        extra["is_bot"] = True                       # a write, not a setting

    def _get_allow_bots(self):
        return self._gate_raw("allow_bots", "DISCORD_ALLOW_BOTS")


def _apply_yaml_config(yaml_cfg, discord_cfg):
    def _gate(key, env_key, *, from_platform_extra, lower=False):
        pass
    _gate("allow_bots", "DISCORD_ALLOW_BOTS", from_platform_extra=True, lower=True)
    for key, env_key in (("reactions", "DISCORD_REACTIONS"),):
        pass
    if "history_backfill" in discord_cfg:
        pass
    allow_mentions_cfg = discord_cfg.get("allow_mentions")
    if isinstance(allow_mentions_cfg, dict):
        for yaml_key in ("everyone", "users"):
            if yaml_key in allow_mentions_cfg:
                pass
    _discord_extra = discord_cfg.get("extra")
'''

CONFIG_LOADER = '''
_TELEGRAM = frozenset()
_DISCORD_SLACK = frozenset()
_SHARED_KEYS: tuple = (
    ("unauthorized_dm_behavior", None, "dm"),
    *_plain("reply_prefix", "typing_indicator"),
    ("allowed_chats", _TELEGRAM, None),
    ("channel_skill_bindings", _DISCORD_SLACK, None),
)
'''


# ------------------------------------------------------------------ reading the Hermes source
def test_adapter_keys_are_read_from_every_place_the_adapter_reads_them():
    found = K.discord_adapter_keys(ADAPTER)
    assert found["allow_bots"]["env"] == "DISCORD_ALLOW_BOTS"
    assert found["require_mention"]["env"] == "DISCORD_REQUIRE_MENTION"
    assert found["reactions"]["env"] == "DISCORD_REACTIONS"
    assert found["slash_commands"]["default"] is True
    assert found["liveness_interval_seconds"]["legacy_of"] == "websocket_liveness_interval_seconds"
    assert {"history_backfill", "allow_mentions", "allow_mentions.everyone", "allow_mentions.users",
            "websocket_max_latency_seconds"} <= set(found)
    assert "is_bot" not in found and "extra" not in found        # writes and the generic sub-map are not settings


def test_shared_keys_leave_out_rows_for_other_platforms():
    found = K.shared_keys(CONFIG_LOADER)
    assert set(found) == {"unauthorized_dm_behavior", "reply_prefix", "typing_indicator", "channel_skill_bindings"}


# ------------------------------------------------------------------ building the schema
def test_deprecated_means_the_description_starts_with_it():
    assert B.is_deprecated("DEPRECATED no-op (uploads are always cached)")
    assert not B.is_deprecated("display.tool_progress_overrides is deprecated (use display.platforms)")
    assert not B.is_deprecated(None)


def test_source_fields_describe_new_keys_and_report_what_does_not_match():
    keys_file = {"section": "discord", "keys": {
        "allow_bots": {"type": "str", "enum": ["none", "mentions", "all"], "default": "none", "row": True,
                       "description": "Messages from other bots."},
        "gone_key": {"type": "bool", "row": True, "description": "Hermes no longer reads this."}}}
    found = {"allow_bots": {"origin": "discord-adapter"}, "require_mention": {"origin": "discord-adapter"},
             "brand_new": {"origin": "discord-adapter", "env": "DISCORD_BRAND_NEW"},
             "shared_one": {"origin": "platform-shared"}}
    fields, report = B.source_fields({"discord.require_mention"}, keys_file, found)
    by = {f["path"]: f for f in fields}
    assert "discord.require_mention" not in by                    # the export already has it
    bots = by["discord.allow_bots"]
    assert bots["enum"] == ["none", "mentions", "all"] and bots["defaults"] == {B.DEFAULT_LABEL: "none"}
    assert bots["row"] and not bots["in_export"] and bots["origin"] == "discord-adapter"
    assert by["discord.brand_new"]["row"] and "DISCORD_BRAND_NEW" in by["discord.brand_new"]["description"]
    assert not by["discord.shared_one"]["row"]
    assert "discord.gone_key" not in by
    assert any("brand_new" in line for line in report) and any("gone_key" in line for line in report)


def test_without_a_hermes_source_the_described_keys_are_used_as_they_are():
    keys_file = {"keys": {"allow_bots": {"type": "str", "row": True}}}
    fields, report = B.source_fields(set(), keys_file, None)
    assert [f["path"] for f in fields] == ["discord.allow_bots"] and "not checked" in report[0]


def test_the_shipped_schema_knows_the_discord_keys_hermes_reads():
    by = {f["path"]: f for f in PAYLOAD["fields"]}
    bots = by["discord.allow_bots"]
    assert bots["enum"] == ["none", "mentions", "all"] and bots["defaults"]["Hermes defaults"] == "none"
    for key in ("allow_from", "allowed_roles", "allow_all_users", "ignored_channels", "no_thread_channels",
                "reply_to_mode", "allow_mentions.everyone", "allow_mentions.replied_user"):
        assert f"discord.{key}" in by, key
    assert by["discord.allow_any_attachment"]["status"] == "deprecated"
    assert "discord.discord_allow_bots" not in by                 # a misspelling, never a setting
    assert all(f["status"] != "observed-custom" for f in PAYLOAD["fields"])


# ------------------------------------------------------------------ the settings tree
def model_for(datas=()):
    return build_model(PAYLOAD, SCHEMA, union_shape(shape(d) for d in datas))


def test_keys_hermes_reads_get_rows_with_their_values_and_defaults():
    m = model_for()
    bots = m.nodes[("discord", "allow_bots")]
    assert bots.kind == "enum" and bots.options == ("none", "mentions", "all") and bots.default == "none"
    assert not bots.extra and not key_tags(bots)
    group = m.nodes[("discord", "allow_mentions")]
    assert group.is_group and not group.extra
    assert m.nodes[("discord", "allow_mentions", "users")].default is True
    assert ("discord", "typing_status_text") not in m.nodes          # described, but Discord does not read it


def test_a_deprecated_setting_says_it_does_nothing():
    node = model_for().nodes[("discord", "allow_any_attachment")]
    assert node.status == "deprecated"
    assert key_tags(node)[0][0] == "deprecated · no effect"


def test_a_misspelled_key_points_to_the_one_hermes_reads():
    m = model_for([{"discord": {"discord_allow_bots": "mentions", "my_own_knob": 1, "reply_prefix": ">"}}])
    typo = m.nodes[("discord", "discord_allow_bots")]
    assert typo.extra and typo.suggest == ("discord", "allow_bots")
    tag, tip = key_tags(typo)[0]
    assert tag == "unknown to Hermes" and "discord › allow_bots" in tip
    custom = m.nodes[("discord", "my_own_knob")]
    assert custom.extra and custom.suggest is None and key_tags(custom)[0][0] == "not in Hermes' settings list"
    shared = m.nodes[("discord", "reply_prefix")]                   # a real Hermes key: no warning
    assert not shared.extra and not key_tags(shared)


def test_did_you_mean():
    known = ["allow_bots", "allowed_roles", "require_mention"]
    assert did_you_mean("discord_allow_bots", known, ["discord"]) == "allow_bots"
    assert did_you_mean("alow_bots", known) == "allow_bots"
    assert did_you_mean("discord_proxy", known, ["discord"]) is None
    assert did_you_mean("allow_bots", known) is None


# ------------------------------------------------------------------ the move fix
class _Scope:
    def __init__(self, datas):
        self.datas = datas
        self.usable_keys = list(datas)
        self.session = SimpleNamespace(base_of=lambda key: (datas[key], "sha-" + key))

    def draft_value(self, key, path):
        return get_in(self.datas[key], path)


def test_move_keeps_each_agents_own_value_and_never_overwrites():
    old, new = ("discord", "discord_allow_bots"), ("discord", "allow_bots")
    scope = _Scope({
        "a": {"discord": {"discord_allow_bots": "mentions"}},
        "b": {"discord": {"discord_allow_bots": "all", "allow_bots": "all"}},
        "c": {"discord": {"discord_allow_bots": "all", "allow_bots": "none"}},
        "d": {"discord": {"require_mention": True}},
    })
    per_agent, conflicts = move_key_ops(scope, old, new)
    assert conflicts == ["c"] and set(per_agent) == {"a", "b"}
    ops_a = per_agent["a"][2]
    assert [(o.kind, o.path, o.value) for o in ops_a] == [(OpKind.SET, new, "mentions"), (OpKind.REMOVE, old, None)]
    assert [(o.kind, o.path) for o in per_agent["b"][2]] == [(OpKind.REMOVE, old)]
    assert per_agent["a"][1] == "sha-a"
    assert get_in(scope.datas["c"], old) == "all" and get_in(scope.datas["d"], old) is MISSING
