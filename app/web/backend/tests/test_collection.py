"""The parts collection: sources, history and undo, replace-import, previews, sets, notes, review queue, browsing, thumbnails.

Plain-JSON behaviour runs anywhere with a small fake part table. The import tests run the real toolkit CLI as a
subprocess (like test_inventories.py) and skip when the toolkit interpreter cannot import ldraw_tools.
"""
import asyncio
import json
import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import collection
import collection_store
import inventories
import part_images
import set_images
import part_info
import parts_steering
import quick_add
import sandbox
import set_catalog
import settings
import toolkit
import workspace
from main import app
from routers import editor_palette

REPO = Path(__file__).resolve().parents[4]

META = {
    "3001": ("Brick 2 x 4", "Brick"), "3003": ("Brick 2 x 2", "Brick"), "3004": ("Brick 1 x 2", "Brick"), "3005": ("Brick 1 x 1", "Brick"),
    "3010": ("Brick 1 x 4", "Brick"), "3020": ("Plate 2 x 4", "Plate"), "3022": ("Plate 2 x 2", "Plate"), "3023": ("Plate 1 x 2", "Plate"),
    "3024": ("Plate 1 x 1", "Plate"), "3710": ("Plate 1 x 4", "Plate"), "3069b": ("Tile 1 x 2 with Groove", "Tile"),
    "3068b": ("Tile 2 x 2 with Groove", "Tile"), "6141": ("Plate 1 x 1 Round", "Plate"), "3062b": ("Brick 1 x 1 Round with Hollow Stud", "Brick"),
    "98138": ("Tile 1 x 1 Round with Groove", "Tile"), "4073": ("~Moved to 6141", "Moved"), "4624": ("Wheel 8 x 18 with Axle Hole", "Wheel"),
    "3641": ("Tyre 14mm D. x 4mm Smooth Small Single", "Tyre"), "3001pr0001": ("Brick 2 x 4 with Print", "Brick"),
}
COLOURS = {0: "Black", 1: "Blue", 2: "Green", 4: "Red", 7: "Light Grey", 8: "Dark Grey", 14: "Yellow", 15: "White", 19: "Tan", 25: "Orange",
           28: "Dark Tan", 47: "Trans Clear", 70: "Reddish Brown", 71: "Light Bluish Grey", 72: "Dark Bluish Grey", 320: "Dark Red"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "INVENTORIES_DIR", tmp_path / "inventories")
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(part_info, "meta", lambda: META)
    monkeypatch.setattr(part_info, "colour_names", lambda: COLOURS)
    monkeypatch.setattr(quick_add, "_popular", {})
    inventories.clear_caches()
    yield settings.INVENTORIES_DIR
    inventories.clear_caches()


def put(source_id, rows, **extra):
    """An old-style source file (no kind, no schema) with exact rows [(part, colour, qty)]."""
    data = {"id": source_id, "name": extra.pop("name", source_id), "created_at": extra.pop("created_at", 1.0), "source_filename": None,
            "rows": [dict(part=p, colour=c, qty=q, rb_part=p, rb_colour=0) for p, c, q in rows],
            "unmapped_parts": [], "unmapped_colours": [], "warnings": [], **extra}
    settings.INVENTORIES_DIR.mkdir(parents=True, exist_ok=True)
    (settings.INVENTORIES_DIR / f"{source_id}.json").write_text(json.dumps(data), encoding="utf-8")
    inventories.clear_caches()
    return data


def stored(source_id):
    return json.loads((settings.INVENTORIES_DIR / f"{source_id}.json").read_text(encoding="utf-8"))


def table(source_id):
    return {(r["part"], r["colour"]): r["qty"] for r in stored(source_id)["rows"]}


def add(part, colour, qty):
    return {"op": "add", "part": part, "colour": colour, "qty": qty}


# --- sources: old files, kinds, the Loose parts source ----------------------------

def test_old_file_without_new_keys_reads_as_an_import(client):
    put("old", [("3001", 4, 5)])
    item = client.get("/api/inventories").json()["inventories"][0]
    assert (item["kind"], item["schema"], item["total_pieces"]) == ("import", 1, 5)
    assert "schema" not in stored("old")                       # untouched until its first edit


def test_first_edit_upgrades_the_file_and_keeps_old_keys():
    put("old", [("3001", 4, 5)], source_filename="old.csv", unmapped_parts=[{"rb_part": "x", "colour": 4, "qty": 2}])
    collection.apply_ops("old", [add("3001", 4, 2)])
    data = stored("old")
    assert data["schema"] == 2 and data["source_filename"] == "old.csv" and data["unmapped_parts"][0]["qty"] == 2
    assert table("old") == {("3001", 4): 7} and data["totals"]["pieces"] == 7


def test_loose_parts_is_listed_before_it_exists_and_created_on_first_add(client):
    listed = client.get("/api/collection").json()["sources"]
    assert [(s["id"], s["kind"], s.get("virtual")) for s in listed] == [("loose", "loose", True)]
    assert not (settings.INVENTORIES_DIR / "loose.json").exists()
    r = client.post("/api/collection/sources/loose/ops", json={"ops": [add("3001", 4, 20)], "note": "Quick add"})
    assert r.status_code == 200 and r.json()["source"]["kind"] == "loose"
    assert stored("loose")["name"] == "Loose parts" and table("loose") == {("3001", 4): 20}
    assert not client.get("/api/collection").json()["sources"][0].get("virtual")


def test_loose_is_a_reserved_name_for_uploads():
    with pytest.raises(inventories.InventoryError) as exc:
        asyncio.run(collection.import_csv("Loose", "Part,Color,Quantity\n3001,4,1\n"))
    assert exc.value.status == 400


# --- ops: validation, atomic batches, history, undo -------------------------------

@pytest.mark.parametrize("op", [
    {"op": "add", "part": "../x", "colour": 4, "qty": 1}, {"op": "add", "part": "3001", "colour": "red", "qty": 1},
    {"op": "add", "part": "3001", "colour": 16, "qty": 1}, {"op": "add", "part": "3001", "colour": 999, "qty": 1},
    {"op": "add", "part": "3001", "colour": 4, "qty": 0}, {"op": "add", "part": "3001", "colour": 4, "qty": 1.5},
    {"op": "add", "part": "3001", "colour": 4, "qty": True}, {"op": "add", "part": "99999", "colour": 4, "qty": 1},
    {"op": "set", "part": "3001", "colour": 4, "qty": -1}, {"op": "teleport"}, "add"])
