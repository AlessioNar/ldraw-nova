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


SHIP = {"version": 1, "author": "Tests", "sections": [
    {"name": "ship.ldr", "description": "Ship - two wings", "steps": [
        [{"id": "wl", "ref": "wing.ldr", "colour": 15, "at": [-80, 0, 0]},
         {"id": "wr", "ref": "wing.ldr", "colour": 15, "at": [80, 0, 0], "yaw": 180}],
        [{"id": "row", "ref": "3005.dat", "colour": 4, "at": [0, -24, 100], "repeat": {"count": 3, "step": [20, 0, 0]}}]]},
    {"name": "wing.ldr", "description": "wing", "steps": [
        [{"id": "w1", "ref": "3020.dat", "colour": 14, "at": [10, 0, 20]},
         {"id": "w2", "ref": "3020.dat", "colour": 14, "at": [10, 0, 80]}]]}]}


def test_plans_lists_the_plan_files_of_the_work_folder_newest_first(client, chat):
    work = get_store().work_dir(chat)
    (work / "sub").mkdir(parents=True)
    (work / "editor").mkdir()
    old, new = work / "old.plan.json", work / "sub" / "new.plan.json"
    old.write_text(json.dumps(SHIP))
    new.write_text(json.dumps({**SHIP, "sections": [SHIP["sections"][1]]}))
    os.utime(old, (1000, 1000))
    (work / "notes.json").write_text('{"hello": 1}')               # JSON, but not a plan
    (work / "broken.json").write_text("{not json")
    (work / "editor" / "plan.json").write_text(json.dumps(SHIP))   # the editor's own copy is not offered
    (work / ".hidden.json").write_text(json.dumps(SHIP))
    plans = client.get(f"/api/chats/{chat}/editor/plans").json()["plans"]
    assert [p["path"] for p in plans] == ["sub/new.plan.json", "old.plan.json"]
    assert plans[1]["entries"] == 5 and plans[1]["sections"] == 2 and plans[1]["title"] == "Ship - two wings"
    assert client.get("/api/chats/nonexistent/editor/plans").status_code == 404
    assert client.get(f"/api/chats/{get_store().create_chat()['id']}/editor/plans").json() == {"plans": []}


def test_open_with_replace_puts_the_old_session_aside(client, chat):
    work = get_store().work_dir(chat)
    (work / "ship.plan.json").write_text(json.dumps(SHIP))
    open_editor(client, chat)
    refused = client.post(f"/api/chats/{chat}/editor", json={"source": "ship.plan.json"})
    assert refused.status_code == 409
    opened = open_editor(client, chat, source="ship.plan.json", name="Ship", replace=True)
    assert opened["revision"] == 1 and opened["name"] == "Ship"
    assert [e["id"] for e in opened["plan"]["sections"][0]["steps"][0]] == ["wl", "wr"]
    archived = list((work / "editor" / "archive").glob("*/plan.json"))
    assert len(archived) == 1 and json.loads(archived[0].read_text())["sections"][0]["name"] == "model.ldr"
    # The archived copy is not offered as a plan to open.
    assert [p["path"] for p in client.get(f"/api/chats/{chat}/editor/plans").json()["plans"]] == ["ship.plan.json"]


