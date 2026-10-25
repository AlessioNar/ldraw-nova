"""The set detail endpoint: one owned set with per-row needed, on-hand and missing counts."""
import json

import pytest
from fastapi.testclient import TestClient

import inventories
import part_info
import settings
from main import app

META = {"3001": ("Brick 2 x 4", "Brick"), "3020": ("Plate 2 x 4", "Plate")}
COLOURS = {1: "Blue", 4: "Red"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INVENTORIES_DIR", tmp_path / "inventories")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(part_info, "meta", lambda: META)
    monkeypatch.setattr(part_info, "colour_names", lambda: COLOURS)
    inventories.clear_caches()
    with TestClient(app) as c:
        yield c
    inventories.clear_caches()


def put(source_id, rows, set_block=None, **extra):
    data = {"id": source_id, "name": source_id, "created_at": 1.0, "source_filename": None, "unmapped_parts": [], "unmapped_colours": [],
            "warnings": [], "rows": [dict(part=p, colour=c, qty=q, rb_part=p, rb_colour=0) for p, c, q in rows], **extra}
    if set_block:
        data.update(kind="set", set={"set_num": "1-1", "number": "1", "name": "Test", "year": 2000, "theme": "T", "num_parts": 5, "copies": 1,
                                     "state": "parts", "dropped_pieces": 2, **set_block})
    settings.INVENTORIES_DIR.mkdir(parents=True, exist_ok=True)
    (settings.INVENTORIES_DIR / f"{source_id}.json").write_text(json.dumps(data), encoding="utf-8")
    inventories.clear_caches()


def test_built_set_lists_rows_with_nothing_missing(client):
    put("set-1-1", [("3001", 4, 3), ("3020", 1, 2)], {"state": "built"})
    body = client.get("/api/sets/set-1-1").json()
    assert (body["set"]["pieces"], body["set"]["reserved"], body["set"]["missing"], body["set"]["on_hand"]) == (5, 5, 0, 5)
    assert body["set"]["image"] == "/files/set-image/1-1" and body["set"]["brand"] == "LEGO"
    first = body["rows"][0]
    assert (first["part"], first["colour_name"], first["description"], first["category"]) == ("3020", "Blue", "Plate 2 x 4", "Plate")
    assert all(r["on_hand"] == r["needed"] and r["missing"] == 0 for r in body["rows"])


def test_parts_only_set_has_everything_on_hand(client):
    put("set-1-1", [("3001", 4, 3)])
    put("set-2-1", [("3001", 4, 3)], {"set_num": "2-1", "state": "parts"})
    body = client.get("/api/sets/set-2-1").json()
    assert (body["set"]["reserved"], body["set"]["missing"], body["set"]["on_hand"]) == (0, 0, 3)


def test_owned_and_reserved_include_other_sources(client):
    put("loose", [("3001", 4, 1)], kind="loose")
    put("set-1-1", [("3001", 4, 3)], {"state": "built"})
    row = client.get("/api/sets/set-1-1").json()["rows"][0]
    assert (row["needed"], row["owned"], row["reserved"], row["missing"], row["on_hand"]) == (3, 4, 3, 0, 3)


def test_reservations_beyond_what_is_owned_show_as_missing(client, monkeypatch):
    import reservations
    put("set-1-1", [("3001", 4, 3)], {"state": "built"})
    monkeypatch.setattr(reservations, "reserved_totals", lambda exclude_build=None: {("3001", 4): 5})
    row = client.get("/api/sets/set-1-1").json()["rows"][0]
    assert (row["needed"], row["owned"], row["reserved"], row["missing"], row["on_hand"]) == (3, 3, 5, 2, 1)


def test_not_a_set_is_404(client):
    put("plain", [("3001", 4, 1)])
    assert client.get("/api/sets/plain").status_code == 404
    assert client.get("/api/sets/nope").status_code == 404