def test_bad_changes_are_refused_and_change_nothing(op):
    put("box", [("3001", 4, 1)])
    with pytest.raises(inventories.InventoryError) as exc:
        collection.apply_ops("box", [add("3003", 1, 5), op])
    assert exc.value.status == 400 and table("box") == {("3001", 4): 1}
    assert not (settings.INVENTORIES_DIR / "box.log.jsonl").exists()


def test_add_set_and_remove_and_noop():
    put("box", [("3001", 4, 1)])
    r = collection.apply_ops("box", [add("3001", 4, 4), {"op": "set", "part": "3003", "colour": 1, "qty": 9}, {"op": "set", "part": "3001", "colour": 4, "qty": 5}])
    assert r["changes"] == 2 and table("box") == {("3001", 4): 5, ("3003", 1): 9}
    assert collection.apply_ops("box", [{"op": "set", "part": "3001", "colour": 4, "qty": 5}])["batch"] is None      # nothing changed
    collection.apply_ops("box", [{"op": "remove", "part": "3003", "colour": 1}])
    assert table("box") == {("3001", 4): 5}


def test_history_lists_batches_and_undo_walks_back_one_batch_at_a_time(client):
    put("box", [("3001", 4, 1)])
    client.post("/api/collection/sources/box/ops", json={"ops": [add("3001", 4, 4), add("3003", 1, 2)], "note": "first"})
    client.post("/api/collection/sources/box/ops", json={"ops": [{"op": "remove", "part": "3001", "colour": 4}]})
    h = client.get("/api/collection/sources/box/history").json()
    assert [b["changes"] for b in h["batches"]] == [1, 2] and h["batches"][1]["note"] == "first" and h["undo_batch"] == 2
    assert table("box") == {("3003", 1): 2}
    u = client.post("/api/collection/sources/box/undo").json()
    assert u["undone"] == 2 and table("box") == {("3001", 4): 5, ("3003", 1): 2}
    client.post("/api/collection/sources/box/undo")
    assert table("box") == {("3001", 4): 1}
    assert client.post("/api/collection/sources/box/undo").status_code == 409
    log = [json.loads(line) for line in (settings.INVENTORIES_DIR / "box.log.jsonl").read_text().splitlines()]
    assert [e["op"] for e in log] == ["add", "add", "remove", "undo", "undo"]       # append-only: undo is a new entry
    states = client.get("/api/collection/sources/box/history").json()["batches"]
    assert [(b["batch"], b["undone"]) for b in states if b["kind"] == "edit"] == [(2, True), (1, True)]


def test_undo_restores_the_exact_row_including_its_note_fields():
    put("box", [("3001", 4, 3)])
    data = stored("box")
    data["rows"][0].update(note="blue tub", rb_part="3001b", rb_colour=4)
    (settings.INVENTORIES_DIR / "box.json").write_text(json.dumps(data))
    inventories.clear_caches()
    collection.apply_ops("box", [{"op": "remove", "part": "3001", "colour": 4}])
    collection.undo("box")
    assert stored("box")["rows"][0] == {"part": "3001", "colour": 4, "qty": 3, "rb_part": "3001b", "rb_colour": 4, "note": "blue tub"}


def test_history_is_capped_per_source():
    put("box", [])
    for _ in range(collection.MAX_BATCHES + 5):
        collection.apply_ops("box", [add("3001", 4, 1)])
    assert len(collection.history("box", 100)["batches"]) == 100
    lines = (settings.INVENTORIES_DIR / "box.log.jsonl").read_text().splitlines()
    assert len({json.loads(line)["batch"] for line in lines}) == collection.MAX_BATCHES


def test_delete_removes_the_log_too(client):
    put("box", [])
    collection.apply_ops("box", [add("3001", 4, 1)])
    assert client.delete("/api/inventories/box").status_code == 200
    assert not (settings.INVENTORIES_DIR / "box.log.jsonl").exists()


# --- review queue: per piece and per family ---------------------------------------

def pile_source():
    return put("pile", [("3001", 4, 2)], review=[
        {"id": "r1", "part": "3001", "family": "Reds", "candidates": [4, 320], "qty": 6, "brickit_part": "3001", "note": "scan 1"},
        {"id": "r2", "part": "3003", "family": "Reds", "candidates": [4, 320], "qty": 3, "brickit_part": "3003", "note": "scan 1"},
        {"id": "r3", "part": "3022", "family": "Grays", "candidates": [71, 72], "qty": 4, "brickit_part": "3022", "note": "scan 2"}])


def test_review_pieces_are_never_counted_until_resolved(client):
    pile_source()
    item = client.get("/api/inventories").json()["inventories"][0]
    assert (item["total_pieces"], item["review_items"], item["review_pieces"]) == (2, 3, 13)
    browse = client.get("/api/collection/parts").json()
    assert browse["totals"]["pieces"] == 2 and browse["collection"]["pieces"] == 2 and browse["totals"]["review_pieces"] == 13
    owned, _ = workspace.owned_parts()
    assert owned == {("3001", 4): 2}


def test_resolve_one_piece_part_of_its_quantity_then_the_rest(client):
    pile_source()
    r = client.post("/api/collection/review", json={"source": "pile", "id": "r1", "colour": 320, "qty": 2})
    assert r.status_code == 200
    assert table("pile") == {("3001", 4): 2, ("3001", 320): 2}
    assert [(i["id"], i["qty"]) for i in stored("pile")["review"]][0] == ("r1", 4)
    client.post("/api/collection/review", json={"source": "pile", "id": "r1", "colour": 4})
    assert table("pile") == {("3001", 4): 6, ("3001", 320): 2} and "r1" not in {i["id"] for i in stored("pile")["review"]}
    too_many = client.post("/api/collection/review", json={"source": "pile", "id": "r2", "colour": 4, "qty": 99})
    assert too_many.status_code == 400
    assert client.post("/api/collection/review", json={"source": "pile", "id": "nope", "colour": 4}).status_code == 404
    assert client.post("/api/collection/review", json={"source": "pile", "id": "r2", "colour": 16}).status_code == 400


