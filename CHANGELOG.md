# Changelog

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
