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



# --- follow-ups: endpoint changes, Jev key row, isolation status, chunked bodies, CSV cells ---

def _entry(client, **params):
    body = {"model_name": "t", "litellm_params": {"model": "openrouter/openai/gpt-6-luna", **params}}
    response = client.post("/api/llm-models", json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _put(client, entry_id, **params):
    return client.put(f"/api/llm-models/{entry_id}", json={"model_name": "t", "litellm_params": {"model": "openrouter/openai/gpt-6-luna", **params}})


def test_endpoint_change_needs_the_key_again_for_env_referenced_and_own_keys(client):
    ref = _entry(client, api_key="os.environ/OPENROUTER_API_KEY")
    response = _put(client, ref, api_key="os.environ/OPENROUTER_API_KEY", api_base="https://attacker.example/v1")
    assert response.status_code == 400 and "key again" in response.text
    # masked values do not count as re-supplying it, nor does omitting the key
    own = _entry(client, api_key="sk-own-test-key")
    assert _put(client, own, api_key="••••-key", api_base="https://attacker.example/v1").status_code == 400
    assert _put(client, own, api_base="https://attacker.example/v1").status_code == 400
    stored = client.get(f"/api/llm-models/{own}/edit").json()["litellm_params"]
    assert "api_base" not in stored
    # a typed key is accepted, and an unchanged endpoint is never blocked
    assert _put(client, own, api_key="sk-new-test-key", api_base="https://proxy.example/v1").status_code == 200
    assert _put(client, own, api_key="••••-key", api_base="https://proxy.example/v1/").status_code == 200
    assert _put(client, ref, api_key="os.environ/OPENROUTER_API_KEY").status_code == 200


def test_endpoint_change_is_free_for_entries_without_any_credential(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_LDRAW_NOVA_API_KEY", raising=False)
    entry = _entry(client)
    assert _put(client, entry, api_base="http://localhost:11434").status_code == 200


def test_connection_test_uses_only_the_stored_entry(client, monkeypatch):
    """The test route takes no body: a request cannot swap in another endpoint for the stored key."""
    import litellm
    seen = []

    async def fake(**params):
        seen.append(params.get("api_base"))
        return {}

    monkeypatch.setattr(litellm, "acompletion", fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    entry = _entry(client, api_key="os.environ/OPENROUTER_API_KEY")
    client.post(f"/api/llm-models/{entry}/test", json={"litellm_params": {"api_base": "https://attacker.example"}})
    assert _put(client, entry, api_key="os.environ/OPENROUTER_API_KEY", api_base="https://attacker.example").status_code == 400
    client.post(f"/api/llm-models/{entry}/test")
    assert seen and all(base is None for base in seen)


def test_jev_key_row_is_optional_private_and_fixed(client):
    rows = client.get("/api/environment").json()["variables"]
    jev = next(r for r in rows if r["name"] == "JEV_OPENROUTER_API_KEY")
    assert jev["fixed"] and jev["value"] is None
    # untouched row saves nothing; a typed value is stored privately and never echoed
    assert client.put("/api/environment", json={"variables": [{"id": jev["id"], "name": jev["name"], "value": None}]}).status_code == 200
    saved = client.put("/api/environment", json={"variables": [{"id": jev["id"], "name": jev["name"], "value": "or-jev-secret"}]})
    assert saved.status_code == 200 and "or-jev-secret" not in saved.text
    again = next(r for r in saved.json()["variables"] if r["name"] == "JEV_OPENROUTER_API_KEY")
    assert again["has_value"] and again["fixed"]
    assert client.get("/api/environment").json()["jev_source"] == "jev_openrouter"
    client.put("/api/environment", json={"variables": []})


def test_isolation_status_is_reported(client, tmp_path, monkeypatch):
    import sandbox
    assert client.get("/api/environment").json()["sandbox_isolation"]["state"] in {"on", "off", "n/a"}
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 0)
    monkeypatch.setattr(sandbox, "_agent_account", lambda: object())
    monkeypatch.setattr(sandbox, "ISOLATION_FILE", tmp_path / "missing")
    assert sandbox.isolation_status()["state"] == "off"
    (tmp_path / "state").write_text("on API port 8000 blocked")
    monkeypatch.setattr(sandbox, "ISOLATION_FILE", tmp_path / "state")
    assert sandbox.isolation_status() == {"state": "on", "detail": "API port 8000 blocked"}
    (tmp_path / "state").write_text("off iptables is not installed")
    assert sandbox.isolation_status() == {"state": "off", "detail": "iptables is not installed"}


def test_chunked_bodies_are_capped_like_sized_ones(client, monkeypatch):
    monkeypatch.setattr(main, "body_limit_mb", lambda path: 1)

    def chunks(total):
        for _ in range(total):
            yield b"x" * 65536

    big = client.post("/api/llm-models/import", content=chunks(20))            # 1.25 MB, no Content-Length
    assert big.status_code == 413
    small = client.post("/api/llm-models/import", content=chunks(2), headers={"content-type": "application/json"})
    assert small.status_code != 413


def test_csv_cells_that_start_like_formulas_are_neutralised(tmp_path):
    import csv
    from leocad_render import csv_safe, make_csv_safe
    for risky in ("=1+1", "+cmd|' /C calc'!A0", "-2+3", "@SUM(A1)", "\tTAB", "\rCR", '=HYPERLINK("http://x")'):
        assert csv_safe(risky) == "'" + risky
    for fine in ("3001", "-5", "+2", "0.5", "Brick 2 x 4", "", "a=b"):
        assert csv_safe(fine) == fine
    path = tmp_path / "bom.csv"
    path.write_text('Part Name,Color,Quantity,Part ID,Color Code\n"=cmd|x","Red",-1,3001,4\n"Brick",@Red,2,3002,1\n', encoding="utf-8")
    make_csv_safe(path)
    rows = list(csv.DictReader(path.open(newline="", encoding="utf-8")))
    assert rows[0]["Part Name"] == "'=cmd|x" and rows[0]["Quantity"] == "-1"
    assert rows[1]["Color"] == "'@Red" and rows[1]["Part ID"] == "3002"