def test_discard_a_piece_not_mine_and_undo_it(client):
    pile_source()
    client.post("/api/collection/review", json={"source": "pile", "id": "r3", "discard": True})
    assert [i["id"] for i in stored("pile")["review"]] == ["r1", "r2"] and table("pile") == {("3001", 4): 2}
    assert client.post("/api/collection/sources/pile/undo").status_code == 200
    assert [i["id"] for i in stored("pile")["review"]] == ["r1", "r2", "r3"]


def test_resolve_a_whole_family_is_one_undoable_batch(client):
    pile_source()
    r = client.post("/api/collection/review", json={"source": "pile", "family": "Reds", "colour": 4})
    assert r.status_code == 200 and r.json()["changes"] == 2
    assert table("pile") == {("3001", 4): 8, ("3003", 4): 3} and [i["id"] for i in stored("pile")["review"]] == ["r3"]
    client.post("/api/collection/sources/pile/undo")
    assert table("pile") == {("3001", 4): 2} and len(stored("pile")["review"]) == 3
    assert client.post("/api/collection/review", json={"source": "pile", "family": "Pinks", "colour": 4}).status_code == 404


def test_review_queue_groups_by_family_and_offers_owned_colours_first(client):
    pile_source()
    q = client.get("/api/collection/review").json()
    assert [(g["family"], g["pieces"]) for g in q["groups"]] == [("Grays", 4), ("Reds", 9)] and q["items"] == 3 and q["pieces"] == 13
    reds = q["groups"][1]["items"][0]
    assert reds["description"] == "Brick 2 x 4" and [(c["colour"], c["owned"]) for c in reds["candidates"]] == [(4, True), (320, False)]


# --- notes -----------------------------------------------------------------------

def test_note_per_part_is_stored_logged_and_searchable(client):
    put("box", [("3001", 4, 5), ("3003", 4, 1)])
    assert client.put("/api/collection/parts/3001/note", json={"note": "  blue tub "}).json() == {"part": "3001", "note": "blue tub"}
    assert json.loads((settings.INVENTORIES_DIR / collection_store.NOTES_FILE).read_text())["notes"] == {"3001": "blue tub"}
    assert [p["part"] for p in client.get("/api/collection/parts", params={"q": "tub"}).json()["parts"]] == ["3001"]
    assert client.get("/api/collection/parts/3001").json()["note"] == "blue tub"
    assert client.put("/api/collection/parts/3001/note", json={"note": ""}).json()["note"] == ""
    assert collection.get_notes() == {}
    log = [json.loads(line) for line in (settings.INVENTORIES_DIR / collection_store.NOTES_LOG).read_text().splitlines()]
    assert [(e["before"], e["after"]) for e in log] == [("", "blue tub"), ("blue tub", "")]
    assert client.put("/api/collection/parts/3001/note", json={"note": "x" * 201}).status_code == 400
    assert client.put("/api/collection/parts/3001/note", json={"note": "a\nb"}).status_code == 400
    assert client.put("/api/collection/parts/a%20b/note", json={"note": "x"}).status_code == 400


def test_the_notes_file_is_not_a_source():
    put("box", [("3001", 4, 5)])
    collection.set_note("3001", "tub")
    assert [s["id"] for s in inventories.list_inventories()] == ["box"]
    owned, _ = workspace.owned_parts()
    assert owned == {("3001", 4): 5}


# --- browsing --------------------------------------------------------------------

def stocked():
    put("a", [("3001", 4, 5), ("3001", 1, 2), ("3003", 4, 3), ("3020", 15, 10)], name="Box A", created_at=2.0)
    put("b", [("3001", 4, 1), ("3069b", 72, 8), ("6141", 4, 4)], name="Box B", created_at=1.0)


def names(r):
    return [p["part"] for p in r["parts"]]


def test_browse_adds_sources_together_and_counts(client):
    stocked()
    r = client.get("/api/collection/parts").json()
    assert names(r) == ["3020", "3001", "3069b", "6141", "3003"]                 # by quantity, ties by part number
    brick = next(p for p in r["parts"] if p["part"] == "3001")
    assert brick["total"] == 8 and brick["description"] == "Brick 2 x 4" and brick["category"] == "Brick" and brick["size"] == "2x4"
    assert [(c["name"], c["qty"]) for c in brick["colours"]] == [("Red", 6), ("Blue", 2)]
    assert sorted((s["id"], s["qty"]) for s in brick["sources"]) == [("a", 7), ("b", 1)]
    assert r["collection"] == {"pieces": 33, "parts": 5, "colours": 4, "sources": 2}


def test_filters_by_colour_category_size_source_and_search(client):
    stocked()
    get = lambda **p: client.get("/api/collection/parts", params=p).json()  # noqa: E731
    assert names(get(colour="1")) == ["3001"] and get(colour="1")["parts"][0]["total"] == 2
    assert sorted(names(get(category="tile"))) == ["3069b"] and sorted(names(get(category="Plate"))) == ["3020", "6141"]
    assert names(get(size="2x4")) == ["3020", "3001"]
    assert names(get(source="b", sort="part")) == ["3001", "3069b", "6141"] and get(source="b", sort="part")["parts"][1]["total"] == 8
    assert sorted(names(get(q="red"))) == ["3001", "3003", "6141"]                  # colour word narrows the rows
    assert all(c["name"] == "Red" for p in get(q="red")["parts"] for c in p["colours"]) and get(q="red")["totals"]["pieces"] == 13
    assert sorted(names(get(q="4x2"))) == ["3001", "3020"] and names(get(q="3069")) == ["3069b"]
    assert names(get(q="dark bluish grey")) == ["3069b"] and names(get(q="tile groove")) == ["3069b"]
    assert get(q="nothing here")["total"] == 0
    assert client.get("/api/collection/parts", params={"colour": "red"}).status_code == 400


def test_facets_and_paging(client):
    stocked()
    r = client.get("/api/collection/parts", params={"limit": 2, "offset": 1}).json()
    assert len(r["parts"]) == 2 and r["total"] == 5
    f = r["facets"]
    assert {c["colour"]: c["qty"] for c in f["colours"]} == {4: 13, 1: 2, 15: 10, 72: 8}
    assert {c["category"]: c["parts"] for c in f["categories"]} == {"Brick": 2, "Plate": 2, "Tile": 1}
    assert {s["id"] for s in f["sources"]} == {"a", "b"}


