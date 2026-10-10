"""Gateway status and restart (systemd user services over SSH).

The gateway naming this app relies on:
the main agent's gateway is ``hermes-gateway``, a profile's is
``hermes-gateway-<profile>``; both are ``systemctl --user`` units on the host.
Commands run through the OpenSSH client with ``BatchMode=yes`` (existing keys
only; never a password prompt).

Status queries are read-only.  Restarts are only ever triggered by an
explicit user action (or the explicit "restart after apply" option).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..transports.base import Transport, TransportError
from ..transports.ssh import quote

_UNIT_OK = re.compile(r"^[A-Za-z0-9_.@\-]+$")


def service_name(profile: str) -> str:
    return "hermes-gateway" if not profile else f"hermes-gateway-{profile}"


def _check_unit(unit: str) -> str:
    if not _UNIT_OK.match(unit):
        raise TransportError("invalid", f"Refusing unusual service name {unit!r}")
    return unit


@dataclass
class GatewayState:
    profile: str
    unit: str
    state: str          # active | inactive | failed | activating | unknown | not-installed | error
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "active"


def query_status(task_ctx, transport: Transport, profiles: list[str], timeout: float = 20.0) -> list[GatewayState]:
    """``systemctl --user is-active`` for each unit (read-only, one SSH call)."""
    units = [_check_unit(service_name(p)) for p in profiles]
    if not units:
        return []
    command = "systemctl --user is-active " + " ".join(quote(u) for u in units) + "; true"
    result = transport.run(command, timeout=timeout)
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    states: list[GatewayState] = []
    for i, (profile, unit) in enumerate(zip(profiles, units)):
        raw = lines[i] if i < len(lines) else "unknown"
        # Note: "inactive" is also what systemd reports for a unit that does not
        # exist; unit_exists() distinguishes the two when the user asks.
        state = raw if raw in ("active", "inactive", "failed", "activating", "deactivating", "reloading") else "unknown"
        states.append(GatewayState(profile, unit, state, raw))
    if len(lines) < len(units) and result.stderr.strip():
        for st in states[len(lines):]:
            st.state, st.detail = "error", result.stderr.strip()[:200]
    return states


def unit_exists(task_ctx, transport: Transport, profile: str, timeout: float = 15.0) -> bool:
    unit = _check_unit(service_name(profile))
    res = transport.run(f"systemctl --user cat {quote(unit)} >/dev/null 2>&1 && echo yes || echo no", timeout=timeout)
    return res.stdout.strip().endswith("yes")


@dataclass
class RestartResult:
    profile: str
    unit: str
    ok: bool
    message: str


def restart(task_ctx, transport: Transport, profile: str, timeout: float = 45.0) -> RestartResult:
    """Restart one gateway and confirm it is active again."""
    unit = _check_unit(service_name(profile))
    command = f"systemctl --user restart {quote(unit)} && systemctl --user is-active {quote(unit)}"
    try:
        res = transport.run(command, timeout=timeout)
    except TransportError as exc:
        return RestartResult(profile, unit, False, str(exc))
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if res.returncode == 0 and out.endswith("active"):
        return RestartResult(profile, unit, True, "restarted; service is active")
    if "not found" in err or "not loaded" in err or "could not be found" in err:
        return RestartResult(profile, unit, False, "no systemd gateway service is installed for this agent")
    return RestartResult(profile, unit, False, (err or out or f"exit code {res.returncode}")[:300])
