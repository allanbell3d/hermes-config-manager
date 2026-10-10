from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_config_manager.transports.base import ConflictError, Revision, TransportError, discover_config_paths
from hermes_config_manager.transports.hosts import (
    ACCESS_MOCK,
    HostConfig,
    HostConfigError,
    dump_hosts_toml,
    load_registry,
    parse_hosts_toml,
    save_registry,
)
from hermes_config_manager.transports.local import LocalTransport
from hermes_config_manager.transports.mock import MockTransport


def make_tree(root):
    (root / "profiles" / "alpha").mkdir(parents=True)
    (root / "profiles" / "beta").mkdir(parents=True)
    (root / "profiles" / "empty").mkdir(parents=True)
    (root / "config.yaml").write_text("model:\n  model: a\n", encoding="utf-8")
    (root / "profiles" / "alpha" / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    (root / "profiles" / "beta" / "config.yaml").write_text("x: 2\r\n", encoding="utf-8", newline="")


def test_local_discovery_and_read(tmp_path):
    make_tree(tmp_path)
    t = LocalTransport(HostConfig("h", hermes_root=str(tmp_path)))
    found = discover_config_paths(t, str(tmp_path), ("config.yaml", "profiles/*/config.yaml"))
    assert [name for name, _ in found] == ["", "alpha", "beta"]
    data = t.read_bytes(found[2][1])
    assert data.data == b"x: 2\r\n"  # bytes untouched (CRLF preserved)


def test_local_write_checked_backup_and_conflict(tmp_path):
    make_tree(tmp_path)
    path = str(tmp_path / "config.yaml")
    t = LocalTransport(HostConfig("h", hermes_root=str(tmp_path)))
    before = t.read_bytes(path)
    result = t.write_checked(path, b"model:\n  model: b\n", before.revision.sha256)
    assert result.verified and result.backup_path and os.path.exists(result.backup_path)
    assert open(result.backup_path, "rb").read() == before.data
    # Stale expectation => conflict, file untouched.
    with pytest.raises(ConflictError):
        t.write_checked(path, b"model:\n  model: c\n", before.revision.sha256)
    assert open(path, "rb").read() == b"model:\n  model: b\n"
    # No leftover temp files.
    assert not [p for p in os.listdir(tmp_path) if p.endswith(".tmp")]


def test_local_probe_missing_drive():
    t = LocalTransport(HostConfig("h", hermes_root=r"Q:\definitely\missing\hermes"))
    with pytest.raises(TransportError) as info:
        t.probe()
    assert info.value.kind in ("unavailable", "not_found")


def mock_host(fs_files):
    host = HostConfig("m", access_mode=ACCESS_MOCK, hermes_root="/home/u/.hermes")
    t = MockTransport(host)
    for path, text in fs_files.items():
        t.fs.put(path, text)
    return t


def test_mock_faults_and_disconnect():
    t = mock_host({"/home/u/.hermes/config.yaml": "a: 1\n", "/home/u/.hermes/profiles/p/config.yaml": "b: 2\n"})
    found = discover_config_paths(t, "/home/u/.hermes", ("config.yaml", "profiles/*/config.yaml"))
    assert [n for n, _ in found] == ["", "p"]
    t.inject("write", "permission", path_contains="profiles/p", times=1)
    rev = t.read_bytes(found[1][1]).revision.sha256
    with pytest.raises(TransportError) as info:
        t.write_checked(found[1][1], b"b: 3\n", rev)
    assert info.value.kind == "permission"
    # Backup was made before the failing write; original content intact.
    assert t.fs.files[found[1][1]] == b"b: 2\n"
    assert any(".bak_" in p for p in t.fs.files)
    t.connected = False
    with pytest.raises(TransportError) as info:
        t.read_bytes(found[0][1])
    assert info.value.kind == "unavailable"


def test_revision_identity():
    assert Revision.of(b"abc").sha256 == Revision.of(b"abc").sha256
    assert Revision.of(b"abc").sha256 != Revision.of(b"abd").sha256


SMB_HOST = HostConfig("studio", hermes_root=r"\\nas\hermes\.hermes", ssh_hostname="nas.example", ssh_user="hermes")


def test_hosts_toml_round_trip_and_validation(tmp_path):
    hosts = [SMB_HOST, HostConfig("remote", "ssh", "/home/u/.hermes", "10.0.0.2", "u", None, 2222, timeout=5.0)]
    assert parse_hosts_toml(dump_hosts_toml(hosts)) == hosts
    with pytest.raises(HostConfigError):
        parse_hosts_toml('[[hosts]]\nname = "a"\n[[hosts]]\nname = "a"\n')
    with pytest.raises(HostConfigError):
        parse_hosts_toml('[[hosts]]\nname = "a"\naccess_mode = "ftp"\n')


def test_registry_empty_on_first_run_then_saved(tmp_path):
    reg = load_registry()  # HERMES_CONFIG_MANAGER_HOME points into tmp (conftest)
    assert reg.source == "empty" and reg.hosts == []
    save_registry([SMB_HOST])
    reg = load_registry()
    assert reg.source == "app" and [h.name for h in reg.hosts] == ["studio"]


# ------------------------------------------------------------------ SSH server keys
def test_an_unknown_server_key_is_refused_with_its_fingerprint_until_trusted(tmp_path):
    import paramiko

    from hermes_config_manager.transports.ssh import UnknownHostKey, _ask_first_policy, fingerprint, trust_host_key

    key = paramiko.RSAKey.generate(1024)
    with pytest.raises(UnknownHostKey) as caught:
        _ask_first_policy().missing_host_key(None, "[server.example]:2222", key)
    err = caught.value
    assert err.entry == "[server.example]:2222" and err.key_type == "ssh-rsa"
    assert err.fingerprint == fingerprint(key.asbytes()) and err.fingerprint.startswith("SHA256:")
    assert "=" not in err.fingerprint and err.fingerprint in str(err)
    known = tmp_path / ".ssh" / "known_hosts"
    known.parent.mkdir()
    known.write_bytes(b"other.example ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl")
    trust_host_key(err, known)                       # appended on a new line, nothing else touched
    lines = known.read_text(encoding="ascii").splitlines()
    assert lines[0].startswith("other.example ") and lines[1].startswith("[server.example]:2222 ssh-rsa ")
    assert paramiko.HostKeys(str(known)).check("[server.example]:2222", key)


# ------------------------------------------------------------------ ~/.ssh/config
SSH_CONFIG = """Host box
    HostName 10.1.2.3
    User alice
    Port 2200
    IdentityFile {keys}/id_a
    IdentityFile {keys}/id_missing
    IdentitiesOnly yes

Host hop
    HostName 10.9.9.9
    ProxyJump box

Host *
    ServerAliveInterval 30
"""


def _ssh_config(tmp_path):
    (tmp_path / "id_a").write_text("not a real key", encoding="ascii")
    path = tmp_path / "config"
    path.write_text(SSH_CONFIG.format(keys=tmp_path.as_posix()), encoding="ascii")
    return path


def test_an_alias_takes_address_user_port_and_keys_from_ssh_config(tmp_path):
    from hermes_config_manager.transports.ssh import connect_kwargs, resolve_endpoint

    cfg = _ssh_config(tmp_path)
    ep = resolve_endpoint(HostConfig("srv", "ssh", "/home/x/.hermes", "box"), cfg)
    assert (ep.hostname, ep.user, ep.port) == ("10.1.2.3", "alice", 2200)
    assert [Path(k).name for k in ep.keys] == ["id_a"]               # files that do not exist are skipped
    assert ep.identities_only and ep.from_config and ep.describe() == "alice@10.1.2.3:2200 (from ~/.ssh/config)"
    kw = connect_kwargs(ep, 20.0)
    assert kw["hostname"] == "10.1.2.3" and kw["port"] == 2200 and kw["username"] == "alice"
    assert kw["look_for_keys"] is False and [Path(k).name for k in kw["key_filename"]] == ["id_a"]


def test_fields_filled_in_the_app_win_over_ssh_config(tmp_path):
    from hermes_config_manager.transports.ssh import resolve_endpoint

    cfg = _ssh_config(tmp_path)
    key = tmp_path / "mine"
    key.write_text("x", encoding="ascii")
    ep = resolve_endpoint(HostConfig("srv", "ssh", "/r", "box", "bob", str(key), 2222), cfg)
    assert (ep.hostname, ep.user, ep.port, ep.keys) == ("10.1.2.3", "bob", 2222, (str(key),))
    plain = resolve_endpoint(HostConfig("p", "ssh", "/r", "plain.example", "u"), cfg)
    assert (plain.hostname, plain.port, plain.keys, plain.from_config) == ("plain.example", 22, (), False)
    assert resolve_endpoint(HostConfig("hop", "ssh", "/r", "hop"), cfg).proxy_jump == "box"


def test_ssh_commands_pass_the_alias_and_only_the_fields_set_in_the_app():
    from hermes_config_manager.transports.ssh import ssh_command_line

    alias_only = ssh_command_line(HostConfig("srv", "ssh", "/r", "box"), "true")
    assert alias_only[-2:] == ["box", "true"] and "-p" not in alias_only and "-l" not in alias_only
    full = ssh_command_line(HostConfig("x", "ssh", "/r", "10.0.0.2", "u", None, 2222), "true")
    assert full[full.index("-p") + 1] == "2222" and full[full.index("-l") + 1] == "u" and full[-2] == "10.0.0.2"
    assert HostConfig("srv", "ssh", "/r", "box").can_run_commands         # no user needed: ssh/config supplies it
