"""BrickLink / BrickStore / generic CSV imports through the preview API, and the collection exports."""
# ruff: noqa: F811  (fixtures imported from test_collection are used as arguments)
import json
import re

import pytest

import collection_export
import collection_store
import inventories
import part_info
import settings
import test_collection
from test_collection import cli, client, folder, preview, put  # noqa: F401  (fixtures shared with the import tests)

BL_XML = """<INVENTORY>
 <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3001</ITEMID><COLOR>5</COLOR><QTY>8</QTY></ITEM>
 <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3003</ITEMID><COLOR>86</COLOR><QTY>3</QTY></ITEM>
 <ITEM><ITEMTYPE>M</ITEMTYPE><ITEMID>sw0001</ITEMID><COLOR>0</COLOR><QTY>1</QTY></ITEM>
</INVENTORY>"""
BSX = "<BrickStoreXML><Inventory><Item><ItemID>3001</ItemID><ItemTypeID>P</ItemTypeID><ColorID>5</ColorID><Qty>6</Qty></Item></Inventory></BrickStoreXML>"
SHEET = "Brick;Shade;Count\n3001;Red;4\n3003;Light Bluish Gray;2\n3020;Glow Purple;1\n"


def stored(source_id):
    return json.loads((settings.INVENTORIES_DIR / f"{source_id}.json").read_text(encoding="utf-8"))


