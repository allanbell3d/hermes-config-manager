"""The hermes-teammates plugin's rules for a roster (``teammates.yaml``, one top-level ``teammates`` mapping).

Unlike a file that is accepted or rejected as a whole, the plugin checks each teammate on its own: a teammate
with a bad name is skipped, one with an unknown key, a wrong type or bad toolsets cannot be launched, and the
rest of the roster still works.  The editor checks with the same rules (keep in step with the plugin's
``teammates_config.parse`` and ``teammates_host.load_roster_file``) and refuses to write a roster that would
make a teammate unusable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FIELDS = ("description", "route", "model", "reasoning_effort", "toolsets", "instructions")
TEXTS = {"description": 300, "instructions": 4000}         # clipped to this length (a warning, still usable)
LABELS = ("route", "model", "reasoning_effort")             # a string of at most MAX_LABEL characters, or unset
MAX_LABEL = 100
MAX_TOOLSETS = 24
TOP = "teammates"

NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
TOOLSET_RE = re.compile(r"[!-~]{1,48}")
YAML_WORDS = ("yes", "no", "on", "off", "true", "false", "null", "y", "n")


def bad_name(name: Any) -> str | None:
    """Why ``name`` is not a teammate name, or None.  Words like ``yes`` match the plugin's pattern but a YAML
    loader reads them as a boolean, so the plugin would skip that teammate."""
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        return "a teammate name is 1-32 lowercase letters, digits, '-' and '_', starting with a letter"
    if name in YAML_WORDS:
        return f"'{name}' reads as a boolean or null in YAML"
    return None


def unset(value: Any) -> bool:
    """route / model / reasoning_effort: empty strings count as not set."""
    return value is None or (isinstance(value, str) and not value.strip())


def toolsets_problem(value: Any) -> str | None:
    if value is None:
        return None
    ok = isinstance(value, list) and 0 < len(value) <= MAX_TOOLSETS and \
        all(isinstance(t, str) and TOOLSET_RE.fullmatch(t.strip()) for t in value)
    return None if ok else (f"toolsets: 1-{MAX_TOOLSETS} names, each 1-48 printable characters without spaces "
                            "(leave out to inherit the agent's)")


def teammate_problems(values: Any) -> list[str]:
    """What makes one teammate unusable for the plugin (empty list: fine)."""
    if not isinstance(values, dict):
        return ["not a mapping"]
    out = [f"unknown key '{k}'" for k in values if k not in FIELDS]
    for k in TEXTS:
        if k in values and not isinstance(values[k], str):
            out.append(f"{k} must be text")
    for k in LABELS:
        v = values.get(k)
        if v is not None and not isinstance(v, str):
            out.append(f"{k} must be text")
        elif isinstance(v, str) and len(v) > MAX_LABEL:
            out.append(f"{k} is longer than {MAX_LABEL} characters")
    why = toolsets_problem(values.get("toolsets"))
    if why:
        out.append(why)
    return out


def teammate_warnings(values: Any) -> list[str]:
    """What the plugin accepts but changes (texts clipped)."""
    if not isinstance(values, dict):
        return []
    return [f"{k} is clipped to {n} characters" for k, n in TEXTS.items()
            if isinstance(values.get(k), str) and len(values[k]) > n]


@dataclass
class Check:
    """A roster checked like the plugin does."""

    problem: str | None = None                                  # the whole roster is unusable (empty)
    invalid: dict[str, list[str]] = field(default_factory=dict)  # teammate -> why it is skipped / unusable
    warnings: list[str] = field(default_factory=list)


def check(data: Any) -> Check:
    if not isinstance(data, dict):
        return Check("the file is empty or not a YAML mapping")
    roster = data.get(TOP)
    if not isinstance(roster, dict):
        return Check(f"the file needs a top-level '{TOP}' mapping")
    out = Check()
    extra = sorted(str(k) for k in data if k != TOP)
    if extra:
        out.warnings.append(f"unknown top-level key(s) {extra} (ignored by the plugin)")
    for name, values in roster.items():
        why = bad_name(name)
        probs = [why] if why else teammate_problems(values)
        if probs:
            out.invalid[str(name)] = probs
        out.warnings += [f"{name}: {w}" for w in teammate_warnings(values)]
    return out


def review(target: Any, current: Any) -> tuple[str | None, list[str]]:
    """Whether a roster may be written: (why not | None, warnings).

    A write must not make a teammate unusable.  Teammates that were already unusable on the host are only
    warned about, so they never block an unrelated edit.
    """
    after = check(target)
    if after.problem:
        return after.problem, []
    before = check(current).invalid if isinstance(current, dict) else {}
    new = {n: p for n, p in after.invalid.items() if before.get(n) != p}
    if new:
        return "the plugin would skip " + "; ".join(f"teammate '{n}' ({p[0]})" for n, p in new.items()), []
    old = [f"teammate '{n}' is skipped by the plugin, as before ({p[0]})" for n, p in after.invalid.items()]
    return None, old + after.warnings
