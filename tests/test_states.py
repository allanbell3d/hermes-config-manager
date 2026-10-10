"""EditScope.states(paths) must equal state(path) for every path (it is only a faster form)."""

from __future__ import annotations

from hermes_config_manager.core.agents import Status
from hermes_config_manager.core.paths import iter_leaves
from hermes_config_manager.services.editing import EditScope, canonical


def _same(a, b) -> bool:
    return (a.path == b.path and a.targets == b.targets and a.unavailable == b.unavailable
            and {k: canonical(v) for k, v in a.values.items()} == {k: canonical(v) for k, v in b.values.items()}
            and {k: canonical(v) for k, v in a.base_values.items()} == {k: canonical(v) for k, v in b.base_values.items()}
            and [(canonical(v), ks) for v, ks in a.groups] == [(canonical(v), ks) for v, ks in b.groups]
            and sorted(a.changed) == sorted(b.changed))


def test_states_equals_state(session):
    keys = session.ordered_keys()
    scope = EditScope(session, keys, keys[1])
    scope.set(("compression", "threshold"), 0.42)
    scope.remove(("display", "skin"))
    EditScope(session, keys[:1], keys[0]).set(("agent", "max_turns"), 7)
    session.record(keys[-1]).status = Status.ERROR          # one agent not loaded
    paths = [p for p, _ in iter_leaves(session.record(keys[0]).data) if p and not any(isinstance(x, int) for x in p)]
    paths += [("compression", "threshold"), ("display", "skin"), ("agent", "max_turns"), ("no", "such", "key")]
    fast = scope.states(paths)
    for path in paths:
        assert _same(fast[path], scope.state(path)), path
