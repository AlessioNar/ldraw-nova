"""The resident build worker: protocol, crash recovery, timeouts, bounded queue, and the build route."""
import asyncio
import os

import pytest
from fastapi.testclient import TestClient

import build_worker
import settings
from main import app
from store import get_store

DEBUG = {"LDRAW_NOVA_WORKER_DEBUG": "1"}


@pytest.fixture(autouse=True)
def traversable_tmp():
    os.chmod(settings.DATA_DIR.parent, 0o755)


def test_ping_reuses_one_process():
    async def go():
        worker = build_worker.BuildWorker(env_extra=DEBUG)
        try:
            first = await worker.request({"op": "ping"})
            second = await worker.request({"op": "ping"})
            return first, second, worker.starts
        finally:
            await worker.close()
    first, second, starts = asyncio.run(go())
    assert first["pong"] and second["pong"] and starts == 1


def test_crash_is_reported_and_the_worker_restarts():
    async def go():
        worker = build_worker.BuildWorker(env_extra=DEBUG)
        try:
            await worker.request({"op": "ping"})
            with pytest.raises(build_worker.WorkerCrashed) as crashed:
                await worker.request({"op": "crash"})
            assert not worker.alive
            again = await worker.request({"op": "ping"})
            return str(crashed.value), again, worker.starts
        finally:
            await worker.close()
    message, again, starts = asyncio.run(go())
    assert "code 3" in message and "restarts" in message
    assert again["pong"] and starts == 2


def test_a_bad_request_does_not_kill_the_worker():
    async def go():
        worker = build_worker.BuildWorker(env_extra=DEBUG)
        try:
            bad = await worker.request({"op": "nope"})
            good = await worker.request({"op": "ping"})
            return bad, good, worker.starts
        finally:
            await worker.close()
    bad, good, starts = asyncio.run(go())
    assert bad["ok"] is False and "Unknown op" in bad["error"] and good["pong"] and starts == 1


def test_timeout_kills_the_worker_and_the_next_request_restarts_it():
    async def go():
        worker = build_worker.BuildWorker(env_extra=DEBUG)
        try:
            with pytest.raises(build_worker.WorkerTimeout):
                await worker.request({"op": "hang"}, timeout=1)
            return await worker.request({"op": "ping"}), worker.starts
        finally:
            await worker.close()
    again, starts = asyncio.run(go())
    assert again["pong"] and starts == 2


def test_queue_is_bounded():
    async def go():
        worker = build_worker.BuildWorker(env_extra=DEBUG, max_queue=1, timeout=30)
        try:
            slow = asyncio.create_task(worker.request({"op": "hang"}, timeout=3))     # running
            await asyncio.sleep(0.5)
            waiting = asyncio.create_task(worker.request({"op": "ping"}))             # queued (1)
            await asyncio.sleep(0.1)
            with pytest.raises(build_worker.WorkerBusy):
                await worker.request({"op": "ping"})                                  # over the bound
            with pytest.raises(build_worker.WorkerTimeout):
                await slow
            return (await waiting)["pong"]
        finally:
            await worker.close()
    assert asyncio.run(go())


def test_worker_runs_as_the_agent_user_without_server_secrets(monkeypatch):
    import sandbox
    if sandbox._agent_account() is None:
        pytest.skip("needs root + the `agent` user (run in the container)")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-top-secret")

    async def go():
        worker = build_worker.BuildWorker(
            argv=["python3", "-c", "import os,sys,json;print(json.dumps({'ok':True,'user':os.getuid(),'env':sorted(os.environ),'id':json.loads(sys.stdin.readline())['id']}),flush=True)"])
        try:
            return await worker.request({"op": "x"})
        finally:
            await worker.close()
    reply = asyncio.run(go())
    assert reply["user"] != 0 and "OPENAI_API_KEY" not in reply["env"]


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_build_route_builds_then_serves_a_cache_hit(client):
    chat = get_store().create_chat()["id"]
    body = {"vehicle": "grand-tourer", "name": "car", "geometry": False, "glb": False}
    first = client.post(f"/api/chats/{chat}/build", json=body)
    assert first.status_code == 200, first.text
    first = first.json()
    assert first["ok"] and not first["cached"] and first["paths"]["mpd"] == "car.mpd"
    mpd = get_store().work_dir(chat) / "car.mpd"
    assert mpd.read_text().startswith("0 ")
    second = client.post(f"/api/chats/{chat}/build", json=body).json()
    assert second["cached"] and second["key"] == first["key"] and second["seconds"] < 1


def test_build_route_validates_input(client):
    chat = get_store().create_chat()["id"]
    assert client.post(f"/api/chats/{chat}/build", json={}).status_code == 422
    assert client.post(f"/api/chats/{chat}/build", json={"vehicle": "x", "name": "../x"}).status_code == 422
    assert client.post(f"/api/chats/{chat}/build", json={"vehicle": "x", "renders": ["bad"]}).status_code == 422
    assert client.post("/api/chats/nochat/build", json={"vehicle": "x"}).status_code == 404
