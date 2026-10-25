"""BrickLink XML, BrickStore BSX, BrickLink CSV and generic CSV importers."""
import json
from pathlib import Path

import pytest

from ldraw_tools import bricklink, importers, inventory as inv
from ldraw_tools.inventory import ParseError

FIX = Path(__file__).parent / "fixtures"
DATA = Path(inv.__file__).parent / "data"

XML = """<?xml version="1.0" encoding="UTF-8"?>
<INVENTORY>
 <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3001</ITEMID><COLOR>5</COLOR><QTY>8</QTY><CONDITION>N</CONDITION></ITEM>
 <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3001</ITEMID><COLOR>5</COLOR><QTY>2</QTY><CONDITION>U</CONDITION></ITEM>
 <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3003</ITEMID><COLOR>86</COLOR><QTY>3</QTY></ITEM>
 <ITEM><ITEMTYPE>M</ITEMTYPE><ITEMID>sw0001</ITEMID><COLOR>0</COLOR><QTY>1</QTY></ITEM>
 <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3022</ITEMID><COLOR>99999</COLOR><QTY>4</QTY></ITEM>
</INVENTORY>"""
WANTED = """<INVENTORY>
 <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3001</ITEMID><COLOR>5</COLOR><MINQTY>10</MINQTY><QTYFILLED>4</QTYFILLED></ITEM>
 <ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3003</ITEMID><COLOR>7</COLOR><MINQTY>6</MINQTY><QTYFILLED>0</QTYFILLED></ITEM>
</INVENTORY>"""
BSX = """<?xml version="1.0" encoding="UTF-8"?>
<BrickStoreXML><Inventory Currency="EUR">
 <Item><ItemID>3001</ItemID><ItemTypeID>P</ItemTypeID><ColorID>5</ColorID><Qty>6</Qty><Status>I</Status></Item>
 <Item><ItemID>3003</ItemID><ItemTypeID>P</ItemTypeID><ColorID>7</ColorID><Qty>9</Qty><Status>X</Status></Item>
 <Item><ItemID>3020</ItemID><ItemTypeID>P</ItemTypeID><ColorID>11</ColorID><Qty>2</Qty><Status>E</Status></Item>
</Inventory></BrickStoreXML>"""


@pytest.fixture(autouse=True)
def fixture_maps(monkeypatch):
    monkeypatch.setattr(inv, "COLOURS_FILE", FIX / "maps/rebrickable-colours.json")
    monkeypatch.setattr(inv, "PARTS_FILE", FIX / "maps/rebrickable-parts.json")
    inv.clear_map_cache()
    bricklink.clear_cache()
    yield
    inv.clear_map_cache()
    bricklink.clear_cache()


def table(result):
    return {(r.part, r.colour): r.qty for r in result.rows}


def test_bricklink_colour_table_is_consistent():
    data = json.loads((DATA / "bricklink-colours.json").read_text(encoding="utf-8"))
    colours = data["colours"]
    assert colours["5"]["ldraw"] == 4 and colours["11"]["ldraw"] == 0 and colours["86"]["ldraw"] == 71 and colours["85"]["ldraw"] == 72
    assert data["by_name"]["lightbluishgrey"] == 86 and data["reverse"]["4"] == 5 and data["reverse"]["71"] == 86
    assert not set(data["unmapped"]) & set(colours)
    assert all(str(bl) in colours and colours[str(bl)]["ldraw"] == int(code) for code, bl in data["reverse"].items())


def test_bricklink_xml_inventory_sums_lines_and_reports_what_it_cannot_map():
    r = importers.parse(XML, filename="store.xml")
    assert r.origin == {"format": "bricklink-xml", "file": "store.xml"} and importers.detect(XML) == "bricklink-xml"
    assert table(r) == {("3001", 4): 10, ("3003", 71): 3}
    assert r.unmapped_parts == [dict(rb_part="sw0001", colour="0", qty=1, reason="BrickLink item type M (minifigure) is not a loose part")]
    assert [(u["rb_colour"], u["part"], u["qty"]) for u in r.unmapped_colours] == [("99999", "3022", 4)]
    assert r.total_pieces == 18 and r.total_rows == 5


