"""Security pass (2026-10): Host validation, symlink leaks, served-file headers, forbidden environment names."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import gallery
import main
import settings


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "rebind.attacker.test:80", "localhost.evil.example", ""])
def test_unknown_host_is_refused_even_when_origin_matches(client, host):
    """DNS rebinding: the attacker's page is 'same-origin' with its own hostname, so Origin == Host."""
    headers = {"host": host, "origin": f"http://{host}"} if host else {"host": host}
    for method, url in (("get", "/api/llm-models"), ("put", "/api/environment"), ("get", "/files/generated/x.mpd"), ("get", "/")):
        response = getattr(client, method)(url, headers=headers)
        assert response.status_code == 421, (method, url, host)


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765", "192.168.1.20:8765", "10.0.0.5", "host.docker.internal:8765"])
def test_local_hosts_are_served(client, host):
    assert client.get("/api/llm-models", headers={"host": host}).status_code != 421


def test_extra_hosts_can_be_allowed(client, monkeypatch):
    assert client.get("/api/llm-models", headers={"host": "mypc.local:8765"}).status_code == 421
    monkeypatch.setenv("LDRAW_NOVA_ALLOWED_HOSTS", "MyPC.local, nas")
    assert client.get("/api/llm-models", headers={"host": "mypc.local:8765"}).status_code != 421
    assert client.get("/api/llm-models", headers={"host": "nas"}).status_code != 421


def test_served_files_cannot_run_scripts_in_the_app_origin(client, data_dir):
    page = settings.GENERATED_DIR / "planted.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("<script>fetch('/api/environment')</script>")
    response = client.get("/files/generated/planted.html")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("name", ["LD_PRELOAD", "ld_library_path", "PATH", "PYTHONPATH", "PYTHONSTARTUP", "BASH_ENV", "UV_PYTHON", "NODE_OPTIONS"])
def test_environment_refuses_names_that_change_program_loading(client, name):
    response = client.put("/api/environment", json={"variables": [{"name": name, "value": "/tmp/evil"}]})
    assert response.status_code == 400
    assert "/tmp/evil" not in response.text


def test_environment_still_accepts_provider_keys(client):
    response = client.put("/api/environment", json={"variables": [{"name": "OPENAI_API_KEY", "value": "sk-test-not-real"}]})
    assert response.status_code == 200
    assert "sk-test-not-real" not in response.text
    client.put("/api/environment", json={"variables": []})


def _symlink(link, target):
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")


def test_collection_and_zip_ignore_planted_symlinks(client, tmp_path, monkeypatch):
    root = tmp_path / "generated"
    root.mkdir()
    secret = tmp_path / "secret.json"
    secret.write_text("TOP-SECRET-VALUE")
    (root / "real.mpd").write_text("0 FILE real.ldr\n0 Real\n")
    _symlink(root / "leak.mpd", secret)
    _symlink(root / "leak.txt", secret)
    monkeypatch.setattr(settings, "GENERATED_DIR", root)
    monkeypatch.setattr(main.settings, "GENERATED_DIR", root)
    assert [p.name for p in gallery.collection(root)] == ["real.mpd"]
    response = client.get("/api/models/zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        assert zf.namelist() == ["real.mpd"]
        assert b"TOP-SECRET-VALUE" not in b"".join(zf.read(n) for n in zf.namelist())
