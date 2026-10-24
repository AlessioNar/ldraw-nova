"""Per-chat model versions and "what changed", the person's editor publish in the chat, and the agent continuing
from the person's edit (turn-start note, editor_state ids and diff, editor_open replace). The editor parts run the
real toolkit CLI like the other editor tests, so they need the container."""
import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

import model_versions
import settings
import system_prompt
import tools
from main import app
from routers.common import chat_models
from store import get_store

HEADER = "Part Name,Color,Quantity,Part ID,Color Code\n"


def bom(path, rows):
    path.write_text(HEADER + "".join(f'"{n}","{c}",{q},{p}.dat,0\n' for n, c, q, p in rows))


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


def call(chat, name, **args):
    ctx = tools.ToolContext(chat, get_store(), lambda *_: None)
    result = asyncio.run(tools.dispatch(ctx, name, args))
    assert len(result.content) < 1000
    return json.loads(result.content)


# --- versions and "what changed" ---------------------------------------------------------------------

def test_bom_diff_recoloured_added_removed_and_count(tmp_path):
    a, b = tmp_path / "a.mpd", tmp_path / "b.mpd"
    bom(a.with_suffix(".csv"), [("Brick 2 x 4", "Red", 4, "3001"), ("Plate 1 x 2", "White", 2, "3023"),
                                ("Tile 1 x 1", "Black", 3, "3070b")])
    bom(b.with_suffix(".csv"), [("Brick 2 x 4", "Red", 1, "3001"), ("Brick 2 x 4", "Blue", 3, "3001"),
                                ("Plate 1 x 2", "White", 2, "3023"), ("Slope 45 2 x 2", "Red", 2, "3039")])
    d = model_versions.diff(a, b)
    assert d["delta"] == 0 + 2 - 3 and d["total"] == 8
    assert d["recoloured"] == [{"part": "3001", "name": "Brick 2 x 4", "from": "Red", "to": "Blue", "qty": 3}]
    assert [(i["part"], i["qty"]) for i in d["added"]] == [("3039", 2)]
    assert [(i["part"], i["qty"]) for i in d["removed"]] == [("3070b", 3)]
    assert d["summary"].startswith("-1 part (8 total)")
    assert "recoloured 3 x Brick 2 x 4 Red to Blue" in d["summary"]


def test_bom_diff_without_changes_or_boms(tmp_path):
    a, b = tmp_path / "a.mpd", tmp_path / "b.mpd"
    assert model_versions.diff(a, b) is None
    bom(a.with_suffix(".csv"), [("Brick", "Red", 2, "3001")])
    bom(b.with_suffix(".csv"), [("Brick", "Red", 2, "3001")])
    assert model_versions.diff(a, b)["summary"] == "No part changes"


def test_versions_are_per_chat_and_survive_a_new_file_name(chat):
    store = get_store()
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    first, second = settings.GENERATED_DIR / "vc-red-car-v1.mpd", settings.GENERATED_DIR / "vc-speedster-v1.mpd"
    for model, rows in ((first, [("Brick", "Red", 48, "3001")]), (second, [("Brick", "Red", 50, "3001")])):
        model.write_text("0 FILE x\n0 Red car\n")
        bom(model.with_suffix(".csv"), rows)
    store.add_model(chat, "red car", first, [], source="car.mpd")
    store.add_model(chat, "speedster", second, [])           # the agent changed the name: the file counter restarts
    models = list(chat_models(store, chat).values())
    assert [m["version"] for m in models] == [1, 2]
    assert [m["latest"] for m in models] == [False, True]
    assert models[0]["changes"] is None and models[1]["changes"]["summary"].startswith("+2 parts (50 total)")
    assert models[1]["title"] == "Red car" and models[1]["origin"] is None


def test_chats_get_returns_versions(client, chat):
    store = get_store()
    model = settings.GENERATED_DIR / "vc-api-v1.mpd"
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    model.write_text("0 FILE x\n0 Thing\n")
    store.add_model(chat, "thing", model, [])
    models = client.get(f"/api/chats/{chat}").json()["models"]
    assert next(iter(models.values()))["version"] == 1