def test_part_detail_lists_every_source_and_the_maybe_pieces(client):
    stocked()
    put("pile", [], review=[{"id": "r1", "part": "3001", "family": "Reds", "candidates": [4], "qty": 3, "brickit_part": "3001"}])
    d = client.get("/api/collection/parts/3001").json()
    assert d["total"] == 8 and {(r["source"], r["colour"], r["qty"]) for r in d["rows"]} == {("a", 4, 5), ("a", 1, 2), ("b", 4, 1)}
    assert d["maybe"] == [{"source": "pile", "source_name": "pile", "id": "r1", "family": "Reds", "qty": 3}]


# --- the aggregates other features read stay exact ---------------------------------

def test_palette_parts_check_and_steering_aggregate_every_source_exactly(client):
    put("a", [("3001", 4, 5)])
    collection.apply_ops("loose", [add("3001", 4, 3), add("3003", 1, 2)])
    pile_source()
    owned, signature = workspace.owned_parts()
    assert owned == {("3001", 4): 10, ("3003", 1): 2}                              # loose + a + pile's exact row, no review pieces
    assert workspace.check({("3001", 4): 12}, owned, signature) == {"owned": 10, "needed": 12, "missing": [{"part": "3001", "colour": 4, "need": 12, "have": 10}]}
    loaded = [parts_steering.load_inventory(i) for i in ("a", "loose", "pile")]
    assert editor_palette.owned_map(loaded) == {("3001", 4): 10, ("3003", 1): 2}
    collection.apply_ops("pile", [{"op": "review_resolve", "id": "r1", "colour": 320}])
    assert workspace.owned_parts()[0][("3001", 320)] == 6                          # resolved pieces count


def test_loose_parts_can_steer_a_chat():
    collection.apply_ops("loose", [add("3001", 4, 3)])
    options = {"parts_mode": "prefer", "inventory_ids": ["loose"]}
    inventories.validate_parts_options(options)
    assert parts_steering.load_inventory("loose").rows == [("3001", 4, 3)]


# --- sets ------------------------------------------------------------------------

def varints(values):
    out = bytearray()
    for n in values:
        while n >= 0x80:
            out.append(n & 0x7F | 0x80)
            n >>= 7
        out.append(n)
    return bytes(out)


def write_catalog(path, sets):
    """A catalogue file in the documented layout (see ldraw_tools/sets.py): sets = [(num, name, year, theme, dropped, [(part, colour, qty)])]."""
    parts = sorted({p for s in sets for p, _, _ in s[5]})
    themes = sorted({s[3] for s in sets})
    blobs, rows, offset = [], [], 0
    for num, name, year, theme, dropped, lines in sorted(sets):
        ordered = sorted((parts.index(p), c, q) for p, c, q in lines)
        prev, deltas = 0, []
        for i, _, _ in ordered:
            deltas.append(i - prev)
            prev = i
        blob = zlib.compress(varints([len(ordered)] + deltas + [c for _, c, _ in ordered] + [q for _, _, q in ordered]))
        mapped = sum(q for _, _, q in lines)
        rows.append([num, name, year, themes.index(theme), mapped + dropped, 1, mapped, dropped, 0, offset, len(blob)])
        blobs.append(blob)
        offset += len(blob)
    header = zlib.compress(json.dumps({"meta": {"source": "test"}, "parts": parts, "themes": themes, "sets": rows}).encode())
    path.write_bytes(b"LDSETCAT1\n" + struct.pack("<I", len(header)) + header + b"".join(blobs))


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    path = tmp_path / "set-catalog.bin"
    write_catalog(path, [("8448-1", "Super Street Sweeper", 1999, "Technic", 3, [("3001", 4, 2), ("3003", 1, 5), ("3020", 15, 1)]),
                         ("100-1", "Red Racer", 2005, "Racers", 0, [("3001", 4, 4)]),
                         ("200-1", "Big City Tipper Truck", 2010, "City", 0, [("3003", 14, 1)])])
    monkeypatch.setenv("LDRAW_NOVA_SET_CATALOG", str(path))
    set_catalog._load.cache_clear()
    yield path
    set_catalog._load.cache_clear()


def test_set_search_by_number_name_and_theme(client, catalog):
    nums = lambda q: [s["set_num"] for s in client.get("/api/catalog/sets", params={"query": q}).json()["sets"]]  # noqa: E731
    assert nums("8448") == ["8448-1"] and nums("tipper truck") == ["200-1"] and nums("racers") == ["100-1"]
    assert nums("") == ["200-1", "100-1", "8448-1"] and nums("nothing") == []
    one = client.get("/api/catalog/sets/8448").json()
    assert (one["set_num"], one["name"], one["num_parts"], one["mapped_pieces"], one["dropped_pieces"]) == ("8448-1", "Super Street Sweeper", 11, 8, 3)
    assert client.get("/api/catalog/sets/9999").status_code == 404


def test_add_a_set_creates_a_set_source_with_its_parts(client, catalog):
    r = client.post("/api/collection/sets", json={"set_num": "8448", "copies": 2})
    assert r.status_code == 200, r.text
    inv = r.json()["inventory"]
    assert (inv["kind"], inv["id"], inv["name"], inv["total_pieces"]) == ("set", "set-8448-1", "8448-1 Super Street Sweeper", 16)
    data = stored("set-8448-1")
    assert data["set"]["copies"] == 2 and data["set"]["state"] == "parts" and data["set"]["dropped_pieces"] == 6
    assert table("set-8448-1") == {("3001", 4): 4, ("3003", 1): 10, ("3020", 15): 2} and "not listed" in data["warnings"][0]
    again = client.post("/api/collection/sets", json={"set_num": "8448-1", "state": "built"}).json()["inventory"]
    assert again["id"] == "set-8448-1-2" and again["set"]["state"] == "built"
    assert sorted(client.get("/api/catalog/sets/8448").json()["owned_sources"]) == ["set-8448-1", "set-8448-1-2"]
    assert workspace.owned_parts()[0][("3003", 1)] == 15                         # set rows count like any other
    for bad in ({"set_num": "9999"}, {"set_num": "8448", "copies": 0}, {"set_num": "8448", "copies": 100}, {"set_num": "8448", "state": "lost"}):
        assert client.post("/api/collection/sets", json=bad).status_code in (400, 404)


