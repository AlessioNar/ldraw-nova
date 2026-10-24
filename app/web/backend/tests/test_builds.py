"""Builds: the entity (create, list, detail, rename), generating versions with their settings, import, chats attached
to a build, and models that predate builds showing up as builds. The generate and publish parts run the real toolkit
like the other editor tests, so they need the container."""
import asyncio
import json
import os

import pytest
from fastapi.testclient import TestClient

import builds
import settings
import system_prompt
import tools
from main import app
from store import get_store

TINY = ("0 FILE tiny.ldr\n0 Two bricks\n0 Name: tiny.ldr\n0 Author: Tests\n0 !LDRAW_ORG Model\n"
        "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n0 STEP\n1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3003.dat\n0 NOFILE\n")


@pytest.fixture(autouse=True)
def traversable_tmp():
    os.chmod(settings.DATA_DIR.parent, 0o755)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def legacy():
    """A model from before builds: a file in data/generated, produced by a chat, with no build folder."""
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for n in (1, 2):
        path = settings.GENERATED_DIR / f"old-barn-v{n}.mpd"
        path.write_text(TINY.replace("Two bricks", f"Old barn {n}"), encoding="utf-8")
        paths.append(path)
    store = get_store()
    chat = store.create_chat(title="Make a barn")
    for path in paths:
        store.add_model(chat["id"], "Old barn", path, [])
    lonely = store.create_chat(title="Just talking")
    yield {"paths": paths, "chat": chat["id"], "lonely": lonely["id"]}
    for path in settings.GENERATED_DIR.glob("old-barn*"):
        path.unlink()


def test_create_list_detail_rename(client):
    created = client.post("/api/builds", json={"title": "My Red Car", "source": {"kind": "generator"}})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"] == "my-red-car" and body["build"]["key"] == "b-my-red-car"
    assert body["versions"] == [] and body["build"]["versions"] == 0 and body["source"]["kind"] == "generator"
    stored = json.loads((get_store().builds_dir / "my-red-car" / "build.json").read_text())
    assert stored["id"] == "b-my-red-car" and stored["title"] == "My Red Car" and stored["chats"] == []
    again = client.post("/api/builds", json={"title": "My Red Car"}).json()
    assert again["id"] == "my-red-car-2"                       # the same title never reuses a build
    assert client.post("/api/builds", json={}).json()["build"]["title"] == "Untitled build"
    listed = {b["id"]: b for b in client.get("/api/builds").json()["builds"]}
    assert listed["my-red-car"]["title"] == "My Red Car" and listed["my-red-car"]["latest"] is None
    assert client.get("/api/builds/my-red-car").json()["build"]["title"] == "My Red Car"
    renamed = client.patch("/api/builds/my-red-car", json={"title": "Fast car"})
    assert renamed.status_code == 200 and renamed.json()["build"]["title"] == "Fast car"
    assert client.get("/api/builds/my-red-car").json()["build"]["title"] == "Fast car"
    assert client.patch("/api/builds/my-red-car", json={"title": "   "}).status_code == 422
    assert client.get("/api/builds/ghost").status_code == 404
    assert client.get("/api/builds/..%2Fetc").status_code == 404


def test_import_a_model_file_makes_versions(client):
    slug = client.post("/api/builds", json={"title": "Imported tiny", "source": {"kind": "import"}}).json()["id"]
    assert client.post(f"/api/builds/{slug}/import", json={"filename": "x.txt", "content": TINY}).status_code == 422
    assert client.post(f"/api/builds/{slug}/import", json={"filename": "x.ldr", "content": "0 nothing here\n"}).status_code == 422
    first = client.post(f"/api/builds/{slug}/import", json={"filename": "tiny.ldr", "content": TINY})
    assert first.status_code == 200 and first.json() == {"version": 1, "file": f"{slug}-v1.ldr"}
    assert client.post(f"/api/builds/{slug}/import", json={"filename": "again.mpd", "content": TINY}).json()["version"] == 2
    detail = client.get(f"/api/builds/{slug}").json()
    assert [(v["version"], v["origin"]) for v in detail["versions"]] == [(1, "import"), (2, "import")]
    assert detail["build"]["latest"]["version"] == 2 and detail["build"]["source"]["filename"] == "again.mpd"
    listed = {b["id"]: b for b in client.get("/api/builds").json()["builds"]}
    assert listed[slug]["latest_origin"] == "import"
    for path in settings.GENERATED_DIR.glob(f"{slug}-v*"):
        path.unlink()