# --- the person's publish is recorded in the chat -----------------------------------------------------

def open_editor(client, chat, **body):
    r = client.post(f"/api/chats/{chat}/editor", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_editor_publish_is_a_message_and_a_labelled_card(client, chat):
    open_editor(client, chat, name="Manual model")
    before = len(client.get(f"/api/chats/{chat}").json()["messages"])
    published = client.post(f"/api/chats/{chat}/editor/publish", json={})
    assert published.status_code == 200, published.text
    body = published.json()
    assert body["version"] == 1 and body["chat_url"] == f"/chat/{chat}#model-{body['model_id']}"
    detail = client.get(f"/api/chats/{chat}").json()
    assert len(detail["messages"]) == before + 1
    message = detail["messages"][-1]
    assert message["role"] == "user" and message["_editor_publish"] and message["_models"] == [body["model_id"]]
    assert "edited the plan in the 3D editor" in message["content"] and "v1" in message["content"]
    card = detail["models"][body["model_id"]]
    assert card["origin"] == "editor" and card["version"] == 1 and card["latest"]
    # a second publish under another name is v2, not v1 again
    open_second = client.post(f"/api/chats/{chat}/editor/publish", json={"name": "Another name"}).json()
    assert open_second["version"] == 2
    # it is history for the agent, but not a turn start for the status line
    import usage
    assert usage.turn_stats(client.get(f"/api/chats/{chat}").json()["messages"])["turn_steps"] == 0


def test_editor_publish_message_carries_the_parts_check(client, chat, monkeypatch):
    check = {"mode": "only", "complete": False, "needed_total": 2, "owned_total": 1, "short": [], "summary": "1 piece short."}

    async def fake_check(ctx, model):
        return check, "PARTS CHECK (only): 1 piece short."
    monkeypatch.setattr(tools, "parts_check", fake_check)
    open_editor(client, chat)
    client.post(f"/api/chats/{chat}/editor/publish", json={})
    message = client.get(f"/api/chats/{chat}").json()["messages"][-1]
    assert message["_editor_publish"] and message["parts_check"] == check and "Parts check: 1 piece short." in message["content"]
    assert "_parts_check" not in message


# --- the agent continues from the person's edit --------------------------------------------------------

def test_prompt_tail_names_latest_model_and_plan_and_keeps_the_prefix_stable(client, chat):
    store = get_store()
    stable_before, volatile_before = system_prompt.system_prompt_parts(store, chat)
    assert "Where this chat stands" not in volatile_before
    open_editor(client, chat, name="Dart")
    client.post(f"/api/chats/{chat}/editor/publish", json={})
    stable_after, volatile = system_prompt.system_prompt_parts(store, chat, options={"mode": "agent"})
    assert stable_after == stable_before                                  # cached prefix untouched
    assert "output/editor/plan.json, revision 1" in volatile
    assert "published by the person from the 3D editor: v1 (Dart), source output/dart.mpd" in volatile


def test_turn_note_until_the_agent_looks_then_state_has_ids_and_diff(client, chat):
    store = get_store()
    open_editor(client, chat)
    state = call(chat, "editor_state")                                    # the agent's first look
    assert state["entries"] == [["base", "3005", 4]] and state["entries_total"] == 1 and "since" not in state
    opts = {"mode": "agent", "permissions": "full"}
    assert "since you last looked" not in system_prompt.continuity_block(store, chat, opts)
    person = client.post(f"/api/chats/{chat}/editor/edits", json={"base_revision": 1, "edits": [
        {"op": "add", "section": "model.ldr", "part": "3001.dat", "colour": 1, "position": [0, -48, 0]},
        {"op": "recolour", "id": "base", "colour": 2}]})
    assert person.status_code == 200, person.text
    note = system_prompt.continuity_block(store, chat, opts)
    assert "The person edited the plan since you last looked: revision 1 to 2, 1 parts added, 0 removed, 1 changed" in note
    assert "since you last looked" not in system_prompt.continuity_block(store, chat, {"mode": "plan"})
    state = call(chat, "editor_state")
    assert state["since"]["from"] == 1 and state["since"]["changed_ids"] == ["base"]
    assert [row[1:] for row in state["since"]["added"]] == [["3001", 1]]
    assert {row[0] for row in state["entries"]} >= {"base", state["since"]["added"][0][0]}
    assert "since you last looked" not in system_prompt.continuity_block(store, chat, opts)
    assert "since" not in call(chat, "editor_state")                      # seen now


def test_agents_own_edit_is_not_reported_back_to_it(client, chat):
    open_editor(client, chat)
    call(chat, "editor_state")
    done = call(chat, "editor_edit", edits=[{"op": "add", "section": "model.ldr", "part": "3005.dat", "colour": 4,
                                             "position": [0, -24, 0]}], base_revision=1)
    assert done["ok"]
    assert "since" not in call(chat, "editor_state")


def test_editor_state_stays_short_on_a_big_plan_and_pages(client, chat):
    open_editor(client, chat)
    adds = [{"op": "add", "section": "model.ldr", "part": "3005.dat", "colour": 4, "position": [i * 20, -24, 0]}
            for i in range(60)]
    assert client.post(f"/api/chats/{chat}/editor/edits", json={"edits": adds, "base_revision": 1}).status_code == 200
    state = call(chat, "editor_state")                                    # call() asserts the 1000 character cap
    assert state["entries_total"] == 61 and state["entries_more"] > 0 and "(cut)" not in json.dumps(state)
    shown = len(state["entries"])
    page = call(chat, "editor_state", offset=shown)
    assert page["entries"][0] != state["entries"][0]
    assert call(chat, "editor_state", part="3005.dat")["entries_total"] == 61
    assert call(chat, "editor_state", part="9999")["entries_total"] == 0
    # editor_state after another edit still lists a `since` block inside the cap
    client.post(f"/api/chats/{chat}/editor/edits", json={"base_revision": 2, "edits": [
        {"op": "recolour", "id": e["id"], "colour": 1} for s in client.get(f"/api/chats/{chat}/editor").json()["plan"]["sections"]
        for step in s["steps"] for e in step]})
    after = call(chat, "editor_state")
    assert after["since"]["changed"] == 61 and len(after["since"]["changed_ids"]) <= 15


def test_editor_open_replaces_the_plan_after_the_persons_edit(client, chat):
    store = get_store()
    open_editor(client, chat)
    assert client.post(f"/api/chats/{chat}/editor/edits", json={"base_revision": 1, "edits": [
        {"op": "recolour", "id": "base", "colour": 2}]}).status_code == 200
    plan = {"version": 1, "author": "Tests", "sections": [{"name": "model.ldr", "description": "d", "steps": [
        [{"id": "a", "ref": "3005.dat", "colour": 4, "at": [0, 0, 0]}, {"id": "b", "ref": "3005.dat", "colour": 4, "at": [0, -24, 0]}]]}]}
    (store.work_dir(chat) / "rebuilt.json").write_text(json.dumps(plan))
    refused = call(chat, "editor_open", plan="rebuilt.json")
    assert refused["ok"] is False
    replaced = call(chat, "editor_open", plan="rebuilt.json", replace=True)
    assert replaced["ok"] and replaced["pieces"] == 2
    assert list((store.work_dir(chat) / "editor" / "archive").glob("*/plan.json"))
    assert {row[0] for row in call(chat, "editor_state")["entries"]} == {"a", "b"}


def test_editor_publish_message_is_not_a_turn_for_the_status_line():
    import usage
    messages = [{"role": "user", "content": "build", "created_at": 100}, {"role": "assistant", "_usage": {}, "created_at": 110},
                {"role": "user", "_editor_publish": True, "content": "edited", "created_at": 9000}]
    assert usage.turn_stats(messages) == {"turn_steps": 1, "duration_s": 10, "request_cost_eur": 0}
