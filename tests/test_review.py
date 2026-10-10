"""Review grouping and the prepare -> apply flow on disposable local copies."""

from __future__ import annotations

from pathlib import Path

from hermes_config_manager.core.yamlpatch import fast_load
from hermes_config_manager.services import apply as A
from hermes_config_manager.services.editing import EditScope
from hermes_config_manager.services.review import ApplyFlow
from tests.conftest import pump


def _scope(session):
    keys = session.ordered_keys()
    return EditScope(session, keys, keys[0])


def test_prepare_and_apply_writes_backups_and_clears_drafts(qapp, session):
    scope = _scope(session)
    scope.set(("compression", "threshold"), 0.71)
    flow = ApplyFlow(session)
    got = {}
    flow.prepared.connect(lambda items: got.__setitem__("prepared", items))
    flow.finished.connect(lambda results: got.__setitem__("results", results))
    flow.restarted.connect(lambda results: got.__setitem__("restarted", results))
    flow.prepare(scope.keys)
    assert pump(qapp, lambda: "prepared" in got)
    assert all(p.status == A.READY for p in got["prepared"]), [p.message for p in got["prepared"]]
    flow.apply(got["prepared"], restart_gateways=True)
    assert pump(qapp, lambda: "results" in got and "restarted" in got)
    assert {r.status for r in got["results"]} == {A.WRITTEN}
    for key in scope.keys:
        rec = session.record(key)
        on_disk = fast_load(Path(rec.path).read_text(encoding="utf-8"))
        assert on_disk["compression"]["threshold"] == 0.71
        assert list(Path(rec.path).parent.glob("config.yaml.bak_*")), "backup missing"
    assert not session.drafts.has_changes()
    # no SSH endpoint configured: restart is reported, never attempted silently
    assert got["restarted"] and not any(r.ok for r in got["restarted"])
