# Changelog

## v0.1.4 — 2026-10-18

- **Backups go into a `config-backups` folder** next to each written file (the main config, each profile, and
  their roster files) instead of lying next to the file. The folder gets a `.gitignore` of `*`, so git never sees
  it.
- **Backups are rotated**: each file keeps its 10 newest backups and every backup younger than 7 days; older
  ones are deleted after a write. Backups that earlier versions left next to a file are moved into the folder
  on its next write and follow the same rule. When old backups cannot be moved or deleted, the write still
  counts and Review says so.
- A removed roster file is kept in the `config-backups` folder (never rotated away).

## v0.1.3 — 2026-10-18

- **Models › Teammates** edits the rosters of the hermes-teammates plugin (`teammates.yaml` next to each
  `config.yaml`): the main agent's master roster and each profile's own roster file, with the same Edit → Stage →
  Review → Write config flow and bulk staging as the model chains. The plugin is optional and third-party, not
  part of Hermes Agent or of this app; without it the page only says that no host uses it.
- Each agent shows the roster it uses, worked out like the plugin does from `roster_file` and `roster_source`
  (`own`, `root`, `fallback`) in its `config.yaml`. A profile that uses the main agent's roster, or keeps its
  roster in `config.yaml` itself, shows it read-only. **Replace roster** gives the checked profiles a copy of the
  master (or of the shown agent's file) exactly as written, and sets their `roster_file` when it is not set;
  **Remove own file** sends a profile with `roster_source: fallback` back to the main agent's roster (the file is
  kept as `teammates.yaml.removed-<time>`).
- Teammates can be added, removed and renamed; a rename keeps the teammate's place and comments and never
  overwrites a teammate of the new name. Cells: description, route and model (model picker), reasoning effort,
  toolsets and instructions (a text box of its own). `max_live_runs` is staged into each checked agent's
  `config.yaml`.
- The plugin's own rules are checked per teammate: names (`^[a-z][a-z0-9_-]{0,31}$`, not a YAML boolean), known
  keys, text and label lengths, toolsets. Teammates the plugin would skip are marked on the page, and Review
  refuses a write that would make a teammate unusable (one already broken on the host is only a warning).
- Where the checked agents differ, an amber dot shows who has what ("use this" takes a value); teammates only
  some of them have are listed too, marked "in k of N".
- The same right-click menu as the model chains: on a cell, "not set", pick another model, edit instructions,
  discard the edit, show who has which value, undo a rename, copy the value or the teammate's name, remove or
  keep the teammate, add one; for the roster, Stage (for the checked agents or the shown one only), Discard,
  Unstage, Replace roster and Remove own file. Right-clicking a text box of the table opens this menu too.
- Restarting gateways after the master is written also restarts the profiles that use it (the plugin reads the
  roster on every call, but a new teammate's name reaches its tools only at a restart).
- The model entry removed from an auxiliary task also takes its `base_url` and `api_mode` (they belonged to
  its provider).

## v0.1.2 — 2026-10-18

- Where the checked agents differ, every "who has what" now names the agents: the amber dot of a chain
  cell (tooltip and popup), the dots of the auxiliary task table, and a setting's details, which also show
  the focused agent's value next to "Use the value of …".
- A chain whose shown agent has no entry (an auxiliary task the agent never set) still marks where the
  other checked agents have one, and its popup tells "no entry" apart from `provider: auto`.
- Reasoning effort: the choice that removes the key from the file is called "not set" (was "default").
- An auxiliary task's only model can be removed: the task's model entry (provider, model, effort,
  fallbacks) leaves the file and Hermes uses its default; its other settings stay.
- An empty value box says "not set" instead of showing nothing.

## v0.1.1 — 2026-10-10

First public release.

- View and change any setting of many Hermes agents' `config.yaml` files (the main config and every
  `profiles/*/config.yaml`) on any number of hosts: local folders, network shares and SSH servers.
- Edit → Stage → Review → Write config. Writing rewrites only the changed keys (comments and layout stay),
  keeps a `config.yaml.bak_…` backup, reads the file back to verify, and never overwrites a file that was
  changed elsewhere in the same settings.
- Model chains (primary model, delegation, every auxiliary task) as provider / model / effort tables;
  staging writes only the edited cells, so each agent keeps its own fallbacks. My defaults; a model list file.
- Settings grouped in categories and pages, with Hermes' own nesting; Customize hides, shows and reorders
  them; search; Related settings.
- The settings list comes from Hermes itself (an empty profile's export, and for Discord the keys its
  adapter reads). Unknown keys are marked; a misspelled one (`discord_allow_bots`) can be moved to the key
  Hermes reads (`allow_bots`) through Review. Deprecated settings say so.
- Gateway status and restart over SSH (`hermes-gateway[-<profile>]` systemd user services), only on request.
- SSH: unknown server keys are never trusted silently; the Hosts screen shows the fingerprint and asks.
  A host's address can be a `~/.ssh/config` alias: its HostName, User, Port and IdentityFile fill the
  fields left empty.
- Light and dark theme; fits half of a 1920-pixel screen.