def test_set_source_rows_can_be_edited_and_undone(client, catalog):
    client.post("/api/collection/sets", json={"set_num": "100"})
    client.post("/api/collection/sources/set-100-1/ops", json={"ops": [{"op": "set", "part": "3001", "colour": 4, "qty": 3}]})
    assert table("set-100-1") == {("3001", 4): 3}
    client.post("/api/collection/sources/set-100-1/undo")
    assert table("set-100-1") == {("3001", 4): 4}
    assert client.post("/api/collection/sources/set-100-1/undo").status_code == 409        # the creation is not undoable


def test_missing_catalogue_is_a_clear_503(client, monkeypatch, tmp_path):
    monkeypatch.setenv("LDRAW_NOVA_SET_CATALOG", str(tmp_path / "missing.bin"))
    set_catalog._load.cache_clear()
    assert client.get("/api/catalog/sets", params={"query": "x"}).status_code == 503
    assert client.post("/api/collection/sets", json={"set_num": "1"}).status_code == 503


# --- quick add: the grammar table -------------------------------------------------

def one(text):
    items = quick_add.parse(text)["items"]
    assert len(items) == 1, items
    return items[0]


@pytest.mark.parametrize("text, part, colour, qty", [
    ("20 red 2x4 bricks", "3001", 4, 20),
    ("3 x 3001 blue", "3001", 1, 3),
    ("10 plate 1x2 dark bluish grey", "3023", 72, 10),
    ("2 x 4 red brick", "3001", 4, 1),
    ("red 2x4 brick x20", "3001", 4, 20),
    ("20x yellow 1x1 plate", "3024", 14, 20),
    ("5 4x2 plates white", "3020", 15, 5),
    ("3 trans clear 1x1 round plate", "6141", 47, 3),
    ("3001 blue", "3001", 1, 1),
    ("12 3003 dark red", "3003", 320, 12),
    ("7 wheels black", "4624", 0, 7),
    ("4 tyre black", "3641", 0, 4),
    ("20 Red 2 x 4 Bricks", "3001", 4, 20),
    ("50 REDDISH BROWN 1X2 PLATE", "3023", 70, 50),
    ("6 dark tan 2x2 brick", "3003", 28, 6),
    # typos, corrected only toward known words and always reported
    ("20 reed 2x4 brik", "3001", 4, 20),
    ("8 yelow 1x2 platte", "3023", 14, 8),
    ("5 whit 2x2 plate", "3022", 15, 5),
    # Italian
    ("5 rosso 2x4 mattoncini", "3001", 4, 5),
    ("2 piastre 1x4 grigio scuro", "3710", 8, 2),
    ("8 bianco 1x2 piastre", "3023", 15, 8),
    ("3 blu 2x2 mattoncino", "3003", 1, 3),
    # moved parts follow their redirect
    ("4073 red", "6141", 4, 1),
])
def test_quick_add_understands(text, part, colour, qty):
    item = one(text)
    assert (item["status"], item["part"], item["colour"], item["qty"]) == ("ok", part, colour, qty), item


def test_quick_add_reports_every_correction_and_assumption():
    assert one("20 reed 2x4 brik")["notes"] == ['read "reed" as "red"', 'read "brik" as "brick"']
    assert one("red 2x4 brick")["qty_assumed"] is True and one("5 red 2x4 brick")["qty_assumed"] is False
    assert any("6141" in n for n in one("4073 red")["notes"])


def test_quick_add_never_guesses_between_readings():
    item = one("4 tan 1x1 round")                       # a brick, a plate and a tile are all 1x1 round
    assert item["status"] == "needs_part" and item["part"] is None and item["colour"] == 19 and item["qty"] == 4
    assert {c["part"] for c in item["candidates"]} == {"6141", "3062b", "98138"} and "pick" in item["message"]
    two = one("2x4 red")                                # size alone: brick or plate
    assert two["status"] == "needs_part" and {c["part"] for c in two["candidates"]} == {"3001", "3020"}
    grey = one("5 grey 2x2 brick")                      # plain grey is four colours
    assert (grey["status"], grey["part"], grey["colour"]) == ("needs_colour", "3003", None)
    assert [c["colour"] for c in grey["colour_candidates"]] == [71, 72, 7, 8]
    nocolour = one("5 2x2 brick")
    assert (nocolour["status"], nocolour["part"], nocolour["message"]) == ("needs_colour", "3003", "Which colour?")


@pytest.mark.parametrize("text, fragment", [
    ("banana split", "do not know the word"), ("20 red 9x9 bricks", "No part in the library matches"), ("red", "Say which part"),
    ("0 red 2x4 brick", "out of range"), ("200000 red 2x4 brick", "out of range"), ("5 red 99999", "No part"),
])
def test_quick_add_says_clearly_what_it_could_not_understand(text, fragment):
    item = one(text)
    assert item["status"] == "needs_part" and item["part"] is None and fragment in item["message"], item


def test_quick_add_splits_lines_and_keeps_going_after_a_bad_one():
    r = quick_add.parse("12 black 2x4 bricks, 8 white 1x2 plates and 3 trans clear 1x1 round plate\nfoo bar; 4 tan 1x1 round")
    assert [i["status"] for i in r["items"]] == ["ok", "ok", "ok", "needs_part", "needs_part"] and (r["ok"], r["total"]) == (3, 5)
    assert quick_add.parse("") == {"items": [], "ok": 0, "total": 0}
    assert len(quick_add.parse("\n".join(["1 red 2x4 brick"] * 100))["items"]) == quick_add.MAX_LINES


def test_quick_add_ignores_printed_and_moved_parts_when_searching():
    assert one("1 red 2x4 brick")["part"] == "3001"                 # not 3001pr0001
    assert one("1 red 3001pr0001")["part"] == "3001pr0001"           # but a part number is taken as typed


def test_quick_add_endpoints_parse_then_add_on_confirm(client):
    parsed = client.post("/api/collection/quick-add/parse", json={"text": "20 red 2x4 bricks, 8 black 1x2 plates"}).json()
    assert parsed["ok"] == 2 and not (settings.INVENTORIES_DIR / "loose.json").exists()       # understanding adds nothing
    items = [{"part": i["part"], "colour": i["colour"], "qty": i["qty"]} for i in parsed["items"]]
    r = client.post("/api/collection/quick-add", json={"items": items})
    assert r.status_code == 200 and r.json()["pieces"] == 28
    assert table("loose") == {("3001", 4): 20, ("3023", 0): 8}
    assert client.post("/api/collection/sources/loose/undo").status_code == 200 and table("loose") == {}
    assert client.post("/api/collection/quick-add", json={"items": []}).status_code == 400
    assert client.post("/api/collection/quick-add", json={"items": [{"part": "3001", "colour": 4}]}).status_code == 400