def test_legacy_models_are_builds_and_nothing_is_lost(client, legacy):
    before = sorted(p.read_text() for p in legacy["paths"])
    listed = {b["id"]: b for b in client.get("/api/builds").json()["builds"]}
    barn = listed["old-barn"]
    assert barn["versions"] == 2 and barn["latest"]["version"] == 2 and barn["title"] == "Old barn 2"
    assert barn["source"]["kind"] == "ai" and barn["chats"] == 1
    assert not (get_store().builds_dir / "old-barn" / "build.json").exists()          # listing writes nothing
    detail = client.get("/api/builds/old-barn").json()
    assert [(v["version"], v["origin"]) for v in detail["versions"]] == [(1, "ai"), (2, "ai")]
    assert detail["chats"][0]["id"] == legacy["chat"] and detail["versions"][1]["chat"]["id"] == legacy["chat"]
    # the first action stores the record; the files and the chat are untouched
    assert client.patch("/api/builds/old-barn", json={"title": "Barn"}).status_code == 200
    stored = json.loads((get_store().builds_dir / "old-barn" / "build.json").read_text())
    assert stored["title"] == "Barn" and stored["chats"] == [legacy["chat"]]
    assert sorted(p.read_text() for p in legacy["paths"]) == before
    assert get_store().get_chat(legacy["chat"])["title"] == "Make a barn"
    assert len(get_store().models(legacy["chat"])) == 2
    # the chat finds its build; a chat with no model stays among the older chats
    assert client.get(f"/api/chats/{legacy['chat']}").json()["build"]["id"] == "old-barn"
    assert client.get(f"/api/chats/{legacy['lonely']}").json()["build"] is None
    listing = client.get("/api/builds").json()
    older = {c["id"] for c in listing["older_chats"]}
    assert legacy["lonely"] in older and legacy["chat"] not in older
    assert {m["file"] for m in client.get("/api/models").json()["models"]} >= {"old-barn-v1.mpd", "old-barn-v2.mpd"}


def test_a_model_dropped_into_the_folder_is_a_build_too(client):
    path = settings.GENERATED_DIR / "dropped-in.mpd"
    path.write_text(TINY)
    try:
        detail = client.get("/api/builds/dropped-in").json()
        assert [(v["version"], v["file"], v["origin"]) for v in detail["versions"]] == [(1, "dropped-in.mpd", "import")]
        assert builds.next_version("dropped-in") == 2
    finally:
        path.unlink()


def test_generate_makes_versions_and_remembers_the_settings(client):
    generators = client.get("/api/generators").json()
    house = next(g for g in generators["generators"] if g["id"] == "house")
    assert house["params"]["properties"]["width"]["maximum"] == 24 and "tan" in {c["name"] for c in generators["colours"]}
    slug = client.post("/api/builds", json={"title": "Test house", "source": {"kind": "generator"}}).json()["id"]
    first = client.post(f"/api/builds/{slug}/generate", json={"generator": "house", "params": {"width": 8, "roof_colour": "blue"}})
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1 and first.json()["params"]["roof_colour"] == "blue"
    second = client.post(f"/api/builds/{slug}/generate", json={"generator": "house", "params": {"width": 10, "windows": 4}})
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2 and second.json()["file"] == f"{slug}-v2.mpd"
    assert (settings.GENERATED_DIR / f"{slug}-v1.mpd").is_file() and (settings.GENERATED_DIR / f"{slug}-v2.mpd").is_file()
    detail = client.get(f"/api/builds/{slug}").json()
    assert [(v["version"], v["origin"]) for v in detail["versions"]] == [(1, "generator"), (2, "generator")]
    assert detail["source"]["generator"] == "house" and detail["source"]["params"]["width"] == 10      # the form reopens with these
    assert detail["settings_open"] and detail["latest_from_generator"]
    assert get_store().models(f"b-{slug}")[1]["origin"] == "generator"
    assert client.post(f"/api/builds/{slug}/generate", json={"generator": "house", "params": {"width": 99}}).status_code == 422
    assert client.post(f"/api/builds/{slug}/generate", json={"generator": "nope", "params": {}}).status_code == 422
    assert len(client.get(f"/api/builds/{slug}").json()["versions"]) == 2                              # a failure adds nothing
    preview = client.post("/api/generators/preview", json={"generator": "house", "params": {"width": 6}})
    assert preview.status_code == 200, preview.text
    assert "1 " in client.get(preview.json()["model_url"]).text
    assert client.get("/api/generators/previews/..%2Fx.mpd").status_code in (404, 405)


