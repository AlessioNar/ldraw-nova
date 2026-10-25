"""Reservations ("I built this"), set built/parts, available = owned - reserved, shortage lists and their exports."""
import json

import pytest
from fastapi.testclient import TestClient

import collection_store
import inventories
import part_info
import reservations
import settings
import workspace
from main import app

META = {"3001": ("Brick 2 x 4", "Brick"), "3003": ("Brick 2 x 2", "Brick"), "=evil": ("=HYPERLINK(1)", "x")}
COLOURS = {1: "Blue", 4: "Red", 14: "Yellow"}
MODEL = "0 Test\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n1 1 0 0 0 1 0 0 0 1 0 0 0 1 3003.dat\n"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INVENTORIES_DIR", tmp_path / "inventories")
    monkeypatch.setattr(settings, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(part_info, "meta", lambda: META)
    monkeypatch.setattr(part_info, "colour_names", lambda: COLOURS)
    settings.GENERATED_DIR.mkdir()
    (settings.GENERATED_DIR / "truck-v1.mpd").write_text(MODEL, encoding="utf-8")
    inventories.clear_caches()
    yield
    inventories.clear_caches()


def put(source_id, rows, **extra):
    data = {"id": source_id, "name": source_id, "created_at": 1.0, "source_filename": None,
            "rows": [dict(part=p, colour=c, qty=q, rb_part=p, rb_colour=0) for p, c, q in rows],
            "unmapped_parts": [], "unmapped_colours": [], "warnings": [], **extra}
    settings.INVENTORIES_DIR.mkdir(parents=True, exist_ok=True)
    (settings.INVENTORIES_DIR / f"{source_id}.json").write_text(json.dumps(data), encoding="utf-8")
    inventories.clear_caches()


def test_reserving_holds_the_model_parts_and_releasing_frees_them(client):
    put("mine", [("3001", 4, 5), ("3003", 1, 1)])
    r = client.post("/api/collection/reservations", json={"model": "truck-v1.mpd"})
    assert r.status_code == 200
    body = r.json()
    assert body["shortage"]["missing"] == 0 and body["reservation"]["model"] == "truck-v1.mpd"
    free, _ = reservations.available_totals()
    assert free[("3001", 4)] == 3 and free[("3003", 1)] == 0
    listed = client.get("/api/collection/reservations").json()["reservations"]
    assert len(listed) == 1 and listed[0]["pieces"] == 3 and listed[0]["short"] == []
    assert client.delete(f"/api/collection/reservations/{listed[0]['id']}").status_code == 200
    assert reservations.available_totals()[0][("3001", 4)] == 5
    assert client.delete(f"/api/collection/reservations/{listed[0]['id']}").status_code == 404


def test_reserving_again_replaces_and_a_reservation_may_exceed_ownership(client):
    put("mine", [("3001", 4, 1)])
    client.post("/api/collection/reservations", json={"model": "truck-v1.mpd"})
    client.post("/api/collection/reservations", json={"model": "truck-v1.mpd"})
    listed = client.get("/api/collection/reservations").json()["reservations"]
    assert len(listed) == 1
    assert {(s["part"], s["colour"], s["short"]) for s in listed[0]["short"]} == {("3001", 4, 1), ("3003", 1, 1)}
    assert reservations.available_totals()[0][("3001", 4)] == 0          # never negative


def test_a_built_set_holds_its_parts_until_it_is_taken_apart(client):
    put("set-1", [("3001", 4, 4)], kind="set", set={"set_num": "1-1", "state": "built", "copies": 1})
    assert reservations.available_totals()[0][("3001", 4)] == 0
    assert client.put("/api/collection/sources/set-1/set-state", json={"state": "parts"}).status_code == 200
    assert reservations.available_totals()[0][("3001", 4)] == 4
    assert client.put("/api/collection/sources/set-1/set-state", json={"state": "bogus"}).status_code == 400
    put("plain", [("3001", 4, 1)])
    assert client.put("/api/collection/sources/plain/set-state", json={"state": "built"}).status_code == 400


def test_a_builds_own_reservation_does_not_count_against_its_draft(client):
    put("mine", [("3001", 4, 2), ("3003", 1, 1)])
    client.post("/api/collection/reservations", json={"model": "truck-v1.mpd"})
    assert reservations.available_totals()[0][("3001", 4)] == 0
    free, signature = workspace.available_parts("truck")
    assert free[("3001", 4)] == 2 and signature != workspace.available_parts("other")[1]


def test_shortage_lists_what_is_missing_with_substitutes_and_maybe(client):
    put("mine", [("3001", 4, 1), ("3001", 14, 6), ("3003", 1, 1)],
        review=[{"id": "r1", "part": "3001", "family": "Reds", "candidates": [4, 320], "qty": 3}])
    exact = client.get("/api/collection/shortage?model=truck-v1.mpd").json()
    assert exact["needed"] == 3 and exact["covered"] == 2 and exact["missing"] == 1
    line = exact["lines"][0]
    assert (line["part"], line["colour"], line["missing"], line["maybe"]) == ("3001", 4, 1, 3)
    assert line["substitutes"] == [{"colour": 14, "colour_name": "Yellow", "available": 6}]
    assert "missing_with_substitutes" not in line
    sub = client.get("/api/collection/shortage?model=truck-v1.mpd&mode=substitute").json()
    assert sub["missing"] == 1 and sub["missing_with_substitutes"] == 0
    assert client.get("/api/collection/shortage?model=truck-v1.mpd&mode=x").status_code == 400


def test_shortage_exports_as_csv_and_text_and_escapes_formulas(client):
    put("mine", [])
    csv_body = client.get("/api/collection/shortage/export?model=truck-v1.mpd&format=csv")
    assert csv_body.headers["content-disposition"].endswith('truck-v1-wanted.csv"')
    assert csv_body.text.splitlines()[0] == "part,description,colour,colour_name,needed,owned,reserved,missing"
    assert "3001,Brick 2 x 4,4,Red,2,0,0,2" in csv_body.text
    text = client.get("/api/collection/shortage/export?model=truck-v1.mpd&format=text").text
    assert "2 x 3001 Brick 2 x 4 (Red)" in text
    assert reservations._cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert client.get("/api/collection/shortage/export?model=truck-v1.mpd&format=xml").status_code == 400


def test_model_names_are_checked_and_deleting_a_model_releases_it(client):
    put("mine", [("3001", 4, 5)])
    for bad in ("../x.mpd", ".hidden.mpd", "nope.mpd", "truck-v1.txt"):
        assert client.post("/api/collection/reservations", json={"model": bad}).status_code == 404
    client.post("/api/collection/reservations", json={"model": "truck-v1.mpd"})
    assert client.delete("/api/models/truck-v1.mpd").status_code == 200
    assert client.get("/api/collection/reservations").json()["reservations"] == []
    assert collection_store.read_reservations() == []