# --- thumbnails ------------------------------------------------------------------

def test_thumbnail_is_rendered_once_then_served_from_cache(client, monkeypatch):
    calls = []

    async def fake_render(part):
        calls.append(part)
        path = part_images.thumb_path(part)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG fake")
        return path
    monkeypatch.setattr(part_images, "_render", fake_render)
    for _ in range(2):
        r = client.get("/api/parts/3001/thumb")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "immutable" in r.headers["cache-control"]
    assert calls == ["3001"]


def test_thumbnail_failure_is_a_placeholder_and_is_not_retried_at_once(client, monkeypatch):
    calls = []

    async def broken(part):
        calls.append(part)
        raise RuntimeError("no display")
    monkeypatch.setattr(part_images, "_render", broken)
    part_images._failed.clear()
    for _ in range(2):
        r = client.get("/api/parts/3003/thumb")
        assert r.status_code == 202 and r.headers["content-type"] == "image/svg+xml" and r.headers["cache-control"] == "no-store"
    assert calls == ["3003"]
    assert client.get("/api/parts/not-a-part/thumb").status_code == 404
    assert client.get("/api/parts/..%2F..%2Fetc/thumb").status_code in (404, 400)


# --- imports with the real toolkit CLI: preview first, then commit ------------------

RB = "Part,Color,Quantity\n3001,4,5\n3003,1,2\n3022,14,4\n"
RB2 = "Part,Color,Quantity\n3001,4,7\n3003,1,2\n3020,15,6\n"                     # 3001 changed, 3022 gone, 3020 new
DB = "inventory_id,part_num,color_id,quantity,is_spare\n1,3001,4,3,False\n1,3003,1,2,False\n1,3001,4,1,True\n"
PILE = 'Scan,Part,PartName,Color,Quantity\n1,3001,"Brick 2 x 4",Grays,2\n2,3001,"Brick 2 x 4",Grays,1\n1,3003,"Brick 2 x 2",Reds,4\n1,Unknown,"",Reds,2\n'


@pytest.fixture
def cli(tmp_path, monkeypatch):
    python = os.environ.get("LDRAW_NOVA_TOOLKIT_PYTHON") or str(settings.TOOLKIT_PYTHON)
    roots = [p for p in (Path("/opt/ldraw-nova"), REPO) if (p / "ldraw_tools" / "importers.py").is_file()]
    if not roots or not Path(python).exists():
        pytest.skip("no toolkit interpreter (set LDRAW_NOVA_TOOLKIT_PYTHON)")
    probe = subprocess.run([python, "-c", "import ldraw_tools.importers"], cwd=tmp_path, env={"PYTHONPATH": str(roots[0])}, capture_output=True)
    if probe.returncode:
        pytest.skip("the toolkit interpreter cannot import ldraw_tools")
    fake_root = tmp_path / "toolkit"
    (fake_root / ".venv/bin").mkdir(parents=True)
    (fake_root / ".venv/bin/python").touch()
    (fake_root / "instructions.md").touch()
    monkeypatch.setattr(settings, "TOOLKIT_DIR", fake_root)
    monkeypatch.setattr(settings, "TOOLKIT_PYTHON", Path(python))
    real = toolkit.environment
    monkeypatch.setattr(toolkit, "environment", lambda: {**real(), "PYTHONPATH": f"{roots[0]}:{real()['PYTHONPATH']}"})
    monkeypatch.setattr(sandbox, "SCRIPTS_DIR", "/nonexistent")
    # the real colour table and parts: map by the toolkit's own data; parts known to the library are checked there
    monkeypatch.setattr(part_info, "meta", lambda: {})
    monkeypatch.setattr(part_info, "colour_names", lambda: {})


