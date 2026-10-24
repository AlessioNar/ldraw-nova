"""Brickit Pileometer CSV import: detection, scan summing, colour families kept for review, assignment and unmatched parts."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from ldraw_tools.inventory import FAMILY_CANDIDATES, Inventory, ParseError, combine, make_inventory, parse_rebrickable_csv

FIXTURE = Path(__file__).parent / "fixtures" / "pileometer-parts.csv"
HEADER = '"Scan","Part","PartName","Color","Quantity"\n'


def fake_library():
    codes = {c for cs in FAMILY_CANDIDATES.values() for c in cs}
    by_code = {p: f"Part {p}" for p in ("3001", "3003", "3022", "2412b", "3004")}
    by_code["3005"] = "~Moved to 3004"
    return SimpleNamespace(by_code=by_code, colours_by_code={c: SimpleNamespace(name=f"Colour_{c}") for c in codes})


def parse(text, **kw):
    return parse_rebrickable_csv(text, library=kw.pop("library", fake_library()), **kw)


def table(result):
    return {(r.part, r.colour): r.qty for r in result.rows}


def test_header_is_detected_as_pileometer():
    r = parse(HEADER + '1,"3001","Brick 2 x 4","Reds",2\n')
    assert r.dialect == "pileometer"
    assert r.pileometer["scans"] == 1


def test_quantities_add_up_across_scans_into_review_items():
    r = parse(HEADER + '1,"3001","Brick 2 x 4","Reds",2\n2,"3001","Brick 2 x 4","Reds",3\n2,"3001","Brick 2 x 4","Blues",1\n')
    assert r.rows == []                     # a family is not an exact colour: nothing is owned yet
    assert [(i["part"], i["family"], i["qty"], i["note"]) for i in r.review] == \
        [("3001", "Blues", 1, "scan 2"), ("3001", "Reds", 5, "scan 1, 2")]
    assert r.review[1]["candidates"] == FAMILY_CANDIDATES["Reds"] and r.review[1]["brickit_part"] == "3001"
    assert (r.total_rows, r.total_pieces, r.pileometer["scans"], r.pileometer["review_pieces"]) == (3, 6, 2, 6)


def test_review_items_are_stored_but_never_counted():
    r = parse(HEADER + '1,"3001","","Reds",2\n')
    inv = make_inventory("pile", r)
    assert inv.total_pieces == 0 and combine([inv]) == {} and inv.review[0]["qty"] == 2
    again = Inventory.from_json(inv.to_json())
    assert again.review == inv.review
    old = inv.to_json()
    del old["review"]
    assert Inventory.from_json(old).review == []        # files without the key still read


def test_assigning_a_family_moves_its_pieces_into_rows_marked_approximate():
    text = HEADER + '1,"3001","","Grays",1\n2,"3003","","Grays",2\n1,"3022","","Reds",1\n'
    r = parse(text, family_colours={"Grays": 72})
    assert table(r) == {("3001", 72): 1, ("3003", 72): 2}
    assert all(row.family and row.to_json()["colour_approximate"] for row in r.rows)
    assert [(i["part"], i["family"]) for i in r.review] == [("3022", "Reds")]
    fams = {f["family"]: f["colour"] for f in r.pileometer["families"]}
    assert fams == {"Grays": 72, "Reds": None}
    assert (r.pileometer["assigned_pieces"], r.pileometer["review_pieces"]) == (3, 1)


def test_bad_assignments_are_refused():
    with pytest.raises(ParseError, match="Unknown colour family"):
        parse(HEADER + '1,"3001","","Reds",1\n', family_colours={"Purples": 4})
    with pytest.raises(ParseError, match="not an installed"):
        parse(HEADER + '1,"3001","","Reds",1\n', family_colours={"Reds": 99999})


def test_unmatched_parts_are_listed_with_name_and_quantity():
    r = parse(HEADER + '1,"9999x","Mystery, big","Reds",2\n2,"9999x","Mystery, big","Reds",1\n1,"3001","","Reds",1\n')
    assert r.unmapped_parts == [dict(rb_part="9999x", colour=0, qty=3, name="Mystery, big", family="Reds")]
    assert r.pileometer["matched_rows"] == 1 and [i["part"] for i in r.review] == ["3001"]
    assert r.total_pieces == 4


def test_suffixes_and_moved_parts_resolve():
    r = parse(HEADER + '1,"3001pr0001","","Reds",1\n1,"3005","","Reds",1\n1,"3003c01","","Reds",1\n')
    assert sorted(i["part"] for i in r.review) == ["3001", "3003", "3004"]


def test_unknown_family_is_reported_not_dropped():
    r = parse(HEADER + '1,"3001","","Purples",4\n')
    assert r.rows == [] and r.review == [] and r.unmapped_colours[0]["family"] == "Purples" and r.unmapped_colours[0]["qty"] == 4


def test_bad_rows_raise():
    with pytest.raises(ParseError, match="quantity"):
        parse(HEADER + '1,"3001","","Reds",x\n')
    with pytest.raises(ParseError, match="no data rows"):
        parse(HEADER)


def test_fixture_with_the_official_library(official):
    text = FIXTURE.read_text(encoding="utf-8")
    r = parse_rebrickable_csv(text, library=official)
    p = r.pileometer
    assert (p["scans"], p["rows"], p["pieces"]) == (11, 450, 950)
    assert len(p["families"]) == 9 and r.rows == [] and p["assigned_pieces"] == 0
    assert p["matched_rows"] >= 0.9 * 450
    assert p["review_pieces"] + sum(u["qty"] for u in r.unmapped_parts) == 950
    assert {u["rb_part"] for u in r.unmapped_parts} >= {"Unknown", "Unrecognized"}
    everything = {f: FAMILY_CANDIDATES[f][0] for f in FAMILY_CANDIDATES}
    done = parse_rebrickable_csv(text, library=official, family_colours=everything)
    assert done.review == [] and sum(row.qty for row in done.rows) == p["review_pieces"]
    assert done.pileometer["families"][0]["colour"] is not None
    grey = parse_rebrickable_csv(text, library=official, family_colours={"Grays": 72})
    assert {row.colour for row in grey.rows} == {72} and len(grey.review) > 0
