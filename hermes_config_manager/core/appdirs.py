"""Where the application keeps its own files (never inside Hermes hosts).

``HERMES_CONFIG_MANAGER_HOME`` overrides everything (used by tests and benchmarks).
Otherwise settings, hosts and your defaults live in ``%APPDATA%\\HermesConfigManager`` and
the disposable cache in ``%LOCALAPPDATA%\\HermesConfigManager\\cache``.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "HermesConfigManager"


def _override() -> Path | None:
    value = os.environ.get("HERMES_CONFIG_MANAGER_HOME")
    return Path(value) if value else None


def config_dir() -> Path:
    base = _override()
    if base is None:
        base = Path(os.environ.get("APPDATA") or Path.home() / ".config") / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def cache_dir() -> Path:
    base = _override()
    if base is not None:
        path = base / "cache"
    else:
        path = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / APP_NAME / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = cache_dir().parent / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def hosts_file() -> Path:
    return config_dir() / "hosts.toml"


def settings_file() -> Path:
    return config_dir() / "settings.ini"


def exports_dir() -> Path:
    path = config_dir() / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path
