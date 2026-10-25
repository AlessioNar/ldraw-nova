"""Worker events for the UI (docs/plans/done/swarm-subagents/08-ui.md): live events, reconnect snapshot, transcript endpoint."""
import asyncio
import re

import litellm
import pytest
from fastapi.testclient import TestClient

import run_state
import subagents
import tools
from main import app
from test_agent import chunk, scripted, tool_call_chunks
from test_subagents import save_result, worker  # noqa: F401  (worker is a fixture)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _delegate_with_run(ctx, monkeypatch, **args):
    run = run_state.Run(ctx.chat_id)
    events = []
    real = run.emit
    run.emit = lambda event, data: (real(event, data), events.append((event, dict(data))))[1]    # after Run added the step count
    ctx = tools.ToolContext(ctx.chat_id, ctx.store, run.emit, {"permissions": "full"}, ledger=run.ledger)
    return run, events, ctx, asyncio.run(subagents.delegate(ctx, **args))


def test_events_in_order_and_snapshot_state(monkeypatch, worker, ctx):  # noqa: F811
    fake, _ = scripted([[*tool_call_chunks("w1", "list_files", {})], [chunk(content="Done.")]])
    monkeypatch.setattr(litellm, "acompletion", fake)
    run, events, ctx, result = _delegate_with_run(ctx, monkeypatch, role="researcher", task="look around", max_steps=5)
    names = [e for e, _ in events if e.startswith("worker_")]
    assert names == ["worker_start", "worker_step", "worker_step", "worker_end"]
    start = events[0][1] if events[0][0] == "worker_start" else next(d for e, d in events if e == "worker_start")
    assert start["role"] == "researcher" and start["task"] == "look around" and start["max_steps"] == 5
    assert start["model"] == "openai/gpt-6-luna" and start["max_eur"] > 0
    steps = [d for e, d in events if e == "worker_step"]
    assert [s["steps"] for s in steps] == [1, 2]
    end = next(d for e, d in events if e == "worker_end")
    assert end["status"] == "ok" and end["steps"] == 2
    # the euro figure of the last event is the ledger's, which is what the saved aggregate record holds
    cost_eur = result.record["_usage"]["cost_usd"] * __import__("settings").eur_per_usd()
    assert end["eur"] == pytest.approx(cost_eur, abs=0.0001)
    assert run.workers[start["id"]]["status"] == "ok" and run.workers[start["id"]]["steps"] == 2
    save_result(ctx, result)


def test_snapshot_lists_running_workers_and_done_clears_them(monkeypatch):
    run = run_state.Run("c1")
    run.emit("worker_start", {"id": "ab12cd34", "role": "builder", "task": "t", "model": "m", "max_steps": 20, "max_eur": 0.1})
    run.emit("worker_step", {"id": "ab12cd34", "eur": 0.02})
    run_state._runs["c1"] = run
    run.task = type("T", (), {"done": lambda self: False})()

    async def first():
        async for event, data in run_state.subscribe("c1"):
            return event, data
    event, data = asyncio.run(first())
    assert event == "snapshot" and data["workers"] == [
        {"id": "ab12cd34", "role": "builder", "task": "t", "model": "m", "max_steps": 20, "max_eur": 0.1,
         "status": "running", "steps": 1, "eur": 0.02}]
    run.emit("done", {})
    assert run.workers == {}
    del run_state._runs["c1"]


def test_cancelled_worker_emits_end(monkeypatch, worker, ctx):  # noqa: F811
    async def fake(**kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(litellm, "acompletion", fake)
    run = run_state.Run(ctx.chat_id)
    ctx = tools.ToolContext(ctx.chat_id, ctx.store, run.emit, {"permissions": "full"}, ledger=run.ledger)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(subagents.delegate(ctx, role="researcher", task="x"))
    [state] = run.workers.values()
    assert state["status"] == "cancelled"


def test_worker_transcript_endpoint(monkeypatch, worker, ctx, client):  # noqa: F811
    state = {}

    async def fake(**kwargs):
        state.setdefault("n", 0)
        state["n"] += 1
        wid = re.search(r"output/workers/(\w+)/", kwargs["messages"][0]["content"]).group(1)
        chunks = (tool_call_chunks("w1", "write_file", {"path": f"output/workers/{wid}/part.ldr", "content": "0 hi\n"})
                  if state["n"] == 1 else [chunk(content="Made part.ldr.")])

        async def stream():
            for c in chunks:
                yield c
        return stream()

    monkeypatch.setattr(litellm, "acompletion", fake)
    run, events, ctx, result = _delegate_with_run(ctx, monkeypatch, role="builder", task="build", outputs=["part.ldr"])
    wid = result.record["_worker"]["id"]
    body = client.get(f"/api/chats/{ctx.chat_id}/workers/{wid}").json()
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert body["messages"][-1]["content"] == "Made part.ldr."
    [file] = body["files"]
    assert file["name"] == "part.ldr" and file["size"] == 5
    assert client.get(file["url"]).text == "0 hi\n"
    assert client.get(f"/api/chats/{ctx.chat_id}/workers/{'0' * 8}").status_code == 404
    assert client.get(f"/api/chats/{ctx.chat_id}/workers/..%2f..%2fmessages").status_code == 404
