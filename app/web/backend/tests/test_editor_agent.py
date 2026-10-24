"""The agent's editor tools: offered only in agent mode with a plan, conflicts, short results, the inventory gate."""
import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

import agent
import run_state
import settings
import tools
from main import app
from store import get_store
from tool_policy import available_tools


@pytest.fixture(autouse=True)
def traversable_tmp():
    os.chmod(settings.DATA_DIR.parent, 0o755)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def chat(client):
    return get_store().create_chat()["id"]


@pytest.fixture
def inventories_dir(tmp_path, monkeypatch):
    path = tmp_path / "inventories"
    path.mkdir()
    monkeypatch.setattr(settings, "INVENTORIES_DIR", path)
    return path


def names(opts, chat_id):
    return {t["function"]["name"] for t in available_tools(opts, chat_id)}


def call(chat, name, **args):
    ctx = tools.ToolContext(chat, get_store(), lambda *_: None)
    result = asyncio.run(tools.dispatch(ctx, name, args))
    assert len(result.content) < 1000
    return result.content


def data(chat, name, **args):
    return json.loads(call(chat, name, **args))


def add(part="3005.dat", colour=4, y=-24):
    return {"op": "add", "section": "model.ldr", "part": part, "colour": colour, "position": [0, y, 0]}


def test_tools_follow_mode_and_plan(client, chat):
    agent_mode = {"mode": "agent", "permissions": "full"}
    before = names(agent_mode, chat)
    assert "editor_open" in before and not {"editor_state", "editor_edit"} & before
    assert client.post(f"/api/chats/{chat}/editor", json={}).status_code == 200
    after = names(agent_mode, chat)
    assert {"editor_state", "editor_edit", "editor_open"} <= after
    for opts in ({"mode": "plan"}, {"mode": "chat"}, {"mode": "agent", "permissions": "read_only"}):
        assert not names(opts, chat) & {"editor_state", "editor_edit", "editor_open"}
    run = run_state.Run(chat)
    run.options = {"mode": "plan"}
    ctx = tools.ToolContext(chat, get_store(), lambda *_: None)
    denied = asyncio.run(agent.execute_tool(run, ctx, "c1", "editor_edit", "{}"))
    assert denied.content == "Tool denied by the current mode or permissions."


def test_state_edit_and_stale_conflict(client, chat):
    client.post(f"/api/chats/{chat}/editor", json={})
    state = data(chat, "editor_state")
    assert state["revision"] == 1 and state["pieces"] == 1 and state["bounds"]["min"] == [0, 0, 0]
    done = data(chat, "editor_edit", edits=[add()], base_revision=1)
    assert done["ok"] and done["revision"] == 2 and done["pieces"] == 2
    # the person edits in the meantime: the agent's older revision is a conflict and changes nothing
    person = client.post(f"/api/chats/{chat}/editor/edits",
                         json={"edits": [{"op": "recolour", "id": "base", "colour": 1}], "base_revision": 2})
    assert person.status_code == 200
    stale = data(chat, "editor_edit", edits=[add(y=-48)], base_revision=2)
    assert stale["ok"] is False and stale["conflict"] is True and stale["revision"] == 3
    assert client.get(f"/api/chats/{chat}/editor").json()["revision"] == 3


def test_edit_refused_when_part_is_at_zero_available(client, chat, inventories_dir):
    (inventories_dir / "bricks.json").write_text(json.dumps(
        {"name": "bricks", "rows": [{"part": "3005", "colour": 4, "qty": 1}]}))
    get_store().update_chat(chat, options={"parts_mode": "only", "inventory_ids": ["bricks"]})
    client.post(f"/api/chats/{chat}/editor", json={})
    state = data(chat, "editor_state")
    assert state["available"] == [["3005", 4, 0]]
    refused = data(chat, "editor_edit", edits=[add()], base_revision=1)
    assert refused["ok"] is False and refused["refused_short"] == [["3005", 4, 2, 1]]
    assert data(chat, "editor_state")["revision"] == 1


def test_editor_open_adopts_an_agent_plan(client, chat):
    plan = {"version": 1, "author": "Tests", "sections": [{"name": "model.ldr", "description": "d", "steps": [
        [{"id": "a", "ref": "3005.dat", "colour": 4, "at": [0, 0, 0]}]]}]}
    (get_store().work_dir(chat) / "my.json").write_text(json.dumps(plan))
    opened = data(chat, "editor_open", plan="my.json")
    assert opened["ok"] and opened["pieces"] == 1
    assert client.get(f"/api/chats/{chat}/editor").status_code == 200
    assert data(chat, "editor_open", plan="my.json")["ok"] is False