def test_empty_build_opens_the_editor_and_saves_v1(client):
    slug = client.post("/api/builds", json={"title": "Blank start", "source": {"kind": "empty"}}).json()["id"]
    opened = client.post(f"/api/builds/{slug}/edit")
    assert opened.status_code == 200, opened.text
    assert opened.json()["editor_url"] == f"/build/{slug}/edit" and opened.json()["plan"]["sections"]
    assert opened.json()["plan"]["sections"][0]["steps"] == [[]]                 # a truly empty plan
    refused = client.post(f"/api/builds/{slug}/editor/publish", json={})
    assert refused.status_code == 400 and "Add at least one brick" in refused.text
    added = client.post(f"/api/builds/{slug}/editor/edits", json={"base_revision": opened.json()["revision"], "edits": [
        {"op": "add", "section": "model.ldr", "part": "3001.dat", "colour": 4, "position": [0, 0, 0]}]})
    assert added.status_code == 200, added.text
    saved = client.post(f"/api/builds/{slug}/editor/publish", json={})
    assert saved.status_code == 200, saved.text
    assert saved.json()["file"] == f"{slug}-v1.mpd"
    detail = client.get(f"/api/builds/{slug}").json()
    assert [(v["version"], v["origin"]) for v in detail["versions"]] == [(1, "editor")]
    assert detail["source"]["kind"] == "editor" and detail["build"]["source"]["kind"] == "editor"
    # editing again carries on from that version and saves v2
    assert client.post(f"/api/builds/{slug}/edit").json()["build"] == slug
    assert client.post(f"/api/builds/{slug}/editor/publish", json={}).json()["version"] == 2


def test_a_chat_attached_to_a_build_proposes_into_it(client):
    store = get_store()
    slug = client.post("/api/builds", json={"title": "Attached car", "source": {"kind": "ai"}}).json()["id"]
    chat = client.post(f"/api/builds/{slug}/chat", json={}).json()
    assert chat["build_id"] == slug and store.get_chat(chat["id"])["build_id"] == slug
    assert client.post(f"/api/builds/{slug}/chat", json={}).json()["id"] == chat["id"]            # reopened
    assert client.post(f"/api/builds/{slug}/chat", json={"new": True}).json()["id"] != chat["id"]
    ctx = tools.ToolContext(chat["id"], store, lambda *_: None)
    ctx.work_dir.mkdir(parents=True, exist_ok=True)
    (ctx.work_dir / "model.mpd").write_text(TINY)
    for _ in range(2):                                       # whatever name the agent picks, it is a proposal of the build, not a version
        result = asyncio.run(tools.t_publish_model(ctx, "output/model.mpd", "Something Else Entirely"))
        assert not result.models and "waiting for review" in result.content
    assert not list(settings.GENERATED_DIR.glob(f"{slug}-v*.mpd"))
    assert not list(settings.GENERATED_DIR.glob("something-else-entirely*"))
    assert len(client.get(f"/api/builds/{slug}/proposals").json()["proposals"]) == 2
    detail = client.get(f"/api/builds/{slug}").json()
    assert detail["versions"] == []
    assert chat["id"] in {c["id"] for c in detail["chats"] if c["attached"]}
    note = system_prompt.continuity_block(store, chat["id"])
    assert "belongs to the build" in note and "PROPOSAL" in note and "waiting for review" in note
    assert client.get(f"/api/builds/{slug}").json()["build"]["chats"] == 2
    assert chat["id"] not in {c["id"] for c in client.get("/api/builds").json()["older_chats"]}


def test_deleting_a_build_removes_its_versions_and_folder_but_keeps_its_chats(client):
    store = get_store()
    slug = client.post("/api/builds", json={"title": "Doomed build", "source": {"kind": "import"}}).json()["id"]
    for name in ("a.ldr", "b.ldr"):
        assert client.post(f"/api/builds/{slug}/import", json={"filename": name, "content": TINY}).status_code == 200
    chat = store.create_chat("About the doomed build", build_id=slug)
    builds.attach_chat(store, slug, chat["id"])
    assert len(list(settings.GENERATED_DIR.glob(f"{slug}-v*.ldr"))) == 2

    gone = client.delete(f"/api/builds/{slug}")
    assert gone.status_code == 200 and f"{slug}-v1.ldr" in gone.json()["deleted"]
    assert not list(settings.GENERATED_DIR.glob(f"{slug}-v*"))
    assert not builds.dir_of(store, slug).exists()
    assert client.get(f"/api/builds/{slug}").status_code == 404
    assert slug not in {b["id"] for b in client.get("/api/builds").json()["builds"]}
    kept = store.get_chat(chat["id"])
    assert kept is not None and not kept.get("build_id")
    assert client.delete(f"/api/builds/{slug}").status_code == 404
