"""Host access: local/SMB/UNC paths, SSH/SFTP and an in-memory mock."""

from __future__ import annotations

from .base import ConflictError, Revision, Transport, TransportError
from .hosts import ACCESS_LOCAL, ACCESS_MOCK, ACCESS_SSH, HostConfig


def transport_for(host: HostConfig) -> Transport:
    if host.access_mode == ACCESS_SSH:
        from .ssh import SSHTransport

        return SSHTransport(host)
    if host.access_mode == ACCESS_MOCK:
        raise ValueError("Mock hosts need an explicit MockTransport instance")
    from .local import LocalTransport

    return LocalTransport(host)


__all__ = ["ACCESS_LOCAL", "ACCESS_MOCK", "ACCESS_SSH", "ConflictError", "HostConfig", "Revision", "Transport",
           "TransportError", "transport_for"]
