"""The build workspace API: draft open / save / discard, regenerate, describe, parts check and proposals.

Like the other editor tests every call runs the real toolkit in the container (and a display for the renders a
save makes). The fast-path language model is never called: `fast_path.describe` is replaced.
"""
import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

import builds
import fast_path
import settings
import tools
import workspace
from main import app
from routers import workspace_api
from store import get_store

TINY = ("0 FILE tiny.ldr\n0 Two bricks\n0 Name: tiny.ldr\n0 Author: Tests\n0 !LDRAW_ORG Model\n"
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n0 STEP\n1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat\n0 NOFILE\n")
THREE = TINY.replace("0 NOFILE\n", "0 STEP\n1 2 40 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n0 NOFILE\n")
MOVE = {"op": "move", "id": "p1", "position": [100, 0, 0]}


@pytest.fixture(autouse=True)
def traversable_tmp():
    os.chmod(settings.DATA_DIR.parent, 0o755)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def make_build(client, title, *models):
    slug = client.post("/api/builds", json={"title": title}).json()["id"]
    for text in models:
        assert client.post(f"/api/builds/{slug}/import", json={"filename": "m.mpd", "content": text}).status_code == 200
    return slug


def edit(client, slug, view, *edits):
    r = client.post(f"/api/builds/{slug}/editor/edits", json={"edits": list(edits), "base_revision": view["revision"]})
    assert r.status_code == 200, r.text
    return r.json()


def undo(client, slug, view):
    r = client.post(f"/api/builds/{slug}/editor/undo", json={"base_revision": view["revision"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_open_latest_resume_and_changes_count(client):
    slug = make_build(client, "ws open", TINY, THREE)
    opened = client.post(f"/api/builds/{slug}/draft/open", json={})
    assert opened.status_code == 200, opened.text
    view = opened.json()
    assert view["build"] == slug and view["draft"]["base_version"] == 2 and view["draft"]["changes"] == 0
    assert sum(len(step) for step in view["plan"]["sections"][0]["steps"]) == 3
    assert (get_store().builds_dir / slug / "draft.json").is_file()
    moved = edit(client, slug, view, MOVE)
    ws = client.get(f"/api/builds/{slug}/workspace").json()
    assert ws["draft"]["changes"] == 1 and ws["draft"]["revision"] == moved["revision"] and ws["draft"]["undo_depth"] == 1
    assert ws["draft"]["updated_at"]
    again = client.post(f"/api/builds/{slug}/draft/open", json={}).json()           # resumes, nothing lost
    assert again["revision"] == moved["revision"] and again["draft"]["changes"] == 1 and "1 4 100 0 0" in again["mpd"]
    assert client.post(f"/api/builds/{slug}/draft/open", json={"version": 2}).json()["draft"]["changes"] == 1
    undone = undo(client, slug, moved)
    assert client.get(f"/api/builds/{slug}/workspace").json()["draft"]["changes"] == 0 and undone["undo_depth"] == 0
    assert client.post("/api/builds/nope/draft/open", json={}).status_code == 404


def test_open_another_version_conflicts_until_forced(client):
    slug = make_build(client, "ws conflict", TINY, THREE)
    view = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    edit(client, slug, view, MOVE)
    refused = client.post(f"/api/builds/{slug}/draft/open", json={"version": 1})
    assert refused.status_code == 409 and refused.json()["changes"] == 1
    assert client.get(f"/api/builds/{slug}/workspace").json()["draft"]["changes"] == 1       # still there
    forced = client.post(f"/api/builds/{slug}/draft/open", json={"version": 1, "force": True})
    assert forced.status_code == 200, forced.text
    assert forced.json()["draft"]["base_version"] == 1 and forced.json()["draft"]["changes"] == 0
    assert sum(len(step) for step in forced.json()["plan"]["sections"][0]["steps"]) == 2
    # a clean draft moves to another version without asking
    assert client.post(f"/api/builds/{slug}/draft/open", json={"version": 2}).json()["draft"]["base_version"] == 2
    assert client.post(f"/api/builds/{slug}/draft/open", json={"version": 9}).status_code == 404


def test_empty_build_opens_an_editable_plan(client):
    slug = make_build(client, "ws empty")
    opened = client.post(f"/api/builds/{slug}/draft/open", json={})
    assert opened.status_code == 200, opened.text
    assert opened.json()["draft"]["base_version"] is None and opened.json()["draft"]["changes"] == 0
    ws = client.get(f"/api/builds/{slug}/workspace").json()
    assert ws["versions"] == [] and ws["generator"] is None and ws["proposals"] == [] and ws["chat_id"] is None


def test_save_makes_the_next_version_and_discard_goes_back(client):
    slug = make_build(client, "ws save", TINY)
    view = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    assert client.post(f"/api/builds/{slug}/draft/save").status_code == 409            # nothing to save
    moved = edit(client, slug, view, MOVE)
    saved = client.post(f"/api/builds/{slug}/draft/save")
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 2 and body["file"] == f"{slug}-v2.mpd" and body["message"] == "Saved as v2 in My Builds"
    assert "1 4 100 0 0" in (settings.GENERATED_DIR / body["file"]).read_text()
    ws = client.get(f"/api/builds/{slug}/workspace").json()
    assert ws["draft"]["base_version"] == 2 and ws["draft"]["changes"] == 0
    assert [(v["version"], v["origin"]) for v in ws["versions"]] == [(1, "import"), (2, "editor")]
    # edit again, then discard: back to v2 as saved
    again = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    edit(client, slug, again, {"op": "move", "id": "p1", "position": [160, 0, 0]})
    discarded = client.post(f"/api/builds/{slug}/draft/discard")
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["draft"]["changes"] == 0 and "1 4 100 0 0" in discarded.json()["mpd"]
    assert "1 4 160 0 0" not in discarded.json()["mpd"]
    assert moved["revision"] >= 1
    assert client.post("/api/builds/ws-no-such/draft/discard").status_code == 404


def test_regenerate_is_one_undoable_step(client):
    slug = make_build(client, "ws regen", TINY)
    view = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    first = client.post(f"/api/builds/{slug}/draft/regenerate", json={"generator": "house", "params": {"width": 8}})
    assert first.status_code == 200, first.text
    assert first.json()["replaced_manual_edits"] is False and first.json()["generator"]["id"] == "house"
    assert first.json()["draft"]["changes"] == 1
    house = client.get(f"/api/builds/{slug}/editor").json()
    assert house["revision"] == view["revision"] + 1 and house["undo_depth"] == 1
    moved = edit(client, slug, house, {"op": "delete", "id": house["plan"]["sections"][0]["steps"][0][0]["id"]})
    before = client.get(f"/api/builds/{slug}/editor").json()["plan"]
    second = client.post(f"/api/builds/{slug}/draft/regenerate", json={"generator": "house", "params": {"width": 10}})
    assert second.status_code == 200 and second.json()["replaced_manual_edits"] is True
    after = client.get(f"/api/builds/{slug}/editor").json()
    assert after["plan"] != before and after["undo_depth"] == moved["undo_depth"] + 1
    restored = undo(client, slug, after)                                  # ONE undo brings the earlier plan back exactly
    assert restored["plan"] == before and restored["undo_depth"] == moved["undo_depth"]
    ws = client.get(f"/api/builds/{slug}/workspace").json()
    assert ws["generator"]["id"] == "house" and ws["generator"]["params"]["width"] == 10        # the form reopens with these
    assert client.post(f"/api/builds/{slug}/draft/regenerate", json={"generator": "nope", "params": {}}).status_code == 422


def test_describe_regenerates_for_generator_and_edit_routes_and_spends_nothing_otherwise(client, monkeypatch):
    slug = make_build(client, "ws describe", TINY)
    seen = []

    async def fake(text, current=None, build_id=None, **kw):
        seen.append(current)
        return answers[text]

    answers = {
        "a house": {"route": "generator", "generator": "house", "params": {"width": 8}, "notes": [], "cost_eur": 0.0},
        "wider": {"route": "edit", "generator": "house", "params": {"width": 12}, "changes": [], "notes": [], "cost_eur": 0.0},
        "a castle": {"route": "needs_ai", "reason": "no generator", "estimate": {"eur_low": 1.0}},
        "hmm": {"route": "clarify", "question": "What?"},
    }
    monkeypatch.setattr(fast_path, "describe", fake)
    done = client.post(f"/api/builds/{slug}/draft/describe", json={"text": "a house"})
    assert done.status_code == 200, done.text
    assert done.json()["route"] == "generator" and done.json()["draft"]["changes"] == 1 and seen[0] is None
    view = client.get(f"/api/builds/{slug}/editor").json()
    edited = client.post(f"/api/builds/{slug}/draft/describe", json={"text": "wider"}).json()
    assert edited["route"] == "edit" and edited["draft"]["changes"] == 2
    assert seen[1] == {"generator": "house", "params": {"width": 8, **seen[1]["params"]}}      # the build's generator source
    assert client.get(f"/api/builds/{slug}/editor").json()["revision"] == view["revision"] + 1

    async def nothing(payload):
        raise AssertionError("the generator must not run")

    monkeypatch.setattr(workspace_api, "_worker", nothing)
    revision = client.get(f"/api/builds/{slug}/editor").json()["revision"]
    for text, route in (("a castle", "needs_ai"), ("hmm", "clarify")):
        reply = client.post(f"/api/builds/{slug}/draft/describe", json={"text": text})
        assert reply.status_code == 200 and reply.json()["route"] == route and reply.json()["draft"]["changes"] == 2
    assert client.get(f"/api/builds/{slug}/editor").json()["revision"] == revision               # untouched
    assert client.post(f"/api/builds/{slug}/draft/describe", json={"text": "  "}).status_code == 422


def test_describe_by_rules_needs_no_model(client, monkeypatch):
    """A rule answers 'make the roof blue' on a generator build: no language model is called."""
    import litellm
    import generator_bridge
    from pathlib import Path
    registry = json.loads((Path(__file__).with_name("generators_registry.json")).read_text(encoding="utf-8"))
    generator_bridge.set_registry(registry)

    async def boom(**kwargs):
        raise AssertionError("no model call expected")

    monkeypatch.setattr(litellm, "acompletion", boom)
    slug = make_build(client, "ws rules", TINY)
    client.post(f"/api/builds/{slug}/draft/regenerate", json={"generator": "house", "params": {}})
    try:
        reply = client.post(f"/api/builds/{slug}/draft/describe", json={"text": "make the roof blue"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["route"] == "edit" and reply.json()["understanding_cost_eur"] == 0.0
        assert reply.json()["draft"]["changes"] == 2
    finally:
        generator_bridge.set_registry(None)


@pytest.fixture
def parts_lists():
    folder = settings.INVENTORIES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for path in (folder, *folder.parents[:3]):         # the sandboxed toolkit user reads them for the palette search
        os.chmod(path, 0o755)
    made = []
    for name, rows, review in (("ws-first", [{"part": "3001", "colour": 4, "qty": 3}], [{"part": "3003", "colour": 1, "qty": 9}]),
                               ("ws-second", [{"part": "3001", "colour": 4, "qty": 1}, {"part": "3003", "colour": 1, "qty": 0}], [])):
        path = folder / f"{name}.json"
        path.write_text(json.dumps({"id": name, "name": name, "created_at": 1.0, "rows": rows, "review": review}))
        made.append(path)
    yield
    for path in made:
        path.unlink()


def test_parts_check_uses_every_list_and_ignores_review_pieces(client, parts_lists):
    slug = make_build(client, "ws parts", THREE)
    view = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    # THREE needs 3001 blue x1, 3003 blue... : 3001/4 x1, 3003/1 x1, 3001/2 x1
    check = client.get(f"/api/builds/{slug}/draft/parts-check").json()
    assert check["needed"] == 3 and check["owned"] == 1
    assert sorted((m["part"], m["colour"], m["need"], m["have"]) for m in check["missing"]) == [("3001", 2, 1, 0), ("3003", 1, 1, 0)]
    ws = client.get(f"/api/builds/{slug}/workspace").json()
    assert ws["versions"][0]["parts"] == 3 and ws["versions"][0]["parts_check"] == {"owned": 1, "needed": 3, "missing": 2}
    moved = edit(client, slug, view, {"op": "add", "section": "tiny.ldr", "part": "3001.dat", "colour": 4, "position": [0, -48, 0]})
    again = client.get(f"/api/builds/{slug}/draft/parts-check").json()          # 3001/4 x2 of 4 owned (3 + 1)
    assert again["needed"] == 4 and again["owned"] == 2 and moved["revision"] > view["revision"]
    # the content-hash cache answers the same numbers
    assert client.get(f"/api/builds/{slug}/draft/parts-check").json() == again
    assert workspace.part_usage(THREE) == {("3001", 4): 1, ("3003", 1): 1, ("3001", 2): 1}


def test_part_usage_expands_submodels_and_colour_16():
    text = ("0 FILE main.ldr\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 sub.ldr\n1 1 0 0 0 1 0 0 0 1 0 0 0 1 sub.ldr\n"
            "0 NOFILE\n0 FILE sub.ldr\n1 16 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n1 2 0 0 0 1 0 0 0 1 0 0 0 1 s\\3003.dat\n0 NOFILE\n")
    assert workspace.part_usage(text) == {("3001", 4): 1, ("3001", 1): 1, ("3003", 2): 2}


def publish_from_chat(client, slug, text):
    store = get_store()
    chat = client.post(f"/api/builds/{slug}/chat", json={"new": True}).json()
    ctx = tools.ToolContext(chat["id"], store, lambda *_: None)
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "model.mpd").write_text(text)
    result = asyncio.run(tools.t_publish_model(ctx, "output/model.mpd", "Whatever Name"))
    return chat, result


def test_a_chat_publish_becomes_a_proposal_accepted_as_one_undo_step(client):
    slug = make_build(client, "ws proposals", TINY)
    chat, result = publish_from_chat(client, slug, THREE)
    assert not result.models and "waiting for review" in json.loads(result.content)["message"]
    assert [p.name for p in settings.GENERATED_DIR.glob(f"{slug}-v*.mpd")] == [f"{slug}-v1.mpd"]    # no new version
    listed = client.get(f"/api/builds/{slug}/proposals").json()["proposals"]
    assert len(listed) == 1
    proposal = listed[0]
    assert proposal["summary"] == {"added": 1, "removed": 0, "moved": 0} and proposal["chat_id"] == chat["id"]
    assert set(proposal) >= {"id", "created_at", "summary", "cost_eur", "chat_id"}
    assert (get_store().builds_dir / slug / "proposals" / f"{proposal['id']}.mpd").is_file()
    ws = client.get(f"/api/builds/{slug}/workspace").json()
    assert ws["proposals"] == listed and ws["chat_id"] == chat["id"] and ws["draft"] is None
    assert [v["version"] for v in ws["versions"]] == [1]
    preview = client.get(f"/api/builds/{slug}/proposals/{proposal['id']}.mpd")
    assert preview.status_code == 200 and preview.text == THREE
    assert client.get(f"/api/builds/{slug}/proposals/{'0' * 12}.mpd").status_code == 404
    assert client.get(f"/api/builds/{slug}/proposals/..%2Fbuild.json.mpd").status_code in (404, 405)

    view = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    moved = edit(client, slug, view, MOVE)
    before = client.get(f"/api/builds/{slug}/editor").json()["plan"]
    accepted = client.post(f"/api/builds/{slug}/proposals/{proposal['id']}/accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["proposals"] == [] and accepted.json()["replaced_manual_edits"] is True
    now = client.get(f"/api/builds/{slug}/editor").json()
    assert now["undo_depth"] == moved["undo_depth"] + 1 and sum(len(s) for s in now["plan"]["sections"][0]["steps"]) == 3
    assert undo(client, slug, now)["plan"] == before                       # one undo restores it exactly
    assert client.post(f"/api/builds/{slug}/proposals/{proposal['id']}/accept").status_code == 404


def test_discard_a_proposal_and_accept_without_a_draft(client):
    slug = make_build(client, "ws discard", TINY)
    publish_from_chat(client, slug, THREE)
    publish_from_chat(client, slug, THREE.replace("3003.dat", "3005.dat"))
    first, second = client.get(f"/api/builds/{slug}/proposals").json()["proposals"]
    assert client.post(f"/api/builds/{slug}/proposals/{first['id']}/discard").json()["proposals"] == [second]
    assert not (get_store().builds_dir / slug / "proposals" / f"{first['id']}.mpd").exists()
    assert client.post(f"/api/builds/{slug}/proposals/{first['id']}/discard").status_code == 404
    accepted = client.post(f"/api/builds/{slug}/proposals/{second['id']}/accept")       # opens the draft first
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["draft"]["base_version"] == 1 and accepted.json()["draft"]["changes"] == 1


def test_workspace_get_shape(client):
    slug = make_build(client, "ws shape", TINY)
    ws = client.get(f"/api/builds/{slug}/workspace").json()
    assert set(ws) == {"build", "versions", "draft", "generator", "proposals", "chat_id"}
    assert ws["build"]["id"] == slug and ws["draft"] is None and ws["proposals"] == []
    version = ws["versions"][0]
    assert set(version) >= {"version", "file", "origin", "parts", "changes", "parts_check"}
    assert version["parts"] == 2 and set(version["parts_check"]) == {"owned", "needed", "missing"}
    assert client.get("/api/builds/ws-ghost/workspace").status_code == 404
    assert builds.exists(get_store(), slug)


def changes_of(client, slug):
    return client.get(f"/api/builds/{slug}/workspace").json()["draft"]["changes"]


def redo(client, slug, view):
    r = client.post(f"/api/builds/{slug}/editor/redo", json={"base_revision": view["revision"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_changes_are_the_net_number_of_edits(client):
    slug = make_build(client, "ws net", TINY)
    view = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    one = edit(client, slug, view, MOVE)
    assert changes_of(client, slug) == 1
    two = edit(client, slug, one, {"op": "move", "id": "p1", "position": [160, 0, 0]})
    assert changes_of(client, slug) == 2
    back = undo(client, slug, two)
    assert changes_of(client, slug) == 1
    clean = undo(client, slug, back)
    assert changes_of(client, slug) == 0 and client.post(f"/api/builds/{slug}/draft/save").status_code == 409
    again = redo(client, slug, clean)
    assert changes_of(client, slug) == 1
    redo(client, slug, again)
    assert changes_of(client, slug) == 2


def test_undo_below_the_base_and_a_new_edit_are_never_clean(client):
    slug = make_build(client, "ws below", TINY)
    view = client.post(f"/api/builds/{slug}/draft/open", json={}).json()
    edit_one = edit(client, slug, view, MOVE)
    saved = client.post(f"/api/builds/{slug}/draft/save")
    assert saved.status_code == 200 and changes_of(client, slug) == 0
    view = client.get(f"/api/builds/{slug}/editor").json()
    below = undo(client, slug, view)                              # undoes the saved edit: differs from v2
    assert changes_of(client, slug) == 1 and edit_one["undo_depth"] == 1
    forked = edit(client, slug, below, {"op": "move", "id": "p1", "position": [160, 0, 0]})
    assert changes_of(client, slug) == 2                          # one step below plus the new edit
    undo(client, slug, forked)
    assert changes_of(client, slug) == 1


def test_the_palette_of_a_build_uses_the_whole_collection(client, parts_lists):
    slug = make_build(client, "ws palette", THREE)
    assert client.post(f"/api/builds/{slug}/draft/open", json={}).status_code == 200
    found = client.get(f"/api/builds/{slug}/editor/palette?query=3001&inventory_only=true")
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["mode"] == "prefer"
    item = next(i for i in body["items"] if i["part"] == "3001")
    blue = next(c for c in item["colours"] if c["colour"] == 4)
    assert blue["owned"] == 4 and blue["used"] == 1 and blue["available"] == 3          # 3 + 1 from two lists, 1 used in the draft
    assert not any(c["colour"] == 2 for c in item["colours"])                           # the red 3001 is not owned
    check = client.post(f"/api/builds/{slug}/editor/palette/check", json={"part": "3001", "colour": 2}).json()
    assert check["allowed"] is True and check["warning"] == "Not in your inventory"


def test_the_palette_of_a_build_without_parts_lists_refuses_inventory_search(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "INVENTORIES_DIR", tmp_path / "none")
    slug = make_build(client, "ws palette none", TINY)
    client.post(f"/api/builds/{slug}/draft/open", json={})
    assert client.get(f"/api/builds/{slug}/editor/palette?query=3001&inventory_only=true").status_code == 400
