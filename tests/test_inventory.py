import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import mpd, ref

from ldraw_tools import inventory as inv
from ldraw_tools.inventory import (Inventory, ParseError, check_model, combine, make_inventory, parse_rebrickable_csv,
                                   search_owned, summary)

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def fixture_maps(monkeypatch):
    monkeypatch.setattr(inv, "COLOURS_FILE", FIX / "maps/rebrickable-colours.json")
    monkeypatch.setattr(inv, "PARTS_FILE", FIX / "maps/rebrickable-parts.json")
    inv.clear_map_cache()
    yield
    inv.clear_map_cache()


def parse(name, **kw):
    return parse_rebrickable_csv((FIX / name).read_bytes().decode("utf-8-sig"), **kw)


def table(result):
    return {(r.part, r.colour): r.qty for r in result.rows}


def test_parts_list_dialect():
    r = parse("rb_parts_list.csv")
    assert r.dialect == "rebrickable-parts-list"
    assert table(r) == {("3001", 4): 5, ("3001", 1): 2, ("3003", 4): 3, ("3022", 14): 10}
    assert r.total_rows == 4 and r.total_pieces == 20 and r.spare_pieces == 0
    assert any("approximate" in w for w in r.warnings)


def test_bom_spares_and_zero_rows():
    r = parse("rb_parts_list_spare_bom.csv")
    assert table(r) == {("3001", 4): 2, ("3022", 14): 4}
    assert r.spare_pieces == 1
    assert any("quantity 0" in w for w in r.warnings) and any("spare" in w for w in r.warnings)
    assert table(parse("rb_parts_list_spare_bom.csv", include_spares=True))[("3001", 4)] == 3


def test_database_dialect_unmapped_and_spares():
    r = parse("rb_database.csv")
    assert r.dialect == "rebrickable-database"
    assert table(r) == {("3001", 4): 3, ("3003", 1): 2}
    assert r.spare_pieces == 1
    assert r.unmapped_parts == [dict(rb_part="99999zz", colour=4, qty=7)]
    assert r.unmapped_colours == [dict(rb_colour=9999, part="3001", qty=2)]


def test_semicolon_quoted_spaced_headers():
    r = parse("rb_semicolon.csv")
    assert table(r) == {("3001", 4): 6, ("3022", 1): 2}


def test_duplicates_collapse():
    r = parse("rb_duplicates.csv")
    assert table(r) == {("3001", 4): 6, ("3001", 1): 1}
    assert [x.rb_part for x in r.rows] == ["3001", "3001"]


def test_alias_and_crlf_blank_lines():
    r = parse_rebrickable_csv("\r\n\r\nPART,COLOR,QUANTITY\r\n3001A,4,2\r\n\r\n")
    assert table(r) == {("3001", 4): 2}


def test_errors():
    for name, msg in [("malformed_header.csv", "header"), ("malformed_negative.csv", "negative"), ("malformed_quantity.csv", "integer")]:
        with pytest.raises(ParseError, match=msg):
            parse(name)
    for text in ["", "Part,Color,Quantity\n"]:
        with pytest.raises(ParseError):
            parse_rebrickable_csv(text)
    with pytest.raises(ParseError, match="more than"):
        monkey = inv.MAX_ROWS
        try:
            inv.MAX_ROWS = 2
            parse_rebrickable_csv("Part,Color,Quantity\n3001,4,1\n3003,4,1\n3022,4,1\n")
        finally:
            inv.MAX_ROWS = monkey
    with pytest.raises(ParseError, match="MB"):
        parse_rebrickable_csv("x" * (inv.MAX_BYTES + 1))


def test_missing_maps_warn(monkeypatch):
    monkeypatch.setattr(inv, "COLOURS_FILE", FIX / "nope.json")
    inv.clear_map_cache()
    r = parse("rb_parts_list.csv")
    assert not r.rows and any("missing" in w for w in r.warnings)


def test_library_verifies_identity(parts):
    r = parse_rebrickable_csv("Part,Color,Quantity\n3001,4,1\n3005,4,2\n", library=parts)
    assert table(r) == {("3001", 4): 1}
    assert r.unmapped_parts == [dict(rb_part="3005", colour=4, qty=2)]


