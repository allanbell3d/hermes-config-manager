"""Shared pytest configuration.

The whole suite is offline: temporary copies, generated fixtures and mocked
transports only.  Nothing here may touch real Hermes hosts (local, network or SSH)
or restart gateways.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLES_ROOT = ROOT / "tests" / "fixtures" / "home"     # generated, generic configs (not real agents)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_user_dirs(tmp_path, monkeypatch):
    """Keep tests away from the operator's settings, cache, presets and hosts."""
    monkeypatch.setenv("HERMES_CONFIG_MANAGER_HOME", str(tmp_path / "hm-home"))
    yield


@pytest.fixture(autouse=True)
def qt_teardown():
    """Deterministic Qt cleanup after every test (what pytest-qt's qtbot does).

    Without it, windows/dialogs left open by one test are destroyed by Python's
    garbage collector at an arbitrary later moment - possibly in the middle of
    another test's event processing, which crashes Qt intermittently.
    """
    yield
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


PROFILES = ["alpha", "beta", "gamma"]


def pump(app, predicate, timeout=10.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture()
def hermes_home(tmp_path) -> Path:
    """Disposable copy of the sample main config and three profiles."""
    import shutil

    root = tmp_path / "hermes"
    (root / "profiles").mkdir(parents=True)
    shutil.copy(SAMPLES_ROOT / "config.yaml", root / "config.yaml")
    for name in PROFILES:
        (root / "profiles" / name).mkdir()
        shutil.copy(SAMPLES_ROOT / "profiles" / name / "config.yaml", root / "profiles" / name / "config.yaml")
    return root


@pytest.fixture()
def session(qapp, hermes_home):
    """Session over ``hermes_home`` (local transport), fully loaded."""
    from hermes_config_manager.services.session import Session
    from hermes_config_manager.transports.hosts import HostConfig

    s = Session(hosts=[HostConfig("local", hermes_root=str(hermes_home))], use_cache=False)
    s.refresh_all()
    assert pump(qapp, lambda: len(s.agents) == 4 and not s.is_refreshing())
    assert all(r.usable for r in s.agents.values())
    yield s
    s.shutdown()


MALFORMED_ROOT = ROOT / "tests" / "fixtures" / "malformed"   # broken files, never loaded as agents


def sample_files() -> list[Path]:
    return sorted(SAMPLES_ROOT.glob("**/config.yaml")) + sorted(MALFORMED_ROOT.glob("**/config.yaml"))
