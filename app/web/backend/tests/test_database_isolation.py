"""Agent code must not reach the database: no settings in its environment, no readable password, no route to port 5432.

The network and file tests need the image (root, the `agent` user, the entrypoint's firewall rule: run the container
with NET_ADMIN) and a reachable test database; they skip anywhere else.
"""
import asyncio
import os
import socket
import subprocess

import pytest
from sqlalchemy.engine import make_url

import sandbox
from conftest import TEST_DATABASE_URL

DB_NAMES = ("LDRAW_NOVA_DB_HOST", "LDRAW_NOVA_DB_PORT", "LDRAW_NOVA_DB_NAME", "LDRAW_NOVA_DB_USER",
            "LDRAW_NOVA_DB_PASSWORD_FILE", "LDRAW_NOVA_DATABASE_URL")


def test_no_database_setting_is_passed_to_agent_code():
    assert not [n for n in sandbox.PASSTHROUGH_ENV if "DB" in n or "DATABASE" in n or "PASSWORD" in n]


def test_agent_environment_never_contains_database_settings(monkeypatch, tmp_path):
    for name in DB_NAMES:
        monkeypatch.setenv(name, "secret-value-" + name)
    result = asyncio.run(sandbox.run(["env"], tmp_path / "work", 10))
    assert result.exit_code == 0
    assert "secret-value" not in result.stdout and not any(n in result.stdout for n in DB_NAMES)


def _as_agent(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(["setpriv", "--reuid=agent", "--regid=agent", "--clear-groups", "python3", "-c", code],
                          capture_output=True, text=True, timeout=30)


def _in_the_image():
    if os.geteuid() != 0 or not TEST_DATABASE_URL or subprocess.run(["id", "agent"], capture_output=True).returncode != 0:
        pytest.skip("needs the container image (root + agent user) and LDRAW_NOVA_TEST_DATABASE_URL")
    state = sandbox.isolation_status()
    if state["state"] != "on":
        pytest.skip(f"the entrypoint's firewall rule is not active ({state['detail']}); run the container with NET_ADMIN")


PROBE = """
import socket, sys
try:
    socket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=5).close()
    print("CONNECTED")
except OSError as exc:
    print("REFUSED", type(exc).__name__)
"""


def test_agent_user_cannot_connect_to_the_database_but_the_server_can():
    _in_the_image()
    url = make_url(TEST_DATABASE_URL)
    host, port = url.host, url.port or 5432
    assert port == 5432
    with socket.create_connection((host, port), timeout=5):          # root (the server): the database is reachable
        pass
    run = subprocess.run(["setpriv", "--reuid=agent", "--regid=agent", "--clear-groups", "python3", "-c", PROBE, host, str(port)],
                         capture_output=True, text=True, timeout=30)
    assert "REFUSED" in run.stdout and "CONNECTED" not in run.stdout, run.stdout + run.stderr
    # Any destination, not only the database's address: the loopback and the Docker host are refused too.
    for address in ("127.0.0.1", socket.gethostbyname(host)):
        other = subprocess.run(["setpriv", "--reuid=agent", "--regid=agent", "--clear-groups", "python3", "-c", PROBE, address, "5432"],
                               capture_output=True, text=True, timeout=30)
        assert "CONNECTED" not in other.stdout


def test_the_isolation_report_names_the_database_port():
    """The entrypoint reports what it blocked (the server no longer holds NET_ADMIN, so it cannot list the rules itself)."""
    _in_the_image()
    assert "database port 5432 blocked" in sandbox.isolation_status()["detail"]


def test_agent_user_cannot_read_the_password_file(tmp_path):
    if os.geteuid() != 0 or subprocess.run(["id", "agent"], capture_output=True).returncode != 0:
        pytest.skip("needs root and the agent user")
    folder = tmp_path / "secrets"
    folder.mkdir()
    os.chmod(tmp_path, 0o755)
    os.chmod(folder, 0o755)
    secret = folder / "db_password"
    secret.write_text("hunter2")
    os.chown(secret, 0, 70)
    os.chmod(secret, 0o440)
    assert secret.read_text() == "hunter2"                           # root reads it
    run = _as_agent(f"print(open({str(secret)!r}).read())")
    assert run.returncode != 0 and "hunter2" not in run.stdout and "Permission" in run.stderr
