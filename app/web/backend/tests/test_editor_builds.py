"""Editing a My Builds model in the 3D editor with no chat: open, edit, save as the next version.

Like test_editor_api, every call goes through the real toolkit CLI in the container.
"""
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import settings
from main import app
from store import get_store

REAL = Path(__file__).resolve().parents[3] / "data" / "generated" / "willowbank-cottage-v1.mpd"
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
def tiny():
    path = settings.GENERATED_DIR / "tiny-build-v1.mpd"
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(TINY, encoding="utf-8")
    yield path
    for p in settings.GENERATED_DIR.glob("tiny-build-v*"):
        p.unlink()


def test_open_a_build_without_a_chat(client, tiny):
    chats_before = [c["id"] for c in get_store().list_chats()]
    r = client.post(f"/api/models/{tiny.name}/edit")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["build"] == "tiny-build" and view["editor_url"] == "/build/tiny-build/edit"
    assert [e["ref"] for e in view["plan"]["sections"][0]["steps"][0]] == ["3001.dat"] and view["warnings"] == []
    assert "1 1 0 -24 0" in view["mpd"]
    assert [c["id"] for c in get_store().list_chats()] == chats_before            # no chat appeared
    assert client.get("/api/builds/tiny-build/editor").json()["revision"] == view["revision"]
    assert client.get("/api/chats/b-tiny-build/editor").status_code == 404        # a build is not a chat
    # opening it again resumes the session instead of resetting it
    edited = client.post("/api/builds/tiny-build/editor/edits", json={
        "edits": [{"op": "move", "id": "p1", "position": [40, 0, 0]}], "base_revision": view["revision"]})
    assert edited.status_code == 200, edited.text
    again = client.post(f"/api/models/{tiny.name}/edit").json()
    assert again["revision"] == edited.json()["revision"] and "1 4 40 0 0" in again["mpd"]


def test_unknown_model_and_bad_names(client):
    assert client.post("/api/models/nope-v1.mpd/edit").status_code == 404
    assert client.post("/api/models/..%2Fetc%2Fpasswd/edit").status_code in (404, 405)
    assert client.get("/api/builds/ghost/editor").status_code == 404


def test_save_is_the_next_version_in_my_builds(client, tiny):
    chats_before = [c["id"] for c in get_store().list_chats()]
    revision = client.post(f"/api/models/{tiny.name}/edit").json()["revision"]
    moved = client.post("/api/builds/tiny-build/editor/edits", json={
        "edits": [{"op": "move", "id": "p1", "position": [60, 0, 0]}], "base_revision": revision})
    assert moved.status_code == 200, moved.text
    saved = client.post("/api/builds/tiny-build/editor/publish", json={"name": "ignored"})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["version"] == 2 and body["file"] == "tiny-build-v2.mpd" and body["message"] == "Saved as v2 in My Builds"
    v2 = settings.GENERATED_DIR / "tiny-build-v2.mpd"
    assert "1 4 60 0 0" in v2.read_text() and "1 4 0 0 0" in tiny.read_text()        # the original is kept
    # preview and parts list come from the same render publish_model does (which needs a display; the browser check covers it)
    assert (v2.with_suffix(".png").is_file() and v2.with_suffix(".csv").is_file()) or any(
        "rendering failed" in w for w in body["publish"]["warnings"])
    listed = {m["file"]: m for m in client.get("/api/models").json()["models"]}
    assert listed["tiny-build-v2.mpd"]["origin"] == "editor" and listed["tiny-build-v1.mpd"]["origin"] is None
    assert [c["id"] for c in get_store().list_chats()] == chats_before
    # the session continues from v2: Edit on v2 resumes, a second save is v3
    assert client.post("/api/models/tiny-build-v2.mpd/edit").json()["revision"] == moved.json()["revision"]
    assert client.post("/api/builds/tiny-build/editor/publish", json={}).json()["version"] == 3


def test_editing_a_chat_built_model_leaves_the_chats_editor_alone(client, tiny):
    store = get_store()
    chat = store.create_chat()["id"]
    store.add_model(chat, "Tiny build", tiny, [])
    opened = client.post(f"/api/chats/{chat}/editor", json={})
    assert opened.status_code == 200, opened.text
    chat_revision = opened.json()["revision"]
    client.post(f"/api/models/{tiny.name}/edit")
    client.post("/api/builds/tiny-build/editor/edits", json={
        "edits": [{"op": "move", "id": "p1", "position": [20, 0, 0]}],
        "base_revision": 1})
    chat_view = client.get(f"/api/chats/{chat}/editor").json()
    assert chat_view["revision"] == chat_revision and chat_view["plan"]["sections"][0]["steps"][0][0]["id"] == "base"
    assert any(m["origin"] is None for m in client.get("/api/models").json()["models"] if m["file"] == tiny.name)


@pytest.mark.skipif(not REAL.is_file(), reason="showcase model not in this checkout")
def test_a_real_generated_model_opens_and_edits(client):
    target = settings.GENERATED_DIR / REAL.name
    settings.GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REAL, target)
    try:
        view = client.post(f"/api/models/{REAL.name}/edit").json()
        assert view["build"] == "willowbank-cottage" and view["warnings"] == []
        assert len(view["plan"]["sections"]) > 1 and view["parts"]["rows"]
    finally:
        target.unlink()
