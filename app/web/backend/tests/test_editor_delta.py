"""The editor's edits, undo and redo answer with a difference when asked, and the difference replays to the same state.

Like test_editor_api these go through the real toolkit (the resident editor worker), so they need the container's
LDraw library.
"""
import os
import time

import pytest
from fastapi.testclient import TestClient

import settings
from main import app
from routers import editor as editor_router
from store import get_store


@pytest.fixture(autouse=True)
def traversable_tmp():
    os.chmod(settings.DATA_DIR.parent, 0o755)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def chat(client):
    chat_id = get_store().create_chat()["id"]
    assert client.post(f"/api/chats/{chat_id}/editor", json={}).status_code == 200
    return chat_id


def post(client, chat, action, body):
    r = client.post(f"/api/chats/{chat}/editor/{action}", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def add(n):
    return {"op": "add", "section": "model.ldr", "part": "3001.dat", "colour": 4, "position": [40 * (n + 1), 0, 0]}


def replay(plan, splices):
    for sp in splices:
        step = plan["sections"][sp["section"]]["steps"][sp["step"]]
        step[sp["at"]:sp["at"] + sp["remove"]] = sp["insert"]
    return plan


def test_an_add_answers_with_the_entry_alone(client, chat):
    full = client.get(f"/api/chats/{chat}/editor").json()
    small = post(client, chat, "edits", {"edits": [add(0)], "base_revision": 1, "delta": True})
    assert small["delta"] is True and small["revision"] == 2 and small["undo_depth"] == 1 and small["redo_depth"] == 0
    assert "plan" not in small and "mpd" not in small
    assert small["parts"]["rows"] and small["parts"]["mode"] == "off"
    assert len(small["splices"]) == 1 and small["splices"][0]["remove"] == 0
    assert [e["ref"] for e in small["splices"][0]["insert"]] == ["3001.dat"]
    whole = client.get(f"/api/chats/{chat}/editor").json()
    assert replay(full["plan"], small["splices"]) == whole["plan"]


def test_without_the_flag_the_answer_is_still_the_whole_view(client, chat):
    view = post(client, chat, "edits", {"edits": [add(0)], "base_revision": 1})
    assert "plan" in view and "mpd" in view and "delta" not in view and view["revision"] == 2


def test_adds_undos_and_redos_replay_to_what_a_reload_shows(client, chat):
    mine = client.get(f"/api/chats/{chat}/editor").json()
    plan, revision = mine["plan"], mine["revision"]
    steps = [("edits", {"edits": [add(n)]}) for n in range(4)]
    steps += [("edits", {"edits": [{"op": "move", "id": "base", "position": [0, -24, 0]}]})]
    steps += [("undo", {}), ("undo", {}), ("redo", {}), ("undo", {}), ("undo", {}), ("undo", {}), ("redo", {}), ("redo", {})]
    steps += [("edits", {"edits": [{"op": "delete", "id": "base"}]}), ("undo", {})]
    for action, body in steps:
        answer = post(client, chat, action, {**body, "base_revision": revision, "delta": True})
        revision = answer["revision"]
        if answer.get("delta"):
            plan = replay(plan, answer["splices"])
        else:
            plan = answer["plan"]             # the toolkit had no difference to give (the plan's shape changed)
        whole = client.get(f"/api/chats/{chat}/editor").json()
        assert plan == whole["plan"], (action, body)
        assert revision == whole["revision"]
        assert (answer["undo_depth"], answer["redo_depth"]) == (whole["undo_depth"], whole["redo_depth"])
        assert answer["parts"] == whole["parts"]


def test_a_stale_revision_is_still_a_conflict_with_delta(client, chat):
    post(client, chat, "edits", {"edits": [add(0)], "base_revision": 1, "delta": True})
    r = client.post(f"/api/chats/{chat}/editor/edits", json={"edits": [add(1)], "base_revision": 1, "delta": True})
    assert r.status_code == 409 and r.json()["detail"]["revision"] == 2
    assert client.post(f"/api/chats/{chat}/editor/undo", json={"base_revision": 1, "delta": True}).status_code == 409


def test_a_refused_edit_is_the_same_400(client, chat):
    r = client.post(f"/api/chats/{chat}/editor/edits",
                    json={"edits": [{"op": "move", "id": "nope", "position": [0, 0, 0]}], "base_revision": 1, "delta": True})
    assert r.status_code == 400 and "Unknown entry id" in r.json()["detail"]["detail"]


def test_opening_a_workspace_warms_the_editor_worker_once(client, chat, monkeypatch):
    editor_router._warmed.pop(chat, None)
    sent = []
    worker = editor_router.build_worker.get_editor_worker()
    original = worker.request

    async def spy(payload, timeout=None):
        sent.append(payload)
        return await original(payload, timeout=timeout)

    monkeypatch.setattr(worker, "request", spy)
    client.get(f"/api/chats/{chat}/editor")
    client.get(f"/api/chats/{chat}/editor")
    for _ in range(100):
        if sent:
            break
        time.sleep(0.1)
    assert [p["action"] for p in sent] == ["warm"]
    assert sent[0]["plan"].endswith("plan.json")