def commit(client, p, **body):
    r = client.post(f"/api/collection/imports/{p['preview_id']}/commit", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# --- imports ------------------------------------------------------------------------

def test_bricklink_xml_and_bsx_preview_then_commit(client, cli):
    p = preview(client, "store", BL_XML)
    assert p["format"] == "bricklink-xml" and p["totals"]["pieces"] == 11 and p["report"]["unmapped_parts"][0]["reason"].startswith("BrickLink item type M")
    assert not (settings.INVENTORIES_DIR / "store.json").exists()
    commit(client, p)
    assert {(r["part"], r["colour"], r["qty"]) for r in stored("store")["rows"]} == {("3001", 4, 8), ("3003", 71, 3)}
    assert stored("store")["origin"]["format"] == "bricklink-xml"
    q = preview(client, "bsx", BSX)
    assert q["format"] == "brickstore-bsx" and q["totals"]["pieces"] == 6


def test_format_can_be_overridden(client, cli):
    r = client.post("/api/collection/imports/preview", json={"name": "x", "csv": "Part,Color,Quantity\n3001,5,2\n", "format": "bricklink-csv"})
    assert r.status_code == 400                                    # not a BrickLink CSV header: the override is honoured, not ignored
    r = client.post("/api/collection/imports/preview", json={"name": "x", "csv": "Part,Color,Quantity\n3001,4,2\n", "format": "nope"})
    assert r.status_code == 400 and "format" in r.json()["detail"]


def test_generic_csv_asks_for_columns_then_remembers_them(client, cli):
    first = client.post("/api/collection/imports/preview", json={"name": "sheet", "csv": SHEET}).json()
    need = first["needs_mapping"]
    assert need["headers"] == ["Brick", "Shade", "Count"] and need["guess"] == {"part": "Brick", "qty": "Count"} and need["saved"] is None
    mapping = {"part": "Brick", "colour": "Shade", "qty": "Count", "system": "name"}
    p = client.post("/api/collection/imports/preview", json={"name": "sheet", "csv": SHEET, "mapping": mapping}).json()
    assert p["format"] == "generic-csv" and p["totals"]["pieces"] == 6 and p["mapping"] == mapping and p["mapping_remembered"] is False
    assert [u["rb_colour"] for u in p["report"]["unmapped_colours"]] == ["Glow Purple"]
    commit(client, p)
    assert collection_store.read_import_mappings() == {need["signature"]: mapping}
    again = client.post("/api/collection/imports/preview", json={"name": "sheet 2", "csv": SHEET + "3022;Red;5\n"}).json()
    assert again["mapping_remembered"] is True and again["totals"]["pieces"] == 11                  # no questions the second time
    other = client.post("/api/collection/imports/preview", json={"name": "s3", "csv": "Brick;Shade;Count;Extra\n3001;Red;1;x\n"}).json()
    assert "needs_mapping" in other                                                                  # a different layout asks again


def test_bad_mapping_is_a_400(client, cli):
    for mapping in ({"part": "Nope", "colour": "Shade", "qty": "Count"}, {"part": "Brick", "colour": "Shade", "qty": "Count", "system": "x"},
                    {"bogus": "y"}):
        r = client.post("/api/collection/imports/preview", json={"name": "s", "csv": SHEET, "mapping": mapping})
        assert r.status_code == 400, mapping


# --- exports --------------------------------------------------------------------------

@pytest.fixture
def tables(tmp_path, monkeypatch):
    data = tmp_path / "toolkit/ldraw_tools/data"
    data.mkdir(parents=True)
    (data / "rebrickable-colours.json").write_text(json.dumps({"colours": {
        "4": {"ldraw": 4, "exact": True, "method": "name", "name": "Red"}, "0": {"ldraw": 0, "exact": True, "method": "name", "name": "Black"},
        "71": {"ldraw": 71, "exact": True, "method": "name", "name": "Light Bluish Gray"}, "9": {"ldraw": 9, "exact": False, "method": "rgb", "name": "Approx"}}}))
    (data / "bricklink-colours.json").write_text(json.dumps({"colours": {"5": {"ldraw": 4}, "11": {"ldraw": 0}}, "reverse": {"4": 5, "0": 11}}))
    monkeypatch.setattr(settings, "TOOLKIT_DIR", tmp_path / "toolkit")
    collection_export.clear_caches()
    yield
    collection_export.clear_caches()


def seed():
    put("a", [("3001", 4, 5), ("3003", 71, 2), ("3020", 0, 1)], name="Bin A")
    put("b", [("3001", 4, 3), ("3001pr0001", 4, 1)], name="Bin B")
    rows = stored("b")["rows"]
    rows[1]["rb_part"] = "3001pr0001x"
    (settings.INVENTORIES_DIR / "b.json").write_text(json.dumps({**stored("b"), "rows": rows}))
    inventories.clear_caches()


def test_bricklink_wanted_list_lists_what_it_cannot_map(client, tables):
    seed()
    r = client.get("/api/collection/export/preview", params={"format": "bricklink-xml"}).json()
    assert (r["lines"], r["pieces"], r["omitted_lines"]) == (3, 10, 1)
    assert r["omitted"] == [{"part": "3003", "colour": 71, "colour_name": part_info.colour_name(71), "qty": 2, "reason": "no BrickLink colour for LDraw colour 71"}]
    assert any("imported with" in n for n in r["notes"])
    f = client.get("/api/collection/export", params={"format": "bricklink-xml"})
    assert f.status_code == 200 and "attachment" in f.headers["content-disposition"] and f.headers["x-export-omitted"] == "1"
    items = re.findall(r"<ITEMID>(.*?)</ITEMID><COLOR>(\d+)</COLOR><MINQTY>(\d+)</MINQTY>", f.text)
    assert items == [("3001", "5", "8"), ("3001pr0001x", "5", "1"), ("3020", "11", "1")] and f.text.startswith("<INVENTORY>")


def test_rebrickable_csv_and_scope_to_one_source(client, tables):
    seed()
    f = client.get("/api/collection/export", params={"format": "rebrickable-csv", "scope": "source:a"})
    assert f.text.splitlines() == ["Part,Color,Quantity", "3001,4,5", "3003,71,2", "3020,0,1"] and f.headers["x-export-omitted"] == "0"
    assert "bin-a" in f.headers["content-disposition"]
    assert client.get("/api/collection/export", params={"format": "csv", "scope": "source:missing"}).status_code == 404
    assert client.get("/api/collection/export", params={"format": "csv", "scope": "everything"}).status_code == 400
    assert client.get("/api/collection/export", params={"format": "pdf"}).status_code == 400


def test_plain_csv_round_trips_through_the_generic_importer(client, cli, tables, monkeypatch):
    monkeypatch.setattr(part_info, "meta", lambda: test_collection.META)           # the cli fixture empties the part table
    monkeypatch.setattr(part_info, "colour_names", lambda: test_collection.COLOURS)
    put("a", [("3001", 4, 5), ("3003", 1, 2)], name="Bin A")
    f = client.get("/api/collection/export", params={"format": "csv"})
    lines = f.text.splitlines()
    assert lines[0] == "Part,Description,LDraw colour,Colour,Quantity,Note" and lines[1].startswith("3001,Brick 2 x 4,4,Red,5")
    p = client.post("/api/collection/imports/preview", json={"name": "back", "csv": f.text, "mapping": {"part": "Part", "colour": "LDraw colour", "qty": "Quantity", "system": "ldraw"}}).json()
    assert p["totals"]["pieces"] == 7


def test_csv_text_cannot_start_a_spreadsheet_formula(client, tables):
    put("a", [("3001", 4, 1)], name="A")
    data = stored("a")
    data["rows"][0]["note"] = '=HYPERLINK("http://x")'
    (settings.INVENTORIES_DIR / "a.json").write_text(json.dumps(data))
    inventories.clear_caches()
    assert "'=HYPERLINK" in client.get("/api/collection/export", params={"format": "csv"}).text


def test_export_lines_for_a_shortage_list(client, tables):
    body = {"format": "bricklink-xml", "name": "Shortage", "lines": [{"part": "3001", "colour": 4, "qty": 3}, {"part": "3001", "colour": 4, "qty": 2}, {"part": "3003", "colour": 71, "qty": 1}]}
    r = client.post("/api/collection/export/lines", json=body).json()
    assert (r["lines"], r["pieces"], r["omitted_lines"]) == (1, 5, 1) and "<MINQTY>5</MINQTY>" in r["body"]
    assert client.post("/api/collection/export/lines", params={"download": True}, json=body).headers["content-type"].startswith("application/xml")
    assert client.post("/api/collection/export/lines", json={**body, "lines": [{"part": "../x", "colour": 4, "qty": 1}]}).status_code == 400
    assert client.post("/api/collection/export/lines", json={**body, "lines": []}).status_code == 400


def test_missing_colour_tables_say_so(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TOOLKIT_DIR", tmp_path / "none")
    monkeypatch.setattr(collection_export, "data_dir", lambda: tmp_path / "none")
    collection_export.clear_caches()
    put("a", [("3001", 4, 1)])
    assert client.get("/api/collection/export", params={"format": "bricklink-xml"}).status_code == 503
    assert client.get("/api/collection/export", params={"format": "csv"}).status_code == 200
    collection_export.clear_caches()
