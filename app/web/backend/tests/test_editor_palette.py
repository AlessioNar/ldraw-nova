"""The editor palette: colour table, part search with available quantities, and the inventory gate."""
import json
import os

import pytest
from fastapi.testclient import TestClient

import settings
from main import app
from routers import editor_palette as palette
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
    return get_store().create_chat()["id"]


@pytest.fixture
def inventories_dir(tmp_path, monkeypatch):
    path = tmp_path / "inventories"
    path.mkdir()
    monkeypatch.setattr(settings, "INVENTORIES_DIR", path)
    for folder in (path, *path.parents[:3]):      # the sandboxed toolkit user must be able to read it
        os.chmod(folder, 0o755)
    return path


def use_inventory(chat, inventories_dir, mode="only", rows=None):
    rows = rows if rows is not None else [{"part": "3005", "colour": 4, "qty": 2}, {"part": "3003", "colour": 1, "qty": 1}]
    (inventories_dir / "bricks.json").write_text(json.dumps({"id": "bricks", "name": "bricks", "rows": rows}))
    get_store().update_chat(chat, options={"parts_mode": mode, "inventory_ids": ["bricks"]})


def edit(client, chat, edits, revision):
    r = client.post(f"/api/chats/{chat}/editor/edits", json={"edits": edits, "base_revision": revision})
    assert r.status_code == 200, r.text
    return r.json()


def test_every_colour_offered_is_in_the_table(client):
    colours = client.get("/api/editor/colours").json()["colours"]
    assert colours and {c["code"] for c in colours} >= {0, 1, 4, 15}
    assert not {16, 24} & {c["code"] for c in colours}
    assert all(c["name"] and len(c["hex"]) == 7 and c["hex"][0] == "#" for c in colours)
    assert len({c["code"] for c in colours}) == len(colours)


def test_check_add_rules():
    owned, used = {("3005", 4): 2}, {("3005", 4): 1}
    assert palette.check_add("off", owned, used, "3005.dat", 4)["allowed"]
    ok = palette.check_add("only", owned, used, "3005.dat", 4)
    assert ok["allowed"] and ok["available"] == 1 and ok["warning"] is None
    refused = palette.check_add("only", owned, used, "3005", 4, 2)
    assert not refused["allowed"] and refused["message"] == "The inventory cannot cover this edit"
    assert refused["short"] == [["3005", 4, 3, 2]]
    warned = palette.check_add("prefer", owned, used, "3003.dat", 1)
    assert warned["allowed"] and "Not in your inventory" in warned["warning"]


def test_unknown_colour_and_bad_part_are_rejected(client, chat):
    assert client.post(f"/api/chats/{chat}/editor/palette/check", json={"part": "3005", "colour": 99999}).status_code == 400
    assert client.post(f"/api/chats/{chat}/editor/palette/check", json={"part": "../x", "colour": 4}).status_code == 400
    assert client.get(f"/api/chats/{chat}/editor/palette?query=brick&colour=24").status_code == 400
    assert client.get("/api/chats/nope/editor/palette?query=brick").status_code == 404


def test_library_search_without_inventories(client, chat):
    body = client.get(f"/api/chats/{chat}/editor/palette?query=3005").json()
    assert body["mode"] == "off" and body["items"]
    first = body["items"][0]
    assert first["part"] == "3005" and first["description"] and first["owned_total"] is None
    assert client.get(f"/api/chats/{chat}/editor/palette").json()["items"] == []
    assert client.get(f"/api/chats/{chat}/editor/palette?inventory_only=true").status_code == 400


def test_available_is_owned_minus_used_and_returns_on_delete(client, chat, inventories_dir):
    use_inventory(chat, inventories_dir)
    r = client.post(f"/api/chats/{chat}/editor", json={})
    assert r.status_code == 200, r.text
    def search():
        r = client.get(f"/api/chats/{chat}/editor/palette?query=3005&inventory_only=true")
        assert r.status_code == 200, r.text
        return r.json()
    row = lambda: next(i for i in search()["items"] if i["part"] == "3005")["colours"][0]  # noqa: E731
    assert row()["owned"] == 2
    used_before = row()["used"]
    placed = edit(client, chat, [{"op": "add", "section": "model.ldr", "part": "3005.dat", "colour": 4,
                                  "position": [0, -24, 0]}], 1)
    assert row()["used"] == used_before + 1 and row()["available"] == max(2 - row()["used"], 0)
    edit(client, chat, [{"op": "delete", "id": "e1"}], placed["revision"])
    assert row()["used"] == used_before and row()["available"] == 2 - used_before


def test_used_up_part_is_flagged_and_gate_refuses_in_only_mode(client, chat, inventories_dir):
    use_inventory(chat, inventories_dir, rows=[{"part": "3003", "colour": 1, "qty": 1}, {"part": "3005", "colour": 4, "qty": 1}])
    assert client.post(f"/api/chats/{chat}/editor", json={}).status_code == 200
    found = client.get(f"/api/chats/{chat}/editor/palette?query=3005&inventory_only=true")
    assert found.status_code == 200, found.text
    item = found.json()["items"][0]
    assert item["used_up"] is True and item["available_total"] == 0
    check = client.post(f"/api/chats/{chat}/editor/palette/check", json={"part": "3005", "colour": 4}).json()
    assert check["allowed"] is False and check["message"] == "The inventory cannot cover this edit"
    refused = client.post(f"/api/chats/{chat}/editor/edits", json={"base_revision": 1, "edits": [
        {"op": "add", "section": "model.ldr", "part": "3005.dat", "colour": 4, "position": [0, -24, 0]}]})
    assert refused.status_code == 400 and refused.json()["detail"]["detail"] == check["message"]


def test_prefer_mode_warns_but_allows(client, chat, inventories_dir):
    use_inventory(chat, inventories_dir, mode="prefer")
    check = client.post(f"/api/chats/{chat}/editor/palette/check", json={"part": "3001.dat", "colour": 4}).json()
    assert check["allowed"] is True and check["warning"] == "Not in your inventory"