def test_inventory_json_roundtrip_and_slug():
    i = make_inventory("My Bricks! 2024", parse("rb_parts_list.csv"), source_filename="a.csv")
    assert i.id == "my-bricks-2024" and i.total_pieces == 20
    j = json.loads(json.dumps(i.to_json()))
    assert Inventory.from_json(j).to_json() == i.to_json()
    assert make_inventory("x", parse("rb_parts_list.csv"), id="custom").id == "custom"


def test_combine_summary_search(parts):
    a = make_inventory("a", parse("rb_parts_list.csv"))
    b = make_inventory("b", parse("rb_duplicates.csv"))
    assert combine([a, b])[("3001", 4)] == 11
    s = summary([a, b], limit=2)
    assert s["total_pieces"] == 20 + 7 and s["distinct_parts"] == 3 and len(s["top_parts"]) == 2
    assert s["top_parts"][0]["part"] == "3001" and s["top_colours"][0]["name"]
    hits = search_owned([a], "brick 2 x 4", library=parts)
    assert {h["part"] for h in hits} == {"3001"} and hits[0]["qty"] == 5 and hits[0]["description"]
    assert [h["colour"] for h in search_owned([a], "3001", colour=1)] == [1]
    assert search_owned([a], "3001", min_qty=6) == []
    assert len(search_owned([a], limit=1)) == 1


def model_text(*lines):
    return mpd("\n".join(lines))


def make_model(text, parts):
    from ldraw_tools.validation import validate_text
    model, diagnostics = validate_text(text, parts, assembly=True)
    assert model, diagnostics
    return model


def test_check_model(parts):
    text = model_text(ref("3001.dat", 4, "0 0 0"), ref("3001.dat", 4, "100 0 0"), ref("3001.dat", 4, "200 0 0"),
                      ref("3003.dat", 1, "0 -24 0"), ref("3022.dat", 14, "300 0 0"), ref("3022.dat", 4, "400 0 0"))
    model = make_model(text, parts)
    have = make_inventory("h", parse_rebrickable_csv("Part,Color,Quantity\n3001,4,2\n3001,1,9\n3003,4,3\n3022,14,1\n"))
    r = check_model(model, [have], library=parts)
    assert r["mode"] == "prefer" and not r["complete"] and r["needed_total"] == 6 and r["ok_pieces"] == 3
    short = {(s["part"], s["colour"]): s for s in r["short"]}
    assert short[("3001", 4)]["missing"] == 1 and short[("3003", 1)]["owned"] == 0 and short[("3022", 4)]["missing"] == 1
    assert [n["part"] for n in r["not_owned_parts"]] == []
    subs = {(s["part"], s["wanted_colour"]): s["owned_colours"] for s in r["colour_substitutions"]}
    assert subs[("3001", 4)] == [dict(colour=1, qty=9)] and subs[("3003", 1)] == [dict(colour=4, qty=3)]
    assert subs[("3022", 4)] == [dict(colour=14, qty=1)]
    assert check_model(model, [have], library=parts, substitutions=False)["colour_substitutions"] == []
    full = make_inventory("f", parse_rebrickable_csv("Part,Color,Quantity\n3001,4,3\n3003,1,1\n3022,14,1\n3022,4,1\n"))
    ok = check_model(model, [full], mode="only", library=parts)
    assert ok["complete"] and ok["short"] == [] and ok["mode"] == "only"
    with pytest.raises(ValueError):
        check_model(model, [full], mode="bogus", library=parts)


def test_check_model_not_owned(parts):
    model = make_model(model_text(ref("3003.dat", 4)), parts)
    r = check_model(model, [], library=parts)
    assert [n["part"] for n in r["not_owned_parts"]] == ["3003"] and r["owned_total"] == 0


def test_check_counts_like_bom_with_submodels(parts):
    text = ("0 FILE main.ldr\r\n0 Test\r\n0 Name: main.ldr\r\n0 Author: T\r\n0 !LDRAW_ORG Model\r\n"
            "1 4 0 0 0 1 0 0 0 1 0 0 0 1 pair.ldr\r\n1 4 100 0 0 1 0 0 0 1 0 0 0 1 pair.ldr\r\n0 NOFILE\r\n"
            "0 FILE pair.ldr\r\n0 Pair\r\n0 Name: pair.ldr\r\n0 Author: T\r\n0 !LDRAW_ORG Model\r\n"
            + ref("3001.dat", 16, "0 0 0") + "\r\n" + ref("3003.dat", 1, "50 0 0") + "\r\n0 NOFILE\r\n")
    model = make_model(text, parts)
    from ldraw_tools.document import physical_context
    view, overlay = physical_context(model, parts)
    bom = {(r.part.removesuffix(".dat"), r.colour_code): r.quantity for r in view.bill_of_materials(parts=overlay)}
    assert bom == {("3001", 4): 2, ("3003", 1): 2}
    assert inv.model_bom(model, parts) == bom


