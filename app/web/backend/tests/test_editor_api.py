"""The editor API: open, edit, undo/redo, the inventory gate and publish.

Every route round-trips through the real toolkit CLI (`ldraw-agent editor`),
sandboxed like the agent's own tool calls, so these tests need the container's
baked LDraw library and indexes.
"""
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import settings
from main import app
from store import get_store


@pytest.fixture(autouse=True)
def traversable_tmp():
    """Let the sandboxed toolkit user traverse the redirected /data, like the `ctx` fixture does."""
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


def open_editor(client, chat, **body):
    r = client.post(f"/api/chats/{chat}/editor", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def apply_edits(client, chat, edits, base_revision):
    return client.post(f"/api/chats/{chat}/editor/edits",
                       json={"edits": edits, "base_revision": base_revision})


def test_open_edit_undo_redo_and_get(client, chat):
    opened = open_editor(client, chat)
    assert opened["revision"] == 1 and opened["undo_depth"] == 0 and opened["redo_depth"] == 0
    assert opened["plan"]["sections"][0]["steps"][0][0]["id"] == "base"
    assert "0 FILE" in opened["mpd"] and opened["parts"]["mode"] == "off"

    moved = apply_edits(client, chat, [{"op": "move", "id": "base", "position": [20, -24, 0]}], 1)
    assert moved.status_code == 200, moved.text
    view = moved.json()
    assert view["revision"] == 2 and view["undo_depth"] == 1 and view["redo_depth"] == 0
    assert view["plan"]["sections"][0]["steps"][0][0]["at"] == [20.0, -24.0, 0.0]
    assert "1 4 20 -24 0" in view["mpd"]

    undone = client.post(f"/api/chats/{chat}/editor/undo", json={})
    assert undone.status_code == 200
    assert undone.json()["plan"]["sections"][0]["steps"][0][0]["at"] == [0, 0, 0]
    assert undone.json()["undo_depth"] == 0 and undone.json()["redo_depth"] == 1

    redone = client.post(f"/api/chats/{chat}/editor/redo", json={})
    assert redone.status_code == 200
    assert redone.json()["plan"]["sections"][0]["steps"][0][0]["at"] == [20.0, -24.0, 0.0]

    assert client.get(f"/api/chats/{chat}/editor").json()["revision"] == redone.json()["revision"]


def test_open_twice_and_unknown_chat(client, chat):
    assert client.post(f"/api/chats/{chat}/editor", json={}).status_code == 200
    assert client.post(f"/api/chats/{chat}/editor", json={}).status_code == 409
    assert client.get("/api/chats/nonexistent/editor").status_code == 404
    assert client.get(f"/api/chats/{chat}/editor").status_code == 200


def test_stale_base_revision_is_a_conflict_with_the_current_one(client, chat):
    open_editor(client, chat)
    assert apply_edits(client, chat, [{"op": "recolour", "id": "base", "colour": 0}], 1).status_code == 200
    stale = apply_edits(client, chat, [{"op": "recolour", "id": "base", "colour": 1}], 1)
    assert stale.status_code == 409
    assert stale.json()["detail"]["revision"] == 2


def test_invalid_edit_rejects_the_whole_batch_and_persists_nothing(client, chat):
    open_editor(client, chat)
    edits = [{"op": "recolour", "id": "base", "colour": 0},
             {"op": "move", "id": "missing", "position": [0, 0, 0]}]
    r = apply_edits(client, chat, edits, 1)
    assert r.status_code == 400 and "Unknown entry id missing" in r.text
    view = client.get(f"/api/chats/{chat}/editor").json()
    assert view["revision"] == 1 and view["plan"]["sections"][0]["steps"][0][0]["colour"] == 4


def test_failed_rebuild_keeps_the_last_good_plan(client, chat):
    open_editor(client, chat)
    r = apply_edits(client, chat, [{"op": "add", "section": "model.ldr", "part": "3005.dat",
                                    "colour": 4, "position": [0, 0, 0]}], 1)
    assert r.status_code == 400 and "same world transform" in r.text
    view = client.get(f"/api/chats/{chat}/editor").json()
    assert view["revision"] == 1 and len(view["plan"]["sections"][0]["steps"][0]) == 1
    assert "1 4 0 0 0" in view["mpd"]


def test_open_from_a_plan_in_the_work_folder(client, chat):
    store = get_store()
    source = store.work_dir(chat) / "bridge.plan.json"
    source.write_text(json.dumps({"version": 1, "author": "Tests", "sections": [
        {"name": "model.ldr", "description": "d", "steps": [
            [{"id": "pillar", "ref": "3003.dat", "colour": 4, "at": [0, 0, 0]}]]}]}))
    opened = open_editor(client, chat, source="bridge.plan.json", name="My model")
    assert opened["plan"]["sections"][0]["steps"][0][0]["id"] == "pillar"
    assert opened["name"] == "My model" and opened["parts"]["rows"][0]["part"] == "3003"
    other = get_store().create_chat()["id"]
    assert client.post(f"/api/chats/{other}/editor", json={"source": "../etc/passwd"}).status_code == 404


def test_only_mode_refuses_and_prefer_flags(client, chat, inventories_dir):
    (inventories_dir / "bricks.json").write_text(json.dumps(
        {"name": "bricks", "rows": [{"part": "3005", "colour": 4, "qty": 2}]}))
    get_store().update_chat(chat, options={"parts_mode": "only", "inventory_ids": ["bricks"]})
    open_editor(client, chat)
    over = apply_edits(client, chat, [{"op": "add", "section": "model.ldr", "part": "3003.dat",
                                      "colour": 1, "position": [0, -24, 0]}], 1)
    assert over.status_code == 400
    assert over.json()["detail"]["short"] == [["3003", 1, 1, 0]]
    assert client.get(f"/api/chats/{chat}/editor").json()["revision"] == 1

    within = apply_edits(client, chat, [{"op": "add", "section": "model.ldr", "part": "3005.dat",
                                         "colour": 4, "position": [0, -24, 0]}], 1)
    assert within.status_code == 200, within.text
    parts = within.json()["parts"]
    assert parts["mode"] == "only"
    row = next(r for r in parts["rows"] if r["part"] == "3005")
    assert row == {"part": "3005", "colour": 4, "used": 2, "owned": 2, "available": 0}

    get_store().update_chat(chat, options={"parts_mode": "prefer", "inventory_ids": ["bricks"]})
    flagged = apply_edits(client, chat, [{"op": "add", "section": "model.ldr", "part": "3003.dat",
                                          "colour": 1, "position": [0, -48, 0]}], 2)
    assert flagged.status_code == 200
    assert flagged.json()["parts"]["short"] == [{"part": "3003", "colour": 1, "used": 1, "owned": 0, "missing": 1}]


def test_publish_reuses_the_model_card_path(client, chat):
    open_editor(client, chat, name="Manual model")
    r = client.post(f"/api/chats/{chat}/editor/publish", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["publish"]["model_url"].startswith("/files/generated/manual-model-v")
    assert body["models"] and get_store().models(chat)
    published = next(p for p in Path(settings.GENERATED_DIR).glob("manual-model-v*.mpd"))
    assert published.is_file()
    assert (get_store().work_dir(chat) / "manual-model.mpd").is_file()