def preview(client, name, csv, **extra):
    r = client.post("/api/collection/imports/preview", json={"name": name, "csv": csv, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_preview_stores_nothing_and_commit_creates_the_source(client, cli):
    p = preview(client, "Technic bin", RB, filename="bin.csv")
    assert p["format"] == "rebrickable-csv" and p["report"]["dialect"] == "rebrickable-parts-list" and p["same_name"] is None
    assert p["totals"]["pieces"] == 11 and p["totals"]["lines"] == 3
    assert not list(settings.INVENTORIES_DIR.glob("*.json"))                       # preview wrote no source
    c = client.post(f"/api/collection/imports/{p['preview_id']}/commit", json={})
    assert c.status_code == 200, c.text
    assert c.json()["inventory"]["id"] == "technic-bin" and c.json()["inventory"]["total_pieces"] == 11
    data = stored("technic-bin")
    assert data["schema"] == 2 and data["origin"]["format"] == "rebrickable-csv" and (settings.INVENTORIES_DIR / "technic-bin.csv").read_text() == RB
    assert client.post(f"/api/collection/imports/{p['preview_id']}/commit", json={}).status_code == 410     # used up


def test_commit_can_rename_and_a_taken_name_is_a_409(client, cli):
    first = preview(client, "one", RB)
    client.post(f"/api/collection/imports/{first['preview_id']}/commit", json={"name": "Renamed"})
    assert (settings.INVENTORIES_DIR / "renamed.json").exists() and stored("renamed")["name"] == "Renamed"
    second = preview(client, "renamed", RB)
    assert second["same_name"] == "renamed"
    assert client.post(f"/api/collection/imports/{second['preview_id']}/commit", json={}).status_code == 409
    assert client.post(f"/api/collection/imports/{second['preview_id']}/commit", json={"name": "loose"}).status_code == 400


def test_each_importer_previews_then_commits(client, cli):
    for name, csv, fmt, dialect in (("rb", RB, "rebrickable-csv", "rebrickable-parts-list"), ("db", DB, "rebrickable-csv", "rebrickable-database"),
                                    ("pile", PILE, "brickit-pileometer", "pileometer")):
        p = preview(client, name, csv)
        assert (p["format"], p["report"]["dialect"]) == (fmt, dialect)
        assert not (settings.INVENTORIES_DIR / f"{name}.json").exists()
        client.post(f"/api/collection/imports/{p['preview_id']}/commit", json={})
        assert stored(name)["origin"]["format"] == fmt
    assert table("db") == {("3001", 4): 3, ("3003", 1): 2}                          # the spare row is not counted
    pile = stored("pile")
    assert pile["rows"] == [] and {(i["part"], i["family"], i["qty"]) for i in pile["review"]} == {("3001", "Grays", 3), ("3003", "Reds", 4)}
    assert pile["origin"]["scans"] == 2
    p2 = preview(client, "pile2", PILE, family_colours={"Grays": 72})
    assert p2["totals"]["review_items"] == 1 and p2["totals"]["pieces"] == 3


def test_bad_files_fail_at_preview_with_a_clear_message(client, cli):
    r = client.post("/api/collection/imports/preview", json={"name": "bad", "csv": "A,B\n1,2\n"})
    assert r.status_code == 200 and r.json()["needs_mapping"]["headers"] == ["A", "B"]       # not a failure: the owner picks the columns
    r = client.post("/api/collection/imports/preview", json={"name": "bad", "csv": "<INVENTORY><ITEM></INVENTORY>"})
    assert r.status_code == 400 and "xml" in r.json()["detail"].lower()
    assert client.post("/api/collection/imports/preview", json={"name": "bad", "csv": ""}).status_code == 400
    assert client.post("/api/collection/imports/nope/commit", json={}).status_code == 410


def test_replace_import_shows_a_diff_and_keeps_the_id(client, cli):
    p = preview(client, "Bin", RB)
    client.post(f"/api/collection/imports/{p['preview_id']}/commit", json={})
    created = stored("bin")["created_at"]
    q = preview(client, "Bin", RB2)
    assert q["same_name"] == "bin"
    d = client.post(f"/api/collection/imports/{q['preview_id']}/diff", json={"replace": "bin"}).json()
    assert (d["added"], d["removed"], d["changed"], d["unchanged"]) == (1, 1, 1, 1) and (d["pieces_before"], d["pieces_after"]) == (11, 15)
    assert [(x["part"], x["before"], x["after"]) for x in d["lines"]["added"]] == [("3020", 0, 6)]
    assert [(x["part"], x["colour_name"] if x["colour_name"] else "", x["before"]) for x in d["lines"]["removed"]][0][0] == "3022"
    assert [(x["part"], x["before"], x["after"]) for x in d["lines"]["changed"]] == [("3001", 5, 7)]
    assert table("bin") == {("3001", 4): 5, ("3003", 1): 2, ("3022", 14): 4}          # diff alone changed nothing
    c = client.post(f"/api/collection/imports/{q['preview_id']}/commit", json={"replace": "bin"})
    assert c.status_code == 200 and c.json()["inventory"]["id"] == "bin" and c.json()["diff"]["changed"] == 1
    assert table("bin") == {("3001", 4): 7, ("3003", 1): 2, ("3020", 15): 6}
    assert stored("bin")["created_at"] == created and len(client.get("/api/inventories").json()["inventories"]) == 1
    assert (settings.INVENTORIES_DIR / "bin.csv").read_text() == RB2
    h = client.get("/api/collection/sources/bin/history").json()
    assert h["batches"][0]["kind"] == "replace" and "+1 -1 ~1" in h["batches"][0]["summary"]
    client.post("/api/collection/sources/bin/undo")                                   # one undo brings the old list back
    assert table("bin") == {("3001", 4): 5, ("3003", 1): 2, ("3022", 14): 4} and (settings.INVENTORIES_DIR / "bin.csv").read_text() == RB


def test_replace_refuses_sources_that_are_not_imports(client, cli):
    collection.apply_ops("loose", [add("3001", 4, 1)])
    q = preview(client, "x", RB)
    assert client.post(f"/api/collection/imports/{q['preview_id']}/diff", json={"replace": "loose"}).status_code == 400
    assert client.post(f"/api/collection/imports/{q['preview_id']}/commit", json={"replace": "loose"}).status_code == 400
    assert client.post(f"/api/collection/imports/{q['preview_id']}/commit", json={"replace": "missing"}).status_code == 404


def test_chats_that_use_a_source_keep_working_after_a_replace_import(client, cli):
    p = preview(client, "Bin", RB)
    client.post(f"/api/collection/imports/{p['preview_id']}/commit", json={})
    options = {"parts_mode": "only", "inventory_ids": ["bin"]}
    inventories.validate_parts_options(options)
    q = preview(client, "Bin", RB2)
    client.post(f"/api/collection/imports/{q['preview_id']}/commit", json={"replace": "bin"})
    again = {"parts_mode": "only", "inventory_ids": ["bin"]}
    inventories.validate_parts_options(again)                                          # the id still exists
    assert sorted(parts_steering.load_inventory("bin").rows) == [("3001", 4, 7), ("3003", 1, 2), ("3020", 15, 6)]
    # the bug this fixes: delete then re-upload used to give the same id back only by luck of the name; now replace never deletes
    assert (settings.INVENTORIES_DIR / "bin.json").exists()


def test_replace_keeps_row_notes_of_surviving_lines(client, cli):
    p = preview(client, "Bin", RB)
    client.post(f"/api/collection/imports/{p['preview_id']}/commit", json={})
    data = stored("bin")
    data["rows"][0]["note"] = "blue tub"
    (settings.INVENTORIES_DIR / "bin.json").write_text(json.dumps(data))
    inventories.clear_caches()
    q = preview(client, "Bin", RB2)
    client.post(f"/api/collection/imports/{q['preview_id']}/commit", json={"replace": "bin"})
    assert next(r for r in stored("bin")["rows"] if r["part"] == "3001")["note"] == "blue tub"


def test_family_remap_through_the_old_endpoint_is_now_undoable(client, cli):
    client.post("/api/inventories", json={"name": "Pile", "csv": PILE})
    assert stored("pile")["review"]
    assert client.put("/api/inventories/pile/family-colours", json={"family_colours": {"Grays": 72, "Reds": 4}}).status_code == 200
    assert table("pile") == {("3001", 72): 3, ("3003", 4): 4} and "review" not in stored("pile")
    client.post("/api/collection/sources/pile/undo")
    assert stored("pile")["rows"] == [] and len(stored("pile")["review"]) == 2


def test_old_upload_endpoint_still_creates_sources(client, cli):
    r = client.post("/api/inventories", json={"name": "Plain", "csv": RB})
    assert r.status_code == 200 and r.json()["inventory"]["total_pieces"] == 11 and r.json()["report"]["format"] == "rebrickable-csv"
    assert client.post("/api/inventories", json={"name": "Plain", "csv": RB}).status_code == 409


def test_owned_sets_list_records_with_brand_and_counts(client, catalog):
    client.post("/api/collection/sets", json={"set_num": "8448", "copies": 2, "state": "built"})
    client.post("/api/collection/sets", json={"set_num": "100", "brand": "  CADA "})
    sets = {s["id"]: s for s in client.get("/api/sets").json()["sets"]}
    big, racer = sets["set-8448-1"], sets["set-100-1"]
    assert (big["brand"], big["image"], big["copies"], big["state"], big["set_num"]) == ("LEGO", "/files/set-image/8448-1", 2, "built", "8448-1")
    assert (big["pieces"], big["reserved"], big["missing"], big["on_hand"]) == (16, 16, 0, 16)
    assert (racer["brand"], racer["state"], racer["pieces"], racer["reserved"]) == ("CADA", "parts", 4, 0)
    assert client.post("/api/collection/sets", json={"set_num": "100", "brand": "x" * 41}).status_code == 400


def test_set_stored_before_brands_reads_as_lego_without_a_rewrite(client, catalog):
    client.post("/api/collection/sets", json={"set_num": "100"})
    data = stored("set-100-1")
    del data["set"]["brand"], data["set"]["image"]
    collection_store.save(data)
    [old] = client.get("/api/sets").json()["sets"]
    assert (old["brand"], old["image"], old["copies"]) == ("LEGO", "/files/set-image/100-1", 1)
    assert client.get("/api/collection").json()["sources"][0]["set"]["brand"] == "LEGO"
    assert "brand" not in stored("set-100-1")["set"]


# --- set pictures ----------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"fake"
JPG = b"\xff\xd8\xff\xe0" + b"fake"


@pytest.fixture
def pictures(tmp_path, monkeypatch):
    monkeypatch.delenv("LDRAW_NOVA_SET_IMAGE_FETCH", raising=False)
    set_images._failed.clear()
    downloads = []

    def fake_download(url):
        downloads.append(url)
        return JPG
    monkeypatch.setattr(set_images, "_download", fake_download)
    return downloads


def test_set_picture_is_a_placeholder_and_nothing_is_downloaded_until_opted_in(client, catalog, pictures):
    client.post("/api/collection/sets", json={"set_num": "8448"})
    r = client.get("/files/set-image/8448-1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/svg+xml" and r.headers["cache-control"] == "no-store"
    assert pictures == [] and client.get("/api/set-images/settings").json()["remote_fetch"] is False


def test_opted_in_fetch_happens_once_for_an_owned_set_and_is_cached(client, catalog, pictures):
    client.post("/api/collection/sets", json={"set_num": "8448"})
    assert client.put("/api/set-images/settings", json={"remote_fetch": True}).json()["remote_fetch"] is True
    assert client.get("/files/set-image/100-1").headers["content-type"] == "image/svg+xml"      # not owned: never fetched
    for _ in range(2):
        r = client.get("/files/set-image/8448-1")
        assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content == JPG
    assert pictures == ["https://cdn.rebrickable.com/media/sets/8448-1.jpg"]
    assert (set_images.cache_dir() / "8448-1.catalog.jpg").is_file()


def test_failed_fetch_serves_the_placeholder_and_is_not_retried_at_once(client, catalog, pictures, monkeypatch):
    client.post("/api/collection/sets", json={"set_num": "8448"})
    client.put("/api/set-images/settings", json={"remote_fetch": True})
    calls = []

    def offline(url):
        calls.append(url)
        raise OSError("offline")
    monkeypatch.setattr(set_images, "_download", offline)
    for _ in range(2):
        assert client.get("/files/set-image/8448-1").headers["content-type"] == "image/svg+xml"
    assert len(calls) == 1


def test_upload_works_for_any_set_and_wins_over_the_catalogue_picture(client, catalog, pictures):
    client.post("/api/collection/sets", json={"set_num": "100", "brand": "CADA"})
    r = client.post("/api/sets/set-100-1/image", content=PNG, headers={"content-type": "image/png"})
    assert r.status_code == 200 and r.json()["image"] == "/files/set-image/100-1"
    got = client.get("/files/set-image/100-1")
    assert got.headers["content-type"] == "image/png" and got.content == PNG
    client.put("/api/set-images/settings", json={"remote_fetch": True})
    assert client.get("/files/set-image/100-1").content == PNG and pictures == []
    assert client.delete("/api/sets/set-100-1/image").json() == {"removed": True}
    client.put("/api/set-images/settings", json={"remote_fetch": False})
    assert client.get("/files/set-image/100-1").headers["content-type"] == "image/svg+xml"


def test_upload_is_checked_for_type_size_and_set(client, catalog, pictures):
    client.post("/api/collection/sets", json={"set_num": "100"})
    assert client.post("/api/sets/set-100-1/image", content=b"<svg onload=alert(1)>").status_code == 415
    assert client.post("/api/sets/set-100-1/image", content=b"").status_code == 400
    assert client.post("/api/sets/set-100-1/image", content=PNG + b"x" * set_images.MAX_BYTES).status_code == 413
    assert client.post("/api/sets/nope/image", content=PNG).status_code == 404
    assert client.get("/files/set-image/..%2Fsecret").status_code == 404


def test_set_with_a_bundled_model_falls_back_to_a_render(client, catalog, pictures, tmp_path, monkeypatch):
    models = tmp_path / "toolkit" / "data" / "models-annotated"
    models.mkdir(parents=True)
    (models / "8448-1.mpd").write_text("0 model\n", encoding="utf-8")
    monkeypatch.setattr(settings, "TOOLKIT_DIR", tmp_path / "toolkit")
    renders = []

    async def fake_render(model, out, **kwargs):
        renders.append(model.name)
        out.write_bytes(PNG)
        return out
    monkeypatch.setattr(set_images.render, "render", fake_render)
    client.post("/api/collection/sets", json={"set_num": "8448"})
    for _ in range(2):
        r = client.get("/files/set-image/8448-1")
        assert r.headers["content-type"] == "image/png" and r.content == PNG
    assert renders == ["8448-1.mpd"] and pictures == []