def run_cli(*args, env=None, cwd=None):
    import os
    e = dict(os.environ, LDRAW_NOVA_RB_MAP_DIR=str(FIX / "maps"), **(env or {}))
    return subprocess.run([sys.executable, "-m", "ldraw_tools.cli", *args], capture_output=True, text=True, env=e, cwd=cwd)


def test_cli_roundtrip(tmp_path, official):
    store = tmp_path / "store"
    csv_path = FIX / "rb_parts_list.csv"
    r = run_cli("inventory", "import", str(csv_path), "--name", "My Set", "--store", str(store))
    assert r.returncode == 0, r.stderr + r.stdout
    assert "dialect: rebrickable-parts-list" in r.stdout and (store / "my-set.json").is_file() and (store / "my-set.csv").is_file()
    assert run_cli("inventory", "import", str(csv_path), "--name", "My Set", "--store", str(store)).returncode == 2
    listing = run_cli("inventory", "list", "--store", str(store), "--json")
    assert json.loads(listing.stdout)["inventories"][0]["id"] == "my-set"
    show = json.loads(run_cli("inventory", "show", "my-set", "--store", str(store), "--json").stdout)
    assert show["distinct_parts"] >= 1
    found = json.loads(run_cli("inventory", "search", "My Set", "3001", "--store", str(store), "--json").stdout)
    assert found["matches"] and found["matches"][0]["part"] == "3001"
    model = tmp_path / "m.mpd"
    model.write_text(mpd(ref("3001.dat", 4) + "\n" + ref("3001.dat", 4, "100 0 0") + "\n" + ref("3001.dat", 4, "200 0 0")
                         + "\n" + ref("3001.dat", 4, "300 0 0") + "\n" + ref("3001.dat", 4, "400 0 0") + "\n" + ref("3001.dat", 4, "500 0 0")))
    rep = run_cli("inventory", "check", str(model), "my-set", "--store", str(store), "--json")
    data = json.loads(rep.stdout)
    assert rep.returncode == 0 and not data["complete"] and data["short"][0]["missing"] == 1
    only = run_cli("inventory", "check", str(model), "my-set", "--mode", "only", "--store", str(store))
    assert only.returncode == 1 and "missing" in only.stdout
    assert run_cli("inventory", "remove", "my-set", "--store", str(store)).returncode == 0
    assert not list(store.glob("my-set.*"))


def test_cli_import_error_and_help(tmp_path):
    r = run_cli("inventory", "import", str(FIX / "malformed_header.csv"), "--store", str(tmp_path))
    assert r.returncode == 2 and "header" in json.loads(r.stdout)["error"]
    h = run_cli("--help")
    assert h.returncode == 0 and "inventory" in h.stdout


def test_available_inventory_subtracts_built_sets_and_reservations(tmp_path):
    from ldraw_tools.inventory import Row, available_inventory
    own = Inventory(id="own", name="own", rows=[Row("3001", 4, 10), Row("3003", 1, 2)])
    built = Inventory(id="set", name="set", kind="set", set={"state": "built"}, rows=[Row("3001", 4, 3)])
    taken_apart = Inventory(id="set2", name="set2", kind="set", set={"state": "parts"}, rows=[Row("3003", 1, 2)])
    res = tmp_path / ".collection-reservations.json"
    res.write_text(json.dumps({"reservations": [{"id": "r", "rows": [["3001", 4, 5], ["3003", 1, 9]]}]}))
    free = available_inventory([own, built, taken_apart], res)
    assert combine([free]).get(("3003", 1), 0) == 0 and combine([free])[("3001", 4)] == 10 + 3 - 3 - 5
    assert combine([available_inventory([own], tmp_path / "missing.json")]) == {("3001", 4): 10, ("3003", 1): 2}
    res.write_text("not json")
    assert combine([available_inventory([own], res)]) == {("3001", 4): 10, ("3003", 1): 2}