def test_edit_inside_a_sub_model_changes_every_copy(client, chat):
    work = get_store().work_dir(chat)
    (work / "ship.plan.json").write_text(json.dumps(SHIP))
    open_editor(client, chat, source="ship.plan.json")
    moved = apply_edits(client, chat, [{"op": "move", "id": "w1", "section": "wing.ldr", "position": [50, 0, 20]}], 1)
    assert moved.status_code == 200, moved.text
    assert "1 14 50 0 20" in moved.json()["mpd"].split("0 FILE wing.ldr")[1]
    # A repeated run moves by its template, and rotating it is refused with a reason.
    run = apply_edits(client, chat, [{"op": "move", "id": "row", "section": "ship.ldr", "position": [20, -24, 100]}], 2)
    assert run.status_code == 200 and "1 4 40 -24 100" in run.json()["mpd"]
    turned = apply_edits(client, chat, [{"op": "rotate", "id": "row", "section": "ship.ldr", "axis": "y", "degrees": 90}], 3)
    assert turned.status_code == 400 and "repeated placement" in turned.text
    # The last part of a sub-model can be deleted too: the sub-model section is left with one empty step.
    first = apply_edits(client, chat, [{"op": "delete", "id": "w1", "section": "wing.ldr"}], 3)
    assert first.status_code == 200, first.text
    last = apply_edits(client, chat, [{"op": "delete", "id": "w2", "section": "wing.ldr"}], 4)
    assert last.status_code == 200 and last.json()["plan"]["sections"][1]["steps"] == [[]]


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


# --- the resident editor worker ---------------------------------------------------------------------------

def test_cli_arguments_become_a_worker_request():
    from routers import editor as routes
    request = routes._request(["apply", "--plan", "/p", "--state", "/s", "--mpd", "/m", "--base-revision", "3",
                               "--edits", "/e", "--owned", "/o", "--mode", "prefer"])
    assert request == {"op": "editor", "action": "apply", "plan": "/p", "state": "/s", "mpd": "/m",
                       "base_revision": 3, "edits": "/e", "owned": "/o", "mode": "prefer"}
    opened = routes._request(["open", "--plan", "/p", "--state", "/s", "--mpd", "/m", "--from-mpd", "/x", "--name", "n"])
    assert opened["from_mpd"] == "/x" and opened["name"] == "n" and "base_revision" not in opened


def test_edits_run_in_one_resident_process(client, chat):
    import build_worker
    if "def run_request" not in (settings.TOOLKIT_DIR / "ldraw_tools/commands/editor.py").read_text():
        pytest.skip("this toolkit has no editor worker op")
    build_worker._editor_worker = None
    open_editor(client, chat)
    assert apply_edits(client, chat, [{"op": "move", "id": "base", "position": [20, -24, 0]}], 1).status_code == 200
    assert client.post(f"/api/chats/{chat}/editor/undo", json={}).status_code == 200
    assert client.post(f"/api/chats/{chat}/editor/redo", json={}).status_code == 200
    worker = build_worker._editor_worker
    assert worker.alive and worker.starts == 1                           # all four went through the same warm process


def test_a_toolkit_without_the_editor_op_falls_back_to_the_cli(client, chat, monkeypatch):
    import build_worker

    class OldWorker:
        async def request(self, payload, timeout=None):
            return {"ok": False, "error": f"Unknown op {payload['op']!r}", "error_type": "ValueError"}
    monkeypatch.setattr(build_worker, "get_editor_worker", lambda: OldWorker())
    opened = open_editor(client, chat)
    assert opened["revision"] == 1 and "0 FILE" in opened["mpd"]
    assert apply_edits(client, chat, [{"op": "recolour", "id": "base", "colour": 0}], 1).status_code == 200


def test_a_worker_that_is_busy_or_crashed_falls_back_to_the_cli(client, chat, monkeypatch):
    import build_worker

    class BrokenWorker:
        async def request(self, payload, timeout=None):
            raise build_worker.WorkerCrashed("the build worker exited (code 3)")
    monkeypatch.setattr(build_worker, "get_editor_worker", lambda: BrokenWorker())
    assert open_editor(client, chat)["revision"] == 1


def test_a_worker_timeout_is_a_gateway_timeout(client, chat, monkeypatch):
    import build_worker
    open_editor(client, chat)

    class SlowWorker:
        async def request(self, payload, timeout=None):
            raise build_worker.WorkerTimeout("the build worker did not answer within 600 s and was stopped")
    monkeypatch.setattr(build_worker, "get_editor_worker", lambda: SlowWorker())
    assert apply_edits(client, chat, [{"op": "recolour", "id": "base", "colour": 0}], 1).status_code == 504
