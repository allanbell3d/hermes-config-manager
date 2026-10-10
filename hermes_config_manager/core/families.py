"""The parameter family tree: where every top-level ``config.yaml`` key lives.

Hermes' own dashboard makes one category per top-level key of
``DEFAULT_CONFIG`` (model, terminal, display, agent, delegation, memory,
approvals, ...).  A *family* here is exactly that: one top-level key, shown
under the name Hermes uses.  Two things are added for navigation:

* families are arranged in a few *groups* (about 140 flat entries cannot be
  scanned);
* top-level *scalars* (``max_concurrent_sessions``, ``timezone``, ...) are
  attached to the family they belong to instead of a catch-all "other".

Keys that no group claims (custom sections such as a hand-written ``tts``
variant, or keys of a newer Hermes) become families of their own in the
"Custom sections" group - never hidden, never merged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# (group id, group label, [(family key, label, [attached top-level keys])])
_TREE: list[tuple[str, str, list[tuple[str, str, list[str]]]]] = [
    ("models", "Models", [
        ("model", "Primary model", ["fallback_providers", "fallback", "fallback_model", "smart_model_routing",
                                    "model_context_length"]),
        ("delegation", "Delegation", []),
        ("auxiliary", "Auxiliary models", []),
        ("moa", "Mixture of agents", []),
        ("x_search", "X search", []),
        ("image_gen", "Image generation", []),
        ("video_gen", "Video generation", []),
        ("vision", "Vision", []),
        ("model_aliases", "Model aliases", []),
        ("model_overrides", "Model overrides", []),
    ]),
    ("providers", "Providers", [
        ("providers", "Providers", []),
        ("custom_providers", "Custom providers", []),
        ("credential_pool_strategies", "Credential pools", []),
        ("openrouter", "OpenRouter", []),
        ("bedrock", "Bedrock", []),
        ("vertex", "Vertex", []),
        ("nous", "Nous", []),
        ("local_runtime", "Local runtime", []),
        ("model_catalog", "Model catalog", []),
        ("models_dev", "models.dev", []),
        ("prompt_caching", "Prompt caching", []),
        ("auth", "Auth", []),
    ]),
    ("agent", "Agent", [
        ("agent", "Agent", []),
        ("context", "Context", ["context_file_max_chars", "context_file_read_timeout", "file_read_max_chars",
                                "prefill_messages_file"]),
        ("compression", "Compression", []),
        ("memory", "Memory", []),
        ("goals", "Goals", []),
        ("loops", "Loops", []),
        ("curator", "Curator", []),
        ("honcho", "Honcho", []),
        ("lcm", "LCM", []),
        ("personalities", "Personalities", []),
        ("human_delay", "Human delay", []),
        ("checkpoints", "Checkpoints", []),
    ]),
    ("tools", "Tools", [
        ("toolsets", "Toolsets", ["known_builtin_toolsets", "known_plugin_toolsets", "platform_toolsets",
                                  "tool_gateway_declined_tools"]),
        ("tools", "Tools", []),
        ("terminal", "Terminal", []),
        ("browser", "Browser", []),
        ("web", "Web", []),
        ("code_execution", "Code execution", []),
        ("computer_use", "Computer use", []),
        ("tool_output", "Tool output", []),
        ("tool_budget", "Tool budget", []),
        ("tool_loop_guardrails", "Tool loop guardrails", []),
        ("mcp", "MCP", ["mcp_discovery_timeout", "mcp_single_query_discovery_timeout"]),
        ("mcp_servers", "MCP servers", []),
        ("skills", "Skills", []),
        ("plugins", "Plugins", []),
        ("hooks", "Hooks", ["hooks_auto_accept"]),
        ("lsp", "LSP", []),
        ("worktree", "Worktree", ["worktree_sync"]),
        ("quick_commands", "Quick commands", []),
        ("a2a_agents", "A2A agents", []),
        ("kanban", "Kanban", []),
        ("cron", "Cron", []),
    ]),
    ("interface", "Interface", [
        ("display", "Display", ["paste_collapse_threshold", "paste_collapse_threshold_fallback",
                                "paste_collapse_char_threshold"]),
        ("dashboard", "Dashboard", []),
        ("desktop", "Desktop", []),
        ("bot_desktop", "Bot desktop", []),
        ("streaming", "Streaming", []),
        ("attachments", "Attachments", []),
        ("onboarding", "Onboarding", []),
    ]),
    ("messaging", "Gateway & messaging", [
        ("gateway", "Gateway", []),
        ("sessions", "Sessions", ["max_concurrent_sessions", "max_live_sessions", "group_sessions_per_user",
                                  "thread_sessions_per_user", "reset_triggers"]),
        ("session", "Session", []),
        ("session_reset", "Session reset", []),
        ("bot_mode", "Bot mode", ["require_mention", "bots_require_mention", "unauthorized_dm_behavior",
                                  "filter_silence_narration"]),
        ("platforms", "Platforms", []),
        ("platform_hints", "Platform hints", []),
        ("channel_prompts", "Channel prompts", []),
        ("multiplex_profiles", "Multiplex profiles", []),
        ("profile_routes", "Profile routes", []),
        ("server_actions", "Server actions", []),
        ("slack", "Slack", []),
        ("discord", "Discord", []),
        ("telegram", "Telegram", []),
        ("whatsapp", "WhatsApp", []),
        ("signal", "Signal", []),
        ("matrix", "Matrix", []),
        ("mattermost", "Mattermost", []),
        ("email", "Email", []),
        ("sms", "SMS", []),
        ("bluebubbles", "BlueBubbles", []),
        ("dingtalk", "DingTalk", []),
        ("feishu", "Feishu", []),
        ("wecom", "WeCom", []),
        ("weixin", "Weixin", []),
        ("qqbot", "QQ bot", []),
        ("yuanbao", "Yuanbao", []),
    ]),
    ("voice", "Voice", [
        ("tts", "Text to speech", []),
        ("stt", "Speech to text", ["stt_echo_transcripts"]),
        ("voice", "Voice", []),
        ("wake_word", "Wake word", []),
    ]),
    ("safety", "Safety", [
        ("approvals", "Approvals", ["command_allowlist"]),
        ("security", "Security", []),
        ("privacy", "Privacy", []),
        ("secrets", "Secrets", []),
        ("vault", "Vault", []),
        ("proxy", "Proxy", []),
    ]),
    ("system", "System", [
        ("runtime", "Runtime", ["_config_version", "timezone"]),
        ("database", "Database", []),
        ("network", "Network", []),
        ("timeouts", "Timeouts", []),
        ("logging", "Logging", ["always_log_local"]),
        ("monitoring", "Monitoring", []),
        ("telemetry", "Telemetry", []),
        ("doctor", "Doctor", []),
        ("updates", "Updates", []),
    ]),
]

CUSTOM_GROUP = ("custom", "Custom sections")

# Families with a dedicated editor (model chains); every other family uses the generic one.
MODEL_FAMILIES = ("model", "delegation", "auxiliary")


@dataclass(frozen=True)
class Family:
    key: str                       # the top-level key Hermes uses ("display")
    label: str                     # friendly label ("Display")
    group: str                     # group id ("interface")
    keys: tuple[str, ...]          # top-level keys shown here: (key, *attached)
    custom: bool = False           # not known to the schema/tree (user or newer-Hermes section)

    @property
    def dedicated(self) -> bool:
        return self.key in MODEL_FAMILIES


@dataclass
class FamilyTree:
    groups: list[tuple[str, str]]                       # (id, label) in display order
    families: list[Family] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._by_key = {f.key: f for f in self.families}
        self._owner = {k: f for f in self.families for k in f.keys}

    def family(self, key: str) -> Family | None:
        return self._by_key.get(key)

    def owner(self, top_key: str) -> Family | None:
        """The family that shows top-level key ``top_key``."""
        return self._owner.get(top_key)

    def in_group(self, group: str) -> list[Family]:
        return [f for f in self.families if f.group == group]


def known_top_keys() -> set[str]:
    return {k for _, _, fams in _TREE for key, _, extra in fams for k in (key, *extra)}


def build_tree(present_top_keys: Iterable[str] = (), schema_top_keys: Iterable[str] = ()) -> FamilyTree:
    """The full tree plus one custom family per unclaimed top-level key.

    ``present_top_keys``: keys found in the loaded agents' configs.
    ``schema_top_keys``: keys documented by the runtime schema; a documented key
    the tree does not place (newer Hermes) is listed under its own name too.
    """
    families: list[Family] = []
    for group, _label, fams in _TREE:
        for key, label, extra in fams:
            families.append(Family(key, label, group, (key, *extra)))
    claimed = known_top_keys()
    extra_keys: list[str] = []
    for key in list(schema_top_keys) + list(present_top_keys):
        if isinstance(key, str) and key not in claimed and key not in extra_keys:
            extra_keys.append(key)
    for key in sorted(extra_keys, key=str.casefold):
        families.append(Family(key, key, CUSTOM_GROUP[0], (key,), custom=True))
    groups = [(g, label) for g, label, _ in _TREE] + [CUSTOM_GROUP]
    return FamilyTree(groups, families)