def test_bricklink_wanted_list_counts_only_what_is_filled():
    r = importers.parse(WANTED)
    assert table(r) == {("3001", 4): 4} and any("QTYFILLED" in w for w in r.warnings)
    with pytest.raises(ParseError, match="wanted quantities"):
        importers.parse("<INVENTORY><ITEM><ITEMTYPE>P</ITEMTYPE><ITEMID>3001</ITEMID><COLOR>5</COLOR><MINQTY>2</MINQTY></ITEM></INVENTORY>")


def test_xml_with_entities_or_garbage_is_refused():
    with pytest.raises(ParseError, match="DOCTYPE"):
        importers.parse('<!DOCTYPE x [<!ENTITY a "b">]><INVENTORY><ITEM/></INVENTORY>')
    with pytest.raises(ParseError, match="Malformed XML"):
        importers.parse("<INVENTORY><ITEM></INVENTORY>")
    with pytest.raises(ParseError, match="no <ITEM>"):
        importers.parse("<INVENTORY></INVENTORY>")
    with pytest.raises(ParseError, match="whole number"):
        importers.parse("<INVENTORY><ITEM><ITEMID>3001</ITEMID><COLOR>5</COLOR><QTY>two</QTY></ITEM></INVENTORY>")


def test_brickstore_bsx_skips_excluded_items():
    r = importers.parse(BSX)
    assert importers.detect(BSX) == "brickstore-bsx" and r.origin["format"] == "brickstore-bsx"
    assert table(r) == {("3001", 4): 6, ("3020", 0): 2}
    assert any("excluded" in w for w in r.warnings)


def test_bricklink_csv_maps_colour_names():
    text = "Item No,Color,Qty,Condition\n3001,Red,2,N\n3001,Light Bluish Gray,5,U\n3003,Glow Purple,1,N\n"
    r = importers.parse(text)
    assert r.origin["format"] == "bricklink-csv" and table(r) == {("3001", 4): 2, ("3001", 71): 5}
    assert [(u["rb_colour"], u["qty"]) for u in r.unmapped_colours] == [("Glow Purple", 1)]
    assert table(importers.parse("Item No;Color ID;Qty\n3001;5;3\n")) == {("3001", 4): 3}


def test_generic_csv_needs_a_mapping_then_reads_it():
    text = "Brick;Shade;Count\n3001;Red;4\n3003;Bluish Grey;2\n"
    with pytest.raises(importers.NeedsMapping) as e:
        importers.parse(text)
    assert e.value.headers == ["Brick", "Shade", "Count"] and len(e.value.sample) == 2
    assert e.value.guess == {"part": "Brick", "qty": "Count"}
    r = importers.parse(text, mapping=dict(part="Brick", colour="Shade", qty="Count", system="name"))
    assert r.origin["format"] == "generic-csv" and table(r) == {("3001", 4): 4}
    assert [u["rb_colour"] for u in r.unmapped_colours] == ["Bluish Grey"]
    assert table(importers.parse("p,c,n\n3001,4,2\n", mapping=dict(part="p", colour="c", qty="n", system="ldraw"))) == {("3001", 4): 2}
    with pytest.raises(ParseError, match="three different columns"):
        importers.parse(text, mapping=dict(part="Brick", colour="Brick", qty="Count", system="name"))
    with pytest.raises(ParseError, match="choose the colour column"):
        importers.parse(text, mapping=dict(part="Brick", colour="Nope", qty="Count"))


def test_format_override_beats_detection():
    r = importers.parse("Part,Color,Quantity\n3001,5,2\n", format="generic-csv", mapping=dict(part="Part", colour="Color", qty="Quantity", system="bricklink"))
    assert table(r) == {("3001", 4): 2}
    with pytest.raises(ParseError, match="Unknown import format"):
        importers.parse("x", format="nope")
