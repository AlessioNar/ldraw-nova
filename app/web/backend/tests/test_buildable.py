"""The buildable-models route and its background cache job, with a fake toolkit runner (no real CLI, no library)."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import buildable
import inventories
import sandbox
import settings
from main import app
from test_inventories import put

RESULT = dict(file="1-1.mpd", id="1-1", set_id="1-1", description="A red car", pieces=40, matched=36, completeness=0.9, missing=4,
              missing_lines=2, custom_pieces=0, top_missing=[dict(part="3001", colour=4, colour_name="Red", description="Brick 2 x 4", needed=4, owned=0, missing=4)])


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def stored(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INVENTORIES_DIR", tmp_path / "inventories")
    inventories.clear_caches()
    buildable.reset()
    put("box", [("3001", 4, 10)], name="Box")
    put("tub", [("3003", 4, 5)], name="Tub")
    yield
    buildable.reset()
    inventories.clear_caches()


def tail(argv):
    """The `inventory buildable` arguments of a recorded sandbox call."""
    return argv[argv.index("buildable", 4) + 1:]


class FakeCli:
    """Stands in for sandbox.run: records the CLI arguments and writes the JSON report the way `> report.json` would."""

    def __init__(self, monkeypatch, cached=0, total=1821, rounds=2, results=(RESULT,)):
        self.calls, self.cached, self.total, self.rounds, self.results, self.fail = [], cached, total, rounds, list(results), False
        monkeypatch.setattr(sandbox, "run", self.run)
        monkeypatch.setattr(sandbox, "give_to_agent", lambda path: None)

    async def run(self, argv, cwd, timeout, env_extra=None, on_output=None, secrets=()):
        self.calls.append(argv)
        args = tail(argv)
        if self.fail and "--warm" in args:
            return sandbox.RunResult(1, "", "boom", False, 0.0)
        if "--warm" in args:
            self.cached = min(self.total, self.cached + self.total // self.rounds + 1)
            if on_output:
                on_output("stderr", f"buildable: {self.cached}/{self.total} models\n")
        pending = self.total - self.cached
        report = dict(models=self.total, cached=self.cached, built=0, pending=pending, partial=bool(pending), skipped=[],
                      results=[] if "--warm" in args else self.results, inventories=["Box"], inventory_pieces=10, unmapped_pieces=0,
                      unmapped_share=0.0, counts=dict(models=self.total, parsed=self.cached, skipped=0, ranked=len(self.results)), warnings=[])
        (cwd / "report.json").write_text(json.dumps(report))
        return sandbox.RunResult(0, "", "", False, 0.0)


def wait_for(client, state, timeout=10):
    end = time.time() + timeout
    body = {}
    while time.time() < end:
        body = client.get("/api/inventories/buildable/status").json()
        if body["state"] == state:
            return body
        time.sleep(0.02)
    raise AssertionError(f"state never became {state}: {body}")


def test_nothing_runs_until_asked(client, monkeypatch):
    fake = FakeCli(monkeypatch)
    assert client.get("/api/inventories").status_code == 200
    assert client.get("/api/inventories/buildable/status").json() == {"state": "idle", "done": 0, "total": 0}
    assert fake.calls == []


def test_cold_cache_answers_202_builds_in_the_background_then_ranks(client, monkeypatch):
    fake = FakeCli(monkeypatch)
    r = client.get("/api/inventories/buildable?ids=box")
    assert r.status_code == 202 and r.json()["state"] == "building"
    assert wait_for(client, "ready") == {"state": "ready", "done": 1821, "total": 1821}
    warm = [c for c in fake.calls if "--warm" in c]
    assert len(warm) == 2 and all("--max-build-seconds=600" in c for c in warm)           # bounded, resumable rounds
    r = client.get("/api/inventories/buildable?ids=box")
    assert r.status_code == 200
    body = r.json()
    assert body["results"] == [RESULT] and body["status"]["state"] == "ready" and body["pending"] == 0
    args = tail(fake.calls[-1])
    assert args[:3] == ["box", "--limit=20", "--min-parts=20"] and "--cache-only" in args and args[-1] == "--json"
    assert args[args.index("--store") + 1] == str(settings.INVENTORIES_DIR)


def test_warm_cache_on_disk_ranks_at_once_after_a_restart(client, monkeypatch):
    fake = FakeCli(monkeypatch, cached=1821)
    r = client.get("/api/inventories/buildable?ids=box,tub")
    assert r.status_code == 200 and r.json()["status"]["state"] == "ready"
    assert len(fake.calls) == 1 and "box,tub" in fake.calls[0]


def test_progress_is_reported_while_building(client, monkeypatch):
    seen = []
    fake = FakeCli(monkeypatch, rounds=3)
    original = fake.run

    async def recording(argv, cwd, timeout, env_extra=None, on_output=None, secrets=()):
        result = await original(argv, cwd, timeout, env_extra, on_output, secrets)
        seen.append(buildable.status())
        return result
    monkeypatch.setattr(sandbox, "run", recording)
    client.post("/api/inventories/buildable/warm")
    wait_for(client, "ready")
    building = [s for s in seen if s["state"] == "building"]
    assert building and building[0]["done"] > 0 and building[0]["total"] == 1821


def test_warm_is_idempotent_and_on_demand(client, monkeypatch):
    fake = FakeCli(monkeypatch)
    first = client.post("/api/inventories/buildable/warm").json()
    again = client.post("/api/inventories/buildable/warm").json()
    assert first["state"] == "building" and again["state"] in ("building", "ready")
    wait_for(client, "ready")
    n = len(fake.calls)
    assert client.post("/api/inventories/buildable/warm").json()["state"] == "ready" and len(fake.calls) == n


def test_a_failing_ranking_call_is_a_500(client, monkeypatch):
    fake = FakeCli(monkeypatch, cached=1821)
    fake.fail = False
    monkeypatch.setattr(sandbox, "run", lambda *a, **k: _failed())
    r = client.get("/api/inventories/buildable?ids=box")
    assert r.status_code == 500 and r.json()["detail"] == "The toolkit could not rank the models"


async def _failed():
    return sandbox.RunResult(1, "", "boom", False, 0.0)


def test_failed_build_reports_an_error_and_a_warm_retries(client, monkeypatch):
    fake = FakeCli(monkeypatch)
    fake.fail = True
    assert client.get("/api/inventories/buildable?ids=box").status_code == 202
    end = time.time() + 10
    while not client.get("/api/inventories/buildable/status").json().get("error") and time.time() < end:
        time.sleep(0.02)
    status = client.get("/api/inventories/buildable/status").json()
    assert status["state"] == "idle" and "could not" in status["error"].lower()
    r = client.get("/api/inventories/buildable?ids=box")
    assert r.status_code == 500 and "could not" in r.json()["detail"].lower()
    fake.fail = False
    assert client.post("/api/inventories/buildable/warm").json()["state"] == "building"
    wait_for(client, "ready")
    assert "error" not in client.get("/api/inventories/buildable/status").json()


def test_parameters_are_validated_and_capped(client, monkeypatch):
    fake = FakeCli(monkeypatch, cached=1821)
    assert client.get("/api/inventories/buildable?ids=box,nope").status_code == 404
    assert client.get("/api/inventories/buildable?ids=../etc").status_code == 404
    assert client.get("/api/inventories/buildable?ids=BAD ID").status_code == 404
    assert client.get("/api/inventories/buildable?ids=box&max_missing_share=1.5").status_code == 422
    assert client.get("/api/inventories/buildable?ids=box&min_parts=-1").status_code == 422
    assert fake.calls == []
    r = client.get("/api/inventories/buildable?ids=box&limit=5000&min_parts=3&max_missing_share=0.25&query=red%20car&substitute=true")
    assert r.status_code == 200
    args = tail(fake.calls[-1])
    assert "--limit=100" in args and "--min-parts=3" in args and "--max-missing-share=0.25" in args
    assert "--query=red car" in args and "--substitute-colours" in args
    client.get("/api/inventories/buildable?ids=box&limit=0&query=--store%20x")
    args = tail(fake.calls[-1])
    assert "--limit=1" in args and "--query=--store x" in args                              # one argv entry: never an option


def test_without_ids_all_inventories_are_used_and_none_is_a_400(client, monkeypatch, tmp_path):
    fake = FakeCli(monkeypatch, cached=1821)
    client.get("/api/inventories/buildable")
    assert tail(fake.calls[-1])[0] == "box,tub"
    monkeypatch.setattr(settings, "INVENTORIES_DIR", tmp_path / "empty")
    inventories.clear_caches()
    assert client.get("/api/inventories/buildable").status_code == 400


def test_the_buildable_routes_do_not_shadow_inventory_ids(client):
    assert client.get("/api/inventories/box").json()["inventory"]["id"] == "box"
    assert client.get("/api/inventories/buildable/status").status_code == 200
    assert client.post("/api/inventories", json={"name": "Buildable", "csv": "Part,Color,Quantity\n3001,4,1\n"}).status_code == 400
