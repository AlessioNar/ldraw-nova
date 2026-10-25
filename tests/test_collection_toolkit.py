"""Toolkit side of the parts collection: optional new keys round-trip, the importer registry, dry-run import,
exact-only counting, and the backend's set catalogue reader checked against ldraw_tools.sets."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ldraw_tools import importers, sets
from ldraw_tools import inventory as inv
from ldraw_tools.inventory import Inventory, ParseError, Row, combine, make_inventory

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"
PILE = 'Scan,Part,PartName,Color,Quantity\n1,3001,"Brick 2 x 4",Grays,2\n2,3001,"Brick 2 x 4",Grays,1\n1,3003,"Brick 2 x 2",Reds,4\n'


@pytest.fixture(autouse=True)
def fixture_maps(monkeypatch):
    monkeypatch.setattr(inv, "COLOURS_FILE", FIX / "maps/rebrickable-colours.json")
    monkeypatch.setattr(inv, "PARTS_FILE", FIX / "maps/rebrickable-parts.json")
    inv.clear_map_cache()
    yield
    inv.clear_map_cache()


def test_new_optional_keys_round_trip_and_old_files_still_load():
    r = importers.parse(PILE)
    i = make_inventory("pile", r)
    i.kind, i.set, i.schema = "loose", {"set_num": "8448-1", "copies": 2, "state": "built"}, 2
    i.rows.append(Row("3001", 4, 3, note="blue tub"))
    again = Inventory.from_json(json.loads(json.dumps(i.to_json())))
    assert (again.kind, again.set, again.schema, again.origin) == ("loose", i.set, 2, {"format": "brickit-pileometer", "scans": 2})
    assert again.rows[0].note == "blue tub" and again.review == i.review
    plain = Inventory("old", "old", rows=[Row("3001", 4, 1)]).to_json()
    assert not {"kind", "set", "origin", "schema", "review"} & set(plain)         # nothing new is written when unused
    assert "note" not in plain["rows"][0]
    legacy = {"id": "old", "name": "old", "rows": [{"part": "3001", "colour": 4, "qty": 2}], "unknown_future_key": 1}
    loaded = Inventory.from_json(legacy)
    assert (loaded.kind, loaded.set, loaded.schema, loaded.rows[0].note) == ("", None, 0, "")


def test_only_exact_rows_are_counted_review_and_notes_never_change_a_count():
    r = importers.parse(PILE)
    i = make_inventory("pile", r)
    assert i.review and combine([i]) == {}
    i.rows.append(Row("3001", 4, 3, note="blue tub"))
    i.rows.append(Row("3001", 4, 2, note="red tub"))                     # same part and colour in two places: summed
    assert combine([i]) == {("3001", 4): 5}


def test_importers_detect_the_format_of_an_upload():
    assert importers.detect("Part,Color,Quantity\n3001,4,1\n") == "rebrickable-csv"
    assert importers.detect("inventory_id,part_num,color_id,quantity,is_spare\n1,3001,4,1,f\n") == "rebrickable-csv"
    assert importers.detect(PILE) == "brickit-pileometer"
    assert importers.detect("Item No,Color,Qty,Condition\n3001,Red,1,N\n") == "bricklink-csv"
    assert importers.detect("Brick,Shade,Count\n3001,Red,1\n") is None
    assert importers.detect("") is None and importers.detect(b"\xef\xbb\xbfPart;Color;Quantity\n3001;4;1\n") == "rebrickable-csv"
    assert set(importers.FORMATS) == {"rebrickable-csv", "brickit-pileometer", "bricklink-xml", "brickstore-bsx", "bricklink-csv", "generic-csv"}


def test_parse_tags_the_origin_and_keeps_the_original_errors():
    r = importers.parse("Part,Color,Quantity\n3001,4,5\n", filename="bin.csv")
    assert r.origin == {"format": "rebrickable-csv", "file": "bin.csv"} and r.rows[0].qty == 5
    assert importers.parse(PILE).origin == {"format": "brickit-pileometer", "scans": 2}
    with pytest.raises(ParseError, match="Unrecognised header"):
        importers.parse("A,B\n1,2\n")


def run_cli(*args, cwd):
    return subprocess.run([sys.executable, "-m", "ldraw_tools.cli", *args], cwd=cwd, capture_output=True, text=True,
                          env={**__import__("os").environ, "PYTHONPATH": str(ROOT), "LDRAW_NOVA_RB_MAP_DIR": str(FIX / "maps")})


def test_cli_dry_run_reports_without_writing_anything(tmp_path):
    csv = tmp_path / "bin.csv"
    csv.write_text("Part,Color,Quantity\n3001,4,5\n")
    store = tmp_path / "store"
    r = run_cli("inventory", "import", str(csv), "--name=Bin", "--store", str(store), "--dry-run", "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["format"] == "rebrickable-csv" and report["inventory"]["rows"][0]["qty"] == 5 and report["inventory"]["origin"]["file"] == "bin.csv"
    assert not store.exists()
    real = run_cli("inventory", "import", str(csv), "--name=Bin", "--store", str(store), "--json", cwd=tmp_path)
    assert real.returncode == 0 and (store / "bin.json").is_file() and (store / "bin.csv").is_file()
    assert json.loads(real.stdout)["stored"].endswith("bin.json")


def test_backend_set_catalogue_reader_matches_ldraw_tools_sets(tmp_path, monkeypatch):
    path = tmp_path / "set-catalog.bin"
    rows = [dict(set_num="8448-1", name="Super Street Sweeper", year=1999, theme="Technic", num_parts=11, inv_version=1,
                 dropped_part=3, dropped_colour=0, lines=[("3001", 4, 2), ("3003", 1, 5), ("3020", 15, 1)]),
            dict(set_num="100-1", name="Red Racer", year=2005, theme="Racers", num_parts=4, inv_version=2,
                 dropped_part=0, dropped_colour=0, lines=[("3001", 4, 4)])]
    for r in rows:
        r["mapped_pieces"] = sum(q for _, _, q in r["lines"])
    sets.write_catalog(path, {"source": "test"}, rows)
    backend = ROOT / "app" / "web" / "backend"
    monkeypatch.syspath_prepend(str(backend))
    monkeypatch.syspath_prepend(str(ROOT / "app"))
    sys.modules.pop("settings", None)
    spec = importlib.util.spec_from_file_location("backend_set_catalog", backend / "set_catalog.py")
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:                      # the backend needs its own packages (fastapi settings imports): run it in the image
        pytest.skip(f"backend modules unavailable here: {exc}")
    theirs, mine = sets.read_catalog(path), module.Catalog(path.read_bytes())
    assert mine.sets == theirs.sets and mine.meta == theirs.meta
    for s in theirs.sets:
        assert mine.lines(s["set_num"]) == theirs.lines(s["set_num"])
    assert [s["set_num"] for s in mine.search("racer")] == [s["set_num"] for s in sets.search_sets("racer", catalog=theirs)]
    assert mine.get("8448")["name"] == sets.get_set("8448", theirs)["name"]
